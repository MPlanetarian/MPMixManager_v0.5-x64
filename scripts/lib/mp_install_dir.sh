#!/usr/bin/env bash
# ==============================================================================
# MP Mix Manager install directory resolver.
#
# Sourced by launchers and helpers so nothing has to hard-code
# /var/home/<user>/MPMixManager_vX.Y-arch, or assume a version in the name.
#
# Usage:
#   mp_find_install_dir [marker-file]     # prints the tree that holds marker-file
#   mp_install_dir_has <dir> <marker>     # 0 when dir holds marker
#
# Lookup order:
#   1. $MP_MIX_MANAGER_DIR
#   2. The target of the launcher symlinks install.sh creates in ~/.local/bin
#   3. The directory recorded in ~/.config/mix-manager/install_dir
#   4. Newest matching tree under $HOME, then /opt, then this script's parent
#
# Every candidate is verified with the marker file, so a stale symlink or a
# leftover backup directory cannot win.
# ==============================================================================

# Print the directory that holds a file, following symlinks.
mp_resolve_dir() {
    local src="$1" dir
    [ -n "$src" ] || return 1
    while [ -h "$src" ]; do
        dir="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)" || return 1
        src="$(readlink "$src")" || return 1
        case "$src" in
            /*) ;;
            *) src="$dir/$src" ;;
        esac
    done
    cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd
}

# 0 when this directory holds the marker file.
mp_install_dir_has() {
    local dir="$1" marker="${2:-Mix_Archive_Manager.sh}"
    [ -n "$dir" ] && [ -d "$dir" ] && [ -f "$dir/$marker" ]
}

# Print the tree that holds the marker, walking up from a starting directory.
mp_install_dir_from_path() {
    local dir="$1" marker="${2:-Mix_Archive_Manager.sh}" _i
    [ -n "$dir" ] || return 1
    dir="$(cd -P "$dir" >/dev/null 2>&1 && pwd)" || return 1
    for _i in 0 1 2 3; do
        mp_install_dir_has "$dir" "$marker" && { printf '%s\n' "$dir"; return 0; }
        [ "$dir" = "/" ] && break
        dir="$(dirname "$dir")"
    done
    return 1
}

# Print the newest tree under a base directory that holds the marker.
mp_install_dir_scan() {
    local base="$1" marker="$2" dir
    [ -d "$base" ] || return 1
    while IFS= read -r dir; do
        [ -n "$dir" ] || continue
        mp_install_dir_has "$dir" "$marker" && { printf '%s\n' "$dir"; return 0; }
    done < <(ls -d "$base"/MP*ix*anager* "$base"/MP*ix*anager*/* 2>/dev/null | sort -rV)
    return 1
}

# Print the install directory, or return 1.
mp_find_install_dir() {
    local marker="${1:-Mix_Archive_Manager.sh}" dir recorded

    # 1. Explicit override.
    mp_install_dir_from_path "${MP_MIX_MANAGER_DIR:-}" "$marker" && return 0

    # 2. Launcher symlinks in ~/.local/bin (and ~/manager.sh). These are what
    #    the `manager` command actually runs, so they win over a stale record.
    for dir in \
        "$HOME/.local/bin/mix-archive-manager" \
        "$HOME/.local/bin/manager" \
        "$HOME/.local/bin/launch-manager" \
        "$HOME/.local/bin/launch-liquid-glass" \
        "$HOME/manager.sh"; do
        [ -e "$dir" ] || continue
        mp_install_dir_from_path "$(mp_resolve_dir "$dir")" "$marker" && return 0
    done

    # 3. Directory recorded at install time.
    recorded="$HOME/.config/mix-manager/install_dir"
    if [ -f "$recorded" ]; then
        read -r dir < "$recorded" 2>/dev/null || dir=""
        mp_install_dir_from_path "$dir" "$marker" && return 0
    fi

    # 4. Newest matching tree. No version numbers or user names are assumed.
    mp_install_dir_scan "$HOME" "$marker" && return 0
    mp_install_dir_scan "/opt" "$marker" && return 0
    mp_install_dir_from_path "$(mp_resolve_dir "${BASH_SOURCE[0]}")" "$marker" && return 0
    return 1
}
