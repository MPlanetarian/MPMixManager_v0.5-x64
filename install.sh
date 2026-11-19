#!/usr/bin/env bash
# ==============================================================================
# MP_Mix_Manager_v0.3 - Cross-Platform Automated Installer
# Supports Linux (Bazzite / SteamOS / Fedora / Ubuntu), macOS, Windows, and FreeBSD
#
# Reinstall / factory reset:
#   ./install.sh --reinstall                  Ask what to keep, then reinstall
#   ./install.sh --reinstall --yes            Keep everything and reinstall
#   ./install.sh --reinstall --fresh --yes    Wipe all config and reinstall
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

# Bright white for the brand logo. Deliberately not DIM/MAGENTA: the wordmark
# is the first thing the user sees, so it must read as clean white branding.
LOGO_WHITE='\033[1;97m'

# ------------------------------------------------------------------------------
# Brand logo
#
# Six-row block-letter wordmark: "MP MIX MANAGER" built from the block glyph
# U+2588 and the double-line box-drawing glyphs (U+2550-U+256C), so the letters
# have double-line stems, solid counters and a uniform letter height. It is
# printed in bright white and runs before anything else the installer emits.
# ------------------------------------------------------------------------------
print_brand_logo() {
    printf '\n'
    printf '%b\n' "${LOGO_WHITE}███╗   ███╗██████╗     ███╗   ███╗██╗██╗  ██╗    ███╗   ███╗ █████╗ ███╗   ██╗ █████╗  ██████╗ ███████╗██████╗ ${NC}"
    printf '%b\n' "${LOGO_WHITE}████╗ ████║██╔══██╗    ████╗ ████║██║╚██╗██╔╝    ████╗ ████║██╔══██╗████╗  ██║██╔══██╗██╔════╝ ██╔════╝██╔══██╗${NC}"
    printf '%b\n' "${LOGO_WHITE}██╔████╔██║██████╔╝    ██╔████╔██║██║ ╚███╔╝     ██╔████╔██║███████║██╔██╗ ██║███████║██║  ███╗█████╗  ██████╔╝${NC}"
    printf '%b\n' "${LOGO_WHITE}██║╚██╔╝██║██╔═══╝     ██║╚██╔╝██║██║ ██╔██╗     ██║╚██╔╝██║██╔══██║██║╚██╗██║██╔══██║██║   ██║██╔══╝  ██╔══██╗${NC}"
    printf '%b\n' "${LOGO_WHITE}██║ ╚═╝ ██║██║         ██║ ╚═╝ ██║██║██╔╝ ██╗    ██║ ╚═╝ ██║██║  ██║██║ ╚████║██║  ██║╚██████╔╝███████╗██║  ██║${NC}"
    printf '%b\n' "${LOGO_WHITE}╚═╝     ╚═╝╚═╝         ╚═╝     ╚═╝╚═╝╚═╝  ╚═╝    ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝${NC}"
    printf '\n'
}

print_brand_logo

_RESOLVED_SRC="${BASH_SOURCE[0]}"
while [ -h "$_RESOLVED_SRC" ]; do
    _RESOLVED_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
    _RESOLVED_SRC="$(readlink "$_RESOLVED_SRC")"
    [[ $_RESOLVED_SRC != /* ]] && _RESOLVED_SRC="$_RESOLVED_DIR/$_RESOLVED_SRC"
done
SCRIPT_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
unset _RESOLVED_SRC _RESOLVED_DIR

if [ -z "${VERSION:-}" ]; then
    if [ -f "$SCRIPT_DIR/VERSION" ]; then
        VERSION="$(head -n 1 "$SCRIPT_DIR/VERSION" | tr -d '\r\n')"
    else
        VERSION="0.5.4 Stable (Sensorium)"
    fi
fi
export VERSION

# Refuse to run as root. Everything here is a per-user install: launchers go in
# ~/.local/bin, the venv in ~/.local/share, config in ~/.config. Running with
# sudo writes all of that under root's HOME with root-owned files, and the real
# user's shortcuts then point at an interpreter they cannot update. The old
# script happily did this and only surfaced it later as permission errors.
if [ "$(id -u 2>/dev/null || echo 1)" -eq 0 ]; then
    echo -e "${RED}Do not run the installer as root (or with sudo).${NC}" >&2
    echo "Run it as the user who will use MP Mix Manager:  ./install.sh" >&2
    exit 1
fi

# 0. Optional Reinstall / Factory Reset
#
# This is a shift-based loop, not `for _arg in "$@"`. The old loop read the
# --apps value from "$2" (the second word of the command line), so any option
# placed before it swallowed the value: `./install.sh --reinstall --apps gui`
# set MP_INSTALL_APPS="--apps" and then skipped "gui". The selection was
# silently lost and all four apps were installed.
REINSTALL=0
REINSTALL_ARGS=()
MP_DRY_RUN="${MP_DRY_RUN:-0}"
while [ $# -gt 0 ]; do
    case "$1" in
        --reinstall|--re-install|--factory-reset|--reset)
            REINSTALL=1
            shift
            ;;
        --help|-h)
            echo -e "${BOLD}MP Mix Manager Installer${NC}"
            echo ""
            echo "Usage: ./install.sh [options] [-- reinstall options]"
            echo ""
            echo "Options:"
            echo "  --install                 Normal install (default)"
            echo "  --version, -V             Print the installer version and exit"
            echo "  --install-harmony         Install the optional MP Harmony Agent (default)"
            echo "  --no-harmony              Skip the optional MP Harmony Agent"
            echo "  --apps LIST               Set up all four apps (--apps all) or only"
            echo "                            the ones named: --apps gui"
            echo "                            (manager, gui, liquid, visualizer; comma-separated)"
            echo "  --reinstall               Wipe this user's configuration and reinstall"
            echo "                            from a first-install state"
            echo "  --preserve-playlists      Reinstall: keep playlists and favorites"
            echo "  --preserve-music-folders  Reinstall: keep music folder config"
            echo "  --preserve-archive-config Reinstall: keep mix archive folder config"
            echo "  --fresh                   Reinstall: preserve nothing"
            echo "  --yes, -y                 Reinstall: do not prompt; preserve all"
            echo "  --dry-run                 Reinstall: show what would change"
            echo "  --                        Pass every later option to --reinstall"
            echo "  MP_INSTALL_ACCOMPANIMENT=0  Run in silence (no theme track, no exit movie)"
            echo "  -h, --help                Show this help"
            echo ""
            echo "Converted audio files are never deleted by a reinstall."
            echo "Run ./scripts/reinstall_mp_manager.sh --help for the full option list."
            echo "Run ./uninstall.sh --help to reset or remove apps."
            exit 0
            ;;
        --version|-V)
            echo "MP Mix Manager installer (MP_Mix_Manager_v${VERSION#v})"
            exit 0
            ;;
        --install)
            # Explicit normal install. Nothing to do; the script proceeds.
            shift
            ;;
        --install-harmony)
            MP_INSTALL_HARMONY="always"
            export MP_INSTALL_HARMONY
            shift
            ;;
        --no-harmony)
            MP_INSTALL_HARMONY="never"
            export MP_INSTALL_HARMONY
            shift
            ;;
        --apps|--components)
            if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
                echo "--apps needs a value: all, manager, gui, liquid, visualizer" >&2
                exit 1
            fi
            MP_INSTALL_APPS="$2"
            export MP_INSTALL_APPS
            shift 2
            ;;
        --apps=*|--components=*)
            if [ -z "${1#*=}" ]; then
                echo "--apps needs a value: all, manager, gui, liquid, visualizer" >&2
                exit 1
            fi
            MP_INSTALL_APPS="${1#*=}"
            export MP_INSTALL_APPS
            shift
            ;;
        --preserve-playlists|--preserve-music-folders|--preserve-archive-config|\
        --no-preserve-playlists|--no-preserve-music-folders|--no-preserve-archive-config|\
        --keep-playlists|--keep-music-folders|--keep-archive-config|--no-preserve|\
        --fresh|--yes|-y|--dry-run)
            REINSTALL_ARGS+=("$1")
            [ "$1" = "--dry-run" ] && MP_DRY_RUN=1
            shift
            ;;
        --)
            shift
            while [ $# -gt 0 ]; do
                REINSTALL_ARGS+=("$1")
                [ "$1" = "--dry-run" ] && MP_DRY_RUN=1
                shift
            done
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            echo "Run ./install.sh --help to see the available options." >&2
            exit 1
            ;;
    esac
done
unset _arg 2>/dev/null || true

# Reinstall-only options are meaningless on a normal install. They used to be
# collected into REINSTALL_ARGS and silently ignored, so `./install.sh --fresh`
# ran a plain install and did the opposite of what it looked like.
if [ "$REINSTALL" -eq 0 ] && [ ${#REINSTALL_ARGS[@]} -gt 0 ]; then
    echo -e "${RED}These options only apply to --reinstall: ${REINSTALL_ARGS[*]}${NC}" >&2
    echo "Use: ./install.sh --reinstall ${REINSTALL_ARGS[*]}" >&2
    exit 2
fi

# The theme track plays until this process exits. Exit_MP_Video.mp4 loops in
# its own window for the same stretch, then the window is closed. The EXIT
# trap is what makes a normal finish immediate. The watcher also stops on its
# own if this process disappears without running the trap.
# MP_INSTALL_ACCOMPANIMENT=0 leaves the run silent.
MP_INSTALL_ACCOMPANIMENT_PID=""
MP_INSTALL_ACCOMPANIMENT_PIDFILE=""

mp_install_accompaniment_stop() {
    local pid="${MP_INSTALL_ACCOMPANIMENT_PID:-}"
    local pidfile="${MP_INSTALL_ACCOMPANIMENT_PIDFILE:-}"
    local script="$SCRIPT_DIR/scripts/install_accompaniment.sh"
    local i=0
    if [ -n "$pid" ]; then
        kill -TERM "$pid" 2>/dev/null || true
        while [ "$i" -lt 20 ] && kill -0 "$pid" 2>/dev/null; do
            sleep 0.05
            i=$((i + 1))
        done
        kill -KILL "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
    if [ -n "$pidfile" ] && [ -f "$script" ]; then
        bash "$script" --stop "$pidfile" >/dev/null 2>&1 || true
    fi
    MP_INSTALL_ACCOMPANIMENT_PID=""
    return 0
}

mp_install_accompaniment_abort() {
    local code="$1"
    trap - EXIT INT TERM HUP
    mp_install_accompaniment_stop
    exit "$code"
}

mp_install_accompaniment_start() {
    case "${MP_INSTALL_ACCOMPANIMENT:-1}" in
        0|false|no|off|never) return 0 ;;
    esac
    local script="$SCRIPT_DIR/scripts/install_accompaniment.sh"
    [ -f "$script" ] || return 0
    local theme="$SCRIPT_DIR/MP_SAMPLES/MPlanetarian - The Orbits of Planets (Cosmic Dreamworlds Remake).flac"
    local movie="$SCRIPT_DIR/Exit_MP_Video.mp4"
    if [ ! -f "$movie" ] && [ -f "$SCRIPT_DIR/assets/Exit_MP_Video.mp4" ]; then
        movie="$SCRIPT_DIR/assets/Exit_MP_Video.mp4"
    fi
    if [ -f "$theme" ]; then
        echo -e "${DIM}      • Theme track playing — it stops when the installer finishes${NC}"
    fi
    if [ -f "$movie" ]; then
        echo -e "${DIM}      • Exit movie looping — the window closes when the installer finishes${NC}"
    fi
    MP_INSTALL_ACCOMPANIMENT_PIDFILE="${TMPDIR:-/tmp}/mp-install-accompaniment-$$.pid"
    bash "$script" --watch "$$" --pidfile "$MP_INSTALL_ACCOMPANIMENT_PIDFILE" </dev/null >/dev/null 2>&1 &
    MP_INSTALL_ACCOMPANIMENT_PID=$!
    return 0
}

trap 'mp_install_accompaniment_stop' EXIT
trap 'mp_install_accompaniment_abort 130' INT
trap 'mp_install_accompaniment_abort 143' TERM
trap 'mp_install_accompaniment_abort 129' HUP
mp_install_accompaniment_start
if [ "${MP_INSTALL_ACCOMPANIMENT_SELF_TEST:-0}" = "1" ]; then
    sleep "${MP_INSTALL_ACCOMPANIMENT_SELF_TEST_SLEEP:-1}"
    exit 0
fi

if [ "$REINSTALL" -eq 1 ]; then
    echo -e "${BOLD}${MAGENTA}================================================================================${NC}"
    echo -e "${BOLD}${MAGENTA}        MP Mix Archive Manager Reinstall (MP_Mix_Manager_v${VERSION#v})          ${NC}"
    echo -e "${BOLD}${MAGENTA}          Linux • macOS • Windows 10 & 11 • FreeBSD                              ${NC}"
    echo -e "${BOLD}${MAGENTA}================================================================================${NC}"
    echo ""
    _reinstall_script="$SCRIPT_DIR/scripts/reinstall_mp_manager.sh"
    if [ ! -f "$_reinstall_script" ]; then
        echo -e "${RED}Error: scripts/reinstall_mp_manager.sh was not found.${NC}" >&2
        exit 1
    fi
    set +e
    if [ ${#REINSTALL_ARGS[@]} -gt 0 ]; then
        MP_REINSTALL_FROM_INSTALLER=1 bash "$_reinstall_script" --root "$SCRIPT_DIR" "${REINSTALL_ARGS[@]}"
    else
        MP_REINSTALL_FROM_INSTALLER=1 bash "$_reinstall_script" --root "$SCRIPT_DIR"
    fi
    _reinstall_rc=$?
    set -e
    if [ "$_reinstall_rc" -eq 10 ]; then
        echo -e "\n${YELLOW}Reinstall cancelled. The existing installation was left alone.${NC}"
        exit 0
    elif [ "$_reinstall_rc" -ne 0 ]; then
        echo -e "\n${RED}Reinstall stopped. No further changes were made.${NC}" >&2
        exit 1
    fi
    echo -e "${BOLD}${BLUE}Reinstalling the program around the fresh configuration...${NC}"
    echo ""
    if [ ${#REINSTALL_ARGS[@]} -gt 0 ]; then
        for _arg in "${REINSTALL_ARGS[@]}"; do
            if [ "$_arg" = "--dry-run" ]; then
                echo -e "${YELLOW}Dry run only: the program was not reinstalled.${NC}"
                exit 0
            fi
        done
    fi
    unset _arg
fi
unset _reinstall_script _reinstall_rc 2>/dev/null || true

echo -e "${BOLD}${MAGENTA}================================================================================${NC}"
echo -e "${BOLD}${MAGENTA}        MP Mix Archive Manager Installer (MP_Mix_Manager_v${VERSION#v})           ${NC}"
echo -e "${BOLD}${MAGENTA}          Cross-Platform: Linux • macOS • Windows 10 & 11                       ${NC}"
echo -e "${BOLD}${MAGENTA}================================================================================${NC}"
echo ""

# 1. Detect Host Environment
echo -e "${BOLD}${BLUE}[1/10] Detecting Host Platform & Environment...${NC}"
HOST_OS="linux"
case "$(uname -s)" in
    Darwin*)
        HOST_OS="macos"
        MAC_VER=$(sw_vers -productVersion 2>/dev/null || uname -r)
        MAC_ARCH=$(uname -m)
        echo -e "      Platform:     ${GREEN}Apple macOS${NC} (${CYAN}${MAC_VER}${NC}, ${MAC_ARCH})"
        ;;
    CYGWIN*|MINGW*|MSYS*)
        HOST_OS="windows"
        echo -e "      Platform:     ${GREEN}Microsoft Windows${NC} (Git Bash / MSYS2)"
        ;;
    FreeBSD*)
        HOST_OS="freebsd"
        echo -e "      Platform:     ${GREEN}FreeBSD${NC} ($(uname -r), $(uname -m))"
        ;;
    Linux*)
        if grep -qi microsoft /proc/version 2>/dev/null; then
            HOST_OS="wsl"
            echo -e "      Platform:     ${GREEN}Microsoft Windows${NC} (WSL2 Linux Subsystem)"
        else
            HOST_OS="linux"
            if [ -f /etc/os-release ]; then
                # Never `source /etc/os-release` here: it defines VERSION, which
                # this script exports as the app version. Sourcing it made the
                # banner (and every child that reads $VERSION) report the distro
                # release, e.g. "MP_Mix_Manager_v44.20261012.0 (Kinoite)".
                MP_OS_NAME="$( . /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-Linux}" )"
                MP_OS_ID="$( . /etc/os-release 2>/dev/null; printf '%s' "${ID:-}" )"
                MP_OS_VARIANT="$( . /etc/os-release 2>/dev/null; printf '%s' "${VARIANT_ID:-}" )"
                MP_OS_ALIAS="$( . /etc/os-release 2>/dev/null; printf '%s' "${NAME:-}" )"
                echo -e "      Platform:     ${GREEN}Linux${NC} (${CYAN}${MP_OS_NAME:-Linux}${NC})"
                if [[ "${MP_OS_ID:-}" == *"bazzite"* ]] || [[ "${MP_OS_VARIANT:-}" == *"bazzite"* ]] || [[ "${MP_OS_ALIAS:-}" == *"Bazzite"* ]]; then
                    echo -e "${GREEN}      ✓ Verified Bazzite Linux environment.${NC}"
                fi
                unset MP_OS_NAME MP_OS_ID MP_OS_VARIANT MP_OS_ALIAS
            else
                echo -e "      Platform:     ${GREEN}Generic Linux${NC}"
            fi
        fi
        ;;
    *)
        HOST_OS="linux"
        # ${OS} is never defined, and `set -u` turned an unknown uname into a
        # hard abort right here instead of a warning.
        echo -e "      Platform:     ${YELLOW}Unknown ($(uname -s 2>/dev/null || echo unknown))${NC}"
        ;;
esac
echo ""

# 1b. Resolve a working Python interpreter.
#     On Windows "python3" is usually the Microsoft Store *alias*, which exits
#     with status 49 and prints "Python was not found".  Under `set -e` that
#     aborted the installer silently at the first python3 call, so steps 4-7
#     (dependencies, config, symlinks, desktop shortcuts) never ran. Resolve a
#     real interpreter once, here, and use MP_PYTHON everywhere below.
#
#     3.8 is the floor: the app uses f-strings with =, walrus operators and
#     typing constructs that 3.6 cannot parse. The old 3.6 gate let a 3.6
#     interpreter through and then every launch failed with a SyntaxError.
#
#     scripts/mp_dependencies.py owns the candidate list and the capability
#     test, so the installer, the reinstall helper and the test harness all get
#     the same answer. Duplicating that search in bash is what made macOS pick
#     Apple's /usr/bin/python3 (no PyGObject) over Homebrew's python3 (has it).
MP_PYTHON=""
MP_PYTHON_MIN_MAJOR=3
MP_PYTHON_MIN_MINOR=8

mp_try_python() {
    local _cand="$1"
    [ -n "$_cand" ] || return 1
    [ -x "$_cand" ] || return 1
    case "$_cand" in
        */WindowsApps/*) return 1 ;;   # Store alias, not an interpreter
    esac
    "$_cand" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1
}

# Every probe below may fail, and that must never abort the installer. The
# script runs under `set -euo pipefail`, so a helper that exits non-zero (the
# Windows finder exits 1 when it finds nothing) would kill the whole run at
# step 1 with no message at all. That is exactly what happened on a fresh
# Windows machine. run_probe captures the output and the status, and callers
# decide what a failure means.
run_probe() {
    local _out="" _rc=0
    set +e
    _out="$("$@" 2>/dev/null)"
    _rc=$?
    set -e
    printf '%s\n' "$_out"
    return "$_rc"
}

# Windows resolves through the same .cmd the .bat launchers use, because Git
# Bash cannot execute a Windows python.exe path reliably until it is absolute.
if [ "$HOST_OS" = "windows" ] && [ -f "$SCRIPT_DIR/scripts/windows_find_python.cmd" ]; then
    if _found="$( cd "$SCRIPT_DIR/scripts" 2>/dev/null || exit 1
                  cmd //c "windows_find_python.cmd" 2>/dev/null )"; then
        _found="$(printf '%s\n' "$_found" | tr -d '\r' | tail -n 1)"
        if mp_try_python "$_found"; then MP_PYTHON="$_found"; fi
    fi
    unset _found
fi

# Ask the engine. It considers the venv, Homebrew, the Windows install folders,
# PATH and the system prefixes, and prefers an interpreter that can import gi.
if [ -z "$MP_PYTHON" ] && [ -f "$SCRIPT_DIR/scripts/mp_dependencies.py" ]; then
    _found=""
    for _bootstrap in python3 python; do
        _cand_bin="$(command -v "$_bootstrap" 2>/dev/null)" || continue
        [ -n "$_cand_bin" ] || continue
        case "$_cand_bin" in
            */WindowsApps/*) continue ;;
        esac
        mp_try_python "$_cand_bin" || continue
        _found="$("$_cand_bin" "$SCRIPT_DIR/scripts/mp_dependencies.py" --python-path 2>/dev/null | tr -d '\r' | head -n 1)" || _found=""
        [ -n "$_found" ] && break
    done
    unset _bootstrap _cand_bin
    if mp_try_python "$_found"; then MP_PYTHON="$_found"; fi
    unset _found
fi

# Last resort when the engine cannot run yet (no usable interpreter on PATH but
# one exists in a fixed location).
if [ -z "$MP_PYTHON" ]; then
    for _cand in \
        "${MP_PYTHON_OVERRIDE:-}" \
        "$HOME/.local/share/mp-mix-manager/python-env/bin/python3" \
        "$SCRIPT_DIR/venv/Scripts/python.exe" "$SCRIPT_DIR/venv/bin/python3" \
        /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.13 \
        /opt/homebrew/bin/python3.12 /usr/local/bin/python3 \
        /home/linuxbrew/.linuxbrew/bin/python3 \
        /usr/bin/python3 /usr/local/bin/python3.13; do
        if mp_try_python "$_cand"; then MP_PYTHON="$_cand"; break; fi
    done
    unset _cand
fi

if [ -n "$MP_PYTHON" ]; then
    echo -e "      Python:       ${GREEN}${MP_PYTHON}${NC} ($("$MP_PYTHON" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null))"
else
    # Nothing usable. Offer the platform's own installer rather than a bare
    # error: Python is a hard requirement for every part of the application.
    echo -e "      Python:       ${YELLOW}not found (3.8 or newer is required)${NC}"
    MP_INSTALL_PYTHON="${MP_INSTALL_PYTHON:-ask}"
    _mp_py_install=""
    _mp_py_needs_sudo=0
    case "$HOST_OS" in
        macos)
            _mp_py_install='if ! command -v brew >/dev/null 2>&1; then echo "Homebrew is required: https://brew.sh"; exit 1; fi; brew install python3'
            _mp_py_hint="brew install python3"
            ;;
        windows|wsl)
            _mp_py_install='winget install --silent --accept-package-agreements --accept-source-agreements --id Python.Python.3.13 --exact'
            _mp_py_hint="winget install Python.Python.3.13"
            ;;
        freebsd)
            _mp_py_install='pkg install -y python3'
            _mp_py_hint="sudo pkg install python3"
            _mp_py_needs_sudo=1
            ;;
        linux)
            # Do not source /etc/os-release: it defines VERSION, which this
            # script exports as the app version. The package manager command is
            # all that is needed here anyway.
            _mp_py_needs_sudo=1
            if command -v rpm-ostree >/dev/null 2>&1; then
                _mp_py_install='rpm-ostree install --allow-inactive --apply-live python3'
            elif command -v dnf >/dev/null 2>&1; then
                _mp_py_install='dnf install -y python3'
            elif command -v apt-get >/dev/null 2>&1; then
                _mp_py_install='apt-get install -y python3'
            elif command -v pacman >/dev/null 2>&1; then
                _mp_py_install='pacman -S --needed --noconfirm python3'
            elif command -v zypper >/dev/null 2>&1; then
                _mp_py_install='zypper --non-interactive install python3'
            fi
            _mp_py_hint="sudo dnf install python3  (or apt/pacman/zypper equivalent)"
            ;;
    esac
    [ -n "$_mp_py_install" ] || _mp_py_needs_sudo=0

    if [ -n "$_mp_py_install" ]; then
        _do_py_install=0
        if [ "$MP_INSTALL_PYTHON" = "always" ]; then
            _do_py_install=1
        elif [ "$MP_INSTALL_PYTHON" != "never" ] && [ -t 0 ]; then
            # `|| _mp_py_ans=""` keeps a piped/EOF run alive: under `set -e`
            # an EOF from read aborted the installer instead of taking the default.
            read -r -p "      Install Python 3 now? [Y/n] " _mp_py_ans || _mp_py_ans=""
            case "${_mp_py_ans:-y}" in
                n|N|no|NO) _do_py_install=0 ;;
                *) _do_py_install=1 ;;
            esac
            unset _mp_py_ans
        fi
        if [ "$_do_py_install" -eq 1 ]; then
            echo -e "${CYAN}      Installing Python 3...${NC}"
            set +e
            # Decide from the platform, not by pattern-matching the command
            # string. The macOS command begins with a brew presence check, so
            # the old `grep -qE '^(brew|winget)'` never matched and Homebrew was
            # run under sudo (which fails, or worse, installs root-owned files).
            if [ "${_mp_py_needs_sudo:-0}" -eq 1 ] && [ "$(id -u 2>/dev/null || echo 1)" -ne 0 ]; then
                if command -v sudo >/dev/null 2>&1; then
                    # shellcheck disable=SC2086
                    sudo sh -c "$_mp_py_install"
                else
                    sh -c "$_mp_py_install"
                fi
            else
                sh -c "$_mp_py_install"
            fi
            set -e
            # Re-resolve after the install; brew and rpm-ostree put python3 in
            # prefixes that may not be on this shell's PATH yet.
            for _cand in \
                /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                /usr/bin/python3 "$(command -v python3 2>/dev/null)"; do
                if mp_try_python "$_cand"; then MP_PYTHON="$_cand"; break; fi
            done
            unset _cand
            if [ -n "$MP_PYTHON" ]; then
                echo -e "${GREEN}      ✓ Python installed: ${CYAN}${MP_PYTHON}${NC}"
            fi
        fi
    fi
    if [ -z "$MP_PYTHON" ]; then
        echo -e "${YELLOW}      Install Python 3.8+ manually: ${CYAN}${_mp_py_hint}${NC}"
        echo -e "${DIM}      The installer will finish the remaining steps, but MP Mix Manager${NC}"
        echo -e "${DIM}      and its players cannot start until Python is present.${NC}"
    fi
    unset _mp_py_install _mp_py_hint
fi
unset MP_INSTALL_PYTHON 2>/dev/null || true
echo ""

# 2. Ensure Executable Permissions
echo -e "${BOLD}${BLUE}[2/10] Configuring Script & Binary Permissions...${NC}"
chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.command "$SCRIPT_DIR"/bin/* 2>/dev/null || true
if [ -d "$SCRIPT_DIR/scripts" ]; then
    chmod +x "$SCRIPT_DIR"/scripts/*.sh "$SCRIPT_DIR"/scripts/*.py 2>/dev/null || true
fi
chmod +x "$SCRIPT_DIR"/bin/cliamp 2>/dev/null || true
echo -e "${GREEN}      ✓ Executable permissions granted across all scripts and tools.${NC}"
echo ""

# 3. Create Scaffold Working Directories
echo -e "${BOLD}${BLUE}[3/10] Setting Up Archive Directory Structure...${NC}"
mkdir -p "$SCRIPT_DIR"/{FLAC_CONVERTED_OUTPUTS,MP3_CONVERTED_OUTPUTS,WAV_CONVERTED_OUTPUTS,MP4_CONVERTED_OUTPUTS,CONVERTED_WAV_FILES,SPEK_OUTPUTS,BACKUP_LOGS,VERIFY_LOGS,IMPORT_LOGS,COVERS,config_backups,exported_configs}
touch "$SCRIPT_DIR/FLAC_CONVERTED_OUTPUTS/.gitkeep"
touch "$SCRIPT_DIR/MP3_CONVERTED_OUTPUTS/.gitkeep"
touch "$SCRIPT_DIR/WAV_CONVERTED_OUTPUTS/.gitkeep"
touch "$SCRIPT_DIR/MP4_CONVERTED_OUTPUTS/.gitkeep"
touch "$SCRIPT_DIR/CONVERTED_WAV_FILES/.gitkeep"
touch "$SCRIPT_DIR/SPEK_OUTPUTS/.gitkeep"
touch "$SCRIPT_DIR/BACKUP_LOGS/.gitkeep"
touch "$SCRIPT_DIR/VERIFY_LOGS/.gitkeep"
touch "$SCRIPT_DIR/IMPORT_LOGS/.gitkeep"
touch "$SCRIPT_DIR/COVERS/.gitkeep"
touch "$SCRIPT_DIR/config_backups/.gitkeep"
touch "$SCRIPT_DIR/exported_configs/.gitkeep"
echo -e "${GREEN}      ✓ Archive, output, and log directories ready.${NC}"
echo ""

# 3b. Offer the manual before the long automatic phase
#
# Everything from here on is unattended: packages are fetched, Python modules
# are installed, services are registered. That takes minutes on a slow link and
# the user has nothing to do but wait. Offer the README now so they can read
# what is being installed while it happens. On a TTY-only session the helper
# prints the path instead of trying to open a window. It never fails the run.
if [ -f "$SCRIPT_DIR/scripts/mp_show_readme.sh" ] && [ "${MP_DRY_RUN:-0}" != "1" ]; then
    bash "$SCRIPT_DIR/scripts/mp_show_readme.sh" || true
fi
echo ""

# 4. Check & Install Dependencies
#
# One engine (scripts/mp_dependencies.py) owns the list of what the app needs,
# the package name on every distro, and how to install it. The installer just
# drives it, so the answers cannot drift from the code that imports them.
echo -e "${BOLD}${BLUE}[4/10] Checking & Installing Dependencies...${NC}"
MP_DEPS="$SCRIPT_DIR/scripts/mp_dependencies.py"
MP_DEV_CHOICE="${MP_INSTALL_DEV_DEPS:-ask}"
MP_DEPS_OK=1

if [ ! -f "$MP_DEPS" ]; then
    echo -e "${YELLOW}      ⚠ scripts/mp_dependencies.py is missing; skipping dependency setup.${NC}"
    MP_DEPS_OK=0
elif [ -z "$MP_PYTHON" ]; then
    echo -e "      [${RED}MISSING${NC}] No working Python 3.8+ interpreter was found."
    echo -e "${YELLOW}      Python is required to run MP Mix Manager.${NC}"
    if [ "$HOST_OS" = "macos" ]; then
        echo -e "${CYAN}      Install it with:  brew install python3${NC}"
    elif [ "$HOST_OS" = "windows" ] || [ "$HOST_OS" = "wsl" ]; then
        echo -e "${CYAN}      Install it with:  winget install Python.Python.3.13${NC}"
        echo -e "${CYAN}      or download it from https://www.python.org/downloads/windows/${NC}"
    elif [ "$HOST_OS" = "freebsd" ]; then
        echo -e "${CYAN}      Install it with:  sudo pkg install python3${NC}"
    else
        echo -e "${CYAN}      Fedora/Bazzite: sudo rpm-ostree install python3${NC}"
        echo -e "${CYAN}      Debian/Ubuntu:  sudo apt install python3${NC}"
    fi
    echo -e "${DIM}      Re-run ./install.sh afterwards; it will finish the setup.${NC}"
    MP_DEPS_OK=0
else
    # --install probes, installs whatever is missing, then probes again. It
    # exits non-zero only when a *required* dependency is still absent.
    set +e
    MP_DEPS_RC=0
    if [ "${MP_DRY_RUN:-0}" = "1" ]; then
        "$MP_PYTHON" "$MP_DEPS" --install --dry-run --python "$MP_PYTHON"
        MP_DEPS_RC=$?
    else
        "$MP_PYTHON" "$MP_DEPS" --install --python "$MP_PYTHON"
        MP_DEPS_RC=$?
    fi
    set -e

    # The engine may have created the per-user venv that the launchers prefer.
    # Point the rest of the installer at it so the desktop entries, tray and
    # shortcut scripts all use the interpreter that now holds the modules.
    #
    # The old check was `import numpy`, which made the handoff depend on one
    # module. When numpy was installed but unimportable (an x86-64-v2 wheel on a
    # pre-AVX CPU), the installer kept the bare system interpreter, the desktop
    # entries and tray were written against it, and the final --verify gate then
    # reported "not ready" while the dependency step had said 8/8 present. Ask
    # the engine whether it produced a usable venv instead of importing one
    # library here.
    MP_ENV_PYTHON=""
    for _cand in \
        "$HOME/.local/share/mp-mix-manager/python-env/bin/python3" \
        "$HOME/.local/share/mp-mix-manager/python-env/bin/python" \
        "$SCRIPT_DIR/venv/Scripts/python.exe" \
        "$SCRIPT_DIR/venv/bin/python3"; do
        if [ -x "$_cand" ]; then
            MP_ENV_PYTHON="$_cand"
            break
        fi
    done
    if [ -n "$MP_ENV_PYTHON" ]; then
        MP_PYTHON="$MP_ENV_PYTHON"
        echo -e "${GREEN}      ✓ Python environment: ${CYAN}${MP_ENV_PYTHON}${NC}"
    fi
    unset _cand

    if [ "$MP_DEPS_RC" -ne 0 ]; then
        MP_DEPS_OK=0
    fi

    # ---- VTE 2.91 for the embedded terminal in MP Liquid Glass ---------------
    # Bazzite and other immutable images do not ship vte291, and a user cannot
    # layer it on without a reboot. mp_dependencies.py reports the typelib as
    # missing there, which is accurate but leaves the glass window unable to
    # start ("Namespace Vte not available"). Download the distribution's own
    # package and unpack the two files into ~/.local/lib/mp-liquid-glass; the
    # launchers already put that directory on LD_LIBRARY_PATH/GI_TYPELIB_PATH.
    if [ "${MP_DRY_RUN:-0}" != "1" ] && [ -x "$SCRIPT_DIR/scripts/mp_vte_local.sh" ]; then
        if "$SCRIPT_DIR/scripts/mp_vte_local.sh" --check >/dev/null 2>&1; then
            echo -e "${GREEN}      ✓ VTE 2.91 terminal library present.${NC}"
        else
            echo -e "${DIM}      VTE 2.91 is missing; fetching it for the embedded terminal...${NC}"
            set +e
            MP_VTE_SILENT=0 "$SCRIPT_DIR/scripts/mp_vte_local.sh" --install
            MP_VTE_RC=$?
            set -e
            case "$MP_VTE_RC" in
                0) echo -e "${GREEN}      ✓ VTE 2.91 installed to ~/.local/lib/mp-liquid-glass${NC}" ;;
                2) echo -e "${DIM}      VTE bundling does not apply on this platform.${NC}" ;;
                *)
                    echo -e "${YELLOW}      ⚠ VTE 2.91 could not be installed automatically.${NC}"
                    echo -e "${DIM}        MP Liquid Glass needs it for its embedded terminal.${NC}"
                    echo -e "${DIM}        Retry any time with:  ./scripts/mp_vte_local.sh --install${NC}"
                    ;;
            esac
        fi
    fi

    # Optional extras: report them, and offer to install them when this is an
    # interactive run. They are never allowed to fail the install.
    if [ "$MP_DEPS_RC" -eq 0 ] && [ "${MP_DRY_RUN:-0}" != "1" ]; then
        if [ "$MP_DEV_CHOICE" = "always" ]; then
            "$MP_PYTHON" "$MP_DEPS" --install --optional --python "$MP_PYTHON" || true
        else
            echo ""
            if [ "$MP_DEV_CHOICE" = "never" ]; then
                echo -e "${DIM}      Optional extras skipped (MP_INSTALL_DEV_DEPS=never).${NC}"
            elif [ -t 0 ]; then
                echo -e "${DIM}      Optional extras (spectrograms, cloud backup, PDF reports, tray) are\n      not required. Install them now?${NC}"
                read -r -p "      Install optional extras? [y/N] " _mp_opt || _mp_opt=""
                case "${_mp_opt:-n}" in
                    y|Y|yes|YES) "$MP_PYTHON" "$MP_DEPS" --install --optional --python "$MP_PYTHON" || true ;;
                    *) echo -e "${DIM}      Skipped. Run 'scripts/mp_dependencies.py --install --optional' any time.${NC}" ;;
                esac
                unset _mp_opt
            else
                echo -e "${DIM}      Non-interactive run: optional extras skipped.${NC}"
                echo -e "${DIM}      Add them with: $MP_PYTHON $MP_DEPS --install --optional${NC}"
            fi
        fi
    fi
fi
echo ""

# 5. User Configuration Setup
echo -e "${BOLD}${BLUE}[5/10] Checking Configuration File...${NC}"
MP_CONFIG_CREATED=0
if [ ! -f "$SCRIPT_DIR/config.env" ]; then
    if [ -f "$SCRIPT_DIR/config.env.example" ]; then
        # config.env can hold SMTP and SMB passwords (see the *PASSWORD keys in
        # the template). Create it private from the start; copying it with the
        # template's mode left it world-readable on a shared machine.
        ( umask 077; cp "$SCRIPT_DIR/config.env.example" "$SCRIPT_DIR/config.env" ) 2>/dev/null \
            || cp "$SCRIPT_DIR/config.env.example" "$SCRIPT_DIR/config.env"
        chmod 600 "$SCRIPT_DIR/config.env" 2>/dev/null || true
        MP_CONFIG_CREATED=1
        echo -e "${GREEN}      ✓ Created default config.env from template (mode 600).${NC}"
    fi
else
    echo -e "${GREEN}      ✓ Active config.env found.${NC}"
    # An install from an older version may have left it group/world readable.
    if [ -w "$SCRIPT_DIR/config.env" ]; then
        chmod 600 "$SCRIPT_DIR/config.env" 2>/dev/null || true
    fi
fi

# First install selects Dreamworlds Ultra [Default]. The shared theme
# file is what the CLI manager, MP Gui, the Liquid Player and the tracklist
# viewer all read, so writing it here means every window opens in that theme.
# An existing choice is never overwritten: a reinstall or an update keeps the
# theme the user picked with C.
MP_THEME_DIR="$HOME/.config/mix-manager"
MP_THEME_FILE="$MP_THEME_DIR/theme"
mkdir -p "$MP_THEME_DIR" 2>/dev/null || true
if [ ! -f "$MP_THEME_FILE" ]; then
    printf '%s\n' "dreamworlds_ultra" > "$MP_THEME_FILE" 2>/dev/null || true
    echo -e "${GREEN}      ✓ Default theme: Dreamworlds Ultra [Default]${NC}"
else
    echo -e "${DIM}      • Keeping your saved theme: $(tr -d ' \t\n\r' < "$MP_THEME_FILE" 2>/dev/null)${NC}"
fi

# Pick the MP Visualizer backend for this machine. NVIDIA GPUs that advertise a
# working Vulkan device get Vulkan; everything else stays on OpenGL. The
# decision lives in scripts/visualizer_backend.py so the installer, the app and
# the tests cannot disagree.
#
# The template ships "opengl" as a placeholder, so a config that was just
# created from it is not a user choice and gets replaced. A config the user has
# already edited keeps its value unless it is still "auto"/empty.
# MP_VISUALIZER_BACKEND=vulkan|opengl|auto forces the answer.
MP_BACKEND_CHOICE="${MP_VISUALIZER_BACKEND:-auto}"
if [ -n "$MP_PYTHON" ] && [ -f "$SCRIPT_DIR/scripts/visualizer_backend.py" ]; then
    set +e
    _mp_backend="$("$MP_PYTHON" "$SCRIPT_DIR/scripts/visualizer_backend.py" \
        --recommend-backend 2>/dev/null | tr -d '\r' | head -n 1)"
    _mp_backend_rc=$?
    set -e
    [ "$_mp_backend_rc" -eq 0 ] || _mp_backend=""
else
    _mp_backend=""
fi
if [ -z "$_mp_backend" ]; then
    # No interpreter or no probe: fall back to the PCI ids the kernel already
    # exposes. NVIDIA's ICD only ships with the proprietary driver, so the
    # json file is what proves the GPU can actually present Vulkan.
    _mp_backend="opengl"
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        for _mp_icd in /usr/share/vulkan/icd.d/nvidia_icd.x86_64.json \
                       /usr/share/vulkan/icd.d/nvidia_icd.json \
                       /etc/vulkan/icd.d/nvidia_icd.json; do
            if [ -f "$_mp_icd" ]; then
                _mp_backend="vulkan"
                break
            fi
        done
        unset _mp_icd
    fi
fi
case "$(printf '%s' "$MP_BACKEND_CHOICE" | tr '[:upper:]' '[:lower:]')" in
    opengl|gl|ogl) _mp_backend="opengl" ;;
    vulkan|vk)     _mp_backend="vulkan" ;;
esac
unset MP_BACKEND_CHOICE

if [ -f "$SCRIPT_DIR/config.env" ]; then
    _mp_existing="$(grep -E '^LIQUID_GLASS_BACKEND=' "$SCRIPT_DIR/config.env" 2>/dev/null | head -n 1 | cut -d'=' -f2- | tr -d '"'\'' ' || true)"
    case "$(printf '%s' "$_mp_existing" | tr '[:upper:]' '[:lower:]')" in
        ""|auto) _mp_keep_existing=0 ;;
        *)       _mp_keep_existing=1 ;;
    esac
    if [ "$MP_CONFIG_CREATED" -eq 0 ] && [ "$_mp_keep_existing" -eq 1 ]; then
        echo -e "${GREEN}      ✓ Visualizer backend kept: ${CYAN}${_mp_existing}${NC}"
    else
        if [ -n "$MP_PYTHON" ] && "$MP_PYTHON" "$SCRIPT_DIR/scripts/visualizer_backend.py" \
                --apply-config "$SCRIPT_DIR/config.env" --backend "$_mp_backend" >/dev/null 2>&1; then
            :
        else
            printf 'LIQUID_GLASS_BACKEND="%s"\n' "$_mp_backend" >> "$SCRIPT_DIR/config.env" 2>/dev/null || true
        fi
        _mp_gpu="$(printf '%s' "$_mp_backend" | tr '[:lower:]' '[:upper:]')"
        echo -e "${GREEN}      ✓ Visualizer backend detected: ${CYAN}${_mp_gpu}${NC}"
        unset _mp_gpu
        # Launchers started outside this tree read
        # ~/.config/mix-manager/config.env, so the same choice has to reach it.
        if [ -f "$HOME/.config/mix-manager/config.env" ] && [ -n "$MP_PYTHON" ]; then
            "$MP_PYTHON" "$SCRIPT_DIR/scripts/visualizer_backend.py" \
                --apply-config "$HOME/.config/mix-manager/config.env" --backend "$_mp_backend" >/dev/null 2>&1 || true
        fi
    fi
fi
unset _mp_backend _mp_backend_rc _mp_existing _mp_keep_existing MP_CONFIG_CREATED
echo ""

# 6. Install Global/Local User Symlinks
echo -e "${BOLD}${BLUE}[6/10] Installing Local User Symlinks & Launchers...${NC}"
mkdir -p "$HOME/.local/bin"

# link <target> <name-in-~/.local/bin>
# link <target> <name>       publish ~/.local/bin/<name>
# link <target> <full-path>  publish an explicit path (e.g. ~/manager.sh)
# Only links a target that exists. A link to a missing file is worse than no
# link: `cliamp` was published as a dead symlink because bin/cliamp is shipped
# by the cliamp package (Homebrew/Flatpak) and is not part of this tree.
MP_LINKED=0
MP_LINK_SKIPPED=()
link() {
    local target="$1" name="$2" dest
    case "$name" in
        /*) dest="$name" ;;
        *)  dest="$HOME/.local/bin/$name" ;;
    esac
    if [ ! -e "$target" ]; then
        MP_LINK_SKIPPED+=("${name##*/}")
        return 0
    fi
    mkdir -p "$(dirname "$dest")" 2>/dev/null || true
    if ln -sf "$target" "$dest" 2>/dev/null; then
        MP_LINKED=$((MP_LINKED + 1))
    else
        MP_LINK_SKIPPED+=("${name##*/}")
    fi
    return 0
}

link "$SCRIPT_DIR/bin/mix-archive-manager" "mix-archive-manager"
link "$SCRIPT_DIR/Mix_Archive_Manager.sh" "manager"
link "$SCRIPT_DIR/Mix_Archive_Manager.sh" "$HOME/manager.sh"
link "$SCRIPT_DIR/bin/mp-desktop-exec" "mp-desktop-exec"
link "$SCRIPT_DIR/bin/launch-manager-fullscreen" "launch-manager-fullscreen"
link "$SCRIPT_DIR/bin/transfer-monitor" "transfer-monitor"
link "$SCRIPT_DIR/bin/chrome-upload-monitor" "chrome-upload-monitor"
link "$SCRIPT_DIR/bin/update-nft-playlist" "update-nft-playlist"
link "$SCRIPT_DIR/bin/watch-nft-copy-and-update.sh" "watch-nft-copy-and-update.sh"
link "$SCRIPT_DIR/bin/list-midi-devices" "list-midi-devices"
link "$SCRIPT_DIR/scripts/view_tracklist_console.sh" "view-tracklist"
link "$SCRIPT_DIR/scripts/inspect_playing_audio.sh" "view-mix-specs"
link "$SCRIPT_DIR/bin/dj-monitor" "dj-monitor"
link "$SCRIPT_DIR/scripts/dj_monitor.sh" "dj_monitor.sh"
link "$SCRIPT_DIR/Cut_Video.sh" "cut-video"
link "$SCRIPT_DIR/Split_FLAC_File.sh" "split-flac"
link "$SCRIPT_DIR/Split_Video_File.sh" "split-video"
link "$SCRIPT_DIR/scripts/kc_start_wan2gp_profile45.sh" "wan2gp-start-profile45"
link "$SCRIPT_DIR/scripts/kc_stop_wan2gp.sh" "wan2gp-stop"
link "$SCRIPT_DIR/scripts/kc_run_ollama_serve.sh" "ollama-serve"
link "$SCRIPT_DIR/scripts/kc_stop_ollama.sh" "ollama-stop"
link "$SCRIPT_DIR/scripts/manage_ollama.sh" "manage-ollama"
link "$SCRIPT_DIR/scripts/manage_harmony_agent.sh" "manage-harmony"
link "$SCRIPT_DIR/scripts/install_harmony_agent.sh" "install-harmony"
link "$SCRIPT_DIR/scripts/mp_harmony_tts.py" "mp-harmony-tts"
link "$SCRIPT_DIR/scripts/kc_launch_beszel_hub_and_agent.sh" "beszel-hub-and-agent"
link "$SCRIPT_DIR/scripts/beszel.sh" "beszel"
link "$SCRIPT_DIR/scripts/kc_run_aider_qwen.sh" "aider-qwen"

# A dead link left by an older install would shadow a good one from the
# package manager, so remove it before publishing the new set.
if [ -L "$HOME/.local/bin/cliamp" ] && [ ! -e "$HOME/.local/bin/cliamp" ]; then
    rm -f "$HOME/.local/bin/cliamp"
fi
_mp_cliamp=""
for _c in "$(command -v cliamp 2>/dev/null)" \
          "/opt/homebrew/bin/cliamp" \
          "/home/linuxbrew/.linuxbrew/bin/cliamp" \
          "/var/lib/flatpak/exports/bin/io.github.cliamp" \
          "$SCRIPT_DIR/bin/cliamp"; do
    if [ -n "$_c" ] && [ -x "$_c" ]; then _mp_cliamp="$_c"; break; fi
done
unset _c
if [ -n "$_mp_cliamp" ]; then
    echo -e "${GREEN}      ✓ cliamp found: ${CYAN}${_mp_cliamp}${NC}"
else
    echo -e "${DIM}      • cliamp (retro player) is not installed; menu 15 marks it unavailable.${NC}"
fi
unset _mp_cliamp

# Record this install tree and publish the resolver library, so launchers and
# desktop entries do not have to hard-code a user name or a versioned path.
mkdir -p "$HOME/.config/mix-manager/lib" 2>/dev/null || true
printf '%s\n' "$SCRIPT_DIR" > "$HOME/.config/mix-manager/install_dir" 2>/dev/null || true
if [ -f "$SCRIPT_DIR/scripts/lib/mp_install_dir.sh" ]; then
    cp -f "$SCRIPT_DIR/scripts/lib/mp_install_dir.sh" "$HOME/.config/mix-manager/lib/mp_install_dir.sh" 2>/dev/null || true
fi
# Keep the user config next to the resolver so a launcher started outside this
# tree still finds the archive and theme settings.
if [ -f "$SCRIPT_DIR/config.env" ] && [ ! -f "$HOME/.config/mix-manager/config.env" ]; then
    # `cp -p` is intentional: the source is the private (mode 600) config, so
    # the copy keeps those permissions instead of widening them.
    ( umask 077; cp -p "$SCRIPT_DIR/config.env" "$HOME/.config/mix-manager/config.env" ) 2>/dev/null || true
    chmod 600 "$HOME/.config/mix-manager/config.env" 2>/dev/null || true
fi

echo -e "${GREEN}      ✓ Installed ${MP_LINKED} launchers into ~/.local/bin and ~/manager.sh.${NC}"
if [ ${#MP_LINK_SKIPPED[@]} -gt 0 ]; then
    echo -e "${DIM}      • not linked (not in this build): ${MP_LINK_SKIPPED[*]}${NC}"
fi
unset MP_LINKED MP_LINK_SKIPPED

# Publish ~/.local/bin on PATH. Plenty of distributions (and GNOME/KDE
# terminals that start from a desktop session) do not include it, so `manager`
# worked only if the user had already edited a profile by hand. Add a small
# drop-in rather than editing .bashrc: it is idempotent and easy to remove.
if [ "$HOST_OS" != "windows" ] && [ "$HOST_OS" != "wsl" ]; then
    case ":${PATH:-}:" in
        *":$HOME/.local/bin:"*)
            echo -e "${GREEN}      ✓ ~/.local/bin is already on PATH.${NC}"
            ;;
        *)
            _mp_path_done=0
            if [ "$HOST_OS" = "macos" ]; then
                _mp_profile="$HOME/.zprofile"
                _mp_marker="# Added by MP Mix Manager installer"
                if [ -f "$_mp_profile" ] && grep -qF "$_mp_marker" "$_mp_profile" 2>/dev/null; then
                    _mp_path_done=1
                else
                    {
                        printf '\n%s\n' "$_mp_marker"
                        printf '%s\n' 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'
                    } >> "$_mp_profile" 2>/dev/null && _mp_path_done=1
                    unset _mp_marker
                fi
                unset _mp_marker
            elif [ -d "$HOME/.config/environment.d" ] || mkdir -p "$HOME/.config/environment.d" 2>/dev/null; then
                # systemd user environment: read by every login session on
                # Bazzite, Fedora, Arch and Debian.
                printf '%s\n' 'PATH=$HOME/.local/bin:$PATH' \
                    > "$HOME/.config/environment.d/90-mp-mix-manager.conf" 2>/dev/null && _mp_path_done=1
            fi
            if [ "$_mp_path_done" -eq 1 ]; then
                echo -e "${GREEN}      ✓ Added ~/.local/bin to PATH for future sessions.${NC}"
                if [ "$HOST_OS" != "macos" ]; then
                    echo -e "${DIM}        Log out and back in, or run: export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
                fi
            else
                echo -e "${YELLOW}      ⚠ Could not add ~/.local/bin to PATH automatically.${NC}"
                echo -e "${DIM}        Add it yourself: export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
            fi
            unset _mp_path_done _mp_profile
            ;;
    esac
fi

# Register KWin Borderless Window Rule on Bazzite / KDE Plasma
if [ "$HOST_OS" = "linux" ] && [ -n "$MP_PYTHON" ]; then
    "$MP_PYTHON" -c "
import configparser, os, uuid, subprocess

rc_path = os.path.expanduser('~/.config/kwinrulesrc')
if not os.path.exists(rc_path):
    os.makedirs(os.path.dirname(rc_path), exist_ok=True)
    with open(rc_path, 'w') as f:
        f.write('[General]\ncount=0\nrules=\n')

cp = configparser.ConfigParser()
cp.read(rc_path)

rule_desc = 'Mix Tracklist Viewer (Borderless)'
found = False
for sec in cp.sections():
    if cp.has_option(sec, 'description') and cp.get(sec, 'description') == rule_desc:
        found = True
        break

if not found:
    new_uuid = str(uuid.uuid4())
    cp.add_section(new_uuid)
    cp.set(new_uuid, 'description', rule_desc)
    cp.set(new_uuid, 'wmclass', 'konsole')
    cp.set(new_uuid, 'wmclassmatch', '1')
    cp.set(new_uuid, 'title', 'Mix Tracklist Viewer')
    cp.set(new_uuid, 'titlematch', '1')
    cp.set(new_uuid, 'types', '1')
    cp.set(new_uuid, 'noborder', 'true')
    cp.set(new_uuid, 'noborderrule', '2')

    if not cp.has_section('General'):
        cp.add_section('General')
    count = int(cp.get('General', 'count', fallback='0')) + 1
    cp.set('General', 'count', str(count))
    existing_rules = cp.get('General', 'rules', fallback='')
    rules_list = [r.strip() for r in existing_rules.split(',') if r.strip()]
    rules_list.append(new_uuid)
    cp.set('General', 'rules', ','.join(rules_list))

    with open(rc_path, 'w') as f:
        cp.write(f)

    if os.path.exists('/usr/bin/qdbus'):
        subprocess.run(['/usr/bin/qdbus', 'org.kde.KWin', '/KWin', 'reconfigure'], capture_output=True)
" 2>/dev/null || true
    echo -e "${GREEN}      ✓ Configured KWin borderless console window rule for Bazzite / KDE.${NC}"
fi
echo ""

# 7. MP Companion Service
#
# The Studio & DJ Booth Mobile Web Companion (scripts/mix_web_companion.py) is
# the phone/tablet dashboard on port 8888. Until now the installer created the
# desktop shortcuts and the tray icon but never registered the companion's
# service, so `mix_web_companion.sh install` had to be run by hand after every
# install and the dashboard did not come back after a reboot. Install it here
# so a plain `./install.sh` leaves a working, auto-starting companion.
#
# Linux uses a `systemd --user` unit (mp-web-companion.service); macOS uses a
# launchd LaunchAgent (com.mplanetarian.mp-web-companion). Windows/WSL have
# neither, and the companion is started from the tray there instead.
echo -e "${BOLD}${BLUE}[7/10] Installing the MP Companion Service...${NC}"
MP_COMPANION_SCRIPT="$SCRIPT_DIR/scripts/mix_web_companion.sh"
MP_COMPANION_CHOICE="${MP_INSTALL_COMPANION:-auto}"
MP_COMPANION_OK=0
if [ "$MP_COMPANION_CHOICE" = "never" ]; then
    echo -e "${DIM}      • Companion service skipped (MP_INSTALL_COMPANION=never).${NC}"
    MP_COMPANION_OK=-1
elif [ "$HOST_OS" = "windows" ] || [ "$HOST_OS" = "wsl" ]; then
    # Windows has no systemd --user or launchd for a per-user process. The
    # equivalent is a shortcut in the user's Startup folder plus a start now,
    # both of which scripts/windows_user_integration.ps1 writes in step 10. Say
    # so instead of claiming the companion is unsupported.
    echo -e "${DIM}      • Windows: the companion starts at login from Startup\\MP Web Companion.lnk${NC}"
    echo -e "${DIM}        (installed with the desktop shortcuts in step 10, then started)${NC}"
    MP_COMPANION_OK=-1
elif [ ! -f "$MP_COMPANION_SCRIPT" ]; then
    echo -e "${YELLOW}      ⚠ scripts/mix_web_companion.sh is missing; companion not installed.${NC}"
elif [ -z "$MP_PYTHON" ]; then
    echo -e "${YELLOW}      ⚠ The companion needs Python 3.8+; install it and re-run ./install.sh.${NC}"
else
    # An SSH or cron install has no desktop session environment, so systemctl
    # --user cannot find the user's bus and the service would look "installed"
    # while never being enabled. Point it at the real runtime directory when the
    # user is logged in; the wrapper still writes the unit when there is none.
    _MP_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u 2>/dev/null || echo 0)}"
    if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "$_MP_RUNTIME_DIR" ]; then
        export XDG_RUNTIME_DIR="$_MP_RUNTIME_DIR"
    fi
    if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$_MP_RUNTIME_DIR/bus" ]; then
        export DBUS_SESSION_BUS_ADDRESS="unix:path=$_MP_RUNTIME_DIR/bus"
    fi
    unset _MP_RUNTIME_DIR
    set +e
    if MP_PYTHON="$MP_PYTHON" bash "$MP_COMPANION_SCRIPT" install; then
        MP_COMPANION_OK=1
    else
        MP_COMPANION_OK=0
    fi
    set -e
    if [ "$MP_COMPANION_OK" -eq 1 ]; then
        echo -e "${GREEN}      ✓ MP Companion Service installed and started on port 8888.${NC}"
        if [ "$HOST_OS" = "macos" ]; then
            echo -e "${DIM}        Autostart: launchd com.mplanetarian.mp-web-companion${NC}"
        else
            echo -e "${DIM}        Autostart: systemd --user mp-web-companion.service${NC}"
        fi
    else
        echo -e "${YELLOW}      ⚠ The companion service could not be started automatically.${NC}"
        echo -e "${DIM}        Finish it at next login with: scripts/mix_web_companion.sh install${NC}"
    fi
fi
unset MP_COMPANION_SCRIPT MP_COMPANION_CHOICE MP_COMPANION_OK
echo ""

# 8. Optional: MP Harmony Agent
#
# The MP Harmony Agent is a SEPARATE project (github.com/MPlanetarian/
# MP-Harmony-Agent), not a part of this tree. It is offered here because the
# manager launches and supervises it, but nothing in MP Mix Manager requires
# it: decline and every other feature still works.
#
# Default is YES on an interactive run. MP_INSTALL_HARMONY controls it:
#   always | ask (default) | never
echo -e "${BOLD}${BLUE}[8/10] MP Harmony Agent (Optional)...${NC}"
MP_HARMONY_CHOICE="${MP_INSTALL_HARMONY:-ask}"
MP_HARMONY_INSTALLER="$SCRIPT_DIR/scripts/install_harmony_agent.sh"
MP_HARMONY_WANT=0
if [ "$MP_HARMONY_CHOICE" = "never" ]; then
    echo -e "${DIM}      • MP Harmony Agent skipped (MP_INSTALL_HARMONY=never).${NC}"
elif [ "$MP_HARMONY_CHOICE" = "always" ]; then
    MP_HARMONY_WANT=1
elif [ ! -t 0 ]; then
    # Non-interactive installs get the default, which is to include it.
    MP_HARMONY_WANT=1
    echo -e "${DIM}      • Non-interactive run: installing the agent (the default).${NC}"
else
    echo -e "      ${DIM}The MP Harmony Agent is a separate project: an autonomous engineering${NC}"
    echo -e "      ${DIM}assistant and voice bridge for this manager (port 11435).${NC}"
    echo -e "      ${DIM}It is installed outside this folder, in ~/OpenAI/MP_Harmony_Agent,${NC}"
    echo -e "      ${DIM}and is not required by any other feature.${NC}"
    read -r -p "      Install the MP Harmony Agent? [Y/n] " _mp_harmony_ans || _mp_harmony_ans=""
    case "${_mp_harmony_ans:-y}" in
        n|N|no|NO) MP_HARMONY_WANT=0 ;;
        *)         MP_HARMONY_WANT=1 ;;
    esac
    unset _mp_harmony_ans
fi

if [ "$MP_HARMONY_CHOICE" = "never" ] || [ "$MP_HARMONY_WANT" -eq 0 ]; then
    if [ "$MP_HARMONY_CHOICE" != "never" ]; then
        echo -e "${DIM}      • Skipped. Install it any time with:${NC}"
        echo -e "${DIM}        scripts/install_harmony_agent.sh install${NC}"
    fi
elif [ ! -f "$MP_HARMONY_INSTALLER" ]; then
    echo -e "${YELLOW}      ⚠ scripts/install_harmony_agent.sh is missing; the agent was not installed.${NC}"
elif ! command -v git >/dev/null 2>&1; then
    echo -e "${YELLOW}      ⚠ git is required to install the agent and was not found.${NC}"
    echo -e "${DIM}        Install git, then run: scripts/install_harmony_agent.sh install${NC}"
else
    if bash "$MP_HARMONY_INSTALLER" install; then
        # Only register the user unit on a host that actually has systemd and
        # only when the user opted in: an unrequested daemon that restarts
        # forever is worse than no daemon.
        if [ "$HOST_OS" = "linux" ] && command -v systemctl >/dev/null 2>&1; then
            bash "$MP_HARMONY_INSTALLER" unit >/dev/null 2>&1 || true
            echo -e "${DIM}        Autostart: systemd --user mp-harmony-agent.service${NC}"
            echo -e "${DIM}        Enable it with: systemctl --user enable --now mp-harmony-agent.service${NC}"
        fi
    else
        echo -e "${YELLOW}      ⚠ The MP Harmony Agent could not be installed.${NC}"
        # This is step 8: the app selection, desktop shortcuts and tray still
        # run afterwards, so do not claim the whole install already finished.
        echo -e "${DIM}        The rest of the install continues. Retry the agent any time with:${NC}"
        echo -e "${DIM}        scripts/install_harmony_agent.sh install${NC}"
    fi
fi
unset MP_HARMONY_CHOICE MP_HARMONY_INSTALLER
echo ""

# 9. Which MP applications this install sets up
#
# The tree is one folder, so every script is always present. What changes is
# what the user is given to launch: the Desktop shortcuts, the application-menu
# entries, the shared tray menu and the Web Companion launch pad only carry the
# apps chosen here. That is what "install only MP Gui" means in practice, and
# it is recorded in ~/.config/mix-manager/installed_apps so every later launch
# (and a tray started at login) agrees.
#
# MP_INSTALL_APPS selects without a prompt:
#   all | manager | gui | liquid | visualizer | gui,liquid | ...
# An unset value asks; a non-interactive run takes "all".
echo -e "${BOLD}${BLUE}[9/10] Choosing the MP Applications to Install...${NC}"
MP_APPS_CHOICE="${MP_INSTALL_APPS:-}"
MP_APPS_SELECTION="manager gui liquid visualizer"
MP_APPS_INTERACTIVE=0
if [ -n "$MP_APPS_CHOICE" ]; then
    MP_APPS_INTERACTIVE=1
elif [ ! -t 0 ]; then
    echo -e "${DIM}      • Non-interactive run: installing all four MP applications.${NC}"
else
    MP_APPS_INTERACTIVE=1
fi

if [ "$MP_APPS_INTERACTIVE" -eq 1 ] && [ -z "$MP_APPS_CHOICE" ]; then
    echo "      Which applications should this install set up?"
    echo ""
    echo -e "        ${BOLD}1)${NC} All four  ${DIM}MP Mix Manager, MP Gui, MP Liquid Player, MP Liquid Visualizer${NC}"
    echo -e "        ${BOLD}2)${NC} MP Mix Manager only        ${DIM}(the terminal manager)${NC}"
    echo -e "        ${BOLD}3)${NC} MP Gui only                ${DIM}(the Liquid Glass manager window)${NC}"
    echo -e "        ${BOLD}4)${NC} MP Liquid Player only      ${DIM}(the audio player + visualizer host)${NC}"
    echo -e "        ${BOLD}5)${NC} MP Liquid Visualizer only  ${DIM}(the standalone 3D visualizer)${NC}"
    echo -e "        ${BOLD}6)${NC} Choose several            ${DIM}(pick more than one)${NC}"
    echo ""
    read -r -p "      Select [1]: " _mp_apps_ans || true
    case "${_mp_apps_ans:-1}" in
        2) MP_APPS_CHOICE="manager" ;;
        3) MP_APPS_CHOICE="gui" ;;
        4) MP_APPS_CHOICE="liquid" ;;
        5) MP_APPS_CHOICE="visualizer" ;;
        6)
            echo ""
            echo -e "        ${DIM}Enter the numbers you want, separated by spaces, e.g.${NC} 2 3"
            echo -e "        ${DIM}  1)${NC} MP Mix Manager   ${DIM}2)${NC} MP Gui   ${DIM}3)${NC} MP Liquid Player   ${DIM}4)${NC} MP Liquid Visualizer"
            read -r -p "      Numbers: " _mp_apps_multi || true
            _mp_apps_picked=""
            for _mp_app_num in $_mp_apps_multi; do
                case "$_mp_app_num" in
                    1) _mp_apps_picked="$_mp_apps_picked manager" ;;
                    2) _mp_apps_picked="$_mp_apps_picked gui" ;;
                    3) _mp_apps_picked="$_mp_apps_picked liquid" ;;
                    4) _mp_apps_picked="$_mp_apps_picked visualizer" ;;
                esac
            done
            # No valid number means the safe default, not an empty install.
            MP_APPS_CHOICE="${_mp_apps_picked:-all}"
            unset _mp_apps_picked _mp_app_num _mp_apps_multi
            ;;
        *) MP_APPS_CHOICE="all" ;;
    esac
    unset _mp_apps_ans
fi
MP_APPS_CHOICE="${MP_APPS_CHOICE:-all}"

# Normalise through the same parser the rest of the suite uses, so "gui,liquid",
# "MP Gui" and "3" cannot drift apart between the installer and the tray.
if [ -n "$MP_PYTHON" ] && [ -f "$SCRIPT_DIR/scripts/mp_app_selection.py" ]; then
    _mp_apps_norm="$("$MP_PYTHON" "$SCRIPT_DIR/scripts/mp_app_selection.py" --set "$MP_APPS_CHOICE" 2>/dev/null || true)"
    [ -n "$_mp_apps_norm" ] && MP_APPS_SELECTION="$_mp_apps_norm"
    unset _mp_apps_norm
else
    # No interpreter: parse in bash, but never accept an empty or unknown set.
    # The old fallback passed the raw text through, so `--apps nonsense` produced
    # a selection no shortcut installer understood and installed nothing.
    case "$MP_APPS_CHOICE" in
        all|"")
            MP_APPS_SELECTION="manager gui liquid visualizer"
            ;;
        *)
            _mp_apps_parsed=""
            for _mp_token in $(printf '%s' "$MP_APPS_CHOICE" | tr ',;' '  '); do
                case "$(printf '%s' "$_mp_token" | tr '[:upper:]' '[:lower:]')" in
                    manager|mp-mix-manager|mp_mix_manager) _mp_apps_parsed="$_mp_apps_parsed manager" ;;
                    gui|mp-gui|mp_gui)                     _mp_apps_parsed="$_mp_apps_parsed gui" ;;
                    liquid|player|mp-liquid-player)        _mp_apps_parsed="$_mp_apps_parsed liquid" ;;
                    visualizer|mp-liquid-visualizer)       _mp_apps_parsed="$_mp_apps_parsed visualizer" ;;
                esac
            done
            unset _mp_token
            # Empty means the value named nothing valid; fall back to all four,
            # matching the Python parser (an empty install is never intended).
            MP_APPS_SELECTION="${_mp_apps_parsed:-manager gui liquid visualizer}"
            # Rebuild in the canonical order, which is what the parser stores.
            _mp_apps_ordered=""
            for _mp_want in manager gui liquid visualizer; do
                case " $MP_APPS_SELECTION " in
                    *" $_mp_want "*) _mp_apps_ordered="$_mp_apps_ordered $_mp_want" ;;
                esac
            done
            unset _mp_want
            MP_APPS_SELECTION="${_mp_apps_ordered# }"
            unset _mp_apps_parsed _mp_apps_ordered
            ;;
    esac
    # Record the choice here too. Previously only the step-10 shortcut installer
    # wrote this file, so a failure there silently lost the selection (the tray
    # and the phone launch pad reverted to all four).
    _mp_apps_state="$HOME/.config/mix-manager"
    mkdir -p "$_mp_apps_state" 2>/dev/null || true
    printf '%s\n' "$MP_APPS_SELECTION" > "$_mp_apps_state/installed_apps" 2>/dev/null || true
    unset _mp_apps_state
fi
export MP_APPS_SELECTION

_mp_apps_count=0
_mp_apps_headline=""
for _mp_app in $MP_APPS_SELECTION; do
    _mp_apps_count=$((_mp_apps_count + 1))
    case "$_mp_app" in
        manager)    _mp_apps_headline="$_mp_apps_headline|MP Mix Manager" ;;
        gui)        _mp_apps_headline="$_mp_apps_headline|MP Gui" ;;
        liquid)     _mp_apps_headline="$_mp_apps_headline|MP Liquid Player" ;;
        visualizer) _mp_apps_headline="$_mp_apps_headline|MP Liquid Visualizer" ;;
    esac
done
_mp_apps_headline="${_mp_apps_headline#|}"
_mp_apps_headline="${_mp_apps_headline//|/, }"
MP_APPS_LABELS="$_mp_apps_headline"
export MP_APPS_LABELS
if [ "$_mp_apps_count" -eq 4 ]; then
    echo -e "${GREEN}      ✓ Installing all four: ${CYAN}${_mp_apps_headline}${NC}"
elif [ "$_mp_apps_count" -eq 1 ]; then
    echo -e "${GREEN}      ✓ Installing one application: ${CYAN}${_mp_apps_headline}${NC}"
else
    echo -e "${GREEN}      ✓ Installing ${_mp_apps_count} applications: ${CYAN}${_mp_apps_headline}${NC}"
fi
echo -e "${DIM}        Selection saved to ~/.config/mix-manager/installed_apps${NC}"
if [ "$(uname -s 2>/dev/null)" = "Linux" ]; then
    echo -e "${DIM}        The Linux MP Liquid Mixer shortcut is added beside the selection.${NC}"
fi
echo -e "${DIM}        Change it later: ./install.sh --apps all      (or --apps gui)${NC}"
unset _mp_apps_count _mp_app _mp_apps_ans
echo ""

# 10. Desktop Integration
echo -e "${BOLD}${BLUE}[10/10] Installing Desktop Integration...${NC}"
if [ "$HOST_OS" = "windows" ] || [ "$HOST_OS" = "wsl" ]; then
    # python3 on a normal Windows install is the Microsoft Store alias, not an
    # interpreter. Shortcuts and the tray are written into the signed-in user's
    # profile (Desktop, Start Menu, and Startup) by PowerShell.
    if bash "$SCRIPT_DIR/scripts/install_windows_user.sh" "$SCRIPT_DIR" --components="$MP_APPS_SELECTION"; then
        echo -e "${GREEN}      ✓ Desktop shortcuts are on this user profile: ${MP_APPS_LABELS:-MP Mix Manager}${NC}"
        echo -e "${GREEN}      ✓ System tray icon installed and started.${NC}"
    else
        echo -e "${YELLOW}      ⚠ Desktop shortcuts or the system tray icon could not be installed for this user.${NC}"
    fi
elif [ -n "$MP_PYTHON" ]; then
    if "$MP_PYTHON" "$SCRIPT_DIR/scripts/install_desktop_shortcuts.py" --root "$SCRIPT_DIR" --components "$MP_APPS_SELECTION"; then
        echo -e "${GREEN}      ✓ Desktop shortcuts: ${MP_APPS_LABELS:-MP Mix Manager}${NC}"
        if [ "$(uname -s 2>/dev/null)" = "Linux" ]; then
            echo -e "${GREEN}      ✓ MP Liquid Mixer shortcut installed.${NC}"
        fi
    else
        echo -e "${YELLOW}      ⚠ Desktop shortcuts could not be created.${NC}"
    fi
    if "$MP_PYTHON" "$SCRIPT_DIR/scripts/mp_system_tray.py" --install; then
        echo -e "${GREEN}      ✓ System tray icon installed and started.${NC}"
    else
        echo -e "${YELLOW}      ⚠ System tray icon could not be started.${NC}"
    fi
else
    echo -e "${YELLOW}      ⚠ python3 is required to create the desktop shortcuts. Install Python 3.8+ and re-run this installer.${NC}"
fi
if [ "$HOST_OS" = "macos" ] || [ "$HOST_OS" = "windows" ] || [ "$HOST_OS" = "wsl" ]; then
    :
else
    # Linux Desktop Entries (.desktop)
    mkdir -p "$HOME/.local/share/applications"
    # The desktop entries below ship in this tree. Keep the list to files that
    # actually exist: the Python shortcut installer already writes MP-specific
    # entries, so these are the extras.
    for _dentry in "wan2gp-start-profile45.desktop" "wan2gp-stop.desktop" "ollama-serve.desktop" "ollama-stop.desktop" "beszel-hub-and-agent.desktop" "aider-qwen.desktop"; do
        if [ -f "$SCRIPT_DIR/desktop/$_dentry" ]; then
            cp -p "$SCRIPT_DIR/desktop/$_dentry" "$HOME/.local/share/applications/$_dentry"
            if [ -d "$HOME/Desktop" ]; then
                cp -p "$SCRIPT_DIR/desktop/$_dentry" "$HOME/Desktop/$_dentry"
                chmod +x "$HOME/Desktop/$_dentry" 2>/dev/null || true
            fi
        fi
    done
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo -e "${GREEN}      ✓ Application, WAN2GP, Ollama and Beszel shortcuts installed.${NC}"

    # MP Harmony and MP Reporter are separate programs, so their shortcuts are
    # owned by their own installer. Offer them here only when the user asked
    # for the agent, so a plain manager install is unchanged.
    if [ "${MP_HARMONY_WANT:-0}" -eq 1 ] && [ -f "$SCRIPT_DIR/scripts/install_harmony_desktop_shortcuts.sh" ]; then
        if bash "$SCRIPT_DIR/scripts/install_harmony_desktop_shortcuts.sh" >/dev/null 2>&1; then
            echo -e "${GREEN}      ✓ MP Harmony and MP Reporter desktop shortcuts installed.${NC}"
        else
            echo -e "${YELLOW}      ⚠ The MP Harmony/Reporter shortcuts could not be created.${NC}"
            echo -e "${DIM}        Retry with: scripts/install_harmony_desktop_shortcuts.sh${NC}"
        fi
    fi

    # Sync KDE Connect command scripts if directory exists
    if [ -d "$HOME/KDE_CONNECT_CMDS" ]; then
        cp -p "$SCRIPT_DIR/scripts/kc_start_wan2gp_profile45.sh" "$HOME/KDE_CONNECT_CMDS/kc_start_wan2gp_profile45.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_stop_wan2gp.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_wan2gp.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_run_ollama_serve.sh" "$HOME/KDE_CONNECT_CMDS/kc_run_ollama_serve.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_stop_ollama.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_ollama.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_launch_beszel_hub_and_agent.sh" "$HOME/KDE_CONNECT_CMDS/kc_launch_beszel_hub_and_agent.sh" 2>/dev/null || true
        chmod +x "$HOME/KDE_CONNECT_CMDS/kc_start_wan2gp_profile45.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_wan2gp.sh" "$HOME/KDE_CONNECT_CMDS/kc_run_ollama_serve.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_ollama.sh" "$HOME/KDE_CONNECT_CMDS/kc_launch_beszel_hub_and_agent.sh" 2>/dev/null || true
        echo -e "${GREEN}      ✓ Synced KDE Connect scripts to ~/KDE_CONNECT_CMDS.${NC}"
    fi
fi

unset MP_HARMONY_WANT 2>/dev/null || true

# 10. Final verification
#
# An installer that ends with a green banner while something is broken is the
# worst kind: the user has no idea what to fix. Ask the dependency engine for a
# verdict, say plainly whether the app can start, and exit non-zero when a
# required piece is missing so scripts and support can tell the difference.
echo -e "${BOLD}${BLUE}[check] Verifying the installation...${NC}"
MP_VERIFIED=0
MP_VERIFY_OUT=""
if [ -f "$MP_DEPS" ] && [ -n "$MP_PYTHON" ]; then
    set +e
    # Keep the engine's report. It used to be discarded with >/dev/null while the
    # failure text below told the user to read "the list directly above" — a list
    # that had never been printed.
    MP_VERIFY_OUT="$("$MP_PYTHON" "$MP_DEPS" --verify --python "$MP_PYTHON" 2>&1)"
    MP_FINAL_RC=$?
    set -e
    [ "$MP_FINAL_RC" -eq 0 ] && MP_VERIFIED=1
fi

if [ "$MP_VERIFIED" -ne 1 ]; then
    echo -e "${RED}      ✗ This install is not ready to run yet.${NC}"
    if [ -z "$MP_PYTHON" ]; then
        echo -e "${YELLOW}        Python 3.8+ is required before anything can start.${NC}"
        case "$HOST_OS" in
            macos)       _mp_fix="brew install python3" ;;
            windows|wsl) _mp_fix="winget install Python.Python.3.13" ;;
            freebsd)     _mp_fix="sudo pkg install python3" ;;
            *)           _mp_fix="sudo dnf install python3" ;;
        esac
        echo -e "${CYAN}        $_mp_fix${NC}"
        unset _mp_fix
    else
        # Print what the engine actually said, then the next step.
        if [ -n "$MP_VERIFY_OUT" ]; then
            printf '%s\n' "$MP_VERIFY_OUT" | sed 's/^/        /'
            echo ""
        fi
        echo -e "${DIM}        The report above names each missing dependency and the${NC}"
        echo -e "${DIM}        command that installs it. Nothing else needs reinstalling.${NC}"
    fi
    echo -e "${DIM}        Everything else is installed. Re-run ./install.sh when fixed.${NC}"
    echo ""
    exit 1
fi
unset MP_VERIFY_OUT 2>/dev/null || true

echo -e "${GREEN}      ✓ All required dependencies are present.${NC}"
echo -e "${GREEN}      ✓ MP Mix Manager is ready to launch.${NC}"
echo ""
echo -e "${BOLD}${GREEN}======================================================================${NC}"
echo -e "${BOLD}${GREEN}              MP MIX ARCHIVE MANAGER INSTALLATION COMPLETE!              ${NC}"
echo -e "${BOLD}${GREEN}======================================================================${NC}"
echo ""
echo -e "Installed applications:"
for _mp_app in $MP_APPS_SELECTION; do
    case "$_mp_app" in
        manager)    echo -e "  ${BOLD}${CYAN}MP Mix Manager${NC}" ;;
        gui)        echo -e "  ${BOLD}${CYAN}MP Gui${NC}" ;;
        liquid)     echo -e "  ${BOLD}${CYAN}MP Liquid Player${NC}" ;;
        visualizer) echo -e "  ${BOLD}${CYAN}MP Liquid Visualizer${NC}" ;;
    esac
done
echo ""
echo -e "Desktop shortcuts:"
for _mp_app in $MP_APPS_SELECTION; do
    case "$_mp_app" in
        manager)    echo -e "  ${BOLD}${CYAN}MP Mix Manager${NC}" ;;
        gui)        echo -e "  ${BOLD}${CYAN}MP Gui${NC}" ;;
        liquid)     echo -e "  ${BOLD}${CYAN}MP Liquid Player${NC}" ;;
        visualizer) echo -e "  ${BOLD}${CYAN}MP Liquid Visualizer${NC}" ;;
    esac
done
echo -e "  ${BOLD}${CYAN}MP Liquid Player VR Mode${NC} (Windows + Bigscreen, or the VR launcher)"
echo ""
echo -e "${DIM}Only the applications selected in step 9 have shortcuts and tray-menu${NC}"
echo -e "${DIM}entries. Add the rest later with: ./install.sh --apps all${NC}"
echo ""
# Point at the manual here as well: someone who skipped the offer at step 3
# still needs to know where it lives, and a TTY user has no window to open it in.
if [ -f "$SCRIPT_DIR/README.md" ]; then
    echo -e "Manual:"
    echo -e "  ${BOLD}${CYAN}$SCRIPT_DIR/README.md${NC}"
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
        echo -e "  ${DIM}Read it now with: xdg-open \"$SCRIPT_DIR/README.md\"${NC}"
    else
        echo -e "  ${DIM}Read it now with: less \"$SCRIPT_DIR/README.md\"${NC}"
    fi
    echo ""
fi
if [ "$HOST_OS" != "windows" ] && [ "$HOST_OS" != "wsl" ] && [ "${MP_INSTALL_COMPANION:-auto}" != "never" ]; then
    echo -e "Studio & DJ Booth Web Companion (phone / tablet dashboard):"
    echo -e "  ${BOLD}${CYAN}http://<this-machine-ip>:8888${NC}  (scripts/mix_web_companion.sh status)"
    echo ""
fi
if [ "$HOST_OS" = "macos" ]; then
    echo -e "Menu bar: the MP Mix Manager icon is on the right side of the menu bar. Click it to launch MP Gui, MP Liquid Player, or MP Mix Manager."
else
    echo -e "System tray: right-click the MP Mix Manager icon to launch MP Gui, MP Liquid Player, or MP Mix Manager."
fi
echo ""
echo -e "You can also launch MP Mix Manager:"
if [ "$HOST_OS" = "macos" ]; then
    echo -e "  1. Double-click:                  ${BOLD}${CYAN}~/Desktop/MP Mix Manager.command${NC}"
    echo -e "  2. Terminal command:              ${BOLD}${CYAN}manager${NC} or ${BOLD}${CYAN}~/manager.sh${NC}"
    echo -e "  3. Launch directly from source:   ${BOLD}${CYAN}cd $SCRIPT_DIR && ./manager_macos.command${NC}"
elif [ "$HOST_OS" = "windows" ] || [ "$HOST_OS" = "wsl" ]; then
    echo -e "  1. Double-click on Windows:       ${BOLD}${CYAN}MP Mix Manager${NC} on the Desktop"
    echo -e "  2. Run inside Git Bash / MSYS2:   ${BOLD}${CYAN}./Mix_Archive_Manager.sh${NC}"
    echo -e "  3. Run inside WSL2:               ${BOLD}${CYAN}./Mix_Archive_Manager.sh${NC}"
else
    echo -e "  1. Run from anywhere in terminal: ${BOLD}${CYAN}manager${NC} or ${BOLD}${CYAN}~/manager.sh${NC}"
    echo -e "  2. Launch directly from source:   ${BOLD}${CYAN}cd $SCRIPT_DIR && ./Mix_Archive_Manager.sh${NC}"
    echo -e "  3. Open from Desktop or Menu:     ${BOLD}${CYAN}MP Mix Manager${NC}"
fi
echo ""
exit 0
