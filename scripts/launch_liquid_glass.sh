#!/usr/bin/env bash
# ==============================================================================
# MP Liquid Glass Audio Player Desktop Launcher
# Launches the Liquid Glass standalone player with user-configured backend
# (Metal on Apple Silicon macOS, or OpenGL Core 3.3 [Default] / NVIDIA Vulkan on Linux).
# ==============================================================================

# Resolve symlinks so BASE_DIR is accurate even if invoked via ~/.local/bin symlink
_RESOLVED_SRC="${BASH_SOURCE[0]}"
while [ -h "$_RESOLVED_SRC" ]; do
    _RESOLVED_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
    _RESOLVED_SRC="$(readlink "$_RESOLVED_SRC")"
    [[ $_RESOLVED_SRC != /* ]] && _RESOLVED_SRC="$_RESOLVED_DIR/$_RESOLVED_SRC"
done
SCRIPT_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
[ ! -f "$BASE_DIR/MP_Audio_Player_Liquid_Glass_OpenGL.py" ] && [ ! -f "$BASE_DIR/MP_Audio_Player_Liquid_Glass.py" ] && BASE_DIR="$SCRIPT_DIR"

# Fallback candidate search when script is copied to ~/.local/bin/launch-liquid-glass.
# Resolve the install tree by looking for the player, so no user name or
# version-numbered directory is baked in here.
if [ ! -f "$BASE_DIR/MP_Audio_Player_Liquid_Glass_OpenGL.py" ] && [ ! -f "$BASE_DIR/MP_Audio_Player_Liquid_Glass.py" ]; then
    for _lib in \
        "$SCRIPT_DIR/lib/mp_install_dir.sh" \
        "$SCRIPT_DIR/scripts/lib/mp_install_dir.sh" \
        "$HOME/.config/mix-manager/lib/mp_install_dir.sh"; do
        [ -f "$_lib" ] && . "$_lib" && break
    done
    _found=""
    if command -v mp_find_install_dir >/dev/null 2>&1; then
        _found="$(mp_find_install_dir MP_Audio_Player_Liquid_Glass_OpenGL.py 2>/dev/null || true)"
        [ -z "$_found" ] && _found="$(mp_find_install_dir MP_Audio_Player_Liquid_Glass.py 2>/dev/null || true)"
    fi
    # A copied launcher has no library beside it. Take the newest tree in $HOME.
    if [ -z "$_found" ]; then
        for _c in $(ls -d "$HOME"/MP*ix*anager* 2>/dev/null | sort -rV); do
            if [ -f "$_c/MP_Audio_Player_Liquid_Glass_OpenGL.py" ] || [ -f "$_c/MP_Audio_Player_Liquid_Glass.py" ] || [ -f "$_c/MP_Audio_Player_Liquid_Glass_macOS.py" ]; then
                _found="$_c"
                break
            fi
        done
    fi
    [ -n "$_found" ] && BASE_DIR="$_found"
fi
unset _RESOLVED_SRC _RESOLVED_DIR _c _found _lib

CONFIG_FILE="$HOME/.config/mix-manager/config.env"
[ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="$BASE_DIR/config.env"

# Raspberry Pi / low-power ARM detection. A Pi cannot run the Vulkan host
# (liquid_glass_vulkan.py refuses every non-NVIDIA device), so the backend
# choice is corrected here instead of opening a dead window.
_PI_LIB="$BASE_DIR/scripts/lib/mp_pi_detect.sh"
[ ! -f "$_PI_LIB" ] && _PI_LIB="$SCRIPT_DIR/lib/mp_pi_detect.sh"
# shellcheck source=/dev/null
[ -f "$_PI_LIB" ] && . "$_PI_LIB"
if command -v mp_pi_apply_defaults >/dev/null 2>&1; then
    mp_pi_apply_defaults
fi

TARGET_PY=""
# Keep the standalone visualizer on the same GPU path as this player host.
VIS_BACKEND="auto"

# macOS check
if [ "$(uname -s)" = "Darwin" ]; then
    TARGET_PY="$BASE_DIR/MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py"
    [ ! -f "$TARGET_PY" ] && TARGET_PY="$BASE_DIR/scripts/MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py"
    if [ ! -f "$TARGET_PY" ]; then
        if [ "$(uname -m)" != "arm64" ]; then
            echo "Error: MP Audio Player Liquid Glass Metal requires Apple Silicon (M1/M2/M3/M4+). Intel Macs are not supported." >&2
            exit 1
        fi
        TARGET_PY="$BASE_DIR/MP_Audio_Player_Liquid_Glass_macOS.py"
        [ ! -f "$TARGET_PY" ] && TARGET_PY="$BASE_DIR/scripts/MP_Audio_Player_Liquid_Glass_macOS.py"
    fi
fi

if [ -z "$TARGET_PY" ]; then
    BACKEND="opengl"
    if [ -f "$CONFIG_FILE" ]; then
        VAL=$(grep -E "^LIQUID_GLASS_BACKEND=" "$CONFIG_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"'\'' ')
        [ -n "$VAL" ] && BACKEND="$(echo "$VAL" | tr '[:upper:]' '[:lower:]')"
    fi
    # Correct the backend for this machine. On a Raspberry Pi the Vulkan host
    # refuses to draw (no NVIDIA device), so `vulkan` in config.env used to
    # leave the user with a window that never opened. OpenGL via Mesa V3D is
    # the supported path there.
    if command -v mp_renderer_backend >/dev/null 2>&1; then
        RESOLVED_BACKEND="$(mp_renderer_backend "$BACKEND")"
        if [ "$RESOLVED_BACKEND" != "$BACKEND" ]; then
            echo "MP Liquid Glass: $BACKEND is not supported on $(mp_pi_summary | cut -d'|' -f1 | sed 's/ *$//'); using OpenGL." >&2
        fi
        BACKEND="$RESOLVED_BACKEND"
    fi

    VIS_BACKEND="$BACKEND"

    if [ "$BACKEND" = "vulkan" ]; then
        TARGET_PY="$BASE_DIR/MP_Audio_Player_Liquid_Glass.py"
        [ ! -f "$TARGET_PY" ] && TARGET_PY="$BASE_DIR/scripts/MP_Audio_Player_Liquid_Glass.py"
    fi

    if [ -z "$TARGET_PY" ] || [ ! -f "$TARGET_PY" ]; then
        TARGET_PY="$BASE_DIR/MP_Audio_Player_Liquid_Glass_OpenGL.py"
        [ ! -f "$TARGET_PY" ] && TARGET_PY="$BASE_DIR/scripts/MP_Audio_Player_Liquid_Glass_OpenGL.py"
    fi

    if [ ! -f "$TARGET_PY" ]; then
        TARGET_PY="$BASE_DIR/MP_Audio_Player_Liquid_Glass.py"
    fi
fi

# Ensure audio volume level is set to 100% and unmuted
#if command -v wpctl >/dev/null 2>&1; then
#    wpctl set-mute @DEFAULT_AUDIO_SINK@ 0 2>/dev/null || true
#    wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 2>/dev/null || true
#fi
#if command -v pactl >/dev/null 2>&1; then
#    pactl set-sink-mute @DEFAULT_SINK@ 0 2>/dev/null || true
#    pactl set-sink-volume @DEFAULT_SINK@ 100% 2>/dev/null || true
#    pactl list short sinks 2>/dev/null | awk '{print $1}' | while read -r _s; do
#        pactl set-sink-mute "$_s" 0 2>/dev/null || true
#        pactl set-sink-volume "$_s" 100% 2>/dev/null || true
#    done
#fi

# Ensure MP Audio Player engine volume is set to 100%
#if [ -f "$BASE_DIR/MP_Audio_Player.py" ]; then
#    python3 "$BASE_DIR/MP_Audio_Player.py" --vol 100 2>/dev/null || true
#fi

# Pick the interpreter that can actually build the GTK window. Homebrew's
# python3 is first on PATH but has no PyGObject, which killed every MP
# shortcut with "No module named 'gi'".
PYTHON_BIN=""
_mp_resolver="$BASE_DIR/scripts/resolve_mp_python.sh"
[ ! -f "$_mp_resolver" ] && _mp_resolver="$SCRIPT_DIR/resolve_mp_python.sh"
if [ -f "$_mp_resolver" ]; then
    # shellcheck disable=SC1090
    source "$_mp_resolver"
    PYTHON_BIN="$(mp_gui_python_bin 2>/dev/null || true)"
fi
unset _mp_resolver
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    fi
fi

# If a previous window opened on the Hisense, bring it back to the main screen.
# Run it in the background: it only repositions windows that are already open,
# and the new window must not wait on wmctrl before it can appear.
"$PYTHON_BIN" - "$BASE_DIR" << 'PY' >/dev/null 2>&1 &
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from desktop_launch_screen import reposition_off_hisense
reposition_off_hisense()
PY
disown 2>/dev/null || true

# One tray icon shared with MP Mix Manager and MP Gui.
_mp_tray=""
for _mp_tray in \
    "$BASE_DIR/scripts/mp_system_tray.sh" \
    "$SCRIPT_DIR/mp_system_tray.sh"
do
    if [ -f "$_mp_tray" ]; then
        if command -v setsid >/dev/null 2>&1; then
            setsid bash "$_mp_tray" --ensure >/dev/null 2>&1 </dev/null &
        else
            nohup bash "$_mp_tray" --ensure >/dev/null 2>&1 </dev/null &
        fi
        disown 2>/dev/null || true
        break
    fi
done
unset _mp_tray

# Four MP Liquid Player windows at most. The claim below is atomic, so two
# shortcuts pressed together cannot both take the last slot. Each launch
# opens another window until the fourth; only a fifth focuses a window.
_mp_limit_sh="$BASE_DIR/scripts/mp_instance_limit.sh"
[ ! -f "$_mp_limit_sh" ] && _mp_limit_sh="$SCRIPT_DIR/mp_instance_limit.sh"
if [ -f "$_mp_limit_sh" ]; then
    # shellcheck disable=SC1090
    source "$_mp_limit_sh"
    if ! mp_instance_claim player; then
        mp_instance_focus player || true
        mp_instance_notify_limit player
        exit 0
    fi
    # The first MP Liquid Player window of a session plays Exit_MP_Video.mp4
    # before its glass appears. Do it here, before the companion visualizer
    # starts, so the movie is the only thing on screen. Windows two to four
    # open straight away. The host repeats the election (mp_instance_limit.py)
    # for launch paths that skip this script; a second movie cannot happen
    # because the claim above is already counted.
    #
    # Helper flags draw no player window, so they get no movie either.
    _mp_intro_wanted=1
    for _mp_arg in "$@"; do
        case "$_mp_arg" in
            --self-test|--embed-check|--list-fonts|--manager)
                _mp_intro_wanted=0
                ;;
            --set-font|--set-font=*|--tracklist|--tracklist=*)
                _mp_intro_wanted=0
                ;;
        esac
    done
    if [ "$_mp_intro_wanted" = "1" ] && mp_instance_is_first_player; then
        _mp_intro_sh="$BASE_DIR/scripts/display_loader_video.sh"
        [ ! -f "$_mp_intro_sh" ] && _mp_intro_sh="$SCRIPT_DIR/display_loader_video.sh"
        if [ -f "$_mp_intro_sh" ]; then
            bash "$_mp_intro_sh" --intro || true
            # The host repeats the election for launch paths that skip this
            # script. Tell it this launch has already had its movie, or a
            # launcher-started window would play it a second time.
            export MP_PLAYER_INTRO_DONE=1
        fi
    fi
fi
unset _mp_intro_sh _mp_intro_wanted _mp_arg

# Start the companion visualizer. The Liquid Player closes this window on exit.
# MP_VISUALIZER_COMPANION marks it as the window the player owns, so it stays
# bonded behind the player permanently. A visualizer opened later (Z) is a
# separate window and stays free.
#
# The pattern must match the interpreter invocation, not any shell whose text
# merely mentions the script name, or a wrapper script would look like a
# running visualizer and suppress the companion.
_mp_vis_running() {
    pgrep -f "python[^ ]* [^ ]*MP_Liquid_Visualizer\.py" >/dev/null 2>&1 \
        || pgrep -f "python[^ ]* [^ ]*mp_liquid_visualizer_vulkan\.py" >/dev/null 2>&1
}
if ! _mp_vis_running; then
    VIS_PY="$BASE_DIR/MP_Liquid_Visualizer.py"
    [ ! -f "$VIS_PY" ] && VIS_PY="$BASE_DIR/scripts/mp_liquid_visualizer_vulkan.py"
    if [ -f "$VIS_PY" ]; then
        # Mark it active before the child starts, or its first poll reads
        # active:false and the window never bonds to this player. A fresh
        # player shows its background, so ``visible`` is reset to True; the
        # Visualization button can hide it again for this session.
        "$PYTHON_BIN" - "$BASE_DIR" <<'PYVIS' >/dev/null 2>&1 || true
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "scripts"))
try:
    from liquid_player_visualizer import mark_player_visualizer_attached
    mark_player_visualizer_attached(visible=True)
except Exception:
    pass
PYVIS
        MP_VISUALIZER_COMPANION=1 nohup "$PYTHON_BIN" "$VIS_PY" --preset random --backend "$VIS_BACKEND" >> "$HOME/.config/mix-manager/liquid_visualizer.log" 2>&1 &
    fi
fi

mkdir -p "$HOME/.config/mix-manager" 2>/dev/null || true
_VTE_BASE="${HOME}/.local/lib/mp-liquid-glass"
if [ -f "$_VTE_BASE/girepository-1.0/Vte-2.91.typelib" ]; then
    export LD_LIBRARY_PATH="${_VTE_BASE}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export GI_TYPELIB_PATH="${_VTE_BASE}/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
fi
cd "$BASE_DIR" || exit 1
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi
if [ "$(uname -s)" = "Darwin" ] && [ -f "$BASE_DIR/scripts/ensure_mp_python_env.sh" ]; then
    if _mp_env_python="$(bash "$BASE_DIR/scripts/ensure_mp_python_env.sh")"; then
        PYTHON_BIN="$_mp_env_python"
    else
        echo "Error: MP Liquid Player could not prepare its macOS audio environment." >&2
        exit 1
    fi
fi
for _py in \
    "$PYTHON_BIN" \
    "$HOME/.local/share/mp-mix-manager/python-env/bin/python3" \
    "$HOME/.local/share/mp-mix-manager/python-env/bin/python" \
    "$BASE_DIR/.venv/bin/python3" \
    "$BASE_DIR/.venv/bin/python"; do
    if [ -x "$_py" ]; then
        PYTHON_BIN="$_py"
        break
    fi
done
# Reset the shared equalizer whenever MP Liquid is launched on Linux. This must
# finish before the player reads the equalizer, so it stays synchronous.
if [ "$(uname -s 2>/dev/null)" = "Linux" ] && [ -f "$BASE_DIR/MP_Audio_Player.py" ]; then
    "$PYTHON_BIN" "$BASE_DIR/MP_Audio_Player.py" --reset-eq >/dev/null 2>&1 || true
fi
exec "$PYTHON_BIN" "$TARGET_PY" "$@"
