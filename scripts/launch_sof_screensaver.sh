#!/usr/bin/env bash
# ==============================================================================
# MP Stream of Frequency Screen Saver
#
# Launches the MP Visualizer in screen-saver mode, pinned to the two
# Stream of Frequency — Dynamic Waves presets, on every connected display.
#
# These presets are paper-white rivers of colour whose streams are fired by
# real Linux process creation (one burst per new PID), not by music, so the
# saver is alive on an idle desktop with no player running.
#
# Any keypress or mouse movement on any screen wakes it and closes the whole
# wall. Pass extra arguments straight through, for example:
#
#   launch_sof_screensaver.sh --saver-dwell 60
#   launch_sof_screensaver.sh --saver-preset stream_of_frequency_dynamic_waves_p2
#
# Cross-Platform: Linux / macOS / Windows 10 & 11 (Windows via manager.bat path)
# ==============================================================================
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# The two Dynamic Waves presets. The first is the horizontal rainbow river,
# the second the vertical P2 matrix rain. The saver alternates between them.
PRESET_POOL="stream_of_frequency_dynamic_waves,stream_of_frequency_dynamic_waves_p2"
DWELL="${MP_SOF_SAVER_DWELL:-90}"

PYTHON_BIN=""
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
    VIS_PY="${PROJECT_ROOT}/MP_Liquid_Visualizer.py"
elif [[ -f "${SCRIPT_DIR}/mp_liquid_visualizer_vulkan.py" ]]; then
    VIS_PY="${SCRIPT_DIR}/mp_liquid_visualizer_vulkan.py"
else
    echo "Error: MP_Liquid_Visualizer.py not found in ${PROJECT_ROOT}" >&2
    exit 1
fi

# The PID stream is the whole point of these presets, so make sure it is on.
export MP_PID_STREAM="${MP_PID_STREAM:-1}"

# Log to the same place the idle daemon does, so a failure is diagnosable.
LOG_DIR="${HOME}/.config/mix-manager"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="${LOG_DIR}/sof_screensaver.log"

cd "$PROJECT_ROOT" || exit 1

# --saver-presets keeps the saver on the two Dynamic Waves rivers instead of
# touring the whole preset library. Anything the caller passes follows, so a
# later --saver-preset still overrides the pool.
exec "$PYTHON_BIN" "$VIS_PY" \
    --screensaver \
    --saver-dwell "$DWELL" \
    --saver-presets "$PRESET_POOL" \
    "$@" >> "$LOG_FILE" 2>&1
