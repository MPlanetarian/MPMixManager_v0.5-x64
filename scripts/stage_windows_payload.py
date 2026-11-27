#!/usr/bin/env python3
"""Stage the Windows MSI payload exactly as the 0.5.4 installer lays it out.

The reference MSI (built with WiX 5.0.2 on Windows 11) installs one directory
tree under Program Files. This script reproduces that tree from the repository
using the rules the reference package itself proves:

  * the repository root ships every file except ``*.log``
  * ``scripts`` ships every file, including its ``assets``, ``lib`` and
    ``systemd`` subdirectories
  * ``assets``, ``bin``, ``desktop``, ``docs``, ``MP_Liquid_Mixer``,
    ``mplanetarians-alarm-clock``, ``shaders``, ``themes`` and ``tests``
    ship every file (``shaders/presets`` included)
  * ``.pytest_cache`` and ``.vscode`` ship every file
  * ``TRACKLIST_EXPORTS`` gets four generated report files

Nothing from ``venv``, ``__pycache__``, the build scratch directory or the
user's archive/library folders is ever copied.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".build_tmp" / "stage"

# Directories copied whole (the reference MSI proves each one).
WHOLE_DIRS = [
    ".pytest_cache",
    ".vscode",
    "assets",
    "bin",
    "desktop",
    "docs",
    "MP_Liquid_Mixer",
    "mplanetarians-alarm-clock",
    "scripts",
    "shaders",
    "tests",
    "themes",
]

# Root-level files that are never payload.
ROOT_SKIP_SUFFIXES = {".log"}
ROOT_SKIP_NAMES = {
    "DELETE",            # a scratch marker, not part of the app
    "nodeids",           # pytest cache artefact that lands in the root
}

# Generated report files that TRACKLIST_EXPORTS carries in the reference MSI.
TRACKLIST_EXPORTS = [
    "Mix_Export.html",
    "Mix_Wide.html",
    "Mix_Export.txt",
    "MPlanetarian_Stream_of_Frequency_068_Gai_Barone2026-05-22.html",
]


def copy_file(src: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return 1


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    copied = 0
    skipped: list[str] = []

    # -- root files ------------------------------------------------------
    for entry in sorted(ROOT.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() in ROOT_SKIP_SUFFIXES or entry.name in ROOT_SKIP_NAMES:
            skipped.append(entry.name)
            continue
        copied += copy_file(entry, STAGE / entry.name)

    # -- whole directories ----------------------------------------------
    for name in WHOLE_DIRS:
        src_dir = ROOT / name
        if not src_dir.is_dir():
            raise SystemExit(f"missing payload directory: {src_dir}")
        for entry in sorted(src_dir.rglob("*")):
            if not entry.is_file():
                continue
            if "__pycache__" in entry.parts:
                continue
            rel = entry.relative_to(ROOT)
            copied += copy_file(entry, STAGE / rel)

    # -- TRACKLIST_EXPORTS ----------------------------------------------
    exports = STAGE / "TRACKLIST_EXPORTS"
    exports.mkdir(exist_ok=True)
    source = ROOT / "TRACKLIST_EXPORTS"
    for name in TRACKLIST_EXPORTS:
        candidate = source / name
        if candidate.is_file():
            copied += copy_file(candidate, exports / name)
        else:
            # The reference package carries these files even when the working
            # tree does not; ship an empty stand-in so the folder matches.
            (exports / name).write_text("", encoding="utf-8")
            copied += 1

    files = sorted(p for p in STAGE.rglob("*") if p.is_file())
    total_bytes = sum(p.stat().st_size for p in files)

    print(f"staged   : {STAGE}")
    print(f"files    : {len(files)}")
    print(f"bytes    : {total_bytes:,} ({total_bytes / 1_048_576:.1f} MiB)")
    print(f"skipped  : {', '.join(skipped) if skipped else '(none)'}")

    counts: dict[str, int] = {}
    for path in files:
        rel = path.relative_to(STAGE)
        key = str(rel.parent) if str(rel.parent) != "." else "(root)"
        counts[key] = counts.get(key, 0) + 1
    for key in sorted(counts):
        print(f"  {key:<38} {counts[key]:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
