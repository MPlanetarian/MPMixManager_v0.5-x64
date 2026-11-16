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
    # Sensorium: bright tangerine, acid chartreuse and electric azure. The warm
    # mix turns the still-water glass pane into champagne gold, which none of
    # the other themes produce.
    "sensorium": ((255, 122, 24), (200, 255, 61), (58, 134, 255)),
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
    "sensorium": "Sensorium",
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
    # Sensorium
    "sensorium": "sensorium",
    "sensorium interface": "sensorium",
    "sensorium_interface": "sensorium",
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
    "retro-linux-memories": "retro_linux_memories",
    "retro-linux": "retro_linux_memories",
    "linux": "retro_linux_memories",
    "retrolinux": "retro_linux_memories",
    "charcoal_vault": "retro_linux_memories",
    "charcoal": "retro_linux_memories",
    "veracrypt_dark": "retro_linux_memories",
    "vault": "retro_linux_memories",
    "dark_vault": "retro_linux_memories",
    "linux_memories": "retro_linux_memories",
    "veracrypt": "retro_linux_memories",
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

# Ids the manager's THEME_BG table paints. Aliases that the glass collapses
# (dreamworlds, dracula, nord, …) are listed so the shell and this dict can
# be compared one key at a time.
_CONSOLE_THEME_IDS: Tuple[str, ...] = (
    "stream_of_frequency",
    "dreamworlds",
    "dreamworlds_ultra",
    "dynamic_rainbow_liquid_glass",
    "cosmic_supernova",
    "dreamworlds_liquid",
    "dreamworlds_neon",
    "retro_studio",
    "cyber_neon",
    "phosphor",
    "ice_cold",
    "retro_terminal",
    "sensorium",
    "retro_linux_memories",
    "into_darkness_void_out",
    "cyberpunk",
    "dracula",
    "nord",
    "matrix",
    "solarized",
    "tokyo",
    "monokai",
    "gruvbox",
    "emerald",
    "classic",
    "the_whitest_white",
)


def _theme_mix01(colors: Tuple[RGB, RGB, RGB]) -> Tuple[float, float, float]:
    """The shader's theme mix: mix(highlight, accent, 0.42) then mix(that, reflection, 0.20)."""
    hi, accent, refl = ([c / 255.0 for c in rgb] for rgb in colors)
    blended = tuple(hi[i] + (accent[i] - hi[i]) * 0.42 for i in range(3))
    return tuple(blended[i] + (refl[i] - blended[i]) * 0.20 for i in range(3))


def glass_field_rgb(name: Optional[str]) -> RGB:
    """Still-water field MP Gui and MP Liquid Player paint for this theme.

    Both glass shaders start from rgb(0.76, 0.80, 0.87) and mix 22% of the
    theme colour into it. That is the background behind the text. Into
    Darkness mixes to black, and both shaders then force the pane to opaque
    black (void out). The console manager paints this same RGB, so pressing
    C lands on one background in all three apps.
    """
    theme = _theme_mix01(theme_colors(name))
    luma = 0.299 * theme[0] + 0.587 * theme[1] + 0.114 * theme[2]
    if luma < 0.001:
        return (0, 0, 0)
    base = (0.76, 0.80, 0.87)
    water = tuple(base[i] + (theme[i] - base[i]) * 0.22 for i in range(3))
    return tuple(max(0, min(255, int(round(channel * 255.0)))) for channel in water)


def _relative_luminance(rgb: RGB) -> float:
    def channel(value: int) -> float:
        scaled = value / 255.0
        if scaled <= 0.04045:
            return scaled / 12.92
        return ((scaled + 0.055) / 1.055) ** 2.4

    red, green, blue = rgb
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(fore: RGB, back: RGB) -> float:
    hi, lo = sorted((_relative_luminance(fore), _relative_luminance(back)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable_ink(ink: RGB, background: RGB, minimum: float = 4.6) -> RGB:
    """Pull a hue toward black until it clears `minimum` on `background`.

    The still-water field is pale, so the bright menu hues that were written
    for a dark console disappear on it. The hue stays; only the lightness
    moves. Black, and any ink that already clears the floor, are left alone.
    """
    if contrast_ratio(ink, background) >= minimum or ink == (0, 0, 0):
        return ink
    lo, hi = 0.0, 1.0
    best: RGB = (0, 0, 0)
    for _ in range(30):
        mid = (lo + hi) / 2.0
        cand = tuple(max(0, min(255, int(round(channel * mid)))) for channel in ink)
        if contrast_ratio(cand, background) >= minimum:
            best = cand
            lo = mid
        else:
            hi = mid
    return best


def theme_background(name: Optional[str]) -> RGB:
    """Console pane for a theme name: the same field the glass paints."""
    return THEME_BACKGROUNDS.get(normalize_theme_name(name), DEFAULT_BACKGROUND)



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


# Console pane per theme. Each value is glass_field_rgb(), the still-water
# colour MP Gui and MP Liquid Player already show. Mix_Archive_Manager.sh
# mirrors this table in its THEME_BG case. The glass hosts themselves stay
# transparent: OSC 11 would paint over the shader.
THEME_BACKGROUNDS: Dict[str, RGB] = {
    name: glass_field_rgb(name) for name in _CONSOLE_THEME_IDS
}
DEFAULT_BACKGROUND: RGB = glass_field_rgb(DEFAULT_THEME)


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
        "sensorium",
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
