#!/usr/bin/env bash
# ==============================================================================
# scripts/install_harmony_desktop_shortcuts.sh
# Purpose: Put the MP Harmony Agent, MP Reporter and MP Audio Responder
#          shortcuts on the Desktop.
#
# These three programs are separate from MP Mix Manager, so their shortcuts are
# not written by install_desktop_shortcuts.py (which owns the four manager
# entries). This script owns theirs and can be re-run at any time: existing
# entries are replaced, so an old path is corrected rather than duplicated.
#
# Every entry uses absolute paths and is self-contained: it does not depend on
# ~/.local/bin being on a desktop session's PATH.
#
# Usage:
#   install_harmony_desktop_shortcuts.sh            Install all shortcuts
#   install_harmony_desktop_shortcuts.sh --remove   Remove them again
#   install_harmony_desktop_shortcuts.sh --list     Show what would be written
#
# Environment:
#   MP_DESKTOP_DIR    Desktop directory (default: XDG_DESKTOP_DIR or ~/Desktop)
#   MP_APPLICATIONS_DIR  Application menu directory (default: ~/.local/share/applications)
# ==============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Desktop location. XDG_DESKTOP_DIR is authoritative when it is set.
desktop_dir() {
    if [ -n "${MP_DESKTOP_DIR:-}" ]; then printf '%s\n' "$MP_DESKTOP_DIR"; return; fi
    local user_dirs="$HOME/.config/user-dirs.dirs"
    if [ -f "$user_dirs" ]; then
        local line
        line="$(grep -E '^XDG_DESKTOP_DIR=' "$user_dirs" 2>/dev/null | head -n 1)"
        if [ -n "$line" ]; then
            local v="${line#*=}"
            v="${v%\"}"; v="${v#\"}"
            v="${v//\$HOME/$HOME}"
            if [ -n "$v" ]; then printf '%s\n' "$v"; return; fi
        fi
    fi
    printf '%s\n' "$HOME/Desktop"
}

DESKTOP="$(desktop_dir)"
APPS="${MP_APPLICATIONS_DIR:-$HOME/.local/share/applications}"

# The responder is a separate git checkout; resolve it the same way the menu does.
find_responder() {
    local cand
    for cand in \
        "$HOME/MP_AI_Agent_Audio_Responder-0.1-x64/MP_OpenCode_Audio_Responder.sh" \
        "$HOME/Documents/BASH_SCRIPTS/MP_OpenCode_Audio_Responder.sh"; do
        [ -f "$cand" ] && { printf '%s\n' "$cand"; return 0; }
    done
    return 1
}
RESPONDER="$(find_responder || true)"

# Terminal wrapper. Konsole is what every existing entry on this desktop uses;
# the fallbacks keep the shortcut usable on another desktop session. A plain
# `sh -c` command with Terminal=false would run with no window at all, so the
# terminal emulator is part of the Exec line, not left to the desktop.
terminal_cmd() {
    local title="$1"; shift
    local inner="$*"
    # Escape for the double-quoted sh -c payload and the terminal's own quoting.
    local esc="${inner//\\/\\\\}"
    esc="${esc//\"/\\\"}"
    local esc_title="${title//\"/\\\"}"
    printf 'sh -c "if command -v konsole >/dev/null 2>&1; then exec konsole --hold --qwindowtitle \\"%s\\" -e /bin/bash -lc \\"%s\\"; elif command -v xterm >/dev/null 2>&1; then exec xterm -T \\"%s\\" -e /bin/bash -lc \\"%s\\"; else exec /bin/bash -lc \\"%s\\"; fi"' \
        "$esc_title" "$esc" "$esc_title" "$esc" "$esc"
}

write_entry() {
    local name="$1" comment="$2" exec_line="$3" icon="$4"
    local path="$DESKTOP/$name.desktop"
    {
        printf '[Desktop Entry]\n'
        printf 'Type=Application\n'
        printf 'Version=1.0\n'
        printf 'Name=%s\n' "$name"
        printf 'Comment=%s\n' "$comment"
        printf 'Exec=%s\n' "$exec_line"
        printf 'Icon=%s\n' "$icon"
        printf 'Terminal=false\n'
        printf 'Categories=AudioVideo;\n'
        printf 'StartupNotify=false\n'
    } > "$path"
    chmod +x "$path" 2>/dev/null || true
    # KDE requires the file to be marked trusted, or it opens as text.
    if command -v gio >/dev/null 2>&1; then
        gio set "$path" metadata::trusted true 2>/dev/null || true
    fi
    if command -v kwriteconfig6 >/dev/null 2>&1; then
        kwriteconfig6 --file "$HOME/.config/kiorc" \
            --group "Desktop Entry - $name.desktop" \
            --key "IsTrusted" "true" 2>/dev/null || true
    fi
    printf '  %s✓%s %s\n' "$GREEN" "$NC" "$name"
}

# Harmony entries run a terminal command that stays open. Each one starts the
# piece it needs, so a shortcut always does something useful when clicked.
emit_harmony() {
    local mgr="$MP_ROOT/scripts/manage_harmony_agent.sh"
    local agent="$HOME/OpenAI/MP_Harmony_Agent/MP_Harmony_Agent.py"

    write_entry "MP Harmony Agent (Chat)" \
        "Chat with the MP Harmony Agent, starting the server if needed" \
        "$(terminal_cmd "MP Harmony Agent" "bash '$mgr' chat")" \
        "utilities-terminal"

    write_entry "MP Harmony - Start Server" \
        "Start the MP Harmony voice bridge on port 11435" \
        "$(terminal_cmd "MP Harmony - Start Server" "bash '$mgr' start")" \
        "media-playback-start"

    write_entry "MP Harmony - Stop Server" \
        "Stop the MP Harmony voice bridge" \
        "$(terminal_cmd "MP Harmony - Stop Server" "bash '$mgr' stop")" \
        "media-playback-stop"

    write_entry "MP Harmony - Status" \
        "Show MP Harmony server, model, port and audio archive status" \
        "$(terminal_cmd "MP Harmony - Status" "bash '$mgr' status")" \
        "utilities-system-monitor"

    write_entry "MP Harmony - Web HUD" \
        "Open the MP Harmony Web HUD in the browser" \
        "sh -c \"xdg-open http://127.0.0.1:11435/ || firefox http://127.0.0.1:11435/ || google-chrome http://127.0.0.1:11435/\"" \
        "web-browser"
}

emit_reporter() {
    local rep="$MP_ROOT/scripts/manage_mp_reporter.sh"

    write_entry "MP Reporter Suite" \
        "Open the MP Reporter suite: reports, schedules, email, audio and voice" \
        "$(terminal_cmd "MP Reporter Suite" "bash '$rep'")" \
        "x-office-document"

    write_entry "MP Reporter - Run Daily Now" \
        "Generate today's daily report and open it" \
        "$(terminal_cmd "MP Reporter - Run Daily Now" "bash '$rep' run DAILY_REPORT_TASK_001")" \
        "document-new"

    write_entry "MP Reporter - Status" \
        "Show report schedules, timers and email status" \
        "$(terminal_cmd "MP Reporter - Status" "bash '$MP_ROOT/scripts/mp_scheduled_reporter.sh' --status")" \
        "utilities-system-monitor"

    write_entry "MP Reporter - View Latest" \
        "Open the most recent generated report" \
        "$(terminal_cmd "MP Reporter - View Latest" "bash '$rep' view")" \
        "application-pdf"
}

emit_responder_voice() {
    local rsp="$MP_ROOT/scripts/manage_mp_responder.sh"
    write_entry "MP Audio Responder - Voice Control" \
        "Narration service, speech engine and shared Harmony voice" \
        "$(terminal_cmd "MP Audio Responder - Voice Control" "bash '$rsp'")" \
        "audio-volume-high"
}

ALL_NAMES=(
    "MP Harmony Agent (Chat)"
    "MP Harmony - Start Server"
    "MP Harmony - Stop Server"
    "MP Harmony - Status"
    "MP Harmony - Web HUD"
    "MP Reporter Suite"
    "MP Reporter - Run Daily Now"
    "MP Reporter - Status"
    "MP Reporter - View Latest"
    "MP Audio Responder - Voice Control"
)

do_install() {
    if [ ! -d "$DESKTOP" ]; then
        echo -e "${RED}Desktop directory not found: $DESKTOP${NC}" >&2
        exit 1
    fi
    echo -e "${BOLD}Installing MP Harmony, Reporter and Responder shortcuts${NC}"
    echo -e "${DIM}  Desktop: $DESKTOP${NC}"
    echo ""
    emit_harmony
    emit_reporter
    emit_responder_voice
    echo ""

    # Mirror into the application menu so the entries are searchable too.
    if mkdir -p "$APPS" 2>/dev/null; then
        local n
        for n in "${ALL_NAMES[@]}"; do
            [ -f "$DESKTOP/$n.desktop" ] && cp -f "$DESKTOP/$n.desktop" "$APPS/$n.desktop" 2>/dev/null || true
        done
        command -v update-desktop-database >/dev/null 2>&1 \
            && update-desktop-database "$APPS" >/dev/null 2>&1 || true
    fi

    if [ -z "$RESPONDER" ]; then
        echo -e "${YELLOW}  Note: the MP Audio Responder script was not found;"
        echo -e "  its Voice Control shortcut will report that when opened.${NC}"
    fi
    if [ ! -f "$HOME/OpenAI/MP_Harmony_Agent/MP_Harmony_Agent.py" ]; then
        echo -e "${YELLOW}  Note: the MP Harmony Agent is not installed yet."
        echo -e "  Install it with: scripts/install_harmony_agent.sh install${NC}"
    fi
    echo ""
    echo -e "${GREEN}✓ ${#ALL_NAMES[@]} shortcuts installed.${NC}"
}

do_remove() {
    local n
    echo -e "${BOLD}Removing MP Harmony, Reporter and Responder shortcuts${NC}"
    for n in "${ALL_NAMES[@]}"; do
        rm -f "$DESKTOP/$n.desktop" "$APPS/$n.desktop" 2>/dev/null && echo -e "  ${DIM}removed $n${NC}"
    done
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$APPS" >/dev/null 2>&1 || true
}

do_list() {
    echo -e "${BOLD}Shortcuts this script manages${NC}"
    echo -e "${DIM}Desktop: $DESKTOP${NC}\n"
    local n
    for n in "${ALL_NAMES[@]}"; do
        if [ -f "$DESKTOP/$n.desktop" ]; then
            echo -e "  ${GREEN}present${NC}  $n"
        else
            echo -e "  ${DIM}absent ${NC}  $n"
        fi
    done
}

case "${1:-install}" in
    install|"")  do_install ;;
    --remove|remove) do_remove ;;
    --list|list|--dry-run) do_list ;;
    -h|--help|help)
        sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        ;;
    *)
        echo "Usage: $(basename "$0") [install|--remove|--list]" >&2
        exit 2
        ;;
esac
