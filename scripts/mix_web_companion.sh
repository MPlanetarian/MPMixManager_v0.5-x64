#!/usr/bin/env bash
# ==============================================================================
# MP_Mix_Manager_v0.3 - Studio & DJ Booth Mobile Web Companion Wrapper
# ==============================================================================
# Resolve the real script location, following symlinks (supports being run via
# a root-level `mix_web_companion.sh` symlink).
_RESOLVED_SRC="${BASH_SOURCE[0]}"
while [ -h "$_RESOLVED_SRC" ]; do
    _RESOLVED_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
    _RESOLVED_SRC="$(readlink "$_RESOLVED_SRC")"
    [[ $_RESOLVED_SRC != /* ]] && _RESOLVED_SRC="$_RESOLVED_DIR/$_RESOLVED_SRC"
done
SCRIPT_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then
    BASE_DIR="$(cd -P "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
else
    BASE_DIR="$SCRIPT_DIR"
fi
PY_SCRIPT="$SCRIPT_DIR/mix_web_companion.py"
[ ! -f "$PY_SCRIPT" ] && PY_SCRIPT="$BASE_DIR/scripts/mix_web_companion.py"
[ ! -f "$PY_SCRIPT" ] && PY_SCRIPT="$BASE_DIR/mix_web_companion.py"

# Source config if available
if [ -f "$BASE_DIR/config.env" ]; then
    source "$BASE_DIR/config.env"
elif [ -f "$SCRIPT_DIR/config.env" ]; then
    source "$SCRIPT_DIR/config.env"
fi

HOST="${WEB_COMPANION_HOST:-0.0.0.0}"
PORT="${WEB_COMPANION_PORT:-8888}"
SERVICE_NAME="mp-web-companion.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$UNIT_DIR/$SERVICE_NAME"
PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"

# Create or refresh the systemd user unit so autostart always points at this
# installation (a stale hardcoded path previously made the unit crash-loop).
ensure_service_file() {
    command -v systemctl &>/dev/null || return 1
    mkdir -p "$UNIT_DIR" || return 1
    cat > "$UNIT_FILE" <<EOF
[Unit]
Description=MP Mix Archive Manager - Studio & DJ Booth Mobile Web Companion
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
Environment=PYTHONUNBUFFERED=1
Environment=WEB_COMPANION_HOST=$HOST
Environment=WEB_COMPANION_PORT=$PORT
EnvironmentFile=-$BASE_DIR/config.env
ExecStart=$PYTHON_BIN $PY_SCRIPT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
}

get_lan_ip() {
    python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('10.255.255.255', 1)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "127.0.0.1"
}

get_pids() {
    pgrep -f "mix_web_companion.py" 2>/dev/null | tr '\n' ' '
}

case "${1:-}" in
    start)
        echo "Starting Mobile Web Companion..."
        if command -v systemctl &>/dev/null; then
            ensure_service_file
            systemctl --user start "$SERVICE_NAME"
            sleep 1
            if systemctl --user is-active --quiet "$SERVICE_NAME"; then
                echo "✓ Mobile Web Companion started via systemd service ($SERVICE_NAME)."
            else
                echo "⚠ systemd start failed; attempting direct background launch..."
                nohup python3 "$PY_SCRIPT" --host "$HOST" --port "$PORT" >/dev/null 2>&1 &
            fi
        else
            nohup python3 "$PY_SCRIPT" --host "$HOST" --port "$PORT" >/dev/null 2>&1 &
            sleep 1
        fi
        "$0" status
        ;;
    stop)
        echo "Stopping Mobile Web Companion..."
        if command -v systemctl &>/dev/null && [ -f "$UNIT_FILE" ]; then
            systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
        fi
        pids=$(get_pids)
        if [ -n "$pids" ]; then
            kill $pids 2>/dev/null || true
            sleep 0.5
        fi
        echo "✓ Mobile Web Companion stopped."
        ;;
    restart)
        "$0" stop
        sleep 1
        "$0" start
        ;;
    status)
        LAN_IP=$(get_lan_ip)
        pids=$(get_pids)
        active=false
        if command -v systemctl &>/dev/null && systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            active=true
        elif [ -n "$pids" ]; then
            active=true
        fi

        if [ "$active" = "true" ]; then
            echo "● Mobile Web Companion is RUNNING"
            [ -n "$pids" ] && echo "  • PID(s):         $pids"
            echo "  • Local Address:  http://localhost:${PORT}"
            echo "  • Mobile LAN URL: http://${LAN_IP}:${PORT}"
        else
            echo "○ Mobile Web Companion is STOPPED"
            echo "  • Target Mobile LAN URL: http://${LAN_IP}:${PORT}"
        fi
        ;;
    enable)
        if command -v systemctl &>/dev/null; then
            ensure_service_file
            systemctl --user enable "$SERVICE_NAME"
            echo "✓ Autostart enabled for $SERVICE_NAME."
        else
            echo "systemctl not available; cannot enable autostart on this system."
        fi
        ;;
    disable)
        if command -v systemctl &>/dev/null; then
            systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
            echo "✓ Autostart disabled for $SERVICE_NAME."
        fi
        ;;
    open|browse)
        LAN_IP=$(get_lan_ip)
        target="http://${LAN_IP}:${PORT}"
        echo "Opening $target in default browser..."
        if command -v xdg-open &>/dev/null; then
            xdg-open "$target" &>/dev/null &
        elif command -v open &>/dev/null; then
            open "$target" &>/dev/null &
        fi
        ;;
    *)
        exec python3 "$PY_SCRIPT" "$@"
        ;;
esac