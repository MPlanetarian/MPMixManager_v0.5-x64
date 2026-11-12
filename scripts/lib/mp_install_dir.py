#!/usr/bin/env python3
"""Locate the MP Mix Manager install tree without hard-coding a path.

Mirrors scripts/lib/mp_install_dir.sh. Lookup order:

  1. $MP_MIX_MANAGER_DIR
  2. The target of the launcher symlinks install.sh creates in ~/.local/bin
  3. The directory recorded in ~/.config/mix-manager/install_dir
  4. Newest matching tree under $HOME, then /opt, then next to this file

Every candidate is checked for the marker file, so a stale symlink or a
leftover backup directory cannot win.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MARKER = "Mix_Archive_Manager.sh"
_CONFIG_DIR = Path.home() / ".config" / "mix-manager"

_STARTING_POINTS = (
    ".local/bin/mix-archive-manager",
    ".local/bin/manager",
    ".local/bin/launch-manager",
    ".local/bin/launch-liquid-glass",
    "manager.sh",
)


def _has_marker(directory: Path, marker: str) -> bool:
    try:
        return directory.is_dir() and (directory / marker).is_file()
    except OSError:
        return False


def _from_path(start: Path | str | None, marker: str) -> Path | None:
    """Walk up from a file or directory looking for the marker."""
    if not start:
        return None
    path = Path(start).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    if path.is_file():
        path = path.parent
    for _ in range(4):
        if _has_marker(path, marker):
            return path
        if path.parent == path:
            break
        path = path.parent
    return None


def _scan(base: Path, marker: str) -> Path | None:
    """Newest matching tree under a base directory."""
    try:
        entries = [p for p in base.glob("MP*ix*anager*") if p.is_dir()]
    except OSError:
        return None
    def sort_key(p: Path) -> tuple:
        parts = []
        for chunk in p.name.replace("-", " ").replace("_", " ").replace(".", " ").split():
            parts.append((0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk))
        return (parts, p.name)
    for entry in sorted(entries, key=sort_key, reverse=True):
        if _has_marker(entry, marker):
            return entry
        try:
            for child in sorted(entry.iterdir(), reverse=True):
                if _has_marker(child, marker):
                    return child
        except OSError:
            continue
    return None


def find_install_dir(marker: str = DEFAULT_MARKER) -> Path | None:
    """Return the install tree, or None when nothing matches."""
    override = os.environ.get("MP_MIX_MANAGER_DIR", "").strip()
    if override:
        found = _from_path(override, marker)
        if found:
            return found

    home = Path.home()
    for relative in _STARTING_POINTS:
        link = home / relative
        if link.exists() or link.is_symlink():
            found = _from_path(link, marker)
            if found:
                return found

    try:
        recorded = (_CONFIG_DIR / "install_dir").read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""
    if recorded:
        found = _from_path(recorded, marker)
        if found:
            return found

    for base in (home, Path("/opt")):
        found = _scan(base, marker)
        if found:
            return found

    return _from_path(Path(__file__).resolve().parent, marker)
