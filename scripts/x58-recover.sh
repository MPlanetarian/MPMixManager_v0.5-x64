#!/usr/bin/env bash
# ==============================================================================
# x58-recover.sh — read-only triage + recovery helper for the X58GROK disk.
# ------------------------------------------------------------------------------
# The 120 GB drive arrives behind a JMicron JMS578 USB-SATA bridge
# (152d:0562) that currently reports 0 sectors:
#
#   sd 9:0:0:0: [sdd] Read Capacity(16) failed: hostbyte=DID_ERROR
#   Sense Key : Hardware Error [current]
#   ASC=0x44 <<vendor>> ASCQ=0x81
#
# That error comes from the bridge, not from the filesystem. Nothing on the
# disk can be inspected until the bridge can read the drive, so this script
# works outward from the hardware:
#
#   1  physical/power hints (which port, which hub, how much current)
#   2  re-enumerate the device without a reboot
#   3  force usb-storage instead of uas and rescan
#   4  try a different USB port / cable / enclosure (manual steps)
#   5  once a size appears, read-only filesystem inspection
#   6  only then consider a repair, and only with -r
#
# Default is read-only. Nothing is written to the disk unless -r is passed,
# and even then only after the drive reports a real capacity.
#
# Usage:
#   sudo ./x58-recover.sh              # triage only
#   sudo ./x58-recover.sh -d /dev/sdd  # pick the device explicitly
#   sudo ./x58-recover.sh -r           # also run read-only fsck/btrfs check
# ==============================================================================
set -uo pipefail

DEV=""
REPAIR=0
while [ $# -gt 0 ]; do
    case "$1" in
        -d|--device) DEV="${2:-}"; shift 2 ;;
        -r|--repair) REPAIR=1; shift ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m[ok]\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m[!!]\033[0m %s\n' "$*"; }
note() { printf '  \033[1;33m[--]\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0 $*" >&2; exit 1; }

# -- find the device ----------------------------------------------------------
if [ -z "$DEV" ]; then
    # The bridge identifies itself as "USB3.0 Super Speed" (152d:0562).
    for blk in /sys/block/sd*; do
        [ -e "$blk" ] || continue
        vid="$(cat "$blk/device/../../idVendor" 2>/dev/null || true)"
        pid="$(cat "$blk/device/../../idProduct" 2>/dev/null || true)"
        if [ "$vid" = "152d" ] && [ "$pid" = "0562" ]; then
            DEV="/dev/$(basename "$blk")"
            break
        fi
        # Fall back to a device that reports zero capacity.
        if [ "$(cat "$blk/size" 2>/dev/null || echo 1)" = "0" ] && [ -z "$DEV" ]; then
            DEV="/dev/$(basename "$blk")"
        fi
    done
fi

[ -n "$DEV" ] && [ -b "$DEV" ] || {
    bad "No candidate block device found."
    echo "  Attach the drive and rerun, or pass -d /dev/sdX."
    echo "  Present disks:"
    lsblk -o NAME,SIZE,MODEL 2>/dev/null | sed 's/^/    /'
    exit 1
}

BASE="$(basename "$DEV")"
SYS="/sys/block/$BASE"
say "Target: $DEV"
[ -d "$SYS" ] || { bad "$SYS does not exist"; exit 1; }

# -- 1. identity and power ----------------------------------------------------
say "1. Identity, link and power"
printf '  Vendor/Model : %s %s\n' \
    "$(cat "$SYS/device/vendor" 2>/dev/null | tr -s ' ')" \
    "$(cat "$SYS/device/model" 2>/dev/null | tr -s ' ')"
printf '  Sectors      : %s  (0 means the bridge cannot read the drive)\n' \
    "$(cat "$SYS/size" 2>/dev/null || echo '?')"
printf '  Removable    : %s\n' "$(cat "$SYS/removable" 2>/dev/null || echo '?')"
printf '  SCSI state   : %s\n' "$(cat "$SYS/device/state" 2>/dev/null || echo '?')"
printf '  Driver       : %s\n' "$(basename "$(readlink -f "$SYS/device/driver" 2>/dev/null)" 2>/dev/null || echo '?')"

USB_PATH="$(readlink -f "$SYS/device" 2>/dev/null | grep -oE 'usb[0-9]+/[0-9]+-[0-9.]+' | head -1)"
if [ -n "$USB_PATH" ]; then
    printf '  USB path     : %s\n' "$USB_PATH"
    MAXP="$(cat "/sys/bus/usb/devices/${USB_PATH}/bMaxPower" 2>/dev/null | tr -d ' ')"
    [ -n "$MAXP" ] && printf '  bMaxPower    : %s\n' "$MAXP"
    if printf '%s' "$USB_PATH" | grep -qE '/[0-9]+-[0-9]+\.[0-9]+$'; then
        note "This drive is behind a USB hub. A 2.5-inch SSD in a bus-powered"
        note "enclosure wants ~900 mA; hubs often cannot deliver it."
        note "Highest-yield fix: plug the drive into a rear motherboard port."
    fi
else
    NOTE_USB=1
fi

# -- 2. re-enumerate ----------------------------------------------------------
say "2. Re-enumerate the device (no reboot)"
RESCANNED=0
for h in /sys/class/scsi_host/host*; do
    [ -e "$h/scan" ] || continue
    if echo "- - -" > "$h/scan" 2>/dev/null; then
        RESCANNED=1
    fi
done
[ "$RESCANNED" = 1 ] && ok "Rescan issued on every SCSI host" || note "No scsi_host scan files were writable"
sleep 3
NOW_SIZE="$(cat "$SYS/size" 2>/dev/null || echo 0)"
if [ "$NOW_SIZE" != "0" ]; then
    ok "Capacity is now readable: $NOW_SIZE sectors ($(( NOW_SIZE / 2 / 1024 )) MiB)"
else
    bad "Still 0 sectors after a rescan"
fi

# -- 3. drop uas for this bridge ---------------------------------------------
say "3. Rebind through usb-storage instead of uas"
# JMS578 bridges are known to mis-handle UAS on some firmware revisions. The
# quarantined path also covers the "Unit Not Ready / ASC 0x44" case.
if [ "$NOW_SIZE" = "0" ]; then
    DRV="$(basename "$(readlink -f "$SYS/device/driver" 2>/dev/null)" 2>/dev/null)"
    if [ "$DRV" = "uas" ]; then
        IFACE=""
        for cand in /sys/bus/usb/drivers/uas/*/; do
            [ -e "$cand" ] || continue
            case "$USB_PATH" in
                *"$(basename "$cand")"*) IFACE="$(basename "$cand")" ;;
            esac
        done
        if [ -n "$IFACE" ]; then
            echo "$IFACE" > /sys/bus/usb/drivers/uas/unbind 2>/dev/null \
                && ok "Unbound $IFACE from uas" || bad "Could not unbind uas"
            # Force usb-storage to claim it, with UAS ignored.
            echo "152d:0562:u" > /sys/module/usb_storage/parameters/quirks 2>/dev/null || true
            for h in /sys/class/scsi_host/host*; do
                echo "- - -" > "$h/scan" 2>/dev/null || true
            done
            sleep 4
            NOW_SIZE="$(cat "$SYS/size" 2>/dev/null || echo 0)"
            if [ "$NOW_SIZE" != "0" ]; then
                ok "Capacity readable via usb-storage: $NOW_SIZE sectors"
            else
                bad "Still 0 sectors with usb-storage"
            fi
        else
            note "Could not identify the uas interface entry"
        fi
    else
        note "Already bound to ${DRV:-an unknown driver}; skipping the uas toggle"
    fi
else
    ok "Skipped: the drive is already readable"
fi

# -- 4. verdict ---------------------------------------------------------------
say "4. Hardware verdict"
if [ "$(cat "$SYS/size" 2>/dev/null || echo 0)" = "0" ]; then
    bad "The bridge still reports no media. This is a hardware-level fault."
    cat <<'EOF'

  The kernel is receiving a SCSI Hardware Error from the bridge:

      Read Capacity(16) failed ... Sense Key : Hardware Error
      ASC=0x44 <<vendor>> ASCQ=0x81

  ASC 0x44 is "internal target failure": the bridge is alive but cannot
  talk to the SATA device behind it. That is not a filesystem problem, so
  fsck cannot help yet. Work through these in order, cheapest first:

    1. Different USB port, directly on the motherboard (no hub, no
       front-panel header). Re-run this script after each change.
    2. Different cable. USB 3.0 Micro-B cables fail in exactly this way.
    3. Power the enclosure from its own supply, or use a powered hub.
    4. Different enclosure/adapter. A JMS578 that reports ASC 0x44 with a
       known-good SSD is usually the adapter, not the drive.
    5. Shuck the drive: put the bare 2.5-inch SATA SSD in another
       enclosure or plug it into a SATA port. This separates
       "adapter dead" from "drive dead" in one step.

  If the drive reports a capacity in step 5, the data is fine and the
  adapter was the fault. If it still reports 0 in a known-good adapter,
  the SSD controller has failed and only professional recovery will
  read it.

EOF
    exit 0
fi

ok "Capacity: $(cat "$SYS/size") sectors"

# -- 5. partition + filesystem inspection (read-only) -------------------------
say "5. Partition table"
lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,PARTUUID "$DEV" 2>/dev/null | sed 's/^/  /'
echo
fdisk -l "$DEV" 2>/dev/null | sed 's/^/  /'

say "6. Filesystem headers"
SIZE_BYTES=$(( $(cat "$SYS/size") * 512 ))
for part in "${DEV}"?*; do
    [ -b "$part" ] || continue
    TYPE="$(blkid -o value -s TYPE "$part" 2>/dev/null || true)"
    LABEL="$(blkid -o value -s LABEL "$part" 2>/dev/null || true)"
    printf '\n  %s: type=%s label=%s\n' "$part" "${TYPE:-none}" "${LABEL:-none}"
    case "$TYPE" in
        btrfs)
            # Read-only metadata dump. Never mounts, never repairs here.
            btrfs inspect-internal dump-super -f "$part" 2>/dev/null \
                | grep -E "magic|fsid|total_bytes|bytes_used|generation|label|checksum_type" \
                | head -10 | sed 's/^/    /'
            ;;
        ext4|ext3|ext2)
            dumpe2fs -h "$part" 2>/dev/null \
                | grep -E "Filesystem volume name|Filesystem UUID|Filesystem state|Last mount time|Last write time" \
                | sed 's/^/    /'
            ;;
        xfs)
            xfs_db -r -c "sb 0" -c "print" "$part" 2>/dev/null \
                | grep -E "magicnum|uuid|blocksize|dblocks" | head -6 | sed 's/^/    /'
            ;;
        *)
            note "Unknown or unformatted partition"
            ;;
    esac
done

say "7. EFI System Partition contents (bootloader entries)"
for part in "${DEV}"?*; do
    [ -b "$part" ] || continue
    [ "$(blkid -o value -s TYPE "$part" 2>/dev/null)" != "vfat" ] && continue
    MNT="$(mktemp -d)"
    if mount -o ro "$part" "$MNT" 2>/dev/null; then
        echo "  Mounted $part read-only on $MNT"
        find "$MNT" -maxdepth 3 \( -name '*.conf' -o -name 'grub.cfg' -o -name '*.efi' \) 2>/dev/null \
            | sed "s|$MNT|  |" | head -30
        if [ -d "$MNT/loader/entries" ]; then
            echo
            echo "  Boot entries found:"
            for e in "$MNT"/loader/entries/*.conf; do
                [ -f "$e" ] || continue
                echo "    --- $(basename "$e")"
                sed 's/^/      /' "$e"
            done
        fi
        if [ -f "$MNT/EFI/fedora/grub.cfg" ]; then
            echo
            echo "  EFI/fedora/grub.cfg (first 40 lines):"
            head -40 "$MNT/EFI/fedora/grub.cfg" | sed 's/^/    /'
        fi
        umount "$MNT"
    else
        note "Could not mount $part read-only"
    fi
    rmdir "$MNT" 2>/dev/null || true
done

# -- 8. optional read-only filesystem check ----------------------------------
if [ "$REPAIR" = 1 ]; then
    say "8. Read-only filesystem check (-r)"
    for part in "${DEV}"?*; do
        [ -b "$part" ] || continue
        TYPE="$(blkid -o value -s TYPE "$part" 2>/dev/null || true)"
        case "$TYPE" in
            btrfs)
                echo "  btrfs check --readonly $part"
                btrfs check --readonly "$part" 2>&1 | tail -20 | sed 's/^/    /'
                ;;
            ext4|ext3|ext2)
                echo "  e2fsck -fn $part   (no changes)"
                e2fsck -fn "$part" 2>&1 | tail -20 | sed 's/^/    /'
                ;;
            *)
                note "No read-only checker wired for '${TYPE:-unknown}'"
                ;;
        esac
    done
else
    say "8. Filesystem check skipped"
    note "Pass -r to run btrfs check --readonly / e2fsck -fn (still no writes)."
fi

cat <<EOF

==============================================================================
 Next step
==============================================================================
 If the boot failed because the kernel could not find its root, the fix is
 usually in the bootloader entry, not on the filesystem. For a Fedora
 Silverblue/bootc-style install the entry carries an ostree= path such as

   ostree=/ostree/boot.0/default/<checksum>/0

 A hand-written entry that says boot.1 when only boot.0 exists makes
 ostree-prepare-root fail and the system drops to emergency mode. Compare
 the failing entry against a known-good one in step 7 above.

 Mount the root read-only to inspect it:

   mkdir -p /mnt/x58
   mount -o ro <root-partition> /mnt/x58
   cat /mnt/x58/etc/os-release
   ls /mnt/x58/usr/lib/modules/          # kernel versions present
   ls /mnt/x58/boot/loader/entries/ 2>/dev/null

 Do not mount read-write until the copy is verified. If this is the only
 copy of the data, image it first:

   dd if=<root-partition> of=/path/to/x58-root.img bs=64M conv=noerror,sync

 Keep the original drive unmodified until a verified copy exists.
EOF
