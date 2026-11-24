#!/usr/bin/env bash
# ==============================================================================
# mp_auto_responder.sh - MP Auto Responder GitHub Project Monitor
# Part of MP_Reporter & MP_Mix_Manager (Dreamworlds Productions)
#
# Installs, starts, stops and reports on the systemd --user timer that watches
# a GitHub project's issues and pull requests on a fixed interval (twenty
# minutes by default). Every tick emails a text report, an audio transcript and
# a spoken MP3 briefing, and announces the same briefing on this workstation.
# The monitor ships disabled and unconfigured: set AUTO_RESPONDER_GITHUB_REPO
# and AUTO_RESPONDER_ENABLED="true" in email_config.env before enabling it.
#
# Usage
#   mp_auto_responder.sh enable [MINUTES]   Install & start (20 min default)
#   mp_auto_responder.sh disable            Stop & remove the timer
#   mp_auto_responder.sh status             Service, timer & reporter status
#   mp_auto_responder.sh send               Send now if this bucket is due
#   mp_auto_responder.sh force              Send a briefing immediately
#   mp_auto_responder.sh dry-run            Build everything, no email
#   mp_auto_responder.sh print              Print the text report only
#   mp_auto_responder.sh log [N]            Tail the Auto Responder log
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORTER_PY="${SCRIPT_DIR}/scripts/mp_auto_responder.py"
[ ! -f "$REPORTER_PY" ] && REPORTER_PY="${SCRIPT_DIR}/mp_auto_responder.py"

MP_REPORTER_DIR="${MP_REPORTER_DIR:-$HOME/MP_REPORTER}"
LOG_FILE="${MP_REPORTER_DIR}/MP_Reporter_Email.log"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_UNIT="mp-auto-responder.service"
TIMER_UNIT="mp-auto-responder.timer"
DEFAULT_MINUTES=20

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

PYTHON_BIN=""
resolve_python() {
    local cand
    for cand in "${MP_PYTHON:-}" /usr/bin/python3 python3; do
        [ -n "$cand" ] || continue
        if "$cand" -c 'import smtplib, urllib.request, email' >/dev/null 2>&1; then
            PYTHON_BIN="$cand"
            return 0
        fi
    done
    PYTHON_BIN="${MP_PYTHON:-python3}"
}

interval_minutes() {
    local value="${1:-}"
    if [ -z "$value" ]; then
        value="$(grep -E '^AUTO_RESPONDER_INTERVAL_MINUTES=' \
            "${MP_REPORTER_DIR}/email_config.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)"
    fi
    case "$value" in
        ''|*[!0-9]*) value="$DEFAULT_MINUTES" ;;
    esac
    [ "$value" -lt 1 ] && value="$DEFAULT_MINUTES"
    printf '%s' "$value"
}

config_value() {
    local key="$1" value="${!1:-}"
    if [ -z "$value" ] && [ -f "${MP_REPORTER_DIR}/email_config.env" ]; then
        value="$(grep -E "^${key}=" "${MP_REPORTER_DIR}/email_config.env" \
            2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' || true)"
    fi
    printf '%s' "$value"
}

write_units() {
    local minutes="$1"
    mkdir -p "$UNIT_DIR"
    cat >"${UNIT_DIR}/${SERVICE_UNIT}" <<EOF
[Unit]
Description=MP Auto Responder GitHub project briefing (text + transcript + audio + email)
Documentation=file://${MP_REPORTER_DIR}/README.md
After=network-online.target

[Service]
Type=oneshot
KillMode=process
TimeoutStartSec=900
WorkingDirectory=${MP_REPORTER_DIR}
Environment=PATH=/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${PYTHON_BIN} ${REPORTER_PY} --send
EOF

    cat >"${UNIT_DIR}/${TIMER_UNIT}" <<EOF
[Unit]
Description=MP Auto Responder project briefing every ${minutes} minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=${minutes}min
AccuracySec=20s
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
        repo="$(config_value AUTO_RESPONDER_GITHUB_REPO)"
        if [ -z "$repo" ]; then
            echo -e "${RED}No repository configured.${NC} Set AUTO_RESPONDER_GITHUB_REPO in ${MP_REPORTER_DIR}/email_config.env to the owner/name of the project to monitor." >&2
            exit 1
        fi
        if [ "$(config_value AUTO_RESPONDER_ENABLED)" != "true" ]; then
            echo -e "${YELLOW}AUTO_RESPONDER_ENABLED is not \"true\" in ${MP_REPORTER_DIR}/email_config.env; the timer will be installed but will send nothing.${NC}" >&2
        fi
        minutes="$(interval_minutes "${2:-}")"
        write_units "$minutes"
        systemctl --user enable --now "$TIMER_UNIT"
        echo -e "${GREEN}MP Auto Responder enabled every ${minutes} minutes.${NC}"
        echo -e "${DIM}Text report + transcript + audio briefing emailed; the briefing is also announced on this workstation.${NC}"
        systemctl --user list-timers --all "$TIMER_UNIT" --no-pager || true
        ;;
    disable|stop|uninstall)
        systemctl --user disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true
        systemctl --user stop "$SERVICE_UNIT" >/dev/null 2>&1 || true
        rm -f "${UNIT_DIR}/${SERVICE_UNIT}" "${UNIT_DIR}/${TIMER_UNIT}"
        systemctl --user daemon-reload
        echo -e "${YELLOW}MP Auto Responder disabled; timer units removed.${NC}"
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
            grep -a 'auto-responder' "$LOG_FILE" | tail -n "$count"
        else
            echo "No log yet: $LOG_FILE"
        fi
        ;;
    install-service) write_units "$(interval_minutes "${2:-}")" ;;
    *)
        sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        ;;
esac
