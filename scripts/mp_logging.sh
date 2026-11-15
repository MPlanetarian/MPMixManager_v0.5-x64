#!/usr/bin/env bash
# mp_logging.sh - Shell Error and Event Logger for MP Mix Archive Manager
# Part of MP_Mix_Manager_v0.3
#
# Every physical line of an entry carries its own [YYYY-MM-DD HH:MM:SS] stamp,
# matching scripts/mp_logging.py:
#
#   [2026-11-14 00:38:16] [ERROR] [run_sub_script] Script 'x.sh' failed with exit code 1
#   [2026-11-14 00:38:16]   Details: Args: update
#   ----------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$HOME/.config/mix-manager"
mkdir -p "$CONFIG_DIR" 2>/dev/null || true

MP_ERROR_LOG="${SCRIPT_DIR}/MP_Error.log"
BACKUP_ERROR_LOG="${CONFIG_DIR}/MP_Error.log"

# Repeats of the same component+message inside this many seconds collapse into
# one entry. Off by default here: shell errors come from deliberate menu
# actions, and a user who retries a failing update three times should see three
# failures. The Python logger keeps its window because a background retry loop
# can emit the same failure thousands of times per second.
MP_ERROR_DEDUPE_SECONDS="${MP_ERROR_DEDUPE_SECONDS:-0}"
_MP_DEDUPE_DIR="${TMPDIR:-/tmp}/mp-error-dedupe.$(id -u 2>/dev/null || echo 0)"

get_mp_error_log() {
    if touch "$MP_ERROR_LOG" 2>/dev/null; then
        echo "$MP_ERROR_LOG"
    else
        touch "$BACKUP_ERROR_LOG" 2>/dev/null || true
        echo "$BACKUP_ERROR_LOG"
    fi
}

_mp_stamp() { date '+%Y-%m-%d %H:%M:%S'; }

# Returns 0 when a duplicate should be suppressed, 1 when it should be logged.
_mp_is_duplicate() {
    local key="$1"
    [ "${MP_ERROR_DEDUPE_SECONDS:-0}" -gt 0 ] 2>/dev/null || return 1
    command -v cksum >/dev/null 2>&1 || return 1
    mkdir -p "$_MP_DEDUPE_DIR" 2>/dev/null || return 1
    local id now prev marker
    id=$(printf '%s' "$key" | cksum 2>/dev/null | awk '{print $1}')
    [ -z "$id" ] && return 1
    marker="${_MP_DEDUPE_DIR}/${id}"
    now=$(date +%s 2>/dev/null || echo 0)
    if [ -f "$marker" ]; then
        prev=$(cat "$marker" 2>/dev/null || echo 0)
        if [ $((now - prev)) -lt "$MP_ERROR_DEDUPE_SECONDS" ] 2>/dev/null; then
            return 0
        fi
    fi
    # Expire the marker by itself. Left behind, it would suppress the next
    # identical error for its window even in a later, unrelated run.
    printf '%s' "$now" > "$marker" 2>/dev/null || true
    return 1
}

log_mp_error() {
    local comp="${1:-MixManager}"
    local msg="${2:-Unspecified error occurred}"
    local details="${3:-}"
    local log_file
    log_file="$(get_mp_error_log)"

    _mp_is_duplicate "E|${comp}|${msg}" && return 0

    local stamp
    stamp="$(_mp_stamp)"
    if [ -n "$details" ]; then
        {
            printf '[%s] [ERROR] [%s] %s\n' "$stamp" "$comp" "$msg"
            printf '[%s]   Details: %s\n' "$stamp" "$details"
            printf '%s\n' "----------------------------------------------------------------------"
        } >> "$log_file" 2>/dev/null || true
    else
        {
            printf '[%s] [ERROR] [%s] %s\n' "$stamp" "$comp" "$msg"
            printf '%s\n' "----------------------------------------------------------------------"
        } >> "$log_file" 2>/dev/null || true
    fi
}

log_mp_warning() {
    local comp="${1:-MixManager}"
    local msg="${2:-Warning}"
    local log_file
    log_file="$(get_mp_error_log)"

    _mp_is_duplicate "W|${comp}|${msg}" && return 0

    local stamp
    stamp="$(_mp_stamp)"
    {
        printf '[%s] [WARNING] [%s] %s\n' "$stamp" "$comp" "$msg"
        printf '%s\n' "----------------------------------------------------------------------"
    } >> "$log_file" 2>/dev/null || true
}

view_mp_errors() {
    local log_file
    log_file="$(get_mp_error_log)"
    if [ ! -s "$log_file" ]; then
        echo -e "\033[32m✔ No errors logged in MP_Error.log.\033[0m"
        return 0
    fi
    echo -e "\033[1;35m======================================================================\033[0m"
    echo -e "\033[1;35m                    MP MIX MANAGER ERROR LOG                          \033[0m"
    echo -e "\033[1;35m======================================================================\033[0m"
    echo -e "\033[2mFile: $log_file\033[0m\n"
    tail -n 40 "$log_file"
}

# If run directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        --tail|tail)
            view_mp_errors
            ;;
        --log)
            shift
            log_mp_error "$@"
            ;;
        *)
            echo "MP_Error.log is located at: $(get_mp_error_log)"
            ;;
    esac
fi
