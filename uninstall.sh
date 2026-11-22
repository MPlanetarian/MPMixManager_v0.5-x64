#!/usr/bin/env bash
# ==============================================================================
# MP Mix Manager - Uninstaller
#
# Reset puts the chosen apps back to first-run settings and leaves them
# installed. Uninstall removes those apps. Settings are kept unless you ask
# for them to be deleted, so a later install can come back as you left it.
#
# While the work runs, Exit_MP_Video.mp4 loops in a borderless window. The
# window stays up until this script exits, including when the movie is shorter
# than the uninstall. SHOW_EXIT_VIDEO=false skips the window.
#
# Music, mixes, playlists, favorites and converted audio are never deleted.
# The install tree is never deleted.
#
#   ./uninstall.sh
#   ./uninstall.sh --uninstall --apps gui --keep-config --yes
#   ./uninstall.sh --uninstall --apps all --delete-config --yes
#   ./uninstall.sh --reset --apps liquid --yes
#   ./uninstall.sh --dry-run --uninstall --apps all --keep-config --yes
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

_resolve_script() {
    local src="$1" dir
    while [ -h "$src" ]; do
        dir="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)"
        src="$(readlink "$src")"
        case "$src" in
            /*) ;;
            *) src="$dir/$src" ;;
        esac
    done
    cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd
}

ROOT="$(_resolve_script "${BASH_SOURCE[0]}")"
ROOT="${MP_UNINSTALL_ROOT:-$ROOT}"
ROOT="${ROOT%/}"

# shellcheck source=scripts/display_loader_video.sh
. "$ROOT/scripts/display_loader_video.sh"

if [ -z "${HOME:-}" ] && [ -n "${USERPROFILE:-}" ]; then
    HOME="$USERPROFILE"
    export HOME
fi

MODE=""
APPS=""
CONFIG_CHOICE=""
ASSUME_YES=0
DRY_RUN=0
NO_VIDEO=0
NO_STOP=0
CONFIG_DIR="${MP_UNINSTALL_CONFIG_DIR:-${HOME:-}/.config/mix-manager}"
DESKTOP_DIR="${MP_UNINSTALL_DESKTOP:-}"
APPS_DIR="${MP_UNINSTALL_APPS_DIR:-}"
HOME_DIR="${MP_UNINSTALL_HOME:-}"

_mp_video_flag=""
_mp_supervisor_pid=""

_mp_cleanup_uninstall_video() {
    set +e
    if [ -n "${_mp_video_flag:-}" ]; then
        rm -f "$_mp_video_flag"
    fi
    if [ -n "${_mp_supervisor_pid:-}" ]; then
        local n=0
        while kill -0 "$_mp_supervisor_pid" 2>/dev/null && [ "$n" -lt 30 ]; do
            sleep 0.1
            n=$((n + 1))
        done
        kill "$_mp_supervisor_pid" 2>/dev/null || true
        wait "$_mp_supervisor_pid" 2>/dev/null || true
    fi
    if [ -n "${_mp_video_flag:-}" ] && [ -f "${_mp_video_flag}.pid" ]; then
        local pid=""
        IFS= read -r pid < "${_mp_video_flag}.pid" || pid=""
        stop_uninstall_exit_video "$pid"
        rm -f "${_mp_video_flag}.pid"
    fi
    return 0
}
trap _mp_cleanup_uninstall_video EXIT

usage() {
    echo -e "${BOLD}MP Mix Manager Uninstaller${NC}"
    echo ""
    echo "Usage: ./uninstall.sh [options]"
    echo ""
    echo "Options:"
    echo "  --reset                   Clear settings for the chosen apps; keep them installed"
    echo "  --uninstall               Remove the chosen apps"
    echo "  --apps LIST               all, or manager,gui,liquid,visualizer,mixer"
    echo "                            (mixer: Linux, FreeBSD, Windows)"
    echo "  --keep-config             Uninstall: keep settings for a later install (default with --yes)"
    echo "  --delete-config           Uninstall: delete that app's saved settings"
    echo "  --yes, -y                 Do not ask for confirmation"
    echo "  --dry-run                 Show what would change and do not open the video"
    echo "  --no-video                Do not play the exit video"
    echo "  --no-stop                 Do not stop running MP apps or the tray"
    echo "  -h, --help                Show this help"
    echo ""
    echo "While the uninstall or reset runs, Exit_MP_Video.mp4 loops in a window"
    echo "until this script exits. Music, mixes, playlists and converted audio"
    echo "are never deleted."
    echo ""
    echo "Removing the last application also removes what the installer added"
    echo "outside this folder: desktop and menu shortcuts, the tray autostart"
    echo "entry, icons, launch agents and user services (Web Companion, bit-rot"
    echo "scrub timer, boot ledger), KDE window rules and the MOTD shell hook."
}

_need_value() {
    if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "$1 needs a value." >&2
        exit 1
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --reset)
            if [ "$MODE" = "uninstall" ]; then
                echo "Choose either --reset or --uninstall." >&2
                exit 1
            fi
            MODE="reset"
            shift
            ;;
        --uninstall)
            if [ "$MODE" = "reset" ]; then
                echo "Choose either --reset or --uninstall." >&2
                exit 1
            fi
            MODE="uninstall"
            shift
            ;;
        --keep-config) CONFIG_CHOICE="keep"; shift ;;
        --delete-config) CONFIG_CHOICE="delete"; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --no-video) NO_VIDEO=1; shift ;;
        --no-stop) NO_STOP=1; shift ;;
        --help|-h) usage; exit 0 ;;
        --apps|--components)
            _need_value "$1" "${2:-}"
            APPS="$2"
            shift 2
            ;;
        --apps=*|--components=*) APPS="${1#*=}"; shift ;;
        --config-dir)
            _need_value "$1" "${2:-}"
            CONFIG_DIR="$2"
            shift 2
            ;;
        --config-dir=*) CONFIG_DIR="${1#*=}"; shift ;;
        --desktop)
            _need_value "$1" "${2:-}"
            DESKTOP_DIR="$2"
            shift 2
            ;;
        --desktop=*) DESKTOP_DIR="${1#*=}"; shift ;;
        --apps-dir)
            _need_value "$1" "${2:-}"
            APPS_DIR="$2"
            shift 2
            ;;
        --apps-dir=*) APPS_DIR="${1#*=}"; shift ;;
        --home)
            _need_value "$1" "${2:-}"
            HOME_DIR="$2"
            shift 2
            ;;
        --home=*) HOME_DIR="${1#*=}"; shift ;;
        --root)
            _need_value "$1" "${2:-}"
            ROOT="$2"
            shift 2
            ;;
        --root=*) ROOT="${1#*=}"; shift ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            echo "Run ./uninstall.sh --help to see the available options." >&2
            exit 1
            ;;
    esac
done

read_answer() {
    local __var="$1" __ans=""
    if [ -t 0 ]; then
        IFS= read -r __ans || __ans=""
    else
        __ans=""
    fi
    printf -v "$__var" '%s' "$__ans"
}

echo ""
echo -e "${BOLD}${MAGENTA}======================================================================${NC}"
echo -e "${BOLD}${MAGENTA}          MP Mix Manager - Reset or Uninstall                        ${NC}"
echo -e "${BOLD}${MAGENTA}======================================================================${NC}"
echo -e "  Install directory:   ${BOLD}${CYAN}${ROOT}${NC}"
echo -e "  Config directory:    ${BOLD}${CYAN}${CONFIG_DIR}${NC}"
echo ""
echo -e "Reset clears an app's saved settings and leaves it installed."
echo -e "Uninstall removes the app. You choose whether its settings stay,"
echo -e "so a later install can keep them."
echo -e "${BOLD}${GREEN}Music, mixes, playlists and converted audio are never deleted.${NC}"
echo ""

if [ -z "$MODE" ]; then
    if [ ! -t 0 ]; then
        echo "A non-interactive run needs --reset or --uninstall." >&2
        exit 1
    fi
    echo -e "  ${BOLD}1)${NC} Reset        ${DIM}(keep the apps, clear their settings)${NC}"
    echo -e "  ${BOLD}2)${NC} Uninstall    ${DIM}(remove the apps)${NC}"
    echo ""
    printf "      Select [1]: "
    read_answer _mode_ans
    case "${_mode_ans:-1}" in
        2) MODE="uninstall" ;;
        *) MODE="reset" ;;
    esac
fi

# The mixer is a GTK/VTE/ALSA host on Linux and FreeBSD and a native
# Tkinter/winmm host on Windows. macOS and WSL do not get it.
_mp_mixer_ok=0
case "$(uname -s 2>/dev/null)" in
    Linux)
        if [ -z "${WSL_DISTRO_NAME:-}" ] && ! grep -qi microsoft /proc/version 2>/dev/null; then
            _mp_mixer_ok=1
        fi
        ;;
    FreeBSD|CYGWIN*|MINGW*|MSYS*) _mp_mixer_ok=1 ;;
esac

if [ -z "$APPS" ]; then
    if [ ! -t 0 ]; then
        APPS="all"
        if [ "$_mp_mixer_ok" -eq 1 ]; then
            echo -e "${DIM}      • Non-interactive run: all five applications.${NC}"
        else
            echo -e "${DIM}      • Non-interactive run: all four applications.${NC}"
        fi
    else
        echo "      Which applications?"
        echo ""
        if [ "$_mp_mixer_ok" -eq 1 ]; then
            echo -e "        ${BOLD}1)${NC} All five"
        else
            echo -e "        ${BOLD}1)${NC} All four"
        fi
        echo -e "        ${BOLD}2)${NC} MP Mix Manager only"
        echo -e "        ${BOLD}3)${NC} MP Gui only"
        echo -e "        ${BOLD}4)${NC} MP Liquid Player only"
        echo -e "        ${BOLD}5)${NC} MP Liquid Visualizer only"
        if [ "$_mp_mixer_ok" -eq 1 ]; then
            echo -e "        ${BOLD}6)${NC} MP Liquid Mixer only"
            echo -e "        ${BOLD}7)${NC} Choose several"
        else
            echo -e "        ${BOLD}6)${NC} Choose several"
        fi
        echo ""
        printf "      Select [1]: "
        read_answer _apps_ans
        _mp_apps_pick_several=0
        case "${_apps_ans:-1}" in
            2) APPS="manager" ;;
            3) APPS="gui" ;;
            4) APPS="liquid" ;;
            5) APPS="visualizer" ;;
            6)
                if [ "$_mp_mixer_ok" -eq 1 ]; then
                    APPS="mixer"
                else
                    _mp_apps_pick_several=1
                fi
                ;;
            7)
                if [ "$_mp_mixer_ok" -eq 1 ]; then
                    _mp_apps_pick_several=1
                else
                    APPS="all"
                fi
                ;;
            *) APPS="all" ;;
        esac
        if [ "$_mp_apps_pick_several" -eq 1 ]; then
            echo ""
            echo -e "        ${DIM}Numbers separated by spaces, e.g.${NC} 2 3"
            if [ "$_mp_mixer_ok" -eq 1 ]; then
                echo -e "        ${DIM}1)${NC} MP Mix Manager   ${DIM}2)${NC} MP Gui   ${DIM}3)${NC} MP Liquid Player   ${DIM}4)${NC} MP Liquid Visualizer   ${DIM}5)${NC} MP Liquid Mixer"
            else
                echo -e "        ${DIM}1)${NC} MP Mix Manager   ${DIM}2)${NC} MP Gui   ${DIM}3)${NC} MP Liquid Player   ${DIM}4)${NC} MP Liquid Visualizer"
            fi
            printf "      Numbers: "
            read_answer _apps_multi
            APPS=""
            for _num in $_apps_multi; do
                case "$_num" in
                    1) APPS="$APPS manager" ;;
                    2) APPS="$APPS gui" ;;
                    3) APPS="$APPS liquid" ;;
                    4) APPS="$APPS visualizer" ;;
                    5)
                        if [ "$_mp_mixer_ok" -eq 1 ]; then
                            APPS="$APPS mixer"
                        fi
                        ;;
                esac
            done
            APPS="${APPS# }"
            APPS="${APPS:-all}"
            unset _apps_multi
        fi
        unset _apps_ans _mp_apps_pick_several
    fi
fi

case "$(printf '%s' "$APPS" | tr '[:upper:]' '[:lower:]')" in
    *mixer*)
        if [ "$_mp_mixer_ok" -ne 1 ]; then
            echo "MP Liquid Mixer is not available on this platform." >&2
            echo "It runs on Linux, FreeBSD and Windows." >&2
            exit 1
        fi
        ;;
esac

if [ "$MODE" = "uninstall" ] && [ -z "$CONFIG_CHOICE" ]; then
    if [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
        CONFIG_CHOICE="keep"
        echo -e "${DIM}      • Keeping saved settings. Pass --delete-config to remove them.${NC}"
    else
        echo ""
        echo "      Delete the saved settings for the apps you are removing?"
        echo -e "        ${BOLD}1)${NC} Keep them     ${DIM}(a later install uses them again)${NC}"
        echo -e "        ${BOLD}2)${NC} Delete them   ${DIM}(equalizer, windows, and that app's own files)${NC}"
        echo ""
        printf "      Select [1]: "
        read_answer _cfg_ans
        case "${_cfg_ans:-1}" in
            2) CONFIG_CHOICE="delete" ;;
            *) CONFIG_CHOICE="keep" ;;
        esac
    fi
fi

echo ""
if [ "$MODE" = "reset" ]; then
    echo -e "  ${BOLD}Reset${NC} settings for: ${CYAN}${APPS}${NC}"
else
    echo -e "  ${BOLD}Uninstall${NC}: ${CYAN}${APPS}${NC}"
    if [ "$CONFIG_CHOICE" = "delete" ]; then
        echo -e "  Settings: ${YELLOW}delete${NC}"
    else
        echo -e "  Settings: ${GREEN}keep${NC}"
    fi
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    _word="RESET"
    [ "$MODE" = "uninstall" ] && _word="UNINSTALL"
    echo ""
    echo -e "      Type ${BOLD}${_word}${NC} to confirm, or press Enter to cancel:"
    printf "      > "
    read_answer _confirm
    if [ "$_confirm" != "$_word" ]; then
        echo -e "\n${YELLOW}Cancelled. Nothing was changed.${NC}"
        exit 10
    fi
fi

_mp_start_exit_video() {
    [ "$DRY_RUN" -eq 0 ] && [ "$NO_VIDEO" -eq 0 ] || return 0
    _mp_video_flag="$(mktemp "${TMPDIR:-/tmp}/mp-uninstall-video.XXXXXX")"
    : > "${_mp_video_flag}.pid"
    (
        set +e
        pid=""
        misses=0
        while [ -f "$_mp_video_flag" ]; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                misses=0
                sleep 0.25
                continue
            fi
            UNINSTALL_VIDEO_PID=""
            start_uninstall_exit_video || true
            pid="${UNINSTALL_VIDEO_PID:-}"
            printf '%s\n' "$pid" > "${_mp_video_flag}.pid"
            if [ -z "$pid" ]; then
                misses=$((misses + 1))
                if [ "$misses" -ge 3 ]; then
                    break
                fi
            else
                misses=0
            fi
            sleep 0.25
        done
        if [ -n "$pid" ]; then
            stop_uninstall_exit_video "$pid"
        fi
    ) &
    _mp_supervisor_pid=$!
    # The window should be up before the removal starts. Give a player that
    # exits immediately a moment to be replaced, then carry on either way.
    local waited=0 live=""
    while [ "$waited" -lt 20 ]; do
        live=""
        IFS= read -r live < "${_mp_video_flag}.pid" 2>/dev/null || live=""
        if [ -n "$live" ] && kill -0 "$live" 2>/dev/null; then
            break
        fi
        if ! kill -0 "$_mp_supervisor_pid" 2>/dev/null; then
            break
        fi
        sleep 0.1
        waited=$((waited + 1))
    done
    if [ -n "$live" ] && kill -0 "$live" 2>/dev/null; then
        echo -e "${DIM}      • Exit video is playing until this finishes.${NC}"
    else
        echo -e "${DIM}      • Exit video was not opened (no display, or it is turned off).${NC}"
    fi
    return 0
}

# An environment variable wins. Otherwise the same config.env switch that
# turns the closing movie off also turns the uninstall window off.
if [ -z "${SHOW_EXIT_VIDEO+x}" ]; then
    _mp_cfg_file=""
    for _mp_cfg_file in \
        "${MP_CONFIG:-}" \
        "${XDG_CONFIG_HOME:-${HOME:-}/.config}/mix-manager/config.env" \
        "$ROOT/config.env"
    do
        [ -n "$_mp_cfg_file" ] && [ -f "$_mp_cfg_file" ] || continue
        _mp_cfg_line="$(MP_CFG_KEY=SHOW_EXIT_VIDEO awk '
            BEGIN { k = ENVIRON["MP_CFG_KEY"] }
            $0 ~ "^[[:space:]]*" k "=" { sub(/\r$/, ""); print; exit }
        ' "$_mp_cfg_file")"
        [ -n "$_mp_cfg_line" ] || continue
        _mp_cfg_value="${_mp_cfg_line#*=}"
        _mp_cfg_value="${_mp_cfg_value#\"}"
        _mp_cfg_value="${_mp_cfg_value%\"}"
        _mp_cfg_value="${_mp_cfg_value#\'}"
        _mp_cfg_value="${_mp_cfg_value%\'}"
        SHOW_EXIT_VIDEO="$_mp_cfg_value"
        export SHOW_EXIT_VIDEO
        break
    done
    unset _mp_cfg_file _mp_cfg_line _mp_cfg_value
fi

_mp_start_exit_video

if [ "$DRY_RUN" -eq 0 ] && [ "$NO_STOP" -eq 0 ]; then
    _killer="$ROOT/scripts/kill_all_mp_audio_players.sh"
    if [ -f "$_killer" ]; then
        bash "$_killer" -q >/dev/null 2>&1 || true
    fi
fi

MP_PYTHON="${MP_PYTHON:-}"
if [ -z "$MP_PYTHON" ]; then
    if command -v python3 >/dev/null 2>&1; then
        MP_PYTHON="$(command -v python3)"
    else
        echo -e "${RED}python3 is required to uninstall.${NC}" >&2
        exit 1
    fi
fi

_result="$(mktemp "${TMPDIR:-/tmp}/mp-uninstall-result.XXXXXX")"
_py_args=(
    "$ROOT/scripts/uninstall_apps.py"
    --mode "$MODE"
    --apps "$APPS"
    --config-dir "$CONFIG_DIR"
    --root "$ROOT"
    --result-file "$_result"
)
if [ "$MODE" = "uninstall" ]; then
    if [ "$CONFIG_CHOICE" = "delete" ]; then
        _py_args+=(--delete-config)
    else
        _py_args+=(--keep-config)
    fi
fi
[ -n "$DESKTOP_DIR" ] && _py_args+=(--desktop "$DESKTOP_DIR")
[ -n "$APPS_DIR" ] && _py_args+=(--apps-dir "$APPS_DIR")
[ -n "$HOME_DIR" ] && _py_args+=(--home "$HOME_DIR")
[ "$DRY_RUN" -eq 1 ] && _py_args+=(--dry-run)
_video_pid=""
if [ -n "${_mp_video_flag:-}" ] && [ -f "${_mp_video_flag}.pid" ]; then
    IFS= read -r _video_pid < "${_mp_video_flag}.pid" || _video_pid=""
fi
[ -n "$_video_pid" ] && _py_args+=(--video-pid "$_video_pid")

echo ""
"$MP_PYTHON" "${_py_args[@]}"

_remaining="none"
if [ -f "$_result" ]; then
    _remaining="$(awk -F= '/^remaining=/{print $2}' "$_result")"
    _alive="$(awk -F= '/^video_alive=/{print $2}' "$_result")"
    if [ "${_alive:-no}" = "yes" ]; then
        echo -e "${DIM}      • Exit video was playing while the work ran.${NC}"
    fi
fi
rm -f "$_result"

if [ "$NO_STOP" -eq 0 ] && [ "$DRY_RUN" -eq 0 ] && [ "${_remaining:-none}" = "none" ] && [ "$MODE" = "uninstall" ]; then
    if command -v pkill >/dev/null 2>&1; then
        pkill -f "mp_system_tray\\.py" >/dev/null 2>&1 || true
        pkill -f "windows_tray\\.ps1" >/dev/null 2>&1 || true
        # The tray and the companion are independent of the five apps, so a
        # full uninstall stops them too; leaving them running would let them
        # rewrite the state that was just deleted.
        pkill -f "MP_Liquid_Mixer\\.py" >/dev/null 2>&1 || true
        pkill -f "black_glass_window\\.py" >/dev/null 2>&1 || true
        pkill -f "schedule_mix_playback\\.py" >/dev/null 2>&1 || true
        pkill -f "mp_visualizer_idle_saver" >/dev/null 2>&1 || true
        pkill -f "mix_web_companion\\.py" >/dev/null 2>&1 || true
    fi
    if [ "$(uname -s 2>/dev/null || echo)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
        launchctl bootout "gui/$(id -u)/com.mplanetarian.mp-mix-manager-tray" >/dev/null 2>&1 || true
    fi
fi

echo ""
if [ "$DRY_RUN" -eq 1 ]; then
    echo -e "${GREEN}Dry run finished. Nothing was changed.${NC}"
else
    echo -e "${GREEN}Finished.${NC}"
fi
exit 0
