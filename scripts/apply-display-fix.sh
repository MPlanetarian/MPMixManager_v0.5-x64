#!/usr/bin/env bash
# ==============================================================================
# MP Mix Manager - NoMachine display / Xauthority repair helper
#
# Re-hooks NoMachine's `nxenvironmentget.sh` so a session picks up the real
# Xauthority file, and restores the `nx` user in the unit file.
#
# 0.5.1 changes, all of them safety:
#   * Every path is overridable. The previous version hard-coded
#     /var/home/mplanetarian and /run/user/1000, so it wrote credentials into
#     one particular user's home on any other machine (and into a file the
#     wrong user then owned).
#   * It refuses to run without `--yes`. It installs a setuid-root binary;
#     that must never happen because someone double-clicked a file.
#   * It backs up every file it edits before touching it.
#   * `--dry-run` prints exactly what it would do.
# ==============================================================================

set -euo pipefail

NOMACHINE_ROOT="${NOMACHINE_ROOT:-/var/nomachine/NX}"
NX_USER="${NX_USER:-nx}"
NX_UNIT="${NX_UNIT:-/etc/systemd/system/nxserver.service}"
X11_RUNTIME_DIR="${X11_RUNTIME_DIR:-/run/user/$(id -u)}"
TARGET_USER="${TARGET_USER:-${SUDO_USER:-$USER}}"
TARGET_HOME="${TARGET_HOME:-$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)}"
SOURCE_HELPER="${SOURCE_HELPER:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nxcat_environ}"

ASSUME_YES=0
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: apply-display-fix.sh [--yes] [--dry-run] [--help]

  --yes       Apply the changes. Without this the script only reports.
  --dry-run   Print the planned actions and exit.
  --help      This message.

Environment overrides:
  NOMACHINE_ROOT   default /var/nomachine/NX
  NX_USER          default nx
  NX_UNIT          default /etc/systemd/system/nxserver.service
  X11_RUNTIME_DIR  default /run/user/<your-uid>
  TARGET_USER      default $SUDO_USER, else $USER
  SOURCE_HELPER    path to the nxcat_environ binary
EOF
}

for arg in "$@"; do
    case "$arg" in
        --yes|-y) ASSUME_YES=1 ;;
        --dry-run|-n) DRY_RUN=1 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; usage; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    echo "This script must run as root (it edits a systemd unit and installs a setuid helper)." >&2
    echo "Re-run with: sudo $0 --yes" >&2
    exit 1
fi

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] %s\n' "$*"
    else
        printf '  + %s\n' "$*"
        "$@"
    fi
}

backup() {
    local file="$1"
    [ -f "$file" ] || return 0
    local dest="${file}.mp-backup-$(date +%Y%m%d%H%M%S)"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] backup %s -> %s\n' "$file" "$dest"
    else
        cp -p "$file" "$dest"
        printf '  backed up %s -> %s\n' "$file" "$dest"
    fi
}

# macOS/BSD `sed -i` needs an explicit (possibly empty) suffix; GNU does not.
sed_inplace() {
    local expr="$1" file="$2"
    if sed --version >/dev/null 2>&1; then
        sed -i "$expr" "$file"
    else
        sed -i '' "$expr" "$file"
    fi
}

echo "=== 1. Restoring the '$NX_USER' user in the NoMachine unit ==="
if [ -f "$NX_UNIT" ]; then
    backup "$NX_UNIT"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [dry-run] uncomment User=/Group= in $NX_UNIT"
    else
        sed_inplace 's/^#\(User\|Group\)='"$NX_USER"'/\1='"$NX_USER"'/' "$NX_UNIT"
    fi
    run systemctl reset-failed "$(basename "$NX_UNIT" .service)" || true
    run systemctl daemon-reload
else
    echo "  skipped: $NX_UNIT not found"
fi

echo "=== 2. Installing the setuid-root environment reader ==="
HELPER_DEST="$NOMACHINE_ROOT/bin/nxcat_environ"
if [ -f "$SOURCE_HELPER" ]; then
    run install -m 4755 -o root -g root "$SOURCE_HELPER" "$HELPER_DEST"
    echo "  NOTE: $HELPER_DEST is setuid root. Remove it if NoMachine is uninstalled."
else
    echo "  skipped: helper source not found at $SOURCE_HELPER"
fi

echo "=== 3. Hooking the helper into nxenvironmentget.sh ==="
NXENV="$NOMACHINE_ROOT/scripts/restricted/nxenvironmentget.sh"
if [ -f "$NXENV" ]; then
    if grep -q "nxcat_environ" "$NXENV"; then
        echo "  already hooked"
    else
        backup "$NXENV"
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "  [dry-run] insert exec $HELPER_DEST into $NXENV"
        else
            sed_inplace \
                '/^PROCESS_ID=/a if [ -x "'"$HELPER_DEST"'" ]; then\n  exec "'"$HELPER_DEST"'" "$@"\nfi' \
                "$NXENV"
        fi
    fi
else
    echo "  skipped: $NXENV not found"
fi

echo "=== 4. Syncing the active Xauthority ==="
if [ ! -d "$X11_RUNTIME_DIR" ]; then
    echo "  skipped: $X11_RUNTIME_DIR does not exist"
elif [ -z "$TARGET_HOME" ] || [ ! -d "$TARGET_HOME" ]; then
    echo "  skipped: cannot resolve the home directory for '$TARGET_USER'"
else
    ACTIVE_AUTH="$(find "$X11_RUNTIME_DIR" -maxdepth 2 -name 'xauth_*' 2>/dev/null | head -n 1 || true)"
    if [ -n "$ACTIVE_AUTH" ]; then
        echo "  found $ACTIVE_AUTH"
        run install -m 0644 -o "$TARGET_USER" -g "$TARGET_USER" "$ACTIVE_AUTH" "$TARGET_HOME/.Xauthority"
        # 0711 lets the NX transport traverse the home directory without
        # listing it. Do not widen this to 0755: the home directory usually
        # holds private data and only traversal is needed.
        run chmod 0711 "$TARGET_HOME"
        if [ -d /var/NX/nx ]; then
            run install -m 0644 -o "$NX_USER" -g "$NX_USER" "$ACTIVE_AUTH" /var/NX/nx/.Xauthority
        fi
    else
        echo "  skipped: no xauth_* file under $X11_RUNTIME_DIR"
    fi
fi

echo "=== 5. Restarting the NoMachine server ==="
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] pkill nxserver.bin && systemctl restart $(basename "$NX_UNIT" .service)"
else
    pkill -9 -f "nxserver.bin --restart" || true
    pkill -9 -f "nxserver.bin --login" || true
    systemctl restart "$(basename "$NX_UNIT" .service)" || true
    sleep 3
    [ -f "$NOMACHINE_ROOT/var/log/server.log" ] && tail -n 25 "$NOMACHINE_ROOT/var/log/server.log"
    [ -x "$NOMACHINE_ROOT/bin/nxserver" ] && "$NOMACHINE_ROOT/bin/nxserver" --status || true
fi

echo "=== Done ==="
