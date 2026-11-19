#!/usr/bin/env bash
# MP Liquid Mixer 0.0.1 - launcher
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

PY=""
# The desktop launcher resolves the interpreter that can import PyGObject and
# VTE, because a desktop session's PATH may find a bare python3 first.
if [ -n "${MP_PYTHON:-}" ] && [ -x "${MP_PYTHON}" ]; then
    PY="$MP_PYTHON"
fi
if [ -z "$PY" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "MP Liquid Mixer: python3 was not found in PATH." >&2
    exit 1
fi

exec "$PY" "$DIR/MP_Liquid_Mixer.py" "$@"
