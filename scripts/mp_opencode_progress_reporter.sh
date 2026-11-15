#!/usr/bin/env bash
# ==============================================================================
# mp_opencode_progress_reporter.sh - MP Reporter OpenCode Progress Scheduler
# Part of MP_Reporter & MP_Mix_Manager (Dreamworlds Productions)
#
# Installs, starts, stops and reports on the systemd --user timer that emails
# the OpenCode agent progress briefing (text + Stephen Hawking audio) on a
# fixed interval, ten minutes by default.
#
# Usage
#   mp_opencode_progress_reporter.sh enable [MINUTES]   Install & start (10 min default)
#   mp_opencode_progress_reporter.sh disable            Stop & remove the timer
#   mp_opencode_progress_reporter.sh status             Service, timer & reporter status
#   mp_opencode_progress_reporter.sh send               Send now if this bucket is due
#   mp_opencode_progress_reporter.sh force              Send a briefing immediately
#   mp_opencode_progress_reporter.sh dry-run           Build both reports, no email
#   mp_opencode_progress_reporter.sh log [N]            Tail the reporter log
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORTER_PY="${SCRIPT_DIR}/scripts/mp_opencode_progress_reporter.py"
[ ! -f "$REPORTER_PY" ] && REPORTER_PY="${SCRIPT_DIR}/mp_opencode_progress_reporter.py"

MP_REPORTER_DIR="${MP_REPORTER_DIR:-$HOME/MP_REPORTER}"
LOG_FILE="${MP_REPORTER_DIR}/MP_Reporter_Email.log"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_UNIT="mp-reporter-opencode-progress.service"
TIMER_UNIT="mp-reporter-opencode-progress.timer"
DEFAULT_MINUTES=10

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

PYTHON_BIN=""
resolve_python() {
    local cand
    for cand in "${MP_PYTHON:-}" /usr/bin/python3 python3; do
        [ -n "$cand" ] || continue
        if "$cand" -c 'import smtplib, sqlite3' >/dev/null 2>&1; then
            PYTHON_BIN="$cand"
            return 0
        fi
    done
    PYTHON_BIN="${MP_PYTHON:-python3}"
}

interval_minutes() {
    local value="${1:-}"
    if [ -z "$value" ]; then
        value="$(grep -E '^OPENCODE_PROGRESS_INTERVAL_MINUTES=' \
            "${MP_REPORTER_DIR}/email_config.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)"
    fi
    case "$value" in
        ''|*[!0-9]*) value="$DEFAULT_MINUTES" ;;
    esac
    [ "$value" -lt 1 ] && value="$DEFAULT_MINUTES"
    printf '%s' "$value"
}

write_units() {
    local minutes="$1"
    mkdir -p "$UNIT_DIR"
    cat >"${UNIT_DIR}/${SERVICE_UNIT}" <<EOF
[Unit]
Description=MP Reporter OpenCode Agent Progress Briefing (text + Stephen Hawking audio)
Documentation=file://${MP_REPORTER_DIR}/README.md
After=network-online.target

[Service]
Type=oneshot
KillMode=process
TimeoutStartSec=600
WorkingDirectory=${MP_REPORTER_DIR}
ExecStart=${PYTHON_BIN} ${REPORTER_PY} --send
EOF

    cat >"${UNIT_DIR}/${TIMER_UNIT}" <<EOF
[Unit]
Description=MP Reporter OpenCode progress briefing every ${minutes} minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=${minutes}min
AccuracySec=15s
Persistent=false
Unit=${SERVICE_UNIT}

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
}

case "${1:-status}" in
    enable|install)
        resolve_python
        if [ ! -f "$REPORTER_PY" ]; then
            echo -e "${RED}Reporter module not found: ${REPORTER_PY}${NC}" >&2
            exit 1
        fi
        minutes="$(interval_minutes "${2:-}")"
        write_units "$minutes"
        systemctl --user enable --now "$TIMER_UNIT"
        echo -e "${GREEN}OpenCode progress briefings enabled every ${minutes} minutes.${NC}"
        echo -e "${DIM}Text report + Stephen Hawking audio, emailed to the configured recipients.${NC}"
        systemctl --user list-timers --all "$TIMER_UNIT" --no-pager || true
        ;;
    disable|stop|uninstall)
        systemctl --user disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true
        systemctl --user stop "$SERVICE_UNIT" >/dev/null 2>&1 || true
        rm -f "${UNIT_DIR}/${SERVICE_UNIT}" "${UNIT_DIR}/${TIMER_UNIT}"
        systemctl --user daemon-reload
        echo -e "${YELLOW}OpenCode progress briefings disabled; timer units removed.${NC}"
        ;;
    status)
        resolve_python
        "$PYTHON_BIN" "$REPORTER_PY" --status
        echo ""
        echo -e "${BOLD}Systemd timer:${NC}"
        systemctl --user status "$TIMER_UNIT" --no-pager 2>/dev/null \
            | sed -n '1,6p' || echo "  Timer is not installed."
        echo ""
        systemctl --user list-timers --all "$TIMER_UNIT" --no-pager 2>/dev/null || true
        ;;
    send)    resolve_python; "$PYTHON_BIN" "$REPORTER_PY" --send ;;
    force)   resolve_python; "$PYTHON_BIN" "$REPORTER_PY" --force ;;
    dry-run|dryrun|test-build) resolve_python; "$PYTHON_BIN" "$REPORTER_PY" --dry-run ;;
    print|text) resolve_python; "$PYTHON_BIN" "$REPORTER_PY" --print ;;
    log|logs)
        count="${2:-40}"
        if [ -f "$LOG_FILE" ]; then
            grep -a 'opencode-progress' "$LOG_FILE" | tail -n "$count"
        else
            echo "No log yet: $LOG_FILE"
        fi
        ;;
    install-service) write_units "$(interval_minutes "${2:-}")" ;;
    *)
        sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        ;;
esac
