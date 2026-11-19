#!/usr/bin/env bash
# ==============================================================================
# Kontrol S4 Hardware MIDI Controller Support Script
# MP Mix Archive Manager v0.5.4 Stable (Sensorium)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

PYTHON_SCRIPT="$SCRIPT_DIR/dj_s4_midi.py"
[ ! -f "$PYTHON_SCRIPT" ] && PYTHON_SCRIPT="$PROJECT_ROOT/scripts/dj_s4_midi.py"
[ ! -f "$PYTHON_SCRIPT" ] && PYTHON_SCRIPT="$PROJECT_ROOT/dj_s4_midi.py"

if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "Error: dj_s4_midi.py not found!" >&2
    exit 1
fi

python3 "$PYTHON_SCRIPT" "$@"
