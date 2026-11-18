#!/usr/bin/env bash
# MP Liquid Mixer 0.0.1 - launcher
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    echo "MP Liquid Mixer: python3 was not found in PATH." >&2
    exit 1
fi

exec "$PY" "$DIR/MP_Liquid_Mixer.py" "$@"
