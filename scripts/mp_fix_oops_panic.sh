#!/usr/bin/env bash
# ==============================================================================
# mp_fix_oops_panic.sh
# ------------------------------------------------------------------------------
# Stops a single kernel oops from rebooting the whole machine, and keeps the
# evidence when something really does die.
#
# The problem
# -----------
# The active boot entry sets `oops=panic`:
#
#   /boot/loader/entries/ostree-99-custom-7.3.4-trimmed.conf
#
# A kernel oops is a *recoverable* fault: the kernel kills the offending task,
# prints a stack trace and carries on. With `oops=panic` it instead panics and
# reboots within about a second (`panic=30`). That makes a driver bug
# indistinguishable from a power cut from the outside -- the journal just stops
# -- and this machine has had 51 unclean boots since 31 Oct.
#
# It has already cost real evidence. On 13 Nov a QEMU start hit this:
#
#   BUG: unable to handle page fault for address: 00000000100023c0
#   RIP: kvm_create_pit+0x68/0x350 [kvm]
#   Kernel panic - not syncing: Fatal exception
#
# That is a recoverable KVM fault that became a full reboot. Without
# `oops=panic` it would have logged, killed the VM and left the desktop running.
#
# What this changes
# -----------------
# It removes ONLY `oops=panic`. Everything else that protects the machine stays:
#
#   nmi_watchdog=panic     hard lockup still panics (correct)
#   softlockup_panic=1     a spinning CPU still panics (correct)
#   hung_task_panic=1      an unkillable task still panics (correct)
#   panic=30               a real panic still reboots after 30s (correct)
#
# Those cover the cases where the machine is genuinely wedged and *should*
# restart. `oops=panic` is different: it converts a survivable fault into a
# reboot, which destroys the very log that would identify it.
#
# Usage
#   scripts/mp_fix_oops_panic.sh              # show what would change
#   scripts/mp_fix_oops_panic.sh --apply      # write the new boot entry
#   scripts/mp_fix_oops_panic.sh --revert     # put `oops=panic` back
#
# Notes
#   * This edits the BLS entry under /boot/loader/entries (the active os-tree
#     deployment's entry), not `rpm-ostree kargs`: this is a hand-built kernel
#     with its own entry, so rpm-ostree does not own it.
#   * The original entry is backed up once, next to itself, and never edited
#     again, so --revert always restores the exact original bytes.
#   * Reboot for the change to take effect.
# ==============================================================================
set -uo pipefail

BOOT_ENTRIES_DIR="/boot/loader/entries"
CANDIDATES=(
    "$BOOT_ENTRIES_DIR/ostree-99-custom-7.3.4-trimmed.conf"
    "$BOOT_ENTRIES_DIR/ostree-99-custom-7.3.4.conf"
)

MODE="dry"
for arg in "$@"; do
    case "$arg" in
        --apply)  MODE="apply" ;;
        --revert) MODE="revert" ;;
        -h|--help) sed -n '2,58p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say()  { printf '\n=== %s\n' "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  warn %s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
diffp(){ printf '  -  %s\n' "$*"; }
diffa(){ printf '  +  %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if sudo -n true 2>/dev/null; then SUDO="sudo -n"; else SUDO="sudo"; fi
fi

find_entry() {
    local c
    for c in "${CANDIDATES[@]}"; do
        if [ -f "$c" ]; then printf '%s' "$c"; return 0; fi
    done
    # Fall back to whichever entry actually carries oops=panic.
    for c in "$BOOT_ENTRIES_DIR"/*.conf; do
        [ -f "$c" ] || continue
        if grep -q "oops=panic" "$c" 2>/dev/null; then printf '%s' "$c"; return 0; fi
    done
    return 1
}

ENTRY="$(find_entry)" || { warn "no boot entry found under $BOOT_ENTRIES_DIR"; exit 1; }

say "Active boot entry"
note "$ENTRY"

ORIG="$ENTRY.mp-original"
CURRENT_KARGS="$(tr ' ' '\n' < /proc/cmdline)"

say "Current protection flags (live kernel)"
for flag in nmi_watchdog=panic softlockup_panic=1 hung_task_panic=1 oops=panic panic=30; do
    if grep -qx "$flag" <<< "$CURRENT_KARGS"; then
        note "present : $flag"
    else
        note "absent  : $flag"
    fi
done

# Build the target cmdline: drop exactly `oops=panic`, keep everything else in
# order, and collapse any doubled spaces the removal leaves behind.
rewrite_options() {
    local file="$1" action="$2"   # action: remove | restore
    local line
    line=$(grep -m1 '^options ' "$file" 2>/dev/null) || return 1
    local opts="${line#options }"
    local out=""
    if [ "$action" = "remove" ]; then
        # shellcheck disable=SC2086
        out=$(printf '%s\n' $opts | grep -vx 'oops=panic' | paste -sd' ' -)
    else
        out="$opts"
    fi
    printf '%s' "$out"
}

case "$MODE" in
    dry)
        say "Proposed change"
        old=$(rewrite_options "$ENTRY" restore)
        new=$(rewrite_options "$ENTRY" remove)
        if [ "$old" = "$new" ]; then
            ok "oops=panic is already absent from the entry; nothing to do"
            exit 0
        fi
        diffp "options $old"
        diffa "options $new"
        printf '\n'
        note "Run with --apply to write this. Reboot afterwards."
        ;;

    apply)
        if [ -f "$ORIG" ]; then
            ok "original already backed up at $ORIG"
        else
            $SUDO cp -p "$ENTRY" "$ORIG" || { warn "could not back up $ENTRY"; exit 1; }
            ok "backed up original to $ORIG"
        fi

        new=$(rewrite_options "$ENTRY" remove)
        if [ -z "$new" ]; then
            warn "could not read the options line from $ENTRY"
            exit 1
        fi

        # Rewrite only the `options` line; keep title/version/linux/initrd as-is.
        tmp=$(mktemp) || exit 1
        awk -v newopts="$new" '
            /^options / { print "options " newopts; next }
            { print }
        ' "$ENTRY" > "$tmp" || { rm -f "$tmp"; exit 1; }

        $SUDO cp "$tmp" "$ENTRY" || { rm -f "$tmp"; warn "could not write $ENTRY"; exit 1; }
        rm -f "$tmp"
        ok "wrote $ENTRY without oops=panic"

        say "Result"
        grep -m1 '^options ' "$ENTRY" | sed 's/^/  /'
        printf '\n'
        note "Reboot for this to take effect."
        note "After reboot, verify: tr ' ' '\\n' < /proc/cmdline | grep oops"
        note "  (no output = oops=panic is gone; a real oops now only kills the task)"
        ;;

    revert)
        if [ ! -f "$ORIG" ]; then
            warn "no backup at $ORIG; cannot revert safely"
            exit 1
        fi
        $SUDO cp -p "$ORIG" "$ENTRY" || { warn "could not restore $ENTRY"; exit 1; }
        ok "restored $ENTRY from $ORIG"
        grep -m1 '^options ' "$ENTRY" | sed 's/^/  /'
        printf '\n'
        note "Reboot for this to take effect."
        ;;
esac
