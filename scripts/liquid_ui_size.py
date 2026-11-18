"""Player-published Full / Vertical / Mini / Micro size for the Glass window.

The glass host must not bind M itself. During search that letter is text, and
the player is the only place that knows the difference. The player writes the
size it actually applied. The host reads it and resizes.
"""
from __future__ import annotations

import os
from pathlib import Path

UI_SIZES = ("NORMAL", "VERTICAL", "MINI", "MICRO")
# The order M steps through in the glass window. The tall stacked view sits
# between the full console and the two compact strips.
UI_SIZE_CYCLE = ("NORMAL", "VERTICAL", "MINI", "MICRO")
# The portrait deck: twice as tall as it is wide at the 9x18 cell, so the
# glass window fits a large clock and a tall analyser in one column.
VERTICAL_COLS, VERTICAL_ROWS = 56, 52
# The sizes a compact (non-full) window has a frame for.
COMPACT_UI_SIZES = ("VERTICAL", "MINI", "MICRO")


def size_grid(mode: str) -> tuple[int, int]:
    """Terminal columns and rows the glass host should give a mode."""
    mode = str(mode or "").strip().upper()
    if mode == "VERTICAL":
        return VERTICAL_COLS, VERTICAL_ROWS
    if mode == "MINI":
        return 74, 14
    if mode == "MICRO":
        return 74, 7
    return 120, 40


def _path(config_dir: Path, pid: int) -> Path:
    return Path(config_dir) / f"liquid_ui_size.{int(pid)}"


def publish_ui_size(config_dir: Path, pid: int, mode: str) -> None:
    mode = str(mode or "").strip().upper()
    if mode not in UI_SIZES or int(pid or 0) <= 0:
        return
    path = _path(config_dir, pid)
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip().upper() == mode:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".liquid_ui_size.{int(pid)}.tmp"
        tmp.write_text(mode + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def read_ui_size(config_dir: Path, pid: int) -> str:
    if int(pid or 0) <= 0:
        return ""
    try:
        mode = _path(config_dir, pid).read_text(encoding="utf-8").strip().upper()
    except OSError:
        return ""
    return mode if mode in UI_SIZES else ""


def clear_ui_size(config_dir: Path, pid: int) -> None:
    if int(pid or 0) <= 0:
        return
    try:
        _path(config_dir, pid).unlink()
    except OSError:
        pass
