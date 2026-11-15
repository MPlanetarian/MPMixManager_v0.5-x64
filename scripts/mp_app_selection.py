#!/usr/bin/env python3
"""Which of the four MP applications this install sets up.

The installer offers "all four" or "only one of them". The answer is stored in
``~/.config/mix-manager/installed_apps`` as one line of component ids, and every
consumer reads it from here so the desktop shortcuts, the shared tray menu and
the Web Companion cannot disagree about what was installed.

Component ids are stable and short; the display names are the shortcut names:

    manager     MP Mix Manager     (the terminal manager)
    gui         MP Gui             (the Liquid Glass manager window)
    liquid      MP Liquid Player   (the Liquid Glass audio player)
    visualizer  MP Liquid Visualizer

Usage:

    python3 scripts/mp_app_selection.py                 # print "manager gui ..."
    python3 scripts/mp_app_selection.py --labels        # print the display names
    python3 scripts/mp_app_selection.py --set all
    python3 scripts/mp_app_selection.py --set gui,liquid
    python3 scripts/mp_app_selection.py --enabled gui   # exit 0 when selected
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Order matters: it is the order the installer, the shortcuts and the tray
# menu use, and the order the user sees in every prompt.
COMPONENTS = ("manager", "gui", "liquid", "visualizer")

LABELS = {
    "manager": "MP Mix Manager",
    "gui": "MP Gui",
    "liquid": "MP Liquid Player",
    "visualizer": "MP Liquid Visualizer",
}

BLURBS = {
    "manager": "the terminal manager (archive, playlists, conversions)",
    "gui": "the Liquid Glass manager window",
    "liquid": "the Liquid Glass audio player and visualizer host",
    "visualizer": "the standalone 3D audio-reactive visualizer",
}

STATE_FILE = "installed_apps"

# Spellings accepted from a command line, an environment variable or a menu.
_ALIASES = {
    "all": COMPONENTS,
    "everything": COMPONENTS,
    "full": COMPONENTS,
    "manager": ("manager",),
    "mp-mix-manager": ("manager",),
    "mp_mix_manager": ("manager",),
    "mix-manager": ("manager",),
    "mix_manager": ("manager",),
    "cli": ("manager",),
    "gui": ("gui",),
    "mp-gui": ("gui",),
    "mp_gui": ("gui",),
    "liquid": ("liquid",),
    "player": ("liquid",),
    "liquid-player": ("liquid",),
    "liquid_player": ("liquid",),
    "mp-liquid-player": ("liquid",),
    "mp_liquid_player": ("liquid",),
    "visualizer": ("visualizer",),
    "viz": ("visualizer",),
    "liquid-visualizer": ("visualizer",),
    "liquid_visualizer": ("visualizer",),
    "mp-liquid-visualizer": ("visualizer",),
    "mp_liquid_visualizer": ("visualizer",),
}

_LABEL_TO_COMPONENT = {label.lower(): ident for ident, label in LABELS.items()}


def state_path(config_dir: Path | str | None = None) -> Path:
    """Where the selection lives; MP_APP_SELECTION overrides the whole path."""
    override = os.environ.get("MP_APP_SELECTION_FILE", "").strip()
    if override:
        return Path(override)
    if config_dir is not None:
        return Path(config_dir) / STATE_FILE
    return Path.home() / ".config" / "mix-manager" / STATE_FILE


def file_says_none(path: Path | str | None = None) -> bool:
    """True when the selection file exists and says nothing is installed.

    A missing file is not this case: a machine that has never chosen is still
    "all four". Only the uninstaller writes ``none``.
    """
    target = Path(path) if path is not None else state_path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return False
    return text.strip().lower() in ("none", "nothing")


def parse(text: str | None) -> list[str]:
    """Turn "all", "gui,liquid" or a space/newline list into canonical ids.

    An empty or unrecognised answer means "all four": a first install must not
    end up with no shortcuts because a variable was misspelled. The words
    ``none`` and ``nothing`` are the uninstaller: every app was removed.
    """
    if text is None:
        return list(COMPONENTS)
    raw = str(text).strip().lower()
    if not raw:
        return list(COMPONENTS)
    if raw in ("none", "nothing"):
        return []
    if raw in _ALIASES:
        return list(_ALIASES[raw])
    found: set[str] = set()
    for token in raw.replace(",", " ").replace(";", " ").split():
        if token in _ALIASES:
            found.update(_ALIASES[token])
        elif token in _LABEL_TO_COMPONENT:
            found.add(_LABEL_TO_COMPONENT[token])
    if not found:
        return list(COMPONENTS)
    return [ident for ident in COMPONENTS if ident in found]


def read(path: Path | str | None = None) -> list[str]:
    """The stored selection, or all four when the file is missing."""
    target = Path(path) if path is not None else state_path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return list(COMPONENTS)
    return parse(text)


def write(components, path: Path | str | None = None, *, allow_empty: bool = False) -> Path:
    """Store one line of component ids. Returns the file that was written.

    An empty list is stored as ``none`` only when ``allow_empty`` is set. The
    installer calls this without that flag, so a blank answer still means all
    four and cannot wipe the Desktop by accident.
    """
    chosen = [ident for ident in COMPONENTS if ident in set(components)]
    target = Path(path) if path is not None else state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not chosen:
        if allow_empty:
            target.write_text("none\n", encoding="utf-8")
            return target
        chosen = list(COMPONENTS)
    target.write_text(" ".join(chosen) + "\n", encoding="utf-8")
    return target


def identify(token: str) -> str | None:
    """One component id for a single spelling, or None when it is not one app.

    ``all`` is not one app. An unknown word is None, so a caller can refuse it
    instead of treating it as every application.
    """
    raw = str(token).strip().lower()
    if raw in _LABEL_TO_COMPONENT:
        return _LABEL_TO_COMPONENT[raw]
    mapped = _ALIASES.get(raw)
    if mapped is not None and len(mapped) == 1:
        return mapped[0]
    return None


def enabled(component: str, path: Path | str | None = None) -> bool:
    return component in read(path)


def labels(components=None) -> list[str]:
    chosen = list(components) if components is not None else list(COMPONENTS)
    return [LABELS[ident] for ident in COMPONENTS if ident in set(chosen)]


def summary(components=None) -> str:
    chosen = labels(components)
    if len(chosen) == len(COMPONENTS):
        return "all four MP applications"
    if len(chosen) == 1:
        return chosen[0]
    return ", ".join(chosen[:-1]) + " and " + chosen[-1]


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--set" in args:
        index = args.index("--set")
        value = args[index + 1] if index + 1 < len(args) else ""
        write(parse(value))
        print(" ".join(read()))
        return 0
    if "--enabled" in args:
        index = args.index("--enabled")
        value = args[index + 1] if index + 1 < len(args) else ""
        return 0 if enabled(value) else 1
    if "--labels" in args:
        for label in labels(read()):
            print(label)
        return 0
    print(" ".join(read()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
