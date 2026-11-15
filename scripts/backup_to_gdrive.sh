#!/usr/bin/env bash
# ==============================================================================
# MP Mix Manager - Google Drive Backup Runner
#
# Thin wrapper around backup_mix_archive.sh. The previous version computed
# SCRIPT_DIR with `dirname "${BASH_SOURCE[0]}"`, which does not follow a
# symlink: invoked through ~/.local/bin/backup_to_gdrive it resolved to
# ~/.local/bin/scripts/backup_mix_archive.sh and failed. It also appended
# "not found" to every non-zero exit, so a real backup failure was reported as
# a missing file.
# ==============================================================================

set -uo pipefail

# Follow the symlink chain so this works from ~/.local/bin or a desktop entry.
_SELF="${BASH_SOURCE[0]}"
while [ -h "$_SELF" ]; do
    _DIR="$(cd -P "$(dirname "$_SELF")" >/dev/null 2>&1 && pwd)"
    _SELF="$(readlink "$_SELF")"
    case "$_SELF" in
        /*) ;;
        *) _SELF="$_DIR/$_SELF" ;;
    esac
done
SCRIPT_DIR="$(cd -P "$(dirname "$_SELF")" >/dev/null 2>&1 && pwd)"
unset _SELF _DIR

# Accept either layout: the tree root, or a scripts/ directory.
if [ -x "$SCRIPT_DIR/scripts/backup_mix_archive.sh" ]; then
    BACKUP_SUITE="$SCRIPT_DIR/scripts/backup_mix_archive.sh"
elif [ -x "$SCRIPT_DIR/backup_mix_archive.sh" ]; then
    BACKUP_SUITE="$SCRIPT_DIR/backup_mix_archive.sh"
else
    echo "Error: backup_mix_archive.sh not found next to $SCRIPT_DIR." >&2
    exit 1
fi

exec "$BACKUP_SUITE" --provider=gdrive --all "$@"
