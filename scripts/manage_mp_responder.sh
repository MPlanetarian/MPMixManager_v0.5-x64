#!/usr/bin/env bash
# ==============================================================================
# scripts/manage_mp_responder.sh
# Purpose: Full control over the MP OpenCode Audio Responder, and over the
#          shared MP Harmony voice that the Responder and MP Reporter both use.
#
# The Responder itself is a user-level program (it narrates OpenCode agent
# activity, system errors, disk space and the MP Mix Manager error log). It is
# installed at ~/Documents/BASH_SCRIPTS/MP_OpenCode_Audio_Responder.sh with a
# systemd --user unit. This menu is the friendly front end for it.
#
# Usage:
#   manage_mp_responder.sh            Interactive menu
#   manage_mp_responder.sh status     One-shot status
#   manage_mp_responder.sh start|stop|restart|pause|resume
#   manage_mp_responder.sh voice [NAME]   Show or set the shared Harmony voice
#   manage_mp_responder.sh engine [MODE]  Show or set the speech backend
#   manage_mp_responder.sh gitclone   Speak the hourly update now
#                                     (unique cloners + Swansea weather)
#   manage_mp_responder.sh archive    Sync Harmony audio into MP Reporter
# ==============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BLUE='\033[0;34m'; MAGENTA='\033[0;35m'; CYAN='\033[0;36m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TTS_PY="$SCRIPT_DIR/mp_harmony_tts.py"
ARCHIVE_PY="$SCRIPT_DIR/mp_harmony_archive.py"
REPORTER_DIR="${MP_REPORTER_DIR:-$HOME/MP_REPORTER}"
STATE_DIR="${HOME}/.local/state/mp-opencode-audio-responder"
UNIT="mp-opencode-audio-responder.service"

# The responder lives outside the manager tree, in its own repository. Resolve it
# through the same layouts the installer and tests use, so a moved tree is
# picked up by editing one list.
find_responder() {
    local cand
    if [ -n "${MP_RESPONDER_SCRIPT:-}" ] && [ -f "$MP_RESPONDER_SCRIPT" ]; then
        printf '%s\n' "$MP_RESPONDER_SCRIPT"; return 0
    fi
    for cand in \
        "$HOME/MP_AI_Agent_Audio_Responder-0.1-x64/MP_OpenCode_Audio_Responder.sh" \
        "$HOME/Documents/BASH_SCRIPTS/MP_OpenCode_Audio_Responder.sh" \
        "$HOME/BASH_SCRIPTS/MP_OpenCode_Audio_Responder.sh"; do
        [ -f "$cand" ] && { printf '%s\n' "$cand"; return 0; }
    done
    return 1
}

RESPONDER="$(find_responder || true)"

if [ -t 1 ]; then safe_clear() { clear 2>/dev/null || true; }; else safe_clear() { :; }; fi
press_enter() { read -r -p "Press Enter to continue..." _ || true; }

unit_active() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl --user is-active --quiet "$UNIT" 2>/dev/null
}

unit_enabled() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl --user is-enabled --quiet "$UNIT" 2>/dev/null
}

paused() { [ -f "$STATE_DIR/paused" ]; }

engine_value() {
    # The unit sets the engine; fall back to the script default.
    local val=""
    if command -v systemctl >/dev/null 2>&1; then
        val=$(systemctl --user show -p Environment --value "$UNIT" 2>/dev/null \
              | tr ' ' '\n' | sed -n 's/^MP_OPENCODE_TTS_ENGINE=//p' | head -n 1)
    fi
    [ -z "$val" ] && val="wan2gp"
    printf '%s\n' "$val"
}

voice_value() { python3 "$TTS_PY" --voice 2>/dev/null || echo "unavailable"; }

harmony_up() { python3 "$TTS_PY" --check >/dev/null 2>&1; }

require_responder() {
    if [ -z "$RESPONDER" ]; then
        echo -e "${RED}MP OpenCode Audio Responder was not found.${NC}"
        echo -e "${DIM}Expected at ~/Documents/BASH_SCRIPTS/MP_OpenCode_Audio_Responder.sh${NC}"
        echo -e "${DIM}Set MP_RESPONDER_SCRIPT to point at it elsewhere.${NC}"
        return 1
    fi
    return 0
}

show_status() {
    safe_clear
    echo -e "${BOLD}${MAGENTA}======================================================================${NC}"
    echo -e "${BOLD}${MAGENTA}      MP AUDIO RESPONDER & SHARED HARMONY VOICE — STATUS               ${NC}"
    echo -e "${BOLD}${MAGENTA}======================================================================${NC}"

    local svc="${RED}NOT FOUND${NC}"
    if [ -n "$RESPONDER" ]; then
        if unit_active; then svc="${GREEN}● RUNNING${NC}"
        else svc="${YELLOW}○ STOPPED${NC}"; fi
    fi
    local enabled="${DIM}n/a${NC}"
    unit_enabled && enabled="${GREEN}enabled${NC}" || enabled="${DIM}disabled${NC}"
    local playback="${GREEN}active${NC}"
    paused && playback="${YELLOW}PAUSED (monitoring continues)${NC}"
    local harm="${RED}● OFFLINE${NC}"
    harmony_up && harm="${GREEN}● ONLINE${NC}"

    echo -e "  Service:        ${svc}   (${enabled})"
    echo -e "  Unit:           ${CYAN}${UNIT}${NC}"
    echo -e "  Script:         ${DIM}${RESPONDER:-not found}${NC}"
    echo -e "  Playback:       ${playback}"
    echo -e "  Speech engine:  ${BOLD}${CYAN}$(engine_value)${NC}"
    echo -e "  Voice:          ${BOLD}${MAGENTA}$(voice_value)${NC}"
    echo -e "  Harmony bridge: ${harm}"
    echo ""
    if [ -n "$RESPONDER" ]; then
        echo -e "  ${BOLD}Recent log:${NC}"
        local logf="$STATE_DIR/responder.log"
        if [ -f "$logf" ]; then
            tail -n 6 "$logf" | sed 's/^/    /'
        else
            echo -e "    ${DIM}(no log yet)${NC}"
        fi
    fi
    echo ""
}

do_action() {
    require_responder || { press_enter; return 1; }
    case "$1" in
        start)
            if command -v systemctl >/dev/null 2>&1; then
                systemctl --user start "$UNIT" 2>/dev/null \
                    && echo -e "${GREEN}✓ Responder started.${NC}" \
                    || bash "$RESPONDER" start
            else
                bash "$RESPONDER" start
            fi
            ;;
        stop)
            if command -v systemctl >/dev/null 2>&1; then
                systemctl --user stop "$UNIT" 2>/dev/null || true
            fi
            bash "$RESPONDER" stop 2>/dev/null || true
            echo -e "${GREEN}✓ Responder stopped.${NC}"
            ;;
        restart)
            if command -v systemctl >/dev/null 2>&1; then
                systemctl --user restart --no-block "$UNIT" 2>/dev/null \
                    && echo -e "${GREEN}✓ Responder restarted.${NC}" \
                    || bash "$RESPONDER" restart
            else
                bash "$RESPONDER" restart
            fi
            ;;
        pause)  bash "$RESPONDER" pause ;;
        resume) bash "$RESPONDER" resume ;;
        logs)   bash "$RESPONDER" log 60 ;;
        progress) bash "$RESPONDER" progress ;;
        gitclone|hourly|update) bash "$RESPONDER" gitclone ;;
        status) bash "$RESPONDER" status 5 ;;
    esac
}

menu_engine() {
    safe_clear
    echo -e "${BOLD}${CYAN}Speech engine for narration${NC}\n"
    echo -e "  ${BOLD}1)${NC} wan2gp         WAN2GP batch TTS, then the Hawking chain ${DIM}(current: $(engine_value))${NC}"
    echo -e "  ${BOLD}2)${NC} harmony-first  MP Harmony, then WAN2GP, then Hawking ${DIM}[recommended]${NC}"
    echo -e "  ${BOLD}3)${NC} harmony        MP Harmony only"
    echo -e "  ${BOLD}4)${NC} espeak         Hawking chain only"
    echo -e "  ${BOLD}0)${NC} Back"
    echo ""
    read -r -p "Select engine [0-4]: " ch || true
    local mode=""
    case "$ch" in
        1) mode="wan2gp" ;;
        2) mode="harmony-first" ;;
        3) mode="harmony" ;;
        4) mode="espeak" ;;
        0|"") return 0 ;;
        *) echo -e "${RED}Invalid choice.${NC}"; sleep 1; return 0 ;;
    esac
    if [ "$mode" = "harmony" ] || [ "$mode" = "harmony-first" ]; then
        if ! harmony_up; then
            echo -e "${YELLOW}⚠ The MP Harmony bridge is offline; narration will fall back to the Hawking voice.${NC}"
            echo -e "${DIM}  Start it from the main menu: AI Assistant & Local LLM Servers → MP Harmony.${NC}"
        fi
    fi
    if command -v systemctl >/dev/null 2>&1; then
        mkdir -p "$HOME/.config/systemd/user/$UNIT.d" 2>/dev/null || true
        printf '[Service]\nEnvironment=MP_OPENCODE_TTS_ENGINE=%s\n' "$mode" \
            > "$HOME/.config/systemd/user/$UNIT.d/tts-engine.conf" 2>/dev/null || true
        systemctl --user daemon-reload 2>/dev/null || true
        unit_active && systemctl --user restart --no-block "$UNIT" 2>/dev/null || true
        echo -e "${GREEN}✓ Engine set to ${mode}.${NC}"
    else
        echo -e "${YELLOW}systemd is not available; set MP_OPENCODE_TTS_ENGINE=${mode} when starting the responder.${NC}"
    fi
    sleep 1
}

menu_voice() {
    safe_clear
    echo -e "${BOLD}${CYAN}Shared MP Harmony voice${NC}"
    echo -e "${DIM}Used by the Responder and by MP Reporter spoken output.${NC}\n"
    python3 "$TTS_PY" --voices 2>/dev/null || echo -e "${RED}Voice list unavailable.${NC}"
    echo ""
    read -r -p "Enter a voice id to use, or press Enter to cancel: " v || true
    [ -z "$v" ] && return 0
    python3 "$TTS_PY" --set-voice "$v"
    sleep 1
}

menu_archive() {
    safe_clear
    echo -e "${BOLD}${CYAN}Harmony audio → MP Reporter archive${NC}\n"
    python3 "$ARCHIVE_PY" status
    echo ""
    read -r -p "Sync new Harmony speech into TTS_AUDIO/HARMONY now? [y/N] " a || true
    case "${a:-n}" in
        y|Y|yes|YES)
            python3 "$ARCHIVE_PY" sync
            ;;
        *) echo -e "${DIM}Cancelled.${NC}" ;;
    esac
    echo ""
    press_enter
}

main_menu() {
    while true; do
        safe_clear
        local svc_state="${RED}NOT FOUND${NC}"
        if [ -n "$RESPONDER" ]; then
            if unit_active; then svc_state="${GREEN}● RUNNING${NC}"
            else svc_state="${YELLOW}○ STOPPED${NC}"; fi
        fi
        local play_state="${GREEN}speaking${NC}"; paused && play_state="${YELLOW}PAUSED${NC}"
        local harm_state="${RED}● offline${NC}"; harmony_up && harm_state="${GREEN}● online${NC}"

        echo -e "${BOLD}${MAGENTA}======================================================================${NC}"
        echo -e "${BOLD}${MAGENTA}        MP AUDIO RESPONDER & HARMONY VOICE CONTROL                    ${NC}"
        echo -e "${BOLD}${MAGENTA}======================================================================${NC}"
        echo -e "  Service: ${svc_state}  │  Playback: ${play_state}  │  Harmony: ${harm_state}"
        echo -e "  Engine:  ${BOLD}$(engine_value)${NC}  │  Voice: ${BOLD}${MAGENTA}$(voice_value)${NC}\n"

        echo -e "  ${BOLD}SERVICE CONTROL:${NC}"
        echo -e "  ${BOLD}${CYAN}1)${NC} Start Responder            ${DIM}(background narration service)${NC}"
        echo -e "  ${BOLD}${CYAN}2)${NC} Stop Responder"
        echo -e "  ${BOLD}${CYAN}3)${NC} Restart Responder"
        echo -e "  ${BOLD}${CYAN}4)${NC} Pause / Resume Playback     ${DIM}(keeps monitoring and archiving)${NC}"
        echo ""
        echo -e "  ${BOLD}VOICE & SPEECH ENGINE:${NC}"
        echo -e "  ${BOLD}${CYAN}5)${NC} Choose Speech Engine        ${DIM}(Harmony / WAN2GP / Hawking)${NC}"
        echo -e "  ${BOLD}${CYAN}6)${NC} Choose Harmony Voice        ${DIM}(11 Matrix voices, shared with Reporter)${NC}"
        echo -e "  ${BOLD}${CYAN}7)${NC} Test the Voice              ${DIM}(speak a sample phrase now)${NC}"
        echo ""
        echo -e "  ${BOLD}INSPECTION:${NC}"
        echo -e "  ${BOLD}${CYAN}8)${NC} Live Status View            ${DIM}(refreshes; press q to return)${NC}"
        echo -e "  ${BOLD}${CYAN}9)${NC} Tail Responder Log"
        echo -e "  ${BOLD}10)${NC} Harmony Audio → Reporter Archive   ${DIM}(sync TTS_AUDIO/HARMONY)${NC}"
        echo -e "  ${BOLD}${CYAN}11)${NC} Announce Session Progress Now    ${DIM}(speak the 10-minute briefing on demand)${NC}"
        echo -e "  ${BOLD}${CYAN}12)${NC} Announce Hourly Update Now   ${DIM}(unique cloners + Swansea weather)${NC}"
        echo ""
        echo -e "  ${BOLD}${CYAN}0)${NC} Return to the AI Reporting menu ${DIM}(or Esc / q)${NC}\n"

        read -r -p "Enter choice [0-12]: " choice || true
        case "$choice" in
            1) do_action start; press_enter ;;
            2) do_action stop; press_enter ;;
            3) do_action restart; press_enter ;;
            4)
                if paused; then do_action resume; else do_action pause; fi
                sleep 1 ;;
            5) menu_engine ;;
            6) menu_voice ;;
            7)
                safe_clear
                if ! harmony_up; then
                    echo -e "${YELLOW}The Harmony bridge is offline; start it first.${NC}"
                    press_enter; continue
                fi
                echo -e "Speaking a sample with voice ${BOLD}$(voice_value)${NC}..."
                local tmp; tmp="$(mktemp --suffix=.wav 2>/dev/null || echo "/tmp/mp_voice_test_$$.wav")"
                if python3 "$TTS_PY" --say "MP Harmony voice check. The responder and reporter are connected." --out "$tmp" >/dev/null 2>&1; then
                    if command -v pw-play >/dev/null 2>&1; then pw-play "$tmp" 2>/dev/null || true
                    elif command -v paplay >/dev/null 2>&1; then paplay "$tmp" 2>/dev/null || true
                    elif command -v aplay >/dev/null 2>&1; then aplay -q "$tmp" 2>/dev/null || true
                    else echo -e "${DIM}No audio player found; saved to $tmp${NC}"; fi
                    rm -f "$tmp" 2>/dev/null || true
                    echo -e "${GREEN}✓ Voice test complete.${NC}"
                else
                    echo -e "${RED}✗ Voice test failed. Is the Harmony bridge running?${NC}"
                fi
                press_enter ;;
            8) do_action status ;;
            9) do_action logs; press_enter ;;
            10) menu_archive ;;
            11)
                safe_clear
                echo -e "Narrating the session progress briefing..."
                bash "$RESPONDER" progress
                press_enter ;;
            12)
                safe_clear
                echo -e "Announcing the hourly update (unique cloners + Swansea weather)..."
                bash "$RESPONDER" gitclone
                press_enter ;;
            0|q|Q|"") return 0 ;;
            *) echo -e "\n${RED}Invalid option!${NC}"; sleep 1 ;;
        esac
    done
}

case "${1:-}" in
    status)  show_status ;;
    start|stop|restart|pause|resume|logs|progress) do_action "$1" ;;
    gitclone|hourly|update) do_action gitclone ;;
    voice)
        shift || true
        if [ -n "${1:-}" ]; then python3 "$TTS_PY" --set-voice "$1"; else python3 "$TTS_PY" --voice; fi
        ;;
    engine)
        shift || true
        if [ -n "${1:-}" ]; then
            mkdir -p "$HOME/.config/systemd/user/$UNIT.d" 2>/dev/null || true
            printf '[Service]\nEnvironment=MP_OPENCODE_TTS_ENGINE=%s\n' "$1" \
                > "$HOME/.config/systemd/user/$UNIT.d/tts-engine.conf" 2>/dev/null || true
            systemctl --user daemon-reload 2>/dev/null || true
            unit_active && systemctl --user restart --no-block "$UNIT" 2>/dev/null || true
            echo "Engine set to $1"
        else
            engine_value
        fi
        ;;
    archive)
        shift || true
        python3 "$ARCHIVE_PY" sync "$@"
        ;;
    "") main_menu ;;
    *)
        echo "Usage: $(basename "$0") {status|start|stop|restart|pause|resume|progress|gitclone|voice [NAME]|engine [MODE]|archive|logs}" >&2
        exit 2
        ;;
esac
