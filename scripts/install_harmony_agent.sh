#!/usr/bin/env bash
# ==============================================================================
# scripts/install_harmony_agent.sh
# Purpose: Install the MP Harmony Agent, which is a SEPARATE project from
#          MP Mix Manager and lives at ~/OpenAI/MP_Harmony_Agent.
#
# MP Harmony Agent is an optional component. MP Mix Manager can launch and
# manage it when it is present, but nothing in the manager requires it.
#
# The agent source is NOT shipped inside this tree. It is cloned from:
#   https://github.com/MPlanetarian/MP-Harmony-Agent.git
#
# Usage:
#   install_harmony_agent.sh install      Clone or update the agent (default)
#   install_harmony_agent.sh status       Report whether the agent is present
#   install_harmony_agent.sh path         Print the resolved agent directory
#   install_harmony_agent.sh unit         Write the systemd user unit only
#
# Environment:
#   HARMONY_DIR              Override the install directory
#   HARMONY_REPO_URL         Override the git remote
#   HARMONY_SKIP_PULL=1      Never update an existing checkout
#   HARMONY_TIMEOUT          Git timeout in seconds (default 120)
#
# Exit status:
#   0  agent present and usable
#   1  agent could not be installed
#   2  usage error
# ==============================================================================
set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

REPO_URL="${HARMONY_REPO_URL:-https://github.com/MPlanetarian/MP-Harmony-Agent.git}"
TARGET="${HARMONY_DIR:-$HOME/OpenAI/MP_Harmony_Agent}"
TIMEOUT="${HARMONY_TIMEOUT:-120}"
ENTRY="MP_Harmony_Agent.py"
UNIT_NAME="mp-harmony-agent.service"
UNIT_DIR="$HOME/.config/systemd/user"

say()  { echo -e "$*"; }
ok()   { echo -e "      ${GREEN}✓ $*${NC}"; }
warn() { echo -e "      ${YELLOW}⚠ $*${NC}"; }
err()  { echo -e "      ${RED}✗ $*${NC}" >&2; }

have_git() { command -v git >/dev/null 2>&1; }

have_agent() { [ -f "$TARGET/$ENTRY" ]; }

# True on Git Bash / MSYS2 / Cygwin, where the agent runs against a Windows
# Python instead of a system python3.
is_windows() {
    case "$(uname -s 2>/dev/null)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
    esac
    [ -n "${WINDIR:-}" ] && return 0
    return 1
}

# The interpreter the launcher will actually use (see launch_mp_harmony.sh).
harmony_python() {
    local bin=""
    if [ -n "${MP_PY_BIN:-}" ] && "$MP_PY_BIN" -c "" >/dev/null 2>&1; then
        printf '%s\n' "$MP_PY_BIN"
        return 0
    fi
    local resolver
    resolver="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)/resolve_mp_python.sh"
    if [ -f "$resolver" ]; then
        bin="$(SCRIPT_DIR="${SCRIPT_DIR:-$(dirname "$resolver")/..}" bash -c '
            source "$1" >/dev/null 2>&1 || exit 1
            mp_python_bin 2>/dev/null || resolve_mp_python 2>/dev/null
        ' _ "$resolver" 2>/dev/null | tail -n 1)"
        bin="${bin//$'\r'/}"
    fi
    if [ -z "$bin" ]; then
        for bin in python3 python; do
            local found=""
            found="$(command -v "$bin" 2>/dev/null || true)"
            case "$found" in
                ''|*[Ww]indows[Aa]pps*) continue ;;
            esac
            bin="$found"
            break
        done
    fi
    [ -n "$bin" ] || return 1
    printf '%s\n' "$bin"
}

# On Windows the agent imports readline and openai_harmony, which are not
# stdlib there: readline is POSIX-only (pyreadline3 provides it) and the
# Harmony renderer is a wheel. `ddgs` is the web-search tool. Installing them
# from the installer is what makes the agent startable on a fresh machine
# instead of dying on "No module named 'readline'".
harmony_install_windows_deps() {
    is_windows || return 0
    local py
    py="$(harmony_python || true)"
    if [ -z "$py" ]; then
        warn "No Windows Python found; the agent dependencies were not installed."
        return 1
    fi
    if "$py" - <<'PY' >/dev/null 2>&1
import importlib
for name in ("httpx", "openai_harmony", "ddgs", "readline"):
    importlib.import_module(name)
PY
    then
        ok "Agent dependencies already present in ${CYAN}${py}${NC}"
        return 0
    fi
    say "      Installing the agent dependencies into ${CYAN}${py}${NC} ..."
    if "$py" -m pip install --disable-pip-version-check --no-input \
            "openai-harmony" "httpx" "ddgs" "pyreadline3" >/dev/null 2>&1; then
        ok "Agent dependencies installed."
        return 0
    fi
    warn "Could not install the agent dependencies automatically."
    say "        ${DIM}Run: \"$py\" -m pip install openai-harmony httpx ddgs pyreadline3${NC}"
    return 1
}

# A directory that has the entry point is usable even if it is not a git
# checkout: the user may have copied it, or cloned it themselves.
agent_dir_kind() {
    if have_agent; then
        if [ -d "$TARGET/.git" ]; then
            echo "git"
        else
            echo "plain"
        fi
    else
        echo "missing"
    fi
}

cmd_status() {
    local kind
    kind="$(agent_dir_kind)"
    case "$kind" in
        git)
            ok "MP Harmony Agent found (git checkout): ${CYAN}${TARGET}${NC}"
            local branch
            branch="$(git -C "$TARGET" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
            local desc
            desc="$(git -C "$TARGET" log -1 --format='%h %s' 2>/dev/null || echo '?')"
            say "        branch: ${branch}  •  ${desc}"
            harmony_report_windows_deps
            return 0
            ;;
        plain)
            ok "MP Harmony Agent found (not a git checkout): ${CYAN}${TARGET}${NC}"
            harmony_report_windows_deps
            return 0
            ;;
        *)
            say "      ${DIM}• MP Harmony Agent is not installed (${TARGET}).${NC}"
            return 1
            ;;
    esac
}

# Status must not install anything, but on Windows it should say whether the
# agent can start, because a missing readline wheel is otherwise only found
# when the AI row is chosen.
harmony_report_windows_deps() {
    is_windows || return 0
    local py
    py="$(harmony_python || true)"
    if [ -z "$py" ]; then
        warn "No Windows Python found, so the agent cannot start."
        return 1
    fi
    if "$py" - <<'PY' >/dev/null 2>&1
import importlib
for name in ("httpx", "openai_harmony", "ddgs", "readline"):
    importlib.import_module(name)
PY
    then
        say "        ${DIM}• Windows dependencies present.${NC}"
        return 0
    fi
    warn "Windows dependencies missing. Run: bash scripts/install_harmony_agent.sh deps"
    return 1
}

# Clone with a hard timeout so a blocked network never hangs the installer.
git_clone() {
    if command -v timeout >/dev/null 2>&1; then
        timeout "$TIMEOUT" git clone --depth 1 "$REPO_URL" "$TARGET"
    else
        git clone --depth 1 "$REPO_URL" "$TARGET"
    fi
}

cmd_install() {
    local kind
    kind="$(agent_dir_kind)"

    if [ "$kind" = "git" ]; then
        ok "MP Harmony Agent already installed: ${CYAN}${TARGET}${NC}"
        if [ "${HARMONY_SKIP_PULL:-0}" = "1" ]; then
            say "      ${DIM}• Update skipped (HARMONY_SKIP_PULL=1).${NC}"
            return 0
        fi
        # Fast-forward only. A dirty tree or local commits are left alone so
        # work in progress is never destroyed by an installer run.
        local dirty
        dirty="$(git -C "$TARGET" status --porcelain 2>/dev/null | head -n 1)"
        if [ -n "$dirty" ]; then
            warn "Local changes present; not updating the checkout."
            say "        ${DIM}Commit or stash them, then re-run this installer to update.${NC}"
            return 0
        fi
        say "      Updating from origin..."
        if ( cd "$TARGET" && { command -v timeout >/dev/null 2>&1 \
                && timeout "$TIMEOUT" git pull --ff-only --quiet \
                || git pull --ff-only --quiet; } ); then
            ok "Updated to $(git -C "$TARGET" log -1 --format='%h %s' 2>/dev/null)"
        else
            warn "Could not update (offline, or local commits diverge). The existing copy will be used."
        fi
        return 0
    fi

    if [ "$kind" = "plain" ]; then
        ok "MP Harmony Agent already present (not a git checkout): ${CYAN}${TARGET}${NC}"
        say "      ${DIM}• Leaving it untouched; delete it to let the installer clone a fresh copy.${NC}"
        harmony_install_windows_deps || true
        return 0
    fi

    # Missing: clone it.
    if ! have_git; then
        err "git is required to install MP Harmony Agent, and it was not found."
        say "        ${CYAN}Fedora/Bazzite: sudo rpm-ostree install git${NC}"
        say "        ${CYAN}Debian/Ubuntu:  sudo apt install git${NC}"
        say "        ${CYAN}macOS:          brew install git${NC}"
        say "        ${CYAN}Or clone it yourself: git clone ${REPO_URL} ${TARGET}${NC}"
        return 1
    fi

    say "      Cloning ${CYAN}${REPO_URL}${NC}"
    say "      into ${CYAN}${TARGET}${NC} ..."
    mkdir -p "$(dirname "$TARGET")" 2>/dev/null || true

    if git_clone; then
        :
    else
        # A partial directory left by a failed clone would break the next run.
        [ -d "$TARGET" ] && [ ! -f "$TARGET/$ENTRY" ] && rm -rf "$TARGET" 2>/dev/null || true
        err "Could not clone MP Harmony Agent."
        say "        ${DIM}Check your network, or clone it manually:${NC}"
        say "        ${CYAN}git clone ${REPO_URL} ${TARGET}${NC}"
        return 1
    fi

    if have_agent; then
        ok "MP Harmony Agent installed: ${CYAN}${TARGET}${NC}"
        harmony_install_windows_deps || true
        return 0
    fi

    err "The clone completed but ${ENTRY} is missing from ${TARGET}."
    return 1
}

# The systemd unit is only meaningful on a systemd host. It is written here so
# the installer and the user share one source of truth for the path.
cmd_unit() {
    if ! command -v systemctl >/dev/null 2>&1; then
        say "      ${DIM}• systemctl not found; skipping the user unit.${NC}"
        return 0
    fi
    if ! have_agent; then
        warn "Not writing $UNIT_NAME because the agent is not installed."
        return 1
    fi

    mkdir -p "$UNIT_DIR" 2>/dev/null || true
    local py
    py="$(command -v python3 2>/dev/null || echo /usr/bin/python3)"

    # The unit deliberately does not pin a model tag. A pinned tag that later
    # disappears from Ollama made the agent exit on startup, and systemd then
    # restart-looped it while the Web Companion showed "connection refused".
    # Without --model the agent picks an installed model itself, preferring
    # gpt-oss-pinned:latest then any Nemotron build. Set HARMONY_MODEL in a
    # drop-in to pin a specific tag.
    cat > "$UNIT_DIR/$UNIT_NAME" <<UNIT
[Unit]
Description=MP Harmony AI Agent & Voice Bridge (:11435)
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$TARGET
ExecStart=$py $TARGET/$ENTRY --server
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT

    systemctl --user daemon-reload 2>/dev/null || true
    ok "Wrote $UNIT_NAME → ${CYAN}${TARGET}${NC}"
    say "      ${DIM}• Enable it with: systemctl --user enable --now $UNIT_NAME${NC}"
    return 0
}

usage() {
    cat <<EOF
MP Harmony Agent installer (optional component of MP Mix Manager)

Usage: bash scripts/install_harmony_agent.sh [command]

Commands:
  install     Clone the agent, or fast-forward an existing checkout (default)
  status      Report whether the agent is installed
  path        Print the resolved agent directory
  deps        Install the Windows agent dependencies (openai-harmony, httpx,
              ddgs, pyreadline3) into the interpreter the launcher uses
  unit        Write/repoint the systemd user unit only

Environment:
  HARMONY_DIR         Install directory (default: \$HOME/OpenAI/MP_Harmony_Agent)
  HARMONY_REPO_URL    Git remote (default: ${REPO_URL})
  HARMONY_SKIP_PULL=1 Never update an existing checkout
  HARMONY_TIMEOUT     Git timeout in seconds (default: 120)
  MP_PY_BIN           Interpreter to install the Windows dependencies into
EOF
}

main() {
    local action="${1:-install}"
    case "$action" in
        install)        cmd_install ;;
        status)         cmd_status ;;
        path)           printf '%s\n' "$TARGET" ;;
        deps)           harmony_install_windows_deps ;;
        unit)           cmd_unit ;;
        -h|--help|help) usage; exit 0 ;;
        *)
            err "Unknown command: $action"
            usage >&2
            exit 2
            ;;
    esac
}

main "$@"
