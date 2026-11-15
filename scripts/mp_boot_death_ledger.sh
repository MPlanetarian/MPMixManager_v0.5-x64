#!/usr/bin/env bash
# ==============================================================================
# mp_boot_death_ledger.sh
# ------------------------------------------------------------------------------
# Records HOW each boot ended, so a hard power loss can be told apart from a
# kernel panic, a watchdog reset or a normal reboot -- after the fact, from the
# journal alone.
#
# Why this exists
# ---------------
# On this machine `oops=panic` is set, so a recoverable kernel oops reboots the
# box in about a second. From the outside that is indistinguishable from a power
# cut: in both cases the journal simply stops. Guessing from the gap between
# boots is unreliable, because a manual power cycle can be any length.
#
# There is a better signal. The kernel reserves a slice of DRAM for crash
# capture (`reserve_mem=8M:4096:ramoops`). DRAM keeps its contents across a
# *warm* reboot, so a panic dump survives and the next boot reports:
#
#     ramoops: using 0x800000@0x87f200000, ecc: 16
#
# A *total power loss* loses DRAM, the ECC header is destroyed, and the next
# boot reports instead:
#
#     ramoops: uncorrectable error in header      (once per zone, usually 10x)
#
# So the verdict for a boot is written by the boot that FOLLOWS it. That is the
# core idea here: each row stores its own facts, and the verdict is derived by
# looking at the next row's `header_err`.
#
# Evidence it was validated against (X299ROG, Nov 2026):
#   * boot that died in `kvm_create_pit` (confirmed Oops + "Kernel panic")
#     -> next boot: header_err=0, dump readable        => warm reset, panic
#   * boot that stopped mid-`sudo ls` with no panic and a 5m07s off period
#     -> next boot: header_err=10, no dump at all      => total power removal
#
# Usage
#   scripts/mp_boot_death_ledger.sh                 # backfill + record + show
#   scripts/mp_boot_death_ledger.sh --show          # show the ledger only
#   scripts/mp_boot_death_ledger.sh --json          # machine readable
#   scripts/mp_boot_death_ledger.sh --boot -2       # one boot, detailed
#   scripts/mp_boot_death_ledger.sh --install       # record automatically
#   scripts/mp_boot_death_ledger.sh --uninstall
#   scripts/mp_boot_death_ledger.sh --reset         # forget the ledger
#
# The ledger is append-only, keyed by boot ID, at
# ${MP_BOOT_LEDGER:-$HOME/.local/state/mp-mix-manager/boot-deaths.tsv}.
# It exists because journald only retains a few boots (~232 MB here); the
# ledger keeps the crash history after the journal has rotated it away.
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/mp-mix-manager"
LEDGER="${MP_BOOT_LEDGER:-$STATE_DIR/boot-deaths.tsv}"
UNIT_NAME="mp-boot-death-ledger.service"

MODE="record"
TARGET_BOOT=""
for arg in "$@"; do
    case "$arg" in
        --show)      MODE="show" ;;
        --json)      MODE="json" ;;
        --install)   MODE="install" ;;
        --uninstall) MODE="uninstall" ;;
        --reset)     MODE="reset" ;;
        --boot)      TARGET_BOOT="__next__" ;;
        -h|--help)   sed -n '2,52p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)
            if [ "$TARGET_BOOT" = "__next__" ]; then TARGET_BOOT="$arg"; fi
            ;;
    esac
done

say()  { printf '\n=== %s\n' "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  warn %s\n' "$*"; }
note() { printf '  %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then SUDO="sudo -n"; else SUDO="sudo"; fi
fi

HEADER='boot_id	boot	start	end	secs	header_err	clean_shutdown	usb_disc	err71	sense	dma'
# Columns: 1 boot_id 2 boot 3 start 4 end 5 secs 6 header_err
#          7 clean_shutdown 8 usb_disc 9 err71 10 sense 11 dma

# --- per-boot facts -----------------------------------------------------------
# header_err is what THIS boot printed about the memory the PREVIOUS boot left
# behind. clean_shutdown describes how THIS boot itself ended (if it did).
read_boot() {
    local b="$1"
    local id start end secs hdr usbdis err71 sense dma clean

    id=$($SUDO journalctl -b "$b" -n 1 -o json --no-pager 2>/dev/null \
         | tr ',' '\n' | sed -n 's/.*"_BOOT_ID":"\([0-9a-f]*\)".*/\1/p' | head -1)
    [ -n "$id" ] || return 1

    start=$($SUDO journalctl -b "$b" -q -o short-iso --no-pager 2>/dev/null | head -1 | awk '{print $1}')
    end=$($SUDO journalctl -b "$b" -q -o short-iso --no-pager 2>/dev/null | tail -1 | awk '{print $1}')
    local s e
    s=$($SUDO journalctl -b "$b" -q -o short-unix --no-pager 2>/dev/null | head -1 | awk '{print int($1)}')
    e=$($SUDO journalctl -b "$b" -q -o short-unix --no-pager 2>/dev/null | tail -1 | awk '{print int($1)}')
    secs=$(( ${e:-0} - ${s:-0} ))
    [ "$secs" -lt 0 ] && secs=0

    local all
    all=$($SUDO journalctl -b "$b" --no-pager 2>/dev/null)

    hdr=$(grep -c "uncorrectable error in header" <<< "$all")
    usbdis=$(grep -c "USB disconnect" <<< "$all")
    err71=$(grep -c "error -71" <<< "$all")
    sense=$(grep -c "Sense Key" <<< "$all")
    dma=$(grep -c "dma error" <<< "$all")

    # Deliberately not `journalctl | grep -q`. Under `set -o pipefail` a `grep -q`
    # that matches early exits immediately, the writer takes SIGPIPE (141), and
    # the pipeline reports failure even though the match succeeded. Feed grep
    # from a herestring instead: no pipe, no SIGPIPE, no false negative.
    if grep -qiE "The system will reboot now|System is rebooting|systemd-shutdown\[|Reached target (Reboot|Power-Off)" <<< "$all"; then
        clean=yes
    else
        clean=no
    fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$id" "$b" "${start}" "${end}" "${secs:-0}" "${hdr:-0}" "$clean" \
        "${usbdis:-0}" "${err71:-0}" "${sense:-0}" "${dma:-0}"
}

has_dump() {
    # A readable pstore dump proves a panic. systemd-pstore empties
    # /sys/fs/pstore into /var/lib/systemd/pstore when the dump survived.
    local d="${1:-/var/lib/systemd/pstore}"
    [ -d "$d" ] && [ -n "$(ls -A "$d" 2>/dev/null)" ]
}

# --- record -------------------------------------------------------------------
# Appends every boot still visible in the journal that the ledger lacks, oldest
# first. This backfills on first run and then simply tops up.
record() {
    mkdir -p "$(dirname "$LEDGER")" 2>/dev/null || true
    [ -f "$LEDGER" ] || printf '%s\n' "$HEADER" > "$LEDGER"

    local rows
    # `--list-boots -o json` emits a single JSON array on one line, so split on
    # the object boundaries before iterating.
    rows=$($SUDO journalctl --list-boots -o json --no-pager 2>/dev/null \
           | sed 's/},{/}\n{/g')
    [ -n "$rows" ] || { warn "could not list boots"; return 1; }

    local added=0 idx
    # journalctl --list-boots -o json yields one object per line, oldest first.
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        idx=$(sed -n 's/.*"index"\s*:\s*\(-\?[0-9]*\).*/\1/p' <<< "$line")
        [ -n "$idx" ] || continue

        local row id
        row=$(read_boot "$idx") || continue
        id=$(printf '%s\n' "$row" | cut -f1)
        [ -n "$id" ] || continue

        if grep -qx "$id" <(cut -f1 "$LEDGER" 2>/dev/null); then
            continue
        fi
        printf '%s\n' "$row" >> "$LEDGER"
        added=$((added + 1))
    done <<< "$rows"

    [ "$added" -gt 0 ] && ok "recorded $added new boot(s)" || ok "ledger already current"
    return 0
}

# --- verdict derivation -------------------------------------------------------
# A boot's fate is only knowable once the next boot has run, because the next
# boot's header_err is what reports whether DRAM (and therefore the dump) was
# lost. The last row is the running boot and has no verdict yet.
# Verdicts are derived after the fact, one per ledger row, in ledger order.
# Must start as an empty ARRAY: `VERDICTS=""` is a scalar, and `VERDICTS+=(x)`
# on a scalar appends to index 0's string, shifting every verdict by one.
VERDICTS=()
derive_verdicts() {
    local ids=() hdrs=() cleans=() secs=() starts=() ends=() boots=()
    local id b start end sec hdr cs usbdis err71 sense dma
    while IFS=$'\t' read -r id b start end sec hdr cs usbdis err71 sense dma; do
        [ "$id" = "boot_id" ] && continue
        ids+=("$id"); boots+=("$b"); starts+=("$start"); ends+=("$end")
        secs+=("$sec"); hdrs+=("$hdr"); cleans+=("$cs")
    done < "$LEDGER"

    local n=${#ids[@]} i next
    local dump=no dump_epoch=0
    if has_dump; then
        dump=yes
        local df
        df=$(ls -t /var/lib/systemd/pstore/* 2>/dev/null | head -1)
        [ -n "$df" ] && dump_epoch=$(stat -c %Y "$df" 2>/dev/null || echo 0)
    fi

    # A dump is only evidence for the death it actually describes. pstore is
    # harvested by the boot AFTER the panic, so the archive file's mtime lands
    # between the dead boot's last journal line and the next boot's first line.
    # Matching that window pins the panic to the right row; without it, a stale
    # dump would label every older warm reset as a panic.
    panic_in_window() {
        local end_iso="$1" start_iso="$2"
        [ "$dump_epoch" -gt 0 ] || return 1
        local e s
        e=$(date -d "$end_iso" +%s 2>/dev/null) || return 1
        s=$(date -d "$start_iso" +%s 2>/dev/null) || return 1
        # Allow a little slack either side for clock and ordering jitter.
        [ "$dump_epoch" -ge $(( e - 60 )) ] && [ "$dump_epoch" -le $(( s + 300 )) ]
    }

    for (( i = 0; i < n; i++ )); do
        next=$(( i + 1 ))
        if [ "$next" -ge "$n" ]; then
            VERDICTS+=("STILL_RUNNING")
        elif [ "${hdrs[$next]}" -gt 0 ] 2>/dev/null; then
            # The following boot found the crash area wiped: power went away.
            VERDICTS+=("POWER_LOSS")
        elif [ "${cleans[$i]}" = "yes" ]; then
            VERDICTS+=("CLEAN_REBOOT")
        elif [ "$dump" = "yes" ] && panic_in_window "${ends[$i]}" "${starts[$next]}"; then
            VERDICTS+=("PANIC")
        else
            VERDICTS+=("UNCLEAR_WARM_RESET")
        fi
    done
}

# --- display ------------------------------------------------------------------
# The stored `boot` column is the journalctl index at the moment the row was
# written, so it goes stale as new boots arrive: two rows can both say "0".
# Ledger order is the stable thing (rows are appended oldest-first), so the
# display derives the index from position and keeps the stored value only as a
# note. The real key is always the boot ID, which never changes.
show() {
    [ -f "$LEDGER" ] || { warn "no ledger yet at $LEDGER"; return 1; }
    derive_verdicts

    printf '\nBOOT DEATH LEDGER  (%s)\n\n' "$LEDGER"
    printf '%-5s %-16s %-16s %-8s %-8s %-9s %s\n' \
        BOOT STARTED ENDED UPTIME HDR_ERR USB/71 VERDICT
    printf '%s\n' "----------------------------------------------------------------------------------------------------"

    local total=0 power=0 panic=0 clean=0 unclear=0 i=0
    local id b start end sec hdr cs usbdis err71 sense dma

    # Row count is needed up front to label rows oldest-first as -(n-1)..0,
    # independent of the stale `boot` index stored in each row.
    local rows
    rows=$(($(grep -c "" "$LEDGER") - 1))   # minus the header line

    while IFS=$'\t' read -r id b start end sec hdr cs usbdis err71 sense dma; do
        [ "$id" = "boot_id" ] && continue
        local up v pos
        up="$(( ${sec:-0} / 3600 ))h$(( (${sec:-0} % 3600) / 60 ))m"
        v="${VERDICTS[$i]:-?}"
        pos=$(( i - rows + 1 ))
        i=$(( i + 1 ))
        total=$((total + 1))
        printf '%-5s %-16s %-16s %-8s %-8s %-9s %s\n' \
            "$pos" "${start:0:16}" "${end:0:16}" "$up" "$hdr" "$usbdis/$err71" "$v"
        case "$v" in
            POWER_LOSS)        power=$((power + 1)) ;;
            PANIC)             panic=$((panic + 1)) ;;
            CLEAN_REBOOT)      clean=$((clean + 1)) ;;
            STILL_RUNNING)     ;;
            *)                 unclear=$((unclear + 1)) ;;
        esac
    done < "$LEDGER"

    local ended=$(( total - 1 ))   # the running boot has not ended yet
    printf '\n%s\n' "----------------------------------------------------------------------------------------------------"
    printf '  %-24s %s\n' "boots recorded"      "$total"
    printf '  %-24s %s\n' "ended boots judged"  "$ended"
    printf '  %-24s %s\n' "total power loss"    "$power"
    printf '  %-24s %s\n' "kernel panic"        "$panic"
    printf '  %-24s %s\n' "clean reboot"        "$clean"
    printf '  %-24s %s\n' "unclear warm reset"  "$unclear"
    if [ "$ended" -gt 0 ]; then
        printf '  %-24s %s%%\n' "power-loss share" "$(( power * 100 / ended ))"
    fi
    printf '\n  HDR_ERR is read from the boot AFTER the one in the row: it is the\n'
    printf '  DRAM crash area that boot found. >0 means the crash area was wiped,\n'
    printf '  so that boot lost power. =0 means DRAM survived a warm reset, which\n'
    printf '  can only be a panic, a watchdog or an orderly reboot.\n\n'
}

json() {
    [ -f "$LEDGER" ] || { warn "no ledger yet at $LEDGER"; return 1; }
    derive_verdicts
    printf '{"ledger":"%s","boots":[' "$LEDGER"
    local first=1 i=0
    local id b start end sec hdr cs usbdis err71 sense dma
    while IFS=$'\t' read -r id b start end sec hdr cs usbdis err71 sense dma; do
        [ "$id" = "boot_id" ] && continue
        [ "$first" -eq 0 ] && printf ','
        first=0
        printf '{"boot_id":"%s","index":"%s","start":"%s","end":"%s","secs":%s,"header_err":%s,"clean_shutdown":"%s","usb_disc":%s,"err71":%s,"sense":%s,"dma":%s,"verdict":"%s"}' \
            "$id" "$b" "$start" "$end" "${sec:-0}" "${hdr:-0}" "$cs" \
            "${usbdis:-0}" "${err71:-0}" "${sense:-0}" "${dma:-0}" "${VERDICTS[$i]:-?}"
        i=$(( i + 1 ))
    done < "$LEDGER"
    printf ']}\n'
}

detail() {
    local b="${TARGET_BOOT:-0}"
    local row
    row=$(read_boot "$b") || { warn "boot $b not found in the journal"; return 1; }

    say "Boot $b"
    local id _ start end secs hdr cs usbdis err71 sense dma
    IFS=$'\t' read -r id _ start end secs hdr cs usbdis err71 sense dma <<< "$row"
    note "boot id        : $id"
    note "started        : $start"
    note "ended          : $end"
    note "uptime         : $(( ${secs:-0} / 3600 ))h$(( (${secs:-0} % 3600) / 60 ))m"
    note "header errors  : ${hdr:-0}   (found by THIS boot: how the PREVIOUS one ended)"
    note "clean shutdown : $cs   (how THIS boot ended, if it did)"
    note "usb disconnect : ${usbdis:-0}   err -71: ${err71:-0}"
    note "scsi sense     : ${sense:-0}"
    note "hda dma errors : ${dma:-0}"

    # Look one boot forward to judge this one.
    local nxt next_hdr=""
    nxt=$(journalctl --list-boots -o json --no-pager 2>/dev/null \
          | grep -o '"index": *-\?[0-9]*' | grep -o '\-\?[0-9]*$' \
          | awk -v t="$b" 'p==1{print; exit} $1==t{p=1}')
    if [ -n "$nxt" ]; then
        next_hdr=$(read_boot "$nxt" 2>/dev/null | cut -f6)
    fi

    local verdict
    if [ -n "$nxt" ] && [ "${next_hdr:-0}" -gt 0 ] 2>/dev/null; then
        verdict="POWER_LOSS   (the next boot found the DRAM crash area wiped)"
    elif [ -z "$nxt" ]; then
        verdict="STILL_RUNNING  (this is the running boot)"
    elif [ "$cs" = "yes" ]; then
        verdict="CLEAN_REBOOT"
    elif has_dump && [ "$(journalctl --list-boots -o json --no-pager 2>/dev/null | wc -l)" -eq 2 ]; then
        verdict="PANIC        (warm reset, no shutdown record, dump on disk)"
    else
        verdict="UNCLEAR_WARM_RESET  (warm reset, no shutdown record)"
    fi
    note "verdict        : $verdict"
}

# --- install ------------------------------------------------------------------
install_unit() {
    command -v systemctl >/dev/null 2>&1 || { warn "systemd not found"; return 1; }
    local unit_dir="$HOME/.config/systemd/user"
    mkdir -p "$unit_dir"
    cat > "$unit_dir/$UNIT_NAME" <<EOF
[Unit]
Description=MP boot death ledger (records how the previous boot ended)
Documentation=file:$SCRIPT_DIR/mp_boot_death_ledger.sh
After=default.target systemd-pstore.service

[Service]
Type=oneshot
ExecStart=$SCRIPT_DIR/mp_boot_death_ledger.sh
RemainAfterExit=yes
Nice=19

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now "$UNIT_NAME" 2>/dev/null \
        && ok "enabled $UNIT_NAME (records once per login)" \
        || warn "could not enable $UNIT_NAME"
    say "Coverage"
    note "A --user unit records at login, which covers every crash this machine"
    note "has produced so far because the desktop logs in automatically. For"
    note "coverage with no user logged in, add the same ExecStart to a root unit:"
    note "  $SCRIPT_DIR/mp_boot_death_ledger.sh"
}

uninstall_unit() {
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/$UNIT_NAME"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "removed $UNIT_NAME (the ledger file is kept)"
}

# --- main ---------------------------------------------------------------------
case "$MODE" in
    install)   install_unit; exit 0 ;;
    uninstall) uninstall_unit; exit 0 ;;
    show)      show; exit 0 ;;
    json)      json; exit 0 ;;
    reset)
        rm -f "$LEDGER"
        ok "ledger cleared: $LEDGER"
        exit 0
        ;;
esac

if [ -n "$TARGET_BOOT" ]; then
    record
    detail
    exit 0
fi

record
show
