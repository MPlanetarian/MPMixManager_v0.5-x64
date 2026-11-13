#!/usr/bin/env python3
"""
MP Mix Manager - shared theme palette and alias table.

Every renderer used to carry its own copy of the colour table and the alias
map: the OpenGL glass renderer, the Vulkan glass renderer, the Metal glass
renderer and the terminal player. The colours agreed, but the alias maps did
not, and that drifted into a user-visible bug:

    liquid_glass_opengl.py  THEME_ALIASES  78 entries
    liquid_glass_vulkan.py  THEME_ALIASES  94 entries
    liquid_glass_metal.py   THEME_ALIASES  90 entries

`cosmic dreamworlds`, `sensorium interface`, `earth flipper` and
`orbital void reflux` resolved on Vulkan and Metal but silently fell back to
`dreamworlds_ultra` on the default OpenGL backend. `mp_liquid` and
`white_on_black` worked on OpenGL and not on Vulkan.

One table, one alias map, one resolver. Adding a theme or an alias now changes
every backend at once.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

# Canonical id -> (accent, secondary, tertiary). These are the authoritative
# values; the renderers only read them.
THEME_COLORS: Dict[str, Tuple[RGB, RGB, RGB]] = {
    "stream_of_frequency": ((0, 242, 255), (76, 217, 123), (245, 237, 106)),
    "dreamworlds_ultra": ((166, 77, 255), (247, 37, 133), (0, 229, 255)),
    "dynamic_rainbow_liquid_glass": ((176, 88, 255), (255, 45, 142), (0, 242, 254)),
    "dreamworlds_liquid": ((0, 245, 255), (199, 125, 255), (72, 149, 239)),
    "cosmic_supernova": ((224, 86, 253), (0, 245, 212), (76, 201, 240)),
    "dreamworlds_neon": ((0, 229, 255), (247, 37, 133), (102, 102, 255)),
    "retro_studio": ((241, 196, 15), (230, 126, 34), (243, 156, 18)),
    "cyber_neon": ((76, 201, 240), (255, 0, 85), (153, 153, 255)),
    "phosphor": ((57, 255, 20), (166, 255, 0), (0, 255, 102)),
    "ice_cold": ((0, 229, 255), (136, 192, 208), (129, 161, 193)),
    "retro_terminal": ((230, 230, 230), (180, 180, 180), (255, 255, 255)),
    "retro_linux_memories": ((45, 45, 45), (21, 83, 158), (26, 30, 33)),
    "into_darkness_void_out": ((0, 0, 0), (0, 0, 0), (0, 0, 0)),
    "cosmic_dreamworlds_radioactive": ((57, 255, 20), (0, 245, 212), (166, 77, 255)),
    "cosmic_dreamworlds": ((232, 13, 104), (0, 245, 212), (176, 88, 255)),
    "orbital_void_reflux": ((0, 240, 255), (157, 78, 221), (255, 0, 127)),
    "sensorium_interface": ((0, 245, 255), (240, 168, 216), (184, 72, 196)),
    "solarized": ((220, 50, 47), (38, 139, 210), (181, 137, 0)),
    "tokyo": ((247, 118, 142), (122, 162, 247), (187, 154, 247)),
    "monokai": ((249, 38, 114), (166, 226, 46), (102, 217, 239)),
    "emerald": ((46, 204, 113), (26, 188, 156), (0, 168, 132)),
}

# "The Whitest White" existed as a .theme file and a btop palette but was never
# added to any picker, so it could not be selected. It is a real palette now.
THEME_COLORS["the_whitest_white"] = ((255, 255, 255), (230, 230, 230), (200, 200, 200))

# A display label per canonical id. The manager menu shows these.
THEME_LABELS: Dict[str, str] = {
    "stream_of_frequency": "Stream of Frequency",
    "dreamworlds_ultra": "Dreamworlds Ultra",
    "dynamic_rainbow_liquid_glass": "Dynamic Liquid Glass [Rainbow Colours]",
    "dreamworlds_liquid": "Dreamworlds Liquid",
    "cosmic_supernova": "Cosmic Supernova",
    "dreamworlds_neon": "Dreamworlds Neon",
    "retro_studio": "Retro Studio",
    "cyber_neon": "Cyber Neon",
    "phosphor": "Phosphor",
    "ice_cold": "Ice Cold",
    "retro_terminal": "Retro Terminal",
    "retro_linux_memories": "Retro Linux Memories",
    "into_darkness_void_out": "Into Darkness (Void Out)",
    "cosmic_dreamworlds_radioactive": "Cosmic Dreamworlds (Radioactive)",
    "cosmic_dreamworlds": "Cosmic Dreamworlds",
    "orbital_void_reflux": "Orbital Void Reflux",
    "sensorium_interface": "Sensorium Interface",
    "solarized": "Solarized",
    "tokyo": "Tokyo Night",
    "monokai": "Monokai",
    "emerald": "Emerald",
    "the_whitest_white": "The Whitest White",
}

# Friendly names, historical spellings and the labels the manager writes into
# config.env. Every backend resolves through this one map.
THEME_ALIASES: Dict[str, str] = {
    # Stream of Frequency
    "stream_of_frequency": "stream_of_frequency",
    "stream_of_frequency_theme": "stream_of_frequency",
    "stream of frequency": "stream_of_frequency",
    "stream-of-frequency": "stream_of_frequency",
    "stream": "stream_of_frequency",
    "frequency": "stream_of_frequency",
    "sof": "stream_of_frequency",
    "frequency_stream": "stream_of_frequency",
    "stream of frequency preset": "stream_of_frequency",
    "infinite_complexity_stream037": "stream_of_frequency",
    "stream 037": "stream_of_frequency",
    # Dreamworlds
    "dreamworlds": "dreamworlds_ultra",
    "default": "dreamworlds_ultra",
    "mplanetarian": "dreamworlds_ultra",
    "dreamworlds_ultra": "dreamworlds_ultra",
    "dreamworlds ultra": "dreamworlds_ultra",
    "dreamworlds_ultra_mplanetarian": "dreamworlds_ultra",
    "dreamworlds ultra (mplanetarian biomech)": "dreamworlds_ultra",
    "biomech": "dreamworlds_ultra",
    "ultra": "dreamworlds_ultra",
    "dreamworlds_liquid": "dreamworlds_liquid",
    "dreamworlds liquid": "dreamworlds_liquid",
    "liquid": "dreamworlds_liquid",
    "dreamworlds_neon": "dreamworlds_neon",
    "dracula": "dreamworlds_neon",
    # Rainbow / dynamic
    "dynamic_rainbow_liquid_glass": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_rainbow": "dynamic_rainbow_liquid_glass",
    "dynamic_rainbow": "dynamic_rainbow_liquid_glass",
    "rainbow_liquid_glass": "dynamic_rainbow_liquid_glass",
    "rainbow": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass": "dynamic_rainbow_liquid_glass",
    "dynamic liquid glass [rainbow colours] (new)": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_[rainbow_colours]_(new)": "dynamic_rainbow_liquid_glass",
    # Cosmic family
    "cosmic": "cosmic_supernova",
    "supernova": "cosmic_supernova",
    "cosmic_supernova": "cosmic_supernova",
    "cosmic dreamworlds": "cosmic_dreamworlds",
    "cosmic dreamworld": "cosmic_dreamworlds",
    "cosmic dream": "cosmic_dreamworlds",
    "cosmic_dreamworlds": "cosmic_dreamworlds",
    "cosmic_dreamworld": "cosmic_dreamworlds",
    "cosmic_dream": "cosmic_dreamworlds",
    "cosmic dreamworlds (liquid glass skin)": "cosmic_dreamworlds",
    "cosmic_dreamworlds_(liquid_glass_skin)": "cosmic_dreamworlds",
    "cosmic dreamworlds radioactive": "cosmic_dreamworlds_radioactive",
    "cosmic_dreamworlds_radioactive": "cosmic_dreamworlds_radioactive",
    "cosmic dreamworlds radioactive (liquid glass skin)": "cosmic_dreamworlds_radioactive",
    "cosmic_dreamworlds_radioactive_(liquid_glass_skin)": "cosmic_dreamworlds_radioactive",
    "cosmic_radioactive": "cosmic_dreamworlds_radioactive",
    "cosmic universe": "cosmic_dreamworlds",
    "cosmic_universe": "cosmic_dreamworlds",
    "universe": "cosmic_dreamworlds",
    "constellation": "cosmic_dreamworlds",
    "the cosmological horizon": "cosmic_dreamworlds",
    "cosmological horizon": "cosmic_dreamworlds",
    "horizon": "cosmic_dreamworlds",
    # Sensorium / Earth Flipper
    "sensorium": "sensorium_interface",
    "sensorium interface": "sensorium_interface",
    "sensorium_interface": "sensorium_interface",
    "sensorium interface (liquid glass skin)": "sensorium_interface",
    "sensorium_interface_(liquid_glass_skin)": "sensorium_interface",
    "earth flipper": "sensorium_interface",
    "earth_flipper": "sensorium_interface",
    "flipper": "sensorium_interface",
    "yaporigami": "sensorium_interface",
    "mplanetarian earth flipper": "sensorium_interface",
    # Orbital Void Reflux
    "orbital": "orbital_void_reflux",
    "orbital void": "orbital_void_reflux",
    "orbital_void": "orbital_void_reflux",
    "orbital void reflux": "orbital_void_reflux",
    "orbital_void_reflux": "orbital_void_reflux",
    "orbital void reflux (liquid glass skin)": "orbital_void_reflux",
    "orbital_void_reflux_(liquid_glass_skin)": "orbital_void_reflux",
    # Terminals and classics
    "unix_terminal": "retro_terminal",
    "retro_unix": "retro_terminal",
    "white_on_black": "retro_terminal",
    "old_style_unix": "retro_terminal",
    "terminal": "retro_terminal",
    "classic": "retro_terminal",
    "retro_terminal": "retro_terminal",
    "retro linux memories": "retro_linux_memories",
    "retro_linux_memories": "retro_linux_memories",
    "mp_liquid": "dreamworlds_liquid",
    "cyberpunk": "cyber_neon",
    "cyber": "cyber_neon",
    "cyber_neon": "cyber_neon",
    "matrix": "phosphor",
    "phosphor": "phosphor",
    "nord": "ice_cold",
    "ice_cold": "ice_cold",
    "ice cold": "ice_cold",
    "gruvbox": "retro_studio",
    "retro_studio": "retro_studio",
    "solarized": "solarized",
    "tokyo": "tokyo",
    "tokyo night": "tokyo",
    "monokai": "monokai",
    "emerald": "emerald",
    "retro_linux": "retro_linux_memories",
    "linux_memories": "retro_linux_memories",
    "veracrypt": "retro_linux_memories",
    "radioactive": "cosmic_dreamworlds_radioactive",
    "void_reflux": "orbital_void_reflux",
    "into darkness": "into_darkness_void_out",
    "into_darkness": "into_darkness_void_out",
    "into_darkness_void_out": "into_darkness_void_out",
    "void out": "into_darkness_void_out",
    "void_out": "into_darkness_void_out",
    # The Whitest White
    "the whitest white": "the_whitest_white",
    "the_whitest_white": "the_whitest_white",
    "whitest white": "the_whitest_white",
    "whitest_white": "the_whitest_white",
    "the whitest white (mp mix manager)": "the_whitest_white",
    "white": "the_whitest_white",
}

DEFAULT_THEME = "dreamworlds_ultra"


def normalize_theme_name(name: Optional[str]) -> str:
    """Map any spelling, label or alias to a canonical id.

    Returns ``DEFAULT_THEME`` when the value is empty or unknown, which matches
    the historical behaviour of every renderer but now does so in one place.
    """
    if not name:
        return DEFAULT_THEME
    key = str(name).strip().lower()
    if not key:
        return DEFAULT_THEME
    # Underscore/hyphen variants collapse to spaces first, then the raw form is
    # tried, so both `stream_of_frequency` and `Stream of Frequency` resolve.
    spaced = key.replace("_", " ").replace("-", " ")
    spaced = " ".join(spaced.split())
    for candidate in (key, spaced,
                      key.replace(" ", "_"), spaced.replace(" ", "_")):
        if candidate in THEME_ALIASES:
            return THEME_ALIASES[candidate]
        if candidate in THEME_COLORS:
            return candidate
    return DEFAULT_THEME


def theme_colors(name: Optional[str]) -> Tuple[RGB, RGB, RGB]:
    """Three colours for a theme name, always resolvable."""
    return THEME_COLORS.get(normalize_theme_name(name),
                            THEME_COLORS[DEFAULT_THEME])


def theme_label(name: Optional[str]) -> str:
    return THEME_LABELS.get(normalize_theme_name(name),
                            THEME_LABELS[DEFAULT_THEME])


def theme_ids() -> List[str]:
    """Canonical ids in a stable, display-worthy order."""
    return [tid for tid in THEME_LABELS if tid in THEME_COLORS]


def cycle_order() -> List[str]:
    """The order the manager's `C` key cycles through."""
    preferred = [
        "dreamworlds_ultra",
        "stream_of_frequency",
        "sensorium_interface",
        "cosmic_dreamworlds",
        "cosmic_dreamworlds_radioactive",
        "orbital_void_reflux",
        "cosmic_supernova",
        "dynamic_rainbow_liquid_glass",
        "dreamworlds_liquid",
        "dreamworlds_neon",
        "cyber_neon",
        "phosphor",
        "ice_cold",
        "retro_terminal",
        "retro_linux_memories",
        "retro_studio",
        "solarized",
        "tokyo",
        "monokai",
        "emerald",
        "the_whitest_white",
        "into_darkness_void_out",
    ]
    return [tid for tid in preferred if tid in THEME_COLORS]


def rgb01(name: Optional[str]) -> Tuple[Tuple[float, float, float], ...]:
    """Palette as 0..1 floats, the form the shaders consume."""
    return tuple(tuple(c / 255.0 for c in rgb) for rgb in theme_colors(name))


def unknown_theme_report() -> List[str]:
    """Every alias that points at an id that does not exist.

    Used by the test suite: a typo in the table would otherwise surface as a
    silent fallback on one backend only.
    """
    return [alias for alias, target in THEME_ALIASES.items()
            if target not in THEME_COLORS]
