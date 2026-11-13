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
        VERSION="0.5.0 Stable (Sensorium)"
    fi
fi
export VERSION

# 0. Optional Reinstall / Factory Reset
REINSTALL=0
REINSTALL_ARGS=()
for _arg in "$@"; do
    case "$_arg" in
        --reinstall|--re-install|--factory-reset|--reset)
            REINSTALL=1
            ;;
        --help|-h)
            echo -e "${BOLD}MP Mix Manager Installer${NC}"
            echo ""
            echo "Usage: ./install.sh [options] [-- reinstall options]"
            echo ""
            echo "Options:"
            echo "  --install                 Normal install (default)"
            echo "  --reinstall               Wipe this user's configuration and reinstall"
            echo "                            from a first-install state"
            echo "  --preserve-playlists      Reinstall: keep playlists and favorites"
            echo "  --preserve-music-folders  Reinstall: keep music folder config"
            echo "  --preserve-archive-config Reinstall: keep mix archive folder config"
            echo "  --fresh                   Reinstall: preserve nothing"
            echo "  --yes, -y                 Reinstall: do not prompt; preserve all"
            echo "  --dry-run                 Reinstall: show what would change"
            echo "  -h, --help                Show this help"
            echo ""
            echo "Converted audio files are never deleted by a reinstall."
            echo "Run ./scripts/reinstall_mp_manager.sh --help for the full option list."
            exit 0
            ;;
        --install)
            # Explicit normal install. Nothing to do; the script proceeds.
            ;;
        --preserve-playlists|--preserve-music-folders|--preserve-archive-config|\
        --no-preserve-playlists|--no-preserve-music-folders|--no-preserve-archive-config|\
        --keep-playlists|--keep-music-folders|--keep-archive-config|--no-preserve|\
        --fresh|--yes|-y|--dry-run)
            REINSTALL_ARGS+=("$_arg")
            ;;
        *)
            echo -e "${RED}Unknown option: $_arg${NC}" >&2
            echo "Run ./install.sh --help to see the available options." >&2
            exit 1
            ;;
    esac
done
unset _arg

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
echo -e "${BOLD}${BLUE}[1/7] Detecting Host Platform & Environment...${NC}"
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
                # shellcheck source=/dev/null
                source /etc/os-release
                echo -e "      Platform:     ${GREEN}Linux${NC} (${CYAN}${PRETTY_NAME:-Linux}${NC})"
                if [[ "${ID:-}" == *"bazzite"* ]] || [[ "${VARIANT_ID:-}" == *"bazzite"* ]] || [[ "${NAME:-}" == *"Bazzite"* ]]; then
                    echo -e "${GREEN}      ✓ Verified Bazzite Linux environment.${NC}"
                fi
            else
                echo -e "      Platform:     ${GREEN}Generic Linux${NC}"
            fi
        fi
        ;;
    *)
        HOST_OS="linux"
        echo -e "      Platform:     ${YELLOW}Unknown (${OS})${NC}"
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
            ;;
        linux)
            if [ -f /etc/os-release ]; then
                # shellcheck source=/dev/null
                . /etc/os-release
            fi
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

    if [ -n "$_mp_py_install" ]; then
        _do_py_install=0
        if [ "$MP_INSTALL_PYTHON" = "always" ]; then
            _do_py_install=1
        elif [ "$MP_INSTALL_PYTHON" != "never" ] && [ -t 0 ]; then
            read -r -p "      Install Python 3 now? [Y/n] " _mp_py_ans
            case "${_mp_py_ans:-y}" in
                n|N|no|NO) _do_py_install=0 ;;
                *) _do_py_install=1 ;;
            esac
            unset _mp_py_ans
        fi
        if [ "$_do_py_install" -eq 1 ]; then
            echo -e "${CYAN}      Installing Python 3...${NC}"
            set +e
            if [ "$(id -u 2>/dev/null || echo 1)" -ne 0 ] && ! printf '%s' "$_mp_py_install" | grep -qE '^(brew|winget)'; then
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
echo -e "${BOLD}${BLUE}[2/7] Configuring Script & Binary Permissions...${NC}"
chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.command "$SCRIPT_DIR"/bin/* 2>/dev/null || true
if [ -d "$SCRIPT_DIR/scripts" ]; then
    chmod +x "$SCRIPT_DIR"/scripts/*.sh "$SCRIPT_DIR"/scripts/*.py 2>/dev/null || true
fi
chmod +x "$SCRIPT_DIR"/bin/cliamp 2>/dev/null || true
echo -e "${GREEN}      ✓ Executable permissions granted across all scripts and tools.${NC}"
echo ""

# 3. Create Scaffold Working Directories
echo -e "${BOLD}${BLUE}[3/7] Setting Up Archive Directory Structure...${NC}"
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

# 4. Check & Install Dependencies
#
# One engine (scripts/mp_dependencies.py) owns the list of what the app needs,
# the package name on every distro, and how to install it. The installer just
# drives it, so the answers cannot drift from the code that imports them.
echo -e "${BOLD}${BLUE}[4/7] Checking & Installing Dependencies...${NC}"
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
    if [ -n "$MP_ENV_PYTHON" ] && "$MP_ENV_PYTHON" -c "import numpy" >/dev/null 2>&1; then
        MP_PYTHON="$MP_ENV_PYTHON"
        echo -e "${GREEN}      ✓ Python environment: ${CYAN}${MP_ENV_PYTHON}${NC}"
    fi
    unset _cand

    if [ "$MP_DEPS_RC" -ne 0 ]; then
        MP_DEPS_OK=0
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
                echo -e "${DIM}      Optional extras (spectrograms, cloud backup, PDF reports, tray,\n      YouTube downloads) are not required. Install them now?${NC}"
                read -r -p "      Install optional extras? [y/N] " _mp_opt
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
echo -e "${BOLD}${BLUE}[5/7] Checking Configuration File...${NC}"
if [ ! -f "$SCRIPT_DIR/config.env" ]; then
    if [ -f "$SCRIPT_DIR/config.env.example" ]; then
        cp "$SCRIPT_DIR/config.env.example" "$SCRIPT_DIR/config.env"
        echo -e "${GREEN}      ✓ Created default config.env from template.${NC}"
    fi
else
    echo -e "${GREEN}      ✓ Active config.env found.${NC}"
fi
echo ""

# 6. Install Global/Local User Symlinks
echo -e "${BOLD}${BLUE}[6/7] Installing Local User Symlinks & Launchers...${NC}"
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
link "$SCRIPT_DIR/bin/traktor-monitor" "traktor-monitor"
link "$SCRIPT_DIR/scripts/traktor_monitor.sh" "traktor_monitor.sh"
link "$SCRIPT_DIR/Cut_Video.sh" "cut-video"
link "$SCRIPT_DIR/Split_FLAC_File.sh" "split-flac"
link "$SCRIPT_DIR/Split_Video_File.sh" "split-video"
link "$SCRIPT_DIR/scripts/kc_start_wan2gp_profile45.sh" "wan2gp-start-profile45"
link "$SCRIPT_DIR/scripts/kc_stop_wan2gp.sh" "wan2gp-stop"
link "$SCRIPT_DIR/scripts/kc_run_ollama_serve.sh" "ollama-serve"
link "$SCRIPT_DIR/scripts/kc_stop_ollama.sh" "ollama-stop"
link "$SCRIPT_DIR/scripts/manage_ollama.sh" "manage-ollama"
link "$SCRIPT_DIR/scripts/kc_launch_beszel_hub_and_agent.sh" "beszel-hub-and-agent"
link "$SCRIPT_DIR/scripts/beszel.sh" "beszel"
link "$SCRIPT_DIR/scripts/dsh_mobile.sh" "dsh-mobile"
link "$SCRIPT_DIR/scripts/dsh_mobile.sh" "manage-dsh-mobile"
link "$SCRIPT_DIR/scripts/kc_run_aider_qwen.sh" "aider-qwen"
link "$SCRIPT_DIR/Mix_Archive_Manager.sh" "$HOME/manager.sh"

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
    cp -p "$SCRIPT_DIR/config.env" "$HOME/.config/mix-manager/config.env" 2>/dev/null || true
fi

echo -e "${GREEN}      ✓ Installed ${MP_LINKED} launchers into ~/.local/bin and ~/manager.sh.${NC}"
if [ ${#MP_LINK_SKIPPED[@]} -gt 0 ]; then
    echo -e "${DIM}      • not linked (not in this build): ${MP_LINK_SKIPPED[*]}${NC}"
fi
unset MP_LINKED MP_LINK_SKIPPED

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

# 7. Desktop Integration
echo -e "${BOLD}${BLUE}[7/7] Installing Desktop Integration...${NC}"
if [ "$HOST_OS" = "windows" ] || [ "$HOST_OS" = "wsl" ]; then
    # python3 on a normal Windows install is the Microsoft Store alias, not an
    # interpreter. Shortcuts and the tray are written into the signed-in user's
    # profile (Desktop, Start Menu, and Startup) by PowerShell.
    if bash "$SCRIPT_DIR/scripts/install_windows_user.sh" "$SCRIPT_DIR"; then
        echo -e "${GREEN}      ✓ Desktop shortcuts are on this user profile: MP Liquid Player, MP Mix Manager, MP Gui, MP Liquid Visualizer${NC}"
        echo -e "${GREEN}      ✓ System tray icon installed and started.${NC}"
    else
        echo -e "${YELLOW}      ⚠ Desktop shortcuts or the system tray icon could not be installed for this user.${NC}"
    fi
elif [ -n "$MP_PYTHON" ]; then
    if "$MP_PYTHON" "$SCRIPT_DIR/scripts/install_desktop_shortcuts.py" --root "$SCRIPT_DIR"; then
        echo -e "${GREEN}      ✓ Desktop shortcuts: MP Liquid Player, MP Mix Manager, MP Gui, MP Liquid Visualizer${NC}"
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
    for _dentry in "wan2gp-start-profile45.desktop" "wan2gp-stop.desktop" "ollama-serve.desktop" "ollama-stop.desktop" "beszel-hub-and-agent.desktop" "dsh-mobile.desktop"; do
        if [ -f "$SCRIPT_DIR/desktop/$_dentry" ]; then
            cp -p "$SCRIPT_DIR/desktop/$_dentry" "$HOME/.local/share/applications/$_dentry"
            if [ -d "$HOME/Desktop" ]; then
                cp -p "$SCRIPT_DIR/desktop/$_dentry" "$HOME/Desktop/$_dentry"
                chmod +x "$HOME/Desktop/$_dentry" 2>/dev/null || true
            fi
        fi
    done
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo -e "${GREEN}      ✓ Application, WAN2GP, Ollama, Beszel, and dsh-mobile shortcuts installed.${NC}"

    # Sync KDE Connect command scripts if directory exists
    if [ -d "$HOME/KDE_CONNECT_CMDS" ]; then
        cp -p "$SCRIPT_DIR/scripts/kc_start_wan2gp_profile45.sh" "$HOME/KDE_CONNECT_CMDS/kc_start_wan2gp_profile45.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_stop_wan2gp.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_wan2gp.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_run_ollama_serve.sh" "$HOME/KDE_CONNECT_CMDS/kc_run_ollama_serve.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_stop_ollama.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_ollama.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_launch_beszel_hub_and_agent.sh" "$HOME/KDE_CONNECT_CMDS/kc_launch_beszel_hub_and_agent.sh" 2>/dev/null || true
        cp -p "$SCRIPT_DIR/scripts/kc_launch_dsh_mobile.sh" "$HOME/KDE_CONNECT_CMDS/kc_launch_dsh_mobile.sh" 2>/dev/null || true
        chmod +x "$HOME/KDE_CONNECT_CMDS/kc_start_wan2gp_profile45.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_wan2gp.sh" "$HOME/KDE_CONNECT_CMDS/kc_run_ollama_serve.sh" "$HOME/KDE_CONNECT_CMDS/kc_stop_ollama.sh" "$HOME/KDE_CONNECT_CMDS/kc_launch_beszel_hub_and_agent.sh" "$HOME/KDE_CONNECT_CMDS/kc_launch_dsh_mobile.sh" 2>/dev/null || true
        echo -e "${GREEN}      ✓ Synced KDE Connect scripts to ~/KDE_CONNECT_CMDS.${NC}"
    fi
fi

# 8. Final verification
#
# An installer that ends with a green banner while something is broken is the
# worst kind: the user has no idea what to fix. Ask the dependency engine for a
# verdict, say plainly whether the app can start, and exit non-zero when a
# required piece is missing so scripts and support can tell the difference.
echo -e "${BOLD}${BLUE}[check] Verifying the installation...${NC}"
MP_VERIFIED=0
if [ -f "$MP_DEPS" ] && [ -n "$MP_PYTHON" ]; then
    set +e
    "$MP_PYTHON" "$MP_DEPS" --verify --python "$MP_PYTHON" >/dev/null 2>&1
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
        echo -e "${DIM}        The list directly above names each missing dependency and${NC}"
        echo -e "${DIM}        the command that installs it.${NC}"
    fi
    echo -e "${DIM}        Everything else is installed. Re-run ./install.sh when fixed.${NC}"
    echo ""
    exit 1
fi

echo -e "${GREEN}      ✓ All required dependencies are present.${NC}"
echo -e "${GREEN}      ✓ MP Mix Manager is ready to launch.${NC}"
echo ""
echo -e "${BOLD}${GREEN}======================================================================${NC}"
echo -e "${BOLD}${GREEN}              MP MIX ARCHIVE MANAGER INSTALLATION COMPLETE!              ${NC}"
echo -e "${BOLD}${GREEN}======================================================================${NC}"
echo ""
echo -e "Desktop shortcuts:"
echo -e "  ${BOLD}${CYAN}MP Liquid Player${NC}"
echo -e "  ${BOLD}${CYAN}MP Mix Manager${NC}"
echo -e "  ${BOLD}${CYAN}MP Gui${NC}"
echo -e "  ${BOLD}${CYAN}MP Liquid Visualizer${NC}"
echo ""
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
