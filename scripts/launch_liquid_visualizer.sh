#!/usr/bin/env bash
# ==============================================================================
# MP_Liquid_Visualizer — borderless 3D visualizer
#   1094x734 on macOS, the Liquid Player size on Linux, 700x700 elsewhere
# Backends: Apple Metal (macOS), Vulkan (MoltenVK on macOS), OpenGL.
# The Python entry point picks the backend: Metal on macOS, Vulkan for an
# NVIDIA driver, OpenGL otherwise. Pass --backend to override.
# ==============================================================================
set -euo pipefail

# Ensure Homebrew and standard paths are present on macOS
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN=""
# Prefer an interpreter that can import the visualizer's dependencies.
# Homebrew's python3 comes first on PATH but is not the system GTK python.
_resolver="${SCRIPT_DIR}/resolve_mp_python.sh"
[ ! -f "$_resolver" ] && _resolver="${PROJECT_ROOT}/scripts/resolve_mp_python.sh"
if [ -f "$_resolver" ]; then
    # shellcheck disable=SC1090
    source "$_resolver"
    PYTHON_BIN="$(mp_visualizer_python_bin 2>/dev/null || true)"
fi
unset _resolver
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        echo "Error: Python 3 not found in PATH." >&2
        exit 1
    fi
fi

if [[ -f "${PROJECT_ROOT}/MP_Liquid_Visualizer.py" ]]; then
    TARGET_PY="${PROJECT_ROOT}/MP_Liquid_Visualizer.py"
elif [[ -f "${SCRIPT_DIR}/mp_liquid_visualizer_vulkan.py" ]]; then
    TARGET_PY="${SCRIPT_DIR}/mp_liquid_visualizer_vulkan.py"
else
    echo "Error: MP_Liquid_Visualizer.py not found in ${PROJECT_ROOT}" >&2
    exit 1
fi

# This launcher is the desktop shortcut: a window the user opened themselves.
# Say so explicitly, the same way the player's own "open a free window" path
# does. Without this the renderer falls back to liquid_player_visualizer.json,
# which is written by the player and never cleared when it exits. A stale
# "active": true then makes this window open unfocused and bond "behind" a
# player that is not running -- and with no player window to stack against it
# is pushed to the bottom of the window stack, under every other app, so the
# shortcut looks like it did nothing at all.
#
# MP_VISUALIZER_COMPANION is deliberately not set: the player spawns its own
# companion directly and never goes through this launcher.
export MP_VISUALIZER_DETACHED=1

exec "$PYTHON_BIN" "$TARGET_PY" "$@"
