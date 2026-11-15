"""MP Gui window shape published when M is pressed.

HORIZONTAL is the normal landscape window at the normal font.
VERTICAL is that window turned portrait (taller than wide) with the
terminal text at 80 percent of the normal size.

The glass host must not bind M. Search types that letter, and only the
manager knows when a key is search text. The manager writes the shape.
The host reads it and resizes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

HORIZONTAL = "HORIZONTAL"
VERTICAL = "VERTICAL"
SHAPES = (HORIZONTAL, VERTICAL)
VERTICAL_FONT_SCALE = 0.8


def _path(config_dir: Path, pid: int) -> Path:
    return Path(config_dir) / f"gui_window_shape.{int(pid)}"


def publish_gui_window_shape(config_dir: Path, pid: int, shape: str) -> None:
    shape = str(shape or "").strip().upper()
    if shape not in SHAPES or int(pid or 0) <= 0:
        return
    path = _path(config_dir, pid)
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip().upper() == shape:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".gui_window_shape.{int(pid)}.tmp"
        tmp.write_text(shape + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def read_gui_window_shape(config_dir: Path, pid: int) -> str:
    if int(pid or 0) <= 0:
        return ""
    try:
        shape = _path(config_dir, pid).read_text(encoding="utf-8").strip().upper()
    except OSError:
        return ""
    return shape if shape in SHAPES else ""


def clear_gui_window_shape(config_dir: Path, pid: int) -> None:
    if int(pid or 0) <= 0:
        return
    try:
        _path(config_dir, pid).unlink()
    except OSError:
        pass


def font_point_size(font_name: str, scale: float = 1.0) -> float:
    """Point size named by a Pango font string, multiplied by ``scale``.

    ``Monospace 11`` at 0.8 is 8.8. A missing size is 11, the glass default.
    """
    match = re.search(r"(\d+(?:\.\d+)?)\s*$", str(font_name or "").strip())
    base = float(match.group(1)) if match else 11.0
    return max(6.0, base * float(scale))


def portrait_window(normal_w: int, normal_h: int) -> tuple[int, int]:
    """Swap a landscape window so the result is taller than it is wide."""
    short, tall = sorted((max(1, int(normal_w)), max(1, int(normal_h))))
    if tall <= short:
        tall = short + 1
    return short, tall


def terminal_grid(
    width: int,
    height: int,
    cell_w: int,
    cell_h: int,
    bezel: int,
    extra: int = 0,
) -> tuple[int, int]:
    """Columns and rows that fill a pixel window.

    ``extra`` is the strip under the text (the transparency bar on the
    OpenGL host). It is not part of the cell grid.
    """
    cell_w = max(1, int(cell_w))
    cell_h = max(1, int(cell_h))
    inner_w = max(cell_w, int(width) - 2 * int(bezel))
    inner_h = max(cell_h, int(height) - 2 * int(bezel) - int(extra))
    return max(20, inner_w // cell_w), max(10, inner_h // cell_h)


def scaled_cell(cell: tuple[int, int], scale: float) -> tuple[int, int]:
    width, height = int(cell[0]), int(cell[1])
    return max(4, int(round(width * float(scale)))), max(8, int(round(height * float(scale))))
