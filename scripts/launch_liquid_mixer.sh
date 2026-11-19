#!/usr/bin/env bash
# ==============================================================================
# MP Liquid Mixer desktop launcher
#
# The .desktop entry calls this through ~/.local/bin/mp-desktop-exec, so no
# user name or versioned install path is stored in the shortcut. When the
# launcher is copied elsewhere it finds the tree through the shared install
# resolver instead of assuming it is still beside MP_Liquid_Mixer/.
#
# The mixer itself plays Exit_MP_Video.mp4 before its window appears and again
# after the window closes. Nothing is played here: every launch path (the
# shortcut, the X key in another MP window, or ./MP_Liquid_Mixer/run.sh) must
# share one implementation or a second movie can start.
# ==============================================================================

_RESOLVED_SRC="${BASH_SOURCE[0]}"
while [ -h "$_RESOLVED_SRC" ]; do
    _RESOLVED_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
    _RESOLVED_SRC="$(readlink "$_RESOLVED_SRC")"
    [[ $_RESOLVED_SRC != /* ]] && _RESOLVED_SRC="$_RESOLVED_DIR/$_RESOLVED_SRC"
done
SCRIPT_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
unset _RESOLVED_SRC _RESOLVED_DIR

# A copied launcher has no mixer beside it. Resolve the install tree the same
# way mp-desktop-exec does, so moving or renaming the folder keeps working.
if [ ! -f "$BASE_DIR/MP_Liquid_Mixer/run.sh" ]; then
    for _lib in \
        "$SCRIPT_DIR/lib/mp_install_dir.sh" \
        "$SCRIPT_DIR/scripts/lib/mp_install_dir.sh" \
        "$HOME/.config/mix-manager/lib/mp_install_dir.sh"; do
        # shellcheck source=/dev/null
        [ -f "$_lib" ] && . "$_lib" && break
    done
    _found=""
    if command -v mp_find_install_dir >/dev/null 2>&1; then
        _found="$(mp_find_install_dir Mix_Archive_Manager.sh 2>/dev/null || true)"
    fi
    if [ -z "$_found" ]; then
        for _c in $(ls -d "$HOME"/MP*ix*anager* 2>/dev/null | sort -rV); do
            if [ -f "$_c/MP_Liquid_Mixer/run.sh" ]; then
                _found="$_c"
                break
            fi
        done
    fi
    [ -n "$_found" ] && BASE_DIR="$_found"
    unset _found _c _lib
fi

if [ ! -f "$BASE_DIR/MP_Liquid_Mixer/run.sh" ]; then
    echo "MP Liquid Mixer: the install tree could not be found." >&2
    exit 1
fi

# This is a fresh host. A mixer opened from another MP window must not inherit
# that window's deck namespace, session record or X backend choice.
unset GDK_BACKEND MP_GUI_SESSION MP_LIQUID_GLASS_MANAGER MP_LIQUID_GLASS_MIXER
unset MP_PLAYER_NAMESPACE MP_PLAYER_INTRO_DONE

# Pick the interpreter that can actually build the GTK/VTE window. Homebrew's
# python3 is often first on PATH but has no PyGObject, which is what made some
# MP shortcuts do nothing when clicked.
MP_PYTHON=""
_mp_resolver="$BASE_DIR/scripts/resolve_mp_python.sh"
[ ! -f "$_mp_resolver" ] && _mp_resolver="$SCRIPT_DIR/resolve_mp_python.sh"
if [ -f "$_mp_resolver" ]; then
    # shellcheck disable=SC1090
    source "$_mp_resolver"
    MP_PYTHON="$(mp_gui_python_bin 2>/dev/null || true)"
fi
unset _mp_resolver
if [ -z "$MP_PYTHON" ]; then
    if command -v python3 >/dev/null 2>&1; then
        MP_PYTHON="python3"
    elif command -v python >/dev/null 2>&1; then
        MP_PYTHON="python"
    fi
fi
if [ -z "$MP_PYTHON" ]; then
    echo "MP Liquid Mixer: python3 was not found in PATH." >&2
    exit 1
fi
export MP_PYTHON

# The bundled VTE typelib, when the installer put one here.
_VTE_BASE="${HOME}/.local/lib/mp-liquid-glass"
if [ -f "$_VTE_BASE/girepository-1.0/Vte-2.91.typelib" ]; then
    export LD_LIBRARY_PATH="${_VTE_BASE}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export GI_TYPELIB_PATH="${_VTE_BASE}/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
fi
unset _VTE_BASE

cd "$BASE_DIR" || exit 1
exec bash "$BASE_DIR/MP_Liquid_Mixer/run.sh" "$@"
