#!/usr/bin/env bash
# ==============================================================================
# mp_email_manager.sh - MP Reporter Daily Email Management Utility
# Part of MP_Reporter & MP_Mix_Manager_v0.3 (Dreamworlds Productions)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer the reporter that ships beside this script, so the copy inside the MP
# Mix Manager tree is the one that runs. ~/MP_REPORTER is only a fallback for a
# machine that keeps the Reporter as a separate installation.
if [ -f "${SCRIPT_DIR}/mp_daily_email_reporter.py" ]; then
    PYTHON_SCRIPT="${SCRIPT_DIR}/mp_daily_email_reporter.py"
elif [ -f "${SCRIPT_DIR}/../MP_REPORTER/scripts/mp_daily_email_reporter.py" ]; then
    PYTHON_SCRIPT="${SCRIPT_DIR}/../MP_REPORTER/scripts/mp_daily_email_reporter.py"
elif [ -f "${HOME}/MP_REPORTER/scripts/mp_daily_email_reporter.py" ]; then
    PYTHON_SCRIPT="${HOME}/MP_REPORTER/scripts/mp_daily_email_reporter.py"
else
    PYTHON_SCRIPT="${SCRIPT_DIR}/mp_daily_email_reporter.py"
fi
LOG_FILE="${SCRIPT_DIR}/MP_Reporter_Email.log"

IS_MACOS=false
if [[ "$(uname -s)" == "Darwin" ]]; then
    IS_MACOS=true
fi

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

print_banner() {
    echo -e "${CYAN}${BOLD}"
    echo "======================================================================"
    echo "       MP REPORTER — DAILY EMAIL AUTOMATION & BRIEFING SUITE          "
    echo "======================================================================"
    echo -e "${NC}"
}

usage() {
    print_banner
    echo -e "Usage: $(basename "$0") [COMMAND]\n"
    echo "Commands:"
    echo "  status               Show daily email dispatch status & timer state"
    echo "  send                 Send daily email briefing if scheduled & within limit"
    echo "  force-send           Force send immediately (bypasses 1-email-per-day check)"
    echo "  dry-run              Build single PDF & Stephen Hawking audio without sending"
    echo "  configure-password   Prompt to enter/update iCloud App-Specific Password"
    echo "  test-smtp            Test connection & credentials with iCloud SMTP server"
    echo "  enable-timer         Enable & start systemd / launchd 13:00 daily timer"
    echo "  disable-timer        Disable systemd / launchd 13:00 daily timer"
    echo "  log                  View real-time email dispatch log"
    echo "  help                 Show this help message"
    echo
}

case "${1:-status}" in
    status)
        python3 "$PYTHON_SCRIPT" --status
        if [[ "$IS_MACOS" == "true" ]]; then
            echo -e "\n${BOLD}macOS Launchd Agent Status:${NC}"
            if launchctl list "com.mp-reporter.daily-email" >/dev/null 2>&1; then
                echo -e "${GREEN}  ✓ com.mp-reporter.daily-email is loaded and active.${NC}"
            else
                echo -e "${YELLOW}  ○ com.mp-reporter.daily-email is not active.${NC}"
            fi
        else
            echo -e "\n${BOLD}Systemd Timer Status:${NC}"
            systemctl --user status mp-reporter-daily-email.timer --no-pager 2>/dev/null || echo "  Timer is not active."
        fi
        ;;
    send)
        python3 "$PYTHON_SCRIPT" --send
        ;;
    force-send|force)
        echo -e "${YELLOW}Force-sending daily email report (bypassing daily rate limit)...${NC}"
        python3 "$PYTHON_SCRIPT" --force
        ;;
    dry-run|test-build)
        python3 "$PYTHON_SCRIPT" --dry-run
        ;;
    configure-password|password|auth)
        python3 "$PYTHON_SCRIPT" --configure-password
        ;;
    test-smtp|smtp)
        python3 "$PYTHON_SCRIPT" --test-smtp
        ;;
    enable-timer)
        if [[ "$IS_MACOS" == "true" ]]; then
            plist_dir="${HOME}/Library/LaunchAgents"
            plist_file="${plist_dir}/com.mp-reporter.daily-email.plist"
            mkdir -p "$plist_dir"
            cat > "$plist_file" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mp-reporter.daily-email</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH" python3 "${PYTHON_SCRIPT}" --send</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>13</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
</dict>
</plist>
EOF
            launchctl unload "$plist_file" 2>/dev/null || true
            launchctl load -w "$plist_file"
            echo -e "${GREEN}✓ Daily Email Report launchd agent loaded and active for 13:00 (1:00 PM) daily!${NC}"
        else
            systemctl --user daemon-reload
            systemctl --user enable --now mp-reporter-daily-email.timer
            echo -e "${GREEN}✓ Daily Email Report timer enabled and active for 13:00 (1:00 PM) daily!${NC}"
            systemctl --user list-timers mp-reporter-daily-email.timer
        fi
        ;;
    disable-timer)
        if [[ "$IS_MACOS" == "true" ]]; then
            plist_file="${HOME}/Library/LaunchAgents/com.mp-reporter.daily-email.plist"
            launchctl unload "$plist_file" 2>/dev/null || true
            rm -f "$plist_file"
            echo -e "${YELLOW}○ Daily Email Report launchd agent unloaded and disabled.${NC}"
        else
            systemctl --user stop mp-reporter-daily-email.timer
            systemctl --user disable mp-reporter-daily-email.timer
            echo -e "${YELLOW}○ Daily Email Report timer disabled.${NC}"
        fi
        ;;
    log)
        if [ -f "$LOG_FILE" ]; then
            tail -n 50 -f "$LOG_FILE"
        else
            echo "No log file found at $LOG_FILE"
        fi
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        usage
        exit 1
        ;;
esac
