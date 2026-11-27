#!/usr/bin/env python3
"""
MP Mix Manager - Studio & DJ Booth Mobile Web Companion
Lightweight, standalone HTTP & REST server providing a modern, theme-adaptive
web dashboard accessible from phones, tablets, and remote browsers on the local LAN.
Displays live mix playback, tracklists, EQ visualizers, and remote playback controls.
Automatically synchronizes with the active theme currently running in Manager.
"""

import argparse
import errno
import glob
import hashlib
import html
import http.server
import json
import math
import os
import random
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import mp_companion_security as sec
except ImportError:  # when imported as scripts.mix_web_companion
    from scripts import mp_companion_security as sec

PORT = 8888

def _get_app_version() -> str:
    ver = os.environ.get("VERSION")
    if ver:
        return ver.strip()
    s_dir = Path(__file__).resolve().parent
    for cand in [s_dir / "VERSION", s_dir.parent / "VERSION"]:
        if cand.is_file():
            try:
                return cand.read_text(encoding="utf-8").strip()
            except Exception:
                pass
    return "0.5.4 Stable (Sensorium)"

VERSION = _get_app_version()

# ------------------------------------------------------------------------------
# THEME ENGINE — Palettes matching Mix_Archive_Manager.sh
# ------------------------------------------------------------------------------
THEMES: Dict[str, Dict[str, Any]] = {
    "dynamic_rainbow_liquid_glass": {
        "id": "dynamic_rainbow_liquid_glass",
        "name": "Dynamic Liquid Glass [Rainbow Colours] (New)",
        "bg": "#070614",
        "card_bg": "rgba(22, 16, 42, 0.70)",
        "card_border": "rgba(176, 88, 255, 0.45)",
        "card_shadow": "0 14px 40px rgba(0, 0, 0, 0.85)",
        "text_main": "#f3f0ff",
        "text_dim": "#a29bfe",
        "accent_primary": "#b058ff",     # Liquid Violet
        "accent_secondary": "#00f2fe",   # Aqua Cyan
        "accent_tertiary": "#ff2d8e",    # Liquid Rose
        "accent_crimson": "#ff0844",     # Rainbow Red
        "header_gradient": "linear-gradient(135deg, #b058ff 0%, #ff2d8e 35%, #fee140 70%, #00f2fe 100%)",
        "btn_gradient": "linear-gradient(135deg, #b058ff, #ff2d8e)",
        "btn_shadow": "0 4px 20px rgba(176, 88, 255, 0.55)",
        "progress_fill": "linear-gradient(90deg, #b058ff, #ff2d8e, #fee140, #00f2fe)",
        "badge_bg": "rgba(176, 88, 255, 0.20)",
        "badge_color": "#00f2fe",
        "eq_bar": "#00f2fe",
        "eq_peak": "#ff2d8e",
        "is_dynamic_rainbow": True,
    },
    "dreamworlds_liquid": {
        "id": "dreamworlds_liquid",
        "name": "MP Dreamworlds Liquid Transparency [Special Edition]",
        "bg": "#060b17",
        "card_bg": "rgba(11, 23, 44, 0.70)",
        "card_border": "rgba(0, 245, 255, 0.35)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.85)",
        "text_main": "#e2f8ff",
        "text_dim": "#70a6c2",
        "accent_primary": "#00f5ff",     # Crystalline Aqua Cyan
        "accent_secondary": "#c77dff",   # Liquid Amethyst
        "accent_tertiary": "#00f5d4",    # Seafoam Mint
        "accent_crimson": "#ff4b82",     # Liquid Coral
        "header_gradient": "linear-gradient(135deg, #00f5ff 0%, #c77dff 50%, #4899ef 100%)",
        "btn_gradient": "linear-gradient(135deg, #00f5ff, #c77dff)",
        "btn_shadow": "0 4px 18px rgba(0, 245, 255, 0.45)",
        "progress_fill": "linear-gradient(90deg, #4899ef, #00f5ff, #00f5d4, #c77dff)",
        "badge_bg": "rgba(0, 245, 255, 0.15)",
        "badge_color": "#00f5ff",
        "eq_bar": "#00f5ff",
        "eq_peak": "#c77dff",
    },
    "cosmic_supernova": {
        "id": "cosmic_supernova",
        "name": "Cosmic Supernova [Special Edition]",
        "bg": "#0a0814",
        "card_bg": "#14112c",
        "card_border": "rgba(224, 86, 253, 0.32)",
        "card_shadow": "0 12px 35px rgba(10, 8, 20, 0.85)",
        "text_main": "#e2e8f0",
        "text_dim": "#7c8ba1",
        "accent_primary": "#00f5d4",     # Aurora Aquamarine
        "accent_secondary": "#e056fd",   # Nebula Orchid
        "accent_tertiary": "#fee440",    # Solar Flare Gold
        "accent_crimson": "#ff3366",     # Supernova Crimson
        "header_gradient": "linear-gradient(135deg, #ff3366 0%, #e056fd 50%, #00f5d4 100%)",
        "btn_gradient": "linear-gradient(135deg, #e056fd, #ff3366)",
        "btn_shadow": "0 4px 18px rgba(224, 86, 253, 0.45)",
        "progress_fill": "linear-gradient(90deg, #5e60ce, #e056fd, #00f5d4)",
        "badge_bg": "rgba(0, 245, 212, 0.15)",
        "badge_color": "#00f5d4",
        "eq_bar": "#00f5d4",
        "eq_peak": "#ff3366",
    },
    "dreamworlds": {
        "id": "dreamworlds",
        "name": "MPlanetarian Dreamworlds",
        "bg": "#0d0f18",
        "card_bg": "#151928",
        "card_border": "rgba(247, 37, 133, 0.28)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.65)",
        "text_main": "#f0f3f6",
        "text_dim": "#7f8ba4",
        "accent_primary": "#f72585",     # Neon Pink
        "accent_secondary": "#00e5ff",   # Electric Cyan
        "accent_tertiary": "#ffaa00",    # Amber Gold
        "accent_crimson": "#ed254e",     # Coral Red
        "header_gradient": "linear-gradient(135deg, #f72585 0%, #00e5ff 100%)",
        "btn_gradient": "linear-gradient(135deg, #f72585, #ed254e)",
        "btn_shadow": "0 4px 18px rgba(247, 37, 133, 0.4)",
        "progress_fill": "linear-gradient(90deg, #f72585, #00e5ff)",
        "badge_bg": "rgba(0, 229, 255, 0.15)",
        "badge_color": "#00e5ff",
        "eq_bar": "#00e5ff",
        "eq_peak": "#f72585",
    },
    "dreamworlds_ultra": {
        "id": "dreamworlds_ultra",
        "name": "MP Mix Archive Manager (Dreamworlds Ultra)",
        "bg": "#554a6b",
        "card_bg": "#463b5c",
        "card_border": "rgba(166, 77, 255, 0.38)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.9)",
        "text_main": "#d8b4fe",
        "text_dim": "#9999ff",
        "accent_primary": "#a64dff",     # Hot Purple
        "accent_secondary": "#00ff00",   # Traffic Green
        "accent_tertiary": "#ff9933",    # Amber
        "accent_crimson": "#ff0000",     # Alert Red
        "header_gradient": "linear-gradient(135deg, #a64dff 0%, #00ff00 100%)",
        "btn_gradient": "linear-gradient(135deg, #a64dff, #ff0000)",
        "btn_shadow": "0 4px 18px rgba(166, 77, 255, 0.45)",
        "progress_fill": "linear-gradient(90deg, #00ff00, #ff9933, #ff0000)",
        "badge_bg": "rgba(0, 255, 0, 0.15)",
        "badge_color": "#00ff00",
        "eq_bar": "#00ff00",
        "eq_peak": "#ff0000",
    },
    "cyberpunk": {
        "id": "cyberpunk",
        "name": "Cyberpunk 2077",
        "bg": "#080812",
        "card_bg": "#121026",
        "card_border": "rgba(255, 0, 85, 0.35)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.8)",
        "text_main": "#e0ffff",
        "text_dim": "#708090",
        "accent_primary": "#ff0055",     # Neon Pink
        "accent_secondary": "#00ffff",   # Cyan
        "accent_tertiary": "#ffff00",    # Neon Yellow
        "accent_crimson": "#ff0055",
        "header_gradient": "linear-gradient(135deg, #ff0055 0%, #ffff00 50%, #00ffff 100%)",
        "btn_gradient": "linear-gradient(135deg, #ff0055, #ff00aa)",
        "btn_shadow": "0 4px 18px rgba(255, 0, 85, 0.5)",
        "progress_fill": "linear-gradient(90deg, #ff0055, #ffff00, #00ffff)",
        "badge_bg": "rgba(0, 255, 255, 0.15)",
        "badge_color": "#00ffff",
        "eq_bar": "#00ffff",
        "eq_peak": "#ff0055",
    },
    "dracula": {
        "id": "dracula",
        "name": "Dracula",
        "bg": "#282a36",
        "card_bg": "#343746",
        "card_border": "rgba(189, 147, 249, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.6)",
        "text_main": "#f8f8f2",
        "text_dim": "#6272a4",
        "accent_primary": "#bd93f9",     # Purple
        "accent_secondary": "#ff79c6",   # Pink
        "accent_tertiary": "#8be9fd",    # Cyan
        "accent_crimson": "#ff5555",
        "header_gradient": "linear-gradient(135deg, #bd93f9 0%, #ff79c6 100%)",
        "btn_gradient": "linear-gradient(135deg, #bd93f9, #ff79c6)",
        "btn_shadow": "0 4px 18px rgba(189, 147, 249, 0.4)",
        "progress_fill": "linear-gradient(90deg, #bd93f9, #ff79c6, #8be9fd)",
        "badge_bg": "rgba(139, 233, 253, 0.15)",
        "badge_color": "#8be9fd",
        "eq_bar": "#50fa7b",
        "eq_peak": "#ff79c6",
    },
    "nord": {
        "id": "nord",
        "name": "Nord Frost",
        "bg": "#242933",
        "card_bg": "#2e3440",
        "card_border": "rgba(136, 192, 208, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.5)",
        "text_main": "#eceff4",
        "text_dim": "#929cb0",
        "accent_primary": "#88c0d0",     # Frost Cyan
        "accent_secondary": "#81a1c1",   # Frost Blue
        "accent_tertiary": "#a3be8c",    # Aurora Green
        "accent_crimson": "#bf616a",
        "header_gradient": "linear-gradient(135deg, #88c0d0 0%, #81a1c1 100%)",
        "btn_gradient": "linear-gradient(135deg, #5e81ac, #88c0d0)",
        "btn_shadow": "0 4px 18px rgba(136, 192, 208, 0.35)",
        "progress_fill": "linear-gradient(90deg, #5e81ac, #88c0d0, #a3be8c)",
        "badge_bg": "rgba(136, 192, 208, 0.15)",
        "badge_color": "#88c0d0",
        "eq_bar": "#88c0d0",
        "eq_peak": "#bf616a",
    },
    "matrix": {
        "id": "matrix",
        "name": "The Matrix",
        "bg": "#070b07",
        "card_bg": "#0e180e",
        "card_border": "rgba(0, 255, 102, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 20, 0, 0.8)",
        "text_main": "#d4ffd4",
        "text_dim": "#448844",
        "accent_primary": "#00ff66",     # Neon Green
        "accent_secondary": "#a6ff00",   # Lime
        "accent_tertiary": "#00cc44",
        "accent_crimson": "#ff3333",
        "header_gradient": "linear-gradient(135deg, #00ff66 0%, #a6ff00 100%)",
        "btn_gradient": "linear-gradient(135deg, #008f39, #00ff66)",
        "btn_shadow": "0 4px 18px rgba(0, 255, 102, 0.4)",
        "progress_fill": "linear-gradient(90deg, #008f39, #00ff66)",
        "badge_bg": "rgba(0, 255, 102, 0.15)",
        "badge_color": "#00ff66",
        "eq_bar": "#00ff66",
        "eq_peak": "#a6ff00",
    },
    "solarized": {
        "id": "solarized",
        "name": "Solarized Dark",
        "bg": "#00212b",
        "card_bg": "#073642",
        "card_border": "rgba(42, 161, 152, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 15, 20, 0.7)",
        "text_main": "#93a1a1",
        "text_dim": "#657b83",
        "accent_primary": "#2aa198",     # Cyan
        "accent_secondary": "#268bd2",   # Blue
        "accent_tertiary": "#b58900",    # Yellow
        "accent_crimson": "#dc322f",
        "header_gradient": "linear-gradient(135deg, #2aa198 0%, #268bd2 100%)",
        "btn_gradient": "linear-gradient(135deg, #268bd2, #2aa198)",
        "btn_shadow": "0 4px 18px rgba(42, 161, 152, 0.35)",
        "progress_fill": "linear-gradient(90deg, #268bd2, #2aa198)",
        "badge_bg": "rgba(42, 161, 152, 0.15)",
        "badge_color": "#2aa198",
        "eq_bar": "#2aa198",
        "eq_peak": "#cb4b16",
    },
    "tokyo": {
        "id": "tokyo",
        "name": "Tokyo Night",
        "bg": "#1a1b26",
        "card_bg": "#24283b",
        "card_border": "rgba(122, 162, 247, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.6)",
        "text_main": "#c0caf5",
        "text_dim": "#565f89",
        "accent_primary": "#7aa2f7",     # Blue
        "accent_secondary": "#bb9af7",   # Magenta
        "accent_tertiary": "#7dcfff",    # Cyan
        "accent_crimson": "#f7768e",
        "header_gradient": "linear-gradient(135deg, #7aa2f7 0%, #bb9af7 100%)",
        "btn_gradient": "linear-gradient(135deg, #7aa2f7, #bb9af7)",
        "btn_shadow": "0 4px 18px rgba(122, 162, 247, 0.4)",
        "progress_fill": "linear-gradient(90deg, #7aa2f7, #bb9af7, #7dcfff)",
        "badge_bg": "rgba(125, 207, 255, 0.15)",
        "badge_color": "#7dcfff",
        "eq_bar": "#7aa2f7",
        "eq_peak": "#f7768e",
    },
    "monokai": {
        "id": "monokai",
        "name": "Monokai Pro",
        "bg": "#1e1f1c",
        "card_bg": "#272822",
        "card_border": "rgba(249, 38, 114, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.6)",
        "text_main": "#f8f8f2",
        "text_dim": "#75715e",
        "accent_primary": "#f92672",     # Pink
        "accent_secondary": "#66d9ef",   # Cyan
        "accent_tertiary": "#a6e22e",    # Green
        "accent_crimson": "#f92672",
        "header_gradient": "linear-gradient(135deg, #f92672 0%, #66d9ef 100%)",
        "btn_gradient": "linear-gradient(135deg, #f92672, #ae81ff)",
        "btn_shadow": "0 4px 18px rgba(249, 38, 114, 0.4)",
        "progress_fill": "linear-gradient(90deg, #f92672, #ae81ff, #66d9ef)",
        "badge_bg": "rgba(102, 217, 239, 0.15)",
        "badge_color": "#66d9ef",
        "eq_bar": "#a6e22e",
        "eq_peak": "#f92672",
    },
    "gruvbox": {
        "id": "gruvbox",
        "name": "Gruvbox Dark",
        "bg": "#1d2021",
        "card_bg": "#282828",
        "card_border": "rgba(250, 189, 47, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.6)",
        "text_main": "#ebdbb2",
        "text_dim": "#a89984",
        "accent_primary": "#fabd2f",     # Yellow
        "accent_secondary": "#fe8019",   # Orange
        "accent_tertiary": "#8ec07c",    # Aqua
        "accent_crimson": "#fb4934",
        "header_gradient": "linear-gradient(135deg, #fabd2f 0%, #fe8019 100%)",
        "btn_gradient": "linear-gradient(135deg, #d65d0e, #fabd2f)",
        "btn_shadow": "0 4px 18px rgba(250, 189, 47, 0.35)",
        "progress_fill": "linear-gradient(90deg, #d65d0e, #fabd2f, #8ec07c)",
        "badge_bg": "rgba(250, 189, 47, 0.15)",
        "badge_color": "#fabd2f",
        "eq_bar": "#fabd2f",
        "eq_peak": "#fb4934",
    },
    "emerald": {
        "id": "emerald",
        "name": "Emerald Isle",
        "bg": "#081410",
        "card_bg": "#10261e",
        "card_border": "rgba(46, 204, 113, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 20, 15, 0.7)",
        "text_main": "#e0f2ec",
        "text_dim": "#528c77",
        "accent_primary": "#2ecc71",     # Emerald
        "accent_secondary": "#4ee0b5",   # Mint
        "accent_tertiary": "#f1c40f",    # Gold
        "accent_crimson": "#e74c3c",
        "header_gradient": "linear-gradient(135deg, #2ecc71 0%, #4ee0b5 100%)",
        "btn_gradient": "linear-gradient(135deg, #27ae60, #2ecc71)",
        "btn_shadow": "0 4px 18px rgba(46, 204, 113, 0.4)",
        "progress_fill": "linear-gradient(90deg, #1abc9c, #2ecc71, #4ee0b5)",
        "badge_bg": "rgba(78, 224, 181, 0.15)",
        "badge_color": "#4ee0b5",
        "eq_bar": "#2ecc71",
        "eq_peak": "#f1c40f",
    },
    "classic": {
        "id": "classic",
        "name": "Classic ANSI",
        "bg": "#101010",
        "card_bg": "#1a1a1a",
        "card_border": "rgba(0, 205, 205, 0.3)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.8)",
        "text_main": "#e0e0e0",
        "text_dim": "#888888",
        "accent_primary": "#00cdcd",     # Cyan
        "accent_secondary": "#cd00cd",   # Magenta
        "accent_tertiary": "#00cd00",    # Green
        "accent_crimson": "#cd0000",
        "header_gradient": "linear-gradient(135deg, #00cdcd 0%, #cd00cd 100%)",
        "btn_gradient": "linear-gradient(135deg, #0077aa, #00cdcd)",
        "btn_shadow": "0 4px 18px rgba(0, 205, 205, 0.35)",
        "progress_fill": "linear-gradient(90deg, #0077aa, #00cdcd)",
        "badge_bg": "rgba(0, 205, 205, 0.15)",
        "badge_color": "#00cdcd",
        "eq_bar": "#00cdcd",
        "eq_peak": "#cd00cd",
    },
    "retro_terminal": {
        "id": "retro_terminal",
        "name": "Old Style Unix Terminal Retro Mode (White on Black)",
        "bg": "#000000",
        "card_bg": "#0a0a0a",
        "card_border": "rgba(255, 255, 255, 0.4)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.95)",
        "text_main": "#ffffff",
        "text_dim": "#aaaaaa",
        "accent_primary": "#ffffff",
        "accent_secondary": "#cccccc",
        "accent_tertiary": "#ffffff",
        "accent_crimson": "#ffffff",
        "header_gradient": "linear-gradient(135deg, #ffffff 0%, #aaaaaa 100%)",
        "btn_gradient": "linear-gradient(135deg, #333333, #111111)",
        "btn_shadow": "0 4px 18px rgba(255, 255, 255, 0.2)",
        "progress_fill": "linear-gradient(90deg, #666666, #ffffff)",
        "badge_bg": "rgba(255, 255, 255, 0.15)",
        "badge_color": "#ffffff",
        "eq_bar": "#ffffff",
        "eq_peak": "#ffffff",
    },
    "retro_linux_memories": {
        "id": "retro_linux_memories",
        "name": "Retro Linux Memories",
        "bg": "#1e2226",
        "card_bg": "rgba(45, 45, 45, 0.90)",
        "card_border": "#3a3f44",
        "card_shadow": "0 8px 24px rgba(0, 0, 0, 0.70)",
        "text_main": "#ffffff",
        "text_dim": "#919190",
        "accent_primary": "#15539e",     # VeraCrypt Selection Blue
        "accent_secondary": "#5b86bb",   # GTK Slate Highlight Blue
        "accent_tertiary": "#f0ad4e",    # Amber Title Accent
        "accent_crimson": "#d9534f",     # GTK Classic Crimson
        "header_gradient": "linear-gradient(135deg, #15539e 0%, #2d3058 50%, #5b86bb 100%)",
        "btn_gradient": "linear-gradient(180deg, #3c3c3c 0%, #2a2a2a 100%)",
        "btn_shadow": "0 2px 8px rgba(0, 0, 0, 0.60)",
        "progress_fill": "linear-gradient(90deg, #15539e, #1b6acb, #5b86bb)",
        "badge_bg": "rgba(21, 83, 158, 0.25)",
        "badge_color": "#5b86bb",
        "eq_bar": "#15539e",
        "eq_peak": "#5b86bb",
    },
    "into_darkness_void_out": {
        "id": "into_darkness_void_out",
        "name": "Into Darkness (Void Out)",
        "bg": "#000000",
        "card_bg": "#000000",
        "card_border": "#303030",
        "card_shadow": "0 8px 24px rgba(0, 0, 0, 0.95)",
        "text_main": "#ffffff",
        "text_dim": "#a0a0a0",
        "accent_primary": "#ffffff",
        "accent_secondary": "#c8c8c8",
        "accent_tertiary": "#ffffff",
        "accent_crimson": "#ffffff",
        "header_gradient": "linear-gradient(135deg, #000000 0%, #080808 100%)",
        "btn_gradient": "linear-gradient(135deg, #181818, #000000)",
        "btn_shadow": "0 2px 8px rgba(0, 0, 0, 0.85)",
        "progress_fill": "linear-gradient(90deg, #555555, #ffffff)",
        "badge_bg": "rgba(255, 255, 255, 0.12)",
        "badge_color": "#ffffff",
        "eq_bar": "#c8c8c8",
        "eq_peak": "#ffffff",
    },
    "sensorium": {
        "id": "sensorium",
        "name": "Sensorium",
        "bg": "#f08080",
        "card_bg": "rgba(255, 245, 240, 0.90)",
        "card_border": "rgba(43, 10, 16, 0.40)",
        "card_shadow": "0 14px 45px rgba(43, 10, 16, 0.30), 0 0 25px rgba(255, 122, 24, 0.18)",
        "text_main": "#2b0a10",
        "text_dim": "#52242c",
        "accent_primary": "#5c2400",
        "accent_secondary": "#263d00",
        "accent_tertiary": "#0b2f6b",
        "accent_crimson": "#5e140f",
        "header_gradient": "linear-gradient(135deg, #5c2400 0%, #263d00 50%, #0b2f6b 100%)",
        "btn_gradient": "linear-gradient(135deg, #ff7a18, #c8ff3d)",
        "btn_shadow": "0 4px 18px rgba(92, 36, 0, 0.35)",
        "progress_fill": "linear-gradient(90deg, #5c2400, #263d00, #0b2f6b)",
        "badge_bg": "rgba(43, 10, 16, 0.12)",
        "badge_color": "#2b0a10",
        "eq_bar": "#5c2400",
        "eq_peak": "#263d00",
    },
    "ms_windows_95_gui": {
        "id": "ms_windows_95_gui",
        "name": "MS Windows 95 GUI (Nostalgia Theme)",
        # The Win95 desktop teal behind the silver window face, black
        # WindowText on the face, and the classic 3D bevel borders.
        "bg": "#008080",
        "card_bg": "#c0c0c0",
        "card_border": "#808080",
        "card_shadow": "inset -1px -1px 0 #000000, inset 1px 1px 0 #dfdfdf, 0 2px 6px rgba(0, 0, 0, 0.55)",
        "text_main": "#000000",
        "text_dim": "#404040",
        "accent_primary": "#000080",     # Active Title Bar navy
        "accent_secondary": "#008080",   # Desktop teal
        "accent_tertiary": "#808080",    # Button shadow grey
        "accent_crimson": "#800000",     # Classic dark red
        "header_gradient": "linear-gradient(90deg, #000080 0%, #1084d0 100%)",
        "btn_gradient": "linear-gradient(180deg, #dfdfdf 0%, #c0c0c0 100%)",
        "btn_shadow": "inset -1px -1px 0 #000000, inset 1px 1px 0 #ffffff",
        "progress_fill": "linear-gradient(90deg, #000080, #008080)",
        "badge_bg": "#000080",
        "badge_color": "#ffffff",
        "eq_bar": "#000080",
        "eq_peak": "#008080",
    }
}

THEME_ALIASES: Dict[str, str] = {
    "sensorium": "sensorium",
    "sensorium_interface": "sensorium",
    "sensorium interface": "sensorium",
    "dynamic_rainbow_liquid_glass": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_rainbow": "dynamic_rainbow_liquid_glass",
    "dynamic_rainbow": "dynamic_rainbow_liquid_glass",
    "rainbow_liquid_glass": "dynamic_rainbow_liquid_glass",
    "rainbow": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_[rainbow_colours]_(new)": "dynamic_rainbow_liquid_glass",
    "dynamic_liquid_glass_[rainbow_colours]": "dynamic_rainbow_liquid_glass",
    "dynamic liquid glass [rainbow colours] (new)": "dynamic_rainbow_liquid_glass",
    "dynamic liquid glass [rainbow colours]": "dynamic_rainbow_liquid_glass",
    "mp_dreamworlds_liquid_transparency": "dreamworlds_liquid",
    "dreamworlds_liquid_transparency": "dreamworlds_liquid",
    "liquid_transparency": "dreamworlds_liquid",
    "liquid": "dreamworlds_liquid",
    "glass": "dreamworlds_liquid",
    "liquid_glass": "dreamworlds_liquid",
    "mplanetarian_dreamworlds": "dreamworlds",
    "mplanetarian": "dreamworlds",
    "default": "dreamworlds_ultra",
    "ultra": "dreamworlds_ultra",
    "dreamworlds ultra": "dreamworlds_ultra",
    "dreamworlds_ultra_mp_mix_manager": "dreamworlds_ultra",
    "mp mix archive manager (dreamworlds ultra)": "dreamworlds_ultra",
    "cosmic": "cosmic_supernova",
    "supernova": "cosmic_supernova",
    "cosmic supernova": "cosmic_supernova",
    "cosmic supernova [special]": "cosmic_supernova",
    "cosmic supernova [special edition]": "cosmic_supernova",
    "cosmic_supernova_mp_mix_manager": "cosmic_supernova",
    "the matrix": "matrix",
    "tokyo night": "tokyo",
    "solarized dark": "solarized",
    "monokai pro": "monokai",
    "gruvbox dark": "gruvbox",
    "emerald isle": "emerald",
    "classic ansi": "classic",
    "retro_terminal": "retro_terminal",
    "unix_terminal": "retro_terminal",
    "retro_unix": "retro_terminal",
    "old_style_unix": "retro_terminal",
    "white_on_black": "retro_terminal",
    "retro": "retro_terminal",
    "terminal": "retro_terminal",
    "vt100": "retro_terminal",
    "old style unix terminal retro mode (white on black)": "retro_terminal",
    "old style unix terminal": "retro_terminal",
    "retro_linux_memories": "retro_linux_memories",
    "into_darkness_void_out": "into_darkness_void_out",
    "into darkness (void out)": "into_darkness_void_out",
    "into darkness": "into_darkness_void_out",
    "void out": "into_darkness_void_out",
    "retro_linux": "retro_linux_memories",
    "linux_memories": "retro_linux_memories",
    "retro-linux-memories": "retro_linux_memories",
    "retro-linux": "retro_linux_memories",
    "retro linux memories": "retro_linux_memories",
    "retro linux": "retro_linux_memories",
    "veracrypt": "retro_linux_memories",
    "retrolinux": "retro_linux_memories",
    "linux": "retro_linux_memories",
    "ms_windows_95_gui": "ms_windows_95_gui",
    "ms_windows_95": "ms_windows_95_gui",
    "ms windows 95 gui": "ms_windows_95_gui",
    "ms windows 95 gui (nostalgia theme)": "ms_windows_95_gui",
    "ms windows 95": "ms_windows_95_gui",
    "windows_95": "ms_windows_95_gui",
    "windows 95": "ms_windows_95_gui",
    "win95": "ms_windows_95_gui",
    "nostalgia": "ms_windows_95_gui",
}

CLI_THEME: Optional[str] = None
WEB_THEME_FILE = Path.home() / ".config" / "mix-manager" / "web_companion_theme"
_tracklist_cache: Dict[str, str] = {}

# Every server launch gets a fresh id. A dashboard that sees it change knows
# the companion was restarted and refreshes so the phone resumes the stream
# without a manual reload. The tray also posts a notice to /api/announce just
# before it stops the service, so phones see why they are reconnecting.
INSTANCE_ID = f"{int(time.time())}-{os.getpid()}"
_announce_message: Optional[str] = None
_announce_at = 0.0
_announce_lock = threading.Lock()
ANNOUNCE_TTL_SECONDS = 45.0
ANNOUNCE_MESSAGE_DEFAULT = "MP Audio Player (Web) has been restarted"


def set_announcement(message: str) -> Optional[Dict[str, Any]]:
    """Queue a one-shot notice for every connected dashboard."""
    text = (message or "").strip()
    if not text:
        return None
    global _announce_message, _announce_at
    with _announce_lock:
        _announce_message = text
        _announce_at = time.time()
    return {"message": text, "instance": INSTANCE_ID}


def current_announcement() -> Optional[Dict[str, Any]]:
    with _announce_lock:
        if not _announce_message:
            return None
        if (time.time() - _announce_at) > ANNOUNCE_TTL_SECONDS:
            return None
        return {"message": _announce_message, "at": round(_announce_at, 3)}


def get_base_dir() -> Path:
    p = Path(__file__).resolve()
    return p.parent.parent if p.parent.name == "scripts" else p.parent


def normalize_theme_id(value: Any) -> Optional[str]:
    """Map a theme name, alias, or id to a real palette id."""
    if not value:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    if key in THEME_ALIASES:
        return THEME_ALIASES[key]
    if key in THEMES:
        return key
    # Accept a human-readable palette name too, e.g. "Retro Linux Memories".
    for tid, palette in THEMES.items():
        if str(palette.get("name", "")).strip().lower() == key:
            return tid
    return None


def get_web_theme_override() -> Optional[str]:
    """Theme chosen from the web companion UI. It wins over the manager theme."""
    if WEB_THEME_FILE.is_file():
        try:
            return normalize_theme_id(WEB_THEME_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return None


def set_web_theme(theme_id: str) -> Optional[str]:
    tid = normalize_theme_id(theme_id)
    if not tid:
        return None
    try:
        WEB_THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
        WEB_THEME_FILE.write_text(tid + "\n", encoding="utf-8")
    except Exception:
        return None
    return tid


def available_themes() -> List[Dict[str, str]]:
    return [
        {"id": tid, "name": str(palette.get("name", tid))}
        for tid, palette in THEMES.items()
    ]


def get_current_theme_id(cli_theme: Optional[str] = None) -> str:
    if cli_theme:
        k = cli_theme.strip().lower()
        if k in THEME_ALIASES: return THEME_ALIASES[k]
        if k in THEMES: return k

    override = get_web_theme_override()
    if override:
        return override

    env_theme = os.environ.get("CURRENT_THEME", "").strip().lower()
    if env_theme:
        if env_theme in THEME_ALIASES: return THEME_ALIASES[env_theme]
        if env_theme in THEMES: return env_theme

    theme_file = Path.home() / ".config" / "mix-manager" / "theme"
    if theme_file.is_file():
        try:
            saved = theme_file.read_text(encoding="utf-8").strip().lower()
            if saved in THEME_ALIASES: return THEME_ALIASES[saved]
            if saved in THEMES: return saved
        except Exception:
            pass

    return "dreamworlds_ultra" if "dreamworlds_ultra" in THEMES else "dreamworlds"


def get_theme_palette(cli_theme: Optional[str] = None) -> Dict[str, Any]:
    tid = get_current_theme_id(cli_theme)
    return THEMES.get(tid, THEMES.get("dreamworlds_ultra", THEMES["dreamworlds"]))


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


# ------------------------------------------------------------------------------
# DESKTOP LAUNCH PAD (Linux)
#
# The dashboard runs as a user service, so the phone can be the only thing in
# front of the user. These buttons start the installed desktop apps, show
# whether each app is already live, and close them all without stopping the
# tray icon or this companion.
# ------------------------------------------------------------------------------
try:
    import mp_suite_control as suite_control
except ImportError:  # The companion is also importable from the repo root.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mp_suite_control as suite_control  # type: ignore[no-redef]
    except ImportError:
        suite_control = None  # type: ignore[assignment]

# The same playlist reader the player and the exporter use. A playlist the
# player accepts (M3U, M3U8, XSPF, PLS or JSON) is a playlist this page can
# stream, without a second parser that only knows two of the five types.
try:
    import playlist_formats as _playlists
except ImportError:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import playlist_formats as _playlists  # type: ignore[no-redef]
    except ImportError:
        _playlists = None  # type: ignore[assignment]


def launch_pad_status() -> Dict[str, Any]:
    if suite_control is None:
        return {"supported": False, "apps": [], "tray_running": False}
    try:
        return suite_control.app_status()
    except Exception as exc:
        return {"supported": False, "apps": [], "tray_running": False, "detail": str(exc)}


def launch_pad_action(role: str) -> Dict[str, Any]:
    if suite_control is None:
        return {"success": False, "action": "unsupported", "app": role}
    try:
        return suite_control.launch_app(role)
    except Exception as exc:
        return {"success": False, "action": "error", "app": role, "detail": str(exc)}


def launch_pad_close_all(wait: bool = True) -> Dict[str, Any]:
    if suite_control is None:
        return {"success": False, "action": "unsupported", "remaining": []}
    try:
        try:
            return suite_control.close_all_apps(wait=wait)
        except TypeError:
            # Older companions take no wait argument and close in the request.
            return suite_control.close_all_apps()
    except Exception as exc:
        return {"success": False, "action": "error", "remaining": [], "detail": str(exc)}


def launch_pad_visualizer() -> Dict[str, Any]:
    """Open, fullscreen, or close the one Web Companion visualizer window."""
    if suite_control is None:
        return {"success": False, "action": "unsupported"}
    try:
        return suite_control.web_visualizer_action()
    except Exception as exc:
        return {"success": False, "action": "error", "detail": str(exc)}


# ------------------------------------------------------------------------------
# STREAMING SERVICE STATE
#
# This process is an HTTP streamer only. It deliberately contains no IPC to a
# local audio player, because it must never cause sound on the host machine.
# ------------------------------------------------------------------------------
CONFIG_DIR = Path.home() / ".config" / "mix-manager"

try:
    from audio_formats import AUDIO_EXTENSIONS, AUDIO_MIME_TYPES
except ImportError:  # imported as scripts.mix_web_companion
    from scripts.audio_formats import AUDIO_EXTENSIONS, AUDIO_MIME_TYPES
AUDIO_CONTENT_TYPES = dict(AUDIO_MIME_TYPES)
RESUME_FILE = CONFIG_DIR / "playback_resume.json"
PLAYLIST_DIR = CONFIG_DIR / "playlists"
MUSIC_FOLDERS_FILE = CONFIG_DIR / "music_folders.json"
WEB_MODE_FILE = CONFIG_DIR / "web_companion_mode"

# Network containment. The dashboard is reachable only from the addresses in
# `COMPANION_CIDRS` when the operator sets one; an empty list allows any device
# that can connect. There is no password or access token to type — opening the
# URL is enough, and the optional CIDR list is how a machine on an untrusted
# network is restricted.
COMPANION_CIDRS: List[Any] = []
# Requests that change state must carry this header. A cross-origin form
# cannot set a custom header without a preflight, and this server answers no
# preflight, so this is what stops another web site from driving the controls.
CSRF_HEADER = sec.REQUIRED_HEADER

# The MP Audio Player daemon that is actually making sound on this machine.
# The companion never starts one and never opens an audio device; it only
# *reads* what that player is doing (and forwards remote-control taps to it when
# it is already running). Those files are the player's own contract.
PLAYER_SOCKET_PATH = CONFIG_DIR / "mp_audio_player.sock"
PLAYER_PORT_FILE = CONFIG_DIR / "mp_audio_player.port"
PLAYER_STATE_FILE = CONFIG_DIR / "mp_player_state.json"
PLAYER_PID_FILE = CONFIG_DIR / "mp_player.pid"

# Same libraries the MP Audio Player switches between with [1], [2] and [3].
LIBRARY_MODE_MIX = "MIX_ARCHIVE"
LIBRARY_MODE_MUSIC = "MUSIC"
LIBRARY_MODE_FAVORITES = "FAVORITES"
MODE_LABELS = {
    LIBRARY_MODE_MIX: "Mix Archive",
    LIBRARY_MODE_MUSIC: "Music",
    LIBRARY_MODE_FAVORITES: "Favorites",
}
# The Favorites playlist is shared with the MP Audio Player. The desk marks a
# track with [H]; this page marks one with the heart. Both write this file.
FAVORITES_FILE = CONFIG_DIR / "favorites.json"
_favorites_lock = threading.Lock()
# mtime, size -> keys. The status poll asks on every tick; the file is tiny
# but it lives on the config dir and does not need to be re-parsed each time.
_favorite_cache: Dict[str, Any] = {"stamp": object(), "keys": frozenset()}

_autoplay_done = threading.Event()
_autoplay_result: Dict[str, Any] = {"status": "pending", "detail": ""}

# The resolved stream queue is expensive to build for a large music library, so
# it is cached per mode for a few seconds while the dashboard polls.
QUEUE_CACHE_TTL = 10.0
# {"mode": str, "built_at": float, "files": tuple[str, ...]}
#
# A dict rather than a tuple: callers and tests need to read and reset the
# fields by name, and a positional tuple silently changed meaning the last time
# a field was added.
_queue_cache: Optional[Dict[str, Any]] = None
_queue_source_state: Dict[str, Any] = {"fallback": False, "detail": "", "music_offline": False}
# Resolving the queue can take seconds (a 51k-entry music playlist), and the
# dashboard polls hard. Only one thread may scan; the rest wait and then read
# the fresh cache instead of starting their own scan.
_queue_build_lock = threading.Lock()


# ------------------------------------------------------------------------------
# MP HARMONY ASSISTANT (the "MP Assistant" tab)
#
# The voice agent listens on 127.0.0.1:11435, so a phone on the LAN cannot talk
# to it directly. The dashboard therefore chats through this companion, which
# proxies the OpenAI-compatible stream, the speech endpoint and the voice
# switch. Nothing here opens a sound device: the phone plays the speech.
#
# The default voice is Stephen Hawking. The choice is only overridden when the
# user types "/voice <name>" in the assistant prompt, which persists to both
# this dashboard and ~/.harmony_voice (through the agent's /api/settings).
# ------------------------------------------------------------------------------
HARMONY_AGENT_HOST = os.environ.get("HARMONY_AGENT_HOST", "127.0.0.1")
HARMONY_DEFAULT_VOICE = "stephen_hawking_matrix"
HARMONY_VOICE_FILE = Path.home() / ".harmony_voice"
WEB_ASSISTANT_VOICE_FILE = CONFIG_DIR / "web_assistant_voice.json"
_HARMONY_AGENT_PORT_ENV = os.environ.get("HARMONY_AGENT_PORT", "").strip()

# Mirrors VOICES_MAP in MP_Harmony_Agent.py, so the picker and /voice list the
# same ids the agent can synthesize.
HARMONY_VOICES: Dict[str, str] = {
    "stephen_hawking_matrix": "Stephen Hawking (Enters the Matrix) [Default]",
    "morpheus": "Morpheus (The Matrix - Master)",
    "morpheus_construct": "Morpheus (The Construct - 'What is real?')",
    "morpheus_redpill": "Morpheus (Red Pill - 'Rabbit Hole')",
    "morpheus_dojo": "Morpheus (Combat Dojo - 'Free your mind')",
    "morpheus_system": "Morpheus (The System - 'Our enemy')",
    "agent_smith": "Agent Smith (The Matrix)",
    "neo": "Neo (The Matrix)",
    "trinity": "Trinity (The Matrix)",
    "oracle": "The Oracle (The Matrix)",
    "zion": "Zion (Councillor Hamann)",
}
HARMONY_VOICE_ALIASES: Dict[str, str] = {
    "smith": "agent_smith",
    "hawking": "stephen_hawking_matrix",
    "stephen_hawking": "stephen_hawking_matrix",
    "stephen": "stephen_hawking_matrix",
    "default": HARMONY_DEFAULT_VOICE,
    "master": "morpheus",
    "construct": "morpheus_construct",
    "redpill": "morpheus_redpill",
    "dojo": "morpheus_dojo",
    "system": "morpheus_system",
    "the_oracle": "oracle",
    "hamann": "zion",
}

_HARMONY_STATUS_CACHE: Dict[str, Any] = {"at": 0.0, "payload": None}
_HARMONY_STATUS_LOCK = threading.Lock()
_HARMONY_STATUS_TTL = 4.0

# Replies are replayed often (tap the speaker twice, rotate the phone). Keeping
# the last few clips avoids re-synthesizing the same paragraph each time.
_SPEECH_CACHE: Dict[Tuple[str, str, float], bytes] = {}
_SPEECH_CACHE_LOCK = threading.Lock()
_SPEECH_CACHE_MAX = 8


def harmony_agent_url() -> str:
    return f"http://{HARMONY_AGENT_HOST}:{_harmony_agent_port()}"


# Conversation limits for the proxied agent. The web dashboard is a public
# surface on the LAN, so the payload is bounded and the roles are restricted:
# a client-supplied `system` message would otherwise replace the agent's
# instructions, and the agent holds shell tools.
AGENT_MAX_MESSAGES = 24
AGENT_MAX_CONTENT_CHARS = 16384
AGENT_ALLOWED_ROLES = ("user", "assistant")


def sanitize_agent_messages(messages: Any) -> List[Dict[str, str]]:
    """Keep only well-formed user/assistant turns, bounded in size and count.

    Anything else - a `system` role, a nested object, a 10 MB string - is
    dropped rather than forwarded, so a malicious client cannot use the
    conversation body to steer the agent or to burn memory. The *last* user
    message always survives when it is the only one, because that is what the
    `/voice` command parser and the model both need.
    """
    if not isinstance(messages, list):
        return []
    cleaned: List[Dict[str, str]] = []
    for message in messages[-AGENT_MAX_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in AGENT_ALLOWED_ROLES:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content[:AGENT_MAX_CONTENT_CHARS]})
    return cleaned


def _harmony_agent_port() -> int:
    """Resolve the agent port lazily; config.env is read after import."""
    for candidate in (_HARMONY_AGENT_PORT_ENV, _config_value("HARMONY_AGENT_PORT", "")):
        if str(candidate or "").strip().isdigit():
            return int(str(candidate).strip())
    return 11435


def _harmony_request(path: str, payload: Optional[Dict[str, Any]] = None,
                     timeout: float = 6.0, method: Optional[str] = None):
    """Open a request against the Harmony agent. Callers own the response."""
    url = harmony_agent_url() + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method or ("POST" if data else "GET"))
    return urllib.request.urlopen(request, timeout=timeout)


def normalize_harmony_voice(value: Any) -> Optional[str]:
    if not value:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    key = HARMONY_VOICE_ALIASES.get(key, key)
    if key in HARMONY_VOICES:
        return key
    for vid in HARMONY_VOICES:
        if key in vid:
            return vid
    return None


def harmony_voice_label(voice: str) -> str:
    return HARMONY_VOICES.get(voice, voice)


def load_web_assistant_voice() -> Optional[str]:
    """The voice picked with /voice in this dashboard, if any."""
    try:
        payload = json.loads(WEB_ASSISTANT_VOICE_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            voice = normalize_harmony_voice(payload.get("voice"))
            if voice:
                return voice
    except (OSError, ValueError):
        pass
    return None


def save_web_assistant_voice(voice: str) -> bool:
    try:
        WEB_ASSISTANT_VOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WEB_ASSISTANT_VOICE_FILE.write_text(
            json.dumps({"voice": voice}, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except OSError:
        return False


def harmony_health() -> Optional[Dict[str, Any]]:
    """The agent's /health payload, or None when it is not answering."""
    try:
        with _harmony_request("/health", timeout=2.5) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
            return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ensure_harmony_default_voice(health: Optional[Dict[str, Any]]) -> Optional[str]:
    """Speak as Stephen Hawking until the user chooses another voice with /voice.

    Priority:
      1. a voice chosen with /voice in this dashboard (persisted here),
      2. a voice the user set in the agent with /voice (shared file/environment),
      3. Stephen Hawking.
    The agent is nudged to the default only when nothing has been chosen, so a
    fresh install speaks in the Hawking voice on first use.
    """
    stored = load_web_assistant_voice()
    if stored:
        return stored
    chosen = normalize_harmony_voice(_read_agent_voice_file())
    if chosen:
        return chosen
    current = normalize_harmony_voice((health or {}).get("active_voice"))
    if current == HARMONY_DEFAULT_VOICE:
        return current
    if set_harmony_voice(HARMONY_DEFAULT_VOICE, remember=False):
        return HARMONY_DEFAULT_VOICE
    return current or HARMONY_DEFAULT_VOICE


def _read_agent_voice_file() -> Optional[str]:
    """The voice the agent itself remembers (~/.harmony_voice)."""
    try:
        value = HARMONY_VOICE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def set_harmony_voice(voice: Any, remember: bool = True) -> Optional[str]:
    """Switch the agent voice (and optionally remember it for this dashboard)."""
    target = normalize_harmony_voice(voice)
    if not target:
        return None
    try:
        with _harmony_request("/api/settings", {"voice": target}, timeout=10.0) as response:
            if response.status != 200:
                return None
            response.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if remember:
        save_web_assistant_voice(target)
    return target


def harmony_voice_speed() -> float:
    """Speech speed multiplier shared with the agent (~/.harmony_voice_speed)."""
    try:
        value = float(Path.home().joinpath(".harmony_voice_speed").read_text(encoding="utf-8").strip())
        return max(0.5, min(3.0, value))
    except (OSError, ValueError):
        return 1.0


def set_harmony_voice_speed(speed: Any) -> Optional[float]:
    try:
        value = round(max(0.5, min(3.0, float(speed))), 2)
    except (TypeError, ValueError):
        return None
    try:
        Path.home().joinpath(".harmony_voice_speed").write_text(f"{value}\n", encoding="utf-8")
        return value
    except OSError:
        return None


def harmony_status(force: bool = False) -> Dict[str, Any]:
    """Availability plus the active voice, cached for a few seconds."""
    now = time.time()
    with _HARMONY_STATUS_LOCK:
        cached = _HARMONY_STATUS_CACHE.get("payload")
        if cached and not force and (now - float(_HARMONY_STATUS_CACHE.get("at") or 0)) < _HARMONY_STATUS_TTL:
            return cached

    health = harmony_health()
    available = health is not None
    voice = HARMONY_DEFAULT_VOICE
    if available:
        voice = ensure_harmony_default_voice(health) or HARMONY_DEFAULT_VOICE
    else:
        voice = load_web_assistant_voice() or HARMONY_DEFAULT_VOICE
    payload = {
        "available": available,
        "agent_url": harmony_agent_url(),
        "agent": (health or {}).get("agent", "MP Harmony AI Agent"),
        "model": (health or {}).get("model", ""),
        "voice": voice,
        "voice_label": harmony_voice_label(voice),
        "voice_speed": harmony_voice_speed(),
        "default_voice": HARMONY_DEFAULT_VOICE,
        "voices": [{"id": vid, "label": label} for vid, label in HARMONY_VOICES.items()],
        "detail": "" if available else (
            "The MP Harmony Agent is not answering on "
            f"{harmony_agent_url()}. Start it from the button above or the manager."
        ),
    }
    with _HARMONY_STATUS_LOCK:
        _HARMONY_STATUS_CACHE.update({"at": now, "payload": payload})
    return payload


def start_harmony_agent() -> Dict[str, Any]:
    """Start the agent in the background from the dashboard's Start button."""
    script = None
    for candidate in (
        get_base_dir() / "scripts" / "manage_harmony_agent.sh",
        get_base_dir() / "manage_harmony_agent.sh",
        Path(__file__).resolve().parent / "manage_harmony_agent.sh",
    ):
        if candidate.is_file():
            script = candidate
            break
    if script is None:
        return {"success": False, "detail": "manage_harmony_agent.sh was not found."}
    try:
        subprocess.Popen(
            ["bash", str(script), "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return {"success": False, "detail": str(exc)}
    # The model load can take a while, so report "starting" and let the tab poll.
    return {"success": True, "status": "starting", "detail": "Starting the MP Harmony Agent…"}


def _fallback_hawking_wav(text: str, speed: float = 1.0) -> bytes:
    """espeak-ng Hawking profile, used when the agent's TTS endpoint is down."""
    clean = re.sub(r"```.*?```", " code block omitted ", text or "", flags=re.DOTALL)
    clean = re.sub(r"[*_#`>]+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return b""
    espeak = shutil_which("espeak-ng") or shutil_which("espeak")
    if not espeak:
        return b""
    raw_path = out_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as raw_f:
            raw_path = raw_f.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
            out_path = out_f.name
        subprocess.run(
            [espeak, "-v", "en-us+m3", "-p", "19",
             "-s", str(int(110 * max(0.5, min(3.0, speed)))),
             clean[:1200], "-w", raw_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
        )
        final = raw_path
        ffmpeg = shutil_which("ffmpeg")
        if ffmpeg:
            result = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-i", raw_path,
                 "-af", "bass=g=12:f=95:w=0.6,equalizer=f=2400:t=q:w=1.2:g=2,"
                        "aecho=0.8:0.7:50|100:0.3|0.2",
                 out_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
            )
            if result.returncode == 0 and os.path.getsize(out_path) > 44:
                final = out_path
        with open(final, "rb") as handle:
            return handle.read()
    except (OSError, subprocess.SubprocessError):
        return b""
    finally:
        for path in (raw_path, out_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def shutil_which(name: str) -> Optional[str]:
    return shutil.which(name)


def harmony_speech(text: str, voice: Optional[str] = None,
                   speed: Optional[float] = None) -> bytes:
    """Speech bytes for the reply. Falls back to local espeak-ng for Hawking."""
    spoken = (text or "").strip()
    if not spoken:
        return b""
    target = normalize_harmony_voice(voice) or (
        load_web_assistant_voice() or HARMONY_DEFAULT_VOICE
    )
    rate = speed if isinstance(speed, (int, float)) else harmony_voice_speed()
    key = (spoken, target, round(float(rate), 2))
    with _SPEECH_CACHE_LOCK:
        cached = _SPEECH_CACHE.get(key)
    if cached:
        return cached
    audio = b""
    try:
        with _harmony_request(
            "/v1/audio/speech",
            {"input": spoken, "voice": target, "speed": float(rate)},
            timeout=180.0,
        ) as response:
            if response.status == 200:
                audio = response.read()
                if audio and len(audio) <= 100:
                    audio = b""
    except (urllib.error.URLError, OSError, ValueError):
        audio = b""
    if not audio and target == HARMONY_DEFAULT_VOICE:
        audio = _fallback_hawking_wav(spoken, float(rate))
    if audio:
        with _SPEECH_CACHE_LOCK:
            # A phone replays the same bubble often; keep the last few clips.
            while len(_SPEECH_CACHE) >= _SPEECH_CACHE_MAX:
                _SPEECH_CACHE.pop(next(iter(_SPEECH_CACHE)))
            _SPEECH_CACHE[key] = audio
    return audio


def _voice_command_reply(text: str) -> Optional[str]:
    """/voice handling for the web prompt, matching the agent's CLI command."""
    stripped = (text or "").strip()
    if not stripped.lower().startswith("/voice"):
        return None
    arg = stripped[6:].strip()
    low = arg.lower()
    if not arg:
        lines = [
            "MP Harmony voice presets — type /voice <id> to switch:",
            "",
        ]
        current = harmony_status(force=True).get("voice", HARMONY_DEFAULT_VOICE)
        for vid, label in HARMONY_VOICES.items():
            lines.append(f"  {'● ' if vid == current else '  '}{vid} — {label}")
        lines.append("")
        lines.append("Also: /voice speed <0.5-3.0> and /voice test")
        return "\n".join(lines)
    if low.startswith("speed"):
        parts = arg.split(maxsplit=1)
        if len(parts) > 1:
            value = set_harmony_voice_speed(parts[1])
            if value is None:
                return "Voice speed must be a number between 0.5 and 3.0."
            return f"Voice speed set to {value}x."
        return f"Current voice speed: {harmony_voice_speed()}x (use /voice speed <0.5-3.0>)."
    if low == "test":
        current = harmony_status(force=True)
        return (
            f"Voice test ready — press the speaker button on this reply to hear "
            f"{harmony_voice_label(current.get('voice', HARMONY_DEFAULT_VOICE))}."
        )
    target_id = normalize_harmony_voice(arg)
    if not target_id:
        return (
            f"Unknown voice '{arg}'. Type /voice on its own to list the "
            f"{len(HARMONY_VOICES)} available presets."
        )
    target = set_harmony_voice(target_id)
    if not target:
        return (
            f"Could not switch to {harmony_voice_label(target_id)} — the MP Harmony "
            "Agent is not answering. Start it with ▶ Start Agent, then try again."
        )
    return f"Active voice switched to {harmony_voice_label(target)}."


# ------------------------------------------------------------------------------
# STREAM CATALOG & QUEUE
#
# This service is a pure HTTP audio streamer. It never opens a sound device and
# never spawns a local player: phones and tablets pull the mix themselves from
# /api/stream. Everything below is bookkeeping for which file to serve next.
# ------------------------------------------------------------------------------
def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def read_config_env() -> Dict[str, str]:
    """Read config.env so the service sees manager settings without systemd env."""
    cfg: Dict[str, str] = {}
    for cand in (get_base_dir() / "config.env", CONFIG_DIR / "config.env"):
        if not cand.is_file():
            continue
        try:
            for line in cand.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    cfg.setdefault(key.strip(), val.strip().strip("\"'"))
        except Exception:
            pass
    return cfg


def _config_value(key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ[key]
    return read_config_env().get(key, default)


def is_audio_file(path: Any) -> bool:
    try:
        return bool(path) and Path(str(path)).suffix.lower() in AUDIO_EXTENSIONS
    except Exception:
        return False


# Some recordings are saved as a WAV header only: RIFF/WAVE magic followed by a
# `data` chunk that declares zero frames (a 44-byte file). They are larger than
# zero bytes, so a plain size check lets them into the queue, and the phone then
# pulls a body with nothing to decode. The <audio> element raises `error`, the
# dashboard reads that as "the companion restarted", and the reconnect banner
# flashes forever while the page reloads the same dead URL. Treating them as
# unstreamable removes the loop at its source.
WAV_HEADER_SIZE = 44

# Anything this small cannot hold audio in any container, so it is rejected
# without reading the file. The number is deliberately *below* the smallest
# file that has been measured to decode to real audio: a single AAC ADTS frame
# at 43 bytes. A floor above that would silently delete valid music, which is
# the worse failure of the two - a rejected track disappears from the queue with
# no explanation, whereas a stub that slips through now produces a clear "this
# mix cannot be played" message after the client's bounded retry.
#
# This is only the cheap first gate. The precise "valid header, no audio"
# checks for WAV and FLAC below are what actually fix the flicker bug, and they
# need to be per-format because the defect looks different in each container.
MIN_STREAMABLE_BYTES = 32

# The smallest a FLAC frame can be: a frame header, a subframe header, a
# constant subframe and the frame CRC. Used to decide whether anything is left
# after the metadata blocks.
FLAC_MIN_FRAME_BYTES = 13


def _wav_has_no_audio_frames(path: Any) -> bool:
    """True when a file is a RIFF/WAVE with no audio data in it.

    Only a file that positively identifies itself as WAV is judged here: a
    non-WAV container is left alone, because the browser may well play it and
    guessing would silently drop good audio.

    Two shapes are caught: a complete 44-byte header whose `data` chunk
    declares zero frames (the file that caused the flicker bug), and a file
    too short to even hold that header - a truncated WAV has no frames either.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(WAV_HEADER_SIZE)
    except OSError:
        return False
    if header and not any(header):
        # Every WAV variant (RIFF, RF64, BW64, RIFX) puts a magic at offset 0,
        # so an all-zero head cannot be any of them. This is the observed
        # "interrupted export": a multi-gigabyte file of nulls whose size passes
        # the floor, so it reached the phone and produced a media error. It is
        # the same failure class as the 44-byte stub, one layer down.
        return True
    if header[0:4] != b"RIFF":
        # Not a WAV at all (or empty): leave it to the size floor.
        return False
    if len(header) < WAV_HEADER_SIZE:
        # Claims RIFF but is shorter than any complete WAV header.
        return True
    if header[8:12] != b"WAVE":
        return False
    # A canonical PCM WAV puts the `data` subchunk at 36 and its length at 40.
    # Any other layout (LIST/bext chunks before the audio) is not judged.
    if header[36:40] != b"data":
        return False
    return int.from_bytes(header[40:44], "little") <= 0


def _flac_has_no_audio_frames(path: Any) -> bool:
    """True for a FLAC file that carries metadata but no audio frames.

    FLAC has the same failure class as WAV: a valid `fLaC` marker plus a
    STREAMINFO block and no frames at all. A truncated file keeps only the
    metadata, so the audio start offset parsed from the block headers is at or
    past the end of the file.

    The minimum a frame can be is ~13 bytes (frame header, subframe header, a
    constant subframe and the CRC), so a file with less than that left after
    the metadata cannot hold audio. The observed smallest playable FLAC is 66
    bytes; this rejects the 44-byte stub without guessing at a magic size.
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"fLaC":
                return False
            start = 4
            # Walk the metadata blocks to find where audio begins. The header
            # is one byte of flags plus a 3-byte length; the last-block flag is
            # the high bit of the first byte. A file truncated mid-metadata is
            # treated as empty, which is correct: there is no audio in it.
            for _ in range(64):
                block_header = f.read(4)
                if len(block_header) < 4:
                    return True
                is_last = bool(block_header[0] & 0x80)
                length = int.from_bytes(block_header[1:4], "big")
                start += 4 + length
                if is_last:
                    break
                if f.seek(length, os.SEEK_CUR) is None:  # pragma: no cover
                    return True
            else:
                return False
    except OSError:
        return False
    try:
        return os.path.getsize(str(path)) < (start + FLAC_MIN_FRAME_BYTES)
    except OSError:
        return False


def is_streamable_file(path: Any, st: Optional[os.stat_result] = None) -> bool:
    """A file the phone can actually decode and play.

    This is the queue-side gate: existence, more than a bare header's worth of
    bytes, and at least one audio frame. Anything else is skipped when the queue
    is built rather than being handed to a browser that can only answer with a
    media error.

    `st` lets a caller that already has the stat result (the archive walk does)
    pass it in, so a large library is not stat'ed twice per file.
    """
    if not path:
        return False
    if st is None:
        try:
            st = os.stat(str(path))
        except OSError:
            return False
    if not stat.S_ISREG(st.st_mode) or st.st_size <= MIN_STREAMABLE_BYTES:
        return False
    # Only PCM-WAV and FLAC have a cheap, certain "header but no audio" test.
    # Guarding on the suffix keeps a 51k-track music playlist from opening
    # every file: it already pays one stat per path in the queue build.
    try:
        ext = Path(str(path)).suffix.lower()
    except Exception:
        return True
    if ext in (".wav", ".wave"):
        return not _wav_has_no_audio_frames_cached(path, st)
    if ext == ".flac":
        return not _flac_has_no_audio_frames_cached(path, st)
    return True


# The frame probe opens the file and walks its metadata blocks. That is a few
# syscalls on a local disk but a couple of network round trips per file on the
# CIFS archives, and the archive is re-scanned on every queue rebuild. With
# ~1k mixes of 2.5 GB each, an uncached probe made one scan take 196 s; the
# pollers then queued behind `_queue_build_lock` and the dashboard stalled.
# Keying on (mtime, size) keeps the verdict valid until the file changes, so a
# rescan costs one stat per file instead of opening all of them.
_frame_probe_cache: Dict[str, Tuple[float, int, bool]] = {}
_frame_probe_lock = threading.Lock()
_frame_probe_dirty = False
FRAME_PROBE_CACHE_FILE = CONFIG_DIR / "web_companion_frame_cache.json"
_FRAME_PROBE_CACHE_MAX = 20000
# The verdict schema is versioned so a newer, stricter probe cannot inherit an
# older verdict. Without this, a file already cached as "has audio" by a build
# that only checked for a RIFF header keeps streaming forever: the (mtime, size)
# signature never changes, so the wrong answer is never recomputed. Bumping the
# version makes the next launch re-probe once and rewrite the cache.
FRAME_PROBE_CACHE_VERSION = 2


def _load_frame_probe_cache() -> None:
    """Restore verdicts from the last run.

    Without this every service restart (or reboot) reopens ~1k multi-GB files
    over CIFS, which is minutes of fsck-like latency before the dashboard can
    answer at all.

    A cache written by an older build is ignored rather than migrated: its
    verdicts came from a weaker probe, and the only safe way to keep them is to
    know they were produced by the same rules. Ignoring is cheap (one re-probe
    per file, once) and correct.
    """
    try:
        if not FRAME_PROBE_CACHE_FILE.is_file():
            return
        raw = json.loads(
            FRAME_PROBE_CACHE_FILE.read_text(encoding="utf-8", errors="ignore")
        )
        if not isinstance(raw, dict):
            return
        if raw.get("version") != FRAME_PROBE_CACHE_VERSION:
            return
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return
        loaded = {}
        for key, entry in entries.items():
            if (
                isinstance(entry, list) and len(entry) == 3
                and isinstance(entry[0], (int, float))
                and isinstance(entry[1], int)
                and isinstance(entry[2], bool)
            ):
                loaded[key] = (float(entry[0]), int(entry[1]), entry[2])
        with _frame_probe_lock:
            _frame_probe_cache.update(loaded)
    except Exception:
        pass


def _save_frame_probe_cache() -> None:
    try:
        with _frame_probe_lock:
            if len(_frame_probe_cache) > _FRAME_PROBE_CACHE_MAX:
                # Keep the most recently stamped entries; the archive only
                # grows, and an unbounded file would grow with it.
                ordered = sorted(
                    _frame_probe_cache.items(), key=lambda kv: kv[1][0], reverse=True
                )[:_FRAME_PROBE_CACHE_MAX]
                _frame_probe_cache.clear()
                _frame_probe_cache.update(ordered)
            snapshot = {
                "version": FRAME_PROBE_CACHE_VERSION,
                "entries": {
                    key: [value[0], value[1], value[2]]
                    for key, value in _frame_probe_cache.items()
                },
            }
        FRAME_PROBE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FRAME_PROBE_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot), encoding="utf-8")
        tmp.replace(FRAME_PROBE_CACHE_FILE)
    except Exception:
        pass


def _wav_has_no_audio_frames_cached(path: Any, st: os.stat_result) -> bool:
    return _cached_frame_probe(
        "wav", path, st, lambda: _wav_has_no_audio_frames(path)
    )


def _flac_has_no_audio_frames_cached(path: Any, st: os.stat_result) -> bool:
    return _cached_frame_probe(
        "flac", path, st, lambda: _flac_has_no_audio_frames(path)
    )


def _cached_frame_probe(kind: str, path: Any, st: os.stat_result, probe) -> bool:
    global _frame_probe_dirty
    key = f"{kind}:{path}"
    signature = (st.st_mtime, st.st_size)
    with _frame_probe_lock:
        cached = _frame_probe_cache.get(key)
    if cached and cached[0] == signature[0] and cached[1] == signature[1]:
        return cached[2]
    result = bool(probe())
    with _frame_probe_lock:
        _frame_probe_cache[key] = (signature[0], signature[1], result)
        # Writing the file on every miss would hammer the disk during a cold
        # scan of a thousand files. A scan of this size runs once per restart,
        # so flushing at the end of the scan is enough.
        _frame_probe_dirty = True
    return result


def flush_frame_probe_cache() -> None:
    """Persist verdicts after a scan finishes (see _cached_frame_probe)."""
    global _frame_probe_dirty
    with _frame_probe_lock:
        dirty = _frame_probe_dirty
        if not dirty:
            return
    if dirty:
        _save_frame_probe_cache()
        with _frame_probe_lock:
            _frame_probe_dirty = False


def get_library_mode() -> str:
    """Which library the companion streams from: MIX_ARCHIVE, MUSIC or FAVORITES.

    The web toggle persists here so a phone can switch libraries and the choice
    survives a service restart. Falls back to WEB_COMPANION_MODE in config.env,
    then to the mix archive.
    """
    if WEB_MODE_FILE.is_file():
        try:
            saved = WEB_MODE_FILE.read_text(encoding="utf-8").strip().upper()
            if saved in MODE_LABELS:
                return saved
        except Exception:
            pass
    configured = (_config_value("WEB_COMPANION_MODE", "") or "").strip().upper()
    if configured in MODE_LABELS:
        return configured
    return LIBRARY_MODE_MIX


def set_library_mode(mode: str) -> Optional[str]:
    """Persist the library choice and restart the queue at its first entry."""
    target = str(mode or "").strip().upper()
    if target in ("MIX", "MIXES", "ARCHIVE", "MIXARCHIVE"):
        target = LIBRARY_MODE_MIX
    elif target in ("MUSIC", "TRACKS", "SONGS", "LIBRARY"):
        target = LIBRARY_MODE_MUSIC
    elif target in ("FAV", "FAVS", "FAVES", "FAVOURITE", "FAVOURITES", "FAVORITE", "FAVORITES"):
        target = LIBRARY_MODE_FAVORITES
    if target not in MODE_LABELS:
        return None
    unchanged = get_library_mode() == target
    try:
        WEB_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WEB_MODE_FILE.write_text(target + "\n", encoding="utf-8")
    except Exception:
        return None
    # Tapping the library that is already active must not rewind the stream to
    # track one or pay for another library scan.
    if unchanged:
        return target
    # Drop the previous library before the mount wait. A poll that arrives
    # while the shares are connecting must build the new queue, not keep
    # serving the mix list under the Music label.
    invalidate_queue_cache()
    if target == LIBRARY_MODE_MUSIC:
        # The mix archive needs no preparation, but a music library on SMB does.
        # This runs inside the explicit tap, which the dashboard already paints
        # with a "Switching to Music…" state, so waiting here is safe and it
        # makes the very first Music tap list tracks instead of mixes.
        ensure_music_folders_mounted(wait=True, deadline_sec=20.0)
        invalidate_queue_cache()
    reset_stream_for_new_library()
    return target


def reset_stream_for_new_library() -> None:
    """Point the stream at the first entry of the newly selected library.

    Playback state carries over, so switching libraries while listening keeps
    the phone playing - it just starts the new library from track one. The
    dashboard sees the file change and re-points its <audio> element.
    """
    was_playing = bool(load_stream_session().get("playing"))
    queue = build_stream_queue()
    with _stream_lock:
        state = load_stream_session()
        state.update({
            "index": 0,
            "path": queue[0] if queue else "",
            "position_sec": 0.0,
            "started_at": 0.0,
            "duration_sec": probe_duration(queue[0]) if queue else 0.0,
            "playing": was_playing and bool(queue),
        })
        if state["playing"]:
            state["started_at"] = time.time()
        save_stream_session(state)


def scan_playlist_files() -> List[str]:
    """Return the playlist the companion should play from.

    Order of preference:
      1. WEB_COMPANION_PLAYLIST (explicit .m3u/.m3u8/.json file)
      2. Saved player playlist for the selected library mode
      3. Every audio file in the configured folders for that mode, newest first

    An empty list means "there is nothing to play" and autoplay is skipped.
    """
    explicit = _config_value("WEB_COMPANION_PLAYLIST", "").strip()
    if explicit:
        return _read_playlist_file(Path(os.path.expanduser(explicit)))

    if get_library_mode() == LIBRARY_MODE_MUSIC:
        # Music mode plays the music playlist. An unmounted share used to fall
        # through to the mix archive, so the Music button listed mixes after
        # the "Switching to Music…" state.
        return scan_music_files()

    return scan_archive_files()


def music_playlist_candidates() -> List[Path]:
    """The combined per-machine music playlist, mirroring the MP Audio Player.

    music_folders_manager.py writes the "ALL_MUSIC_FOLDERS" playlist beside the
    per-machine list, under either the config playlists dir or the legacy
    PLAYLISTS_GENERATED folder inside the app.
    """
    base = get_base_dir()
    names = (
        "DJ_Tracks_ALL_MUSIC_FOLDERS.json",
        "DJ_Tracks_ALL_MUSIC_FOLDERS.m3u8",
        "DJ_Tracks_ALL_MUSIC_FOLDERS.m3u",
    )
    dirs = [PLAYLIST_DIR,
            CONFIG_DIR / "PLAYLISTS_GENERATED",
            base / "PLAYLISTS_GENERATED",
            base / "playlists"]
    return [d / name for d in dirs for name in names]


def _music_folders_data() -> List[Dict[str, Any]]:
    """The configured music folders, local and SMB, as stored on this machine."""
    try:
        if not MUSIC_FOLDERS_FILE.is_file():
            return []
        data = json.loads(MUSIC_FOLDERS_FILE.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    items = data.get("folders") if isinstance(data, dict) else data
    return [f for f in (items or []) if isinstance(f, dict)]


def music_folder_roots() -> List[Path]:
    """Local paths of the configured music folders, skipping SMB/UNC entries.

    A share such as \\\\192.0.2.20\\MUSIC001 only becomes a filesystem path
    once it is mounted, and walking a whole mounted home share picks up
    unrelated audio (application sound effects), so those entries are ignored
    here. The generated playlist is the authoritative music list; mounting the
    share is what makes its paths resolve.
    """
    roots: List[Path] = []
    seen = set()
    for folder in _music_folders_data():
        if str(folder.get("type", "")).lower() in ("smb", "nfs", "remote", "unc"):
            continue
        raw = str(folder.get("local_path") or folder.get("path") or "").strip()
        if not raw or raw.startswith("\\\\") or raw.startswith("//"):
            continue
        try:
            p = Path(os.path.expanduser(raw)).resolve()
        except Exception:
            continue
        try:
            if p.is_dir() and str(p) not in seen:
                seen.add(str(p))
                roots.append(p)
        except OSError:
            continue
    return roots


def ensure_user_tool_path(extra_dirs: Optional[List[str]] = None) -> None:
    """Put the user bin directories on PATH.

    The companion's systemd unit starts with `/usr/bin` only. rclone lives in
    `~/.local/bin`, so `shutil.which("rclone")` failed inside the service and
    Music mode never mounted the shares. The playlist paths then all missed
    and the queue was replaced with the mix archive.
    """
    dirs: List[str] = []
    for raw in list(extra_dirs or []) + [str(Path.home() / ".local" / "bin"), "/usr/local/bin"]:
        if raw and raw not in dirs:
            dirs.append(raw)
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    for item in reversed(dirs):
        if Path(item).is_dir() and item not in parts:
            parts.insert(0, item)
    os.environ["PATH"] = os.pathsep.join(parts)


# Mounting is expensive (rclone startup plus a share listing per folder), so a
# failed attempt is not repeated for a minute. The queue retry below is much
# faster than that, so an offline library does not hammer the NAS.
MUSIC_MOUNT_RETRY_TTL = 60.0
_music_mount_lock = threading.Lock()
_last_music_mount_attempt = 0.0
_music_mount_in_flight = False


def ensure_music_folders_mounted(wait: bool = False, deadline_sec: float = 15.0) -> bool:
    """Mount the configured SMB music shares on demand.

    The companion runs as a background service and never goes through the
    Music Folders menu, so nothing else mounts these shares for it. Without
    this the generated playlist points at unmounted paths and Music mode stays
    empty (and silently falls back to the mix archive) even though the library
    is reachable on the network.

    The dashboard polls every couple of seconds, so the default is to start the
    mount in the background and answer immediately: a blocking mount here would
    stall /api/playlist and make the Music button look dead, which is exactly
    the symptom this fixes. The next queue build sees the mounted shares.

    `wait` mounts synchronously for the explicit Music tap, bounded by
    `deadline_sec` so one offline share cannot hold the request for minutes.
    Anything left over is picked up by the background retry.

    Returns True when a share was mounted by this call.
    """
    global _last_music_mount_attempt, _music_mount_in_flight

    folders = [f for f in _music_folders_data()
               if str(f.get("type", "")).lower() == "smb" and f.get("id")]
    if not folders:
        return False

    now = time.time()
    with _music_mount_lock:
        if _music_mount_in_flight:
            return False
        # The retry window exists so an offline NAS is not hammered by the
        # 2-second dashboard poll. An explicit Music tap is a user decision, so
        # it may retry immediately; only the background path is rate-limited.
        if not wait and (now - _last_music_mount_attempt) < MUSIC_MOUNT_RETRY_TTL:
            return False
        _last_music_mount_attempt = now
        _music_mount_in_flight = True

    results: List[bool] = []

    def _mount_all() -> bool:
        threads: List[threading.Thread] = []
        try:
            # The service PATH does not include ~/.local/bin. Do this before
            # music_folders_manager looks up rclone.
            ensure_user_tool_path()
            try:
                import music_folders_manager as mfm
            except ImportError:  # imported as scripts.mix_web_companion
                from scripts import music_folders_manager as mfm
            config = mfm.load_music_config()

            def _one(folder: Dict[str, Any]) -> None:
                try:
                    # connect_folder is a no-op when the share is already
                    # answering, and it remounts a wedged one.
                    ok, _detail = mfm.connect_folder(folder, config, interactive=False)
                    results.append(bool(ok))
                except Exception:
                    results.append(False)

            # One slow share must not eat the whole deadline before the others
            # start. Six serial mounts never finished inside the old 15 s cap,
            # so the scan still saw an empty library and listed mixes.
            for folder in folders:
                thread = threading.Thread(
                    target=_one, args=(folder,), name="mp-music-share", daemon=True,
                )
                thread.start()
                threads.append(thread)
            stop_at = time.time() + max(0.0, deadline_sec)
            for thread in threads:
                thread.join(max(0.0, stop_at - time.time()))
        except Exception:
            pass
        finally:
            alive = [thread for thread in threads if thread.is_alive()]
            if not alive:
                with _music_mount_lock:
                    _music_mount_in_flight = False
            else:
                def _clear_when_done() -> None:
                    for thread in alive:
                        thread.join()
                    with _music_mount_lock:
                        _music_mount_in_flight = False

                threading.Thread(
                    target=_clear_when_done, name="mp-music-mount-done", daemon=True,
                ).start()
        return any(results)

    if not wait:
        threading.Thread(target=_mount_all, name="mp-music-mount", daemon=True).start()
        return False

    return _mount_all()


# A scan that found nothing is only trusted briefly. The generated playlist
# does not change when an SMB share mounts, so caching an empty result against
# the playlist signature kept Music mode on the Mix Archive until the service
# restarted - even though the library was reachable again.
MUSIC_SCAN_RETRY_TTL = 20.0

# (playlist signature, resolved files, when the scan ran)
_music_scan_cache: Optional[Tuple[Tuple[Tuple[str, float, int], ...], Tuple[str, ...], float]] = None


def _music_scan_signature() -> Tuple[Tuple[str, float, int], ...]:
    """Identity of the music playlist files, so a big JSON is parsed only once."""
    sig: List[Tuple[str, float, int]] = []
    for cand in music_playlist_candidates():
        try:
            st = cand.stat()
            sig.append((str(cand), st.st_mtime, st.st_size))
        except OSError:
            continue
    return tuple(sig)


def _music_sample_paths(paths: List[str], sample: int = 12) -> List[str]:
    """A spread of playlist paths, not just the first folder.

    The generated list is grouped by share. The first entries can all sit on
    one offline disk while a later share is mounted and playable.
    """
    if not paths:
        return []
    if len(paths) <= sample:
        return list(paths)
    step = max(1, len(paths) // sample)
    picked: List[str] = []
    for i in range(0, len(paths), step):
        picked.append(paths[i])
        if len(picked) >= sample - 1:
            break
    if paths[-1] not in picked:
        picked.append(paths[-1])
    return picked


def _path_readable_now(path: Any) -> bool:
    """True when this exact file can be opened and read right now.

    A dropped rclone/FUSE mount keeps answering `os.stat()` from its attribute
    cache and fails only on the read ("Transport endpoint is not connected").
    A browser streaming such a file sees a body that ends early and reports it
    as unplayable, which is a lie about the file. Used before recording a mix
    as undecodable, so a flaky share can never blacklist a good track.
    """
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        if not stat.S_ISREG(os.stat(raw).st_mode):
            return False
        with open(raw, "rb") as probe:
            return bool(probe.read(1))
    except OSError:
        return False


def _music_sample_reachable(paths: List[str], sample: int = 12) -> bool:
    """True when at least one playlist path can actually be READ.

    The generated playlist names every track whether or not the SMB share is
    mounted. Sampling a spread of entries tells the dashboard to say
    "connecting" only when nothing in the library can be opened yet. It does
    not decide membership: the playlist itself is the list.

    `os.stat` is not enough. A dropped rclone/FUSE mount keeps answering stat()
    from its attribute cache long after the share is gone, and only the read
    fails with "Transport endpoint is not connected". Probing stat alone
    therefore called 55k unreachable tracks "reachable", the page offered every
    one of them, and each stream attempt died mid-body. One byte proves the
    difference, and it is one byte per sampled file, not a whole read.
    """
    for path in _music_sample_paths(paths, sample):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_size <= MIN_STREAMABLE_BYTES:
            continue
        try:
            with open(path, "rb") as probe:
                if not probe.read(1):
                    continue
        except OSError:
            # A dead mount: stat answered from cache, the read did not.
            continue
        return True
    return False


def scan_music_files() -> List[str]:
    """The MUSIC library: generated playlist first, then the folder roots.

    The combined playlist holds tens of thousands of paths in a ~30 MB JSON
    file, so a re-parse on every library switch made the Mix/Music buttons feel
    dead. The parsed result is reused until the playlist file changes.

    Paths are kept even when the share is not mounted yet. Requiring every
    file to stat before it could join the queue dropped the whole library
    (the mount point exists, the files do not) and the companion then filled
    the playlist with mixes. The desktop player lists these same paths.

    An empty result is re-scanned every MUSIC_SCAN_RETRY_TTL seconds: a share
    that was not mounted when the playlist was read leaves every path
    unresolved, and the playlist file itself never changes when it mounts.
    """
    global _music_scan_cache

    signature = _music_scan_signature()
    now = time.time()
    cached = _music_scan_cache
    if cached:
        cached_signature, cached_files, cached_at = cached
        if cached_signature == signature:
            # A populated library is good until the playlist is rewritten. An
            # empty one is only a hint, because the share may mount at any time.
            if cached_files or (now - cached_at) < MUSIC_SCAN_RETRY_TTL:
                files = list(cached_files)
                if files and not _music_sample_reachable(files):
                    ensure_music_folders_mounted()
                return files

    files: List[str] = []
    for cand in music_playlist_candidates():
        if not cand.is_file():
            continue
        # Do not drop a row because the share is offline. is_streamable_file
        # stats the path, and an unmounted MUSIC_SMB directory makes every
        # row miss, which is what emptied Music mode.
        files = _playlist_entry_paths(cand)
        if files:
            break
    if files and not _music_sample_reachable(files):
        ensure_music_folders_mounted()
    if not files:
        files = _scan_dirs(music_folder_roots())

    _music_scan_cache = (signature, tuple(files), now)
    return list(files)


def _favorite_key(path: Any) -> str:
    """Identity used to de-duplicate Favorites, matching the MP Audio Player."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    if raw.startswith(("\\\\", "//", "smb://")):
        return raw.replace("\\", "/").rstrip("/").lower()
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))
    except Exception:
        return os.path.normcase(raw)


def scan_favorite_files() -> List[str]:
    """The FAVORITES library: the playlist the MP Audio Player marks with [H].

    favorites.json is the authoritative list (favorites.m3u8 is a mirror of it),
    so it is read in saved order - the player's Favorites order is meaningful,
    unlike the newest-first archive and music scans. The file is re-read on each
    call because it is tiny and the player may mark a track at any moment.

    An empty JSON file is still the answer. Falling through to the m3u mirror
    put tracks back after the last one had been removed.
    """
    paths: List[str] = []
    seen = set()
    if FAVORITES_FILE.is_file():
        sources = (FAVORITES_FILE,)
    else:
        sources = (FAVORITES_FILE.with_suffix(".m3u8"),)
    for cand in sources:
        if not cand.is_file():
            continue
        for path in _read_playlist_file(cand):
            key = _favorite_key(path)
            if not key or key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def _load_favorite_document() -> Dict[str, Any]:
    """The desk player's favorites file, preserving rows it already stored."""
    doc: Dict[str, Any] = {"version": 1, "name": "Favorites", "tracks": []}
    if not FAVORITES_FILE.is_file():
        return doc
    try:
        data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return doc
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        raw = data.get("tracks")
        rows = raw if isinstance(raw, list) else []
        doc["version"] = data.get("version", 1)
        doc["name"] = data.get("name") or "Favorites"
    else:
        rows = []
    tracks: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        key = _favorite_key(row.get("path"))
        if not key or key in seen:
            continue
        seen.add(key)
        tracks.append(row)
    doc["tracks"] = tracks
    return doc


def _favorite_key_set() -> set:
    """Keys currently in favorites.json. Cached on the file's own stamp.

    A missing file is cached too. The playlist draws a heart on every row, and
    re-reading a file that is not there on each row made that draw a stat storm.
    """
    try:
        st = FAVORITES_FILE.stat()
        stamp = (str(FAVORITES_FILE), st.st_mtime, st.st_size)
    except OSError:
        stamp = (str(FAVORITES_FILE), None)
    if _favorite_cache.get("stamp") == stamp:
        return set(_favorite_cache.get("keys") or ())
    keys = set()
    for row in _load_favorite_document().get("tracks") or []:
        key = _favorite_key(row.get("path"))
        if key:
            keys.add(key)
    _favorite_cache["stamp"] = stamp
    _favorite_cache["keys"] = frozenset(keys)
    return keys


def is_favorite_path(path: Any) -> bool:
    key = _favorite_key(path)
    return bool(key) and key in _favorite_key_set()


def _favorite_target_allowed(path: str) -> bool:
    """A heart may only mark a library file, never an arbitrary path."""
    if not path or not is_audio_file(path):
        return False
    if is_favorite_path(path):
        return True
    try:
        if sec.safe_stream_path(path, stream_roots()) is not None:
            return True
    except Exception:
        pass
    try:
        if path in build_stream_queue_cached():
            return True
    except Exception:
        return False
    return False


def _favorite_row(path: str) -> Dict[str, Any]:
    ext = Path(path).suffix.lstrip(".").upper() or "AUDIO"
    try:
        size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
    except OSError:
        size_mb = 0.0
    now = time.time()
    return {
        "name": os.path.basename(path) or "Unknown",
        "path": path,
        "ext": ext,
        "size_mb": round(size_mb, 3),
        "duration_fmt": "00:00",
        "duration_sec": 0,
        "source": get_library_mode(),
        "mtime": now,
        "added": now,
    }


def _write_favorite_m3u(tracks: List[Dict[str, Any]]) -> None:
    """Mirror favorites.json the same way the desk player does."""
    m3u = FAVORITES_FILE.with_suffix(".m3u8")
    lines = ["#EXTM3U", "#PLAYLIST:MP Favorites"]
    for track in tracks:
        name = str(track.get("name") or os.path.basename(str(track.get("path") or "")))
        lines.append("#EXTINF:-1," + name.replace("\n", " "))
        lines.append(str(track.get("path") or ""))
    tmp = m3u.with_name(m3u.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(m3u)


def toggle_favorite_path(path: str) -> Optional[bool]:
    """Add or remove one track. True when it is now a favourite.

    None means the path is not one this companion is allowed to mark. The
    document stays in the shape FavoritesStore reads, so a heart tap and the
    desk player's [H] edit the same playlist.
    """
    raw = str(path or "").strip()
    if not _favorite_target_allowed(raw):
        return None
    key = _favorite_key(raw)
    if not key:
        return None
    with _favorites_lock:
        doc = _load_favorite_document()
        tracks = list(doc.get("tracks") or [])
        kept = [row for row in tracks if _favorite_key(row.get("path")) != key]
        added = len(kept) == len(tracks)
        if added:
            kept.append(_favorite_row(raw))
        doc["tracks"] = kept
        FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FAVORITES_FILE.with_name(FAVORITES_FILE.name + ".tmp")
        payload = {
            "version": int(doc.get("version") or 1),
            "name": str(doc.get("name") or "Favorites"),
            "tracks": kept,
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(FAVORITES_FILE)
        try:
            _write_favorite_m3u(kept)
        except OSError:
            pass
        _favorite_cache["stamp"] = object()
        _favorite_cache["keys"] = frozenset()
    if get_library_mode() == LIBRARY_MODE_FAVORITES:
        invalidate_queue_cache()
    return added


def _scan_dirs(roots: List[Path]) -> List[str]:
    """Newest-first audio files underneath the given directories."""
    skip = {"__pycache__", "node_modules", ".git", "venv", ".venv"}
    found: List[Tuple[float, str]] = []
    seen = set()
    for root_dir in roots:
        # A privacy-blocked folder can park a walk inside the kernel, so it is
        # probed first and skipped when unreadable.
        if not _dir_readable(root_dir):
            continue
        for root, subdirs, files in os.walk(root_dir):
            subdirs[:] = [d for d in subdirs if not d.startswith(".") and d not in skip]
            for name in files:
                # "._track.flac" is an AppleDouble resource fork, not audio.
                if name.startswith("._") or not is_audio_file(name):
                    continue
                full = os.path.join(root, name)
                if full in seen:
                    continue
                seen.add(full)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                # A zero-byte file (usually an interrupted conversion) would
                # stall the phone on an empty body, so it is not streamable.
                # Neither is a WAV header with a zero-length data chunk: it has
                # a size, so the old check passed it through and the browser
                # failed on it, which is what made the dashboard reconnect in a
                # loop. is_streamable_file() judges both.
                if not is_streamable_file(full, st):
                    continue
                found.append((st.st_mtime, full))
    # Persist the frame verdicts once per scan rather than once per file.
    flush_frame_probe_cache()
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found]


def _playlist_entry_paths(path: Path) -> List[str]:
    """Audio paths named by a playlist, including files that are not on disk yet.

    The music playlist is the list the MP Audio Player shows. Filtering it
    through `is_streamable_file` deleted every row while the SMB shares were
    unmounted, and Music mode then had nothing of its own to display.
    """
    if not path.is_file():
        return []
    try:
        # One reader for every container (M3U, M3U8, XSPF, PLS and JSON), the
        # same one the player and the exporter use. Rows are only kept when the
        # path is an audio file, so a stray line cannot become a track here.
        if _playlists is None:
            return []
        rows = _playlists.read_playlist(path)
        return [row["path"] for row in rows if row.get("path") and is_audio_file(row["path"])]
    except Exception:
        return []


def _read_playlist_file(path: Path) -> List[str]:
    # A saved playlist can still name a mix that was replaced by a
    # header-only WAV stub, so the same frame check the archive scan uses
    # applies here. Otherwise the queue hands the browser a dead file.
    # Music mode uses `_playlist_entry_paths` instead: that library is allowed
    # to name files whose share is still connecting.
    return [p for p in _playlist_entry_paths(path) if is_streamable_file(p)]


# A removable archive does not keep its mount point. The same drive is
# /run/media/<user>/WD BLACK B on one boot, /mnt/X299ROG_WD_BLACK on another,
# and /Volumes/DATAMAC3 on the Mac, so a single MIX_ARCHIVE_DIR written once at
# install time goes stale and the companion then streams nothing. These globs
# are the mount roots each platform actually uses; the archive is found again
# after a replug instead of needing a rewrite of config.env.
ARCHIVE_MOUNT_GLOBS = (
    "/run/media/*/*/MIX_ARCHIVE",       # Linux desktop automount (udisks2)
    "/media/*/*/MIX_ARCHIVE",           # Ubuntu / older udisks
    "/mnt/*/MIX_ARCHIVE",               # fstab / external-drive mounts
    "/Volumes/*/MIX_ARCHIVE",           # macOS
)


def _archive_mount_globs() -> Tuple[str, ...]:
    """The autodetect list, empty when the operator pinned the folders.

    `WEB_COMPANION_ARCHIVE_AUTODETECT=0` keeps discovery to the configured
    paths only, which matters on a machine whose /mnt holds unrelated
    MIX_ARCHIVE folders that must not be streamed.
    """
    if not _env_flag("WEB_COMPANION_ARCHIVE_AUTODETECT", True):
        return ()
    return ARCHIVE_MOUNT_GLOBS


def _archive_dirs() -> List[Path]:
    dirs: List[Path] = []
    seen = set()

    def add(value: Any) -> None:
        if not value:
            return
        try:
            p = Path(os.path.expanduser(str(value).strip().strip("\"'"))).resolve()
            if p.is_dir() and str(p) not in seen:
                seen.add(str(p))
                dirs.append(p)
        except Exception:
            pass

    add(_config_value("MIX_ARCHIVE_DIR", ""))
    for part in re.split(r"[;\n,]", _config_value("EXTRA_MIX_ARCHIVE_DIRS", "") or ""):
        add(part)
    base = get_base_dir()
    for name in ("MIX_ARCHIVE", "FLAC_CONVERTED_OUTPUTS"):
        add(base / name)
    # Discover every populated archive mount, not only a replacement for a
    # stale configured path. A desktop player can stream from more than one
    # disk at once (the manager and MP Audio Player scan the configured folder
    # *and* the archive mounted under /run/media, /mnt or /Volumes), so the
    # phone must see the same library instead of only the primary folder.
    #
    # A match is added only when it already contains a mix: the empty
    # MIX_ARCHIVE/FLAC_CONVERTED_OUTPUTS skeleton in a fresh install must not
    # become a second root, and an unmounted autofs/CIFS stub must not be
    # pulled in just because it exists.
    #
    # ARCHIVE_MOUNT_GLOBS is emptied by the test suite, which must never walk
    # the developer's real archive; setting WEB_COMPANION_ARCHIVE_AUTODETECT=0
    # does the same for an operator who pins the exact folders.
    globs = _archive_mount_globs()
    if globs:
        for pattern in globs:
            try:
                for match in sorted(glob.glob(pattern)):
                    try:
                        candidate = Path(os.path.expanduser(str(match))).resolve()
                    except Exception:
                        continue
                    if str(candidate) in seen:
                        continue
                    if _dir_is_archive_candidate(candidate):
                        add(match)
            except Exception:
                pass
    return dirs


def _dir_is_archive_candidate(path: Path) -> bool:
    """True when a folder looks like it holds mixes, not just the empty skeleton.

    A freshly installed tree has MIX_ARCHIVE/FLAC_CONVERTED_OUTPUTS as empty
    placeholder directories. Counting those as "the archive" is exactly how the
    volume fallback above got skipped, so a folder only counts when it contains
    an audio file directly or in one of the conversion subfolders.

    The listing must be bounded: a privacy-blocked read on macOS parks for ~20 s
    before the kernel interrupts it, and this runs inside `_archive_dirs()` on
    every queue build. Using the cached `_dir_readable()` keeps the probe cheap
    and stops a denied volume from stalling the status endpoint.
    """
    if not _dir_readable(path):
        return False
    try:
        if path.name == "MIX_ARCHIVE":
            for sub in ("FLAC_CONVERTED_OUTPUTS", "MP3_CONVERTED_OUTPUTS",
                        "CONVERTED_WAV_FILES", ""):
                probe = (path / sub) if sub else path
                if not _dir_readable(probe):
                    continue
                try:
                    with os.scandir(probe) as entries:
                        for entry in entries:
                            if entry.is_file() and is_audio_file(entry.name):
                                return True
                except OSError:
                    continue
            return False
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_file() and is_audio_file(entry.name):
                    return True
    except OSError:
        return False
    return False


def scan_archive_files() -> List[str]:
    """Newest-first audio files across the configured archive folders."""
    return _scan_dirs(_archive_dirs())


def music_mount_roots() -> List[Path]:
    """The SMB mount root the music playlist paths live under.

    `music_folder_roots` skips SMB entries on purpose: walking a mounted home
    share would pick up unrelated audio. The playlist paths are still
    `/MUSIC_SMB/<share>/...`, and the stream allow-list used to contain only
    local folders, so every one of those tracks answered 404. The mount root
    is the containment boundary. It is not walked to build the queue.
    """
    try:
        try:
            import music_folders_manager as mfm
        except ImportError:
            from scripts import music_folders_manager as mfm
        configured = mfm.load_music_config().get("mount_root")
        root = Path(configured) if configured else Path.home() / "MUSIC_SMB"
    except Exception:
        root = Path.home() / "MUSIC_SMB"
    try:
        root = Path(os.path.expanduser(str(root))).resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    try:
        if root.is_dir():
            return [root]
    except OSError:
        return []
    return []


def stream_roots() -> List[Path]:
    """Every folder the stream endpoint may serve from.

    The queue is built from three different libraries - the mix archive, the
    configured music folders and Favorites - but the containment check used to
    be handed `_archive_dirs()` alone. A Music-mode queue is therefore full of
    paths the stream handler refused, so every tap answered 404 while the
    playlist looked correct: the phone showed tracks and would not play one.

    Favorites needs no root of its own: a favorite is a path the user marked in
    the player, and it resolves inside the archive or a music folder. SMB
    music is mounted under `music_mount_roots()`; without that root a track
    on MUSIC003 answered 404 even after the share was mounted. Keeping the
    list to real library roots is what stops the fix from turning the
    endpoint back into an arbitrary-file read.
    """
    roots: List[Path] = []
    seen = set()
    for root in list(_archive_dirs()) + list(music_folder_roots()) + list(music_mount_roots()):
        try:
            key = str(root)
        except Exception:
            continue
        if key and key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def _archive_dirs_permission_denied() -> str:
    """First configured archive folder that exists but was *refused*.

    macOS privacy protection (TCC) refuses a background job access to external
    volumes. Returning the folder lets the dashboard name the folder to
    approve.

    This must use the bounded probe: under launchd the denied read can block
    instead of raising, and a plain os.listdir here hung the whole status
    request (the phone saw an empty reply).

    Only a folder whose probe was refused counts. A folder that merely timed
    out is reported by `_archive_dirs_stalled()` instead, because telling a
    user to grant Full Disk Access for a spun-down USB disk sends them to the
    wrong place.
    """
    for folder in _archive_dirs():
        if _dir_readable(folder):
            return ""
        if str(folder) in _BLOCKED_DIRS and Path(folder).is_dir():
            return str(folder)
    return ""


def _archive_dirs_stalled() -> str:
    """First archive folder that could not be read in time and was not refused.

    Separate from a denial: this is a slow or unresponsive disk, a sleeping
    USB enclosure, or a stale network mount. The dashboard says so instead of
    blaming a permission that is already granted.
    """
    for folder in _archive_dirs():
        if _dir_readable(folder):
            return ""
        if str(folder) in _STALLED_DIRS and Path(folder).is_dir():
            return str(folder)
    return ""


# Directories already found unreadable, with the time of the check. A TCC
# denial can either raise PermissionError or block inside the kernel waiting
# for an approval dialog that a background job cannot show. Probing once and
# remembering the answer keeps the dashboard responsive instead of letting
# every status poll wedge on the same folder for the life of the process.
_BLOCKED_DIRS: Dict[str, float] = {}
# Why five minutes: a denied read is not a transient state. Re-probing it every
# minute made the status endpoint pay the whole probe again and, on macOS, put
# another consent request in front of a user who had already said no (or never
# saw the first one). A rescan clears these caches immediately, so an explicit
# "try again" is still instant.
_BLOCKED_TTL = 300.0
# Folders that could not be read *at all* within the probe window. Kept apart
# from _BLOCKED_DIRS because the two need different advice: a denial is fixed
# in System Settings, a slow volume is fixed by waiting or reseating the disk.
# A stall is retried sooner than a refusal, because a spun-down disk does wake.
_STALLED_DIRS: Dict[str, float] = {}
_STALLED_TTL = 60.0
# Folders with a probe thread still running. A denied read parks for the whole
# watchdog window, and a busy endpoint used to start a fresh probe per call, so
# macOS saw a stream of consent requests. Only one may be outstanding per folder.
_PROBE_INFLIGHT: Set[str] = set()
_probe_inflight_lock = threading.Lock()


def _dir_readable(path: Any, timeout: float = 8.0) -> bool:
    """True when the directory can be listed, giving up after `timeout`.

    The listing runs on a daemon thread because a privacy-blocked read does not
    always return an error: it can park indefinitely. A hung probe only leaks
    that one thread and never blocks the caller.

    Why 8 s rather than 2 s: macOS answers a *denied* read with EINTR when the
    TCC consent watchdog expires, and that takes about five seconds. A shorter
    probe always timed out first, so a real denial was recorded as "hung" and
    the dashboard blamed a privacy block for what could equally have been a
    spun-down disk. Waiting past the watchdog lets the denial identify itself,
    and the verdict is cached so the cost is paid once.

    A timeout is still *not* proof of a denial. A TCC refusal arrives as EINTR
    or EACCES; anything else is recorded as a stall and reported in those
    words, so the user is not sent to System Settings over a slow USB disk.
    """
    key = str(path)
    now = time.time()
    cached_at = _BLOCKED_DIRS.get(key)
    if cached_at is not None and (now - cached_at) < _BLOCKED_TTL:
        return False
    stalled_at = _STALLED_DIRS.get(key)
    if stalled_at is not None and (now - stalled_at) < _STALLED_TTL:
        return False

    # Never stack probes for the same folder. Each scan used to start its own
    # thread, and a denied read parks for the full watchdog window, so a busy
    # endpoint could accumulate dozens of simultaneous requests. On macOS every
    # one of those raises another TCC consent request, which is why the kernel
    # log filled with "watchdog expired for approval entry" for this process.
    with _probe_inflight_lock:
        if key in _PROBE_INFLIGHT:
            return False
        _PROBE_INFLIGHT.add(key)

    result = {"ok": False, "denied": False, "done": False}

    def _probe():
        try:
            os.listdir(key)
            result["ok"] = True
        except PermissionError:
            # An explicit refusal: this really is a privacy denial.
            result["denied"] = True
        except OSError as exc:
            # EINTR is how the TCC watchdog aborts a denied read on macOS, and
            # EACCES is the plain POSIX refusal. Everything else (EIO, ENXIO,
            # ESTALE) is a disk problem, not a permission one.
            result["denied"] = exc.errno in (errno.EACCES, errno.EINTR)
        finally:
            result["done"] = True
            # A probe that outlived the caller's wait still owns the mark; it
            # must release it here or the folder is never probed again.
            with _probe_inflight_lock:
                _PROBE_INFLIGHT.discard(key)

    worker = threading.Thread(target=_probe, name="mp-dir-probe", daemon=True)
    worker.start()
    worker.join(max(0.1, timeout))
    try:
        if not result["done"]:
            # Still blocked after the full window: a denial would have returned
            # by now on macOS, so this is reported as a stall and retried later.
            _STALLED_DIRS[key] = now
            _BLOCKED_DIRS.pop(key, None)
            return False
        if not result["ok"]:
            if result["denied"]:
                _STALLED_DIRS.pop(key, None)
                _BLOCKED_DIRS[key] = now
            else:
                # An unreadable-but-not-denied folder (I/O error, stale handle)
                # is also cached, but as a stall so the message stays honest.
                _BLOCKED_DIRS.pop(key, None)
                _STALLED_DIRS[key] = now
            return False
        _BLOCKED_DIRS.pop(key, None)
        _STALLED_DIRS.pop(key, None)
        return True
    finally:
        # Only the caller that started the probe clears the in-flight mark. A
        # probe that is still parked when the timeout expires keeps its mark
        # until its own thread finishes, so a second scan cannot stack a
        # duplicate consent request on top of it.
        if result["done"]:
            with _probe_inflight_lock:
                _PROBE_INFLIGHT.discard(key)


# ------------------------------------------------------------------------------
# STREAM CATALOG
#
# Pure HTTP streaming state. No sound device is ever opened and no local player
# process is ever spawned - the phone or tablet is the only audio output.
# ------------------------------------------------------------------------------
def player_position(status: Optional[Dict[str, Any]]) -> float:
    try:
        return float((status or {}).get("position_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_resume_state() -> Optional[Dict[str, Any]]:
    if not RESUME_FILE.is_file():
        return None
    try:
        data = json.loads(RESUME_FILE.read_text(encoding="utf-8", errors="ignore"))
        if isinstance(data, dict) and data.get("path") and os.path.isfile(str(data["path"])):
            return data
    except Exception:
        pass
    return None


def _pick_start_track(playlist: List[str]) -> Tuple[Optional[str], float]:
    """Choose the resume track when it is still in the playlist, else the first."""
    resume = load_resume_state()
    if resume:
        saved_path = str(resume.get("path"))
        try:
            saved_pos = max(0.0, float(resume.get("position_sec", 0.0) or 0.0))
        except (TypeError, ValueError):
            saved_pos = 0.0
        if saved_path in playlist:
            return saved_path, saved_pos
    return (playlist[0], 0.0) if playlist else (None, 0.0)


def build_stream_queue(force: bool = False) -> List[str]:
    """Resolve the play queue the phones will stream through.

    Scanning the music playlist stats tens of thousands of paths, so the result
    is cached briefly. Without this every 2-second status poll would re-walk the
    library. `force` skips the cache, which is what a user-initiated "rescan"
    or a library-mode switch needs: those must see the files that exist now,
    not the ones that existed ten seconds ago.
    """
    global _queue_cache

    mode = get_library_mode()
    now = time.time()
    cached = _queue_cache
    if not force and cached and cached.get("mode") == mode and (now - cached.get("built_at", 0.0)) < QUEUE_CACHE_TTL:
        return list(cached.get("files") or ())

    with _queue_build_lock:
        # Another request may have finished the scan while this one waited. A
        # forced rebuild must not accept that result: the whole point of the
        # call is to re-read what is on disk after the user changed it.
        now = time.time()
        cached = _queue_cache
        if (not force and cached and cached.get("mode") == mode
                and (now - cached.get("built_at", 0.0)) < QUEUE_CACHE_TTL):
            return list(cached.get("files") or ())

        # An explicit playlist outranks the library mode. `WEB_COMPANION_PLAYLIST`
        # is documented as "overrides the mode" (README, config.env.example) and
        # `scan_playlist_files()` exists to say so, but the streaming queue stopped
        # consulting it, so a pinned playlist was silently replaced by whichever
        # library the toggle happened to be on. Resolve it first, and keep the
        # mode-only path below untouched for every operator who never sets it.
        explicit = _config_value("WEB_COMPANION_PLAYLIST", "").strip()
        if explicit:
            files = scan_playlist_files()
            _queue_source_state.update({
                "fallback": False,
                "music_offline": False,
                "detail": "" if files else (
                    f"The playlist named by WEB_COMPANION_PLAYLIST is empty or missing ({explicit})"
                ),
            })
        elif mode == LIBRARY_MODE_MUSIC:
            files = scan_music_files()
            # The Music button loads the music playlist, and it must NOT quietly
            # become the mix archive: that once made the list say it was loading
            # tracks and then fill with mixes. But when the shares are dead the
            # page must not sit on 55k unreadable rows pretending to connect
            # forever either - `music_offline` lets the dashboard say what is
            # actually wrong and offer the archive in one tap.
            if files and not _music_sample_reachable(files):
                detail = (
                    "The music folders are offline. Reconnect the shares, "
                    "or switch to the Mix Archive to keep listening."
                )
            elif not files:
                detail = "No music tracks found in the music library"
            else:
                detail = ""
            _queue_source_state.update({
                "fallback": False,
                "detail": detail,
                "music_offline": bool(files) and bool(detail),
            })
        elif mode == LIBRARY_MODE_FAVORITES:
            files = scan_favorite_files()
            # An empty Favorites playlist is a normal state, not a broken
            # library, so it is reported as empty instead of borrowed from
            # another mode. Marking tracks with [H] in the player fills it.
            _queue_source_state.update({
                "fallback": False,
                "music_offline": False,
                "detail": "" if files else "No favorites yet — tap the heart on a mix",
            })
        else:
            _queue_source_state.update({"fallback": False, "detail": "", "music_offline": False})
            files = scan_archive_files()
            # A launchd/systemd job on macOS is denied read access to external
            # volumes until it is approved in Full Disk Access. Without this the
            # empty scan looks like an empty archive and the phone shows
            # "No mixes found" instead of the real cause.
            if not files:
                blocked = _archive_dirs_permission_denied()
                if blocked:
                    # The gate macOS actually enforces for a USB/removable
                    # archive is kTCCServiceSystemPolicyRemovableVolumes (seen
                    # in tccd's log for this bundle), which lives under
                    # Privacy & Security > Files and Folders. Full Disk Access
                    # is the blunt alternative that also covers it.
                    _queue_source_state.update({
                        "fallback": False,
                        "detail": ("macOS blocked archive access - allow Removable Volumes "
                                   "(or Full Disk Access) for MP Web Companion.app "
                                   f"({blocked})"),
                        "blocked_path": blocked,
                    })
                else:
                    stalled = _archive_dirs_stalled()
                    if stalled:
                        # The folder exists but did not answer in time. That is
                        # a disk problem, not a permission one, and saying so
                        # avoids sending the user to System Settings for a
                        # drive that is simply asleep or unresponsive.
                        _queue_source_state.update({
                            "fallback": False,
                            "detail": ("The archive folder did not respond in time - "
                                       "the drive may be asleep, busy, or disconnected "
                                       f"({stalled}). It is retried automatically."),
                            "blocked_path": stalled,
                        })

        if files and _env_flag("WEB_COMPANION_SHUFFLE", False):
            files = list(files)
            random.shuffle(files)

        _queue_cache = {"mode": mode, "built_at": now, "files": tuple(files)}
        return list(files)


def invalidate_queue_cache() -> None:
    global _queue_cache, _music_scan_cache
    # Reset in place so a caller holding the dict (or a test that reads it by
    # key) keeps a valid object rather than a dangling None.
    if not isinstance(_queue_cache, dict):
        _queue_cache = {}
    _queue_cache["mode"] = ""
    _queue_cache["built_at"] = 0.0
    _queue_cache["files"] = ()
    _music_scan_cache = None
    # The search blob is built from that same tuple. Leaving it would let a
    # rescan answer with titles from the playlist that was just replaced.
    _playlist_search_cache["id"] = None
    _playlist_search_cache["texts"] = ()
    # A rescan must also forget which folders were unreadable. The negative
    # probe is what makes a permission fix invisible: the user grants Full Disk
    # Access, taps rescan, and the folder is still skipped from the cache, so
    # the phone keeps saying "macOS blocked archive access" for up to a minute
    # (or, with the queue cache also stale, until the service is restarted).
    _BLOCKED_DIRS.clear()
    _STALLED_DIRS.clear()


# Older name for the same operation, still used by callers and by the control
# API's "rescan" action. Kept as a real alias rather than deleted so an
# out-of-tree caller does not crash on an AttributeError.
def invalidate_stream_queue() -> None:
    invalidate_queue_cache()


# The dashboard polls /api/status, /api/playlist and /api/eq continuously, and
# every one of those used to call build_stream_queue(). As soon as the 10 s
# cache expired the next poll took _queue_build_lock and every other request
# thread queued behind the scan (a 51k-entry music playlist takes seconds, and
# an unmounted share makes it worse). The phone then had a page that stopped
# answering, which is exactly the "unresponsive dashboard" symptom.
#
# These helpers let the read-only polling endpoints serve the last good queue
# immediately and refresh it on a worker thread, so a slow scan can never block
# a status reply. Control actions still build synchronously, because they must
# act on an accurate queue.
_queue_refresh_lock = threading.Lock()
_queue_refresh_active = False


def _refresh_queue_in_background() -> None:
    """Start at most one background queue scan at a time."""
    global _queue_refresh_active
    with _queue_refresh_lock:
        if _queue_refresh_active:
            return
        _queue_refresh_active = True

    def _worker() -> None:
        global _queue_refresh_active
        try:
            build_stream_queue()
        except Exception:
            pass
        finally:
            with _queue_refresh_lock:
                _queue_refresh_active = False

    threading.Thread(target=_worker, name="mp-queue-refresh", daemon=True).start()


def build_stream_queue_cached() -> List[str]:
    """The stream queue for read-only endpoints: never waits on a scan.

    A populated cache is returned as-is, and a stale entry is refreshed on a
    worker thread while the caller answers from the old copy.

    A cold cache builds synchronously, which keeps control actions and the
    tests on a predictable contract. The one case that must not block is a
    scan already in progress (the startup prewarm, or a background refresh
    after an invalidation): waiting for that lock is what made the first polls
    after a reboot hang for minutes while the CIFS archive was walked. Then the
    caller gets the last known queue (empty) and the running scan fills it in.
    """
    mode = get_library_mode()
    cached = _queue_cache
    if cached and cached.get("mode") == mode:
        if (time.time() - cached.get("built_at", 0.0)) >= QUEUE_CACHE_TTL:
            _refresh_queue_in_background()
        return list(cached.get("files") or ())
    if _queue_build_lock.locked():
        # A scan is already underway; do not queue behind it.
        return list((cached or {}).get("files") or ())
    return build_stream_queue()


def _prewarm_queue_at_startup() -> None:
    """Build the queue in the background so the first poll finds a cache.

    Without this the first request after a restart paid the whole scan on a
    request thread.
    """
    try:
        build_stream_queue()
    except Exception:
        pass


def start_prewarm_worker() -> threading.Thread:
    thread = threading.Thread(
        target=_prewarm_queue_at_startup,
        name="mp-companion-queue-prewarm",
        daemon=True,
    )
    thread.start()
    return thread


def autoplay_once() -> Dict[str, Any]:
    """Arm the stream at startup so a phone starts playing with no extra taps.

    Nothing is played on the host: the session is only marked as playing, which
    makes /api/status report a running stream so the browser dashboard points
    its <audio> element at /api/stream and autoplays. The phone is the only
    audio output.
    """
    if _autoplay_done.is_set():
        return dict(_autoplay_result)

    if not _env_flag("WEB_COMPANION_AUTOPLAY", True):
        _autoplay_result.update(status="disabled", detail="Autoplay disabled by WEB_COMPANION_AUTOPLAY")
        _autoplay_done.set()
        return dict(_autoplay_result)

    playlist = build_stream_queue()
    track, position = _pick_start_track(playlist)
    if not track:
        # build_stream_queue() already knows why the library is empty - most
        # often a macOS privacy block on the archive volume. Report that instead
        # of a generic "empty playlist" so the phone can show the fix.
        reason = _queue_source_state.get("detail") or "Playlist is empty"
        _autoplay_result.update(status="empty_playlist", detail=reason)
        _autoplay_done.set()
        return dict(_autoplay_result)

    # Resume where the last session stopped, otherwise start at the top.
    with _stream_lock:
        state = load_stream_session()
        state["path"] = track
        try:
            state["index"] = playlist.index(track)
        except ValueError:
            state["index"] = 0
        state["duration_sec"] = probe_duration(track)
        state["position_sec"] = clamp_position(state, position)
        state["playing"] = True
        state["started_at"] = time.time()
        save_stream_session(state)

    _autoplay_result.update(
        status="ready",
        detail=f"Auto-streaming {MODE_LABELS.get(get_library_mode(), 'library')} ({len(playlist)} items)",
        match=track,
        track=os.path.basename(track),
        position=round(float(state.get("position_sec", 0.0) or 0.0), 2),
        queue_len=len(playlist),
        mode=get_library_mode(),
        verified=True,
        verified_position=round(float(state.get("position_sec", 0.0) or 0.0), 2),
    )
    _autoplay_done.set()
    return dict(_autoplay_result)


def start_autoplay_worker() -> threading.Thread:
    thread = threading.Thread(target=autoplay_once, name="mp-companion-stream-queue", daemon=True)
    thread.start()
    return thread


def autoplay_state() -> Dict[str, Any]:
    return dict(_autoplay_result)



# ------------------------------------------------------------------------------
# TRACKLIST RESOLUTION & METADATA PARSING
# ------------------------------------------------------------------------------
_COVER_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_COVER_NAMES = (
    "Cover.png", "cover.png", "Cover.jpg", "cover.jpg", "cover.jpeg",
    "folder.jpg", "Folder.jpg", "folder.png",
)
# path -> (expires_at, resolved cover or ""). Status polls must not stat a
# handful of siblings on every tick.
_artwork_cache: Dict[str, Tuple[float, str]] = {}
_ARTWORK_TTL = 30.0


def _find_sibling_cover(audio: Path) -> Optional[Path]:
    """A picture sitting next to the mix, never a symlink that leaves its folder."""
    parent = audio.parent
    names = [audio.stem + ext for ext in _COVER_SUFFIXES]
    names.extend(_COVER_NAMES)
    try:
        parent_resolved = parent.resolve()
    except OSError:
        return None
    for name in names:
        cand = parent / name
        try:
            if not cand.is_file():
                continue
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved.parent != parent_resolved:
            continue
        if resolved.suffix.lower() not in _COVER_SUFFIXES:
            continue
        return resolved
    return None


def artwork_for_stream(file_path: str) -> Optional[Path]:
    """Cover image for a library mix, or None when there is nothing safe to show.

    The query is an audio path. The picture is chosen here, so a client cannot
    name an image of its own and read it through this route.
    """
    try:
        audio = sec.safe_stream_path(file_path, stream_roots())
    except Exception:
        audio = None
    if audio is None:
        return None
    now = time.time()
    key = str(audio)
    hit = _artwork_cache.get(key)
    if hit and hit[0] > now:
        return Path(hit[1]) if hit[1] else None
    found = _find_sibling_cover(audio)
    if len(_artwork_cache) > 2048:
        _artwork_cache.clear()
    _artwork_cache[key] = (now + _ARTWORK_TTL, str(found) if found else "")
    return found


def image_content_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")


def app_icon_path() -> Optional[Path]:
    """The install icon, used for the home-screen shortcut and a blank lock screen."""
    base = get_base_dir()
    for name in ("mp_liquid_glass_icon.png", "dreamworld_aurora.png", "Cover.png"):
        cand = base / "assets" / name
        if cand.is_file():
            return cand
    return None


def web_manifest(theme: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Add-to-home-screen description. The page itself stays network-fresh."""
    palette = theme or {}
    theme_color = str(palette.get("bg") or "#000000")
    if not theme_color.startswith("#"):
        theme_color = "#000000"
    background = str(palette.get("bg") or "#000000")
    if not background.startswith("#"):
        background = "#000000"
    return {
        "name": "MP Audio Player",
        "short_name": "MP Audio",
        "description": "Studio and DJ booth stream",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": background,
        "theme_color": theme_color,
        "icons": [{
            "src": "/icon.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        }],
    }


def find_tracklist_for_mix(mix_file: str) -> str:
    if not mix_file:
        return ""
    if mix_file in _tracklist_cache:
        return _tracklist_cache[mix_file]

    p = Path(mix_file).expanduser().resolve()
    base = get_base_dir()
    stem = p.stem

    exact = p.parent / f"{stem}.txt"
    if exact.is_file():
        _tracklist_cache[mix_file] = str(exact)
        return str(exact)

    candidate_dirs = [
        p.parent,
        p.parent / "FLAC_CONVERTED_OUTPUTS",
        p.parent.parent / "FLAC_CONVERTED_OUTPUTS",
        base / "FLAC_CONVERTED_OUTPUTS",
        base / "MIX_ARCHIVE" / "FLAC_CONVERTED_OUTPUTS",
        Path.home() / "GoogleDrive" / "MIX_ARCHIVE" / "FLAC_CONVERTED_OUTPUTS"
    ]

    for d in candidate_dirs:
        if d.is_dir():
            c = d / f"{stem}.txt"
            if c.is_file():
                _tracklist_cache[mix_file] = str(c)
                return str(c)

    m = re.search(r'[_\ -](\d{2,3})([_\ -]|$)', stem)
    mix_num = m.group(1) if m else None

    if mix_num:
        for d in candidate_dirs:
            if d.is_dir():
                try:
                    for f in d.iterdir():
                        if f.is_file() and f.suffix.lower() == ".txt":
                            if mix_num in f.name:
                                _tracklist_cache[mix_file] = str(f)
                                return str(f)
                except Exception:
                    pass

    _tracklist_cache[mix_file] = ""
    return ""


_parsed_tracklist_cache: Dict[str, Dict[str, Any]] = {}


def parse_tracklist_file(tracklist_path: str) -> Dict[str, Any]:
    if not tracklist_path or not os.path.isfile(tracklist_path):
        return {"metadata": {}, "tracks": []}
    if tracklist_path in _parsed_tracklist_cache:
        return _parsed_tracklist_cache[tracklist_path]
    try:
        with open(tracklist_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        metadata: Dict[str, str] = {}
        tracks: List[str] = []
        in_tracklist = False
        for line in content.splitlines():
            s = line.strip()
            if not s:
                continue
            if "TRACKLIST" in s.upper():
                in_tracklist = True
                continue
            if s.startswith("Artist:") and "artist" not in metadata:
                metadata["artist"] = s.split(":", 1)[1].strip()
            elif s.startswith("Album:") and "album" not in metadata:
                metadata["album"] = s.split(":", 1)[1].strip()
            elif s.startswith("Title:") and "title" not in metadata:
                metadata["title"] = s.split(":", 1)[1].strip()

            if in_tracklist:
                if s.startswith("=") or s.startswith("-"):
                    continue
                if re.match(r'^\d{1,3}[\.\:\-\s]', s):
                    tracks.append(s)
            else:
                if re.match(r'^\d{1,3}[\.\:\-]\s+', s):
                    tracks.append(s)
        parsed = {"metadata": metadata, "tracks": tracks}
        _parsed_tracklist_cache[tracklist_path] = parsed
        return parsed
    except Exception:
        return {"metadata": {}, "tracks": []}


def clean_track_title(raw_title: str, raw_path: str, tl_meta: Dict[str, str]) -> Tuple[str, str, str]:
    if tl_meta.get("title"):
        return tl_meta["title"], tl_meta.get("artist", "MPlanetarian"), tl_meta.get("album", "Stream of Frequency")

    if not raw_title and raw_path:
        raw_title = Path(raw_path).name

    stem = Path(raw_title).stem
    clean = re.sub(r'[\uf000-\uf8ff]', ':', stem).strip().replace('_', ' ')
    clean = re.sub(r'\s+', ' ', clean).strip()

    artist, album, title = "MPlanetarian", "Stream of Frequency", clean
    if " - " in clean:
        parts = clean.split(" - ", 1)
        artist, title = parts[0].strip(), parts[1].strip()

    return title, artist, album


# ------------------------------------------------------------------------------
# STREAMING SESSION
#
# The companion tracks "which file is being served" and a coarse elapsed clock,
# so the phone dashboard has a position and a progress bar. It is deliberately
# NOT an authority on playback: the browser owns that. Nothing here opens an
# audio device.
# ------------------------------------------------------------------------------
VOLUME_FILE = CONFIG_DIR / "web_companion_stream.json"
_stream_lock = threading.Lock()


def _stream_state_path() -> Path:
    return VOLUME_FILE


def load_stream_session() -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "index": 0,
        "path": "",
        "playing": False,
        "position_sec": 0.0,
        "started_at": 0.0,
        "duration_sec": 0.0,
        "volume": 100,
        # Mute keeps `volume` so a second tap can restore it. This key has to
        # live in the template or the next read drops it and unmute is lost.
        "muted": False,
        # DJ pitch, in percent, matching MP Audio Player (-20..+20). It has to
        # live in this template: load_stream_session() restores only the keys
        # listed here, so a value written beside them is dropped on the next read
        # and the pitch buttons appear to do nothing.
        "pitch_percent": 0,
        "shuffle": False,
        "repeat": "all",
        # Set when every candidate in the queue has been proven undecodable.
        # It has to be part of the template: load_stream_session() restores only
        # the keys listed here, so a flag written outside it is silently lost on
        # the next read and the dashboard never hears that the playlist is dead.
        "playback_exhausted": False,
    }
    if _stream_state_path().is_file():
        try:
            saved = json.loads(_stream_state_path().read_text(encoding="utf-8", errors="ignore"))
            if isinstance(saved, dict):
                state.update({k: saved[k] for k in state if k in saved})
        except Exception:
            pass
    return state


def save_stream_session(state: Dict[str, Any]) -> None:
    try:
        _stream_state_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _stream_state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(_stream_state_path())
    except Exception:
        pass


def clamp_pitch(value: Any) -> int:
    """DJ pitch percent, the same -20..+20 range as MP Audio Player."""
    try:
        percent = int(value)
    except (TypeError, ValueError):
        percent = 0
    return max(-20, min(20, percent))


def pitch_factor_for(percent: Any) -> float:
    """Playback rate for a pitch percent. +10% plays at 1.10x, like the desk."""
    return 1.0 + (clamp_pitch(percent) / 100.0)


def stream_position(state: Dict[str, Any]) -> float:
    """Elapsed seconds within the current file, accounting for active playback.

    Wall time is scaled by the DJ pitch. A mix at +10% reaches the next second
    of audio sooner, and the resume clock has to follow that or a later
    listener starts behind the point this one actually heard.
    """
    base = float(state.get("position_sec", 0.0) or 0.0)
    if state.get("playing") and state.get("started_at"):
        elapsed = max(0.0, time.time() - float(state["started_at"]))
        base += elapsed * pitch_factor_for(state.get("pitch_percent", 0))
    return max(0.0, base)


def _set_stream_pitch(state: Dict[str, Any], percent: Any) -> None:
    """Store a new pitch without rescoring the time already played.

    stream_position() multiplies the whole interval since started_at by the
    current factor. Changing the factor in place would jump the clock by that
    whole interval times the difference.
    """
    state["position_sec"] = clamp_position(state, stream_position(state))
    state["started_at"] = time.time() if state.get("playing") else 0.0
    state["pitch_percent"] = clamp_pitch(percent)


def clamp_position(state: Dict[str, Any], pos: float) -> float:
    duration = float(state.get("duration_sec", 0.0) or 0.0)
    pos = max(0.0, float(pos))
    return min(pos, duration) if duration > 0 else pos


# The service arms the session when it boots and nothing plays on the host, so
# the elapsed clock keeps running even when no phone is listening. After a long
# idle stretch it reports the *end* of the mix as the position (#repro: a 45 s
# track left for 83 minutes reported 45.0/45.0). A phone that then engages
# seeks to the last frame, play() resolves into "ended" immediately, and the
# listener sees a button that does nothing.
#
# Playback is owned by the browser, so a position that close to the end is not
# a resume point: it is an unfinished unattended run. Starting the mix from the
# top is the only useful answer, and an actually-listening phone never asks for
# the tail because it keeps its own local clock.
RESUME_END_MARGIN_MIN = 2.0
RESUME_END_MARGIN_RATIO = 0.02


def resume_point(state: Dict[str, Any], duration: float = 0.0) -> float:
    """Where a phone picking up this session should start, in seconds.

    Returns 0.0 whenever the recorded position is not safely inside the file
    (an overrun, or the final moments that cannot be buffered before the media
    ends). Otherwise returns the elapsed position.
    """
    dur = float(duration or state.get("duration_sec", 0.0) or 0.0)
    pos = clamp_position(state, stream_position(state))
    if dur <= 0:
        # No length known: a bare elapsed value is the only hint available.
        return pos
    margin = max(RESUME_END_MARGIN_MIN, dur * RESUME_END_MARGIN_RATIO)
    if pos < 0.5 or pos >= (dur - margin):
        return 0.0
    return pos


# ------------------------------------------------------------------------------
# UNPLAYABLE FILE LEDGER
#
# The queue gate (is_streamable_file) can only judge the shapes it knows how to
# read. Everything else is proven by the device that actually tries to decode
# it: the phone reports a media error for a file, and that verdict is what stops
# the next phone - or the next poll on the same phone - from being handed the
# same dead URL.
#
# The ledger is keyed by path and stamped with (mtime, size), so it is not a
# permanent blacklist: re-exporting or replacing the file changes its signature
# and it becomes a candidate again automatically.
#
# It is persisted because the file outlives the process. A mix that a phone
# proved undecodable stays on the archive, and a service restart (a tray toggle,
# a reboot) would otherwise forget the verdict and hand the same dead URL to the
# next listener - which is exactly the report this fix is for. The signature is
# what makes persistence safe: a repaired file no longer matches, so a stale
# entry can never outlive the repair.
# ------------------------------------------------------------------------------
_unplayable_lock = threading.Lock()
# path -> (st_mtime, st_size) of the copy that failed
_unplayable_files: Dict[str, Tuple[float, int]] = {}
UNPLAYABLE_FILE = CONFIG_DIR / "web_companion_unplayable.json"
_UNPLAYABLE_MAX = 5000


def _load_unplayable() -> None:
    """Restore the ledger so a restart does not re-serve a proven-dead mix."""
    try:
        if not UNPLAYABLE_FILE.is_file():
            return
        raw = json.loads(
            UNPLAYABLE_FILE.read_text(encoding="utf-8", errors="ignore")
        )
        if not isinstance(raw, dict):
            return
        loaded: Dict[str, Tuple[float, int]] = {}
        for path, stamp in raw.items():
            if (isinstance(stamp, list) and len(stamp) == 2
                    and isinstance(stamp[0], (int, float))
                    and isinstance(stamp[1], int)):
                loaded[str(path)] = (float(stamp[0]), int(stamp[1]))
        with _unplayable_lock:
            _unplayable_files.update(loaded)
    except Exception:
        pass


def _save_unplayable() -> None:
    try:
        with _unplayable_lock:
            if len(_unplayable_files) > _UNPLAYABLE_MAX:
                # Keep this bounded: the archive only grows, and a caller that
                # fails many files must not grow the file without limit.
                keep = dict(list(_unplayable_files.items())[-_UNPLAYABLE_MAX:])
                _unplayable_files.clear()
                _unplayable_files.update(keep)
            snapshot = {p: [s[0], s[1]] for p, s in _unplayable_files.items()}
        UNPLAYABLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = UNPLAYABLE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        tmp.replace(UNPLAYABLE_FILE)
    except Exception:
        pass


def mark_unplayable(path: Any) -> bool:
    """Record that a device could not decode this file. False if unknown."""
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        st = os.stat(raw)
    except OSError:
        # Gone entirely: record it with a sentinel so it is skipped until a
        # rescan drops it from the queue anyway.
        with _unplayable_lock:
            _unplayable_files[raw] = (-1.0, -1)
        _save_unplayable()
        return True
    with _unplayable_lock:
        _unplayable_files[raw] = (float(st.st_mtime), int(st.st_size))
    _save_unplayable()
    return True


def clear_unplayable(path: Any = None) -> None:
    """Forget failed files: one path, or the whole ledger on a rescan."""
    with _unplayable_lock:
        if path is None:
            changed = bool(_unplayable_files)
            _unplayable_files.clear()
        else:
            changed = _unplayable_files.pop(str(path or "").strip(), None) is not None
    if changed:
        _save_unplayable()


def is_unplayable(path: Any) -> bool:
    """True when this exact copy of the file has already failed to decode."""
    raw = str(path or "").strip()
    if not raw:
        return False
    with _unplayable_lock:
        stamp = _unplayable_files.get(raw)
    if stamp is None:
        return False
    try:
        st = os.stat(raw)
    except OSError:
        # The recorded file no longer exists; nothing to skip, and the queue
        # rebuild will drop it. Do not keep it in the ledger forever.
        clear_unplayable(raw)
        return False
    if (float(st.st_mtime), int(st.st_size)) == stamp:
        return True
    # The file changed (re-exported, replaced, repaired): the old verdict no
    # longer describes what is on disk, so the new copy gets a fresh chance.
    clear_unplayable(raw)
    return False


def next_playable_index(queue: List[str], start: int,
                        step: int = 1) -> Optional[int]:
    """First index at or after `start` in walk order whose file may play.

    Returns None when every candidate has already failed. The walk is bounded
    by the queue length, so it terminates even when the whole queue is dead.
    """
    if not queue:
        return None
    n = len(queue)
    start = int(start or 0)
    for offset in range(n):
        idx = (start + offset * step) % n
        if not is_unplayable(queue[idx]):
            return idx
    return None


# A queue entry is only handed to a device if it can be opened right now. The
# verdict is cached for a few seconds because the status poll asks repeatedly
# and a network mount read costs a round trip: long enough to keep polling
# cheap, short enough that a share coming back is noticed almost at once.
_READABLE_TTL = 3.0
_readable_cache: Dict[str, Tuple[float, bool]] = {}


def _entry_readable(path: str) -> bool:
    now = time.time()
    hit = _readable_cache.get(path)
    if hit and (now - hit[0]) < _READABLE_TTL:
        return hit[1]
    verdict = _path_readable_now(path)
    if len(_readable_cache) > 4096:
        _readable_cache.clear()
    _readable_cache[path] = (now, verdict)
    return verdict


def next_readable_index(queue: List[str], start: int) -> Optional[int]:
    """First index at or after `start` whose file can actually be read.

    Kept for callers that want to resolve a starting point before a session is
    created (the engage path). It is deliberately NOT used to keep the serving
    cursor honest on every poll: see the note in `current_stream_file()` - a
    probe-driven cursor alternates between files on a flaky mount and the
    dashboard restarts its player on every change.
    """
    if not queue:
        return None
    start = int(start or 0) % len(queue)
    for offset in range(len(queue)):
        idx = (start + offset) % len(queue)
        if _entry_readable(queue[idx]):
            return idx
    return None


def unplayable_count(queue: Optional[List[str]] = None) -> int:
    """How many entries of the queue have already failed to decode.

    Bounded to the queue so the count describes this library, not every path
    this process happens to remember from a previous mode.
    """
    try:
        candidates = queue if queue is not None else build_stream_queue_cached()
    except Exception:
        return 0
    return sum(1 for path in candidates if is_unplayable(path))


def current_stream_file(cached: bool = False) -> Tuple[str, Dict[str, Any]]:
    """Return the file the phone should currently be streaming.

    `cached` is for the polling endpoints: they must answer instantly even
    while a large library scan runs, so they read the last good queue.

    A file the browser has already told us it cannot decode is skipped here,
    not only in the control action that recorded it. That keeps every consumer
    (status, EQ, playlist, a late poll) on the same surviving file, so a phone
    can never be handed the same dead URL twice.
    """
    queue = build_stream_queue_cached() if cached else build_stream_queue()
    state = load_stream_session()
    if not queue:
        return "", state
    idx = int(state.get("index", 0) or 0)
    if idx < 0 or idx >= len(queue):
        idx = 0
        state["index"] = 0
    # Keep the saved path authoritative if it is still present in the queue.
    saved = str(state.get("path", "") or "")
    if saved and saved in queue:
        idx = queue.index(saved)
    else:
        # The saved file is gone from the queue. That happens when a mix is
        # renamed, deleted, or dropped by is_streamable_file() because it is a
        # header-only WAV. The cursor moves to the new entry, so the elapsed
        # clock belongs to a different file now: carrying it over would resume
        # the replacement mix at an arbitrary mid-point (or at its very end,
        # which looks like a dead player). Start the new file at the top.
        if saved:
            state["position_sec"] = 0.0
            state["duration_sec"] = 0.0
            # started_at must follow `playing`, exactly like switch() and
            # select_index() do. Leaving it at 0 while playing stays True makes
            # stream_position() treat the clock as inactive, so the phone would
            # show a frozen 00:00:00 for the whole mix.
            state["started_at"] = time.time() if state.get("playing") else 0.0
        state["path"] = queue[idx]
    # Step past any file this process has been told is undecodable. The walk is
    # bounded by the queue length so a queue of nothing but failures settles on
    # the last candidate instead of spinning.
    if is_unplayable(state["path"]):
        replacement = next_playable_index(queue, idx)
        if replacement is None:
            # Nothing in the queue has been proven playable. Still return a
            # real path rather than "" so the dashboard can name the failure
            # and offer a retry, instead of pretending the library is empty.
            return state["path"], state
        idx = replacement
        state["index"] = idx
        state["path"] = queue[idx]
        state["position_sec"] = 0.0
        state["duration_sec"] = 0.0
        state["started_at"] = time.time() if state.get("playing") else 0.0
    # Deliberately NO readability re-point here.
    #
    # A first attempt did exactly that - skip to the next entry whose file
    # opens - and it caused a reload loop in the browser. The verdict comes from
    # a short-lived probe, so on a flaky mount it flips between "readable" and
    # "not", which makes this function name a DIFFERENT file on alternate polls:
    # a, b, a, b. The dashboard treats a changed active_file as a track change
    # and re-points its <audio> element every time, so the player restarted
    # roughly every two seconds. The file the listener is on must be stable;
    # unreadable entries are dealt with where the failure is actually known -
    # a 404 from /api/stream (skipped by the page) and the unplayable ledger.
    return queue[idx], state


def _follow_stream(follow: str) -> bool:
    """True when this request belongs to the page's own stream, not the desk.

    A browser that has engaged audio is a player in its own right. Its polls
    must keep naming the file that browser is hearing, even while MP Audio
    Player is also running on the workstation.
    """
    return str(follow or "").strip().lower() in ("stream", "web", "phone")


# ------------------------------------------------------------------------------
# SERVER-SIDE SPECTRUM FOR THE iOS VISUALIZER
#
# iOS keeps the <audio> element on the native media pipeline so background
# playback survives, which also means no AnalyserNode can hear it. The page
# already falls back to /api/eq for that case, but the phone stream had no
# levels to report there and the equaliser sat flat. Decode a short window of
# the file the phone is hearing with ffmpeg and turn it into the same 24 bands
# the browser analyser produces.
# ------------------------------------------------------------------------------
_STREAM_EQ_CACHE: Dict[str, Dict[str, Any]] = {}
_STREAM_EQ_FAILED: Dict[str, float] = {}
_STREAM_EQ_LOCK = threading.Lock()
_STREAM_EQ_WINDOW = 4.0
_STREAM_EQ_RATE = 22050
_STREAM_EQ_FPS = 4.0
_STREAM_EQ_FRAME = 2048


def _spectrum_bands(samples: Any, rate: int, bands: int = 24) -> List[float]:
    """24 log-spaced band levels from mono float samples, analyser-style."""
    try:
        import numpy as np
    except Exception:
        return [0.0] * bands
    if samples is None or len(samples) < 256:
        return [0.0] * bands
    frame = np.asarray(samples, dtype=np.float32)
    # A Hann window keeps the low bands from leaking across the spectrum.
    window = np.hanning(len(frame)).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frame * window))
    freqs = np.fft.rfftfreq(len(frame), 1.0 / float(rate))
    # Full-scale int16 is 1.0, matching the browser analyser's dB scale.
    magnitude = spectrum / (max(1.0, float(np.sum(window)) / 2.0)) / 32768.0
    db = 20.0 * np.log10(np.maximum(magnitude, 1e-9))
    edges = np.geomspace(30.0, min(18000.0, rate / 2.0), bands + 1)
    levels: List[float] = []
    for index in range(bands):
        mask = (freqs >= edges[index]) & (freqs < edges[index + 1])
        if not bool(np.any(mask)):
            levels.append(0.0)
            continue
        # The browser analyser maps -90..-20 dBFS to 0..255. Use the strongest
        # bin in the band so a narrow tone is visible instead of averaged away.
        value = float(np.max(db[mask]))
        levels.append(max(0.0, min(1.0, (value + 90.0) / 70.0)))
    return levels


def _decode_stream_window(path: str, start: float, duration: float) -> Optional[Any]:
    try:
        import numpy as np
    except Exception:
        return None
    ffmpeg = shutil_which("ffmpeg")
    if not ffmpeg:
        return None
    command = [
        ffmpeg, "-v", "error", "-nostdin",
        "-ss", f"{max(0.0, start):.3f}",
        "-i", path,
        "-t", f"{max(0.25, duration):.3f}",
        "-vn", "-map", "0:a:0",
        "-ac", "1", "-ar", str(_STREAM_EQ_RATE),
        "-f", "s16le", "-",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=8, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return np.frombuffer(result.stdout, dtype="<i2").astype(np.float32)


def stream_spectrum_levels(path: str, position: float, bands: int = 24) -> List[float]:
    """Spectrum for the file and position a phone is hearing.

    The window is cached for a few seconds, so a 2-second status poll does not
    spawn ffmpeg every time. Any failure returns zeros: a visualizer must never
    make playback or the status request fail.
    """
    if not path:
        return [0.0] * bands
    try:
        stat_result = os.stat(path)
    except OSError:
        return [0.0] * bands
    now = time.time()
    key = f"{path}|{stat_result.st_size}|{int(stat_result.st_mtime)}"
    position = max(0.0, float(position or 0.0))
    with _STREAM_EQ_LOCK:
        failed_at = _STREAM_EQ_FAILED.get(key, 0.0)
        if failed_at and now - failed_at < 20.0:
            return [0.0] * bands
        entry = _STREAM_EQ_CACHE.get(key)
        if entry and entry["start"] <= position <= entry["end"] + 0.5:
            frames = entry["frames"]
            if frames:
                nearest = min(frames, key=lambda frame: abs(frame[0] - position))
                return list(nearest[1])
    start = max(0.0, position - 0.5)
    samples = _decode_stream_window(path, start, _STREAM_EQ_WINDOW)
    if samples is None or len(samples) < _STREAM_EQ_FRAME:
        with _STREAM_EQ_LOCK:
            _STREAM_EQ_FAILED[key] = now
        return [0.0] * bands
    hop = max(1, int(_STREAM_EQ_RATE / _STREAM_EQ_FPS))
    frames: List[Tuple[float, Tuple[float, ...]]] = []
    for offset in range(0, max(0, len(samples) - _STREAM_EQ_FRAME) + 1, hop):
        frame = samples[offset:offset + _STREAM_EQ_FRAME]
        levels = _spectrum_bands(frame, _STREAM_EQ_RATE, bands)
        frames.append((start + offset / float(_STREAM_EQ_RATE), tuple(levels)))
    if not frames:
        with _STREAM_EQ_LOCK:
            _STREAM_EQ_FAILED[key] = now
        return [0.0] * bands
    end = start + len(samples) / float(_STREAM_EQ_RATE)
    with _STREAM_EQ_LOCK:
        _STREAM_EQ_CACHE[key] = {"start": start, "end": end, "frames": frames, "built": now}
        _STREAM_EQ_FAILED.pop(key, None)
        if len(_STREAM_EQ_CACHE) > 4:
            oldest = min(_STREAM_EQ_CACHE, key=lambda item: _STREAM_EQ_CACHE[item]["built"])
            if oldest != key:
                _STREAM_EQ_CACHE.pop(oldest, None)
    nearest = min(frames, key=lambda frame: abs(frame[0] - position))
    return list(nearest[1])


def get_eq_status(follow: str = "") -> Dict[str, Any]:
    # The host player is the authority on what is on air, so the visualizer and
    # the progress bar must read its clock, not the phone-facing stream cursor.
    # An engaged web player opts out: its clock is the stream session.
    host = None if _follow_stream(follow) else host_player_payload()
    if host is not None:
        return {
            "state": host["state"],
            "position": host["position"],
            "duration": host["duration"],
            "position_sec": host["position_sec"],
            "duration_sec": host["duration_sec"],
            "progress_pct": host["progress_pct"],
            "volume": host["volume"],
            "muted": bool(host.get("muted", False)),
            "pitch_percent": host["pitch_percent"],
            "shuffle": host["shuffle"],
            "repeat": host["repeat"],
            "levels": host["levels"],
            "peaks": host["peaks"],
        }

    active_file, state = current_stream_file(cached=True)
    playing = bool(state.get("playing"))
    pos = stream_position(state)
    dur = float(state.get("duration_sec", 0.0) or 0.0)
    if dur <= 0 and active_file:
        dur = probe_duration(active_file)
    pct = round(pos / dur * 100.0, 1) if dur > 0 else 0.0
    # The phone's own element is the only audio output, so the server cannot
    # observe it. Decode the file it is hearing instead. This is what feeds the
    # iOS equalizer while the native media pipeline keeps background playback
    # alive; other browsers still use their local AnalyserNode.
    levels = stream_spectrum_levels(active_file, pos) if (playing and active_file) else [0.0] * 24
    return {
        "state": "playing" if playing else ("paused" if active_file else "stopped"),
        "position": format_seconds(pos),
        "duration": format_seconds(dur) if dur > 0 else "--:--",
        "position_sec": round(pos, 2),
        "duration_sec": round(dur, 2),
        "progress_pct": pct,
        "volume": int(state.get("volume", 100)),
        "muted": bool(state.get("muted", False)),
        "pitch_percent": clamp_pitch(state.get("pitch_percent", 0)),
        "shuffle": bool(state.get("shuffle", False)),
        "repeat": str(state.get("repeat", "all")),
        "levels": levels,
        "peaks": list(levels),
    }



def format_seconds(value: Any) -> str:
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError
        total = int(max(0.0, seconds))
    except (TypeError, ValueError):
        total = 0
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


_duration_cache: Dict[str, Tuple[float, float, float]] = {}
# A corrupt or absent length field must not reach the dashboard as a duration.
# libsndfile reports 2**63-1 frames (about 2.9 million years) for a FLAC that
# does not declare its total samples; the phone then shows that as the length
# and computes a progress bar that can never move.
MAX_PLAUSIBLE_AUDIO_SEC = 7 * 24 * 3600


def _plausible_duration(seconds: Any) -> float:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value) or value <= 0.0 or value > MAX_PLAUSIBLE_AUDIO_SEC:
        return 0.0
    return value


def probe_duration(path: str) -> float:
    """Read only the container header for length. Never decodes audio."""
    try:
        st = os.stat(path)
    except OSError:
        return 0.0
    cached = _duration_cache.get(path)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    duration = 0.0
    try:
        import soundfile  # header-only probe; no device, no decoding
        duration = _plausible_duration(soundfile.info(path).duration)
    except Exception:
        duration = 0.0
    if duration <= 0.0:
        duration = _wav_duration(path)
    if duration <= 0.0:
        # soundfile lives in the user site-packages, which a systemd start may
        # not resolve. ffprobe reads the same header and is package-managed.
        duration = _ffprobe_duration(path)
    duration = _plausible_duration(duration)
    _duration_cache[path] = (st.st_mtime, st.st_size, duration)
    return duration


def _ffprobe_duration(path: str) -> float:
    """Container length from ffprobe, used when soundfile is unavailable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=6,
        )
        if out.returncode == 0:
            return max(0.0, float((out.stdout or "").strip() or 0.0))
    except Exception:
        pass
    return 0.0


def _wav_duration(path: str) -> float:
    """Fallback for plain PCM WAV: data chunk size / byte rate."""
    try:
        with open(path, "rb") as f:
            header = f.read(64)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return 0.0
        byte_rate = int.from_bytes(header[28:32], "little")
        data_size = int.from_bytes(header[40:44], "little")
        if byte_rate > 0 and data_size > 0:
            return data_size / float(byte_rate)
    except Exception:
        pass
    return 0.0


# ------------------------------------------------------------------------------
# HOST PLAYER (READ-ONLY)
#
# "Now playing" means the track the workstation's MP Audio Player is actually
# rendering. The companion's own stream session below only knows which file a
# phone should pull over HTTP, which is not the same thing: it is a queue
# cursor with an unattended clock, and it drifts away from the host the moment
# playback is started, stopped or skipped anywhere other than this dashboard.
#
# Reading the player is not the same as driving it: nothing here opens a sound
# device or spawns a player. The IPC command below is only ever called when the
# player is already running, so a phone tap can never start audio on the host.
# ------------------------------------------------------------------------------
def _player_paths() -> Tuple[Path, Path, Path, Path]:
    """Resolve the player's files from CONFIG_DIR at call time.

    Resolving lazily means a test (or a relocated profile) that redirects
    CONFIG_DIR also redirects host-player detection.
    """
    return (
        CONFIG_DIR / "mp_audio_player.sock",
        CONFIG_DIR / "mp_audio_player.port",
        CONFIG_DIR / "mp_player_state.json",
        CONFIG_DIR / "mp_player.pid",
    )


def is_pid_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except (OSError, ValueError):
        return False


def _recv_ipc_json(sock: socket.socket, limit: int = 1000000) -> Optional[Dict[str, Any]]:
    """Read one JSON object, including a reply that arrives in several packets.

    The player writes the reply and closes. A single recv() often returns only
    the first segment, and json.loads on that fragment fails. The companion
    then behaves as if no player exists, so the transport buttons edit an
    unused session while the mix the listener hears does not move.
    """
    chunks: List[bytes] = []
    total = 0
    while total < limit:
        try:
            raw = sock.recv(65536)
        except socket.timeout:
            break
        if not raw:
            break
        chunks.append(raw)
        total += len(raw)
        try:
            response = json.loads(b"".join(chunks).decode("utf-8", errors="ignore"))
        except ValueError:
            continue
        return response if isinstance(response, dict) else None
    return None


def query_player_ipc(timeout: float = 0.35) -> Optional[Dict[str, Any]]:
    """Live status over the player's socket. None when it is not answering."""
    socket_path, port_file, _state, _pid = _player_paths()
    has_unix = hasattr(socket, "AF_UNIX")
    if has_unix:
        if not socket_path.exists():
            return None
    elif not port_file.is_file():
        return None
    sock = None
    try:
        if has_unix:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(str(socket_path))
        else:
            sock = socket.create_connection(
                ("127.0.0.1", int(port_file.read_text().strip())), timeout
            )
            sock.settimeout(timeout)
        sock.sendall(json.dumps({"cmd": "status"}).encode("utf-8"))
        response = _recv_ipc_json(sock)
        if response:
            data = response.get("data")
            if response.get("status") == "ok" and isinstance(data, dict):
                return data
            if "path" in response:
                return response
    except Exception:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    return None


def read_player_state_file() -> Optional[Dict[str, Any]]:
    _socket, _port, state_path, _pid = _player_paths()
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def host_player_status() -> Tuple[Optional[Dict[str, Any]], str]:
    """Status of the player that is actually running, plus where it came from.

    Returns (None, "none") when no live player exists. A state file left behind
    by a dead daemon is deliberately ignored: reporting a stopped mix as "now
    playing" is the same class of lie as the wrong title.
    """
    live = query_player_ipc()
    if live is not None:
        pid = live.get("pid")
        # The socket answering is already proof of a live daemon; the pid field
        # is only a cross-check for older IPC payloads.
        if not pid or is_pid_alive(pid):
            return live, "ipc_socket"

    state = read_player_state_file()
    if state is None:
        return None, "none"
    pid = state.get("pid")
    if not pid:
        _socket, _port, _state_path, pid_file = _player_paths()
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
    if is_pid_alive(pid):
        return state, "state_file"
    return None, "none"


def send_player_command(cmd: str, **kwargs: Any) -> bool:
    """Forward one remote-control command to an already-running player.

    Never starts a player: if nothing is listening this simply returns False and
    the caller falls back to its own stream bookkeeping.
    """
    socket_path, port_file, _state, _pid = _player_paths()
    has_unix = hasattr(socket, "AF_UNIX")
    if has_unix:
        if not socket_path.exists():
            return False
    elif not port_file.is_file():
        return False
    sock = None
    try:
        if has_unix:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.5)
            sock.connect(str(socket_path))
        else:
            sock = socket.create_connection(
                ("127.0.0.1", int(port_file.read_text().strip())), 1.5
            )
            sock.settimeout(1.5)
        sock.sendall(json.dumps({"cmd": cmd, **kwargs}).encode("utf-8"))
        response = _recv_ipc_json(sock)
        return bool(response) and response.get("status") == "ok"
    except Exception:
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    return False


def host_player_payload() -> Optional[Dict[str, Any]]:
    """The host player's state as the dashboard's status payload needs it."""
    data, source = host_player_status()
    if data is None:
        return None

    active_file = str(data.get("path", "") or "")
    raw_title = str(data.get("title", "") or "")
    duration = float(data.get("duration_sec", 0.0) or 0.0)
    if duration <= 0 and active_file:
        duration = probe_duration(active_file)
    position = max(0.0, float(data.get("position_sec", 0.0) or 0.0))

    title, artist, album = clean_track_title(raw_title, active_file, {})
    tracklist: List[str] = []
    if active_file:
        tl_path = find_tracklist_for_mix(active_file)
        tl_info = parse_tracklist_file(tl_path) if tl_path else {"metadata": {}, "tracks": []}
        title, artist, album = clean_track_title(
            raw_title, active_file, tl_info.get("metadata", {})
        )
        tracklist = tl_info.get("tracks", [])

    sample_rate = int(data.get("sample_rate", 0) or 0)
    channels = int(data.get("channels", 2) or 2)
    state = str(data.get("state", "stopped")).lower()
    playing = state == "playing" and bool(active_file)

    # A phone engaging the stream should hear what the host is hearing now. The
    # same tail guard as the stream session applies: a position on the final
    # frames is an unfinished run, not a resume point.
    resume = resume_point({"position_sec": position, "duration_sec": duration}, duration)

    return {
        "player": "MP Audio Player",
        "state": "playing" if playing else ("paused" if active_file else "stopped"),
        "title": title or "No Mix Playing",
        "raw_title": raw_title,
        "artist": artist,
        "album": album,
        "position": format_seconds(position),
        "duration": format_seconds(duration) if duration else "00:00:00",
        "position_sec": round(position, 2),
        "duration_sec": round(duration, 2),
        "resume_sec": round(resume, 2),
        "progress_pct": round(position / duration * 100.0, 1) if duration > 0 else 0.0,
        "active_file": active_file,
        "format": Path(active_file).suffix.lstrip(".").upper() if active_file else "",
        "sample_rate": f"{round(sample_rate / 1000.0, 1)} kHz" if sample_rate else "",
        "channels": "Stereo" if channels == 2 else ("Mono" if channels == 1 else f"{channels} Ch"),
        "volume": int(data.get("volume", 100) or 0),
        "muted": bool(data.get("muted", False)),
        "pitch_percent": int(data.get("pitch_percent", 0) or 0),
        "pitch_factor": float(data.get("pitch_factor", 1.0) or 1.0),
        "shuffle": bool(data.get("shuffle", False)),
        "repeat": str(data.get("repeat", "all")),
        "favorite": is_favorite_path(active_file),
        "has_artwork": bool(active_file) and artwork_for_stream(active_file) is not None,
        "levels": data.get("levels", [0.0] * 24),
        "peaks": data.get("peaks", [0.0] * 24),
        "mini_eq": str(data.get("mini_eq", "")),
        "tracklist": tracklist,
        "tracklist_total": len(tracklist),
        "queue_len": int(data.get("total_tracks", 0) or 0),
        "queue_position": int(data.get("index", 0) or 0) + 1,
        "mode": str(data.get("mode", "") or get_library_mode()),
        "host_player": True,
        "host_player_source": source,
    }


def get_playback_status(cli_theme: Optional[str] = None, follow: str = "") -> Dict[str, Any]:
    active_theme = get_theme_palette(cli_theme)

    # The running host player is the authority on what is on air. The stream
    # session below is only the phone-facing queue cursor used when nothing is
    # playing on this machine at all. `follow=stream` is the engaged web
    # player asking for its own cursor so a desk player cannot replace the
    # mix the browser is in the middle of.
    host = None if _follow_stream(follow) else host_player_payload()
    if host is not None:
        host["mode_label"] = MODE_LABELS.get(str(host.get("mode", "")), "Library")
        host["library_fallback"] = bool(_queue_source_state.get("fallback"))
        host["library_detail"] = _queue_source_state.get("detail", "")
        host["theme"] = active_theme
        return host

    active_file, state = current_stream_file(cached=True)
    queue = build_stream_queue_cached()


    duration = probe_duration(active_file) if active_file else 0.0
    if duration > 0 and abs(float(state.get("duration_sec", 0.0) or 0.0) - duration) > 0.5:
        state["duration_sec"] = duration

    pos = clamp_position(state, stream_position(state))
    pct = round(pos / duration * 100.0, 1) if duration > 0 else 0.0
    playing = bool(state.get("playing")) and bool(active_file)
    resume = resume_point(state, duration)

    title, artist, album = clean_track_title("", active_file, {})
    tracklist: List[str] = []
    if active_file:
        tl_path = find_tracklist_for_mix(active_file)
        tl_info = parse_tracklist_file(tl_path) if tl_path else {"metadata": {}, "tracks": []}
        title, artist, album = clean_track_title(
            os.path.basename(active_file), active_file, tl_info.get("metadata", {})
        )
        tracklist = tl_info.get("tracks", [])

    return {
        "player": "Web Stream",
        "state": "playing" if playing else ("paused" if active_file else "stopped"),
        "title": title or "No Mix Playing",
        "raw_title": os.path.basename(active_file),
        "artist": artist,
        "album": album,
        "position": format_seconds(pos),
        "duration": format_seconds(duration) if duration else "00:00:00",
        "position_sec": round(pos, 2),
        "duration_sec": round(duration, 2),
        # What a phone that has not started the mix yet should seek to. It is
        # 0 for an unattended run that drifted to the end, so engaging audio
        # always produces sound instead of an instantly-finished track.
        "resume_sec": round(resume, 2),
        "progress_pct": pct,
        "active_file": active_file,
        "format": Path(active_file).suffix.lstrip(".").upper() if active_file else "",
        "sample_rate": "",
        "channels": "Stereo",
        "volume": int(state.get("volume", 100)),
        "muted": bool(state.get("muted", False)),
        "pitch_percent": clamp_pitch(state.get("pitch_percent", 0)),
        "pitch_factor": round(pitch_factor_for(state.get("pitch_percent", 0)), 3),
        "shuffle": bool(state.get("shuffle", False)),
        "repeat": str(state.get("repeat", "all")),
        "favorite": is_favorite_path(active_file),
        "has_artwork": bool(active_file) and artwork_for_stream(active_file) is not None,
        "levels": [0.0] * 24,
        "peaks": [0.0] * 24,
        "mini_eq": "",
        "tracklist": tracklist,
        "tracklist_total": len(tracklist),
        "queue_len": len(queue),
        "queue_position": int(state.get("index", 0) or 0) + 1,
        "mode": get_library_mode(),
        "mode_label": MODE_LABELS.get(get_library_mode(), "Library"),
        "library_fallback": bool(_queue_source_state.get("fallback")),
        "library_detail": _queue_source_state.get("detail", ""),
        "music_offline": bool(_queue_source_state.get("music_offline")),
        # True once every candidate in the queue has been proven undecodable.
        # The dashboard uses it to show one clear "nothing in this playlist can
        # play" message instead of retrying dead URLs forever.
        "playback_exhausted": bool(state.get("playback_exhausted")),
        "unplayable_count": unplayable_count(queue),
        "theme": active_theme,
    }


MINI_PLAYLIST_SIZE = 5
# Five rows are visible on a phone; the extra entries are what the list scrolls
# through, so a long mix queue is browsable without loading the whole library.
MINI_PLAYLIST_WINDOW = 20
# A search can match far more than the idle scrollback. Forty rows still fit
# in the same five-row scroller, and probing every hit on a 51k music library
# is what would make the box feel dead.
PLAYLIST_SEARCH_WINDOW = 40
# Same character class the desk player's `/` filter folds away, so "stream-of"
# and "stream of" describe the same track.
_PLAYLIST_SEARCH_CLEAN_RE = re.compile(r"[^a-z0-9]+")
# id(queue tuple) -> cleaned path text. Rebuilt only when that tuple changes.
_playlist_search_cache: Dict[str, Any] = {"id": None, "texts": ()}


def _playlist_query_words(query: str) -> List[str]:
    """Words of a playlist search, folded the same way as the track text."""
    folded = _PLAYLIST_SEARCH_CLEAN_RE.sub(" ", (query or "").lower())
    return folded.split()[:12]


def _playlist_search_texts(queue: List[str]) -> Tuple[str, ...]:
    """Lower-cased path text for every queue entry, built once per playlist.

    The music queue is tens of thousands of paths. Cleaning it on each
    keystroke is what would stall the playlist poll, so the blobs stay until
    the queue tuple itself is replaced.
    """
    source: Optional[Tuple[str, ...]] = None
    cached = _queue_cache if isinstance(_queue_cache, dict) else None
    if cached and cached.get("mode") == get_library_mode():
        files = cached.get("files")
        if isinstance(files, tuple) and len(files) == len(queue):
            source = files
    if source is None:
        source = tuple(queue)
    if (_playlist_search_cache.get("id") == id(source)
            and len(_playlist_search_cache.get("texts") or ()) == len(source)):
        return _playlist_search_cache["texts"]
    texts = tuple(
        _PLAYLIST_SEARCH_CLEAN_RE.sub(" ", path.lower()) for path in source
    )
    _playlist_search_cache["id"] = id(source)
    _playlist_search_cache["texts"] = texts
    return texts


def filter_playlist_indices(queue: List[str], query: str) -> List[int]:
    """Queue indices whose path contains every word of `query`.

    An empty query matches nothing here: the caller keeps the unfiltered
    window. Every word has to appear, which is how the desk player's live
    playlist filter works, so a search for two words cannot combine one track's
    artist with another track's title.
    """
    words = _playlist_query_words(query)
    if not words or not queue:
        return []
    texts = _playlist_search_texts(queue)
    return [
        i for i, text in enumerate(texts)
        if all(word in text for word in words)
    ]


def _mini_playlist_item(path: str, index: int, current: bool, include_duration: bool) -> Dict[str, Any]:
    """One playlist row. Duration is optional because a search page must not
    stat every hit on a network library just to draw the list."""
    title, artist, _album = clean_track_title("", path, {})
    item: Dict[str, Any] = {
        "index": index,
        "title": title,
        "artist": artist,
        "file": path,
        "current": current,
        # Already proven undecodable on a device. The row stays visible (so
        # the listener can see what was skipped) but is greyed out.
        "unplayable": is_unplayable(path),
        "favorite": is_favorite_path(path),
    }
    if include_duration:
        duration = probe_duration(path)
        item["duration"] = format_seconds(duration)
        item["duration_sec"] = round(duration, 2)
    else:
        item["duration"] = ""
        item["duration_sec"] = 0.0
    return item


def get_mini_playlist(limit: Optional[int] = None, follow: str = "", query: str = "") -> Dict[str, Any]:
    """A short window of the stream queue: the current track plus the next ones.

    The dashboard renders `show` rows inside a scrollable box and the server
    sends up to `limit` (default MINI_PLAYLIST_WINDOW) entries, so five tracks
    are visible at once and the rest can be scrolled. `total` is the full queue
    length and is what the dashboard shows as the track count.

    `query` filters that same queue. The rows that come back are the matches,
    still addressed by their original queue index so a tap plays that file
    rather than whatever now sits in the filtered slot.
    """
    queue = build_stream_queue_cached()
    mode = get_library_mode()
    show = MINI_PLAYLIST_SIZE
    # A box full of punctuation still counts as a search: folding it away and
    # then showing the whole library looks like the filter was ignored.
    raw_query = " ".join((query or "").split())[:80]
    words = _playlist_query_words(raw_query)
    normalized = " ".join(words)
    searching = bool(raw_query)
    if not queue:
        return {
            "items": [],
            "total": 0,
            "matched": 0,
            "query": normalized or raw_query,
            "show": show,
            "current_index": -1,
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, "Library"),
            "library_fallback": bool(_queue_source_state.get("fallback")),
            "library_detail": _queue_source_state.get("detail", ""),
        "music_offline": bool(_queue_source_state.get("music_offline")),
            "playback_exhausted": False,
            "unplayable_count": 0,
            "modes": [{"id": mid, "label": label} for mid, label in MODE_LABELS.items()],
        }

    try:
        # A search uses a longer page than the idle "up next" scrollback, so a
        # short query still has room to list what it found.
        default_window = PLAYLIST_SEARCH_WINDOW if searching else MINI_PLAYLIST_WINDOW
        window = int(limit) if limit is not None else default_window
    except (TypeError, ValueError):
        window = PLAYLIST_SEARCH_WINDOW if searching else MINI_PLAYLIST_WINDOW
    # The endpoint sends the whole scrollback, while the dashboard renders
    # `show` (5) at a time. Truncation at the end of the queue is handled below.
    window = max(1, window)

    state = load_stream_session()
    idx = int(state.get("index", 0) or 0)
    saved = str(state.get("path", "") or "")
    if saved and saved in queue:
        idx = queue.index(saved)
    # When the host player is on air, its file is the one the dashboard names,
    # so the highlighted row must be that same file. Showing the stream
    # session's cursor instead is what made the playlist disagree with the
    # title above it.
    host = None if _follow_stream(follow) else host_player_payload()
    if host and host.get("active_file"):
        host_file = str(host["active_file"])
        if host_file in queue:
            idx = queue.index(host_file)
    idx = max(0, min(idx, len(queue) - 1))
    # A row that the device has already failed on must not be the highlighted
    # "now playing" row: the title above the list names a file the phone cannot
    # decode, and tapping it would only reproduce the error. Advance the window
    # to the same surviving file the status endpoint reports.
    if is_unplayable(queue[idx]):
        playable = next_playable_index(queue, idx)
        if playable is not None:
            idx = playable

    items: List[Dict[str, Any]] = []
    matched = len(queue)
    if searching:
        # The filtered rows keep the queue's own index. Playing "the third
        # search hit" has to load that file, not queue slot 2.
        matched_indices = filter_playlist_indices(queue, normalized) if normalized else []
        matched = len(matched_indices)
        for i in matched_indices[:window]:
            items.append(_mini_playlist_item(queue[i], i, i == idx, i == idx))
    else:
        # Stop at the end of the queue instead of wrapping: a repeating window
        # looks broken once it is scrollable.
        for offset in range(min(window, len(queue))):
            i = idx + offset
            if i >= len(queue):
                break
            items.append(_mini_playlist_item(queue[i], i, i == idx, True))
    return {
        "items": items,
        "total": len(queue),
        "matched": matched,
        "query": normalized or (raw_query if searching else ""),
        "show": show,
        "current_index": idx,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, "Library"),
        "library_fallback": bool(_queue_source_state.get("fallback")),
        "library_detail": _queue_source_state.get("detail", ""),
        "music_offline": bool(_queue_source_state.get("music_offline")),
        "playback_exhausted": bool(state.get("playback_exhausted")),
        "unplayable_count": unplayable_count(queue),
        "modes": [{"id": mid, "label": label} for mid, label in MODE_LABELS.items()],
    }


def play_random_track() -> Dict[str, Any]:
    """Pick a random track from the active playlist and arm it immediately.

    "Play Anything" from the MP Harmony Agent lands here. The playlist is the
    same one the dashboard streams, so the Mix / Music / Favorites choice the
    listener made is respected. An empty playlist is reported instead of
    pretending something was selected: the caller cannot play a track that
    does not exist.

    The current track is skipped while there is more than one entry, so a
    second request in a row never replays the same file.
    """
    queue = build_stream_queue()
    mode = get_library_mode()
    total = len(queue)
    base_payload: Dict[str, Any] = {
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, "Library"),
        "total": total,
        "app_root": str(get_base_dir()),
        "player_script": str(get_base_dir() / "MP_Audio_Player.py"),
        "library_fallback": bool(_queue_source_state.get("fallback")),
        "library_detail": _queue_source_state.get("detail", ""),
        "music_offline": bool(_queue_source_state.get("music_offline")),
    }
    if total < 1:
        detail = _queue_source_state.get("detail") or (
            f"No tracks available in the {MODE_LABELS.get(mode, 'library')} playlist"
        )
        return dict(base_payload, success=False, status="empty_playlist", detail=detail)

    with _stream_lock:
        state = load_stream_session()
        current = str(state.get("path", "") or "")
        candidates = (
            [i for i, path in enumerate(queue) if path != current]
            if total > 1
            else [0]
        )
        idx = random.choice(candidates or [0])
        path = queue[idx]
        state["index"] = idx
        state["path"] = path
        state["position_sec"] = 0.0
        state["duration_sec"] = probe_duration(path)
        state["playing"] = True
        state["started_at"] = time.time()
        save_stream_session(state)

    title, artist, album = clean_track_title("", path, {})
    try:
        tl_path = find_tracklist_for_mix(path)
        if tl_path:
            info = parse_tracklist_file(tl_path)
            title, artist, album = clean_track_title(
                os.path.basename(path), path, info.get("metadata", {})
            )
    except Exception:
        pass

    return dict(
        base_payload,
        success=True,
        status="playing",
        index=idx,
        position=0.0,
        track=os.path.basename(path),
        path=path,
        title=title or os.path.basename(path),
        artist=artist,
        album=album,
        detail=f"Picked {os.path.basename(path)} at random from {total} "
               f"{MODE_LABELS.get(mode, 'library')} tracks",
    )


def _control_targets_stream(params: Dict[str, List[str]]) -> bool:
    """True when the engaged web player, not the desk, should move."""
    try:
        raw = str((params.get("target") or [""])[0] or "")
    except Exception:
        raw = ""
    return raw.strip().lower() in ("stream", "web", "phone")


def _flag_param(params: Dict[str, List[str]], name: str) -> bool:
    """True when a control query flag is on (1 / true / yes / on)."""
    try:
        raw = str((params.get(name) or ["0"])[0] or "").strip().lower()
    except Exception:
        return False
    return raw in ("1", "true", "yes", "on")


def _adopt_host_into_session(params: Dict[str, List[str]]) -> bool:
    """Point the web stream at the mix the desk is playing right now.

    Engage starts the browser on the host's file. The session has to name
    that same file before the page follows its own cursor, or the next poll
    swaps in a stale path and the audio jumps or stops.
    """
    host, _source = host_player_status()
    path = str((host or {}).get("path") or "")
    if not path:
        return True
    try:
        position = float((params.get("position") or [host.get("position_sec", 0.0)])[0])
    except (TypeError, ValueError, IndexError, AttributeError):
        position = float((host or {}).get("position_sec") or 0.0)
    if position != position or position in (float("inf"), float("-inf")):
        position = 0.0
    queue = build_stream_queue()
    with _stream_lock:
        state = load_stream_session()
        if path in queue:
            state["index"] = queue.index(path)
        state["path"] = path
        state["position_sec"] = max(0.0, position)
        try:
            state["duration_sec"] = float((host or {}).get("duration_sec") or 0.0)
        except (TypeError, ValueError):
            state["duration_sec"] = 0.0
        state["playing"] = True
        state["started_at"] = time.time()
        state["playback_exhausted"] = False
        # Engage should hear the desk's pitch and level, not snap back to
        # the session defaults.
        state["pitch_percent"] = clamp_pitch((host or {}).get("pitch_percent", 0))
        try:
            state["volume"] = max(0, min(100, int((host or {}).get("volume", 100) or 0)))
        except (TypeError, ValueError):
            state["volume"] = int(state.get("volume", 100) or 100)
        state["muted"] = bool((host or {}).get("muted", False))
        save_stream_session(state)
    return True


def _queue_ends_after(queue: List[str], index: int) -> bool:
    """True when the next forward step would wrap, or the queue has one entry."""
    n = len(queue)
    if n <= 1:
        return True
    nxt = next_playable_index(queue, (int(index) + 1) % n, 1)
    if nxt is None:
        return True
    return nxt <= int(index)


def control_player(action: str, params: Optional[Dict[str, List[str]]] = None) -> bool:
    """Drive playback. The running host player owns the audio, so a live one is
    forwarded to it; otherwise the browser performs the playback of its stream.

    `target=stream` is the engaged web player. Those taps must move the stream
    the browser is hearing, not the desk player in the room.
    """
    params = params or {}
    if action in ("random", "play_random", "play_random_track"):
        return bool(play_random_track().get("success"))

    # A rescan drops the cached queue and rebuilds it from what is on disk now.
    # Without this the dashboard kept serving a stale list after a mix was
    # added or a drive was mounted.
    if action in ("refresh", "rescan", "reload"):
        invalidate_queue_cache()
        # A rescan is the operator's "look again" gesture, so it also forgives
        # files this process had written off: a repaired or re-exported mix must
        # become a candidate again. The signature check in is_unplayable() would
        # catch a changed file on its own, but clearing makes the intent
        # explicit and covers an in-place repair that kept mtime and size.
        clear_unplayable()
        # The exhausted flag is the other half of that verdict: leaving it set
        # would make the dashboard keep the "nothing in this playlist plays"
        # card up after the operator has just asked it to look again, so the
        # retry would appear to be refused. Clearing both together is what makes
        # rescan a genuine fresh start.
        with _stream_lock:
            state = load_stream_session()
            if state.get("playback_exhausted"):
                state["playback_exhausted"] = False
                save_stream_session(state)
        try:
            build_stream_queue(force=True)
        except Exception:
            return False
        return True

    if action == "adopt_host":
        return _adopt_host_into_session(params)

    # The heart edits the shared playlist, not whichever player is on air.
    # Doing it before the queue walk keeps a tap from scanning the library.
    if action in ("toggle_favorite", "favorite"):
        target = str((params.get("path") or [""])[0] or "")
        if not target:
            target, _state = current_stream_file(cached=True)
        return toggle_favorite_path(target) is not None

    # A running MP Audio Player is what an unengaged dashboard is reporting, so
    # those transport buttons must act on that player. Without this the title
    # would follow the host while every tap silently edited the unused stream
    # session. An engaged web player sends target=stream and is handled below,
    # because forwarding those taps to the desk left the browser mix untouched.
    host, _source = host_player_status()
    if host is not None and not _control_targets_stream(params):
        if action in ("play", "resume"):
            return send_player_command("play")
        if action in ("pause",):
            return send_player_command("pause")
        if action in ("play-pause", "toggle"):
            return send_player_command("play_pause")
        if action == "next":
            return send_player_command("next")
        if action in ("previous", "prev"):
            return send_player_command("prev")
        if action == "seek_forward":
            return send_player_command("seek", seconds=30.0)
        if action == "seek_backward":
            return send_player_command("seek", seconds=-30.0)
        if action == "seek":
            try:
                delta = float(params.get("seconds", [10.0])[0])
            except (TypeError, ValueError):
                delta = 10.0
            return send_player_command("seek", seconds=delta)
        if action == "seek_to":
            try:
                target = float(params.get("position", [0.0])[0])
            except (TypeError, ValueError):
                target = 0.0
            return send_player_command("seek_to", position=target)
        if action in ("shuffle", "toggle_shuffle"):
            return send_player_command("shuffle")
        if action in ("repeat", "cycle_repeat"):
            return send_player_command("repeat")
        if action == "vol_up":
            return send_player_command("vol_up", step=5)
        if action == "vol_down":
            return send_player_command("vol_down", step=5)
        if action == "set_volume":
            try:
                volume = int(params.get("volume", [100])[0])
            except (TypeError, ValueError):
                volume = 100
            return send_player_command("set_volume", volume=volume)
        if action == "mute":
            return send_player_command("mute")
        if action == "pitch_up":
            return send_player_command("pitch_up", step=1)
        if action == "pitch_down":
            return send_player_command("pitch_down", step=1)
        if action == "reset_pitch":
            return send_player_command("reset_pitch")
        if action == "select_index":
            # Companion queue indices do not match the player's own playlist
            # indices, so resolve the row to a file and ask the player to load
            # that file rather than guessing an index.
            try:
                target_index = int(params.get("index", [0])[0])
            except (TypeError, ValueError):
                target_index = 0
            host_queue = build_stream_queue()
            if 0 <= target_index < len(host_queue):
                return send_player_command("play", file=host_queue[target_index])
            return False
        if action == "track_ended":
            # The host player advances itself; a phone reporting the end of a
            # file it streamed must not move the host's queue.
            return True
        if action == "track_failed":
            # A media error on the phone says nothing about the host's own
            # queue, but it does say the shared file is undecodable. Record it
            # so the next phone skips it, then let the desk player keep going.
            #
            # Only record it when the host can actually read the file. A dropped
            # rclone/FUSE mount answers stat() from cache and fails the read with
            # "Transport endpoint is not connected"; the browser then sees a
            # truncated body and reports MEDIA_ERR_SRC_NOT_SUPPORTED, which is
            # indistinguishable from real corruption. Writing those off would
            # permanently blacklist good tracks because a share hiccuped, so the
            # reachability of the file itself is the deciding vote.
            failed_path = str((params.get("path") or [""])[0] or "")
            if _path_readable_now(failed_path):
                mark_unplayable(failed_path)
            return True
        if action == "report_position":
            # The host's own clock is authoritative, so a phone clock report is
            # irrelevant and must never be written over it.
            return True

    # Transport that only reads or writes the session must not touch the queue.
    # `build_stream_queue()` can walk the whole music library, and on a sleeping
    # CIFS share a single `stat()` costs seconds: a pause tap was measured
    # blocking for ~119 s because it built the queue first. Pause and toggle
    # need the clock and the saved path, nothing else.
    if action in ("pause", "play-pause", "toggle"):
        with _stream_lock:
            state = load_stream_session()
            pos = clamp_position(state, stream_position(state))
            if action == "pause" or state.get("playing"):
                state["playing"] = False
                state["position_sec"] = pos
                state["started_at"] = 0.0
            else:
                if not str(state.get("path", "") or ""):
                    return False
                state["playing"] = True
                state["position_sec"] = pos
                state["started_at"] = time.time()
            save_stream_session(state)
        return True

    queue = build_stream_queue()
    if not queue:
        return False

    with _stream_lock:
        state = load_stream_session()
        # Resolve the current file from the already-built queue. Calling
        # current_stream_file() here would re-enter the queue lock and could
        # hold the stream lock across a multi-second library scan, which made
        # every control tap wait behind a busy scan.
        saved = str(state.get("path", "") or "")
        if saved and saved in queue:
            path = saved
        else:
            idx = int(state.get("index", 0) or 0)
            idx = max(0, min(idx, len(queue) - 1))
            path = queue[idx]
        if path and state.get("path") != path:
            state["path"] = path
        if state.get("path"):
            try:
                state["index"] = queue.index(state["path"])
            except ValueError:
                state["index"] = 0
                state["path"] = queue[0]

        pos = clamp_position(state, stream_position(state))
        path_before = str(state.get("path") or "")
        stored_duration = float(state.get("duration_sec", 0.0) or 0.0)
        # A seek has to know the file it is inside. The first tap used to run
        # past the end when duration_sec was still 0, and a later tap clamped
        # to the previous mix when that length had been copied onto this one.
        seek_action = action in ("seek", "seek_to", "seek_forward", "seek_backward")
        if path_before and (seek_action or stored_duration <= 0):
            probed_duration = probe_duration(path_before)
        else:
            probed_duration = 0.0
        if probed_duration > 0:
            duration = probed_duration
            if stored_duration <= 0 or abs(stored_duration - probed_duration) > 0.5:
                state["duration_sec"] = float(probed_duration)
        else:
            duration = stored_duration

        def seek_to(target: float) -> None:
            state["position_sec"] = clamp_position(state, target)
            state["started_at"] = time.time() if state.get("playing") else 0.0

        def switch(step: int) -> None:
            n = len(queue)
            if n <= 0:
                return
            current = int(state.get("index", 0) or 0)
            # Shuffle must actually shuffle. This used to be a plain +1 walk, so
            # the flag only changed the badge: enabling shuffle still played the
            # queue in order. Pick a random entry instead, never repeating the
            # one already playing.
            # Previous stays a real step backward. Shuffling it picked a
            # random mix, so the button looked dead: the listener asked for
            # the track before this one and got something else, or the same
            # one again when the random draw lost the race with the poll.
            if state.get("shuffle") and n > 1 and step > 0:
                saved_path = str(state.get("path", "") or "")
                try:
                    avoid = queue.index(saved_path) if saved_path in queue else current
                except ValueError:
                    avoid = current
                # A file the device already failed on is not a candidate, so a
                # shuffle cannot keep landing on the one dead mix in the queue.
                choices = [i for i in range(n) if i != avoid and not is_unplayable(queue[i])]
                if not choices:
                    choices = [i for i in range(n) if i != avoid]
                state["index"] = random.choice(choices) if choices else 0
            else:
                # Walk in `step` order and honour the unplayable ledger: this is
                # what makes "next" (and an automatic advance) skip a damaged
                # mix instead of handing the same dead URL back to the phone.
                target = next_playable_index(queue, (current + step) % n, 1 if step > 0 else -1)
                if target is None:
                    # Nothing in the queue is known-good; fall back to the plain
                    # walk so a dead library still reports a concrete file and
                    # the dashboard can name the failure.
                    target = (current + step) % n
                state["index"] = target
            state["path"] = queue[state["index"]]
            state["position_sec"] = 0.0
            state["started_at"] = time.time() if state.get("playing") else 0.0
            state["duration_sec"] = probe_duration(state["path"])

        if action in ("play", "resume"):
            state["playing"] = True
            state["started_at"] = time.time()
        elif action == "next":
            switch(1)
        elif action in ("previous", "prev"):
            switch(-1)
        elif action == "select_index":
            try:
                target = int(params.get("index", [0])[0])
            except (TypeError, ValueError):
                target = 0
            target = max(0, min(target, len(queue) - 1))
            state["index"] = target
            state["path"] = queue[target]
            state["position_sec"] = 0.0
            # A playlist tap, including one on a search hit, is a request to
            # hear that track. Without this a paused session only moved the
            # cursor and the phone stayed silent on the old file.
            if _flag_param(params, "play"):
                state["playing"] = True
                state["started_at"] = time.time()
                state["playback_exhausted"] = False
            else:
                state["started_at"] = time.time() if state.get("playing") else 0.0
            state["duration_sec"] = probe_duration(state["path"])
        elif action == "seek_forward":
            seek_to(pos + 30.0)
        elif action == "seek_backward":
            seek_to(pos - 30.0)
        elif action == "seek":
            seek_to(pos + float(params.get("seconds", [10.0])[0]))
        elif action == "seek_to":
            seek_to(float(params.get("position", [0.0])[0]))
        elif action == "report_position":
            # The phone owns playback, so its clock is the true one. Without
            # this the host's unattended clock free-runs from boot and reports
            # the end of the mix after a few idle hours, which then becomes the
            # resume point for the next device. Reports only move the clock
            # forward within the same file; they never change which file plays.
            try:
                reported = float(params.get("position", [pos])[0])
            except (TypeError, ValueError):
                reported = pos
            if reported != reported or reported in (float("inf"), float("-inf")):
                return False
            state["position_sec"] = clamp_position(state, reported)
            state["started_at"] = time.time() if state.get("playing") else 0.0
        elif action in ("shuffle", "toggle_shuffle"):
            state["shuffle"] = not bool(state.get("shuffle"))
        elif action in ("repeat", "cycle_repeat"):
            order = ["off", "all", "one"]
            cur = str(state.get("repeat", "all"))
            state["repeat"] = order[(order.index(cur) + 1) % len(order)] if cur in order else "all"
        elif action == "vol_up":
            state["volume"] = min(100, int(state.get("volume", 100)) + 5)
            state["muted"] = False
        elif action == "vol_down":
            state["volume"] = max(0, int(state.get("volume", 100)) - 5)
            state["muted"] = False
        elif action == "set_volume":
            state["volume"] = max(0, min(100, int(params.get("volume", [100])[0])))
            state["muted"] = False
        elif action == "mute":
            # Toggle. Zeroing `volume` left nothing to restore, so the next
            # tap could not unmute.
            state["muted"] = not bool(state.get("muted"))
        elif action == "pitch_up":
            try:
                step = int((params.get("step") or ["1"])[0])
            except (TypeError, ValueError, IndexError):
                step = 1
            _set_stream_pitch(state, int(state.get("pitch_percent", 0) or 0) + step)
        elif action == "pitch_down":
            try:
                step = int((params.get("step") or ["1"])[0])
            except (TypeError, ValueError, IndexError):
                step = 1
            _set_stream_pitch(state, int(state.get("pitch_percent", 0) or 0) - step)
        elif action == "reset_pitch":
            _set_stream_pitch(state, 0)
        elif action == "set_pitch":
            try:
                target = int((params.get("percent") or ["0"])[0])
            except (TypeError, ValueError, IndexError):
                target = 0
            _set_stream_pitch(state, target)
        elif action == "track_ended":
            # The phone finished the file. Repeat One replays it, Repeat Off
            # stops on the last mix, and Repeat All (or shuffle) advances.
            # This used to call switch() unconditionally, so the button changed
            # the badge and the queue ignored it.
            repeat_mode = str(state.get("repeat", "all") or "all")
            if repeat_mode not in ("off", "all", "one"):
                repeat_mode = "all"
            at_end = (not state.get("shuffle")) and _queue_ends_after(
                queue, int(state.get("index", 0) or 0))
            if repeat_mode == "one":
                state["position_sec"] = 0.0
                state["playing"] = True
                state["started_at"] = time.time()
                state["playback_exhausted"] = False
            elif repeat_mode == "off" and at_end:
                state["playing"] = False
                state["started_at"] = 0.0
                end_at = float(state.get("duration_sec", 0.0) or 0.0) or pos
                state["position_sec"] = clamp_position(state, end_at)
            else:
                state["playing"] = True
                switch(1)
                state["started_at"] = time.time()
                state["playback_exhausted"] = False
        elif action == "track_failed":
            # The device could not decode the file it was handed (a media error,
            # not a normal end). Two things have to happen:
            #   1. remember the exact copy that failed, so no poll, phone or
            #      shuffle can serve it again;
            #   2. advance to the next candidate, so the listener keeps hearing
            #      music instead of staring at an error card.
            # `path` may name the file explicitly, because the phone's poll can
            # be one step behind the file that actually errored. When it is
            # absent the current cursor is what failed.
            failed_path = str((params.get("path") or [""])[0] or state.get("path", ""))
            mark_unplayable(failed_path)
            # Resolve the failing entry to its position in the current queue; a
            # path that is no longer in the queue (deleted, renamed) simply lets
            # the walk start from the cursor.
            try:
                failed_index = queue.index(failed_path)
            except ValueError:
                failed_index = int(state.get("index", 0) or 0)
            state["index"] = failed_index
            state["path"] = queue[failed_index] if queue else ""
            remaining = next_playable_index(queue, (failed_index + 1) % len(queue)) \
                if queue else None
            if remaining is None:
                # Every single candidate has now failed. Stop cleanly and let the
                # dashboard say so; do not spin on a queue that cannot play.
                state["playing"] = False
                state["position_sec"] = 0.0
                state["started_at"] = 0.0
                state["playback_exhausted"] = True
            else:
                state["index"] = remaining
                state["path"] = queue[remaining]
                state["position_sec"] = 0.0
                state["duration_sec"] = probe_duration(state["path"])
                state["started_at"] = time.time() if state.get("playing") else 0.0
                state["playback_exhausted"] = False
        else:
            return False

        # Only write this length back when we are still on the file it
        # describes. Next, previous and track-end already stored the new
        # file's length inside switch(); putting `duration` back stamped the
        # previous mix onto the new one, and the next seek clamped to it.
        if duration > 0 and str(state.get("path") or "") == path_before:
            state["duration_sec"] = float(duration)
        save_stream_session(state)
    return True


def render_launch_pad_html() -> str:
    """Linux-only desktop launch pad. Colours come from the page's CSS vars."""
    if suite_control is None:
        return ""
    try:
        if not suite_control.is_supported():
            return ""
        apps = suite_control.app_definitions()
        tty_mode = bool(suite_control.tty_mode_active())
    except Exception:
        return ""

    buttons = []
    disabled_attr = " disabled" if tty_mode else ""
    for app in apps:
        role = html.escape(str(app["id"]), quote=True)
        label = html.escape(str(app["label"]))
        icon = html.escape(str(app["icon"]))
        buttons.append(
            f'<button type="button" class="launch-btn" data-app="{role}" '
            f'data-label="{label}" onclick="launchApp(\'{role}\')" '
            f'aria-pressed="false" title="Launch {label}"{disabled_attr}>'
            f'<span class="launch-icon" aria-hidden="true">{icon}</span>'
            f'<span class="launch-label">{label}</span>'
            f'<span class="launch-state">Off</span>'
            f'</button>'
        )
    visualizer_label = html.escape(str(suite_control.APP_LABELS.get(
        suite_control.ROLE_VISUALIZER, "MP Visualizer")))
    # The MP Visualizer control opens the same app as the visualizer shortcut,
    # so a setup that did not install it does not get the button either.
    try:
        visualizer_installed = suite_control.ROLE_VISUALIZER in suite_control.installed_components()
    except Exception:
        visualizer_installed = True
    disabled = disabled_attr
    pad_class = "launchpad tty-disabled" if tty_mode else "launchpad"
    tty_banner = (
        '<div class="launchpad-tty" id="launchPadTty" role="status">'
        '<strong>⌨ MP Mix Manager TTY Audio Mode</strong>'
        '<span>These desktop launchers are disabled while the manager runs in '
        'the text console. Return to the desktop to use them.</span>'
        '</div>'
        if tty_mode else
        '<div class="launchpad-tty" id="launchPadTty" role="status" hidden></div>'
    )
    # MP Visualizer: one window, owned by this dashboard. Press to open, press
    # again for fullscreen, press a third time to close it. A setup that did
    # not install the visualizer does not get the button.
    if visualizer_installed:
        visualizer_button = (
            '<button type="button" class="launch-btn launch-visualizer" id="launchVisualizer" '
            'data-label="' + visualizer_label + '" onclick="launchVisualizer()" '
            'aria-pressed="false" aria-live="polite" title="Open MP Visualizer on the computer"'
            + disabled + '>'
            '<span class="launch-icon" aria-hidden="true">🌌</span>'
            '<span class="launch-label">MP Visualizer</span>'
            '<span class="launch-state" id="launchVisualizerState">Off</span>'
            '</button>'
        )
    else:
        visualizer_button = ""
    return (
        f'<div class="{pad_class}" id="launchPad">'
        '<div class="launchpad-head">'
        '<span>🖥 Desktop Suite</span>'
        '<span id="launchPadTray" class="launch-state">Tray off</span>'
        '</div>'
        + tty_banner +
        '<div class="launchpad-grid" id="launchPadGrid">'
        + "".join(buttons)
        + '<button type="button" class="launch-btn launch-close" id="launchCloseAll" '
          'onclick="closeAllApps()" title="Close all installed apps, keep the tray icon"'
        + disabled + '>'
          '<span class="launch-icon" aria-hidden="true">⏻</span>'
          '<span class="launch-label">Close All</span>'
          '</button>'
        '</div>'
        + visualizer_button
        + '<div class="launchpad-note" id="launchPadNote">'
        'Tap a button to open that app on the computer.'
        '</div>'
        '</div>'
    )

# DYNAMIC HTML TEMPLATE (SEAMLESS AUDIO STREAMING & SYNCED SEEKING)
# ------------------------------------------------------------------------------
def render_html_page(theme: Dict[str, Any]) -> str:
    t = theme
    launch_pad_html = render_launch_pad_html()

    # Dynamic Liquid Glass [Rainbow Colours]. The block is always emitted.
    # Every rule is scoped to a body class, so another theme stays put until
    # the menu selects this one. Emitting it only for the theme that was active
    # at render time meant a menu switch added the class and then nothing moved
    # until a full reload.
    #
    # The colour itself is the custom property --rainbow-hue, written from a
    # short timer (see syncRainbowDrive). iOS Safari does not animate
    # `filter: hue-rotate` on a ::before, and a ::before with z-index -1 paints
    # underneath <body>'s opaque background, so that layer never showed.
    # Filtering <body> itself re-rasterises every glyph and is what used to
    # freeze the dashboard. Accents, borders, buttons and the wash all read the
    # one variable, so the chrome changes colour without a page-sized filter.
    rainbow_anim_css = """
        @property --rainbow-hue {
            syntax: "<number>";
            inherits: true;
            initial-value: 272;
        }
        @keyframes dynamicRainbowHue {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        @keyframes dynamicRainbowBreath {
            0%, 100% { opacity: 0.72; }
            50% { opacity: 1; }
        }
        /* isolation keeps the wash (z-index -1) above the body's own
           background and below the dashboard. Without it the wash disappears
           behind background-color. */
        body.dynamic-rainbow-glass-active {
            isolation: isolate;
            --accent-primary: hsl(var(--rainbow-hue) 85% 64%);
            --accent-secondary: hsl(calc(var(--rainbow-hue) + 150) 92% 60%);
            --accent-tertiary: hsl(calc(var(--rainbow-hue) + 310) 88% 62%);
            --accent-crimson: hsl(calc(var(--rainbow-hue) + 28) 95% 56%);
            --card-border: rgba(255, 255, 255, 0.18);
            /* Low-alpha neutral, not a violet at 0.72: #rainbowWash sits
               behind the dashboard, so a translucent card is tinted by the
               same moving light as the page. The old opaque purple hid the
               wash and left every panel one fixed colour while only the top
               background appeared to animate. */
            --card-bg: rgba(6, 5, 14, 0.32);
            --card-shadow: 0 14px 40px hsl(var(--rainbow-hue) 80% 42% / 0.38);
            --badge-bg: hsl(var(--rainbow-hue) 80% 60% / 0.22);
            --badge-color: hsl(calc(var(--rainbow-hue) + 150) 92% 62%);
            --text-dim: hsl(calc(var(--rainbow-hue) + 40) 45% 78%);
            --eq-bar: hsl(calc(var(--rainbow-hue) + 150) 92% 60%);
            --eq-peak: hsl(calc(var(--rainbow-hue) + 310) 88% 62%);
            --btn-gradient: linear-gradient(135deg,
                hsl(var(--rainbow-hue) 85% 58%),
                hsl(calc(var(--rainbow-hue) + 55) 90% 56%));
            --btn-shadow: 0 4px 20px hsl(var(--rainbow-hue) 85% 52% / 0.55);
            --header-gradient: linear-gradient(100deg,
                hsl(var(--rainbow-hue) 88% 68%),
                hsl(calc(var(--rainbow-hue) + 48) 92% 64%),
                hsl(calc(var(--rainbow-hue) + 110) 90% 62%),
                hsl(calc(var(--rainbow-hue) + 170) 88% 64%));
            --progress-fill: linear-gradient(90deg,
                hsl(var(--rainbow-hue) 85% 62%),
                hsl(calc(var(--rainbow-hue) + 48) 90% 60%),
                hsl(calc(var(--rainbow-hue) + 110) 92% 62%),
                hsl(calc(var(--rainbow-hue) + 170) 88% 58%));
        }
        #rainbowWash {
            display: none;
        }
        body.dynamic-rainbow-glass-active #rainbowWash {
            display: block;
            position: fixed;
            /* Exactly the viewport. This used to be `inset: -50%` so a
               continuously rotating layer still covered the corners. On an
               iPhone 16 Pro Max (430x932 CSS at DPR 3) that is a 14.4 MP
               surface -- about 58 MB of RGBA -- promoted to its own compositor
               layer and re-rasterised forever, which is what got the WebContent
               process killed and made iOS Safari silently reload the page. The
               wash no longer rotates (the hue is stepped in JS), so it needs no
               oversized box at all. */
            inset: 0;
            z-index: -1;
            pointer-events: none;
            background:
                radial-gradient(circle at 22% 18%, hsl(var(--rainbow-hue) 90% 62% / 0.62), transparent 52%),
                radial-gradient(circle at 78% 16%, hsl(calc(var(--rainbow-hue) + 140) 95% 58% / 0.48), transparent 50%),
                radial-gradient(circle at 48% 92%, hsl(calc(var(--rainbow-hue) + 280) 90% 58% / 0.55), transparent 55%);
            /* Opacity and a compositor-only hue rotation. Do NOT rotate or
               transform this layer: a transform animation forces the whole wash
               to be re-composited every frame, and any `backdrop-filter` above
               it then re-blurs a backdrop that never stops changing. That pair
               is what reloaded the iPhone.

               The hue-rotate animation is what replaced the JavaScript timer
               that used to step --rainbow-hue four times a second. That timer
               was the other half of the reload: every write restyled the whole
               document (27 custom properties derive from the variable), and
               WebKit never released the result. Measured in WebKitGTK on a
               430x932 viewport the old tick grew the WebContent process by
               0.81 MB/s - about 49 MB a minute, 2.9 GB an hour - while iddling
               at 4.8% of a core. The CSS animation below grows at 0.01 MB/s
               and costs 0.4%, and the colour still turns once every 24 s. */
            animation: dynamicRainbowBreath 6s ease-in-out infinite,
                       dynamicRainbowSpin 24s linear infinite;
        }
        @keyframes dynamicRainbowSpin {
            from { filter: hue-rotate(0deg); }
            to { filter: hue-rotate(360deg); }
        }
        /* The cards stay translucent (--card-bg carries 0.72 alpha under this
           theme) but are not blurred. `backdrop-filter` over the animating wash
           made the compositor read back and re-blur the full card every frame. */
        body.dynamic-rainbow-glass-active .card,
        body.dynamic-rainbow-glass-active .overlay-card {
            backdrop-filter: none;
            -webkit-backdrop-filter: none;
        }
        body.dynamic-rainbow-glass-active .overlay-card {
            box-shadow: 0 16px 50px rgba(0, 0, 0, 0.9),
                        0 0 30px hsl(var(--rainbow-hue) 85% 60% / 0.45);
        }
        /* Hidden tab: stop the drift. The wash animations pause with it. */
        body.dynamic-rainbow-glass-frozen #rainbowWash {
            animation-play-state: paused;
        }
        /* Reduce Motion drops the moving blobs only. The slow hue change is
           the theme, and it is a crossfade rather than motion. */
        @media (prefers-reduced-motion: reduce) {
            body.dynamic-rainbow-glass-active #rainbowWash {
                animation: none;
            }
        }
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-title" content="MP Audio">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="theme-color" content="{html.escape(str(t.get('bg') or '#000000'), quote=True)}">
    <link rel="manifest" href="/manifest.webmanifest">
    <link rel="apple-touch-icon" href="/icon.png">
    <link rel="icon" href="/icon.png" type="image/png">
    <title>MP Audio Player (Web) — DJ Booth Remote</title>
    <style>
        :root {{
            --bg: {t['bg']};
            --card-bg: {t['card_bg']};
            --card-border: {t['card_border']};
            --card-shadow: {t['card_shadow']};
            --text-main: {t['text_main']};
            --text-dim: {t['text_dim']};
            --accent-primary: {t['accent_primary']};
            --accent-secondary: {t['accent_secondary']};
            --accent-tertiary: {t['accent_tertiary']};
            --accent-crimson: {t['accent_crimson']};
            --header-gradient: {t['header_gradient']};
            --btn-gradient: {t['btn_gradient']};
            --btn-shadow: {t['btn_shadow']};
            --progress-fill: {t['progress_fill']};
            --badge-bg: {t['badge_bg']};
            --badge-color: {t['badge_color']};
            --eq-bar: {t['eq_bar']};
            --eq-peak: {t['eq_peak']};
            --rainbow-hue: 272;
        }}
        {rainbow_anim_css}
        * {{
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            margin: 0;
            padding: 16px 12px;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            /* iOS Safari reports the *large* viewport for 100vh, so the last
               control could sit behind the toolbar. dvh tracks the visible
               height and keeps the page exactly one screen tall. */
            min-height: 100dvh;
            overflow-x: hidden;
        }}
        /* While the engage gate is up the page behind it must not scroll, so
           the button cannot be pushed off screen by a stray swipe. */
        body.overlay-active {{
            overflow: hidden;
            height: 100vh;
            height: 100dvh;
            position: fixed;
            inset: 0;
            width: 100%;
        }}
        .header {{
            text-align: center;
            margin-bottom: 16px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 1.75rem;
            background: var(--header-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 900;
            letter-spacing: 1.5px;
            text-transform: uppercase;
        }}
        .app-version {{
            font-size: 0.75rem;
            color: var(--accent-secondary);
            font-weight: 700;
            vertical-align: middle;
            background: rgba(255, 255, 255, 0.08);
            padding: 2px 8px;
            border-radius: 8px;
            border: 1px solid var(--card-border);
            margin-left: 6px;
            display: inline-block;
        }}
        .header p {{
            margin: 6px 0 8px 0;
            font-size: 0.85rem;
            color: var(--text-dim);
            font-weight: 500;
        }}
        .theme-pill {{
            display: inline-block;
            font-size: 0.72rem;
            padding: 3px 12px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-dim);
            border: 1px solid var(--card-border);
            letter-spacing: 0.5px;
            font-weight: 600;
        }}
        .theme-bar {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 10px;
        }}
        .theme-btn {{
            appearance: none;
            -webkit-appearance: none;
            display: inline-flex;
            align-items: center;
            gap: 7px;
            padding: 9px 18px;
            border-radius: 12px;
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0.4px;
            color: var(--text-main);
            background: var(--btn-gradient);
            border: 1px solid var(--card-border);
            box-shadow: var(--btn-shadow);
            cursor: pointer;
            transition: transform 0.12s ease, filter 0.15s ease;
        }}
        .theme-btn:active {{
            transform: scale(0.96);
        }}
        .theme-btn:hover {{
            filter: brightness(1.12);
        }}
        .theme-btn.open {{
            border-color: var(--accent-secondary);
        }}
        .theme-menu {{
            display: none;
            margin-top: 10px;
            width: 100%;
            max-width: 420px;
            max-height: 270px;
            overflow-y: auto;
            padding: 6px;
            border-radius: 14px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            box-shadow: var(--card-shadow);
            -webkit-overflow-scrolling: touch;
        }}
        .theme-menu.show {{
            display: block;
        }}
        .theme-option {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            width: 100%;
            text-align: left;
            padding: 9px 12px;
            margin: 2px 0;
            border: 1px solid transparent;
            border-radius: 10px;
            background: transparent;
            color: var(--text-main);
            font-size: 0.8rem;
            font-weight: 600;
            font-family: inherit;
            cursor: pointer;
        }}
        .theme-option:hover {{
            background: rgba(255, 255, 255, 0.07);
        }}
        .theme-option.active {{
            background: var(--badge-bg);
            color: var(--badge-color);
            border-color: var(--badge-color);
            font-weight: 800;
        }}
        .theme-option .theme-check {{
            font-size: 0.72rem;
            opacity: 0.9;
        }}
        .mini-playlist {{
            margin-top: 14px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        /* Five rows are visible at once; the rest of the window scrolls. */
        #miniPlItems {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            /* 5 rows x 48px + 4 gaps x 6px = 264px */
            max-height: 264px;
            overflow-y: auto;
            overscroll-behavior: contain;
            -webkit-overflow-scrolling: touch;
            touch-action: pan-y;
            padding-right: 4px;
        }}
        #miniPlItems::-webkit-scrollbar {{
            width: 5px;
        }}
        #miniPlItems::-webkit-scrollbar-thumb {{
            background: var(--accent-secondary);
            border-radius: 3px;
        }}
        #miniPlItems::-webkit-scrollbar-track {{
            background: transparent;
        }}
        .mini-pl-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 0.7px;
            color: var(--text-dim);
            text-transform: uppercase;
        }}
        .mini-pl-count {{
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.5px;
            padding: 3px 9px;
            border-radius: 10px;
            background: var(--badge-bg);
            color: var(--badge-color);
            border: 1px solid var(--badge-color);
            white-space: nowrap;
            text-transform: none;
        }}
        .mode-switch {{
            display: flex;
            gap: 4px;
            padding: 3px;
            border-radius: 11px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
        }}
        .mode-switch button {{
            appearance: none;
            border: none;
            background: transparent;
            color: var(--text-dim);
            font-family: inherit;
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.2px;
            padding: 7px 6px;
            border-radius: 8px;
            cursor: pointer;
            flex: 1 1 0;
            min-width: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            transition: background 0.15s ease, color 0.15s ease;
        }}
        .mode-switch button.active {{
            background: var(--badge-bg);
            color: var(--badge-color);
            box-shadow: 0 0 0 1px var(--badge-color) inset;
        }}
        .mode-switch button:disabled {{
            cursor: progress;
            opacity: 0.55;
        }}
        .mode-switch button.pending:disabled {{
            opacity: 1;
            animation: modePulse 1s ease-in-out infinite;
        }}
        @keyframes modePulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.45; }}
        }}
        @media (prefers-reduced-motion: reduce) {{
            .mode-switch button.pending:disabled {{ animation: none; }}
        }}
        .mini-pl-search {{
            display: block;
        }}
        .mini-pl-search input {{
            width: 100%;
            box-sizing: border-box;
            appearance: none;
            -webkit-appearance: none;
            border: 1px solid var(--card-border);
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-main);
            font-family: inherit;
            /* 16px keeps iOS from zooming the page when the box is focused. */
            font-size: 16px;
            font-weight: 600;
            padding: 8px 12px;
            border-radius: 10px;
        }}
        .mini-pl-search input::placeholder {{
            color: var(--text-dim);
            font-weight: 600;
        }}
        .mini-pl-search input:focus {{
            outline: none;
            border-color: var(--accent-secondary);
            box-shadow: 0 0 0 1px var(--accent-secondary) inset;
        }}
        .mini-pl-empty {{
            font-size: 0.75rem;
            color: var(--text-dim);
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px dashed var(--card-border);
            text-align: center;
        }}
        .mini-pl-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 12px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--card-border);
            cursor: pointer;
            transition: background 0.15s ease, border-color 0.15s ease;
            /* Uniform 48px rows put exactly five in the 264px window. */
            flex: 0 0 48px;
            height: 48px;
            box-sizing: border-box;
        }}
        .mini-pl-item:hover {{
            background: rgba(255, 255, 255, 0.08);
            border-color: var(--accent-secondary);
        }}
        .mini-pl-item.current {{
            background: var(--badge-bg);
            border-color: var(--badge-color);
            box-shadow: 0 0 0 1px var(--badge-color) inset;
        }}
        /* A row the device could not decode. It stays in the list so the
           listener can see what was skipped, but it reads as unavailable
           rather than as a normal "tap to play" row. */
        .mini-pl-item.unplayable {{
            opacity: 0.55;
            border-style: dashed;
        }}
        .mini-pl-item.unplayable .mini-pl-num {{
            color: #ffaa00;
        }}
        .mini-pl-num {{
            font-size: 0.68rem;
            font-weight: 800;
            color: var(--text-dim);
            min-width: 16px;
        }}
        .mini-pl-item.current .mini-pl-num {{
            color: var(--badge-color);
        }}
        .mini-pl-body {{
            flex: 1;
            min-width: 0;
        }}
        .mini-pl-title {{
            font-size: 0.78rem;
            font-weight: 700;
            color: var(--text-main);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .mini-pl-dur {{
            font-size: 0.68rem;
            color: var(--text-dim);
            margin-top: 2px;
        }}
        .mini-pl-fav {{
            flex: 0 0 36px;
            width: 36px;
            height: 36px;
            border: none;
            border-radius: 10px;
            background: transparent;
            color: var(--text-dim);
            font-size: 1.05rem;
            line-height: 1;
            padding: 0;
        }}
        .mini-pl-fav.on {{
            color: var(--accent-crimson);
        }}
        .autoplay-notice {{
            margin-top: 8px;
            padding: 6px 12px;
            border-radius: 10px;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.3px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-dim);
        }}
        .autoplay-notice.ok {{
            background: var(--badge-bg);
            color: var(--badge-color);
            border-color: var(--badge-color);
        }}
        /* Tray start/stop of the companion: the phone shows why it is
           reconnecting, then reloads itself to resume the stream. */
        .restart-notice {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 10000;
            display: none;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 10px 14px;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.2px;
            text-align: center;
            color: #fff;
            background: var(--btn-gradient);
            border-bottom: 2px solid var(--badge-color);
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.7);
        }}
        .restart-notice.show {{
            display: flex;
        }}
        .restart-notice .restart-spinner {{
            display: inline-block;
            width: 12px;
            height: 12px;
            flex: 0 0 auto;
            border-radius: 50%;
            border: 2px solid rgba(255, 255, 255, 0.35);
            border-top-color: #fff;
            animation: restartSpin 0.8s linear infinite;
        }}
        @keyframes restartSpin {{
            to {{ transform: rotate(360deg); }}
        }}
        @media (prefers-reduced-motion: reduce) {{
            .restart-notice .restart-spinner {{ animation: none; }}
        }}
        .autoplay-notice.warn {{
            background: rgba(255, 170, 0, 0.12);
            color: #ffaa00;
            border-color: #ffaa00;
        }}
        .card {{
            background-color: var(--card-bg);
            border-radius: 18px;
            padding: 22px;
            width: 100%;
            max-width: 520px;
            box-shadow: var(--card-shadow);
            border: 1px solid var(--card-border);
            margin-bottom: 16px;
        }}
        .badges-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            width: 100%;
            margin-bottom: 12px;
        }}
        .badge {{
            background: var(--badge-bg);
            color: var(--badge-color);
            padding: 4px 10px;
            border-radius: 14px;
            font-size: 0.72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 1px;
            border: 1px solid var(--badge-color);
        }}
        .badge-source {{
            background: rgba(255, 255, 255, 0.06);
            color: var(--text-dim);
            border-color: rgba(255, 255, 255, 0.12);
        }}
        .track-info {{
            text-align: center;
            width: 100%;
            margin-bottom: 14px;
        }}
        .track-cover {{
            width: 96px;
            height: 96px;
            object-fit: cover;
            border-radius: 16px;
            margin: 0 auto 10px;
            display: block;
            border: 1px solid var(--card-border);
            background: rgba(0, 0, 0, 0.25);
        }}
        .track-cover[hidden] {{
            display: none;
        }}
        .title-row {{
            display: flex;
            align-items: flex-start;
            justify-content: center;
            gap: 8px;
        }}
        .title-row .track-title {{
            flex: 1;
            min-width: 0;
        }}
        .btn-fav {{
            flex: 0 0 44px;
            width: 44px;
            height: 44px;
            border-radius: 12px;
            border: 1px solid var(--card-border);
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-dim);
            font-size: 1.25rem;
            line-height: 1;
            padding: 0;
        }}
        .btn-fav.on {{
            color: var(--accent-crimson);
            border-color: var(--accent-crimson);
        }}
        .track-title {{
            font-size: 1.25rem;
            font-weight: 800;
            margin: 0 0 6px 0;
            line-height: 1.35;
            color: var(--text-main);
            word-break: break-word;
        }}
        .track-artist {{
            font-size: 0.95rem;
            color: var(--accent-primary);
            font-weight: 600;
            margin: 0 0 6px 0;
        }}
        .track-specs {{
            font-size: 0.75rem;
            color: var(--text-dim);
            letter-spacing: 0.5px;
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 4px;
        }}
        .spec-chip {{
            background: rgba(255, 255, 255, 0.04);
            padding: 2px 8px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }}
        .mobile-audio-banner {{
            display: none;
            background: linear-gradient(135deg, rgba(0, 245, 255, 0.12), rgba(176, 88, 255, 0.12));
            border: 1px solid var(--accent-primary);
            border-radius: 12px;
            padding: 10px 14px;
            margin-bottom: 16px;
            text-align: center;
        }}
        .mobile-audio-banner h4 {{
            margin: 0;
            font-size: 0.82rem;
            color: var(--accent-secondary);
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }}
        .mobile-audio-banner p {{
            margin: 3px 0 0 0;
            font-size: 0.72rem;
            color: var(--text-dim);
        }}
        /* iOS Safari will not start media that is display:none, or whose
           ancestor is. The mix and the spoken reply stay in the render tree
           (1px, transparent) so Engage and replies can actually play. */
        .phone-audio {{
            position: absolute;
            width: 1px;
            height: 1px;
            margin: 0;
            padding: 0;
            border: 0;
            opacity: 0;
            pointer-events: none;
            overflow: hidden;
        }}
        /* Stream Activation Modal */
        #streamOverlay {{
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            /* dvh keeps the gate inside the visible area on iOS Safari, where
               100% of the fixed viewport can extend under the browser chrome. */
            height: 100dvh;
            background: rgba(5, 4, 15, 0.92);
            z-index: 9999;
            flex-direction: column;
            align-items: center;
            /* The engage button is the only way forward: the card is pinned to
               the middle of the *visible* screen and the gate itself never
               scrolls. Short screens get a smaller card through the media
               queries below rather than a scrollbar that pushes the button
               off the page. */
            justify-content: center;
            padding: max(12px, env(safe-area-inset-top)) 12px
                     max(12px, env(safe-area-inset-bottom));
            text-align: center;
            overflow: hidden;
            overscroll-behavior: none;
            /* `manipulation` still kills the double-tap zoom, but `none` makes
               iOS Safari drop the click on the Engage button entirely. */
            touch-action: manipulation;
        }}
        #streamOverlay.show {{
            display: flex;
        }}
        .overlay-card {{
            background: var(--card-bg);
            border: 2px solid var(--accent-primary);
            border-radius: 20px;
            padding: 28px 20px;
            max-width: 400px;
            width: 100%;
            /* Never taller than the gate. Laid out as a column so the engage
               button is a fixed row that is always on screen; only the copy
               above it can shrink. No scrollbar anywhere. */
            max-height: 100%;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            justify-content: center;
            overflow: hidden;
            margin: 0;
            box-shadow: 0 16px 50px rgba(0, 0, 0, 0.9), 0 0 30px rgba(176, 88, 255, 0.3);
        }}
        .overlay-card h2 {{
            flex: 0 0 auto;
            margin: 0 0 10px 0;
            font-size: 1.4rem;
            background: var(--header-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 900;
        }}
        .overlay-card p {{
            flex: 0 1 auto;
            min-height: 0;
            overflow: hidden;
            font-size: 0.88rem;
            color: var(--text-dim);
            margin: 0 0 20px 0;
            line-height: 1.4;
        }}
        .overlay-card p.stream-overlay-detail {{
            color: #ffaa00;
            font-size: 0.82rem;
            margin: -8px 0 18px 0;
        }}
        .overlay-card .btn-activate-stream {{
            /* The one control that must never leave the screen. */
            flex: 0 0 auto;
        }}
        .btn-activate-stream {{
            background: var(--btn-gradient);
            color: #fff;
            border: none;
            padding: 16px 28px;
            border-radius: 14px;
            font-size: 1.05rem;
            font-weight: 800;
            cursor: pointer;
            width: 100%;
            box-shadow: var(--btn-shadow);
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        /* Second action on the gate: only appears when the chosen library
           cannot play, so the listener is never left on a dead button. */
        .btn-switch-library {{
            margin-top: 10px;
            padding: 12px 20px;
            font-size: 0.82rem;
            background: rgba(255, 255, 255, 0.10);
            box-shadow: none;
            border: 1px solid var(--accent-secondary);
            color: var(--accent-secondary);
        }}
        .btn-switch-library:disabled {{
            opacity: 0.55;
            cursor: default;
        }}
        /* Short viewports (landscape phones, split screens): shrink the card
           instead of scrolling it, so the engage button stays centred and on
           screen no matter how little height there is. These rules sit after
           the base sizes on purpose: same specificity, so they must come last
           to win. */
        @media (max-height: 560px) {{
            .overlay-card {{
                padding: 16px 16px;
            }}
            .overlay-card h2 {{
                margin-bottom: 6px;
                font-size: 1.15rem;
            }}
            .overlay-card p {{
                margin-bottom: 12px;
                font-size: 0.78rem;
            }}
            .overlay-card p.stream-overlay-detail {{
                margin: -4px 0 12px 0;
            }}
            .btn-activate-stream {{
                padding: 12px 20px;
                font-size: 0.92rem;
            }}
        }}
        @media (max-height: 400px) {{
            .overlay-card {{
                padding: 10px 14px;
            }}
            .overlay-card h2 {{
                font-size: 1rem;
                margin-bottom: 4px;
            }}
            .overlay-card p {{
                margin-bottom: 8px;
                font-size: 0.74rem;
            }}
            .overlay-card p.stream-overlay-detail {{
                margin: 0 0 8px 0;
            }}
            .btn-activate-stream {{
                padding: 10px 16px;
                font-size: 0.84rem;
            }}
        }}
        .vis-block {{
            width: 100%;
            margin: 12px 0 16px 0;
        }}
        .vis-effects {{
            display: flex;
            gap: 6px;
            margin-bottom: 6px;
        }}
        .vis-effect {{
            flex: 1;
            min-width: 0;
            appearance: none;
            -webkit-appearance: none;
            border: 1px solid var(--card-border);
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-dim);
            border-radius: 8px;
            padding: 6px 8px;
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.3px;
            text-transform: uppercase;
            cursor: pointer;
            touch-action: manipulation;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .vis-effect.active {{
            background: var(--badge-bg);
            color: var(--text-main);
            border-color: var(--accent-primary);
        }}
        .visualizer-container {{
            width: 100%;
            height: 56px;
            display: flex;
            align-items: stretch;
            justify-content: center;
            gap: 3px;
            margin: 0;
            padding: 4px 6px;
            background: rgba(0, 0, 0, 0.35);
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.06);
            position: relative;
            cursor: pointer;
            /* manipulation keeps the synthesised click on iOS. touch-action:none
               swallows it, which is the same trap the Engage button hit. */
            touch-action: manipulation;
            -webkit-user-select: none;
            user-select: none;
            transition: height 0.18s ease;
        }}
        .visualizer-container:focus-visible {{
            outline: 2px solid var(--accent-primary);
            outline-offset: 2px;
        }}
        .visualizer-container.mode-spectrogram {{
            height: 168px;
            padding: 22px 6px 6px;
        }}
        .vis-caption {{
            position: absolute;
            left: 8px;
            right: 8px;
            top: 3px;
            z-index: 2;
            font-size: 0.62rem;
            line-height: 1.2;
            letter-spacing: 0.4px;
            text-transform: uppercase;
            color: var(--text-dim);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            pointer-events: none;
            text-shadow: 0 1px 2px rgba(0, 0, 0, 0.85);
        }}
        .visualizer-container.mode-spectrogram .vis-caption {{
            top: 4px;
            color: var(--text-main);
        }}
        #visSpectrogram {{
            display: none;
            position: absolute;
            left: 6px;
            right: 6px;
            top: 22px;
            bottom: 6px;
            width: calc(100% - 12px);
            height: calc(100% - 28px);
            border-radius: 6px;
            /* The drawing must not take the tap. On iPhone a press on the
               canvas never becomes a click, so the second press could not
               return to the bars. */
            pointer-events: none;
        }}
        .visualizer-container.mode-spectrogram #visSpectrogram {{
            display: block;
        }}
        .v-col {{
            flex: 1;
            max-width: 14px;
            min-height: 0;
            align-self: stretch;
            display: flex;
            align-items: flex-end;
            position: relative;
        }}
        .visualizer-container.mode-spectrogram .v-col {{
            display: none;
        }}
        .v-bar {{
            width: 100%;
            height: 2px;
            background: linear-gradient(180deg, var(--eq-peak) 0%, var(--eq-bar) 28%, var(--accent-primary) 100%);
            border-radius: 2px 2px 0 0;
            position: relative;
        }}
        .v-peak {{
            position: absolute;
            left: 0;
            right: 0;
            height: 2px;
            background: var(--eq-peak);
            border-radius: 1px;
            bottom: 2px;
            pointer-events: none;
        }}
        .progress-box {{
            width: 100%;
            margin-bottom: 16px;
        }}
        .progress-bar-bg {{
            width: 100%;
            height: 44px;
            display: flex;
            align-items: center;
            background: transparent;
            cursor: pointer;
            position: relative;
            touch-action: none;
        }}
        .progress-track {{
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 5px;
            overflow: hidden;
            position: relative;
        }}
        .progress-bar-fill {{
            height: 100%;
            width: 0%;
            background: var(--progress-fill);
            border-radius: 5px;
            transition: width 0.15s ease-out;
        }}
        .progress-bar-bg.scrubbing .progress-bar-fill {{
            transition: none;
        }}
        .time-row {{
            display: flex;
            justify-content: space-between;
            font-size: 0.8rem;
            color: var(--text-dim);
            font-weight: 600;
            margin-top: 6px;
        }}
        .controls-row {{
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 10px;
            width: 100%;
            margin-top: 8px;
        }}
        .mode-controls-row {{
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 10px;
            width: 100%;
            margin-top: 10px;
        }}
        .btn-mode {{
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 8px 12px;
            font-size: 0.84rem;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-dim);
            border-radius: 10px;
        }}
        .btn-mode.active {{
            background: var(--badge-bg);
            color: var(--text-main);
            border-color: var(--accent-primary);
            font-weight: 800;
        }}
        .badge-shuffle {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-dim);
            border-color: rgba(255, 255, 255, 0.12);
            cursor: pointer;
        }}
        .badge-shuffle.active {{
            background: var(--badge-bg);
            color: var(--badge-color);
            border-color: var(--badge-color);
            font-weight: 800;
        }}
        button {{
            background: rgba(255, 255, 255, 0.07);
            color: var(--text-main);
            border: 1px solid var(--card-border);
            padding: 12px 16px;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 700;
            cursor: pointer;
        }}
        .btn-main {{
            background: var(--btn-gradient);
            color: #fff;
            padding: 14px 24px;
            font-size: 1.15rem;
            box-shadow: var(--btn-shadow);
            border: none;
            flex-grow: 1;
            max-width: 180px;
        }}
        /* The transport button changes wording with playback, so it needs room
           for the widest label and must not reflow the control row when it
           flips between Play and Pause. */
        #btnPlayPause {{
            min-width: 132px;
            white-space: nowrap;
        }}
        /* A playing mix keeps the accent glow; a paused one reads as the
           standing-by control it is. */
        #btnPlayPause.is-playing {{
            box-shadow: var(--btn-shadow), 0 0 0 2px var(--badge-color) inset;
        }}
        .sec-controls {{
            display: flex;
            /* Two control clusters cannot sit side by side on a 390px phone;
               without stacking, the pitch cluster is clipped off the card. */
            flex-direction: column;
            gap: 8px;
            width: 100%;
            margin-top: 14px;
            padding-top: 12px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
        }}
        .control-group {{
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 1 1 auto;
            min-width: 0;
            justify-content: center;
            flex-wrap: nowrap;
        }}
        .control-pill {{
            font-size: 0.78rem;
            color: var(--text-dim);
            font-weight: 700;
            min-width: 52px;
            text-align: center;
        }}
        .btn-mini {{
            padding: 6px 10px;
            font-size: 0.78rem;
            border-radius: 8px;
        }}
        .tracklist-card {{
            max-height: 280px;
            display: flex;
            flex-direction: column;
        }}
        .tl-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.07);
        }}
        .tl-header h3 {{
            margin: 0;
            font-size: 0.92rem;
            color: var(--accent-secondary);
            font-weight: 800;
        }}
        .tl-items {{
            overflow-y: auto;
            flex: 1;
            padding-right: 4px;
            list-style: none;
            margin: 0;
            padding-left: 0;
        }}
        .tl-item {{
            padding: 6px 10px;
            margin-bottom: 4px;
            border-radius: 6px;
            font-size: 0.82rem;
            color: var(--text-main);
            background: rgba(255, 255, 255, 0.02);
            border-left: 3px solid var(--accent-primary);
        }}
        .footer {{
            margin-top: auto;
            text-align: center;
            font-size: 0.72rem;
            color: var(--text-dim);
            opacity: 0.75;
            padding: 10px 0;
        }}
        /* Desktop launch pad: only rendered for the Linux host. Themed with the
           same palette variables as the rest of the dashboard. A running app
           keeps its button pressed down; an app that is not running sits up. */
        .launchpad {{
            width: 100%;
            max-width: 520px;
            margin-top: 16px;
            padding: 12px;
            border-radius: 16px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            box-shadow: var(--card-shadow);
        }}
        .launchpad-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 10px;
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 0.7px;
            text-transform: uppercase;
            color: var(--text-dim);
        }}
        .launchpad-grid {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px;
        }}
        .launch-btn {{
            appearance: none;
            -webkit-appearance: none;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 4px;
            min-width: 0;
            padding: 10px 4px;
            border-radius: 12px;
            font-family: inherit;
            font-size: 0.68rem;
            font-weight: 800;
            line-height: 1.15;
            text-align: center;
            color: var(--text-dim);
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--card-border);
            box-shadow: 0 3px 0 rgba(0, 0, 0, 0.35);
            cursor: pointer;
            transition: transform 0.1s ease, box-shadow 0.1s ease,
                        background 0.15s ease, color 0.15s ease;
            overflow: hidden;
        }}
        .launch-btn .launch-icon {{
            font-size: 1.15rem;
            line-height: 1;
        }}
        .launch-btn .launch-label {{
            display: block;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .launch-btn:hover {{
            color: var(--text-main);
            border-color: var(--accent-secondary);
        }}
        .launch-btn:focus-visible {{
            outline: 2px solid var(--accent-secondary);
            outline-offset: 2px;
        }}
        /* Pressed down: the button is physically sunk into the panel and lit
           with the theme's primary/secondary gradient. */
        .launch-btn.running {{
            color: #fff;
            background: var(--btn-gradient);
            border-color: var(--badge-color);
            box-shadow: inset 0 3px 8px rgba(0, 0, 0, 0.55),
                        0 0 0 1px var(--badge-color) inset,
                        0 0 14px -2px var(--accent-primary);
            transform: translateY(2px);
        }}
        .launch-btn.running .launch-state {{
            color: var(--badge-color);
        }}
        .launch-btn:active {{
            transform: translateY(2px);
            box-shadow: inset 0 3px 8px rgba(0, 0, 0, 0.55);
        }}
        .launch-btn.busy {{
            cursor: progress;
            opacity: 0.7;
        }}
        .launch-state {{
            font-size: 0.58rem;
            font-weight: 800;
            letter-spacing: 0.4px;
            text-transform: uppercase;
            opacity: 0.9;
        }}
        .launch-close {{
            grid-column: 1 / -1;
            flex-direction: row;
            gap: 8px;
            margin-top: 8px;
            padding: 11px 10px;
            font-size: 0.76rem;
            background: rgba(255, 255, 255, 0.03);
            color: var(--accent-crimson);
            border-color: var(--accent-crimson);
            box-shadow: 0 3px 0 rgba(0, 0, 0, 0.35);
        }}
        .launch-close:hover {{
            color: #fff;
            background: var(--accent-crimson);
            border-color: var(--accent-crimson);
        }}
        /* MP Visualizer: a full-width button under the grid, because it is one
           window with three states rather than a member of the suite. */
        .launch-visualizer {{
            flex-direction: row;
            gap: 8px;
            width: 100%;
            margin-top: 8px;
            padding: 12px 10px;
            font-size: 0.8rem;
            color: var(--accent-secondary);
            border-color: var(--accent-primary);
        }}
        .launch-visualizer .launch-label {{
            flex: 1;
            text-align: left;
            white-space: normal;
        }}
        .launch-visualizer.running {{
            color: #fff;
        }}
        .launch-visualizer.fullscreen {{
            border-color: var(--badge-color);
            box-shadow: inset 0 3px 8px rgba(0, 0, 0, 0.55),
                        0 0 0 1px var(--badge-color) inset,
                        0 0 18px -2px var(--accent-secondary);
        }}
        /* TTY Audio Mode: the desktop launchers cannot work from the text
           console, so they are visibly disabled and the reason is spelled out. */
        .launch-btn:disabled,
        .launch-btn[disabled] {{
            opacity: 0.4;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
            filter: grayscale(1);
        }}
        .launchpad-tty {{
            display: block;
            margin: 0 0 10px 0;
            padding: 10px 12px;
            border-radius: 12px;
            border: 1px solid var(--accent-tertiary);
            background: rgba(255, 45, 142, 0.12);
            color: var(--text-main);
            font-size: 0.72rem;
            line-height: 1.45;
            text-align: left;
        }}
        .launchpad-tty[hidden] {{
            display: none;
        }}
        .launchpad-tty strong {{
            display: block;
            margin-bottom: 3px;
            color: var(--accent-tertiary);
            font-size: 0.74rem;
            letter-spacing: 0.4px;
            text-transform: uppercase;
        }}
        .launchpad-note {{
            margin-top: 8px;
            font-size: 0.66rem;
            font-weight: 700;
            color: var(--text-dim);
            text-align: center;
        }}
        .launchpad-note.ok {{
            color: var(--badge-color);
        }}
        /* ------------------------------------------------------------------
           Top tabs: the dashboard switches between the MP Liquid Player and
           the MP Assistant without a page load, so a stream that is already
           playing keeps playing while the user chats.
           ------------------------------------------------------------------ */
        .app-tabs {{
            display: flex;
            gap: 6px;
            width: 100%;
            max-width: 520px;
            margin: 0 0 16px 0;
            padding: 5px;
            border-radius: 16px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            box-shadow: var(--card-shadow);
            position: sticky;
            top: 8px;
            z-index: 200;
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
        }}
        .app-tab {{
            appearance: none;
            -webkit-appearance: none;
            flex: 1 1 0;
            min-width: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 7px;
            padding: 12px 10px;
            border: 1px solid transparent;
            border-radius: 12px;
            background: transparent;
            color: var(--text-dim);
            font-family: inherit;
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            cursor: pointer;
            transition: background 0.18s ease, color 0.18s ease,
                        box-shadow 0.18s ease, transform 0.12s ease;
        }}
        .app-tab .app-tab-icon {{
            font-size: 1rem;
            line-height: 1;
        }}
        .app-tab:hover {{
            color: var(--text-main);
        }}
        .app-tab:active {{
            transform: scale(0.98);
        }}
        .app-tab:focus-visible {{
            outline: 2px solid var(--accent-secondary);
            outline-offset: 2px;
        }}
        .app-tab.active {{
            color: #fff;
            background: var(--btn-gradient);
            border-color: var(--badge-color);
            box-shadow: var(--btn-shadow), 0 0 0 1px var(--badge-color) inset;
        }}
        .app-tab .app-tab-badge {{
            display: none;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--accent-tertiary);
            box-shadow: 0 0 8px var(--accent-tertiary);
        }}
        .app-tab.just-pinged .app-tab-badge {{
            display: inline-block;
        }}
        .tab-panel {{
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .tab-panel[hidden] {{
            display: none !important;
        }}
        /* The assistant needs vertical space on a phone. While it is the active
           tab the big brand header collapses to one compact line. */
        body.assistant-tab-open .header h1 {{
            font-size: 1.05rem;
            letter-spacing: 0.8px;
        }}
        body.assistant-tab-open .header p,
        body.assistant-tab-open .theme-bar,
        body.assistant-tab-open .theme-pill,
        body.assistant-tab-open #autoplayNotice {{
            display: none !important;
        }}
        body.assistant-tab-open .header {{
            margin-bottom: 10px;
        }}
        /* Assistant shell */
        .assistant-shell {{
            width: 100%;
            max-width: 520px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-bottom: 16px;
        }}
        .assistant-card {{
            background-color: var(--card-bg);
            border-radius: 18px;
            padding: 16px;
            border: 1px solid var(--card-border);
            box-shadow: var(--card-shadow);
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .assistant-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .assistant-title {{
            display: flex;
            flex-direction: column;
            gap: 2px;
            min-width: 0;
        }}
        .assistant-title strong {{
            font-size: 0.95rem;
            color: var(--text-main);
            letter-spacing: 0.4px;
        }}
        .assistant-title span {{
            font-size: 0.7rem;
            color: var(--text-dim);
            font-weight: 600;
        }}
        .assistant-state {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.66rem;
            font-weight: 800;
            letter-spacing: 0.6px;
            text-transform: uppercase;
            border: 1px solid var(--card-border);
            color: var(--text-dim);
            background: rgba(255, 255, 255, 0.04);
            white-space: nowrap;
        }}
        .assistant-state .dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--text-dim);
            flex: 0 0 auto;
        }}
        .assistant-state.online {{
            color: var(--badge-color);
            border-color: var(--badge-color);
            background: var(--badge-bg);
        }}
        .assistant-state.online .dot {{
            background: var(--badge-color);
            box-shadow: 0 0 10px var(--badge-color);
        }}
        .assistant-state.offline {{
            color: var(--accent-crimson);
            border-color: var(--accent-crimson);
        }}
        .assistant-state.offline .dot {{
            background: var(--accent-crimson);
        }}
        .assistant-voice-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 0.72rem;
            color: var(--text-dim);
            font-weight: 700;
        }}
        .assistant-voice-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 11px;
            border-radius: 12px;
            background: var(--badge-bg);
            color: var(--badge-color);
            border: 1px solid var(--badge-color);
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.3px;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .assistant-actions {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .assistant-actions button {{
            flex: 1 1 auto;
            padding: 9px 12px;
            font-size: 0.74rem;
            border-radius: 10px;
        }}
        .assistant-actions .btn-speak {{
            background: var(--btn-gradient);
            color: #fff;
            border: none;
            box-shadow: var(--btn-shadow);
        }}
        .assistant-actions .btn-speak.off {{
            background: rgba(255, 255, 255, 0.06);
            color: var(--text-dim);
            box-shadow: none;
            border: 1px solid var(--card-border);
        }}
        .chat-log {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            /* Roughly half a phone screen, so the prompt stays reachable. */
            max-height: 46vh;
            min-height: 150px;
            overflow-y: auto;
            overscroll-behavior: contain;
            -webkit-overflow-scrolling: touch;
            padding: 4px 4px 4px 0;
            touch-action: pan-y;
        }}
        .chat-log::-webkit-scrollbar {{
            width: 5px;
        }}
        .chat-log::-webkit-scrollbar-thumb {{
            background: var(--accent-secondary);
            border-radius: 3px;
        }}
        .chat-bubble {{
            max-width: 92%;
            padding: 10px 12px;
            border-radius: 14px;
            font-size: 0.82rem;
            line-height: 1.45;
            white-space: pre-wrap;
            word-break: break-word;
            border: 1px solid var(--card-border);
            background: rgba(255, 255, 255, 0.04);
            color: var(--text-main);
            position: relative;
        }}
        .chat-bubble.user {{
            align-self: flex-end;
            background: var(--badge-bg);
            border-color: var(--badge-color);
            color: var(--text-main);
        }}
        .chat-bubble.assistant {{
            align-self: flex-start;
            border-left: 3px solid var(--accent-primary);
        }}
        .chat-bubble.system {{
            align-self: center;
            font-size: 0.7rem;
            font-weight: 700;
            color: var(--text-dim);
            background: transparent;
            border-style: dashed;
            text-align: center;
            max-width: 100%;
        }}
        .chat-bubble .bubble-tools {{
            display: flex;
            gap: 6px;
            margin-top: 8px;
            padding-top: 6px;
            border-top: 1px solid rgba(255, 255, 255, 0.08);
            align-items: center;
        }}
        .chat-bubble .bubble-tools button {{
            padding: 4px 9px;
            font-size: 0.68rem;
            border-radius: 8px;
        }}
        .chat-bubble.speaking {{
            border-color: var(--accent-tertiary);
            box-shadow: 0 0 0 1px var(--accent-tertiary) inset;
        }}
        .chat-bubble .speak-state {{
            font-size: 0.62rem;
            font-weight: 800;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            color: var(--accent-tertiary);
        }}
        .chat-empty {{
            align-self: center;
            text-align: center;
            font-size: 0.76rem;
            color: var(--text-dim);
            padding: 16px 12px;
            border-radius: 12px;
            border: 1px dashed var(--card-border);
            line-height: 1.5;
        }}
        .chat-empty strong {{
            display: block;
            color: var(--text-main);
            margin-bottom: 4px;
            font-size: 0.86rem;
        }}
        .chat-composer {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .chat-composer textarea {{
            width: 100%;
            min-height: 64px;
            max-height: 160px;
            resize: vertical;
            padding: 11px 12px;
            border-radius: 12px;
            border: 1px solid var(--card-border);
            background: rgba(0, 0, 0, 0.28);
            color: var(--text-main);
            font-family: inherit;
            font-size: 0.88rem;
            line-height: 1.4;
        }}
        .chat-composer textarea:focus {{
            outline: none;
            border-color: var(--accent-secondary);
            box-shadow: 0 0 0 1px var(--accent-secondary);
        }}
        .chat-buttons {{
            display: flex;
            gap: 8px;
        }}
        .chat-buttons .btn-send {{
            flex: 1;
            background: var(--btn-gradient);
            color: #fff;
            border: none;
            box-shadow: var(--btn-shadow);
            font-weight: 800;
            letter-spacing: 0.6px;
            text-transform: uppercase;
            font-size: 0.82rem;
            padding: 12px 16px;
        }}
        .chat-buttons .btn-stop {{
            display: none;
            background: var(--accent-crimson);
            color: #fff;
            border: none;
            font-weight: 800;
            padding: 12px 16px;
        }}
        .chat-buttons .btn-stop.show {{
            display: block;
        }}
        .chat-buttons button:disabled {{
            opacity: 0.5;
            cursor: progress;
        }}
        .assistant-hint {{
            font-size: 0.66rem;
            color: var(--text-dim);
            font-weight: 600;
            text-align: center;
            line-height: 1.5;
        }}
        .assistant-hint code {{
            background: rgba(255, 255, 255, 0.07);
            padding: 1px 5px;
            border-radius: 5px;
            color: var(--accent-secondary);
            font-size: 0.66rem;
        }}
        @media (max-width: 390px) {{
            .chat-log {{
                max-height: 40vh;
            }}
        }}
        @media (max-width: 390px) {{
            .launchpad-grid {{
                gap: 6px;
            }}
            .launch-btn {{
                padding: 9px 3px;
                font-size: 0.62rem;
            }}
            .launch-btn .launch-icon {{
                font-size: 1rem;
            }}
        }}
    </style>
</head>
<body class="{'dynamic-rainbow-glass-active' if t.get('is_dynamic_rainbow') else ''}">
    <!-- Hue wash for Dynamic Liquid Glass. Hidden until that theme is on.
         A ::before cannot sit above the body's opaque background on iOS. -->
    <div id="rainbowWash" aria-hidden="true"></div>
    <!-- On <body>, not inside a tab panel: hiding a panel is display:none on
         the ancestor, and iOS then refuses play() — the Engage tap looks dead. -->
    <audio id="mobileAudioStream" class="phone-audio" playsinline webkit-playsinline preload="none"></audio>
    <audio id="assistantSpeech" class="phone-audio" playsinline webkit-playsinline preload="none"></audio>
    <div id="restartNotice" class="restart-notice" role="status" aria-live="polite">
        <span class="restart-spinner" aria-hidden="true"></span>
        <span id="restartNoticeText">MP Audio Player (Web) has been restarted — reconnecting…</span>
    </div>
    <!-- Stream Activation Modal to Satisfy Mobile Autoplay Policies.
         The dashboard starts the stream by itself as soon as it loads and stays
         out of the way while it does, so this gate begins hidden and comes up
         only when the browser refused that (iOS Safari, or a desktop autoplay
         policy) or when the host has nothing queued. The whole card is then the
         engage control: one tap anywhere starts the same stream the automatic
         attempt wanted to start, so the listener never has to aim at the
         button. -->
    <div id="streamOverlay"
         ontouchend="activateMobileStream(event)"
         onclick="activateMobileStream(event)">
        <div class="overlay-card">
            <h2>🎧 MP Audio Player (Web)</h2>
            <p id="streamOverlayLead">Tap anywhere to start the mix on this device.</p>
            <p id="streamOverlayDetail" class="stream-overlay-detail" style="display: none;"></p>
            <button type="button" class="btn-activate-stream"
                    ontouchend="activateMobileStream(event)"
                    onclick="activateMobileStream(event)">Engage Audio Stream</button>
            <!-- Only shown when the selected library itself is unplayable (dead
                 music shares). One tap switches to the Mix Archive instead of
                 leaving the listener on a button that cannot produce sound. -->
            <button type="button" id="streamOverlaySwitch"
                    class="btn-activate-stream btn-switch-library"
                    ontouchend="event.stopPropagation(); switchToPlayableLibrary();"
                    onclick="event.stopPropagation(); switchToPlayableLibrary();"
                    style="display: none;">Switch to Mix Archive</button>
        </div>
    </div>

    <!-- Top tabs. The stream keeps playing while the assistant tab is open,
         so the listener can switch back at any time without re-engaging it. -->
    <div class="app-tabs" role="tablist" aria-label="MP Web Companion sections">
        <button type="button" class="app-tab active" id="tabPlayer" role="tab"
                aria-selected="true" aria-controls="panelPlayer"
                onclick="switchAppTab('player')">
            <span class="app-tab-icon" aria-hidden="true">🎧</span> MP Audio Player
        </button>
        <button type="button" class="app-tab" id="tabAssistant" role="tab"
                aria-selected="false" aria-controls="panelAssistant"
                onclick="switchAppTab('assistant')">
            <span class="app-tab-icon" aria-hidden="true">🧠</span> MP Assistant
            <span class="app-tab-badge" aria-hidden="true"></span>
        </button>
    </div>

    <div class="header">
        <h1>MP AUDIO PLAYER <span class="app-version">v{html.escape(VERSION)}</span></h1>
        <p>Studio & DJ Booth Mobile Web Companion</p>
        <div id="themeBadge" class="theme-pill">🎨 <span id="themeNameText">{html.escape(t['name'])}</span></div>
        <div class="theme-bar">
            <button class="theme-btn" id="themeBtn" onclick="toggleThemeMenu()"
                    aria-haspopup="true" aria-expanded="false"
                    title="Change the companion theme">
                🎨 Theme
            </button>
        </div>
        <div class="theme-menu" id="themeMenu" role="menu"></div>
        <div id="autoplayNotice" class="autoplay-notice" style="display: none;"></div>
    </div>

    <div class="tab-panel" id="panelPlayer" role="tabpanel" aria-labelledby="tabPlayer">
    <div class="card now-playing">
        <div id="mobileAudioBanner" class="mobile-audio-banner">
            <h4>📱 Mobile Audio Stream Active</h4>
            <p>High-resolution FLAC/WAV streaming active</p>
        </div>

        <div class="badges-row">
            <div id="playBadge" class="badge">STANDBY</div>
            <div id="shuffleBadge" class="badge badge-shuffle" onclick="sendAction('shuffle')">🔀 SHUFFLE OFF</div>
            <div id="sourceBadge" class="badge badge-source">CHECKING...</div>
        </div>

        <div class="track-info">
            <img id="trackCover" class="track-cover" alt="" hidden>
            <div class="title-row">
                <div id="trackTitle" class="track-title">Connecting to MP Mix Session...</div>
                <button type="button" class="btn-fav" id="btnFavorite"
                        onclick="toggleFavorite()"
                        aria-pressed="false"
                        aria-label="Add to favourites">♡</button>
            </div>
            <div id="trackArtist" class="track-artist">—</div>
            <div id="trackSpecs" class="track-specs">
                <span id="specFormat" class="spec-chip">FLAC / WAV</span>
                <span id="specRate" class="spec-chip">48 kHz</span>
                <span id="specChannels" class="spec-chip">Stereo</span>
            </div>
        </div>

        <!-- Two effects only: the bars, and Stream of Frequency. -->
        <div class="vis-block">
            <div class="vis-effects" role="group" aria-label="Equaliser effects">
                <button type="button" class="vis-effect active" id="vizEffectBars"
                        aria-pressed="true"
                        ontouchend="selectVisualizerMode(event, 'bars')"
                        onclick="selectVisualizerMode(event, 'bars')">Equaliser</button>
                <button type="button" class="vis-effect" id="vizEffectSpectro"
                        aria-pressed="false"
                        title="Stream of Frequency (Dynamic Spectrogram)"
                        ontouchend="selectVisualizerMode(event, 'spectrogram')"
                        onclick="selectVisualizerMode(event, 'spectrogram')">Stream of Frequency</button>
            </div>
            <div class="visualizer-container" id="visContainer" role="button" tabindex="0"
                 aria-pressed="false"
                 aria-label="Equaliser. Press to show Stream of Frequency, Dynamic Spectrogram."
                 ontouchend="toggleVisualizerMode(event)"
                 onclick="toggleVisualizerMode(event)">
                <div class="vis-caption" id="visCaption">Equaliser</div>
                {"".join(f'<div class="v-col"><div class="v-bar" id="vb{i}"></div><div class="v-peak" id="vp{i}"></div></div>' for i in range(24))}
                <canvas id="visSpectrogram" aria-hidden="true"></canvas>
            </div>
        </div>

        <!-- Progress & Time -->
        <div class="progress-box">
            <div class="progress-bar-bg" id="progressBg" role="slider"
                 aria-label="Playback position" aria-valuemin="0" aria-valuemax="100"
                 aria-valuenow="0" tabindex="0">
                <div class="progress-track">
                    <div class="progress-bar-fill" id="progressBar"></div>
                </div>
            </div>
            <div class="time-row">
                <span id="posTime">00:00</span>
                <span id="durTime">--:--</span>
            </div>
        </div>

        <!-- Primary Controls -->
        <div class="controls-row">
            <button class="btn-sub" onclick="skipBy(-30)"
                    title="Rewind 30 seconds (Left arrow rewinds 10s, Shift+Left rewinds 30s)">⏪ 30s</button>
            <button onclick="sendAction('previous')">⏮</button>
            <button class="btn-main" id="btnPlayPause" onclick="handlePlayPauseClick()"
                    aria-label="Paused - tap to play">▶ Play</button>
            <button onclick="sendAction('next')">⏭</button>
            <button class="btn-sub" onclick="skipBy(30)"
                    title="Forward 30 seconds (Right arrow forwards 10s, Shift+Right forwards 30s)">30s ⏩</button>
        </div>

        <!-- Mode Controls -->
        <div class="mode-controls-row">
            <button class="btn-sub btn-mode" id="btnShuffle" onclick="sendAction('shuffle')">
                <span>🔀</span> Shuffle: <span id="shufText">OFF</span>
            </button>
            <button class="btn-sub btn-mode" id="btnRepeat" onclick="cycleRepeat()">
                <span>🔁</span> Repeat: <span id="repText">ALL</span>
            </button>
            <button class="btn-sub btn-mode" id="btnSleep" onclick="cycleSleep()"
                    aria-label="Sleep timer">
                <span>🌙</span> Sleep: <span id="sleepText">OFF</span>
            </button>
        </div>

        <!-- Secondary Controls -->
        <div class="sec-controls">
            <div class="control-group">
                <button class="btn-mini" onclick="bumpVolume(-5); sendAction('vol_down')">🔉 -</button>
                <span class="control-pill" id="volPill">Vol: 85%</span>
                <button class="btn-mini" onclick="bumpVolume(5); sendAction('vol_up')">+ 🔊</button>
                <button class="btn-mini" onclick="toggleStreamMute(); sendAction('mute')">🔇</button>
            </div>
            <div class="control-group">
                <button class="btn-mini" onclick="bumpPitch(-1); sendAction('pitch_down')">[-]</button>
                <span class="control-pill" id="pitchPill">Pitch: 0%</span>
                <button class="btn-mini" onclick="bumpPitch(1); sendAction('pitch_up')">[+]</button>
                <button class="btn-mini" onclick="applyStreamPitch(0); sendAction('reset_pitch')">0</button>
            </div>
        </div>
        <!-- Mini Playlist: the current track plus the next four, scrollable -->
        <div class="mini-playlist" id="miniPlaylist">
            <div class="mini-pl-head">
                <span id="miniPlTitle">🎵 Up Next</span>
                <span class="mini-pl-count" id="miniPlCount">0 tracks</span>
            </div>
            <div class="mode-switch" id="modeSwitch" role="group" aria-label="Choose library mode">
                <button type="button" data-mode="MIX_ARCHIVE" onclick="switchLibraryMode('MIX_ARCHIVE')">Mix</button>
                <button type="button" data-mode="MUSIC" onclick="switchLibraryMode('MUSIC')">Music</button>
                <button type="button" data-mode="FAVORITES" onclick="switchLibraryMode('FAVORITES')">Favourites</button>
            </div>
            <label class="mini-pl-search" for="miniPlSearch">
                <input id="miniPlSearch" type="search" inputmode="search" enterkeyhint="search"
                       placeholder="Search this playlist"
                       aria-label="Search tracks in the current playlist"
                       autocomplete="off" autocapitalize="off" spellcheck="false"
                       oninput="onPlaylistSearchInput()"
                       onsearch="onPlaylistSearchInput()">
            </label>
            <div id="miniPlNote"></div>
            <div id="miniPlItems"></div>
        </div>
    </div>

    <!-- Tracklist Card -->
    <div class="card tracklist-card" id="tlCard" style="display: none;">
        <div class="tl-header">
            <h3>📜 Mix Tracklist</h3>
            <span class="tl-count" id="tlCount">0 Tracks</span>
        </div>
        <ul class="tl-items" id="tlItems"></ul>
    </div>

    {launch_pad_html}
    </div><!-- /panelPlayer -->

    <div class="tab-panel" id="panelAssistant" role="tabpanel" aria-labelledby="tabAssistant" hidden>
        <div class="assistant-shell">
            <div class="assistant-card">
                <div class="assistant-head">
                    <div class="assistant-title">
                        <strong>🧠 MP Harmony Agent</strong>
                        <span id="assistantModel">Local engineering assistant</span>
                    </div>
                    <span class="assistant-state" id="assistantState">
                        <span class="dot" aria-hidden="true"></span>
                        <span id="assistantStateText">Checking…</span>
                    </span>
                </div>

                <div class="assistant-voice-row">
                    <span>🔊 Voice</span>
                    <span class="assistant-voice-pill" id="assistantVoicePill"
                          title="Stephen Hawking speaks every reply until you change it with /voice">
                        Stephen Hawking
                    </span>
                </div>

                <div class="assistant-actions">
                    <button type="button" class="btn-speak" id="assistantSpeakBtn"
                            onclick="toggleAutoSpeak()"
                            title="Read every reply out loud on this device">
                        🔊 Auto-speak: ON
                    </button>
                    <button type="button" id="assistantStartBtn"
                            onclick="startAssistantAgent()"
                            title="Start the MP Harmony Agent on the computer">
                        ▶ Start Agent
                    </button>
                </div>

                <div class="chat-log" id="chatLog" aria-live="polite">
                    <div class="chat-empty" id="chatEmpty">
                        <strong>MP Harmony is ready when you are.</strong>
                        Ask about your mixes, tracklists or archive — replies are
                        spoken in Stephen Hawking's voice.
                    </div>
                </div>

                <div class="chat-composer">
                    <textarea id="chatPrompt" rows="2"
                              placeholder="Ask MP Harmony anything… (/voice switches the voice)"
                              aria-label="Message MP Harmony Agent"></textarea>
                    <div class="chat-buttons">
                        <button type="button" class="btn-send" id="chatSendBtn"
                                onclick="sendAssistantPrompt()">Send</button>
                        <button type="button" class="btn-stop" id="chatStopBtn"
                                onclick="stopAssistantReply()">Stop</button>
                    </div>
                </div>

                <div class="assistant-hint">
                    Type <code>/voice</code> to list voices, <code>/voice hawking</code>
                    for Stephen Hawking, or <code>/voice speed 1.15</code>.
                </div>
            </div>
        </div>
    </div><!-- /panelAssistant -->

    <div class="footer">
        MP Mix Archive Manager v{html.escape(VERSION)} • MPlanetarian (Dreamworlds Productions)
    </div>

    <script>
        // The server requires this header on every state-changing request
        // (CSRF defence: a cross-origin form cannot set a custom header
        // without a preflight). Wrap fetch once here so every POST the
        // dashboard makes carries it. Without this the server answered
        // "missing_csrf_header" and every control tap (transport, launch pad,
        // assistant stop) was silently refused.
        const MP_CSRF_HEADER = '{sec.REQUIRED_HEADER}';
        const mpNativeFetch = window.fetch.bind(window);
        window.fetch = function (input, init) {{
            const options = Object.assign({{}}, init || {{}});
            const method = String(options.method || 'GET').toUpperCase();
            if (method !== 'GET' && method !== 'HEAD') {{
                const headers = new Headers(options.headers || {{}});
                if (!headers.has(MP_CSRF_HEADER)) headers.set(MP_CSRF_HEADER, '1');
                options.headers = headers;
            }}
            return mpNativeFetch(input, options);
        }};

        // Older versions required an access token in the URL. The companion no
        // longer has one, but a bookmark saved back then still carries a dead
        // value in its query string. Strip it so it is not kept in history or
        // sent onward. `replaceState` avoids a reload, which would cost the
        // user their audio-unlock gesture.
        (function stripStaleTokenFromAddressBar() {{
            try {{
                const url = new URL(window.location.href);
                if (!url.searchParams.has('token') && !url.searchParams.has('access_token')) return;
                url.searchParams.delete('token');
                url.searchParams.delete('access_token');
                window.history.replaceState(null, '', url.pathname + url.search + url.hash);
            }} catch (e) {{}}
        }})();

        let currentThemeId = "{t['id']}";
        let currentDurationSec = 0;
        let lastLoadedMix = "";
        let isPlayerActive = false;
        let audioStreamConfigured = false;
        let activeAudioFile = "";
        let audioSeekInFlight = false;
        let lastAudioSyncAt = 0;
        let lastServerPos = -1.0;
        // The server's safe starting point for this file. Unlike lastServerPos
        // (its live, possibly drifted clock) this is where a phone that has not
        // started yet should seek, so a track left unattended does not begin at
        // its final frame and end the instant play() is called.
        let lastServerResume = 0.0;
        let audioStreamEngaged = false;
        // True once a real person started (or restarted) this page's stream,
        // as opposed to the automatic start that runs on page load. The
        // difference matters: an unattended run may retry a dead read, but it
        // must not permanently write off archive files on a transient share
        // failure, which is what advancing past an "unplayable" mix does.
        let listenerEngaged = false;
        // Set only by the Play/Stop button. A stall, a track change and a
        // reconnect must keep going unless the listener asked them to stop.
        let listenerPaused = false;
        // After Engage, polls follow this page's stream instead of the desk.
        let followStream = false;
        // DJ pitch the browser is actually playing, in percent. The pill and
        // the <audio> element both read this so a tap is audible immediately.
        let currentPitchPercent = 0;
        // Repeat mode the phone last heard from the server. Repeat One has to
        // restart the element itself: the file does not change, so the poll
        // never reloads a mix that has already ended.
        let currentRepeatMode = 'all';
        let scrubbing = false;
        // The level the phone is actually playing, and whether mute is on.
        // iOS ignores assignments to audio.volume, so these drive a GainNode.
        let currentVolumePercent = 100;
        let streamMuted = false;
        let mixGain = null;
        let adoptInFlight = false;
        let pendingTransport = null;
        let networkResumeAttempts = 0;
        let networkResumePending = false;
        let networkResumeTimer = 0;
        // Restart detection. The server reports a fresh instance id on every
        // launch; a change means the companion was stopped and started again.
        let serverInstance = "";
        // Flap guard. `active_file` changing is normally a track change, and the
        // element is re-pointed once. But a server that alternates A, B, A makes
        // that a reload loop that restarts playback on every poll. A genuine
        // change keeps naming the new file, so the change is only acted on once
        // the same new file has been reported on consecutive polls.
        const FILE_CHANGE_CONFIRMATIONS = 2;
        const FILE_FLAP_COOLDOWN_MS = 15000;
        let pendingFile = "";
        let pendingFileCount = 0;
        let secondLastFile = "";
        let fileFollowFrozenUntil = 0;
        let fileFlapDetected = false;
        let restartNoticeShown = false;
        // A restart notice is transient; a skip notice belongs to the run of
        // unplayable mixes and stays put until a mix really plays. Without the
        // distinction the 2 s status poll cleared the skip notice right after
        // it appeared, and the next failed file put it straight back: the top
        // of the page strobed in Chrome.
        let restartNoticeKind = "";
        let statusFailureCount = 0;
        // Native media errors (the server went away mid-stream) are recovered
        // by re-attaching to the fresh instance, not by a full page reload.
        // The count is what stops an endless retry: re-attach resets the
        // per-file flag, so a file the browser can never decode (a header-only
        // WAV, a truncated conversion) used to reload the same dead URL forever
        // and the "companion has been restarted" banner never went away. After
        // two failed recoveries for the same file the dashboard stops and says
        // what is wrong instead of flickering.
        let mediaRecoveryUsed = false;
        let lastMediaErrorAt = 0;
        let mediaRecoveryAttempts = 0;
        let mediaRecoveryFile = "";
        const MEDIA_RECOVERY_LIMIT = 2;
        // Auto-skip state. A media error means either "the service went away"
        // (recoverable by re-attaching) or "this file will never decode"
        // (only recoverable by moving on). These track the latter: which files
        // have been written off on this phone, and how many skips have happened
        // in a row. The run stops at the queue length, so a playlist in which
        // nothing works produces one message instead of an endless loop.
        let unplayableFiles = new Set();
        let consecutiveSkips = 0;
        let skipRunStartAt = 0;
        let exhaustedNoticeShown = false;
        // True once an unattended run has told the listener to tap. Repeats are
        // suppressed so a failing source cannot repaint the gate per file.
        let unattendedStopShown = false;
        // Set when a run stops for a reason that will not fix itself (every
        // entry failed, or the source share dropped mid-walk). Unlike
        // exhaustedNoticeShown this is not cleared by the next status poll: the
        // poll cannot know whether the share is back, so only a successful
        // play() or a deliberate re-engage clears it. This is what keeps the
        // page from resuming the skip walk two seconds later.
        let skipRunStopped = false;
        // Many different files failing within seconds is not a handful of
        // damaged mixes, it is the archive drive or share being unreachable.
        // Walking the rest of the queue would both write off hundreds of good
        // files and keep repainting the top banner, so the walk stops there.
        let skipBurstStartAt = 0;
        let skipBurstCount = 0;
        const SKIP_BURST_WINDOW_MS = 20000;
        const SKIP_BURST_LIMIT = 8;
        // Size of the current library queue, from the last status poll. It is
        // the bound on automatic skipping: a run may try every entry once, no
        // more, so a library where nothing decodes settles instead of looping.
        let queueLength = 0;
        // The host's own tally of files it has been told cannot be decoded. A
        // phone that has just loaded the page has seen no failures itself, so
        // this is what makes the "nothing plays" message count correctly.
        let serverUnplayableCount = 0;

        // ---------------------------------------------------------------------
        // Top tabs: MP Liquid Player <-> MP Assistant.
        //
        // Switching only hides one panel, so the <audio> element keeps its
        // source and playback position. Coming back never costs a re-engage
        // tap, which is the whole point of putting the two in one page.
        // ---------------------------------------------------------------------
        let activeTab = 'player';
        try {{
            const savedTab = window.localStorage.getItem('mp-companion-tab');
            if (savedTab === 'assistant' || savedTab === 'player') activeTab = savedTab;
        }} catch (e) {{}}

        function switchAppTab(tab) {{
            activeTab = (tab === 'assistant') ? 'assistant' : 'player';
            const playerPanel = document.getElementById('panelPlayer');
            const assistantPanel = document.getElementById('panelAssistant');
            const playerTab = document.getElementById('tabPlayer');
            const assistantTab = document.getElementById('tabAssistant');
            if (playerPanel) playerPanel.hidden = activeTab !== 'player';
            if (assistantPanel) assistantPanel.hidden = activeTab !== 'assistant';
            document.body.classList.toggle('assistant-tab-open', activeTab === 'assistant');
            if (playerTab) {{
                playerTab.classList.toggle('active', activeTab === 'player');
                playerTab.setAttribute('aria-selected', activeTab === 'player' ? 'true' : 'false');
            }}
            if (assistantTab) {{
                assistantTab.classList.toggle('active', activeTab === 'assistant');
                assistantTab.setAttribute('aria-selected', activeTab === 'assistant' ? 'true' : 'false');
                assistantTab.classList.remove('just-pinged');
            }}
            try {{ window.localStorage.setItem('mp-companion-tab', activeTab); }} catch (e) {{}}
            if (activeTab === 'assistant') {{
                fetchAssistantStatus();
                const prompt = document.getElementById('chatPrompt');
                // Focus only on devices with a real keyboard; on a phone the
                // focus would pop the soft keyboard over the chat log.
                if (prompt && window.matchMedia('(min-width: 720px)').matches) {{
                    try {{ prompt.focus(); }} catch (e) {{}}
                }}
            }}
            renderStreamOverlay();
        }}

        // ---------------------------------------------------------------------
        // MP Assistant: stream the agent reply, speak it with the chosen voice.
        // ---------------------------------------------------------------------
        let assistantOnline = false;
        let assistantBusy = false;
        let autoSpeak = true;
        let assistantVoice = 'stephen_hawking_matrix';
        let assistantAbort = null;
        let chatHistory = [];
        try {{
            const savedSpeak = window.localStorage.getItem('mp-assistant-autospeak');
            if (savedSpeak === '0') autoSpeak = false;
        }} catch (e) {{}}

        function paintAutoSpeak() {{
            const btn = document.getElementById('assistantSpeakBtn');
            if (!btn) return;
            btn.classList.toggle('off', !autoSpeak);
            btn.innerText = autoSpeak ? '🔊 Auto-speak: ON' : '🔇 Auto-speak: OFF';
            btn.setAttribute('aria-pressed', autoSpeak ? 'true' : 'false');
        }}

        function toggleAutoSpeak() {{
            autoSpeak = !autoSpeak;
            try {{ window.localStorage.setItem('mp-assistant-autospeak', autoSpeak ? '1' : '0'); }} catch (e) {{}}
            paintAutoSpeak();
            if (!autoSpeak) {{
                stopSpeech();
            }} else if (!assistantVoice) {{
                assistantVoice = 'stephen_hawking_matrix';
            }}
        }}

        function paintAssistantVoice(label) {{
            const pill = document.getElementById('assistantVoicePill');
            if (!pill) return;
            pill.innerText = '🔊 ' + (label || 'Stephen Hawking');
            pill.title = 'Type /voice in the prompt to change this voice';
        }}

        function setAssistantState(state, text) {{
            const box = document.getElementById('assistantState');
            const label = document.getElementById('assistantStateText');
            if (box) {{
                box.classList.remove('online', 'offline');
                if (state === 'online' || state === 'offline') box.classList.add(state);
            }}
            if (label) label.innerText = text;
        }}

        async function fetchAssistantStatus(force) {{
            try {{
                const res = await fetch('/api/assistant/status' + (force ? '?force=1' : ''));
                const data = await res.json();
                assistantOnline = Boolean(data.available);
                assistantVoice = data.voice || 'stephen_hawking_matrix';
                paintAssistantVoice(data.voice_label);
                if (assistantOnline) {{
                    setAssistantState('online', 'Online');
                    const model = document.getElementById('assistantModel');
                    if (model) {{
                        model.innerText = data.model
                            ? ('Model: ' + data.model)
                            : 'Local engineering assistant';
                    }}
                    setAssistantStartVisible(false);
                }} else {{
                    setAssistantState('offline', 'Offline');
                    setAssistantStartVisible(true);
                }}
                return data;
            }} catch (e) {{
                assistantOnline = false;
                setAssistantState('offline', 'Unreachable');
                setAssistantStartVisible(true);
                return null;
            }}
        }}

        function setAssistantStartVisible(visible) {{
            const btn = document.getElementById('assistantStartBtn');
            if (btn) btn.style.display = visible ? 'inline-flex' : 'none';
        }}

        async function startAssistantAgent() {{
            const btn = document.getElementById('assistantStartBtn');
            if (!btn) return;
            btn.disabled = true;
            setAssistantState('', 'Starting…');
            try {{
                const res = await fetch('/api/assistant/start', {{ method: 'POST' }});
                const data = await res.json();
                addChatSystem(data.detail || 'Starting the MP Harmony Agent…');
            }} catch (e) {{
                addChatSystem('Could not reach the companion service.');
            }} finally {{
                btn.disabled = false;
                // The model loads in the background; poll until the health
                // endpoint answers instead of leaving the button dead.
                let tries = 0;
                const timer = setInterval(async () => {{
                    tries += 1;
                    const data = await fetchAssistantStatus(true);
                    if ((data && data.available) || tries >= 20) {{
                        clearInterval(timer);
                        if (data && data.available) addChatSystem('MP Harmony Agent is online.');
                    }}
                }}, 3000);
            }}
        }}

        function clearChatEmpty() {{
            const empty = document.getElementById('chatEmpty');
            if (empty) empty.remove();
        }}

        function scrollChatToEnd() {{
            const log = document.getElementById('chatLog');
            if (log) log.scrollTop = log.scrollHeight;
        }}

        function addChatBubble(role, text) {{
            clearChatEmpty();
            const log = document.getElementById('chatLog');
            if (!log) return null;
            const bubble = document.createElement('div');
            bubble.className = 'chat-bubble ' + role;
            const body = document.createElement('div');
            body.className = 'bubble-text';
            body.textContent = text || '';
            bubble.appendChild(body);
            log.appendChild(bubble);
            scrollChatToEnd();
            return bubble;
        }}

        function addChatSystem(text) {{
            return addChatBubble('system', text);
        }}

        function attachSpeakControl(bubble, text, isUser) {{
            if (!bubble || isUser) return;
            const tools = document.createElement('div');
            tools.className = 'bubble-tools';
            const speak = document.createElement('button');
            speak.type = 'button';
            speak.innerText = '🔊 Speak';
            speak.title = 'Play this reply in the active MP Harmony voice';
            speak.onclick = () => speakText(text, bubble);
            const state = document.createElement('span');
            state.className = 'speak-state';
            tools.appendChild(speak);
            tools.appendChild(state);
            bubble.appendChild(tools);
        }}

        function stopSpeech() {{
            const audio = speechElement();
            if (audio) {{
                try {{
                    audio.pause();
                    audio.removeAttribute('src');
                    audio.load();
                }} catch (e) {{}}
                audio.__bubble = null;
                audio.__requestId = (audio.__requestId || 0) + 1;
                if (audio.__objectUrl) {{
                    try {{ URL.revokeObjectURL(audio.__objectUrl); }} catch (e) {{}}
                    audio.__objectUrl = null;
                }}
            }}
            document.querySelectorAll('.chat-bubble.speaking').forEach(el => {{
                el.classList.remove('speaking');
                const state = el.querySelector('.speak-state');
                if (state) state.innerText = '';
            }});
            restoreMixAfterSpeech();
        }}

        // The reply is spoken by the phone itself. The companion synthesizes
        // the WAV through the agent (or espeak-ng for Hawking) and streams it
        // back over the same origin, so no LAN port is exposed.
        //
        // A hidden <audio> element with playsinline is reused for every reply:
        // iOS refuses a fresh Audio() object that was not created inside the
        // original tap, but it happily re-points an element that already has
        // one successful play() behind it.
        function speechElement() {{
            return document.getElementById('assistantSpeech');
        }}

        // Duck the mix while a reply is spoken, then put the volume back, so
        // the Hawking voice is audible over a playing set without stopping it.
        let duckedMixVolume = null;

        function duckMixForSpeech() {{
            const mix = document.getElementById('mobileAudioStream');
            if (!mix || mix.paused || mix.ended) return;
            if (streamMuted || mixLoudness() <= 0) return;
            if (duckedMixVolume === null) duckedMixVolume = mixLoudness();
            setMixLoudness(Math.max(0.08, duckedMixVolume * 0.25));
        }}

        function restoreMixAfterSpeech() {{
            if (duckedMixVolume === null) return;
            const level = duckedMixVolume;
            duckedMixVolume = null;
            setMixLoudness(level);
        }}

        function speakText(text, bubble) {{
            const spoken = (text || '').trim();
            if (!spoken) return;
            stopSpeech();
            const audio = speechElement();
            if (!audio) return;
            if (bubble) {{
                bubble.classList.add('speaking');
                const state = bubble.querySelector('.speak-state');
                if (state) state.innerText = 'synthesizing…';
            }}
            // A POST keeps long replies out of the URL, and the blob URL lets
            // the element play the synthesized WAV as soon as it arrives.
            audio.__bubble = bubble || null;
            const requestId = (audio.__requestId || 0) + 1;
            audio.__requestId = requestId;
            fetch('/api/assistant/speech', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ text: spoken, voice: assistantVoice }}),
            }}).then(async (res) => {{
                if (!res.ok) {{
                    const err = await res.json().catch(() => ({{}}));
                    throw new Error((err && err.error) || 'Speech synthesis failed');
                }}
                return res.blob();
            }}).then((blob) => {{
                if (audio.__requestId !== requestId) return;
                const url = URL.createObjectURL(blob);
                if (audio.__objectUrl) {{
                    try {{ URL.revokeObjectURL(audio.__objectUrl); }} catch (e) {{}}
                }}
                audio.__objectUrl = url;
                audio.src = url;
                return audio.play();
            }}).catch((err) => {{
                if (audio.__requestId !== requestId) return;
                if (bubble) {{
                    bubble.classList.remove('speaking');
                    const state = bubble.querySelector('.speak-state');
                    if (state) state.innerText = '';
                }}
                addChatSystem(
                    (err && err.message) ? err.message :
                    'Speech synthesis failed on the host.'
                );
            }});
        }}

        function initSpeechElement() {{
            const audio = speechElement();
            if (!audio) return;
            audio.addEventListener('playing', () => {{
                const bubble = audio.__bubble;
                const state = bubble && bubble.querySelector('.speak-state');
                if (state) state.innerText = 'speaking';
                duckMixForSpeech();
            }});
            audio.addEventListener('ended', () => {{
                const bubble = audio.__bubble;
                if (bubble) bubble.classList.remove('speaking');
                audio.__bubble = null;
                restoreMixAfterSpeech();
            }});
            audio.addEventListener('error', () => {{
                const bubble = audio.__bubble;
                if (bubble) {{
                    bubble.classList.remove('speaking');
                    const state = bubble.querySelector('.speak-state');
                    if (state) state.innerText = '';
                }}
                audio.__bubble = null;
                restoreMixAfterSpeech();
            }});
        }}

        // A silent WAV played inside the user's tap unlocks the element for the
        // reply that arrives seconds later.
        //
        // It is built as a `blob:` URL rather than a `data:` one because the
        // page's own Content-Security-Policy allows `media-src 'self' blob:`
        // and nothing else. A `data:` clip is blocked by that policy, the
        // element reports MEDIA_ERR_SRC_NOT_SUPPORTED, and the media error
        // handler then blames the real mix for it. `blob:` is same-origin-ish
        // from the policy's point of view and plays normally.
        let silentWavUrl = "";

        function silentWavBlobUrl() {{
            if (silentWavUrl) return silentWavUrl;
            try {{
                // 0.2 s of silence: 48 kHz, 16-bit, mono.
                const frames = 9600;
                const bytes = new Uint8Array(44 + frames * 2);
                const view = new DataView(bytes.buffer);
                const write = (offset, text) => {{
                    for (let i = 0; i < text.length; i++) bytes[offset + i] = text.charCodeAt(i);
                }};
                write(0, 'RIFF');
                view.setUint32(4, 36 + frames * 2, true);
                write(8, 'WAVEfmt ');
                view.setUint32(16, 16, true);
                view.setUint16(20, 1, true);       // PCM
                view.setUint16(22, 1, true);       // mono
                view.setUint32(24, 48000, true);
                view.setUint32(28, 96000, true);
                view.setUint16(32, 2, true);
                view.setUint16(34, 16, true);
                write(36, 'data');
                view.setUint32(40, frames * 2, true);
                silentWavUrl = URL.createObjectURL(new Blob([bytes], {{ type: 'audio/wav' }}));
            }} catch (e) {{
                silentWavUrl = "";
            }}
            return silentWavUrl;
        }}

        // Mark the element as busy, so its `error` handler knows the source it
        // failed on was the throwaway unlock clip and not the listener's mix.
        function beginUnlock(audio) {{
            audio.__unlocking = true;
            audio.__unlockSrc = silentWavBlobUrl();
            return audio.__unlockSrc;
        }}

        function endUnlock(audio) {{
            audio.__unlocking = false;
        }}

        function unlockSpeechElement() {{
            const audio = speechElement();
            if (!audio || audio.__unlocked) return;
            try {{
                const src = beginUnlock(audio);
                if (!src) return;
                audio.__unlocked = true;
                audio.src = src;
                const p = audio.play();
                if (p && p.catch) p.catch(() => {{ endUnlock(audio); }});
            }} catch (e) {{
                endUnlock(audio);
            }}
        }}

        function assistantHistory() {{
            // Only the last few turns are sent, so a long phone chat cannot
            // overflow the agent's context window.
            return chatHistory.slice(-12);
        }}

        async function sendAssistantPrompt() {{
            if (assistantBusy) return;
            const input = document.getElementById('chatPrompt');
            if (!input) return;
            const text = (input.value || '').trim();
            if (!text) return;
            input.value = '';

            // A phone only allows audible playback from inside the tap that
            // started it. Unlocking the shared element here means every reply
            // after this can be spoken without another gesture.
            unlockSpeechElement();

            addChatBubble('user', text);
            chatHistory.push({{ role: 'user', content: text }});

            const sendBtn = document.getElementById('chatSendBtn');
            const stopBtn = document.getElementById('chatStopBtn');
            assistantBusy = true;
            if (sendBtn) sendBtn.disabled = true;
            if (stopBtn) stopBtn.classList.add('show');

            const replyBubble = addChatBubble('assistant', '');
            const replyBody = replyBubble && replyBubble.querySelector('.bubble-text');
            let replyText = '';
            let replyVoice = assistantVoice;
            let replyVoiceLabel = '';
            let isLocal = false;

            assistantAbort = (typeof AbortController !== 'undefined') ? new AbortController() : null;

            try {{
                const res = await fetch('/api/assistant/chat', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        messages: assistantHistory(),
                        voice: assistantVoice,
                    }}),
                    signal: assistantAbort ? assistantAbort.signal : undefined,
                }});
                const contentType = res.headers.get('Content-Type') || '';
                if (contentType.indexOf('text/event-stream') === -1) {{
                    // /voice and other local replies come back as plain JSON.
                    const data = await res.json();
                    replyText = data.content || '';
                    isLocal = Boolean(data.local);
                    replyVoice = data.voice || replyVoice;
                    replyVoiceLabel = data.voice_label || '';
                    if (replyBody) replyBody.textContent = replyText;
                    if (replyVoice) {{
                        assistantVoice = replyVoice;
                        paintAssistantVoice(replyVoiceLabel || undefined);
                    }}
                }} else {{
                    if (!res.body || !res.body.getReader) {{
                        throw new Error('This browser cannot stream replies.');
                    }}
                    const reader = res.body.getReader();
                    const decoder = new TextDecoder();
                    let buffered = '';
                    while (true) {{
                        const chunk = await reader.read();
                        if (chunk.done) break;
                        buffered += decoder.decode(chunk.value, {{ stream: true }});
                        const events = buffered.split('\\n\\n');
                        buffered = events.pop() || '';
                        for (const event of events) {{
                            const line = event.split('\\n').find(l => l.startsWith('data: '));
                            if (!line) continue;
                            const payload = line.slice(6).trim();
                            if (payload === '[DONE]') continue;
                            let obj;
                            try {{ obj = JSON.parse(payload); }} catch (e) {{ continue; }}
                            const type = obj.object || '';
                            if (type === 'mp.assistant.delta') {{
                                replyText += obj.content || '';
                                if (replyBody) replyBody.textContent = replyText;
                                scrollChatToEnd();
                            }} else if (type === 'mp.assistant.meta') {{
                                if (obj.voice) replyVoice = obj.voice;
                                replyVoiceLabel = obj.voice_label || '';
                                paintAssistantVoice(replyVoiceLabel || undefined);
                            }} else if (type === 'mp.assistant.error') {{
                                replyText += (replyText ? '\\n' : '') + (obj.message || 'Agent error');
                                if (replyBody) replyBody.textContent = replyText;
                            }}
                        }}
                    }}
                }}
            }} catch (e) {{
                if (e && e.name === 'AbortError') {{
                    replyText += (replyText ? '\\n' : '') + '[Stopped]';
                }} else {{
                    replyText += (replyText ? '\\n' : '')
                        + 'The MP Harmony Agent did not answer. Tap ▶ Start Agent, then send again.';
                }}
                if (replyBody) replyBody.textContent = replyText;
            }} finally {{
                assistantBusy = false;
                assistantAbort = null;
                if (sendBtn) sendBtn.disabled = false;
                if (stopBtn) stopBtn.classList.remove('show');
            }}

            const spoken = replyText.trim();
            if (spoken) {{
                chatHistory.push({{ role: 'assistant', content: spoken }});
                attachSpeakControl(replyBubble, spoken, false);
                if (autoSpeak) speakText(spoken, replyBubble);
                scrollChatToEnd();
                // A reply that lands while the player tab is showing gets a dot
                // on the assistant tab, so the user knows it is ready.
                if (activeTab !== 'assistant') {{
                    const tab = document.getElementById('tabAssistant');
                    if (tab) tab.classList.add('just-pinged');
                }}
            }} else if (replyBubble) {{
                replyBubble.remove();
            }}
            if (isLocal) fetchAssistantStatus(true);
        }}

        function stopAssistantReply() {{
            if (assistantAbort) {{
                try {{ assistantAbort.abort(); }} catch (e) {{}}
            }}
            // The agent's own barge-in endpoint stops generation server-side.
            fetch('/api/assistant/abort', {{ method: 'POST' }}).catch(() => {{}});
            stopSpeech();
        }}

        function audioElIsPlaying() {{
            const audioEl = document.getElementById('mobileAudioStream');
            return Boolean(audioEl && !audioEl.paused && !audioEl.ended);
        }}

        // The transport button names the next action, from the playback that
        // is really happening. Playing reads "Pause"; paused or stopped reads
        // "Play".
        //
        // It used to be painted from the server's `state`, which is not the
        // same thing: a phone interruption, a headphone unplug or a locked
        // screen pauses the <audio> element without telling the companion, so
        // the poll kept answering "playing" and the button stayed on "Pause"
        // while the room was silent. Driving the label from media events
        // makes it flip on the tap itself, and keeps the host-player case
        // working, where the phone element is idle and only the host knows.
        function paintPlayPauseButton(playing) {{
            const btn = document.getElementById('btnPlayPause');
            if (!btn) return;
            if (playing) {{
                btn.innerText = '⏸ Pause';
                btn.classList.add('is-playing');
                btn.setAttribute('aria-label', 'Playing - tap to pause');
            }} else {{
                btn.innerText = '▶ Play';
                btn.classList.remove('is-playing');
                btn.setAttribute('aria-label', 'Paused - tap to play');
            }}
        }}

        // True when this page, not the desktop player, is what the listener hears.
        //
        // Once Engage (or desktop autoplay) has started the element, Play,
        // Stop, Next, Previous and the 30s skips belong to that element. A
        // live desk player used to steal every one of those taps, so the room
        // changed — or nothing did — while the browser mix kept going. The
        // desk is the remote-control target only while this page is still silent.
        function phoneOwnsPlayback() {{
            if (audioStreamEngaged && activeAudioFile) return true;
            return !hostPlayerActive && Boolean(activeAudioFile);
        }}

        // Newest `state` the daemon reported. Media events repaint from this
        // while a desktop player owns the audio, so a phone-side `pause` on the
        // idle mirror element cannot flip the icon away from the real mix.
        let lastServerState = 'stopped';

        function syncPlayPauseButton(serverState) {{
            if (serverState !== undefined) lastServerState = String(serverState || '').toLowerCase();
            if (hostPlayerActive && !audioStreamEngaged) {{
                paintPlayPauseButton(lastServerState === 'playing');
                return;
            }}
            // Hearing audio again means the reconnect worked. A later stall
            // gets a fresh budget instead of the leftover count from the drop.
            if (audioElIsPlaying()) {{
                networkResumeAttempts = 0;
                networkResumePending = false;
            }}
            paintPlayPauseButton(audioElIsPlaying());
        }}

        // The host's clock is only a fallback: it cannot hear the mix and it
        // free-runs while the service sits idle. Once the phone is actually
        // playing, send it the true position every few seconds so a later
        // listener resumes where this one left off instead of at a drifted
        // point near the end of the file.
        let lastPositionReportAt = 0;

        // How long the element has sat on the same timestamp while it claims
        // to be playing. A stalled read from the archive looks like this
        // before the browser finally raises a media error and goes silent.
        let lastAdvanceAt = 0;
        let lastAdvanceTime = -1;

        function reportPlaybackPosition() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !audioStreamEngaged || !audioElIsPlaying()) return;
            if (!isFinite(audioEl.currentTime) || audioEl.currentTime <= 0) return;
            const now = performance.now();
            if (Math.abs(audioEl.currentTime - lastAdvanceTime) > 0.2) {{
                lastAdvanceTime = audioEl.currentTime;
                lastAdvanceAt = now;
            }} else if (audioEl.readyState < 3 && lastAdvanceAt
                        && (now - lastAdvanceAt) > 8000) {{
                // Not paused, not buffered, and the clock has not moved.
                // Reconnect the same mix instead of waiting for the element
                // to error out and stay stopped.
                lastAdvanceAt = now;
                resumeAfterNetworkDrop();
            }}
            if (now - lastPositionReportAt < 4000) return;
            lastPositionReportAt = now;
            // target=stream: a desk player must not swallow the browser's
            // clock. Without it the session stays at 0 and the next seek
            // lands at the start of the mix.
            fetch('/api/control?action=report_position&target=stream&position=' +
                  encodeURIComponent(audioEl.currentTime.toFixed(1)), {{ method: 'POST' }})
                .catch(() => {{}});
        }}

        // Seek the phone's element only once per request and clear the guard
        // when the element reports it actually moved. Without this the 100 ms
        // poll can re-issue the same seek while the previous one is settling.
        // A seek requested before the stream has a timeline (metadata not yet
        // loaded) is parked here and applied once the duration is known.
        // Stacking matters: two taps of +30 s before load must land at +60 s,
        // not each start again from a currentTime of 0.
        let pendingSeek = null;

        document.addEventListener('DOMContentLoaded', function () {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl) {{
                audioEl.addEventListener('loadedmetadata', applyPendingSeek);
            }}
        }});

        function applyPendingSeek() {{
            if (pendingSeek === null) return;
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl) return;
            const dur = Number(audioEl.duration);
            if (!isFinite(dur) || dur <= 0) return;
            let target = pendingSeek;
            pendingSeek = null;
            if (target < 0) target = 0;
            if (target > dur) target = dur - 0.25;
            seekAudioTo(target);
        }}

        function seekAudioTo(pos) {{
            seekAudioToWithResume(pos, false);
        }}

        // `duration` is the honest length once the browser has probed the
        // file, but a stream it has not finished measuring reports Infinity.
        // Relying on duration alone sent those skips down the "no timeline
        // yet" path forever, so the buttons moved nothing while the mix
        // played. The seekable range is the fallback.
        function audioTimelineEnd(audioEl) {{
            const dur = Number(audioEl && audioEl.duration);
            if (isFinite(dur) && dur > 0) return dur;
            try {{
                if (audioEl && audioEl.seekable && audioEl.seekable.length) {{
                    const end = Number(audioEl.seekable.end(audioEl.seekable.length - 1));
                    if (isFinite(end) && end > 0) return end;
                }}
            }} catch (e) {{}}
            return 0;
        }}

        function skipBy(delta, opts) {{
            const options = opts || {{}};
            clearHostAlign();
            const audioEl = document.getElementById('mobileAudioStream');
            const dur = audioTimelineEnd(audioEl);
            const configured = dur > 0;
            let target = null;
            if (!configured) {{
                // No timeline yet: stack on the parked offset so two quick
                // presses add up, then land it once metadata arrives.
                let base;
                if (pendingSeek === null) {{
                    base = Number(audioEl && audioEl.currentTime || 0);
                }} else if (pendingSeek !== null) {{
                    base = pendingSeek;
                }}
                pendingSeek = Math.max(0, base + delta);
                applyPendingSeek();
                // A mix that is already audible can be moved before the
                // browser admits a duration. Parking the offset and waiting
                // for metadata left the 30s buttons silent for the whole file.
                if (phoneOwnsPlayback() && audioEl && isFinite(audioEl.currentTime)) {{
                    try {{ audioEl.currentTime = pendingSeek; }} catch (e) {{}}
                }}
            }} else {{
                target = Number(audioEl && audioEl.currentTime || 0) + delta;
                if (target < 0) target = 0;
                if (dur > 0 && target > dur) target = dur - 0.25;
            }}
            if (target !== null) {{
                if (phoneOwnsPlayback()) {{
                    seekAudioTo(target);
                }} else if (audioEl && audioEl.readyState > 0) {{
                    // The desktop player owns the sound, so this only keeps the
                    // idle phone element aligned for the opt-in listen. Moving
                    // it is still correct; it is just not what is audible.
                    try {{ audioEl.currentTime = target; }} catch (e) {{}}
                }}
            }}
            // Always tell the server as well. The engaged page sends the
            // absolute position: a relative +30 against a session clock that
            // is still at 0 jumps the mix back to the start. A silent mirror
            // still sends the relative seek, which is what moves the desk.
            if (phoneOwnsPlayback()) {{
                const told = target !== null ? target : pendingSeek;
                sendAction('seek_to', {{ position: Number(told || 0).toFixed(1) }});
            }} else if (options.relativeSeconds) {{
                // Keyboard seeking has to move the desk by the key's own step.
                // The two named actions are fixed at 30 s inside the server, so
                // a 10 s arrow sent as seek_forward moved the room 30 s while
                // the browser mirror moved 10 - the two disagreed on every
                // press. `seek` carries the distance with it.
                sendAction('seek', {{ seconds: delta }});
            }} else {{
                sendAction(delta > 0 ? 'seek_forward' : 'seek_backward');
            }}
        }}

        // Left/Right seek, the same contract the desktop player already has.
        //
        // The transport row was pointer-only: on a laptop, a keyboard case or
        // a TV browser the arrow keys moved the page nowhere and the mix
        // stayed where it was. The keys go through skipBy on purpose, so they
        // inherit its timeline probe, its clamped targets, its parked offset
        // before metadata and its forward to a desk player that owns the
        // sound - pressing Left must never leave the two out of step.
        //
        // Ten seconds is the player's own arrow step. Shift makes it the 30
        // seconds the two transport buttons use, which is the longer jump
        // when the listener is hunting for a spot in an hour-long mix.
        const KEY_SEEK_STEP_SECONDS = 10;
        const KEY_SEEK_STEP_SHIFT_SECONDS = 30;

        // Holding the key must scrub, but not issue sixty seeks a second. The
        // OS repeat is throttled to a rate a browser can actually serve; the
        // first press is never delayed.
        const KEY_SEEK_REPEAT_MS = 150;
        let lastKeySeekAt = -Infinity;

        // A keystroke means text here. The playlist search and the assistant
        // prompt are both real text fields, and a caret move inside them must
        // not skip the mix.
        function isSeekBlockedTarget(target) {{
            if (!target) return false;
            if (target.isContentEditable) return true;
            const tag = String(target.tagName || '').toLowerCase();
            return tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'option';
        }}

        function handleSeekKeydown(ev) {{
            if (!ev || ev.defaultPrevented) return;
            const key = ev.key;
            if (key !== 'ArrowLeft' && key !== 'ArrowRight') return;
            // A modified arrow belongs to the browser or the OS: Ctrl+Left is
            // a word jump in text, Cmd+Left is the start of a line, Alt+Left
            // is Back. Only a bare arrow seeks.
            if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
            if (isSeekBlockedTarget(ev.target)) return;
            // The assistant tab has no transport on screen, and the engage
            // gate is asking for a tap rather than for a seek.
            if (activeTab !== 'player') return;
            if (streamOverlayWanted) return;
            // Nothing is queued: leave the arrows to the browser so the page
            // still scrolls on a laptop instead of swallowing the keystroke.
            if (!activeAudioFile) return;
            const now = performance.now();
            if (ev.repeat && (now - lastKeySeekAt) < KEY_SEEK_REPEAT_MS) {{
                ev.preventDefault();
                return;
            }}
            lastKeySeekAt = now;
            const step = ev.shiftKey ? KEY_SEEK_STEP_SHIFT_SECONDS : KEY_SEEK_STEP_SECONDS;
            ev.preventDefault();
            skipBy(key === 'ArrowRight' ? step : -step, {{ relativeSeconds: true }});
        }}

        // Two-argument form kept internal; the single documented entry point
        // is seekAudioTo(pos). Resume callers pass `true` through the helper
        // below so a stale server position can never silence the player.
        function seekAudioToWithResume(pos, fromResume) {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !isFinite(pos) || pos < 0) return;
            // A resume that lands on the last frames ends the media the moment
            // playback starts. Treat that tail as "start from the top" so a
            // stale server position can never silence the player. A deliberate
            // seek from the progress bar is left alone - the listener asked for
            // that spot.
            if (fromResume) {{
                const dur = Number(audioEl.duration);
                if (isFinite(dur) && dur > 0) {{
                    const margin = Math.max(2.0, dur * 0.02);
                    if (pos >= dur - margin) pos = 0;
                }}
            }}
            if (audioSeekInFlight && Math.abs(audioEl.currentTime - pos) < 0.5) return;
            try {{
                audioSeekInFlight = true;
                audioEl.currentTime = pos;
                lastAudioSyncAt = performance.now();
                setTimeout(() => {{ audioSeekInFlight = false; }}, 1500);
            }} catch (e) {{
                audioSeekInFlight = false;
            }}
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            syncRainbowDrive();
            // The gate is shown in the initial markup, before any script runs.
            // Apply the no-scroll state now so the first paint is already
            // locked and the engage button is centred.
            renderStreamOverlay();
            const vis = document.getElementById('visContainer');
            if (vis) {{
                vis.addEventListener('keydown', (ev) => {{
                    if (ev.key === 'Enter' || ev.key === ' ') toggleVisualizerMode(ev);
                }});
            }}
            // Document-level, so the arrow keys work without the listener
            // first having to click the player. The handler ignores text
            // fields, modified arrows and the assistant tab on its own.
            document.addEventListener('keydown', handleSeekKeydown);
            try {{
                if (localStorage.getItem('mpVizMode') === 'spectrogram') {{
                    applyVisualizerMode('spectrogram');
                }}
            }} catch (e) {{}}
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl) {{
                // The icon follows the media element, not the poll. These fire
                // on the tap itself, so the button never sits on the wrong
                // glyph for up to two seconds while the status request lands.
                audioEl.addEventListener('play', () => syncPlayPauseButton());
                audioEl.addEventListener('pause', () => syncPlayPauseButton());
                audioEl.addEventListener('playing', () => syncPlayPauseButton());
                // Lock-screen / Control Center controls, plus the iOS audio
                // session category that keeps this page audible while the app
                // is in the background. Both follow the element's own state.
                installMediaSessionActions();
                audioEl.addEventListener('play', () => {{
                    claimPlaybackAudioSession();
                    updateMediaSessionMetadata();
                    updateMediaSessionState(true);
                    if (audioCtx && audioCtx.state === 'suspended') {{
                        try {{ audioCtx.resume(); }} catch (e) {{}}
                    }}
                }});
                audioEl.addEventListener('pause', () => updateMediaSessionState(false));
                audioEl.addEventListener('timeupdate', () => {{
                    if (!scrubbing) paintLocalProgress(audioEl);
                    if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
                    const dur = isFinite(audioEl.duration) ? audioEl.duration : 0;
                    if (dur <= 0) return;
                    try {{
                        navigator.mediaSession.setPositionState({{
                            duration: dur,
                            position: Math.min(audioEl.currentTime, dur),
                            playbackRate: audioEl.playbackRate || 1,
                        }});
                    }} catch (e) {{}}
                }});
                audioEl.addEventListener('playing', () => {{
                    if (audioCtx) ensureAudioGraph();
                    // The first playing event often still sits at 0: the seek
                    // was requested before the stream could move. Land on the
                    // desk player's position, then stop correcting.
                    if (!hostAlignTarget || hostAlignTries >= 5) return;
                    if (!isFinite(audioEl.currentTime)) return;
                    if (Math.abs(audioEl.currentTime - hostAlignTarget) <= 1.5) {{
                        hostAlignTarget = 0;
                        return;
                    }}
                    hostAlignTries += 1;
                    try {{ audioEl.currentTime = hostAlignTarget; }} catch (e) {{}}
                }});
                audioEl.addEventListener('ended', () => syncPlayPauseButton());
                audioEl.addEventListener('seeked', () => {{ audioSeekInFlight = false; }});
                // The phone decides when a mix ends, so tell the server which
                // file to serve next instead of waiting for a local player.
                audioEl.addEventListener('ended', () => {{
                    // An end within a couple of seconds of engaging is not a
                    // finished mix, it is a resume point that landed on the
                    // final frame. Restart the same mix instead of advancing,
                    // otherwise the tap looks dead and the queue skips a track.
                    const sinceEngage = performance.now() - lastEngageAt;
                    if (audioStreamEngaged && lastEngageAt > 0 && sinceEngage < 2500) {{
                        restartStreamFromTop();
                        return;
                    }}
                    // Repeat One stays on this file, so the status poll will
                    // not load it again. Start it here, inside the ended
                    // event, or the mix stops while the badge still says ONE.
                    if (phoneOwnsPlayback() && currentRepeatMode === 'one') {{
                        sendAction('track_ended');
                        try {{
                            listenerPaused = false;
                            audioEl.currentTime = 0;
                            const started = audioEl.play();
                            if (started && started.catch) started.catch(() => {{}});
                        }} catch (e) {{}}
                        return;
                    }}
                    sendAction('track_ended');
                }});
                // The tray stopped the service while this mix was playing, OR
                // the browser cannot decode the file the queue handed it.
                //
                // These are two different problems and they used to share one
                // handler, which is why a damaged file produced the "companion
                // has been restarted" banner and a re-attach to the same dead
                // URL. Now:
                //
                //   * `MEDIA_ERR_SRC_NOT_SUPPORTED`/`MEDIA_ERR_DECODE` from a
                //     source that never produced a single `playing` event means
                //     this file cannot be decoded here, so the phone reports it
                //     to the host (`track_failed`) and the queue advances to the
                //     next mix - repeatedly, until something plays or the queue
                //     is exhausted.
                //   * anything else is a dropped connection (the archive
                //     stalled, the browser aborted a seek). Resume the same
                //     mix. Tearing the element down here is what made the page
                //     go silent and stay silent: the poll reloaded the file
                //     and left it paused.
                audioEl.addEventListener('error', () => {{
                    // The element is still holding the throwaway silence clip
                    // (its `src` is replaced with the real stream immediately
                    // after). That error says nothing about the listener's mix,
                    // so treating it as one writes off a perfectly good track
                    // - the tap then looks dead. Drop it and let the engage
                    // proceed.
                    if (audioEl.__unlocking || (audioEl.src || '').indexOf('blob:') === 0) {{
                        endUnlock(audioEl);
                        return;
                    }}
                    if (!activeAudioFile) return;
                    const code = audioEl.error ? audioEl.error.code : 0;
                    // A seek or a src swap aborts the previous fetch. That is
                    // not a drop, and reconnecting here would throw away the
                    // 30s skip the listener just made.
                    if (code === 1 /* MEDIA_ERR_ABORTED */) return;
                    // A network failure is not a damaged file. The archive is on
                    // a mounted share, so a read that dies mid-body arrives as
                    // MEDIA_ERR_NETWORK or as a short body, and both say
                    // "the connection went away", not "this mix is broken".
                    // Only a decode/source refusal is proof about the file.
                    if (code === 2 /* MEDIA_ERR_NETWORK */) {{
                        resumeAfterNetworkDrop();
                        return;
                    }}
                    // Nothing here is a listener decision: this page started the
                    // stream by itself. Writing files off from that unattended
                    // run would permanently skip good mixes because a share
                    // hiccuped for a second. So an automatic start may retry and
                    // report, but only a stream a person engaged may advance the
                    // queue past a file.
                    const automaticRun = audioStreamEngaged && !listenerEngaged;
                    if (automaticRun) {{
                        resumeAfterNetworkDrop();
                        return;
                    }}
                    const decodeFailure =
                        code === 3 /* MEDIA_ERR_DECODE */ ||
                        code === 4 /* MEDIA_ERR_SRC_NOT_SUPPORTED */;
                    if (decodeFailure || unplayableFiles.has(activeAudioFile)) {{
                        handleUnplayableMix(activeAudioFile);
                        return;
                    }}
                    resumeAfterNetworkDrop();
                }});
            }}
            bindScrubber();
            restoreSleep();
            loadThemeList();
            fetchMiniPlaylist();
            paintAutoSpeak();
            initSpeechElement();
            switchAppTab(activeTab);
            fetchAssistantStatus();
            const prompt = document.getElementById('chatPrompt');
            if (prompt) {{
                // Enter sends, Shift+Enter adds a line — the same convention as
                // the agent's own Web HUD chat box.
                prompt.addEventListener('keydown', (e) => {{
                    if (e.key === 'Enter' && !e.shiftKey) {{
                        e.preventDefault();
                        sendAssistantPrompt();
                    }}
                }});
            }}
        }});

        function applyPalette(th) {{
            if (!th || !th.id) return;
            currentThemeId = th.id;
            const nameEl = document.getElementById('themeNameText');
            if (nameEl) nameEl.innerText = th.name;
            const r = document.querySelector(':root');
            r.style.setProperty('--bg', th.bg);
            r.style.setProperty('--card-bg', th.card_bg);
            r.style.setProperty('--card-border', th.card_border);
            r.style.setProperty('--card-shadow', th.card_shadow);
            r.style.setProperty('--text-main', th.text_main);
            r.style.setProperty('--text-dim', th.text_dim);
            r.style.setProperty('--accent-primary', th.accent_primary);
            r.style.setProperty('--accent-secondary', th.accent_secondary);
            r.style.setProperty('--accent-tertiary', th.accent_tertiary);
            r.style.setProperty('--accent-crimson', th.accent_crimson);
            r.style.setProperty('--header-gradient', th.header_gradient);
            r.style.setProperty('--btn-gradient', th.btn_gradient);
            r.style.setProperty('--btn-shadow', th.btn_shadow);
            r.style.setProperty('--progress-fill', th.progress_fill);
            r.style.setProperty('--badge-bg', th.badge_bg);
            r.style.setProperty('--badge-color', th.badge_color);
            r.style.setProperty('--eq-bar', th.eq_bar);
            r.style.setProperty('--eq-peak', th.eq_peak);
            syncRainbowDrive();
        }}

        // One hue for the whole theme. It used to be stepped from JavaScript
        // four times a second, and that timer was the last cause of the page
        // being reloaded on an iPhone.
        //
        // Why: every tick rewrote --rainbow-hue on the document element, and 27
        // custom properties (borders, badges, buttons, the wash gradients) are
        // derived from it, so each write was a whole-document style
        // recalculation. WebKit did not reclaim what the restyle allocated.
        // Measured in WebKitGTK at 430x932 the old 250 ms tick grew the
        // WebContent process 0.81 MB/s and burned 4.8% of a core while idle;
        // iOS ends a WebContent process that grows like that while it is also
        // decoding FLAC, and Safari then silently reloads the page - the
        // "snaps back to the top and restarts the mix" that was reported.
        //
        // The colour motion now comes from a CSS hue-rotate animation on the
        // wash layer, which the compositor runs without touching style. Same
        // 24 s turn, 0.01 MB/s, 0.4% of a core. The chrome keeps the violet
        // palette below and stays steady, which is also easier on the eye.
        let rainbowTimer = 0;

        function rainbowShouldRun() {{
            return currentThemeId === 'dynamic_rainbow_liquid_glass'
                && !document.hidden
                && !document.body.classList.contains('dynamic-rainbow-glass-frozen');
        }}

        function tickRainbow() {{
            // Kept as the single place the theme's chrome colour is applied.
            // It is deliberately not on a timer any more: one write per theme
            // change is free, several per second is a leak.
            if (currentThemeId !== 'dynamic_rainbow_liquid_glass') return;
            document.documentElement.style.setProperty('--rainbow-hue', '272');
        }}

        function stopRainbowDrive() {{
            // The animation is CSS now, so there is no interval to clear. The
            // callers (a hidden tab, a theme switch, Reduce Motion) still mean
            // something: the body class they toggle is what pauses it.
            rainbowTimer = 0;
        }}

        function syncRainbowDrive() {{
            document.body.classList.toggle(
                'dynamic-rainbow-glass-active',
                currentThemeId === 'dynamic_rainbow_liquid_glass'
            );
            // No interval is started. The hue motion is the CSS animation on
            // the wash (see dynamicRainbowSpin); this only has to make sure the
            // chrome palette is applied once per theme change. See the note on
            // rainbowTimer for the measurement that removed the timer.
            if (rainbowShouldRun()) {{
                tickRainbow();
            }} else {{
                stopRainbowDrive();
            }}
        }}

        async function loadThemeList() {{
            try {{
                const res = await fetch('/api/themes');
                const data = await res.json();
                const menu = document.getElementById('themeMenu');
                if (!menu || !data.themes) return;
                const active = data.current || currentThemeId;
                menu.innerHTML = '';
                data.themes.forEach(th => {{
                    const opt = document.createElement('button');
                    opt.type = 'button';
                    opt.className = 'theme-option' + (th.id === active ? ' active' : '');
                    opt.setAttribute('role', 'menuitem');
                    opt.dataset.themeId = th.id;

                    const label = document.createElement('span');
                    label.innerText = th.name;
                    const check = document.createElement('span');
                    check.className = 'theme-check';
                    check.innerText = th.id === active ? '✓ Active' : '';

                    opt.appendChild(label);
                    opt.appendChild(check);
                    opt.onclick = () => changeTheme(th.id);
                    menu.appendChild(opt);
                }});
            }} catch (e) {{}}
        }}

        function toggleThemeMenu() {{
            const menu = document.getElementById('themeMenu');
            const btn = document.getElementById('themeBtn');
            if (!menu) return;
            const show = !menu.classList.contains('show');
            menu.classList.toggle('show', show);
            if (btn) {{
                btn.classList.toggle('open', show);
                btn.setAttribute('aria-expanded', show ? 'true' : 'false');
            }}
        }}

        function closeThemeMenu() {{
            const menu = document.getElementById('themeMenu');
            const btn = document.getElementById('themeBtn');
            if (menu) menu.classList.remove('show');
            if (btn) {{
                btn.classList.remove('open');
                btn.setAttribute('aria-expanded', 'false');
            }}
        }}

        document.addEventListener('click', (e) => {{
            const menu = document.getElementById('themeMenu');
            const btn = document.getElementById('themeBtn');
            if (!menu || !menu.classList.contains('show')) return;
            if (menu.contains(e.target) || (btn && btn.contains(e.target))) return;
            closeThemeMenu();
        }});

        async function changeTheme(themeId) {{
            if (!themeId) return;
            closeThemeMenu();
            try {{
                const res = await fetch('/api/set_theme?theme=' + encodeURIComponent(themeId));
                const data = await res.json();
                if (data && data.success && data.palette) {{
                    applyPalette(data.palette);
                    loadThemeList();
                }} else {{
                    loadThemeList();
                }}
            }} catch (e) {{
                loadThemeList();
            }}
        }}

        function renderMiniPlaylist(data) {{
            const wrap = document.getElementById('miniPlItems');
            if (!wrap) return;
            const items = (data && data.items) || [];
            const total = Number(data && data.total) || 0;
            const matched = (data && data.matched !== undefined && data.matched !== null)
                ? Number(data.matched) : total;
            const query = (data && data.query) ? String(data.query) : '';
            const show = Math.max(1, Number(data && data.show) || 5);
            // Music asked for but the archive is being streamed instead: the
            // rows below are mixes, so they must not be presented as tracks
            // from the selected library.
            const fellBack = Boolean(data && data.library_fallback);

            const countEl = document.getElementById('miniPlCount');
            if (countEl) {{
                if (query) {{
                    // The badge counts matches in this playlist, not the whole
                    // library, so a search that found three tracks says so.
                    const noun = matched === 1 ? ' match' : ' matches';
                    countEl.innerText = (matched > items.length)
                        ? (items.length + ' of ' + matched + noun)
                        : (matched + noun);
                    countEl.title = items.length + ' shown of ' + matched +
                        ' matches in ' + total + ' tracks';
                }} else {{
                    // Track count for the whole selected library, not just the
                    // rows that fit on a phone screen.
                    countEl.innerText = total + (total === 1 ? ' track' : ' tracks') +
                        (fellBack ? ' (from Mix Archive)' : '');
                    countEl.title = Math.min(items.length, show) + ' shown of ' + total +
                                    ' in this library · scroll for more';
                }}
            }}

            const titleEl = document.getElementById('miniPlTitle');
            if (titleEl) {{
                const label = fellBack ? 'Mix Archive (Music unavailable)'
                                       : ((data && data.mode_label) || 'Library');
                titleEl.innerText = (query ? '🔍 Search · ' : '🎵 Up Next · ') + label;
            }}

            // The offline notice sits above the list, so five rows always fit.
            const noteEl = document.getElementById('miniPlNote');
            if (noteEl) {{
                noteEl.innerHTML = '';
                if (data && data.library_detail) {{
                    // A warning when the mode fell back, a plain hint otherwise
                    // (an empty Favorites playlist is normal, not an error).
                    const note = document.createElement('div');
                    note.className = 'mini-pl-empty';
                    if (data.library_fallback) {{
                        note.style.borderColor = '#ffaa00';
                        note.style.color = '#ffaa00';
                    }}
                    note.innerText = (data.library_fallback ? '⚠ ' : '') + data.library_detail;
                    noteEl.appendChild(note);
                }}
            }}

            // Keep the scroll position across the 15-second refresh instead of
            // snapping back to the top while the user is browsing. A new
            // search string starts at the first match.
            const queryChanged = query !== renderedPlaylistQuery;
            renderedPlaylistQuery = query;
            const scrollTop = queryChanged ? 0 : wrap.scrollTop;
            wrap.innerHTML = '';

            if (!items.length) {{
                const empty = document.createElement('div');
                empty.className = 'mini-pl-empty';
                if (query) {{
                    empty.innerText = 'No tracks match "' + query + '"';
                }} else {{
                    empty.innerText = (data && data.mode === 'FAVORITES')
                        ? 'No favourites yet — tap ♡ on a mix to save it here'
                        : 'No audio found in this library';
                }}
                wrap.appendChild(empty);
                return;
            }}

            items.forEach((item, i) => {{
                const row = document.createElement('div');
                row.className = 'mini-pl-item' + (item.current ? ' current' : '') +
                    (item.unplayable ? ' unplayable' : '');
                row.onclick = () => playPlaylistTrack(item.index, item.file);

                const num = document.createElement('div');
                num.className = 'mini-pl-num';
                num.innerText = item.current ? '▶' : (item.unplayable ? '⚠' : String(i + 1));

                const body = document.createElement('div');
                body.className = 'mini-pl-body';
                const title = document.createElement('div');
                title.className = 'mini-pl-title';
                title.innerText = item.title || 'Untitled Track';
                const dur = document.createElement('div');
                dur.className = 'mini-pl-dur';
                dur.innerText = item.unplayable
                    ? 'Cannot be played on this device — skipped'
                    : (item.artist || '') + (item.duration ? ' • ' + item.duration : '');
                body.appendChild(title);
                body.appendChild(dur);

                const heart = document.createElement('button');
                heart.type = 'button';
                heart.className = 'mini-pl-fav' + (item.favorite ? ' on' : '');
                heart.innerText = item.favorite ? '♥' : '♡';
                heart.setAttribute('aria-label', item.favorite
                    ? 'Remove from favourites' : 'Add to favourites');
                heart.onclick = (ev) => {{
                    ev.stopPropagation();
                    ev.preventDefault();
                    toggleFavorite(item.file);
                }};

                row.appendChild(num);
                row.appendChild(body);
                row.appendChild(heart);
                wrap.appendChild(row);
            }});
            wrap.scrollTop = scrollTop;
        }}

        function updateModeSwitch(mode) {{
            document.querySelectorAll('#modeSwitch button').forEach(btn => {{
                btn.classList.toggle('active', btn.dataset.mode === mode);
            }});
        }}

        // The mode request scans a multi-thousand-entry library, so it can take
        // a few seconds. A single token makes sure only the newest tap decides
        // the highlighted mode and the playlist that is finally shown.
        let modeRequestToken = 0;
        let modeSwitchBusy = false;

        function setModeBusy(busy, pendingMode) {{
            modeSwitchBusy = busy;
            const group = document.getElementById('modeSwitch');
            if (group) group.classList.toggle('busy', busy);
            document.querySelectorAll('#modeSwitch button').forEach(btn => {{
                btn.disabled = busy;
                btn.classList.toggle('pending', busy && btn.dataset.mode === pendingMode);
            }});
            const titleEl = document.getElementById('miniPlTitle');
            if (!titleEl || !busy) return;
            const labels = {{ MIX_ARCHIVE: 'Mix Archive', MUSIC: 'Music', FAVORITES: 'Favourites' }};
            titleEl.innerText = '⏳ Switching to ' + (labels[pendingMode] || 'library') + '…';
        }}

        async function switchLibraryMode(mode) {{
            if (!mode) return;
            const token = ++modeRequestToken;
            updateModeSwitch(mode);
            setModeBusy(true, mode);
            try {{
                const res = await fetch('/api/mode?mode=' + encodeURIComponent(mode));
                const data = await res.json();
                if (token !== modeRequestToken) return;
                if (data && data.success) {{
                    updateModeSwitch(data.mode);
                    // fetchMiniPlaylist reports the offline notice itself, so no
                    // placeholder render here (it would flash "No audio").
                    await fetchMiniPlaylist();
                    await fetchStatus();
                }} else {{
                    fetchMiniPlaylist();
                }}
            }} catch (e) {{
                if (token === modeRequestToken) fetchMiniPlaylist();
            }} finally {{
                if (token === modeRequestToken) setModeBusy(false, null);
            }}
        }}

        // The search box is the filter. The poll keeps sending whatever is
        // typed so a library switch or a 15-second refresh does not put the
        // unfiltered queue back over the matches.
        let playlistSearchTimer = 0;
        let playlistFetchToken = 0;
        let renderedPlaylistQuery = '';

        function currentPlaylistQuery() {{
            const box = document.getElementById('miniPlSearch');
            return box ? String(box.value || '') : '';
        }}

        function onPlaylistSearchInput() {{
            clearTimeout(playlistSearchTimer);
            playlistSearchTimer = setTimeout(fetchMiniPlaylist, 160);
        }}

        // A row tap plays that queue entry. Search hits carry the original
        // index, so the file that matched is the file that starts, not the
        // first row of the filtered list.
        function playPlaylistTrack(index, file) {{
            if (phoneOwnsPlayback()) listenerPaused = false;
            claimPlaybackAudioSession();
            const audioEl = document.getElementById('mobileAudioStream');
            const sameFile = Boolean(file && activeAudioFile === file);
            // The status poll only reloads the element when the file changes.
            // Tapping the track that is already selected, while it is paused,
            // has to start it inside this tap or iOS will refuse the later play().
            if (sameFile && audioStreamEngaged && audioEl && (audioEl.paused || audioEl.ended)) {{
                applyStreamPitch(currentPitchPercent);
                const started = audioEl.play();
                if (started && started.catch) started.catch(() => {{}});
            }}
            sendAction('select_index', {{ index: index, play: '1' }});
        }}

        async function fetchMiniPlaylist() {{
            const token = ++playlistFetchToken;
            const q = currentPlaylistQuery().trim();
            try {{
                let path = '/api/playlist';
                if (q) path += '?q=' + encodeURIComponent(q);
                const res = await fetch(apiUrl(path));
                const data = await res.json();
                // A slower reply for an older string must not overwrite the
                // matches for the string that is in the box now.
                if (token !== playlistFetchToken) return;
                renderMiniPlaylist(data);
                // A tap already picked the new mode; the poll must not paint the
                // old one back while that switch is still in flight.
                if (!modeSwitchBusy && data && data.mode) updateModeSwitch(data.mode);
            }} catch (e) {{}}
        }}

        // The host tells us why it has nothing to stream (empty archive, macOS
        // privacy block). Cache the newest reason so the overlay can explain a
        // refused engage tap instead of silently doing nothing.
        let streamUnavailableReason = "";

        // Start streaming with no extra taps. The server arms the session when
        // the service boots, so the phone picks up the running stream. Browsers
        // only allow audible playback after a gesture, so a refused play() keeps
        // the overlay up and the first touch starts the same stream.
        let lastEngageAt = 0;

        // Automatic playback is attempted as soon as the page loads. A browser
        // that refuses it (an autoplay policy, not a fault) usually approves the
        // very next interaction, so the first gesture anywhere on the dashboard
        // retries the same engage. That tap may land on a tab or a transport
        // button rather than the gate, which is why the listener is on the
        // document and runs before the control's own handler.
        let gestureEngageArmed = false;
        let gestureEngageHandler = null;

        function armFirstGestureEngage() {{
            if (gestureEngageArmed) return;
            gestureEngageArmed = true;
            gestureEngageHandler = function (ev) {{
                // Audio is already running, or the listener deliberately
                // stopped it: nothing to retry.
                if ((audioStreamEngaged && audioElIsPlaying()) || listenerPaused) {{
                    disarmGestureEngage();
                    return;
                }}
                const target = ev && ev.target;
                const tag = target && target.tagName ? String(target.tagName).toLowerCase() : '';
                // Do not hijack typing. The assistant prompt and the playlist
                // search are the two places a keystroke means text.
                if (ev && ev.type === 'keydown' && (tag === 'input' || tag === 'textarea')) return;
                // A tap on a control with its own meaning (transport, tabs,
                // volume, track row, the gate itself) must do exactly what it
                // says. Starting audio as a side effect of pressing Pause is
                // not a favour, so the retry only claims taps on the page
                // itself - anywhere the listener would otherwise get nothing.
                if (target && target.closest && target.closest(
                        'button, a, input, select, textarea, label, [onclick], [role="button"]')) {{
                    return;
                }}
                // One entry point: the same handler the Engage button runs, so
                // the tap gets the unlock, the rescan and the explanation.
                activateMobileStream();
            }};
            // touchend and click are the two events WebKit approves; keydown
            // covers the desktop keyboard. pointerdown is deliberately not
            // used: on iOS it does not grant playback activation, and it would
            // spend activateMobileStream's de-dupe window before the touchend
            // that actually can start the sound. activateMobileStream also
            // collapses the two events one tap produces.
            document.addEventListener('touchend', gestureEngageHandler, true);
            document.addEventListener('click', gestureEngageHandler, true);
            document.addEventListener('keydown', gestureEngageHandler, true);
        }}

        function disarmGestureEngage() {{
            if (!gestureEngageArmed) return;
            gestureEngageArmed = false;
            if (!gestureEngageHandler) return;
            document.removeEventListener('touchend', gestureEngageHandler, true);
            document.removeEventListener('click', gestureEngageHandler, true);
            document.removeEventListener('keydown', gestureEngageHandler, true);
            gestureEngageHandler = null;
        }}

        // The listener started this page's own audio. Later polls and taps
        // follow that stream. While a desk player is also on air, copy its
        // file into the session first so the switch does not jump mixes.
        function noteStreamEngaged() {{
            audioStreamEngaged = true;
            networkResumeAttempts = 0;
            networkResumePending = false;
            // The mix is audible now: mark the session as media playback so
            // iOS keeps it running when this page leaves the foreground.
            claimPlaybackAudioSession();
            updateMediaSessionMetadata();
            updateMediaSessionState(true);
            // Audio is running, so a pending "tap to start" retry is spent.
            disarmGestureEngage();            if (!hostPlayerActive) {{
                followStream = true;
                return;
            }}
            if (adoptInFlight) return;
            adoptInFlight = true;
            fetch('/api/control?action=adopt_host&target=stream', {{ method: 'POST' }})
                .finally(() => {{
                    adoptInFlight = false;
                    followStream = true;
                    const queued = pendingTransport;
                    pendingTransport = null;
                    const done = fetchStatus().catch(() => {{}});
                    if (queued) {{
                        Promise.resolve(done).finally(() => sendAction(queued.act, queued.params || {{}}));
                    }}
                }});
        }}

        // A stalled or reset connection. Put the same mix back on the element
        // at the position it had reached, and play it again. A decode error
        // does not come here: that file really cannot be played.
        function resumeAfterNetworkDrop() {{
            if (listenerPaused || !audioStreamEngaged || !activeAudioFile) return;
            if (networkResumePending) return;
            if (networkResumeAttempts >= 5) {{
                showStreamOverlay('The audio stream dropped. Tap anywhere to start it again.');
                return;
            }}
            networkResumePending = true;
            networkResumeAttempts += 1;
            const audioEl = document.getElementById('mobileAudioStream');
            const file = activeAudioFile;
            let resumeAt = 0;
            try {{
                if (audioEl && isFinite(audioEl.currentTime)) resumeAt = audioEl.currentTime;
            }} catch (e) {{}}
            const attempt = networkResumeAttempts;
            clearTimeout(networkResumeTimer);
            networkResumeTimer = setTimeout(() => {{
                networkResumePending = false;
                if (listenerPaused || activeAudioFile !== file) return;
                const el = document.getElementById('mobileAudioStream');
                if (!el) return;
                // `r` is ignored by the server. It keeps the browser from
                // reusing the connection that just died.
                el.src = '/api/stream?file=' + encodeURIComponent(file) + '&r=' + attempt;
                audioStreamConfigured = true;
                const once = function () {{
                    el.removeEventListener('loadedmetadata', once);
                    if (resumeAt > 1) {{
                        try {{ el.currentTime = resumeAt; }} catch (err) {{}}
                    }}
                    applyStreamPitch(currentPitchPercent);
                    const started = el.play();
                    if (started && started.catch) started.catch(() => {{}});
                }};
                el.addEventListener('loadedmetadata', once);
                try {{ el.load(); }} catch (e) {{}}
            }}, 350 * attempt);
        }}

        // `opts.gesture` is true when a real tap (or keypress) is calling us.
        // `opts.auto` is true for the page-load attempt, where a refusal is
        // expected on iPhone and must read as "tap to start", not as an error.
        function engageStreamFromServer(resumePos, opts) {{
            const options = opts || {{}};
            const onGesture = Boolean(options.gesture);
            const automatic = Boolean(options.auto);
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl) return;
            // In the Engage tap this still runs on the gesture's turn, which
            // is the only moment iOS will resume the audio graph. On the
            // automatic attempt a newly created context would start suspended,
            // and routing the element through it would silence the mix on
            // desktop Chrome, so the graph is only built from a real gesture
            // (or from a context a previous gesture already unlocked).
            if (onGesture) {{
                unlockAudioContext();
                claimPlaybackAudioSession();
                ensureAudioGraph();
            }} else if (audioCtx && audioCtx.state === 'running') {{
                ensureAudioGraph();
            }}
            if (!activeAudioFile) {{
                // /api/stream?file= would 404 here, which is indistinguishable
                // from a dead button on the phone. Explain the real cause.
                showStreamOverlay(streamUnavailableReason ||
                    'No audio is queued on the host. Start a mix in MP Audio Player, then tap again.');
                return;
            }}
            const streamUrl = '/api/stream?file=' + encodeURIComponent(activeAudioFile);
            // A real gesture makes this a listener-driven run, which is the only
            // kind allowed to advance the queue past a file it cannot decode.
            if (onGesture) {{
                listenerEngaged = true;
                unattendedStopShown = false;
                skipRunStopped = false;
            }}
            const current = audioEl.currentSrc || audioEl.src || '';
            let alreadyPointed = false;
            try {{
                alreadyPointed = current.indexOf('/api/stream') !== -1
                    && decodeURIComponent(current).indexOf(activeAudioFile) !== -1;
            }} catch (e) {{
                alreadyPointed = false;
            }}
            // A source installed by the status poll is not a user gesture.
            // iOS will not start that element from a later play() once the
            // poll's load was refused, so the tap binds the source itself.
            const mustBind = !alreadyPointed || audioEl.error || !audioStreamConfigured;
            lastEngageAt = performance.now();
            // Where this stream starts. A desk player that is already in the
            // mix wins over the saved resume point, and the seek runs even
            // when the element was already pointed at the file: that earlier
            // load captured a stale offset (or 0) and play() then continued
            // from there. After this snap the browser owns playback.
            let startAt = Number(resumePos);
            if (hostPlayerActive) {{
                const live = liveHostPosition();
                if (live > 0.5) startAt = live;
            }}
            if (isFinite(startAt) && startAt > 0.5) {{
                alignPlaybackTo(startAt, hostPlayerActive);
            }}
            if (mustBind) {{
                if (audioEl.__unlocking) endUnlock(audioEl);
                audioEl.src = streamUrl;
                audioStreamConfigured = true;
            }}
            // A new src can put the rate back to 1. Set it again before play
            // so the first moment of audio is already at the chosen pitch.
            applyStreamPitch(currentPitchPercent);
            let pending;
            try {{
                pending = audioEl.play();
            }} catch (err) {{
                reportEngageRefused(err, onGesture, automatic, audioEl);
                return;
            }}
            if (!pending || !pending.then) {{
                noteStreamEngaged();
                hideStreamOverlay();
                return;
            }}
            pending.then(() => {{
                noteStreamEngaged();
                // Playback really started, so the file is good: clear the
                // recovery budget for it and drop any leftover error notice.
                // The guard is the duration: a header-only file can resolve
                // play() and then end immediately, and the ended handler would
                // otherwise restart it forever.
                if (isFinite(audioEl.duration) && audioEl.duration > 0) {{
                    mediaRecoveryAttempts = 0;
                    mediaRecoveryUsed = false;
                    mediaRecoveryFile = activeAudioFile || "";
                    // A mix that really started means the skip run is over: the
                    // next damaged file should get a fresh budget, not inherit
                    // the tail of the previous burst.
                    consecutiveSkips = 0;
                    skipRunStartAt = 0;
                    skipBurstStartAt = 0;
                    skipBurstCount = 0;
                    exhaustedNoticeShown = false;
                    skipRunStopped = false;
                }}
                hideRestartNotice();
                hideStreamOverlay();
            }}).catch((err) => {{
                reportEngageRefused(err, onGesture, automatic, audioEl);
            }});
        }}

        // A refused play() has two very different meanings. The page-load
        // attempt being refused is not a failure: the browser's autoplay
        // policy simply wants a gesture, so the gate stays up with a calm
        // "tap to start" line and the next touch retries. A tap being refused
        // is a real answer and keeps the specific reason on screen.
        function reportEngageRefused(err, onGesture, automatic, audioEl) {{
            const why = (err && (err.message || err.name))
                ? ((err.name ? err.name + ': ' : '') + (err.message || 'playback was blocked'))
                : 'This browser blocked playback. Tap again.';
            const notAllowed = Boolean(err && (
                err.name === 'NotAllowedError'
                || /notallowed|user gesture|user didn.t interact|autoplay/i.test(
                    String((err && err.name) + ' ' + (err && err.message)))));
            if (!onGesture && (automatic || notAllowed)) {{
                // Keep the element pointed at the mix so the tap only has to
                // call play(), and say the one thing that helps: tap. The lead
                // line already carries the instruction, so an empty detail
                // avoids printing it twice, and a real host problem still
                // outranks it.
                clearTimeout(autoStartWatchdog);
                autoStartWatchdog = 0;
                armFirstGestureEngage();
                setStreamOverlayLead(STREAM_LEAD_TAP);
                noteTapExplanation(STREAM_LEAD_TAP);
                showStreamOverlay(streamUnavailableReason || '');
                return;
            }}
            noteTapExplanation(why);
            showStreamOverlay(streamUnavailableReason || why);
        }}

        // After a skip the element is in an error/ended state, so it does not
        // report itself as "playing" even though the listener expects the next
        // mix to start on its own. This flag carries that intent across the
        // status poll that names the replacement file.
        let skipResumePending = false;

        // The mix the queue handed this phone cannot be decoded here. Rather
        // than stopping on an error card, write the file off on this device,
        // tell the host (so no other device is served it either) and walk on to
        // the next mix. The walk is bounded, because "keep trying forever" is
        // just the flicker loop with extra steps: once every candidate has been
        // tried in one run, the listener gets a single clear message.
        function handleUnplayableMix(file) {{
            if (!file) return;
            // The run already ended with one clear message (nothing plays, or
            // the archive stopped answering). A late `error` from the last
            // source must not restart the walk or the banner.
            if (skipRunStopped) return;
            // An unattended run must not write files off. The only thing that
            // proves a mix is broken is a decode refusal, and even that is not
            // worth permanently skipping a library entry because a page opened
            // on its own. Leave the queue alone, say how to try, and let the
            // tap that follows become a normal listener run.
            if (!listenerEngaged) {{
                if (!unattendedStopShown) {{
                    unattendedStopShown = true;
                    noteTapExplanation(STREAM_LEAD_TAP);
                    setStreamOverlayLead(STREAM_LEAD_TAP);
                    showStreamOverlay(streamUnavailableReason ||
                        'This mix could not be loaded. Tap anywhere to try again.');
                    armFirstGestureEngage();
                }}
                return;
            }}
            const label = file.split('/').pop() || file;
            const alreadyKnown = unplayableFiles.has(file);
            const now = performance.now();
            if (!skipRunStartAt || (now - skipRunStartAt) > 60000) {{
                // A new run: the last burst of failures was long enough ago that
                // this is a fresh problem, so the budget starts over.
                skipRunStartAt = now;
                consecutiveSkips = 0;
                exhaustedNoticeShown = false;
            }}
            if (alreadyKnown) {{
                // The same file errored again (a second `error` event, or a
                // stale poll naming it). It must not cost a second slot in the
                // run budget, otherwise a single file that fires twice looks
                // like two dead mixes and the queue gives up one entry early.
                // Still tell the host, so a server that has not advanced yet
                // does advance now.
                mediaRecoveryUsed = true;
                lastMediaErrorAt = now;
                sendAction('track_failed', {{ path: file }});
                skipResumePending = true;
                fetchStatus().catch(() => {{}});
                return;
            }}
            unplayableFiles.add(file);
            consecutiveSkips += 1;
            mediaRecoveryUsed = true;
            lastMediaErrorAt = now;
            mediaRecoveryFile = file;
            // Tell the host which exact file failed. The server records it, so
            // the next poll hands out a different file even if this page is
            // closed and reopened.
            sendAction('track_failed', {{ path: file }});
            // A burst of different files failing seconds apart means the source
            // itself is gone, not that each mix is damaged. Stop before the
            // walk writes off the rest of the library or repaints the banner
            // once per file.
            if (!skipBurstStartAt || (now - skipBurstStartAt) > SKIP_BURST_WINDOW_MS) {{
                skipBurstStartAt = now;
                skipBurstCount = 0;
            }}
            skipBurstCount += 1;
            if (skipBurstCount >= SKIP_BURST_LIMIT) {{
                skipRunStopped = true;
                hideRestartNotice();
                showRestartNotice(
                    'The music library stopped responding — skipping is paused. ' +
                    'Check the archive drive or share, then tap anywhere to try again.',
                    'skip');
                showPlaybackExhaustedNotice(
                    'The last ' + skipBurstCount + ' mixes could not be loaded. ' +
                    'The archive drive or share may be offline. Reconnect it, then ' +
                    'tap anywhere to try again.');
                return;
            }}
            // The queue length is what bounds the run: once this phone has
            // written off as many files as the library holds, there is nothing
            // left to try. `queueLength` comes from the last status poll.
            const total = Number(queueLength) || 0;
            if (total > 0 && consecutiveSkips >= total) {{
                // Every entry in this library has now failed on this device.
                showPlaybackExhaustedNotice();
                return;
            }}
            showRestartNotice('“' + label + '” cannot be played — skipping to the next mix…', 'skip');
            // The action above advanced the server cursor; the next status poll
            // re-points the element at the new file and resumes it because this
            // flag says the listener still wants sound.
            skipResumePending = true;
            fetchStatus().catch(() => {{
                skipResumePending = false;
            }});
        }}

        // No mix in the playlist can be decoded on this device. This is the one
        // case that stops automatic skipping: continuing would mean probing the
        // whole library again on every poll, which is exactly the behaviour the
        // old reconnect handler was fixed to avoid.
        function showPlaybackExhaustedNotice(detail) {{
            if (exhaustedNoticeShown) return;
            exhaustedNoticeShown = true;
            skipRunStopped = true;
            // A page loaded afresh has not seen any failures itself, so the
            // host's count is the truthful one there. Whichever is larger is the
            // number this playlist has actually lost.
            const count = Math.max(unplayableFiles.size, Number(serverUnplayableCount) || 0);
            showStreamOverlay(detail ||
                'None of the mixes in this playlist can be played on this device. ' +
                (count > 0
                    ? count + ' ' + (count === 1 ? 'mix was' : 'mixes were') +
                      ' tried and each one is empty or damaged on the computer. '
                    : '') +
                'Re-export the mixes (or choose another library), then tap ' +
                '“Engage Audio Stream” to try again.');
        }}

        // A resume point on the final frame makes play() resolve and then end
        // the media at once: the tap "works" but nothing is heard. The server
        // no longer sends such a position, but a browser that dropped the seek
        // can still land there, so an end that arrives immediately after an
        // engage restarts the mix instead of skipping it.
        //
        // This is bounded on purpose. A file the browser can never play bounces
        // straight back to "ended" every time, so an unconditional restart here
        // is another way to spin the page forever: reset, end, reset, end.
        // After two restarts of the same file, hand it to the normal advance.
        let quickEndRestarts = 0;
        let quickEndFile = "";

        function restartStreamFromTop() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl) return;
            if (quickEndFile !== activeAudioFile) {{
                quickEndFile = activeAudioFile || "";
                quickEndRestarts = 0;
            }}
            if (quickEndRestarts >= 2) {{
                // Two immediate ends is not a bad seek, it is an unplayable
                // file. Report it the same way a decode error is reported, so
                // the queue moves on instead of restarting it again.
                quickEndRestarts = 0;
                handleUnplayableMix(activeAudioFile);
                return;
            }}
            quickEndRestarts += 1;
            try {{
                audioEl.currentTime = 0;
            }} catch (e) {{}}
            lastEngageAt = performance.now();
            audioEl.play().catch(() => {{
                showStreamOverlay(streamUnavailableReason);
            }});
        }}

        // The overlay is a gate for the mobile autoplay gesture, so it belongs
        // to the player tab only. Without this the fixed overlay would cover
        // the tab bar and the assistant could never be reached before the
        // first successful play().
        //
        // It starts hidden: the dashboard is usable at once and the stream
        // starts by itself, so a gate that says "tap to engage" is only shown
        // when the browser actually refused or there is nothing to play.
        let streamOverlayWanted = false;
        // Last `autoplay.status` the host reported. "pending" means its queue
        // is still being built, so a missing file is not yet a reason to ask
        // the listener for anything.
        let hostAutoplayStatus = "";
        // The last thing a tap told the listener, and when. The 2 s status poll
        // must not paint its generic waiting text over a specific answer the
        // tap just produced.
        let lastTapExplanation = "";
        let lastTapExplanationAt = 0;

        function noteTapExplanation(text) {{
            lastTapExplanation = text || "";
            lastTapExplanationAt = performance.now();
        }}

        function renderStreamOverlay() {{
            const overlay = document.getElementById('streamOverlay');
            if (!overlay) return;
            // Never cover a mix the listener is actually hearing. The gate
            // exists to get audio started; while it is playing it can only be
            // in the way, and `overlay-active` locks the page with
            // body {{ position: fixed }}, which is what pinned the dashboard to
            // the top of the screen on iPhone while the mix played behind it.
            //
            // Checking the element rather than `audioStreamEngaged` matters:
            // that flag stays true across a pause or a dropped connection, and
            // those are exactly the cases where the gate must still be able to
            // say "tap anywhere to start it again".
            if (streamOverlayWanted && audioElIsPlaying()) {{
                streamOverlayWanted = false;
            }}
            const visible = streamOverlayWanted && activeTab === 'player';
            overlay.classList.toggle('show', visible);
            // Lock the page behind the gate: a swipe would otherwise scroll the
            // dashboard and leave the engage button below the fold.
            document.body.classList.toggle('overlay-active', visible);
        }}

        // The gate's headline copy, switched to an invitation only once the
        // browser has actually refused, so the page never asks for a tap it
        // does not need.
        const STREAM_LEAD_TAP = 'Tap anywhere to start the mix on this device.';

        function setStreamOverlayLead(text) {{
            const lead = document.getElementById('streamOverlayLead');
            if (lead && text) lead.innerText = text;
        }}

        function showStreamOverlay(detail) {{
            streamOverlayWanted = true;
            const note = document.getElementById('streamOverlayDetail');
            if (note) {{
                if (detail) {{
                    note.innerText = detail;
                    note.style.display = 'block';
                }} else {{
                    note.style.display = 'none';
                }}
            }}
            // A visible gate must always be actionable. The status poll can put
            // the gate up a moment before the automatic attempt is refused, and
            // a tap in that window used to do nothing at all. Arming here means
            // whatever brought the gate up, the very next tap starts the mix.
            if (!(audioStreamEngaged && audioElIsPlaying())) armFirstGestureEngage();
            renderStreamOverlay();
        }}

        // The overlay is the first thing a phone sees, so it explains the host
        // state on its own instead of waiting for a tap that would go nowhere.
        //
        // The tap's own answer outranks the background poll. A rescan that
        // found nothing writes a specific explanation ("The host still has no
        // mixes in the archive…"), and this used to replace it with the generic
        // "Waiting for the host to queue a mix…" on the next 2 s poll - so the
        // listener saw the message they had just earned blink away and the
        // button look dead. While a recent tap is being explained, the poll
        // leaves the note alone.
        const TAP_EXPLANATION_HOLD_MS = 8000;

        // True while the host's own queue build is still running. Asking the
        // listener to "engage" a stream that has no file yet is the very gate
        // this page is trying to get rid of, so a pending host leaves the
        // dashboard alone until it names a file (or reports a real problem).
        function hostQueueIsStillBuilding() {{
            return !hostAutoplayStatus || hostAutoplayStatus === 'pending';
        }}

        function syncStreamOverlayDetail() {{
            if (activeAudioFile) {{
                setStreamSwitchVisible(false);
                return;
            }}
            if (lastTapExplanation && (performance.now() - lastTapExplanationAt) < TAP_EXPLANATION_HOLD_MS) {{
                return;
            }}
            // The queue is being built: the automatic attempt has not had its
            // chance yet, so there is nothing to ask the listener for.
            if (hostQueueIsStillBuilding() && !audioStreamEngaged) return;
            // The selected music library is offline while the archive is fine.
            // The Engage button cannot help, so offer the library that can play
            // instead of a button that only produces the same warning again.
            setStreamSwitchVisible(Boolean(musicLibraryOffline));
            setStreamOverlayLead(STREAM_LEAD_TAP);
            showStreamOverlay(streamUnavailableReason ||
                'Waiting for the host to queue a mix…');
        }}

        // True when the host reports the Music library is selected but none of
        // its files can be read. The gate then offers the Mix Archive.
        let musicLibraryOffline = false;

        function setStreamSwitchVisible(visible) {{
            const btn = document.getElementById('streamOverlaySwitch');
            if (btn) btn.style.display = visible ? 'block' : 'none';
        }}

        // One tap: switch to a library that can play, then start the stream from
        // inside the same gesture so the browser approves the play().
        async function switchToPlayableLibrary() {{
            const btn = document.getElementById('streamOverlaySwitch');
            if (btn) btn.disabled = true;
            try {{
                unlockAudioContext();
                await switchLibraryMode('MIX_ARCHIVE');
                // The queue has just been rebuilt; wait for the status poll to
                // name the first archive file, then engage inside this gesture.
                for (let i = 0; i < 20 && !activeAudioFile; i++) {{
                    await fetchStatus();
                    if (!activeAudioFile) await new Promise(r => setTimeout(r, 150));
                }}
                if (activeAudioFile) {{
                    engageStreamFromServer(lastServerResume, {{ gesture: true }});
                }} else {{
                    showStreamOverlay(streamUnavailableReason ||
                        'The Mix Archive has nothing playable either.');
                }}
            }} catch (e) {{
                showStreamOverlay('Could not switch library. Tap again to retry.');
            }} finally {{
                if (btn) btn.disabled = false;
                setStreamSwitchVisible(false);
            }}
        }}

        function hideStreamOverlay() {{
            streamOverlayWanted = false;
            clearTimeout(autoStartWatchdog);
            autoStartWatchdog = 0;
            // Playback started, so whatever a tap explained is settled. The
            // hold must not keep a stale message over a gate that is now open.
            lastTapExplanation = "";
            lastTapExplanationAt = 0;
            setStreamOverlayLead(STREAM_LEAD_TAP);
            // Drop the detail line too: "Waiting for the host to queue a mix…"
            // was painted before the first status poll named the file, and a
            // later refusal would otherwise reopen the gate showing it.
            const note = document.getElementById('streamOverlayDetail');
            if (note) {{
                note.innerText = "";
                note.style.display = 'none';
            }}
            renderStreamOverlay();
            const banner = document.getElementById('mobileAudioBanner');
            if (banner) banner.style.display = 'block';
        }}

        let streamAutoStarted = false;
        // True when the dashboard is mirroring a running MP Audio Player. Its
        // desk clock decides where the page starts, but it no longer decides
        // whether the page plays: the dashboard engages the stream by itself.
        let hostPlayerActive = false;
        let hostPlayerFile = "";
        // The desk player's clock at the last status poll, and when that poll
        // landed. Opening the page and pressing Engage minutes later has to
        // seek to where the desk is now, not to the position captured when
        // the element was first pointed at the file.
        let lastHostPositionSec = 0;
        let lastHostPositionAt = 0;
        // A load-time snap to the desk. Cleared once the element lands there,
        // or as soon as the listener seeks for themselves.
        let hostAlignTarget = 0;
        let hostAlignTries = 0;

        function liveHostPosition() {{
            const base = Number(lastHostPositionSec);
            if (!isFinite(base) || base <= 0) return 0;
            if (lastServerState !== 'playing') return base;
            const elapsed = (performance.now() - lastHostPositionAt) / 1000;
            if (!isFinite(elapsed) || elapsed < 0 || elapsed > 20) return base;
            return base + elapsed;
        }}

        function clearHostAlign() {{
            hostAlignTarget = 0;
            hostAlignTries = 0;
        }}

        // Put the element on `pos`. A host snap does not use the "near the
        // end, start over" rule: the desk is actually at that moment, including
        // the last part of a long mix. Retries live on the `playing` event
        // because a seek issued before the browser has a timeline is ignored
        // and the mix then starts at 0.
        function alignPlaybackTo(pos, fromHost) {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !isFinite(pos) || pos <= 0.5) return;
            if (fromHost) {{
                hostAlignTarget = pos;
                hostAlignTries = 0;
            }}
            const apply = function () {{
                if (fromHost) {{
                    try {{
                        audioSeekInFlight = true;
                        audioEl.currentTime = hostAlignTarget || pos;
                        setTimeout(() => {{ audioSeekInFlight = false; }}, 1500);
                    }} catch (e) {{
                        audioSeekInFlight = false;
                    }}
                }} else {{
                    seekAudioToWithResume(pos, true);
                }}
            }};
            // Always wait for the next load as well. Engage often replaces
            // src after this call, and the duration already on the element
            // belongs to the previous file (or the silent unlock clip). A
            // seek against that timeline is discarded when the new source
            // starts, which left the stream at 0 while the desk was midway
            // through the mix.
            const once = function () {{
                audioEl.removeEventListener('loadedmetadata', once);
                audioEl.removeEventListener('loadeddata', once);
                audioEl.removeEventListener('canplay', once);
                apply();
            }};
            audioEl.addEventListener('loadedmetadata', once);
            audioEl.addEventListener('loadeddata', once);
            audioEl.addEventListener('canplay', once);
            if (audioTimelineEnd(audioEl) > 0) apply();
        }}

        // iPhone and iPad: the device this page is listened on most. Two iOS
        // rules shape everything below:
        //
        //   1. Audible play() is only allowed inside the tap that called it, so
        //      a play() from the status poll is rejected. That rejection is
        //      what made Engage look dead: the gate never moved.
        //   2. A plain <audio> element keeps playing when Safari is backgrounded
        //      or the phone is locked. An AudioContext does NOT: WebKit
        //      suspends/interrupts it the moment the app loses focus, and a mix
        //      routed through createMediaElementSource then plays silence.
        //      That is the "music stops when I switch apps" report.
        function isIOSAudioDevice() {{
            const ua = navigator.userAgent || '';
            if (/iPhone|iPad|iPod/i.test(ua)) return true;
            // iPadOS 13+ reports itself as a Mac.
            if (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) return true;
            return false;
        }}

        function needsPlaybackGesture() {{
            return isIOSAudioDevice();
        }}

        // iOS 16.4+ Audio Session API. The default session is "ambient", which
        // the hardware ringer switch silences and which the OS may end as soon
        // as the page loses focus. "playback" marks this page as intentional
        // media output, like a native music app: it survives the silent switch
        // and keeps the audio session in the background. A no-op elsewhere.
        function claimPlaybackAudioSession() {{
            try {{
                const nav = navigator;
                if (nav.audioSession && typeof nav.audioSession === 'object') {{
                    nav.audioSession.type = 'playback';
                }}
            }} catch (e) {{}}
        }}

        // Lock-screen / Control Center metadata. With a metadata record and a
        // playbackState, iOS treats this page as the now-playing app and shows
        // transport controls instead of stopping the mix when the screen locks.
        // The <audio> element still owns the clock and the decoders.
        let trackMeta = {{ title: '', artist: '', album: '', artwork: '' }};

        function updateMediaSessionMetadata() {{
            if (!('mediaSession' in navigator) || typeof MediaMetadata === 'undefined') return;
            const title = trackMeta.title || 'MP Mix Manager';
            const artist = trackMeta.artist || 'MPlanetarian';
            const artSrc = trackMeta.artwork || '';
            const artwork = artSrc ? [{{ src: artSrc }}] : [];
            try {{
                navigator.mediaSession.metadata = new MediaMetadata({{
                    title: title,
                    artist: artist,
                    album: trackMeta.album || 'MP Mix Manager',
                    artwork: artwork,
                }});
            }} catch (e) {{}}
        }}

        function updateMediaSessionState(playing) {{
            if (!('mediaSession' in navigator)) return;
            try {{
                navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
            }} catch (e) {{}}
        }}

        // Hardware and lock-screen buttons must drive whichever side is really
        // playing. While this page owns the mix that is its own element; while
        // the desk player is on air the same buttons are remote controls.
        function mediaSessionPlay() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl && phoneOwnsPlayback()) {{
                if (audioEl.paused) {{
                    listenerPaused = false;
                    applyStreamPitch(currentPitchPercent);
                    const started = audioEl.play();
                    if (started && started.catch) started.catch(() => {{}});
                    sendAction('play');
                }}
                return;
            }}
            sendAction('play');
        }}

        function mediaSessionPause() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl && phoneOwnsPlayback()) {{
                if (!audioEl.paused) {{
                    listenerPaused = true;
                    audioEl.pause();
                    sendAction('pause');
                }}
                return;
            }}
            sendAction('pause');
        }}

        function installMediaSessionActions() {{
            if (!('mediaSession' in navigator)) return;
            const ms = navigator.mediaSession;
            if (!ms.setActionHandler) return;
            const audioEl = document.getElementById('mobileAudioStream');
            const commitMediaSeek = (seconds) => {{
                if (!audioEl || !isFinite(seconds)) return;
                clearHostAlign();
                try {{
                    const dur = isFinite(audioEl.duration) ? audioEl.duration : currentDurationSec;
                    let next = Math.max(0, seconds);
                    if (dur > 0) next = Math.min(next, Math.max(0, dur - 0.25));
                    audioSeekInFlight = true;
                    audioEl.currentTime = next;
                    setTimeout(() => {{ audioSeekInFlight = false; }}, 1500);
                    if (phoneOwnsPlayback()) sendAction('seek_to', {{ position: next.toFixed(1) }});
                }} catch (e) {{
                    audioSeekInFlight = false;
                }}
            }};
            const seekBy = (delta) => {{
                if (!audioEl) return;
                commitMediaSeek((audioEl.currentTime || 0) + delta);
            }};
            const actions = {{
                play: mediaSessionPlay,
                pause: mediaSessionPause,
                stop: mediaSessionPause,
                previoustrack: () => sendAction('prev'),
                nexttrack: () => sendAction('next'),
                seekbackward: (details) => seekBy(-(Number(details && details.seekOffset) || 30)),
                seekforward: (details) => seekBy(Number(details && details.seekOffset) || 30),
                seekto: (details) => {{
                    if (details == null || !isFinite(details.seekTime)) return;
                    commitMediaSeek(Number(details.seekTime));
                }},
            }};
            Object.keys(actions).forEach((name) => {{
                try {{ ms.setActionHandler(name, actions[name]); }} catch (e) {{}}
            }});
        }}

        // Called once the dashboard knows which file the server is streaming.
        //
        // The stream starts by itself: this is the automatic press of "Engage
        // Audio Stream". Chrome and Firefox on a LAN origin generally allow it,
        // and every browser falls back to the gate plus the first-touch retry
        // when it does not. A live desk player no longer suppresses it — the
        // listener opened the page to hear the music here, so this page adopts
        // the desk's file, position and level and plays it.
        const AUTO_START_WATCHDOG_MS = 4000;
        let autoStartWatchdog = 0;

        function maybeAutoStartStream() {{
            if (streamAutoStarted) return;
            if (!activeAudioFile) return;
            // Audible playback outranks every reason to raise the gate. This is
            // the iPhone regression: the branch below returns without marking
            // the automatic attempt as spent, so the 2 s status poll re-entered
            // here and re-showed the gate *over* a mix the listener had already
            // started. `overlay-active` sets body {{ position: fixed }}, so the
            // page locked itself to the top of the screen with the dashboard
            // unusable behind the gate - the "keeps the page open from the
            // start" report - while the audio kept playing underneath it.
            if (audioStreamEngaged || audioElIsPlaying()) {{
                streamAutoStarted = true;
                hideStreamOverlay();
                return;
            }}
            if (needsPlaybackGesture()) {{
                // iOS refuses a play() outside a tap, and a rejected play()
                // marks the element so the later Engage tap fails too. Leave
                // the gate up and let the tap do the first load. Any tap will
                // do: the retry listens on the whole document.
                //
                // The attempt is still spent: without this flag every later
                // poll walked back in here and re-showed the gate, including
                // after the listener's tap had started the mix.
                streamAutoStarted = true;
                armFirstGestureEngage();
                setStreamOverlayLead(STREAM_LEAD_TAP);
                showStreamOverlay(streamUnavailableReason);
                return;
            }}
            streamAutoStarted = true;
            // If the attempt neither starts audio nor explains a refusal (a
            // silent autoplay policy, or a dead stream), the listener must not
            // be left with a silent page and no control. The gate comes up on
            // its own.
            clearTimeout(autoStartWatchdog);
            autoStartWatchdog = setTimeout(() => {{
                autoStartWatchdog = 0;
                if (audioStreamEngaged || audioElIsPlaying()) return;
                setStreamOverlayLead(STREAM_LEAD_TAP);
                noteTapExplanation(STREAM_LEAD_TAP);
                showStreamOverlay(streamUnavailableReason || '');
                armFirstGestureEngage();
            }}, AUTO_START_WATCHDOG_MS);
            engageStreamFromServer(lastServerResume, {{ auto: true }});
        }}

        // Keep the local element pointed at the host's file so a tap can start
        // it immediately. On iOS this only records the file: the first load has
        // to happen inside the tap, so binding src here would spend the gesture
        // approval the tap needs.
        function syncHostPlayerFile() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !activeAudioFile) return;
            if (needsPlaybackGesture() && !audioStreamEngaged) {{
                // Remember the file so a later poll does not load() the
                // element out from under the tap. The tap binds src itself.
                hostPlayerFile = activeAudioFile;
                return;
            }}
            if (hostPlayerFile === activeAudioFile && audioStreamConfigured) return;
            hostPlayerFile = activeAudioFile;
            audioEl.src = '/api/stream?file=' + encodeURIComponent(activeAudioFile);
            audioStreamConfigured = true;
            const resumeAt = hostPlayerActive
                ? (liveHostPosition() || Number(lastServerResume) || 0)
                : (Number(lastServerResume) || 0);
            if (resumeAt > 0.5) {{
                alignPlaybackTo(resumeAt, hostPlayerActive);
            }}
            audioEl.load();
        }}

        // touchend and click both arrive for one tap. The first call is the
        // gesture that iOS will approve; the second must not restart the mix.
        //
        // It starts at -Infinity, not 0: with 0, a tap in the first 700 ms of
        // the page's life was swallowed by the de-dupe and the button looked
        // dead. Only a previous tap may suppress a tap.
        let lastEngageTapAt = -Infinity;

        function activateMobileStream() {{
            const now = (typeof performance !== 'undefined') ? performance.now() : Date.now();
            if (isFinite(lastEngageTapAt) && now - lastEngageTapAt < 700) return;
            lastEngageTapAt = now;
            // Resume before any later await. A context created after the fetch
            // stays suspended on iPhone and the equaliser (and the mix, once
            // it is routed through that context) never starts.
            unlockAudioContext();
            // The session category has to be claimed from a gesture too, and
            // this is the tap that is about to start the mix.
            claimPlaybackAudioSession();
            // A tap must always produce an answer. The old handler returned
            // silently when the host had no queue, so on a phone with an empty
            // archive the button looked broken: no sound, no message, no
            // spinner. Ask the host to re-scan first, then explain the state
            // that is left. Say so before any await, so a slow host is not a
            // button that appears to do nothing.
            noteTapExplanation('Starting the stream on this phone…');
            showStreamOverlay('Starting the stream on this phone…');
            if (!activeAudioFile) {{
                rescanForEngage();
                return;
            }}
            // This runs inside a real tap, so the browser will approve it even
            // when the automatic attempt at page load was refused.
            engageStreamFromServer(lastServerResume, {{ gesture: true }});
        }}

        // The unlock must happen inside the tap, before any await: iOS only
        // grants playback to a play() that runs in the gesture's own task.
        // The older code awaited a fetch first, so the later play() was
        // rejected and the mix stayed silent with the overlay still up.
        let engageRescanToken = 0;

        // A silent play() in the tap's own task marks the element as
        // gesture-approved. Safari keeps that approval for the element while
        // the page is alive, so the real play() after the rescan fetch still
        // works even though it runs a second later.
        //
        // The clip is a `blob:` URL, not a `data:` one, because the page's
        // Content-Security-Policy allows only `media-src 'self' blob:`. The old
        // `data:` connection is refused by the policy, so `src` fails with
        // MEDIA_ERR_SRC_NOT_SUPPORTED before any sound is possible. That error
        // arrives while the element is still pointed at the unlock clip, and
        // the media-error handler cannot tell it apart from a genuine decode
        // failure of the listener's mix - which is the "Engage Audio Stream
        // does nothing" report: the tap silently writes off the mix it was
        // about to play.
        function unlockStreamElement() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || audioEl.__unlocked) return false;
            ensureAudioGraph();
            const src = beginUnlock(audioEl);
            if (!src) return false;
            try {{
                audioEl.__unlocked = true;
                audioEl.src = src;
                const p = audioEl.play();
                if (p && p.catch) p.catch(() => {{ endUnlock(audioEl); }});
            }} catch (e) {{
                endUnlock(audioEl);
            }}
            return true;
        }}

        function rescanForEngage() {{
            const token = ++engageRescanToken;
            unlockStreamElement();
            const scanning = streamUnavailableReason ||
                'No mix is queued on the host yet — asking it to rescan the archive…';
            noteTapExplanation(scanning);
            showStreamOverlay(scanning);
            // The element now holds the silent unlock source; the queue build
            // below re-points it at /api/stream.
            audioStreamConfigured = false;
            // Retrying after the "nothing in this playlist plays" message is a
            // deliberate fresh start: forget the files this phone wrote off and
            // reset the skip run, so repaired mixes get another chance.
            unplayableFiles.clear();
            consecutiveSkips = 0;
            skipRunStartAt = 0;
            skipBurstStartAt = 0;
            skipBurstCount = 0;
            exhaustedNoticeShown = false;
            skipRunStopped = false;
            hideRestartNotice();
            fetch('/api/control?action=rescan', {{ method: 'POST' }})
                .then(r => r.json())
                .then(() => fetchStatus())
                .then(() => {{
                    if (token !== engageRescanToken) return;
                    if (!activeAudioFile) {{
                        const empty = streamUnavailableReason ||
                            'The host still has no mixes in the archive. Add a mix ' +
                            'or choose another library on the host, then tap again.';
                        noteTapExplanation(empty);
                        showStreamOverlay(empty);
                        return;
                    }}
                    engageStreamFromServer(lastServerResume, {{ gesture: true }});
                }})
                .catch(() => {{
                    if (token !== engageRescanToken) return;
                    const offline = streamUnavailableReason ||
                        'Could not reach the host to rescan the archive.';
                    noteTapExplanation(offline);
                    showStreamOverlay(offline);
                }});
        }}

        function handlePlayPauseClick() {{
            const audioEl = document.getElementById('mobileAudioStream');
            // While this page has not started its own stream, a desktop player
            // on air owns the sound, so the tap has to be forwarded there.
            // Toggling only the idle phone element left the room silent and
            // the button disagreeing with the mix.
            if (!phoneOwnsPlayback()) {{
                sendAction('play-pause');
                return;
            }}
            unlockAudioContext();
            claimPlaybackAudioSession();
            ensureAudioGraph();
            if (!audioEl || !activeAudioFile) {{
                showStreamOverlay(streamUnavailableReason ||
                    'No audio is queued on the host. Start a mix in MP Audio Player, then tap again.');
                return;
            }}
            // Send play or pause, not a toggle. The session flag and the
            // element used to be flipped independently, so one tap undid the
            // other and the button looked dead.
            if (!audioEl.paused && !audioEl.ended) {{
                listenerPaused = true;
                audioEl.pause();
                sendAction('pause');
                return;
            }}
            listenerPaused = false;
            applyStreamPitch(currentPitchPercent);
            let pending;
            try {{
                pending = audioEl.play();
            }} catch (e) {{
                showStreamOverlay(streamUnavailableReason ||
                    'This browser blocked playback. Tap anywhere to start the mix.');
                return;
            }}
            sendAction('play');
            if (pending && pending.then) {{
                pending.then(() => noteStreamEngaged()).catch(() => {{
                    showStreamOverlay(streamUnavailableReason ||
                        'This browser blocked playback. Tap anywhere to start the mix.');
                }});
            }} else {{
                noteStreamEngaged();
            }}
        }}

        // Visualizer. The bars and the spectrogram read the phone's <audio>
        // element through Web Audio where that is safe. On iOS the element
        // stays on the native media pipeline for background playback, so the
        // server decodes the file the phone is hearing and /api/eq returns
        // those 24 bands instead.
        const BANDS = 24;
        const currentLevels = new Float32Array(BANDS).fill(0.04);
        const targetLevels = new Float32Array(BANDS).fill(0.04);
        const currentPeaks = new Float32Array(BANDS).fill(0.04);
        const peakVels = new Float32Array(BANDS).fill(0.0);
        const barEls = new Array(BANDS);
        const peakEls = new Array(BANDS);
        let vizMode = 'bars';
        let audioCtx = null;
        let audioGraph = null;
        let freqBins = null;
        let timeBins = null;
        let spectroBacking = null;
        let spectroShiftAcc = 0;
        let captureSilenceFrames = 0;
        let captureSawSignal = false;
        let spectrumSourceFile = '';

        function phoneIsAudible() {{
            const audioEl = document.getElementById('mobileAudioStream');
            return Boolean(
                audioEl && audioStreamConfigured && !audioEl.paused && !audioEl.ended
            );
        }}

        function cacheBarEls() {{
            if (barEls[0]) return;
            for (let i = 0; i < BANDS; i++) {{
                barEls[i] = document.getElementById('vb' + i);
                peakEls[i] = document.getElementById('vp' + i);
            }}
        }}

        // Create and resume the context inside the tap. iOS will not start
        // one that was only constructed from a timer, and a suspended context
        // silences the element once it is routed through Web Audio.
        function unlockAudioContext() {{
            // iOS: never build Web Audio. It is not the missing piece there,
            // it is the thing that stops background playback (see
            // isIOSAudioDevice). Returning null keeps the element on the
            // native media pipeline, which is the only path iOS keeps alive
            // when the app is backgrounded.
            if (isIOSAudioDevice()) return null;
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return null;
            if (!audioCtx) {{
                try {{
                    try {{ audioCtx = new Ctx({{ latencyHint: 'playback' }}); }}
                    catch (e) {{ audioCtx = new Ctx(); }}
                }} catch (e) {{
                    return null;
                }}
            }}
            if (audioCtx.state === 'suspended') {{
                try {{ audioCtx.resume(); }} catch (e) {{}}
            }}
            return audioCtx;
        }}

        function attachSpectrumPull(ctx, analyser) {{
            // WebKit drops AnalyserNode data when nothing pulls the graph.
            // The audible connection does that for the element source. This
            // muted processor does it for a capture-stream source, which must
            // not be wired to the speakers or the mix plays twice.
            //
            // Only for the capture path: an element source is already pulled by
            // its audible connection, so calling this there added a second
            // always-on processor for nothing. A 2048-frame ScriptProcessorNode
            // wakes the audio thread ~21 times a second forever, and on iOS it
            // is the node that most often gets the audio session torn down.
            if (!ctx || !analyser) return;
            if (!(analyser.context === ctx)) return;
            try {{
                const pull = ctx.createScriptProcessor(2048, 1, 1);
                const mute = ctx.createGain();
                mute.gain.value = 0;
                pull.onaudioprocess = function (ev) {{
                    const out = ev.outputBuffer.getChannelData(0);
                    for (let i = 0; i < out.length; i++) out[i] = 0;
                }};
                analyser.connect(pull);
                pull.connect(mute);
                mute.connect(ctx.destination);
            }} catch (e) {{}}
        }}

        function ensureAudioGraph() {{
            // iPhone/iPad: the element is deliberately left unrouted. A
            // MediaElementSourceNode hands the element's output to the
            // AudioContext, which iOS suspends in the background, so the mix
            // would go silent the moment the listener switches apps. The
            // server decodes the same file for /api/eq, so the equaliser has
            // real levels while the music keeps playing. See
            // isIOSAudioDevice() and stream_spectrum_levels().
            if (isIOSAudioDevice()) return null;
            const audioEl = document.getElementById('mobileAudioStream');
            const ctx = unlockAudioContext();
            if (!audioEl || !ctx) return null;
            if (audioGraph && audioGraph.analyser) {{
                if (ctx.state === 'suspended') {{
                    try {{ ctx.resume(); }} catch (e) {{}}
                }}
                return audioGraph;
            }}
            if (audioGraph && audioGraph.failed) return null;
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 2048;
            analyser.smoothingTimeConstant = 0.62;
            analyser.minDecibels = -90;
            analyser.maxDecibels = -20;
            let source = null;
            let mode = '';
            // A MediaElementSource follows the element across a src change, so
            // the analyser keeps seeing the next track. Chrome's captureStream
            // track goes silent once the element loads a new source, which left
            // the equaliser and the spectrogram flat after Next. Prefer the
            // element source; captureStream remains the fallback for an element
            // that refuses the node.
            try {{
                // Progressive FLAC/WAV, and desktop browsers. Routing the
                // element through the context is what lets the analyser see
                // it, and the gain node is the only level Safari will honour.
                // Connect the destination in this same turn or the mix goes
                // silent.
                source = ctx.createMediaElementSource(audioEl);
                mode = 'element';
                mixGain = ctx.createGain();
                mixGain.gain.value = mixLoudness();
                source.connect(analyser);
                analyser.connect(mixGain);
                mixGain.connect(ctx.destination);
            }} catch (e) {{
                source = null;
                mode = '';
            }}
            if (!source) {{
                const captureFn = audioEl.captureStream || audioEl.webkitCaptureStream;
                if (captureFn) {{
                    try {{
                        const stream = captureFn.call(audioEl);
                        const tracks = stream && stream.getAudioTracks ? stream.getAudioTracks() : [];
                        if (tracks && tracks.length) {{
                            source = ctx.createMediaStreamSource(stream);
                            mode = 'capture';
                            source.connect(analyser);
                        }}
                    }} catch (e) {{
                        source = null;
                        mode = '';
                    }}
                }}
            }}
            if (!source) {{
                // Both paths refused. Remember the failure only once a real
                // stream is loaded, so the playing event still gets a chance
                // to connect it.
                const src = audioEl.currentSrc || audioEl.src || '';
                if (src.indexOf('/api/stream') !== -1) audioGraph = {{ failed: true }};
                return null;
            }}
            freqBins = new Uint8Array(analyser.frequencyBinCount);
            timeBins = new Uint8Array(analyser.fftSize);
            audioGraph = {{ analyser: analyser, source: source, mode: mode, gain: mixGain }};
            // Only a captureStream source needs an artificial pull. The element
            // source is pulled by its audible connection to the destination.
            if (mode === 'capture') attachSpectrumPull(ctx, analyser);
            captureSilenceFrames = 0;
            captureSawSignal = false;
            return audioGraph;
        }}

        // iPhone's captureStream() can hand back a silent track while the
        // element itself is loud. After a short run of silence, route the
        // element through the context so the analyser can see the mix.
        function promoteCaptureToElement() {{
            // iOS never builds a graph (see ensureAudioGraph), so there is no
            // capture source to promote and nothing to route.
            if (isIOSAudioDevice()) return;
            if (!audioCtx || !audioGraph || audioGraph.mode !== 'capture') return;
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl) return;
            try {{ if (audioGraph.source) audioGraph.source.disconnect(); }} catch (e) {{}}
            try {{
                const analyser = audioCtx.createAnalyser();
                analyser.fftSize = 2048;
                analyser.smoothingTimeConstant = 0.62;
                analyser.minDecibels = -90;
                analyser.maxDecibels = -20;
                const source = audioCtx.createMediaElementSource(audioEl);
                mixGain = audioCtx.createGain();
                mixGain.gain.value = mixLoudness();
                source.connect(analyser);
                analyser.connect(mixGain);
                mixGain.connect(audioCtx.destination);
                // The audible connection pulls this element source, so it
                // needs no muted ScriptProcessorNode (see attachSpectrumPull).
                freqBins = new Uint8Array(analyser.frequencyBinCount);
                timeBins = new Uint8Array(analyser.fftSize);
                audioGraph = {{ analyser: analyser, source: source, mode: 'element', gain: mixGain }};
                captureSilenceFrames = 0;
                captureSawSignal = false;
            }} catch (e) {{
                audioGraph = {{ failed: true, mode: 'element' }};
            }}
        }}

        function speakersFollowGain() {{
            return Boolean(mixGain && audioGraph && audioGraph.mode === 'element');
        }}

        function mixLoudness() {{
            if (streamMuted) return 0;
            return Math.max(0, Math.min(1, currentVolumePercent / 100));
        }}

        function setMixLoudness(level) {{
            const audioEl = document.getElementById('mobileAudioStream');
            const gain = Math.max(0, Math.min(1, Number(level) || 0));
            if (speakersFollowGain()) {{
                // Chrome applies element.volume on top of the gain node, so
                // the element stays at unity and the node carries the level.
                // iOS ignores element.volume entirely. element.muted would
                // also silence the source node and ignore the gain.
                try {{
                    if (audioEl) {{
                        audioEl.muted = false;
                        audioEl.volume = 1;
                    }}
                }} catch (e) {{}}
                try {{ mixGain.gain.value = gain; }} catch (e) {{}}
                return;
            }}
            if (!audioEl) return;
            try {{ audioEl.muted = gain <= 0; }} catch (e) {{}}
            try {{ audioEl.volume = gain; }} catch (e) {{}}
        }}

        function ensureAudibleGain() {{
            // A poll must not construct the context. iOS leaves that one
            // suspended, and routing the element through it silences the mix.
            if (speakersFollowGain()) return mixGain;
            if (!audioCtx) return null;
            if (!audioGraph || !audioGraph.analyser) ensureAudioGraph();
            if (audioGraph && audioGraph.mode === 'capture') promoteCaptureToElement();
            return speakersFollowGain() ? mixGain : null;
        }}

        function applyStreamVolume(percent, muted) {{
            const p = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
            currentVolumePercent = p;
            if (muted !== undefined && muted !== null) streamMuted = Boolean(muted);
            const pill = document.getElementById('volPill');
            if (pill) {{
                if (streamMuted) {{
                    pill.innerText = 'Vol: MUTED';
                    pill.title = '';
                }} else if (isIOSAudioDevice() && !speakersFollowGain() && phoneOwnsPlayback()) {{
                    // A native iOS element has no settable volume (Safari
                    // ignores audio.volume) and this page no longer routes it
                    // through a GainNode, so the hardware buttons are the
                    // level control. Say so instead of showing a percentage
                    // the tap cannot change.
                    pill.innerText = 'Vol: iOS';
                    pill.title = 'Use the iPhone volume buttons';
                }} else {{
                    pill.innerText = 'Vol: ' + p + '%';
                    pill.title = '';
                }}
            }}
            // iPhone Safari ignores audio.volume. Once the element is the
            // graph source, the gain node is what the speaker follows; on the
            // native iOS path only mute is available, so the pill says so.
            if ((audioCtx && needsPlaybackGesture()) || speakersFollowGain()) ensureAudibleGain();
            setMixLoudness(mixLoudness());
        }}

        function bumpVolume(delta) {{
            unlockAudioContext();
            applyStreamVolume(currentVolumePercent + Number(delta || 0), false);
        }}

        function toggleStreamMute() {{
            unlockAudioContext();
            applyStreamVolume(currentVolumePercent, !streamMuted);
        }}

        function bandsFromFrequency(freq) {{
            const rate = (audioCtx && audioCtx.sampleRate) || 48000;
            const nyquist = rate / 2;
            const binHz = nyquist / freq.length;
            const minHz = 32;
            const maxHz = Math.min(16000, nyquist * 0.96);
            for (let i = 0; i < BANDS; i++) {{
                const f0 = minHz * Math.pow(maxHz / minHz, i / BANDS);
                const f1 = minHz * Math.pow(maxHz / minHz, (i + 1) / BANDS);
                let b0 = Math.max(0, Math.floor(f0 / binHz));
                let b1 = Math.min(freq.length, Math.max(b0 + 1, Math.ceil(f1 / binHz)));
                let sum = 0;
                let peak = 0;
                let n = 0;
                for (let b = b0; b < b1; b++) {{
                    const v = freq[b];
                    sum += v;
                    if (v > peak) peak = v;
                    n++;
                }}
                const mean = n ? sum / n : 0;
                const norm = (0.65 * peak + 0.35 * mean) / 255;
                targetLevels[i] = Math.max(0.04, Math.min(0.98, Math.pow(norm, 0.7)));
            }}
        }}

        function bandsFromWaveform(time) {{
            const rate = (audioCtx && audioCtx.sampleRate) || 48000;
            const N = Math.min(512, time.length);
            const offset = time.length - N;
            const minHz = 40;
            const maxHz = Math.min(14000, rate * 0.45);
            for (let i = 0; i < BANDS; i++) {{
                const hz = minHz * Math.pow(maxHz / minHz, (i + 0.5) / BANDS);
                const coeff = 2 * Math.cos((2 * Math.PI * hz) / rate);
                let s1 = 0;
                let s2 = 0;
                for (let n = 0; n < N; n++) {{
                    const x = (time[offset + n] - 128) / 128;
                    const s0 = x + coeff * s1 - s2;
                    s2 = s1;
                    s1 = s0;
                }}
                const power = s1 * s1 + s2 * s2 - coeff * s1 * s2;
                const mag = Math.sqrt(Math.max(0, power)) / N;
                targetLevels[i] = Math.max(0.04, Math.min(0.98, mag * 3.4));
            }}
        }}

        function sampleLiveSpectrum() {{
            // A captureStream track can end when the element loads the next
            // track. Forget that the old file produced signal so the silent
            // fallback is allowed to promote the graph to an element source
            // instead of staying flat forever after Next.
            if (activeAudioFile !== spectrumSourceFile) {{
                spectrumSourceFile = activeAudioFile;
                captureSawSignal = false;
                captureSilenceFrames = 0;
            }}
            // The context is created in the tap. Finishing a missing graph
            // here is safe after that. Building a brand-new context from this
            // frame leaves it suspended on iPhone, and the mix goes silent
            // once the element is routed through it.
            if (audioCtx && !(audioGraph && audioGraph.analyser)) ensureAudioGraph();
            const graph = audioGraph;
            if (!graph || !graph.analyser) return false;
            if (audioCtx && audioCtx.state === 'suspended') {{
                try {{ audioCtx.resume(); }} catch (e) {{}}
                return false;
            }}
            const analyser = graph.analyser;
            if (!freqBins || freqBins.length !== analyser.frequencyBinCount) {{
                freqBins = new Uint8Array(analyser.frequencyBinCount);
            }}
            analyser.getByteFrequencyData(freqBins);
            let peak = 0;
            for (let i = 0; i < freqBins.length; i += 8) {{
                if (freqBins[i] > peak) peak = freqBins[i];
            }}
            if (peak >= 2) {{
                captureSilenceFrames = 0;
                captureSawSignal = true;
                bandsFromFrequency(freqBins);
                return true;
            }}
            if (!timeBins || timeBins.length !== analyser.fftSize) {{
                timeBins = new Uint8Array(analyser.fftSize);
            }}
            analyser.getByteTimeDomainData(timeBins);
            let energy = 0;
            for (let i = 0; i < timeBins.length; i += 4) {{
                const d = timeBins[i] - 128;
                energy += d * d;
            }}
            if (energy < 12) {{
                for (let i = 0; i < BANDS; i++) targetLevels[i] = 0.04;
                // Only a capture graph that has never seen the mix. A later
                // quiet passage must not tear the audio path down.
                if (!captureSawSignal && audioGraph && audioGraph.mode === 'capture' && phoneIsAudible()) {{
                    captureSilenceFrames += 1;
                    if (captureSilenceFrames === 90) promoteCaptureToElement();
                }}
                return false;
            }}
            captureSilenceFrames = 0;
            captureSawSignal = true;
            bandsFromWaveform(timeBins);
            return true;
        }}

        function energyAt(u) {{
            const clamped = Math.max(0, Math.min(1, u));
            if (freqBins && freqBins.length && audioCtx) {{
                const rate = audioCtx.sampleRate || 48000;
                const nyquist = rate / 2;
                const minHz = 32;
                const maxHz = Math.min(16000, nyquist * 0.96);
                const hz = minHz * Math.pow(maxHz / minHz, clamped);
                const bin = Math.max(0, Math.min(freqBins.length - 1, Math.round(hz / (nyquist / freqBins.length))));
                let sum = 0;
                let n = 0;
                for (let b = bin - 1; b <= bin + 1; b++) {{
                    if (b < 0 || b >= freqBins.length) continue;
                    sum += freqBins[b];
                    n++;
                }}
                const norm = n ? (sum / n) / 255 : 0;
                return Math.max(0, Math.min(1, Math.pow(norm, 0.72)));
            }}
            const idx = Math.max(0, Math.min(BANDS - 1, Math.round(clamped * (BANDS - 1))));
            return targetLevels[idx] || 0;
        }}

        function paintSpectrogramFrame() {{
            if (vizMode !== 'spectrogram' || !phoneIsAudible()) return;
            const canvas = document.getElementById('visSpectrogram');
            if (!canvas) return;
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            const w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
            const h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
            if (w < 2 || h < 2) return;
            if (Math.abs(canvas.width - w) > 1 || Math.abs(canvas.height - h) > 1) {{
                canvas.width = w;
                canvas.height = h;
                spectroBacking = null;
            }}
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            spectroShiftAcc += dpr;
            const shift = Math.floor(spectroShiftAcc);
            if (shift < 1) return;
            spectroShiftAcc -= shift;
            if (!spectroBacking) spectroBacking = document.createElement('canvas');
            if (spectroBacking.width !== w || spectroBacking.height !== h) {{
                spectroBacking.width = w;
                spectroBacking.height = h;
            }}
            const bctx = spectroBacking.getContext('2d');
            if (!bctx) return;
            bctx.clearRect(0, 0, w, h);
            bctx.drawImage(canvas, -shift, 0);
            bctx.clearRect(w - shift, 0, shift, h);
            const now = performance.now() / 1000;
            for (let y = 0; y < h; y++) {{
                const u = 1 - (y + 0.5) / h;
                const energy = energyAt(u);
                if (energy < 0.05) continue;
                const hue = (now * 40 + u * 220) % 360;
                const light = 22 + energy * 58;
                const alpha = Math.min(1, 0.28 + energy * 0.85);
                bctx.fillStyle = 'hsla(' + hue.toFixed(1) + ',' +
                    (62 + energy * 35).toFixed(0) + '%,' +
                    light.toFixed(0) + '%,' + alpha.toFixed(3) + ')';
                bctx.fillRect(w - shift, y, shift, 1);
                if (energy > 0.84) {{
                    bctx.fillStyle = 'rgba(255,255,255,' +
                        Math.min(0.95, (energy - 0.84) * 5).toFixed(3) + ')';
                    bctx.fillRect(w - shift, y, Math.max(1, shift - 1), 1);
                }}
            }}
            ctx.clearRect(0, 0, w, h);
            ctx.drawImage(spectroBacking, 0, 0);
        }}

        function applyVisualizerMode(mode) {{
            vizMode = mode === 'spectrogram' ? 'spectrogram' : 'bars';
            const spectro = vizMode === 'spectrogram';
            const box = document.getElementById('visContainer');
            const caption = document.getElementById('visCaption');
            const canvas = document.getElementById('visSpectrogram');
            if (box) {{
                box.classList.toggle('mode-spectrogram', spectro);
                box.setAttribute('aria-pressed', spectro ? 'true' : 'false');
                box.setAttribute('aria-label', spectro
                    ? 'Stream of Frequency, Dynamic Spectrogram. Press to show the equaliser.'
                    : 'Equaliser. Press to show Stream of Frequency, Dynamic Spectrogram.');
            }}
            if (caption) {{
                caption.textContent = spectro
                    ? 'Stream of Frequency (Dynamic Spectrogram)'
                    : 'Equaliser';
            }}
            const barsBtn = document.getElementById('vizEffectBars');
            const spectroBtn = document.getElementById('vizEffectSpectro');
            if (barsBtn) {{
                barsBtn.classList.toggle('active', !spectro);
                barsBtn.setAttribute('aria-pressed', spectro ? 'false' : 'true');
            }}
            if (spectroBtn) {{
                spectroBtn.classList.toggle('active', spectro);
                spectroBtn.setAttribute('aria-pressed', spectro ? 'true' : 'false');
            }}
            if (canvas) {{
                canvas.setAttribute('aria-hidden', spectro ? 'false' : 'true');
                if (spectro) {{
                    spectroBacking = null;
                    const c = canvas.getContext('2d');
                    if (c) c.clearRect(0, 0, canvas.width, canvas.height);
                }}
            }}
            try {{ localStorage.setItem('mpVizMode', vizMode); }} catch (e) {{}}
        }}

        let lastVizToggleAt = 0;

        function toggleVisualizerMode(ev) {{
            if (ev && ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
            if (ev && ev.key === ' ') ev.preventDefault();
            // touchend and click both arrive for one tap. Counting both
            // flipped the view and flipped it straight back.
            if (ev && (ev.type === 'touchend' || ev.type === 'click')) {{
                const now = (typeof performance !== 'undefined') ? performance.now() : Date.now();
                if (now - lastVizToggleAt < 450) return;
                lastVizToggleAt = now;
            }}
            // A press on the drawing moves between the only two effects.
            applyVisualizerMode(vizMode === 'spectrogram' ? 'bars' : 'spectrogram');
        }}

        // Finger or mouse. Any other name is ignored, so a third effect
        // cannot be selected.
        function selectVisualizerMode(ev, mode) {{
            if (ev) {{
                if (ev.stopPropagation) ev.stopPropagation();
                if (ev.type === 'touchend' && ev.cancelable) ev.preventDefault();
            }}
            if (ev && (ev.type === 'touchend' || ev.type === 'click')) {{
                const now = (typeof performance !== 'undefined') ? performance.now() : Date.now();
                if (now - lastVizToggleAt < 450) return;
                lastVizToggleAt = now;
            }}
            if (mode !== 'bars' && mode !== 'spectrogram') return;
            applyVisualizerMode(mode);
        }}

        function animLoop() {{
            const attack = 0.52;
            const decay = 0.88;
            const gravity = 0.0022;

            if (document.hidden) {{
                // A hidden tab paints nothing; keeping the 60 fps loop alive only
                // burns battery and competes with the poll timers when Safari
                // throttles background work.
                requestAnimationFrame(animLoop);
                return;
            }}

            // The element is the clock. Waiting for the two-second poll left
            // the bars up after a pause and flat while the mix was playing.
            if (phoneIsAudible()) {{
                isPlayerActive = true;
                sampleLiveSpectrum();
            }} else if (audioStreamEngaged) {{
                isPlayerActive = false;
            }}

            if (vizMode === 'spectrogram') paintSpectrogramFrame();

            cacheBarEls();
            const box = document.getElementById('visContainer');
            const span = barEls[0] && barEls[0].parentElement
                ? barEls[0].parentElement.clientHeight
                : (box ? Math.max(8, box.clientHeight - 8) : 48);
            // Pixels, not percentages. On iPhone a percentage height inside
            // this flex row does not resolve, so the bars stayed at their
            // initial size while the mix played.
            for (let i = 0; i < BANDS; i++) {{
                const bar = barEls[i];
                const peakEl = peakEls[i];
                if (!bar) continue;

                if (isPlayerActive) {{
                    if (targetLevels[i] > currentLevels[i]) {{
                        currentLevels[i] += (targetLevels[i] - currentLevels[i]) * attack;
                    }} else {{
                        currentLevels[i] = Math.max(0.04, currentLevels[i] * decay);
                    }}

                    if (currentLevels[i] >= currentPeaks[i]) {{
                        currentPeaks[i] = currentLevels[i];
                        peakVels[i] = 0.0;
                    }} else {{
                        peakVels[i] += gravity;
                        currentPeaks[i] = Math.max(currentLevels[i], currentPeaks[i] - peakVels[i]);
                    }}
                }} else {{
                    currentLevels[i] = Math.max(0.04, currentLevels[i] * 0.82);
                    currentPeaks[i] = Math.max(0.04, currentPeaks[i] * 0.82);
                }}

                if (vizMode !== 'bars') continue;
                const barPx = Math.max(2, Math.round(currentLevels[i] * span));
                const peakPx = Math.max(barPx, Math.round(currentPeaks[i] * span));
                bar.style.height = barPx + 'px';
                if (peakEl) peakEl.style.bottom = Math.max(0, peakPx - 2) + 'px';
            }}
            requestAnimationFrame(animLoop);
        }}
        requestAnimationFrame(animLoop);

        function paintLocalProgress(audioEl) {{
            if (!audioEl || scrubbing) return;
            if (!audioStreamConfigured) return;
            if (!isFinite(audioEl.duration) || audioEl.duration <= 0) return;
            currentDurationSec = audioEl.duration;
            const pos = Math.max(0, audioEl.currentTime || 0);
            const posEl = document.getElementById('posTime');
            const durEl = document.getElementById('durTime');
            const bar = document.getElementById('progressBar');
            if (posEl) posEl.innerText = formatClock(pos);
            if (durEl) durEl.innerText = formatClock(audioEl.duration);
            if (bar) {{
                const pct = (pos / audioEl.duration) * 100;
                bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
            }}
            const scrub = document.getElementById('progressBg');
            if (scrub) {{
                const pctNow = Math.round((pos / audioEl.duration) * 100);
                scrub.setAttribute('aria-valuenow', String(pctNow));
                scrub.setAttribute('aria-valuetext', formatClock(pos) + ' of ' + formatClock(audioEl.duration));
            }}
        }}

        function progressFraction(ev, el) {{
            const rect = el.getBoundingClientRect();
            if (!rect.width) return 0;
            const x = Number(ev.clientX) - rect.left;
            return Math.max(0, Math.min(1, x / rect.width));
        }}

        function paintScrub(fraction) {{
            const bar = document.getElementById('progressBar');
            if (bar) bar.style.width = (Math.max(0, Math.min(1, fraction)) * 100) + '%';
            if (currentDurationSec > 0) {{
                const posEl = document.getElementById('posTime');
                if (posEl) posEl.innerText = formatClock(fraction * currentDurationSec);
            }}
        }}

        function commitScrub(fraction) {{
            if (!currentDurationSec || currentDurationSec <= 0) return;
            clearHostAlign();
            const seekPos = Math.max(0, Math.min(1, fraction)) * currentDurationSec;
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl && (isFinite(audioEl.duration) || audioStreamEngaged)) {{
                seekAudioTo(seekPos);
            }}
            sendAction('seek_to', {{ position: seekPos.toFixed(1) }});
        }}

        function bindScrubber() {{
            const bg = document.getElementById('progressBg');
            if (!bg || bg.dataset.bound) return;
            bg.dataset.bound = '1';
            bg.addEventListener('pointerdown', (ev) => {{
                if (ev.button != null && ev.button > 0) return;
                scrubbing = true;
                bg.classList.add('scrubbing');
                try {{ bg.setPointerCapture(ev.pointerId); }} catch (e) {{}}
                paintScrub(progressFraction(ev, bg));
                ev.preventDefault();
            }});
            bg.addEventListener('pointermove', (ev) => {{
                if (!scrubbing) return;
                paintScrub(progressFraction(ev, bg));
            }});
            const finish = (ev) => {{
                if (!scrubbing) return;
                scrubbing = false;
                bg.classList.remove('scrubbing');
                commitScrub(progressFraction(ev, bg));
            }};
            bg.addEventListener('pointerup', finish);
            bg.addEventListener('pointercancel', () => {{
                scrubbing = false;
                bg.classList.remove('scrubbing');
            }});
        }}

        function seekClick(e) {{
            const bg = e.currentTarget || document.getElementById('progressBg');
            if (!bg) return;
            commitScrub(progressFraction(e, bg));
        }}

        function paintFavorite(on) {{
            const btn = document.getElementById('btnFavorite');
            if (!btn) return;
            btn.classList.toggle('on', Boolean(on));
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            btn.innerText = on ? '♥' : '♡';
            btn.setAttribute('aria-label', on ? 'Remove from favourites' : 'Add to favourites');
        }}

        function paintCover(url) {{
            const img = document.getElementById('trackCover');
            if (!img) return;
            const next = url || '';
            if (!next) {{
                if (!img.hidden) {{
                    img.hidden = true;
                    img.removeAttribute('src');
                }}
                return;
            }}
            if (img.getAttribute('src') === next && !img.hidden) return;
            img.hidden = false;
            img.src = next;
        }}

        function toggleFavorite(path) {{
            const file = path || activeAudioFile;
            if (!file) return;
            const btn = document.getElementById('btnFavorite');
            if (!path && btn) paintFavorite(!btn.classList.contains('on'));
            sendAction('toggle_favorite', {{ path: file }});
        }}

        const SLEEP_STEPS = [0, 15, 30, 60, 90];
        let sleepMinutes = 0;
        let sleepUntil = 0;
        let sleepTimer = 0;
        let sleepPaintTimer = 0;

        function paintSleep() {{
            const btn = document.getElementById('btnSleep');
            const txt = document.getElementById('sleepText');
            if (!btn || !txt) return;
            if (!sleepUntil || Date.now() >= sleepUntil) {{
                txt.innerText = 'OFF';
                btn.classList.remove('active');
                btn.setAttribute('aria-label', 'Sleep timer off');
                return;
            }}
            const left = Math.max(1, Math.ceil((sleepUntil - Date.now()) / 60000));
            txt.innerText = left + 'm';
            btn.classList.add('active');
            btn.setAttribute('aria-label', 'Sleep timer, ' + left + ' minutes left');
        }}

        function scheduleSleepPaint() {{
            clearInterval(sleepPaintTimer);
            sleepPaintTimer = 0;
            if (!sleepUntil) return;
            sleepPaintTimer = setInterval(() => {{
                if (!sleepUntil || Date.now() >= sleepUntil) {{
                    clearInterval(sleepPaintTimer);
                    sleepPaintTimer = 0;
                    return;
                }}
                paintSleep();
            }}, 30000);
        }}

        function armSleep(minutes) {{
            clearTimeout(sleepTimer);
            sleepTimer = 0;
            sleepMinutes = Number(minutes) || 0;
            if (!sleepMinutes) {{
                sleepUntil = 0;
                try {{ sessionStorage.removeItem('mp-sleep-until'); }} catch (e) {{}}
                paintSleep();
                scheduleSleepPaint();
                return;
            }}
            sleepUntil = Date.now() + sleepMinutes * 60000;
            try {{ sessionStorage.setItem('mp-sleep-until', String(sleepUntil)); }} catch (e) {{}}
            sleepTimer = setTimeout(fireSleep, sleepMinutes * 60000);
            paintSleep();
            scheduleSleepPaint();
        }}

        function cycleSleep() {{
            const idx = SLEEP_STEPS.indexOf(sleepMinutes);
            const next = SLEEP_STEPS[(idx < 0 ? 0 : idx + 1) % SLEEP_STEPS.length];
            armSleep(next);
        }}

        function fireSleep() {{
            sleepUntil = 0;
            sleepMinutes = 0;
            clearInterval(sleepPaintTimer);
            sleepPaintTimer = 0;
            try {{ sessionStorage.removeItem('mp-sleep-until'); }} catch (e) {{}}
            paintSleep();
            const audioEl = document.getElementById('mobileAudioStream');
            if (!(audioEl && phoneOwnsPlayback() && !audioEl.paused)) return;
            listenerPaused = true;
            try {{ audioEl.pause(); }} catch (e) {{}}
            sendAction('pause');
            const note = document.getElementById('autoplayNotice');
            if (note) {{
                note.style.display = 'block';
                note.className = 'autoplay-notice';
                note.innerText = '🌙 Sleep timer — playback paused on this phone';
            }}
        }}

        function restoreSleep() {{
            let until = 0;
            try {{ until = Number(sessionStorage.getItem('mp-sleep-until') || 0); }} catch (e) {{}}
            if (!(until > Date.now() + 1000)) return;
            sleepUntil = until;
            sleepMinutes = Math.max(1, Math.round((until - Date.now()) / 60000));
            sleepTimer = setTimeout(fireSleep, until - Date.now());
            paintSleep();
            scheduleSleepPaint();
        }}

        function cycleRepeat() {{
            const order = ['off', 'all', 'one'];
            const cur = order.indexOf(currentRepeatMode);
            const next = order[(cur < 0 ? 1 : cur + 1) % order.length];
            updateRepeatUI(next);
            sendAction('repeat');
        }}

        function updateShuffleUI(isShuf) {{
            const btn = document.getElementById('btnShuffle');
            const txt = document.getElementById('shufText');
            const badge = document.getElementById('shuffleBadge');
            if (btn && txt) {{
                btn.classList.toggle('active', isShuf);
                txt.innerText = isShuf ? 'ON' : 'OFF';
            }}
            if (badge) {{
                badge.innerText = isShuf ? '🔀 SHUFFLE ON' : '🔀 SHUFFLE OFF';
                badge.classList.toggle('active', isShuf);
            }}
        }}

        function updateRepeatUI(repMode) {{
            currentRepeatMode = String(repMode || 'all').toLowerCase();
            const btn = document.getElementById('btnRepeat');
            const txt = document.getElementById('repText');
            if (txt) txt.innerText = currentRepeatMode.toUpperCase();
            if (btn) btn.classList.toggle('active', currentRepeatMode !== 'off');
        }}

        // DJ pitch, matching the desk player: percent moves speed and pitch
        // together. Browsers default preservesPitch to true, which holds the
        // tone still while only the speed changes, so the buttons sound dead.
        function applyStreamPitch(percent) {{
            const p = Math.max(-20, Math.min(20, Math.round(Number(percent) || 0)));
            currentPitchPercent = p;
            const pill = document.getElementById('pitchPill');
            if (pill) pill.innerText = 'Pitch: ' + (p > 0 ? '+' : '') + p + '%';
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl) return;
            try {{
                audioEl.preservesPitch = false;
                audioEl.mozPreservesPitch = false;
                audioEl.webkitPreservesPitch = false;
                audioEl.playbackRate = 1 + (p / 100);
            }} catch (e) {{}}
        }}

        function bumpPitch(delta) {{
            applyStreamPitch(currentPitchPercent + Number(delta || 0));
        }}

        function formatClock(seconds) {{
            const total = Math.max(0, Math.floor(Number(seconds) || 0));
            const h = Math.floor(total / 3600);
            const m = Math.floor((total % 3600) / 60);
            const s = total % 60;
            return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        }}

        async function fetchTheme() {{
            try {{
                const res = await fetch('/api/theme');
                if (!res.ok) throw new Error('theme ' + res.status);
                const th = await res.json();
                if (th && th.id && th.id !== currentThemeId) {{
                    applyPalette(th);
                    loadThemeList();
                }}
            }} catch (e) {{
                throw e;
            }}
        }}

        async function fetchEq() {{
            try {{
                const res = await fetch(apiUrl('/api/eq'));
                if (!res.ok) throw new Error('eq ' + res.status);
                const data = await res.json();
                // The phone is the only audio output, so the visualizer follows
                // its element. The server cannot observe audio itself.
                const audioEl = document.getElementById('mobileAudioStream');
                // A non-iOS phone reads its own AnalyserNode, so the server's
                // levels must not overwrite that live spectrum. On iOS the
                // server decodes the file instead, because the native media
                // element cannot be analysed without breaking background play.
                if (!phoneIsAudible()) {{
                    isPlayerActive = audioElIsPlaying();
                    if (data.levels && data.levels.length) {{
                        for (let i = 0; i < BANDS && i < data.levels.length; i++) {{
                            targetLevels[i] = isPlayerActive ? Math.max(0.04, Math.min(1.0, data.levels[i])) : 0.04;
                        }}
                    }}
                }} else if (isIOSAudioDevice()) {{
                    // iOS plays the native element, so no AnalyserNode can hear
                    // it (see ensureAudioGraph). Follow the server's levels
                    // instead of leaving the bars frozen on the first frame.
                    isPlayerActive = true;
                    if (data.levels && data.levels.length) {{
                        for (let i = 0; i < BANDS && i < data.levels.length; i++) {{
                            targetLevels[i] = Math.max(0.04, Math.min(1.0, data.levels[i]));
                        }}
                    }}
                }} else {{
                    isPlayerActive = true;
                }}
                // While mirroring a live host player, the phone element is
                // idle until the listener opts in, so its clock would sit at
                // zero and contradict the title. Follow the host clock instead
                // until the phone is actually the one playing.
                const phonePlaying = audioEl && !audioEl.paused && !audioEl.ended && audioStreamConfigured;
                const hasLocalClock = !hostPlayerActive && audioEl &&
                    isFinite(audioEl.duration) && audioEl.duration > 0 && audioStreamConfigured;
                const shownPos = hasLocalClock ? audioEl.currentTime : Number(data.position_sec || 0);
                const shownDur = hasLocalClock ? audioEl.duration : Number(data.duration_sec || 0);
                if (hostPlayerActive && !phonePlaying) isPlayerActive = false;

                if (scrubbing) {{
                    // A finger is on the bar. The poll must not yank it back.
                }} else if (hasLocalClock) {{
                    document.getElementById('posTime').innerText = formatClock(shownPos);
                    document.getElementById('durTime').innerText = formatClock(shownDur);
                    const pct = shownDur > 0 ? (shownPos / shownDur) * 100 : 0;
                    document.getElementById('progressBar').style.width = Math.max(0, Math.min(100, pct)) + '%';
                    currentDurationSec = shownDur;
                }} else {{
                    if (data.position) document.getElementById('posTime').innerText = data.position;
                    if (data.duration) document.getElementById('durTime').innerText = data.duration;
                    if (data.progress_pct !== undefined) {{
                        document.getElementById('progressBar').style.width = Math.max(0, Math.min(100, data.progress_pct)) + '%';
                    }}
                }}
                if (data.position_sec !== undefined) {{
                    // The server only records where streaming last left off.
                    // The browser clock is authoritative while audio plays, so
                    // never seek from this poll - that is what used to trap
                    // playback in a repeating fragment.
                    lastServerPos = Number(data.position_sec);
                }}
                if (data.volume !== undefined && duckedMixVolume === null) {{
                    applyStreamVolume(data.volume, data.muted);
                }}
                if (data.pitch_percent !== undefined) applyStreamPitch(data.pitch_percent);
                updateShuffleUI(Boolean(data.shuffle));
                if (data.repeat) updateRepeatUI(data.repeat);
            }} catch (e) {{
                // Let the scheduler back this endpoint off instead of retrying
                // an unreachable service at full rate.
                throw e;
            }}
        }}

        // The reason the host cannot stream right now, phrased for a listener.
        // Empty when the stream is fine.
        function streamUnavailableDetail(data) {{
            const state = (data && data.autoplay) || {{}};
            // `disabled` only means the host did not *arm the stream by itself*
            // at boot (WEB_COMPANION_AUTOPLAY=off). The queue is still built and
            // /api/stream still serves it, so a phone that taps Engage plays
            // normally. Reporting it as "streaming is disabled" put a red
            // failure banner over a working stream, which reads as "the button
            // does not work" even when it just started the mix.
            if (state.status === 'disabled') return '';
            // "ready" means the queue resolved; a library note such as
            // "Music library offline - streaming Mix Archive" is informational
            // then, not a reason the tap would fail.
            if (state.status === 'ready' || state.status === 'playing') return '';
            if (data && data.library_detail) {{
                return data.library_detail;
            }}
            if (state.detail) return state.detail;
            return '';
        }}

        function updateAutoplayNotice(state) {{
            const el = document.getElementById('autoplayNotice');
            if (!el) return;
            if (!state || !state.status || state.status === 'pending') {{
                el.style.display = 'none';
                return;
            }}
            const labels = {{
                ready: '🎧 Stream ready — tap to listen',
                playing: '🎧 Stream ready — tap to listen',
                empty_playlist: '⏹ No mixes found in the archive',
                // Not a fault: the host simply did not start the queue on its
                // own. The queue is still built and /api/stream still serves it,
                // so a tap on Engage plays normally. Painting a failure banner
                // over a working player is what made the button look broken.
                disabled: '🎧 Stream ready — tap to listen',
                player_unavailable: '⚠ Stream source unavailable',
                player_unreachable: '⚠ Stream source unreachable',
                playback_unverified: '⚠ Stream not confirmed',
            }};
            el.innerText = labels[state.status] || state.status;
            // A phone has no hover, so the cause must be on screen, not only in
            // the tooltip: "No mixes found" is wrong when the reason is a macOS
            // privacy block on the archive volume.
            if (state.detail && state.status !== 'ready' && state.status !== 'playing'
                    && state.status !== 'disabled') {{
                el.innerText += ' — ' + state.detail;
            }}
            el.className = 'autoplay-notice ' + (
                (state.status === 'ready' || state.status === 'playing'
                    || state.status === 'disabled') ? 'ok' :
                (state.status === 'empty_playlist') ? '' : 'warn'
            );
            el.style.display = 'block';
            if (state.detail) el.title = state.detail;
        }}

        function showRestartNotice(message, kind) {{
            const el = document.getElementById('restartNotice');
            const text = document.getElementById('restartNoticeText');
            const nextKind = kind || 'restart';
            // One steady banner per skip run. Re-showing it for every dead file
            // and hiding it again on the next status poll is exactly the
            // flicker this notice used to produce.
            if (nextKind === 'skip' && restartNoticeShown
                    && restartNoticeKind === 'skip') return;
            if (text && message) text.innerText = message;
            if (el) el.classList.add('show');
            restartNoticeShown = true;
            restartNoticeKind = nextKind;
        }}

        function hideRestartNotice() {{
            const el = document.getElementById('restartNotice');
            if (el) el.classList.remove('show');
            restartNoticeShown = false;
            restartNoticeKind = "";
        }}

        // The tray tells us before it stops the service; show that reason now
        // rather than waiting for the connection to drop.
        function observeAnnouncement(message, at) {{
            if (!message) return;
            const key = message + '|' + String(at || '');
            if (window.__lastAnnouncementKey === key) return;
            window.__lastAnnouncementKey = key;
            showRestartNotice(message + ' — reconnecting…');
        }}

        function noteServerGone() {{
            if (restartNoticeShown) return;
            showRestartNotice('MP Audio Player (Web) is restarting — reconnecting…');
        }}

        function reloadForFreshInstance() {{
            try {{
                window.location.reload();
            }} catch (e) {{}}
        }}

        // A restarted server is a new instance, so its stream URL and position
        // are fresh. Re-point the <audio> element in place; a full reload
        // would drop the user's tap gesture and force a second "engage stream"
        // tap, which is exactly what they do not need mid-set.
        function reattachToRestartedServer() {{
            const audioEl = document.getElementById('mobileAudioStream');
            // The element is about to be paused on purpose. Remember that the
            // listener still wants sound, or the poll reloads the mix and
            // leaves it stopped. That is the drop that does not come back.
            window.__resumeAfterReattach = audioStreamEngaged && !listenerPaused;
            audioStreamConfigured = false;
            streamAutoStarted = false;
            activeAudioFile = "";
            lastServerPos = -1.0;
            lastServerResume = 0.0;
            // The per-file one-shot guard is cleared so a genuine restart can
            // recover again, but the attempt counter is deliberately kept: it
            // is what bounds recovery for a file that will never play. Only a
            // successful play (or a track change) resets it.
            mediaRecoveryUsed = false;
            lastMediaErrorAt = 0;
            // A restart is not a skip: the element was torn down on purpose, so
            // there is no "resume the next mix" intent to carry across polls.
            // The files this phone wrote off stay written off.
            skipResumePending = false;
            if (audioEl) {{
                try {{
                    audioEl.pause();
                }} catch (e) {{}}
                audioEl.removeAttribute('src');
                try {{
                    audioEl.load();
                }} catch (e) {{}}
            }}
            fetchStatus();
            fetchMiniPlaylist();
            fetchEq();
        }}

        async function fetchStatus() {{
            try {{
                // A backgrounded iPhone delays setTimeout, so the sleep timer
                // is also checked on this poll. The background poll is what
                // still runs while the screen is locked.
                if (sleepUntil && Date.now() >= sleepUntil) fireSleep();
                const res = await fetch(apiUrl('/api/status'));
                const data = await res.json();
                statusFailureCount = 0;

                observeAnnouncement(data.announcement, data.announcement_at);

                // A new instance id means Stop/Start ran while this page was
                // open. Resume on the new instance without a reload so the
                // phone keeps playing after one reconnect.
                if (data.instance) {{
                    if (serverInstance && data.instance !== serverInstance) {{
                        serverInstance = data.instance;
                        showRestartNotice('MP Audio Player (Web) has been restarted — reconnecting…');
                        reattachToRestartedServer();
                        setTimeout(() => hideRestartNotice(), 4000);
                        return;
                    }}
                    serverInstance = data.instance;
                }}
                // Only a transient restart notice is cleared by a poll. A skip
                // notice belongs to the run and stays until a mix really plays,
                // so it must not blink off between two failed files.
                if (restartNoticeShown && restartNoticeKind !== 'skip') hideRestartNotice();

                document.getElementById('trackTitle').innerText = data.title || "No Mix Playing";
                document.getElementById('trackArtist').innerText = data.album ? (data.artist + " • " + data.album) : (data.artist || "MPlanetarian");
                // Keep the lock screen in step with the dashboard. iOS shows
                // this metadata in Control Center and on the lock screen while
                // the mix plays in the background.
                const nextTitle = data.title || '';
                const nextArtist = data.artist || '';
                const nextAlbum = data.album || '';
                const nextArt = (data.has_artwork && data.active_file)
                    ? (location.origin + '/api/artwork?file=' + encodeURIComponent(data.active_file))
                    : (location.origin + '/icon.png');
                if (nextTitle !== trackMeta.title || nextArtist !== trackMeta.artist ||
                    nextAlbum !== trackMeta.album || nextArt !== trackMeta.artwork) {{
                    trackMeta = {{
                        title: nextTitle,
                        artist: nextArtist,
                        album: nextAlbum,
                        artwork: nextArt,
                    }};
                    updateMediaSessionMetadata();
                }}
                paintCover(data.has_artwork ? nextArt : '');
                if (data.favorite !== undefined) paintFavorite(Boolean(data.favorite));

                const badge = document.getElementById('playBadge');
                const state = (data.state || "stopped").toLowerCase();
                badge.innerText = state.toUpperCase();
                
                if (state === 'playing') {{
                    badge.style.background = 'var(--badge-bg)';
                    badge.style.color = 'var(--badge-color)';
                    badge.style.borderColor = 'var(--badge-color)';
                }} else {{
                    badge.style.background = 'rgba(255, 170, 0, 0.15)';
                    badge.style.color = '#ffaa00';
                    badge.style.borderColor = '#ffaa00';
                }}
                // The badge reports the daemon; the transport button reports
                // what will happen when it is tapped. Those differ on a phone
                // whose element was paused by the OS, so the button is derived
                // from media events and only falls back to the daemon state
                // when the desktop player owns the audio. It is painted once
                // host ownership is known, further down.
                lastServerState = state;

                document.getElementById('sourceBadge').innerText = (data.player || "None").toUpperCase();
                if (data.volume !== undefined && duckedMixVolume === null) {{
                    applyStreamVolume(data.volume, data.muted);
                }}
                if (data.pitch_percent !== undefined) applyStreamPitch(data.pitch_percent);
                // The element knows its own length once metadata has arrived.
                // Overwriting that with the server clock aimed a scrub at the
                // previous mix after a track change.
                const clockEl = document.getElementById('mobileAudioStream');
                const elementHasClock = Boolean(clockEl && audioStreamConfigured
                    && isFinite(clockEl.duration) && clockEl.duration > 0);
                if (!elementHasClock) currentDurationSec = Number(data.duration_sec) || 0;
                // What bounds the auto-skip run, and the host's own verdict that
                // every entry has already failed (e.g. another phone proved it,
                // or this page was reloaded mid-run).
                queueLength = Number(data.queue_len) || queueLength;
                serverUnplayableCount = Number(data.unplayable_count) || 0;
                if (data.playback_exhausted) {{
                    showPlaybackExhaustedNotice();
                }} else if (exhaustedNoticeShown) {{
                    // The host found something playable again (a rescan, a
                    // repaired file); forget the old verdict so the next failure
                    // can start a fresh run.
                    exhaustedNoticeShown = false;
                    consecutiveSkips = 0;
                    skipRunStartAt = 0;
                }}
                updateAutoplayNotice(data.autoplay);
                // The host builds its queue at boot and again after a library
                // switch. `pending` means the automatic attempt has no file to
                // ask for yet, so the gate stays down and the next poll with a
                // file starts the stream instead of asking for a tap.
                hostAutoplayStatus = (data.autoplay && data.autoplay.status) || "";
                streamUnavailableReason = streamUnavailableDetail(data);
                musicLibraryOffline = Boolean(data.music_offline);
                syncStreamOverlayDetail();

                // A running host player means the workstation is the output.
                // Mirror its file for an opt-in listen, but never autoplay.
                const wasHostActive = hostPlayerActive;
                hostPlayerActive = Boolean(data.host_player);
                if (hostPlayerActive) {{
                    lastHostPositionSec = Number(data.position_sec) || 0;
                    lastHostPositionAt = performance.now();
                }} else {{
                    lastHostPositionSec = 0;
                    if (wasHostActive) hostPlayerFile = "";
                }}

                if (data.active_file && activeAudioFile !== data.active_file) {{
                    // Require the new file to be named twice in a row before the
                    // element is re-pointed. A real track change keeps naming the
                    // new file; a flapping cursor alternates A, B, A, B and never
                    // settles, which used to reload the player every poll. This
                    // costs one poll (2 s) on a genuine change and makes a bounce
                    // completely inert. The very first file needs no confirmation:
                    // there is nothing yet to flap against, and delaying it would
                    // just slow the initial start.
                    if (activeAudioFile) {{
                        if (data.active_file === pendingFile) {{
                            pendingFileCount += 1;
                        }} else {{
                            pendingFile = data.active_file;
                            pendingFileCount = 1;
                        }}
                        if (pendingFileCount < FILE_CHANGE_CONFIRMATIONS) {{
                            fileFlapDetected = true;
                            return;
                        }}
                        // Confirmed. Now reject an oscillation: A -> B -> A is a
                        // flapping cursor, not two track changes. Latch it, so the
                        // page stops following changes until the server has named
                        // one file steadily for FILE_FLAP_COOLDOWN_MS. Without the
                        // latch an alternating server still restarted playback
                        // every few polls, just more slowly.
                        const nowMs = performance.now();
                        if (data.active_file === secondLastFile) {{
                            fileFlapDetected = true;
                            fileFollowFrozenUntil = nowMs + FILE_FLAP_COOLDOWN_MS;
                            pendingFile = "";
                            pendingFileCount = 0;
                            return;
                        }}
                        if (nowMs < fileFollowFrozenUntil) {{
                            fileFlapDetected = true;
                            pendingFile = "";
                            pendingFileCount = 0;
                            return;
                        }}
                        secondLastFile = activeAudioFile || "";
                        fileFollowFrozenUntil = 0;
                    }}
                    pendingFile = "";
                    pendingFileCount = 0;
                    // A mix that just ran to its end still counts as "was
                    // playing": the element has already flipped to ended, so
                    // audioElIsPlaying() is false by the time the server names
                    // the next file. Without the `ended` term the queue
                    // advanced but the new mix sat paused, which read as the
                    // +30 button stopping the music at the end of a track.
                    const prevEl = document.getElementById('mobileAudioStream');
                    const wasPlaying = audioStreamEngaged &&
                        (audioElIsPlaying() || Boolean(prevEl && prevEl.ended));
                    // A skip is also a reason to keep playing: the listener did
                    // not press pause, the file simply failed. Without this the
                    // replacement mix loaded paused and the skip looked like a
                    // stop.
                    const resumeAfterSkip = skipResumePending && !hostPlayerActive;
                    skipResumePending = false;
                    // A reconnect clears the element before this poll, so
                    // "was playing" is already false. The flag remembers that
                    // the listener had not pressed Stop.
                    const resumeAfterReattach = Boolean(window.__resumeAfterReattach);
                    window.__resumeAfterReattach = false;
                    const keepListening = audioStreamEngaged && !listenerPaused
                        && (!hostPlayerActive || followStream);
                    activeAudioFile = data.active_file;
                    fileFlapDetected = false;
                    audioStreamConfigured = false;
                    lastServerPos = -1.0;
                    lastServerResume = Number(data.resume_sec) || 0;
                    // A different file is a clean slate: whatever failed before
                    // must not count against this one. Without this a bad track
                    // would leave every later track on its last recovery
                    // attempt and the dashboard would refuse to try them.
                    if (mediaRecoveryFile !== activeAudioFile) {{
                        mediaRecoveryAttempts = 0;
                        mediaRecoveryUsed = false;
                    }}
                    if (quickEndFile !== activeAudioFile) {{
                        quickEndFile = "";
                        quickEndRestarts = 0;
                    }}
                    // A file this phone has already written off must never be
                    // loaded again, even if the server names it (a stale cache,
                    // or a rescan that has not happened yet).
                    if (unplayableFiles.has(activeAudioFile)) {{
                        sendAction('track_failed', {{ path: activeAudioFile }});
                    }}
                    const audioEl = document.getElementById('mobileAudioStream');
                    const continueOnPhone = wasPlaying || resumeAfterSkip || resumeAfterReattach || keepListening;
                    // On an iPhone the first load has to happen inside the
                    // Engage tap. Pointing the element at the file here starts
                    // a load outside any gesture, and the tap's play() is then
                    // rejected.
                    if (audioEl && (!needsPlaybackGesture() || continueOnPhone)) {{
                        audioEl.src = '/api/stream?file=' + encodeURIComponent(data.active_file);
                        audioStreamConfigured = true;
                        // A track change is followed by the same rule as the
                        // first engage: resume only from a safe point, never
                        // from the tail of a file that ran unattended.
                        const resumeAt = hostPlayerActive
                            ? (Number(data.position_sec) || Number(data.resume_sec) || 0)
                            : (Number(data.resume_sec) || 0);
                        if (resumeAt > 0.5) {{
                            // Wait for a timeline before seeking, otherwise the
                            // assignment is discarded by the new source load.
                            // A live desk player uses its real position, not
                            // the resume point that turns the tail of a file
                            // into 0.
                            alignPlaybackTo(resumeAt, hostPlayerActive);
                        }}
                        audioEl.load();
                        applyStreamPitch(currentPitchPercent);
                        // Keep playing across a track change and a reconnect.
                        // Stop is the only thing that leaves the new mix
                        // paused. A desk player that this page has not engaged
                        // stays silent: keepListening is false until Engage.
                        if (wasPlaying || resumeAfterSkip || resumeAfterReattach || keepListening) {{
                            audioEl.play().catch(() => {{}});
                        }}
                    }}
                }} else if (audioStreamEngaged === false) {{
                    // Not engaged yet: keep the starting point current so the
                    // tap that does engage uses the newest safe position.
                    lastServerResume = Number(data.resume_sec) || 0;
                }}
                if (hostPlayerActive) syncHostPlayerFile();
                // Host ownership is settled now, so the icon can be trusted.
                syncPlayPauseButton(state);
                // First status poll: pull the stream without waiting for a tap,
                // unless the host player is the one on air.
                maybeAutoStartStream();

                document.getElementById('specFormat').innerText = data.format || "AUDIO";
                document.getElementById('specRate').innerText = data.sample_rate || "48.0 kHz";
                document.getElementById('specChannels').innerText = data.channels || "Stereo";

                updateShuffleUI(Boolean(data.shuffle));
                if (data.repeat) updateRepeatUI(data.repeat);

                if (data.tracklist && data.tracklist.length > 0) {{
                    document.getElementById('tlCard').style.display = 'flex';
                    document.getElementById('tlCount').innerText = data.tracklist.length + ' Tracks';
                    const key = (data.active_file || data.title);
                    if (key !== lastLoadedMix) {{
                        lastLoadedMix = key;
                        const listEl = document.getElementById('tlItems');
                        listEl.innerHTML = '';
                        data.tracklist.forEach(t => {{
                            const li = document.createElement('li');
                            li.className = 'tl-item';
                            li.innerText = t;
                            listEl.appendChild(li);
                        }});
                    }}
                }} else {{
                    document.getElementById('tlCard').style.display = 'none';
                }}
            }} catch (e) {{
                // The service may be mid-restart. Show the notice once the
                // connection has failed repeatedly, then keep retrying and
                // reload as soon as the new instance answers.
                statusFailureCount += 1;
                if (statusFailureCount >= 2) noteServerGone();
                throw e;
            }}
        }}

        function apiUrl(path) {{
            if (!followStream) return path;
            return path + (path.indexOf('?') === -1 ? '?' : '&') + 'follow=stream';
        }}

        async function sendAction(act, params = {{}}) {{
            // The first transport tap can land while Engage is still copying
            // the desk's file into the session. Hold it and run it after, or
            // that copy writes the old file back over the tap.
            if (adoptInFlight && act !== 'adopt_host') {{
                pendingTransport = {{ act: act, params: params }};
                return;
            }}
            // Next and previous are a request to hear that mix, including
            // when the page was sitting on Stop. Leaving listenerPaused set
            // loaded the new file and kept it silent.
            if ((act === 'next' || act === 'previous' || act === 'prev') && phoneOwnsPlayback()) {{
                listenerPaused = false;
            }}
            if (phoneOwnsPlayback() && !params.target) {{
                params = Object.assign({{ target: 'stream' }}, params);
            }}
            let url = '/api/control?action=' + encodeURIComponent(act);
            for (const [k, v] of Object.entries(params)) {{
                url += '&' + encodeURIComponent(k) + '=' + encodeURIComponent(v);
            }}
            try {{
                await fetch(url, {{ method: 'POST' }});
                setTimeout(fetchEq, 50);
                setTimeout(fetchStatus, 150);
                setTimeout(fetchMiniPlaylist, 250);
            }} catch (e) {{}}
        }}

        // ---------------------------------------------------------------------
        // Desktop launch pad (Linux only). A running app keeps its button
        // pressed down; an app that is not running sits in the raised state.
        //
        // MP Visualizer is different: it owns exactly one window, so its
        // button walks through Off -> Windowed -> Fullscreen -> Off and always
        // reflects what the host reports, not how many times it was tapped.
        // In TTY Audio Mode every desktop launcher is disabled and the panel
        // says why.
        // ---------------------------------------------------------------------
        let launchPadTtyMode = false;

        function paintTtyMode(ttyMode) {{
            launchPadTtyMode = Boolean(ttyMode);
            const pad = document.getElementById('launchPad');
            const banner = document.getElementById('launchPadTty');
            if (pad) pad.classList.toggle('tty-disabled', launchPadTtyMode);
            if (banner) {{
                if (launchPadTtyMode) {{
                    banner.hidden = false;
                    if (!banner.dataset.filled) {{
                        banner.innerHTML = '';
                        const title = document.createElement('strong');
                        title.textContent = '⌨ MP Mix Manager TTY Audio Mode';
                        const body = document.createElement('span');
                        body.textContent = 'These desktop launchers are disabled while '
                            + 'the manager runs in the text console. Return to the '
                            + 'desktop to use them.';
                        banner.appendChild(title);
                        banner.appendChild(body);
                        banner.dataset.filled = '1';
                    }}
                }} else {{
                    banner.hidden = true;
                }}
            }}
            document.querySelectorAll('#launchPad .launch-btn').forEach(btn => {{
                if (launchPadTtyMode) {{
                    btn.disabled = true;
                    btn.setAttribute('aria-disabled', 'true');
                }} else {{
                    btn.disabled = false;
                    btn.removeAttribute('aria-disabled');
                }}
            }});
        }}

        function paintVisualizer(data) {{
            const btn = document.getElementById('launchVisualizer');
            if (!btn) return;
            const state = (data && data.visualizer) || {{}};
            const running = Boolean(state.running);
            const fullscreen = Boolean(state.fullscreen);
            const pending = Boolean(state.pending);
            btn.classList.toggle('running', running);
            btn.classList.toggle('fullscreen', running && fullscreen);
            btn.setAttribute('aria-pressed', running ? 'true' : 'false');
            const label = document.getElementById('launchVisualizerState');
            if (label) {{
                label.innerText = !running ? 'Off'
                    : (pending ? 'Opening…' : (fullscreen ? 'Fullscreen' : 'Windowed'));
            }}
            btn.title = !running
                ? 'Open MP Visualizer on the computer'
                : (pending
                    ? 'MP Visualizer is starting up'
                    : (fullscreen
                        ? 'MP Visualizer is fullscreen — press to close it'
                        : 'MP Visualizer is open — press for fullscreen'));
        }}

        function paintLaunchPad(data) {{
            if (!data || !data.supported) return;
            const buttons = document.querySelectorAll('#launchPadGrid .launch-btn[data-app]');
            buttons.forEach(btn => {{
                const app = (data.apps || []).find(a => a.id === btn.dataset.app);
                const running = Boolean(app && app.running);
                btn.classList.toggle('running', running);
                btn.setAttribute('aria-pressed', running ? 'true' : 'false');
                const state = btn.querySelector('.launch-state');
                if (state) state.innerText = running ? 'Running' : 'Off';
            }});
            const tray = document.getElementById('launchPadTray');
            if (tray) {{
                tray.innerText = data.tray_running ? 'Tray on' : 'Tray off';
                tray.classList.toggle('ok', Boolean(data.tray_running));
            }}
            paintVisualizer(data);
            paintTtyMode(data.tty_mode);
        }}

        async function fetchLaunchPad() {{
            const pad = document.getElementById('launchPad');
            if (!pad) return;
            try {{
                const res = await fetch('/api/launchpad');
                const data = await res.json();
                if (!data || !data.supported) {{
                    pad.style.display = 'none';
                    return;
                }}
                pad.style.display = 'block';
                paintLaunchPad(data);
            }} catch (e) {{}}
        }}

        function launchPadNote(text, ok) {{
            const note = document.getElementById('launchPadNote');
            if (!note) return;
            note.innerText = text;
            note.classList.toggle('ok', Boolean(ok));
        }}

        async function launchApp(role) {{
            if (launchPadTtyMode) {{
                launchPadNote('TTY Audio Mode is active — desktop launchers are disabled.', false);
                return;
            }}
            const btn = document.querySelector('#launchPadGrid .launch-btn[data-app="' + role + '"]');
            if (!btn || btn.classList.contains('busy')) return;
            btn.classList.add('busy');
            launchPadNote('Starting ' + (btn.dataset.label || role) + '…', false);
            try {{
                const res = await fetch('/api/launchpad/launch?app=' + encodeURIComponent(role), {{ method: 'POST' }});
                const data = await res.json();
                if (data && data.success) {{
                    const verb = data.action === 'launch' ? 'Launched' : 'Brought forward';
                    launchPadNote(verb + ' ' + (data.label || btn.dataset.label || role) + ' on the computer.', true);
                }} else if (data && data.action === 'tty_mode') {{
                    paintTtyMode(true);
                    launchPadNote('TTY Audio Mode is active — desktop launchers are disabled.', false);
                }} else {{
                    launchPadNote('Could not open ' + (btn.dataset.label || role) + '.', false);
                }}
            }} catch (e) {{
                launchPadNote('Could not reach the computer.', false);
            }} finally {{
                btn.classList.remove('busy');
                // Let the window appear before the pressed state is painted.
                setTimeout(fetchLaunchPad, 1500);
                setTimeout(fetchLaunchPad, 4000);
            }}
        }}

        async function launchVisualizer() {{
            if (launchPadTtyMode) {{
                launchPadNote('TTY Audio Mode is active — the MP Visualizer button is disabled.', false);
                return;
            }}
            const btn = document.getElementById('launchVisualizer');
            if (!btn || btn.classList.contains('busy')) return;
            const wasRunning = btn.classList.contains('running');
            const wasFullscreen = btn.classList.contains('fullscreen');
            btn.classList.add('busy');
            launchPadNote(wasFullscreen ? 'Closing MP Visualizer…'
                : (wasRunning ? 'Taking MP Visualizer fullscreen…' : 'Opening MP Visualizer…'), false);
            try {{
                const res = await fetch('/api/launchpad/visualizer', {{ method: 'POST' }});
                const data = await res.json();
                if (data && data.success) {{
                    if (data.action === 'launch') {{
                        launchPadNote('MP Visualizer opened on the computer.', true);
                    }} else if (data.action === 'fullscreen') {{
                        launchPadNote('MP Visualizer is fullscreen. Press again to close it.', true);
                    }} else if (data.action === 'already_running') {{
                        launchPadNote('MP Visualizer is starting up…', false);
                    }} else {{
                        launchPadNote('MP Visualizer closed.', true);
                    }}
                }} else if (data && data.action === 'tty_mode') {{
                    paintTtyMode(true);
                    launchPadNote('TTY Audio Mode is active — the MP Visualizer button is disabled.', false);
                }} else {{
                    launchPadNote((data && data.detail) || 'Could not control the MP Visualizer.', false);
                }}
            }} catch (e) {{
                launchPadNote('Could not reach the computer.', false);
            }} finally {{
                btn.classList.remove('busy');
                setTimeout(fetchLaunchPad, 800);
                setTimeout(fetchLaunchPad, 2500);
                setTimeout(fetchLaunchPad, 5000);
            }}
        }}

        async function closeAllApps() {{
            if (launchPadTtyMode) {{
                launchPadNote('TTY Audio Mode is active — desktop launchers are disabled.', false);
                return;
            }}
            const btn = document.getElementById('launchCloseAll');
            if (!btn || btn.classList.contains('busy')) return;
            btn.classList.add('busy');
            launchPadNote('Closing the desktop suite…', false);
            try {{
                const res = await fetch('/api/launchpad/close', {{ method: 'POST' }});
                const data = await res.json();
                const left = (data && data.remaining) || [];
                const names = left.map(r => {{
                    const b = document.querySelector('#launchPadGrid .launch-btn[data-app="' + r + '"]');
                    return b ? b.dataset.label : r;
                }});
                if (data && data.action === 'closing') {{
                    // The host answers before the exit movie finishes. The
                    // launch pad poll turns the buttons off as each app goes.
                    launchPadNote('Closing the desktop suite — the tray icon stays up.', true);
                }} else if (data && data.success) {{
                    launchPadNote('All MP apps closed. Tray icon stays up.', true);
                }} else if (data && data.action === 'tty_mode') {{
                    paintTtyMode(true);
                    launchPadNote('TTY Audio Mode is active — desktop launchers are disabled.', false);
                }} else if (names.length) {{
                    launchPadNote('Still open: ' + names.join(', '), false);
                }} else {{
                    launchPadNote('Close All did not complete.', false);
                }}
            }} catch (e) {{
                launchPadNote('Could not reach the computer.', false);
            }} finally {{
                btn.classList.remove('busy');
                setTimeout(fetchLaunchPad, 600);
                setTimeout(fetchLaunchPad, 2500);
                setTimeout(fetchLaunchPad, 6000);
            }}
        }}

        // ---------------------------------------------------------------------
        // POLLING SCHEDULE
        //
        // Every poll used to be a bare setInterval, including fetchEq at 100 ms.
        // That is 10 requests a second forever, and iOS Safari keeps firing
        // timers in a backgrounded tab, so a phone left on this page queued
        // hundreds of pending fetches against a single-threaded Python server
        // and the dashboard became unresponsive. The schedule below:
        //   * pauses completely while the tab is hidden (visibilitychange),
        //   * never overlaps a second request for the same endpoint,
        //   * backs off after failures instead of hammering a dead service,
        //   * talks to the visualiser locally at 60 fps instead of over HTTP.
        // ---------------------------------------------------------------------
        const POLLS = [
            {{ name: 'eq', fn: fetchEq, every: 2000, fail: 5000 }},
            {{ name: 'status', fn: fetchStatus, every: 2000, fail: 4000 }},
            {{ name: 'theme', fn: fetchTheme, every: 6000, fail: 15000 }},
            {{ name: 'playlist', fn: fetchMiniPlaylist, every: 15000, fail: 30000 }},
            {{ name: 'launchpad', fn: fetchLaunchPad, every: 10000, fail: 30000 }},
            {{ name: 'assistant', fn: fetchAssistantStatus, every: 20000, fail: 30000 }},
            {{ name: 'position', fn: reportPlaybackPosition, every: 5000, fail: 10000 }},
        ];

        let pollTimers = [];
        let pollPaused = false;

        function startPolls() {{
            stopPolls();
            if (pollPaused) return;
            POLLS.forEach(p => {{
                const tick = async () => {{
                    if (pollPaused) return;
                    if (p.busy) return;
                    p.busy = true;
                    let failed = false;
                    try {{
                        await p.fn();
                    }} catch (e) {{
                        failed = true;
                    }} finally {{
                        p.busy = false;
                    }}
                    if (pollPaused) return;
                    const delay = failed ? p.fail : p.every;
                    p.timer = setTimeout(tick, delay);
                    pollTimers.push(p.timer);
                }};
                // Stagger the first run so page load does not fire them at once.
                p.timer = setTimeout(tick, 150 + Math.random() * 400);
                pollTimers.push(p.timer);
            }});
        }}

        function stopPolls() {{
            pollTimers.forEach(t => clearTimeout(t));
            pollTimers = [];
            POLLS.forEach(p => {{ p.busy = false; }});
        }}

        function setPollingPaused(paused) {{
            if (pollPaused === paused) return;
            pollPaused = paused;
            // Stop the theme's CSS animations too: on iOS they keep the GPU and
            // the compositor busy for a tab nobody is looking at.
            document.body.classList.toggle('dynamic-rainbow-glass-frozen', paused);
            syncRainbowDrive();
            if (paused) {{
                stopPolls();
                startBackgroundStreamPoll();
            }} else {{
                stopBackgroundStreamPoll();
                // Catch up immediately: the page was frozen while hidden and
                // the displayed state is now stale.
                startPolls();
                fetchStatus();
                fetchEq();
            }}
        }}

        // A backgrounded iPhone keeps the <audio> element playing, and iOS then
        // throttles the full poll schedule away. Without any poll the queue
        // cannot advance when a mix ends: the page would fall silent after one
        // track while the listener was in another app. One status request every
        // three seconds is enough to follow the queue and is nowhere near the
        // ten-a-second eq storm that pausing the schedule was introduced for.
        const BACKGROUND_STREAM_POLL_MS = 3000;
        let backgroundStreamPoll = 0;

        function startBackgroundStreamPoll() {{
            if (!isIOSAudioDevice()) return;
            stopBackgroundStreamPoll();
            const tick = async () => {{
                backgroundStreamPoll = 0;
                if (!document.hidden) return;
                // Stop following the queue only when the listener asked for
                // silence. An element paused by an interruption is still a
                // session that should pick the next mix back up.
                if (!audioStreamEngaged || listenerPaused) return;
                try {{ await fetchStatus(); }} catch (e) {{}}
                if (document.hidden && audioStreamEngaged && !listenerPaused) {{
                    backgroundStreamPoll = setTimeout(tick, BACKGROUND_STREAM_POLL_MS);
                }}
            }};
            backgroundStreamPoll = setTimeout(tick, BACKGROUND_STREAM_POLL_MS);
        }}

        function stopBackgroundStreamPoll() {{
            clearTimeout(backgroundStreamPoll);
            backgroundStreamPoll = 0;
        }}

        // Coming back to the page after a call, Siri or the lock screen. iOS
        // can pause the element for an interruption without telling this page,
        // so resume it when the listener did not ask for silence. A refusal
        // (no fresh gesture) is harmless: the transport button is repainted by
        // the status poll and the next tap starts the same mix.
        function resumeStreamAfterInterruption() {{
            if (isIOSAudioDevice()) claimPlaybackAudioSession();
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !audioStreamEngaged || listenerPaused || !activeAudioFile) return;
            if (!audioEl.paused || audioEl.ended) return;
            if (audioEl.__unlocking) return;
            applyStreamPitch(currentPitchPercent);
            const started = audioEl.play();
            if (started && started.catch) started.catch(() => {{}});
        }}

        document.addEventListener('visibilitychange', () => {{
            setPollingPaused(document.hidden);
            // Backgrounding never pauses the element: that is what lets the
            // mix keep playing while the listener uses another app.
            if (!document.hidden) resumeStreamAfterInterruption();
        }});
        // iOS Safari fires pagehide while the document is still on screen
        // (bfcache snapshot, audio session). Freezing there, and only
        // unfreezing when pageshow says the page was restored, left the
        // rainbow sitting on one colour for the whole visit.
        window.addEventListener('pagehide', () => {{
            if (document.hidden) setPollingPaused(true);
        }});
        window.addEventListener('pageshow', () => {{
            if (!document.hidden) setPollingPaused(false);
        }});

        syncRainbowDrive();
        startPolls();
        fetchEq();
        fetchStatus();
        fetchTheme();
        fetchMiniPlaylist();
        fetchLaunchPad();
        fetchAssistantStatus();
    </script>
</body>
</html>
"""


# ------------------------------------------------------------------------------
# HTTP SERVER & API HANDLER (WITH RANGE HEADER SUPPORT FOR HTML5 STREAMING)
# ------------------------------------------------------------------------------
class CompanionHandler(http.server.BaseHTTPRequestHandler):
    # A phone that locks its screen or backgrounds Safari stops reading the
    # streaming socket. Without a timeout the writing thread parked forever on
    # a full TCP window, so every abandoned page load leaked a thread until the
    # service could no longer serve anything. A bounded write turns that into a
    # closed connection, which is the correct outcome.
    timeout = 30

    # HTTP/1.1 so the dashboard's several polls per second reuse one connection
    # instead of opening a fresh socket each time and filling TIME_WAIT.
    protocol_version = "HTTP/1.1"

    # Headers collected during a request (Set-Cookie, etc.), appended by
    # end_headers so every response path gets them without touching each
    # handler. Reset per request in handle_one_request: this handler is threaded
    # and keep-alive reuses the instance, so a class-level list would be shared
    # between concurrent requests.
    _extra_headers: List[Tuple[str, str]] = []

    def handle_one_request(self):
        # A response path may queue headers before any response exists, so the
        # list must be per-request and must survive send_response().
        self._extra_headers = []
        return super().handle_one_request()

    def end_headers(self):
        try:
            for name, value in sec.security_headers():
                self.send_header(name, value)
            for name, value in self._extra_headers:
                self.send_header(name, value)
        finally:
            self._extra_headers = []
        super().end_headers()

    # ------------------------------------------------------------------
    # ACCESS CONTROL
    # ------------------------------------------------------------------
    def _client_allowed(self) -> bool:
        """Refuse a peer outside WEB_COMPANION_ALLOW_CIDR (empty = allow all)."""
        if sec.is_allowed_client(self.client_address, COMPANION_CIDRS):
            return True
        self._send_plain(403, "This device is not on the companion allowlist.")
        return False

    def _request_allowed(self) -> bool:
        """The one gate every request passes through.

        There is no password: a device that can reach this port is allowed,
        unless the operator narrowed it with WEB_COMPANION_ALLOW_CIDR. That
        opt-in list is the way to restrict a machine on a network you do not
        control; on a home/studio LAN the dashboard simply opens.

        CSRF is a separate check on mutating methods (`_require_mutation`),
        because a plain GET must stay open for the dashboard itself.
        """
        return self._client_allowed()

    def _send_bytes(self, body: bytes, content_type: str, cache: str = "no-cache") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_image(self, path: Path, cache: str) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # A cover is a picture beside a mix, not a movie. Refuse a huge file
        # rather than holding the request thread while it is copied.
        if size <= 0 or size > 15_000_000:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            body = path.read_bytes()
        except OSError:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_bytes(body, image_content_type(path), cache=cache)

    def _send_plain(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _require_mutation(self) -> bool:
        """CSRF gate for a state-changing POST."""
        problem = sec.validate_mutation(self.headers)
        if not problem:
            return True
        self._send_plain(403, f"Request refused: {problem}.")
        return False

    def do_HEAD(self):
        url = urllib.parse.urlparse(self.path)
        parsed_query = urllib.parse.parse_qs(url.query)
        # HEAD answers the same route table as GET, so it passes the same
        # allowlist gate rather than being a way around it.
        if not self._request_allowed():
            return
        if url.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif url.path in ("/manifest.webmanifest", "/icon.png"):
            content_type = "application/manifest+json" if url.path.endswith(".webmanifest") else "image/png"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif url.path in ("/api/status", "/api/theme", "/api/eq", "/api/autoplay",
                          "/api/themes", "/api/set_theme", "/api/playlist", "/api/mode",
                          "/api/launchpad", "/api/announce", "/api/assistant/status",
                          "/api/assistant/voices", "/api/assistant/chat",
                          "/api/assistant/speech", "/api/assistant/start",
                          "/api/assistant/abort"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            # HTTP/1.1 keep-alive needs a framing header even on HEAD; without
            # one the client waits for an EOF that never arrives.
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        parsed_query = urllib.parse.parse_qs(url.query)

        if not self._request_allowed():
            return

        if url.path in ("/", "/index.html"):
            current_theme = get_theme_palette(CLI_THEME)
            html_content = render_html_page(current_theme)
            body = html_content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/eq":
            follow = parsed_query.get("follow", [""])[0]
            self._send_json(200, get_eq_status(follow))
        elif url.path == "/api/status":
            follow = parsed_query.get("follow", [""])[0]
            status = get_playback_status(CLI_THEME, follow)
            status["autoplay"] = autoplay_state()
            status["instance"] = INSTANCE_ID
            announcement = current_announcement()
            if announcement:
                status["announcement"] = announcement["message"]
                status["announcement_at"] = announcement["at"]
            self._send_json(200, status)
        elif url.path == "/api/autoplay":
            self._send_json(200, autoplay_state())
        elif url.path == "/api/theme":
            self._send_json(200, get_theme_palette(CLI_THEME))
        elif url.path == "/api/themes":
            self._send_json(200, {
                "current": get_current_theme_id(CLI_THEME),
                "themes": available_themes(),
            })
        elif url.path == "/api/set_theme":
            requested = parsed_query.get("theme", [""])[0]
            chosen = set_web_theme(requested) if requested else None
            success = chosen is not None
            self._send_json(200 if success else 400, {
                "success": success,
                "theme": chosen or get_current_theme_id(CLI_THEME),
                "palette": get_theme_palette(CLI_THEME) if success else {},
            })
        elif url.path == "/api/mode":
            requested = parsed_query.get("mode", [""])[0]
            chosen = set_library_mode(requested) if requested else None
            payload = {
                "success": chosen is not None,
                "mode": chosen or get_library_mode(),
                "mode_label": MODE_LABELS.get(chosen or get_library_mode(), "Library"),
                "total": len(build_stream_queue_cached()),
                "library_fallback": bool(_queue_source_state.get("fallback")),
                "library_detail": _queue_source_state.get("detail", ""),
        "music_offline": bool(_queue_source_state.get("music_offline")),
            }
            self._send_json(200 if chosen else 400, payload)
        elif url.path == "/api/playlist":
            follow = parsed_query.get("follow", [""])[0]
            # `q` is the playlist search box. The filter runs on the current
            # library only, and the rows keep the queue index a tap plays.
            raw_query = parsed_query.get("q", [""])[0]
            payload = get_mini_playlist(follow=follow, query=raw_query)
            payload["shuffle"] = bool(load_stream_session().get("shuffle", False))
            self._send_json(200, payload)
        elif url.path == "/api/play-random":
            payload = play_random_track()
            self._send_json(200 if payload.get("success") else 409, payload)
        elif url.path == "/api/launchpad":
            self._send_json(200, launch_pad_status())
        elif url.path == "/api/announce":
            # The tray posts here just before it stops the service; a dashboard
            # polling this endpoint can show the notice even if /api/status is
            # already unreachable.
            self._send_json(200, {"announcement": current_announcement()})
        elif url.path == "/api/assistant/status":
            self._send_json(
                200,
                harmony_status(force="1" in parsed_query.get("force", ["0"])),
            )
        elif url.path == "/api/assistant/voices":
            current = harmony_status().get("voice", HARMONY_DEFAULT_VOICE)
            self._send_json(200, {
                "current": current,
                "current_label": harmony_voice_label(current),
                "default": HARMONY_DEFAULT_VOICE,
                "voice_speed": harmony_voice_speed(),
                "voices": [{"id": vid, "label": label} for vid, label in HARMONY_VOICES.items()],
            })
        elif url.path == "/api/assistant/speech":
            # GET form for simple links and tests; the dashboard itself POSTs,
            # because a long reply does not fit comfortably in a URL.
            raw_text = parsed_query.get("text", [""])[0]
            voice = parsed_query.get("voice", [""])[0] or None
            speed_raw = parsed_query.get("speed", [""])[0]
            try:
                speed = float(speed_raw) if speed_raw else None
            except ValueError:
                speed = None
            self._send_assistant_speech(raw_text, voice, speed)
        elif url.path == "/api/assistant/start":
            payload = start_harmony_agent()
            self._send_json(200 if payload.get("success") else 501, payload)
        elif url.path == "/manifest.webmanifest":
            body = json.dumps(web_manifest(get_theme_palette(CLI_THEME))).encode("utf-8")
            self._send_bytes(body, "application/manifest+json", cache="no-cache")
        elif url.path == "/icon.png":
            icon = app_icon_path()
            if icon is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._serve_image(icon, cache="public, max-age=86400")
        elif url.path == "/api/artwork":
            requested = parsed_query.get("file", [""])[0]
            cover = artwork_for_stream(requested)
            if cover is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._serve_image(cover, cache="private, max-age=300")
        elif url.path == "/healthz":
            payload = {"ok": True, "service": "mp-web-companion", "version": VERSION}
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/stream":
            # Containment: the value arrives from the network, so resolve it,
            # require it to live inside a configured library root, and require
            # an audio suffix. Missing and forbidden answer identically so the
            # status code cannot be used to probe the filesystem.
            requested = parsed_query.get("file", [""])[0]
            safe_path = sec.safe_stream_path(requested, stream_roots())
            if safe_path is not None:
                self._serve_audio_stream(str(safe_path))
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    # --------------------------------------------------------------------------
    # AUDIO STREAMING
    #
    # Phones and tablets pull the whole mix over HTTP and seek within it. Two
    # things matter for correctness:
    #   * `bytes=start-` must be honoured for the whole tail, otherwise the
    #     element receives a truncated body and restarts from the same offset.
    #   * A client that disconnects mid-transfer raises BrokenPipeError, which
    #     is normal and must never be reported as a server fault.
    # --------------------------------------------------------------------------
    STREAM_CHUNK = 65536

    def _serve_audio_stream(self, file_path: str) -> None:
        try:
            # Refuse a file with no audio frames up front. Serving its 44-byte
            # header as a 200 makes the <audio> element raise a media error,
            # which the dashboard historically read as "the companion
            # restarted" and retried forever. A 404 is unambiguous: the page
            # stops and can explain that this mix is empty on the computer.
            if not is_streamable_file(file_path):
                self._send_json(404, {
                    "error": "no_audio_frames",
                    "detail": "The mix file has no playable audio.",
                })
                return

            # A network mount can answer stat() from its cache and then fail the
            # actual read ("Transport endpoint is not connected"). Answering 200
            # with Content-Length for a body we cannot deliver made the browser
            # report ERR_CONTENT_LENGTH_MISMATCH, which the dashboard read as
            # "the music library stopped responding" and used to write files off.
            # Prove the first block is readable before any header is written, so
            # a dead share is a clean 404 the page can skip quickly.
            try:
                with open(file_path, "rb") as probe:
                    if not probe.read(1):
                        raise OSError("empty read")
            except OSError:
                self._send_json(404, {
                    "error": "unreadable",
                    "detail": "The mix file could not be read from the archive.",
                })
                return

            file_size = os.path.getsize(file_path)
            content_type = AUDIO_CONTENT_TYPES.get(Path(file_path).suffix.lower(), "audio/mpeg")
            start, end = self._parse_range_header(file_size)

            if start is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return

            length = end - start + 1
            if start == 0 and end == file_size - 1:
                self.send_response(200)
                self.send_header("Content-Length", str(file_size))
            else:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    data = f.read(min(self.STREAM_CHUNK, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            # The phone scrubbed away or closed the tab: not an error.
            self.close_connection = True
        except TimeoutError:
            # A backgrounded phone stopped reading mid-transfer and the socket
            # write timed out. Abandon the response instead of trying to write
            # an error into a half-sent body.
            self.close_connection = True
        except OSError:
            try:
                self.send_error(404)
            except Exception:
                pass

    def _parse_range_header(self, file_size: int) -> Tuple[Optional[int], int]:
        """Return (start, end) for the requested bytes, or (None, 0) if invalid.

        A missing or unparsable header means "send the whole file".
        """
        raw = (self.headers.get("Range") or "").strip()
        if not raw:
            return 0, file_size - 1

        m = re.match(r'bytes=(\d*)-(\d*)$', raw)
        if not m or (not m.group(1) and not m.group(2)):
            return 0, file_size - 1

        try:
            if not m.group(1):
                # Suffix form, e.g. "bytes=-65536" = last 64 KiB.
                suffix = int(m.group(2))
                if suffix <= 0:
                    return None, 0
                return max(0, file_size - suffix), file_size - 1

            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
        except ValueError:
            return 0, file_size - 1

        if start >= file_size:
            return None, 0
        end = min(end, file_size - 1)
        # RFC 7233: a range whose last byte precedes its first is unsatisfiable.
        # Without this, `bytes=100-50` produced `Content-Length: -49`, which is
        # an invalid header: the phone read nothing and the player errored.
        if end < start:
            return None, 0
        return start, end

    def _read_json_body(self, limit: int = 65536) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(min(length, limit)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, OSError):
            payload = {}
        finally:
            # Never leave a partially-read body behind: this server keeps
            # connections alive, and an unread tail would be parsed as the
            # next request line.
            unread = max(0, length - min(length, limit))
            while unread > 0:
                chunk = self.rfile.read(min(unread, 65536))
                if not chunk:
                    break
                unread -= len(chunk)
        return payload if isinstance(payload, dict) else {}

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_assistant_speech(self, text: Any, voice: Any, speed: Any) -> None:
        """Synthesize a reply and stream it back as WAV.

        The phone is the only audio output. When synthesis fails the response
        is a JSON error with a 503, so the page can say what happened instead
        of playing silence.
        """
        audio = harmony_speech(str(text or ""), voice=voice, speed=speed)
        if not audio:
            self._send_json(503, {
                "error": "Speech synthesis failed on the host. Is espeak-ng installed?",
                "voice": normalize_harmony_voice(voice)
                         or load_web_assistant_voice() or HARMONY_DEFAULT_VOICE,
            })
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(audio)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # A hung agent used to pin a companion worker for half an hour. The phone
    # gives up long before that, so the relay is bounded and emits a heartbeat
    # the dashboard can use to tell "thinking" from "connection died".
    ASSISTANT_TIMEOUT = 180.0
    ASSISTANT_HEARTBEAT = 10.0
    ASSISTANT_MAX_MESSAGES = 24
    ASSISTANT_MAX_CONTENT = 16384

    def _stream_assistant_sse(self, payload: Dict[str, Any]) -> None:
        """Relay the agent's token stream as SSE to the dashboard.

        The agent streams OpenAI chat-completion chunks. They are forwarded
        verbatim, so the page can render tokens as they arrive and the whole
        reply is concatenated at the end for speech.
        """
        # A per-browser session id keeps two phones from overwriting each
        # other's conversation. It is derived from the token the client
        # already presents, so no extra state is needed; the header is
        # preferred because the agent keys on X-Session-Id.
        session_id = (
            (self.headers.get("X-MP-Session") or "").strip()
            or "web-companion"
        )[:64]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(obj: Any) -> bool:
            try:
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
                return False
            except OSError:
                self.close_connection = True
                return False

        def emit_done() -> None:
            # The literal marker every OpenAI SSE client stops on, written raw
            # rather than JSON-encoded.
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            self.close_connection = True

        voice = normalize_harmony_voice(payload.get("voice")) or (
            load_web_assistant_voice() or HARMONY_DEFAULT_VOICE
        )
        emit({"object": "mp.assistant.meta", "voice": voice,
              "voice_label": harmony_voice_label(voice)})

        body = {
            "model": payload.get("model") or "mp-harmony-agent",
            # Sanitise the client-supplied conversation. A client that injects
            # a `system` role can rewrite the agent's instructions, which is a
            # prompt-injection route into its shell tools; oversized bodies are
            # a cheap way to exhaust the worker pool.
            "messages": sanitize_agent_messages(payload.get("messages")),
            "stream": True,
        }
        request = urllib.request.Request(
            harmony_agent_url() + "/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Session-Id": session_id},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(
                request, timeout=self.ASSISTANT_TIMEOUT
            )
        except (urllib.error.URLError, OSError) as exc:
            emit({"object": "mp.assistant.error",
                  "message": f"The MP Harmony Agent is not reachable: {exc}"})
            emit_done()
            return
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = (choice.get("delta") or {}).get("content", "")
                if delta:
                    if not emit({"object": "mp.assistant.delta", "content": delta}):
                        return
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
            return
        finally:
            try:
                response.close()
            except Exception:
                pass

        emit({"object": "mp.assistant.done", "voice": voice,
              "voice_label": harmony_voice_label(voice)})
        emit_done()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        parsed_query = urllib.parse.parse_qs(url.query)

        if not self._request_allowed():
            return
        if not self._require_mutation():
            return

        if url.path == "/api/assistant/chat":
            payload = self._read_json_body()
            messages = payload.get("messages") or []
            prompt = ""
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user":
                    prompt = str(message.get("content") or "")
                    break
            # /voice is answered here so the dashboard can switch the voice
            # without sending the command to the model.
            local = _voice_command_reply(prompt)
            if local is not None:
                self._send_json(200, {
                    "object": "mp.assistant.reply",
                    "content": local,
                    "voice": harmony_status().get("voice", HARMONY_DEFAULT_VOICE),
                    "voice_label": harmony_voice_label(
                        harmony_status().get("voice", HARMONY_DEFAULT_VOICE)
                    ),
                    "local": True,
                })
                return
            self._stream_assistant_sse(payload)
            return
        if url.path == "/api/assistant/voice":
            payload = self._read_json_body()
            target = set_harmony_voice(payload.get("voice"))
            self._send_json(200 if target else 400, {
                "success": bool(target),
                "voice": target or harmony_status().get("voice"),
                "voice_label": harmony_voice_label(target or ""),
            })
            return
        if url.path == "/api/assistant/start":
            payload = start_harmony_agent()
            self._send_json(200 if payload.get("success") else 501, payload)
            return
        if url.path == "/api/assistant/speech":
            # POST form: a long reply exceeds the URL limit that <audio src>
            # would otherwise hit, so the page posts the text and blobs the WAV.
            payload = self._read_json_body(limit=200000)
            self._send_assistant_speech(
                payload.get("text"), payload.get("voice"), payload.get("speed")
            )
            return
        if url.path == "/api/assistant/abort":
            # Barge-in: tell the agent to stop generating the current reply so
            # the model does not keep running after the phone hangs up.
            success = False
            try:
                with _harmony_request("/v1/abort", {}, timeout=4.0) as response:
                    success = response.status == 200
                    response.read()
            except (urllib.error.URLError, OSError, ValueError):
                success = False
            self._send_json(200, {"success": success})
            return
        if url.path == "/api/control":
            params = urllib.parse.parse_qs(url.query)
            action = params.get("action", [""])[0]
            success = control_player(action, params)
            self._send_json(200, {"success": success, "action": action})
        elif url.path == "/api/announce":
            message = ANNOUNCE_MESSAGE_DEFAULT
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                try:
                    body = self.rfile.read(min(length, 4096))
                    payload = json.loads(body.decode("utf-8"))
                    if isinstance(payload, dict) and payload.get("message"):
                        message = str(payload["message"])
                except (ValueError, UnicodeDecodeError, OSError):
                    pass
            queued = set_announcement(message)
            self._send_json(200, {
                "success": queued is not None, "announcement": queued,
            })
        elif url.path in ("/api/launchpad/launch", "/api/launchpad/close",
                          "/api/launchpad/visualizer"):
            params = urllib.parse.parse_qs(url.query)
            if url.path.endswith("/close"):
                # Answer at once and sweep in the background: the tray plays a
                # 40-second exit movie before it stops anything, and the phone
                # must not sit on that. The next status poll confirms.
                payload = launch_pad_close_all(wait=False)
            elif url.path.endswith("/visualizer"):
                # One window, three states: open, fullscreen, close. The host
                # decides from the published state, so a tap that races the
                # window's own mode change still does the right thing.
                payload = launch_pad_visualizer()
            else:
                payload = launch_pad_action(params.get("app", [""])[0])
            status = 200 if payload.get("success") else 400
            if payload.get("action") in ("unsupported", "tty_mode"):
                status = 501 if payload.get("action") == "unsupported" else 409
            self._send_json(status, payload)
        elif url.path in ("/api/status", "/api/theme", "/api/eq", "/api/autoplay",
                          "/api/themes", "/api/set_theme", "/api/playlist", "/api/mode",
                          "/api/launchpad", "/api/play-random", "/api/stream",
                          "/api/artwork", "/manifest.webmanifest", "/icon.png",
                          "/api/assistant/status", "/api/assistant/voices",
                          "/healthz"):
            # These are read-only (or deliberately GET-only, like set_theme).
            # A POST to them is a method error, not a missing route: "404" made
            # a client look like the endpoint had been removed.
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, format, *args):
        pass


class CompanionServer(http.server.ThreadingHTTPServer):
    """Threaded server with a bounded worker pool.

    The dashboard polls several endpoints and keeps an audio stream open, so
    concurrent requests are expected. An unbounded thread-per-connection model
    made a stuck client able to exhaust the process, so worker threads are
    capped and surplus connections wait in the listen backlog instead of
    spawning without limit. `daemon_threads` keeps shutdown from hanging on an
    abandoned stream.
    """

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, server_address, handler_class, max_workers: int = 32):
        super().__init__(server_address, handler_class)
        self._worker_slots = threading.BoundedSemaphore(max_workers)

    def process_request(self, request, client_address):
        # Bounded so a flood of stalled phones cannot create unbounded threads.
        # The acquire is timed: if every worker is busy the connection is closed
        # quickly, which keeps the accept loop moving instead of parking it and
        # taking the whole dashboard down with it.
        if not self._worker_slots.acquire(timeout=5.0):
            self._drop_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    @staticmethod
    def _drop_request(request) -> None:
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            request.close()
        except OSError:
            pass

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()

    def handle_error(self, request, client_address) -> None:
        """A client hanging up is normal, not a server fault.

        Phones close tabs, scrub, and time out mid-poll constantly. Only real
        server-side failures should reach the journal.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def _make_console_utf8_safe() -> None:
    """Never let a decorative character kill the server at startup.

    Windows consoles default to cp1252, and the banner uses emoji. Printing one
    raised UnicodeEncodeError before the socket was ever bound, so the companion
    exited immediately on Windows while the installer still reported success
    (it only checked that a process had been spawned). Reconfiguring stdout and
    stderr to UTF-8 with a replacement fallback keeps the banner when the
    console can render it and degrades to '?' when it cannot.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # A redirected or exotic stream: nothing to reconfigure.
            continue


def main():
    _make_console_utf8_safe()
    global CLI_THEME, COMPANION_CIDRS
    # Restore the cached "has audio frames" verdicts before the first scan so a
    # restart does not reopen every multi-GB mix over CIFS.
    _load_frame_probe_cache()
    # Restore the mixes devices have already proved undecodable, so a restart
    # does not hand the same dead URL to the next listener.
    _load_unplayable()
    # Loopback by default: LAN exposure is an explicit opt-in, because the
    # library and the playback controls are personal. `WEB_COMPANION_HOST=0.0.0.0`
    # (or `--host 0.0.0.0`) restores the old behaviour.
    default_host = sec.default_host()
    default_port = int(os.environ.get("WEB_COMPANION_PORT", str(PORT)))

    parser = argparse.ArgumentParser(description="MP Mix Manager Mobile Web Companion")
    parser.add_argument("--host", "-H", type=str, default=default_host)
    parser.add_argument("--port", "-p", type=int, default=default_port)
    parser.add_argument("--theme", "-t", type=str, default=None)
    parser.add_argument("--stream-status", action="store_true",
                        help="Print the resolved stream queue as JSON and exit")
    parser.add_argument("--allow-cidr", type=str, default="",
                        help="Comma-separated CIDRs allowed to connect")
    # Kept so an existing unit file, script or bookmark with these flags still
    # starts instead of dying with "unrecognized arguments". Both were removed
    # with token auth: there is no token to print or rotate and no auth to
    # disable.
    parser.add_argument("--print-token", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--rotate-token", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-auth", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    cidr_config = args.allow_cidr or _config_value("WEB_COMPANION_ALLOW_CIDR", "")
    COMPANION_CIDRS = sec.parse_cidr_list(cidr_config)

    if args.print_token:
        print("This companion has no access token; open its URL directly.",
              file=sys.stderr)
        return 0

    if args.stream_status:
        playlist = scan_playlist_files()
        track, position = _pick_start_track(playlist)
        print(json.dumps({
            "playlist_size": len(playlist),
            "track": track or "",
            "position": round(position, 2),
            "listening": "HTTP stream only (no local audio output)",
        }, indent=2))
        return 0

    # `WEB_COMPANION_ENABLED=false` was documented but never read, so setting
    # it did nothing. Honour it now, but only when the operator did not pass an
    # explicit flag, so a command-line start always wins.
    if not _env_flag("WEB_COMPANION_ENABLED", True):
        print("MP Web Companion is disabled (WEB_COMPANION_ENABLED=false).")
        return 0

    CLI_THEME = args.theme
    active_theme = get_theme_palette(CLI_THEME)
    lan_ip = get_local_ip()
    host_display = lan_ip if args.host in ("0.0.0.0", "", "::") else args.host

    def _access_url(host: str) -> str:
        """The address a phone opens. No token, no query string."""
        return f"http://{host}:{args.port}/"

    server_address = (args.host, args.port)
    try:
        httpd = CompanionServer(server_address, CompanionHandler)
    except OSError as err:
        if err.errno in (98, 48, 10048):
            print(f"\n======================================================================")
            print(f"  🌌 MP MIX ARCHIVE MANAGER — MOBILE WEB COMPANION (ACTIVE)")
            print(f"======================================================================")
            print(f"  • Local Machine:  {_access_url('localhost')}")
            print(f"  • Phone / Tablet: {_access_url(host_display)}")
            print(f"======================================================================\n")
            return 0
        raise

    # Fill the queue cache before the first phone polls, so the initial request
    # does not pay the whole scan on a request thread. Cheap on a warm frame
    # cache; a one-off cost on the first run after a reboot.
    start_prewarm_worker()

    # Arm the stream at startup so the phone begins playing on its own. This
    # only catalogs files and marks the session active; no local device opens.
    stream_thread = start_autoplay_worker()

    print(f"\n======================================================================")
    print(f"  🌌 MP MIX ARCHIVE MANAGER — MOBILE WEB COMPANION")
    print(f"======================================================================")
    print(f"  • Active Theme:   {active_theme['name']} ({active_theme['id']})")
    print(f"  • Library Mode:   {MODE_LABELS.get(get_library_mode(), 'Library')}")
    print(f"  • Local Machine:  {_access_url('localhost')}")
    print(f"  • Phone / Tablet: {_access_url(host_display)}")
    print(f"  • Audio Output:   STREAM ONLY (no local device)")
    print(f"  • Autoplay:       {'ON' if _env_flag('WEB_COMPANION_AUTOPLAY', True) else 'OFF'}")
    if COMPANION_CIDRS:
        print(f"  • Allowed CIDRs:  {', '.join(str(n) for n in COMPANION_CIDRS)}")
    else:
        print("  • Access:         open to this network (no password)")
    if not sec.is_loopback_bind(args.host):
        print("  ⚠  Listening on a network interface: any device that can reach")
        print("     this port can control playback. Set WEB_COMPANION_ALLOW_CIDR")
        print("     to restrict it if this network is not trusted.")
    print(f"======================================================================\n")

    def _report_stream_queue() -> None:
        stream_thread.join()
        state = autoplay_state()
        if state.get("status") == "ready":
            print(f"  ▶ AUTO-STREAM ARMED: {state.get('detail', '')}", flush=True)
            print(f"  • First track:    {state.get('track', '')}", flush=True)
        else:
            print(f"  ⏹ NO STREAM QUEUE: {state.get('detail', '')}", flush=True)
        print(f"======================================================================\n", flush=True)

    threading.Thread(target=_report_stream_queue, name="mp-companion-queue-report", daemon=True).start()

    print("Press Ctrl+C to stop the companion server.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Web Companion...")
        httpd.server_close()


if __name__ == "__main__":
    main()
