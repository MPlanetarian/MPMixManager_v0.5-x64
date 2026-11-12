#!/usr/bin/env bash
# ==============================================================================
# MP_Mix_Manager - Traktor Playlist Importer
# Imports every playlist from collection.nml into the MP Audio Player playlist.
# ==============================================================================

set -eo pipefail 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PYTHON_CMD=$(command -v python3 || command -v python || echo "/usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3" | tr ' ' '\n' | head -n1)

IMP_PY="$SCRIPT_DIR/traktor_playlist_importer.py"
if [ ! -f "$IMP_PY" ]; then
    for dir in "$SCRIPT_DIR/scripts" "$PWD/scripts" "$PWD"; do
        if [ -f "$dir/traktor_playlist_importer.py" ]; then
            IMP_PY="$dir/traktor_playlist_importer.py"
            break
        fi
    done
fi

if [ ! -f "$IMP_PY" ]; then
    echo "Error: traktor_playlist_importer.py not found in $SCRIPT_DIR or subdirectories." >&2
    exit 1
fi

exec "$PYTHON_CMD" "$IMP_PY" "$@"
