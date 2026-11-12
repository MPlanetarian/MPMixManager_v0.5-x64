#!/usr/bin/env bash
# ==============================================================================
# MP_Mix_Manager_v0.3 - Audio & Video File Output Monitor Wrapper
# ==============================================================================
_RESOLVED_SRC="${BASH_SOURCE[0]}"
while [ -h "$_RESOLVED_SRC" ]; do
    _RESOLVED_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
    _RESOLVED_SRC="$(readlink "$_RESOLVED_SRC")"
    [[ $_RESOLVED_SRC != /* ]] && _RESOLVED_SRC="$_RESOLVED_DIR/$_RESOLVED_SRC"
done
SCRIPT_DIR="$(cd -P "$(dirname "$_RESOLVED_SRC")" >/dev/null 2>&1 && pwd)"
unset _RESOLVED_SRC _RESOLVED_DIR
for _cfg in "$SCRIPT_DIR/config.env" "$SCRIPT_DIR/../config.env" "$PWD/config.env"; do
    if [ -f "$_cfg" ]; then
        source "$_cfg" 2>/dev/null || true
        break
    fi
done

PY_SCRIPT="$SCRIPT_DIR/MP_Mix_Manager_Audio_File_Output_Monitor.py"
[ ! -f "$PY_SCRIPT" ] && PY_SCRIPT="$SCRIPT_DIR/scripts/MP_Mix_Manager_Audio_File_Output_Monitor.py"

exec python3 "$PY_SCRIPT" "$@"
