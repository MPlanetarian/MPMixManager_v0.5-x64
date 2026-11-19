#!/usr/bin/env bash
# ==============================================================================
# MP_Visualization_Generator.sh — launcher for the MP Visualisation Generator
#
# Builds 3D audio-reactive MP Visualizer presets from background artwork and
# installs them for the OpenGL, Vulkan and Metal backends. The GUI uses the
# Liquid Player's own GlassHost class, so it follows the active MP theme.
# ==============================================================================
_RESOLVED_SRC="${BASH_SOURCE[0]}"
while [ -h "$_RESOLVED_SRC" ]; do
    _RESOLVED_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
    _RESOLVED_SRC="$(readlink "$_RESOLVED_SRC")"
    [[ $_RESOLVED_SRC != /* ]] && _RESOLVED_SRC="$_RESOLVED_DIR/$_RESOLVED_SRC"
done
SCRIPT_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
unset _RESOLVED_SRC _RESOLVED_DIR

GENERATOR="$SCRIPT_DIR/MP_Visualization_Generator.py"
if [ ! -f "$GENERATOR" ]; then
    echo "Error: MP_Visualization_Generator.py was not found beside this launcher." >&2
    exit 1
fi

PY="python3"
# MP Gui needs an interpreter that can import Gtk; Homebrew python3 often
# cannot. Use the same resolver MP_Gui.sh uses.
if [ -f "$SCRIPT_DIR/scripts/resolve_mp_python.sh" ]; then
    # shellcheck source=/dev/null
    . "$SCRIPT_DIR/scripts/resolve_mp_python.sh"
    _resolved="$(mp_gui_python_bin 2>/dev/null || true)"
    [ -n "$_resolved" ] && PY="$_resolved"
fi
if ! "$PY" -c "import gi" >/dev/null 2>&1; then
    echo "Error: PyGObject (python3-gi / gi) is required for the generator window." >&2
    echo "Install it with your package manager (for example: sudo dnf install python3-gobject)." >&2
    exit 1
fi

exec "$PY" "$GENERATOR" "$@"
