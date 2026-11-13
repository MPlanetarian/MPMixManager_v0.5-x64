#!/usr/bin/env bash
# ==============================================================================
# MP Mix Manager - Shared configuration helpers
#
# Sourced by the manager and by standalone tools. Keeps the one piece of logic
# that every feature depends on - "where is the archive, and what other folders
# belong to it" - in exactly one place.
#
# Why this file exists:
#   * EXTRA_MIX_ARCHIVE_DIRS was split with IFS=':,;' in two places. A Windows
#     entry such as `D:/Mixes` became `D` + `/Mixes`, so every extra folder on
#     Windows silently disappeared. The splitter below is drive-letter aware.
#   * Seven different scripts searched for config.env in seven different orders,
#     one of which included $PWD. That made the active configuration depend on
#     the directory a tool happened to be launched from.
#
# Sourcing this file is safe: it defines functions only.
# ==============================================================================

# Guard against double-sourcing.
[ -n "${_MP_CONFIG_SH_LOADED:-}" ] && return 0
_MP_CONFIG_SH_LOADED=1

# ------------------------------------------------------------------------------
# CONFIGURATION FILE DISCOVERY
# ------------------------------------------------------------------------------

# Print the config.env that should be used, or nothing when none exists.
# Precedence: $MP_CONFIG, then the per-user copy, then the install tree.
# $PWD is deliberately absent: a tool run from inside an archive must not pick
# up a different configuration just because a file happened to be there.
mp_config_path() {
    local script_dir="${1:-${SCRIPT_DIR:-}}"
    local candidate
    if [ -n "${MP_CONFIG:-}" ] && [ -f "$MP_CONFIG" ]; then
        printf '%s\n' "$MP_CONFIG"
        return 0
    fi
    for candidate in \
        "${XDG_CONFIG_HOME:-$HOME/.config}/mix-manager/config.env" \
        "$script_dir/config.env"
    do
        [ -f "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    return 1
}

# Read one KEY from a config file without executing it. `source` on a
# user-editable file runs any `$(...)` it contains, which turns a stray edit
# (or a crafted path) into code execution.
mp_config_get() {
    local key="$1" file="${2:-}" default="${3:-}"
    [ -n "$file" ] && [ -f "$file" ] || return 1
    local line value
    line="$(grep -m1 -E "^[[:space:]]*${key}=" "$file" 2>/dev/null || true)"
    [ -n "$line" ] || { printf '%s' "$default"; return 1; }
    value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    printf '%s' "${value:-$default}"
    return 0
}

# ------------------------------------------------------------------------------
# ARCHIVE DIRECTORY LIST
# ------------------------------------------------------------------------------

# Split EXTRA_MIX_ARCHIVE_DIRS (or the legacy MIX_ARCHIVE_DIRS) into one entry
# per line, preserving Windows drive letters.
#
# The separator is inferred, because a `:` is both a list separator on Unix and
# part of `D:\Mixes` on Windows:
#   * `;`, `,` or a newline present  -> split on those (the documented form)
#   * `:[A-Z]:` anywhere             -> split on `;` only, after repairing the
#                                       joined form back to `;`
#   * a single drive letter and more than one `:` -> split on `:`
#   * otherwise                      -> one entry
mp_split_extra_dirs() {
    local raw="${1:-}"
    [ -n "$raw" ] || return 0

    local -a parts=()
    if [[ "$raw" == *";"* || "$raw" == *","* || "$raw" == *$'\n'* ]]; then
        IFS=';,'$'\n' read -ra parts <<< "$raw"
    elif [[ "$raw" =~ :[a-zA-Z]: ]]; then
        # Two Windows drives separated by the wrong character: repair it.
        local repaired
        repaired="$(printf '%s' "$raw" | sed -E 's/:([a-zA-Z]:)/;\1/g')"
        IFS=';' read -ra parts <<< "$repaired"
    elif [[ ! "$raw" =~ ^[a-zA-Z]: && "$raw" == *":"* ]]; then
        IFS=':' read -ra parts <<< "$raw"
    else
        parts=("$raw")
    fi

    local part
    for part in "${parts[@]+"${parts[@]}"}"; do
        # Trim surrounding whitespace and quotes.
        part="${part#"${part%%[![:space:]]*}"}"
        part="${part%"${part##*[![:space:]]}"}"
        part="${part%\"}"; part="${part#\"}"
        part="${part%\'}"; part="${part#\'}"
        [ -n "$part" ] || continue
        # Normalise Windows separators, but keep the drive colon.
        part="${part//\\//}"
        # Drop a trailing slash unless it is a bare drive root or `/`.
        if [[ ! "$part" =~ ^[a-zA-Z]:/$ && "$part" != "/" ]]; then
            part="${part%/}"
        fi
        [ -n "$part" ] && printf '%s\n' "$part"
    done
}

# Print every configured archive folder, one per line, primary first.
# Empty results are dropped so a caller can `while IFS= read -r`.
mp_all_archive_dirs() {
    local base="${1:-${SCRIPT_DIR:-}}"
    local primary="${MIX_ARCHIVE_DIR:-}"
    local extras="${EXTRA_MIX_ARCHIVE_DIRS:-${MIX_ARCHIVE_DIRS:-}}"

    if [ -z "$primary" ] && [ -n "$base" ]; then
        primary="$base/MIX_ARCHIVE"
    fi
    [ -n "$primary" ] && printf '%s\n' "${primary//\\//}"

    mp_split_extra_dirs "$extras"
    return 0
}

# ------------------------------------------------------------------------------
# SAFETY
# ------------------------------------------------------------------------------

# Create a config file that only the owner can read. Called after writing a
# credential so a path is never world-readable even for a moment.
mp_secure_config() {
    local file="$1"
    [ -f "$file" ] || return 0
    chmod 600 "$file" 2>/dev/null || true
}

# Quote a value for a `KEY="value"` config line. A password that contains a
# quote, a `$` or a backtick otherwise corrupts the file or (on the next
# `source`) executes.
mp_config_quote() {
    local value="${1:-}"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\$/\\\$}"
    value="${value//\`/\\\`}"
    printf '"%s"' "$value"
}
