#!/usr/bin/env bash
# ==============================================================================
# mp_benchmark_mode.sh - Load all four MP desktop shortcuts and time them
# ------------------------------------------------------------------------------
# Benchmark Mode answers one question: "how long until all four MP desktop
# shortcuts are fully loaded and ready?" It launches the same commands the
# .desktop files launch, waits for each one to reach a usable state, records
# the moment each became ready, and reports:
#
#   * per-shortcut time-to-ready
#   * total wall time for all four (sequential launch, the way a user clicks)
#   * peak CPU and RSS per shortcut
#   * whether each one was still alive and responsive at the end
#
# Readiness is detected with a real signal, not a fixed sleep:
#   MP Mix Manager      its console prints the menu banner
#   MP Liquid Player    a window exists on the compositor / WindowServer
#   MP Liquid Visualizer a GL/Vulkan window is mapped
#   MP Gui              its manager menu becomes visible
#
# Usage:
#   ./mp_benchmark_mode.sh                # full run, 4 shortcuts
#   ./mp_benchmark_mode.sh --quick
#   ./mp_benchmark_mode.sh --timeout 120
#   ./mp_benchmark_mode.sh --keep-running # leave them up afterwards
#   ./mp_benchmark_mode.sh --json
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

MODE_SCRIPT="${SCRIPT_DIR}/mp_benchmark_mode.py"
if [ ! -f "$MODE_SCRIPT" ]; then
    echo "mp_benchmark_mode.sh: mp_benchmark_mode.py not found" >&2
    exit 1
fi

RESOLVER="${SCRIPT_DIR}/resolve_mp_python.sh"
[ ! -f "$RESOLVER" ] && RESOLVER="${PROJECT_ROOT}/scripts/resolve_mp_python.sh"
PY=""
if [ -f "$RESOLVER" ]; then
    # shellcheck disable=SC1090
    . "$RESOLVER"
    if command -v mp_gui_python_bin >/dev/null 2>&1; then
        PY="$(mp_gui_python_bin 2>/dev/null || true)"
    fi
fi
[ -z "$PY" ] && PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "mp_benchmark_mode.sh: no python3 found" >&2
    exit 1
fi

exec "$PY" "$MODE_SCRIPT" "$@"
