#!/usr/bin/env bash
# ==============================================================================
# DJ Pro Live Monitor & Audio Recorder - macOS Double-Click Launcher
# ==============================================================================
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$DIR/scripts/dj_monitor.py" "$@"
elif [ -x "/usr/bin/python3" ]; then
    exec /usr/bin/python3 "$DIR/scripts/dj_monitor.py" "$@"
elif [ -x "/usr/local/bin/python3" ]; then
    exec /usr/local/bin/python3 "$DIR/scripts/dj_monitor.py" "$@"
elif [ -x "/opt/homebrew/bin/python3" ]; then
    exec /opt/homebrew/bin/python3 "$DIR/scripts/dj_monitor.py" "$@"
else
    echo "Error: python3 was not found in PATH or standard macOS locations."
    read -r -p "Press [Enter] to exit..."
    exit 1
fi
