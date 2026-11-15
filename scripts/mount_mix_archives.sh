#!/bin/bash
# ==============================================================================
# MP Mix Manager — mount the Linux MIX_ARCHIVE shares on macOS
# ==============================================================================
# The archive lives on the Linux workstations, not on the Mac. This script
# mounts each archive share at a stable path under ~/MIX_ARCHIVE_MOUNTS so the
# same MIX_ARCHIVE_DIR / EXTRA_MIX_ARCHIVE_DIRS settings work on every login.
#
# Usage
#   bash scripts/mount_mix_archives.sh            # mount anything missing
#   bash scripts/mount_mix_archives.sh --status   # report, change nothing
#   bash scripts/mount_mix_archives.sh --install  # install the login agent
#   bash scripts/mount_mix_archives.sh --uninstall
#
# Configuration lives in ~/.config/mix-manager/config.env:
#   MIX_ARCHIVE_SMB_HOST     LAN address of the archive server (required)
#   MIX_ARCHIVE_SMB_USER     SMB username (default: $USER)
#   MIX_ARCHIVE_SMB_PASSWORD default read from the login keychain
#   MIX_ARCHIVE_SMB_SHARES   semicolon list of "host/share|mountname"
#
# Everything runs under a timeout. A powered-off workstation leaves
# mount_smbfs blocked in the kernel, and an unresponsive mountpoint wedges
# every process that touches it, so one dead share must never stall the rest
# or hang the login.
# ==============================================================================

set -u

SCRIPT_NAME="mount_mix_archives.sh"
CONFIG_DIR="${HOME}/.config/mix-manager"
CONFIG_FILE="${CONFIG_DIR}/config.env"
MOUNT_ROOT="${HOME}/MIX_ARCHIVE_MOUNTS"
AGENT_LABEL="com.mplanetarian.mp-mix-archives"
AGENT_PLIST="${HOME}/Library/LaunchAgents/${AGENT_LABEL}.plist"
LOG_FILE="${CONFIG_DIR}/mount_mix_archives.log"
MOUNT_TIMEOUT=20
PROBE_TIMEOUT=5

# No host is baked in: set MIX_ARCHIVE_SMB_HOST (and optionally
# MIX_ARCHIVE_SMB_SHARES) in config.env. With nothing set the script reports
# that it has nothing to mount instead of probing someone else's network.
DEFAULT_SHARES=""

if [ -t 1 ]; then
    BOLD=$'\033[1m'; GREEN=$'\033[38;2;0;229;255m'; RED=$'\033[38;2;237;37;78m'
    YELLOW=$'\033[38;2;255;170;0m'; DIM=$'\033[2m'; NC=$'\033[0m'
else
    BOLD=""; GREEN=""; RED=""; YELLOW=""; DIM=""; NC=""
fi

say() { printf '%s\n' "$*"; }

# --- configuration -----------------------------------------------------------
cfg_get() {
    local key="$1" default="${2:-}"
    local value=""
    if [ -f "$CONFIG_FILE" ]; then
        value="$(sed -n "s/^${key}=//p" "$CONFIG_FILE" | tail -1)"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
    fi
    if [ -n "$value" ]; then printf '%s' "$value"; else printf '%s' "$default"; fi
}

SMB_USER="$(cfg_get MIX_ARCHIVE_SMB_USER "${USER:-}")"
SMB_PASSWORD="$(cfg_get MIX_ARCHIVE_SMB_PASSWORD "")"
SHARES_SPEC="$(cfg_get MIX_ARCHIVE_SMB_SHARES "$DEFAULT_SHARES")"

# An explicitly configured password is used as-is; otherwise ask the keychain,
# so the password is never stored in a file this suite can leak.
password_for_host() {
    local host="$1"
    if [ -n "$SMB_PASSWORD" ]; then printf '%s' "$SMB_PASSWORD"; return; fi
    /usr/bin/security find-internet-password -s "$host" -a "$SMB_USER" -w 2>/dev/null \
        || printf '%s' ""
}

# --- probes (all bounded) ----------------------------------------------------
is_mounted() {
    /usr/bin/timeout "$PROBE_TIMEOUT" /sbin/mount 2>/dev/null \
        | /usr/bin/grep -F " on $1 " >/dev/null 2>&1
}

mountpoint_ok() {
    /usr/bin/timeout "$PROBE_TIMEOUT" /usr/bin/stat -f "%d" "$1" >/dev/null 2>&1
}

# --- actions -----------------------------------------------------------------
parse_shares() {
    # The trailing newline matters: `read` returns false at EOF without one,
    # so the final share would be silently dropped from both actions.
    printf '%s\n' "$SHARES_SPEC" | tr ';' '\n' | while IFS= read -r entry; do
        [ -n "$entry" ] && printf '%s\n' "$entry"
    done
}

mount_all() {
    local mounted=0 skipped=0 failed=0

    mkdir -p "$MOUNT_ROOT" 2>/dev/null

    parse_shares | while IFS='|' read -r share name; do
        [ -n "$share" ] || continue
        local target="$MOUNT_ROOT/$name"
        local host="${share%%/*}"

        if is_mounted "$target"; then
            say "already mounted: $target"
            continue
        fi

        mkdir -p "$target" 2>/dev/null
        # A wedged mountpoint cannot even be stat'ed. Skip it rather than hang;
        # it clears on reboot or when its dead server comes back.
        if ! mountpoint_ok "$target"; then
            say "skipping (mountpoint wedged, reboot to clear): $target"
            continue
        fi

        local password
        password="$(password_for_host "$host")"

        local url
        if [ -n "$password" ]; then
            url="//${SMB_USER}:${password}@${share}"
        else
            url="//${SMB_USER}@${share}"
        fi

        if /usr/bin/timeout "$MOUNT_TIMEOUT" /sbin/mount_smbfs "$url" "$target" >/dev/null 2>&1; then
            say "mounted: $target"
        else
            say "not mounted (host offline or credentials missing): $target"
        fi
    done
}

show_status() {
    local missing=0
    say "${BOLD}MP Mix archive mounts${NC}  ${DIM}${MOUNT_ROOT}${NC}"
    say ""
    parse_shares | while IFS='|' read -r share name; do
        [ -n "$share" ] || continue
        local target="$MOUNT_ROOT/$name"
        local root="$target/MIX_ARCHIVE"
        if is_mounted "$target"; then
            local count
            count="$(/usr/bin/timeout 25 /usr/bin/find "$root" -type f \
                \( -iname '*.flac' -o -iname '*.wav' -o -iname '*.mp3' \
                   -o -iname '*.m4a' -o -iname '*.aiff' -o -iname '*.aif' \
                   -o -iname '*.ogg' -o -iname '*.aac' \) 2>/dev/null | /usr/bin/wc -l)"
            say "  ${GREEN}●${NC} ${name}  ${DIM}(${share})${NC}  ${BOLD}${count}${NC} mixes"
        elif ! mountpoint_ok "$target"; then
            say "  ${YELLOW}●${NC} ${name}  ${DIM}(mountpoint wedged — reboot)${NC}"
        else
            say "  ${RED}○${NC} ${name}  ${DIM}(${share}) — not mounted${NC}"
        fi
    done
    say ""
    say "  ${DIM}tools: bash $SCRIPT_NAME --install  (mount at login and every 5 min)${NC}"
}

install_agent() {
    mkdir -p "$(dirname "$AGENT_PLIST")"
    local script_path="$CONFIG_DIR/$SCRIPT_NAME"

    # The agent runs this script from the config directory so it keeps working
    # even if the application folder is moved, renamed or upgraded.
    if [ "$(cd "$(dirname "$0")" && pwd)/$(basename "$0")" != "$script_path" ]; then
        mkdir -p "$CONFIG_DIR"
        cp "$0" "$script_path"
        chmod +x "$script_path"
        say "installed helper: $script_path"
    fi

    cat > "$AGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${script_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
</dict>
</plist>
PLIST

    /bin/launchctl bootout "gui/$(id -u)/${AGENT_LABEL}" >/dev/null 2>&1
    /bin/launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null \
        || /bin/launchctl load -w "$AGENT_PLIST" 2>/dev/null
    say "login agent installed: ${AGENT_PLIST}"
    say "${DIM}It mounts the archives at login and retries every 5 minutes.${NC}"
}

uninstall_agent() {
    /bin/launchctl bootout "gui/$(id -u)/${AGENT_LABEL}" >/dev/null 2>&1
    rm -f "$AGENT_PLIST"
    say "login agent removed: ${AGENT_PLIST}"
    say "${DIM}Existing mounts stay until you unmount them or reboot.${NC}"
}

case "${1:---mount}" in
    --status)    show_status ;;
    --install)   install_agent; mount_all ;;
    --uninstall) uninstall_agent ;;
    --mount|"")  mount_all ;;
    --help|-h)
        say "Usage: bash $SCRIPT_NAME [--mount|--status|--install|--uninstall]"
        say ""
        say "  --mount      mount every configured archive share (default)"
        say "  --status     show each share, its host and its mix count"
        say "  --install    mount now and install the login LaunchAgent"
        say "  --uninstall  remove the LaunchAgent (mounts are left alone)"
        ;;
    *)
        say "unknown option: $1" >&2
        say "Usage: bash $SCRIPT_NAME [--mount|--status|--install|--uninstall]" >&2
        exit 2
        ;;
esac
