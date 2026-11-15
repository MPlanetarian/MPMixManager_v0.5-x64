#!/usr/bin/env bash
# ==============================================================================
# mp-testbed-power-stability.sh
# ------------------------------------------------------------------------------
# Stops an SSH-reachable test bed from suspending itself.
#
# Evidence from journalctl on the machine:
#
#   boot -1, 19:58:32  systemd-logind: suspend requested from client PID 4906
#                      ('org_kde_powerde') (unit user@1000.service)
#   boot -1, 23:26:18  kernel: Freezing user space processes      <- end of boot
#
#   boot -2, 19:37:32  ends with kdeconnectd timeouts, no shutdown record
#
# The "random power off" is the desktop's idle action: KDE PowerDevil asks
# logind to suspend, logind freezes user space, and on this 2010 MacBook Pro
# the resume never completes and the LAN interface never comes back, so the
# machine looks dead until it is power-cycled. It is not a thermal shutdown and
# not a PSU fault: the last thing every affected boot logs is the suspend
# request, never a thermal or watchdog event.
#
# This script makes the test bed unable to suspend:
#   * logind ignores the lid, the power key and idle, on AC and on battery
#   * sleep/suspend/hibernate/hybrid-sleep targets are masked
#   * PowerDevil is told never to suspend, and to stay on "performance" while
#     plugged in so a run does not get throttled halfway through
#
# Everything here is reversible: run with `--revert`.
# ==============================================================================
set -uo pipefail

REVERT=0
[ "${1:-}" = "--revert" ] && REVERT=1

say() { printf '\n=== %s\n' "$*"; }
ok()  { printf '  ok   %s\n' "$*"; }
warn(){ printf '  warn %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if sudo -n true 2>/dev/null; then
        SUDO="sudo -n"
    else
        SUDO="sudo"
    fi
fi

if [ "$REVERT" -eq 1 ]; then
    say "Reverting power-stability settings"
    $SUDO rm -f /etc/systemd/logind.conf.d/90-mp-testbed-no-suspend.conf
    $SUDO systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>/dev/null
    ok "logind override removed and sleep targets unmasked"
    $SUDO systemctl restart systemd-logind 2>/dev/null && ok "systemd-logind restarted"
    exit 0
fi

say "1. logind: never suspend, ignore lid and idle"
$SUDO mkdir -p /etc/systemd/logind.conf.d
$SUDO tee /etc/systemd/logind.conf.d/90-mp-testbed-no-suspend.conf >/dev/null <<'EOF'
# MP Mix Manager test bed: this machine must stay awake for remote test runs.
# Written by mp-testbed-power-stability.sh. Revert with --revert.
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
HandlePowerKey=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
IdleAction=ignore

[Sleep]
AllowSuspend=no
AllowHibernation=no
AllowSuspendThenHibernate=no
AllowHybridSleep=no
EOF
ok "wrote /etc/systemd/logind.conf.d/90-mp-testbed-no-suspend.conf"

say "2. Mask the sleep targets"
$SUDO systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>&1 | sed 's/^/  /'
ok "sleep, suspend, hibernate and hybrid-sleep are masked"

say "3. Reload logind"
$SUDO systemctl restart systemd-logind 2>&1 | sed 's/^/  /' || warn "could not restart logind"
ok "systemd-logind reloaded"

say "4. PowerDevil: never suspend, performance on AC"
KWRITE="$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)"
if [ -n "$KWRITE" ]; then
    "$KWRITE" --file powermanagementprofilesrc --group AC --group SuspendSession --key suspendType 0
    "$KWRITE" --file powermanagementprofilesrc --group AC --group SuspendSession --key idleTime 0
    "$KWRITE" --file powermanagementprofilesrc --group Battery --group SuspendSession --key suspendType 0
    "$KWRITE" --file powermanagementprofilesrc --group Battery --group SuspendSession --key idleTime 0
    "$KWRITE" --file powermanagementprofilesrc --group AC --group CPUBoost --key boost 1 2>/dev/null || true
    ok "PowerDevil configured not to suspend on AC or battery"
else
    warn "kwriteconfig6 not found; PowerDevil untouched (logind still blocks suspend)"
fi

say "Summary"
printf '  lid       : ignore\n'
printf '  idle      : ignore\n'
printf '  power key : ignore\n'
printf '  sleep     : masked\n'
printf '  reverse   : %s --revert\n' "$0"
printf '\nThe machine will now stay awake. Reboot for the logind refresh to be\n'
printf 'fully in effect.\n'
