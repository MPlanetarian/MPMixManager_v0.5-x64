#!/usr/bin/env python3
"""Which MP applications this install sets up.

The installer offers "all five" or "only one of them". The answer is stored in
``~/.config/mix-manager/installed_apps`` as one line of component ids, and every
consumer reads it from here so the desktop shortcuts, the shared tray menu and
the Web Companion cannot disagree about what was installed.

Component ids are stable and short; the display names are the shortcut names:

    manager     MP Mix Manager     (the terminal manager)
    gui         MP Gui             (the Liquid Glass manager window)
    liquid      MP Liquid Player   (the Liquid Glass audio player)
    visualizer  MP Liquid Visualizer
    mixer       MP Liquid Mixer    (Linux, FreeBSD & Windows)

The **MP Liquid Mixer** is a GTK/VTE/ALSA host on Linux and FreeBSD and a
native Tkinter/winmm host on Windows, so it is selectable there. macOS has no
port yet. ``parse()`` and ``supported_components()`` drop it only on macOS. It
is a real fifth choice:
``--set mixer`` installs the mixer alone, and selecting one of the other apps
does not silently add it.

A state file written before the mixer was selectable has no ``mixer=off``
marker. On Linux such a legacy file is read as if the mixer were selected, so
upgrading does not remove the shortcut that the old installer always added.
``write()`` adds ``mixer=off`` whenever a non-empty selection deliberately
leaves the mixer out.

Usage:

    python3 scripts/mp_app_selection.py                 # print "manager gui ..."
    python3 scripts/mp_app_selection.py --labels        # print the display names
    python3 scripts/mp_app_selection.py --supported     # ids valid on this OS
    python3 scripts/mp_app_selection.py --set all
    python3 scripts/mp_app_selection.py --set gui,liquid
    python3 scripts/mp_app_selection.py --set mixer     # Linux, FreeBSD, Windows
    python3 scripts/mp_app_selection.py --enabled gui   # exit 0 when selected
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

# Order matters: it is the order the installer, the shortcuts and the tray
# menu use, and the order the user sees in every prompt.
COMPONENTS = ("manager", "gui", "liquid", "visualizer", "mixer")

LABELS = {
    "manager": "MP Mix Manager",
    "gui": "MP Gui",
    "liquid": "MP Liquid Player",
    "visualizer": "MP Liquid Visualizer",
    "mixer": "MP Liquid Mixer",
}

BLURBS = {
    "manager": "the terminal manager (archive, playlists, conversions)",
    "gui": "the Liquid Glass manager window",
    "liquid": "the Liquid Glass audio player and visualizer host",
    "visualizer": "the standalone 3D audio-reactive visualizer",
    "mixer": "the four-channel DJ mixer (Linux, FreeBSD, Windows)",
}

STATE_FILE = "installed_apps"

# Written on a second line when a non-empty selection deliberately omits the
# mixer. Legacy files have no marker and keep the mixer on Linux, which is what
# every pre-selectable version installed.
MIXER_EXCLUSION_MARKER = "mixer=off"

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
    "mixer": ("mixer",),
    "dj": ("mixer",),
    "liquid-mixer": ("mixer",),
    "liquid_mixer": ("mixer",),
    "mp-liquid-mixer": ("mixer",),
    "mp_liquid_mixer": ("mixer",),
}

_LABEL_TO_COMPONENT = {label.lower(): ident for ident, label in LABELS.items()}


def detect_platform() -> str:
    """The platform labels the installer uses: linux, macos or windows."""
    system = sys.platform
    if system == "darwin":
        return "macos"
    if system in {"win32", "cygwin", "msys"}:
        return "windows"
    release = platform.release().lower()
    if os.environ.get("WSL_DISTRO_NAME") or "microsoft" in release:
        return "windows"
    return "linux"


def supported_components(platform_name: str | None = None) -> list[str]:
    """The component ids this platform can actually run and give shortcuts.

    The mixer is available on Linux/FreeBSD (GTK/VTE/ALSA) and Windows (the
    native Tkinter/winmm port). macOS has no port yet and drops it.
    """
    name = platform_name or detect_platform()
    return [ident for ident in COMPONENTS
            if ident != "mixer" or name in ("linux", "windows")]


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
    "all supported". Only the uninstaller writes ``none``.
    """
    target = Path(path) if path is not None else state_path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return False
    return text.strip().lower() in ("none", "nothing")


def parse(text: str | None, platform_name: str | None = None) -> list[str]:
    """Turn "all", "gui,liquid" or a space/newline list into canonical ids.

    An empty or unrecognised answer means "all supported": a first install must
    not end up with no shortcuts because a variable was misspelled. The words
    ``none`` and ``nothing`` are the uninstaller: every app was removed.

    ``mixer`` is dropped on macOS instead of being passed to an installer that
    cannot create its shortcut; Windows keeps it.
    """
    supported = supported_components(platform_name)
    if text is None:
        return list(supported)
    raw = str(text).strip().lower()
    if not raw:
        return list(supported)
    if raw in ("none", "nothing"):
        return []
    if raw in ("all", "everything", "full"):
        return list(supported)
    found: set[str] = set()
    # Match a full display name before splitting it. "MP Liquid Mixer" would
    # otherwise be read as "liquid" plus "mixer" because both words are aliases.
    if raw in _LABEL_TO_COMPONENT:
        found.add(_LABEL_TO_COMPONENT[raw])
    else:
        for token in raw.replace(",", " ").replace(";", " ").split():
            if token in _ALIASES:
                found.update(_ALIASES[token])
            elif token in _LABEL_TO_COMPONENT:
                found.add(_LABEL_TO_COMPONENT[token])
    if not found:
        return list(supported)
    return [ident for ident in COMPONENTS if ident in found and ident in supported]


def read(path: Path | str | None = None, platform_name: str | None = None) -> list[str]:
    """The stored selection, or all supported apps when the file is missing.

    A file written before the mixer became selectable has no ``mixer=off``
    marker. On Linux the mixer is added back for those legacy files, because
    every earlier installer always created its shortcut. A new selection that
    deliberately omits the mixer writes the marker, so it stays omitted.
    """
    target = Path(path) if path is not None else state_path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return list(supported_components(platform_name))
    if text.strip().lower() in ("none", "nothing"):
        return []
    chosen = parse(text, platform_name)
    if not chosen:
        return chosen
    supported = supported_components(platform_name)
    excluded = MIXER_EXCLUSION_MARKER in text.lower()
    if "mixer" in supported and "mixer" not in chosen and not excluded:
        chosen = [ident for ident in COMPONENTS if ident in set(chosen) | {"mixer"}]
    return chosen


def write(
    components,
    path: Path | str | None = None,
    *,
    allow_empty: bool = False,
    platform_name: str | None = None,
) -> Path:
    """Store one line of component ids. Returns the file that was written.

    An empty list is stored as ``none`` only when ``allow_empty`` is set. The
    installer calls this without that flag, so a blank answer still means all
    supported apps and cannot wipe the Desktop by accident.

    When a non-empty selection leaves the mixer out on Linux, a second
    ``mixer=off`` line records that decision. Without it, a legacy selection
    would be read as "the old installer always added the mixer".
    """
    supported = supported_components(platform_name)
    chosen = [ident for ident in COMPONENTS if ident in set(components) and ident in supported]
    target = Path(path) if path is not None else state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not chosen:
        if allow_empty:
            target.write_text("none\n", encoding="utf-8")
            return target
        chosen = list(supported)
    text = " ".join(chosen) + "\n"
    if chosen and "mixer" in supported and "mixer" not in chosen:
        text += MIXER_EXCLUSION_MARKER + "\n"
    target.write_text(text, encoding="utf-8")
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


def labels(components=None, platform_name: str | None = None) -> list[str]:
    chosen = list(components) if components is not None else supported_components(platform_name)
    return [LABELS[ident] for ident in COMPONENTS if ident in set(chosen)]


def summary(components=None, platform_name: str | None = None) -> str:
    chosen = labels(components, platform_name)
    supported = supported_components(platform_name)
    if len(chosen) == len(supported):
        words = {4: "four", 5: "five"}
        return f"all {words.get(len(chosen), str(len(chosen)))} MP applications"
    if len(chosen) == 1:
        return chosen[0]
    if not chosen:
        return "no MP applications"
    return ", ".join(chosen[:-1]) + " and " + chosen[-1]


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--set" in args:
        index = args.index("--set")
        value = args[index + 1] if index + 1 < len(args) else ""
        write(parse(value))
        print(" ".join(read()))
        return 0
    if "--supported" in args:
        print(" ".join(supported_components()))
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
