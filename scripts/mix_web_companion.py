#!/usr/bin/env python3
"""
MP Mix Manager - Studio & DJ Booth Mobile Web Companion
Lightweight, standalone HTTP & REST server providing a modern, theme-adaptive
web dashboard accessible from phones, tablets, and remote browsers on the local LAN.
Displays live mix playback, tracklists, EQ visualizers, and remote playback controls.
Automatically synchronizes with the active theme currently running in Manager.
"""

import argparse
import html
import http.server
import json
import os
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    return "0.5.0 Stable (Sensorium)"

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
        "bg": "#000000",
        "card_bg": "#110e20",
        "card_border": "rgba(166, 77, 255, 0.38)",
        "card_shadow": "0 12px 35px rgba(0, 0, 0, 0.9)",
        "text_main": "#d1d1e0",
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
    "cosmic_dreamworlds_radioactive": {
        "id": "cosmic_dreamworlds_radioactive",
        "name": "Cosmic Dreamworlds Radioactive (Liquid Glass Skin)",
        "bg": "#050e08",
        "card_bg": "rgba(8, 20, 14, 0.78)",
        "card_border": "rgba(57, 255, 20, 0.45)",
        "card_shadow": "0 14px 45px rgba(0, 0, 0, 0.85), 0 0 25px rgba(57, 255, 20, 0.22)",
        "text_main": "#ffffff",
        "text_dim": "#a2e6b5",
        "accent_primary": "#39ff14",
        "accent_secondary": "#00f5d4",
        "accent_tertiary": "#a64dff",
        "accent_crimson": "#00ff66",
        "header_gradient": "linear-gradient(135deg, #39ff14 0%, #00f5d4 50%, #a64dff 100%)",
        "btn_gradient": "linear-gradient(135deg, #39ff14, #00f5d4)",
        "btn_shadow": "0 4px 18px rgba(57, 255, 20, 0.45)",
        "progress_fill": "linear-gradient(90deg, #39ff14, #00f5d4, #a64dff)",
        "badge_bg": "rgba(57, 255, 20, 0.20)",
        "badge_color": "#39ff14",
        "eq_bar": "#39ff14",
        "eq_peak": "#00f5d4",
    },
    "cosmic_dreamworlds": {
        "id": "cosmic_dreamworlds",
        "name": "Cosmic Dreamworlds (Liquid Glass Skin)",
        "bg": "#0c040d",
        "card_bg": "rgba(22, 9, 25, 0.78)",
        "card_border": "rgba(232, 13, 104, 0.45)",
        "card_shadow": "0 14px 45px rgba(0, 0, 0, 0.85), 0 0 25px rgba(232, 13, 104, 0.22)",
        "text_main": "#ffffff",
        "text_dim": "#fbcfe8",
        "accent_primary": "#e80d68",
        "accent_secondary": "#00f5d4",
        "accent_tertiary": "#b058ff",
        "accent_crimson": "#ff007f",
        "header_gradient": "linear-gradient(135deg, #e80d68 0%, #b058ff 50%, #00f5d4 100%)",
        "btn_gradient": "linear-gradient(135deg, #e80d68, #b058ff)",
        "btn_shadow": "0 4px 18px rgba(232, 13, 104, 0.45)",
        "progress_fill": "linear-gradient(90deg, #e80d68, #b058ff, #00f5d4)",
        "badge_bg": "rgba(232, 13, 104, 0.20)",
        "badge_color": "#e80d68",
        "eq_bar": "#e80d68",
        "eq_peak": "#00f5d4",
    },
    "orbital_void_reflux": {
        "id": "orbital_void_reflux",
        "name": "Orbital Void Reflux (Liquid Glass Skin)",
        "bg": "#080714",
        "card_bg": "rgba(15, 13, 36, 0.78)",
        "card_border": "rgba(0, 240, 255, 0.45)",
        "card_shadow": "0 14px 45px rgba(0, 0, 0, 0.85), 0 0 25px rgba(157, 78, 221, 0.25)",
        "text_main": "#ffffff",
        "text_dim": "#c8b6ff",
        "accent_primary": "#00f0ff",
        "accent_secondary": "#ff007f",
        "accent_tertiary": "#9d4edd",
        "accent_crimson": "#ff0055",
        "header_gradient": "linear-gradient(135deg, #00f0ff 0%, #9d4edd 50%, #ff007f 100%)",
        "btn_gradient": "linear-gradient(135deg, #00f0ff, #9d4edd)",
        "btn_shadow": "0 4px 18px rgba(0, 240, 255, 0.45)",
        "progress_fill": "linear-gradient(90deg, #00f0ff, #9d4edd, #ff007f)",
        "badge_bg": "rgba(0, 240, 255, 0.20)",
        "badge_color": "#00f0ff",
        "eq_bar": "#00f0ff",
        "eq_peak": "#ff007f",
    }
}

THEME_ALIASES: Dict[str, str] = {
    "cosmic_dreamworlds": "cosmic_dreamworlds",
    "cosmic_dreamworlds_(liquid_glass_skin)": "cosmic_dreamworlds",
    "cosmic dreamworlds (liquid glass skin)": "cosmic_dreamworlds",
    "cosmic dreamworlds": "cosmic_dreamworlds",
    "cosmic_dream": "cosmic_dreamworlds",
    "cosmic dream": "cosmic_dreamworlds",
    "cosmic_dreamworld": "cosmic_dreamworlds",
    "cosmic dreamworld": "cosmic_dreamworlds",
    "cosmic_dreamworlds_radioactive": "cosmic_dreamworlds_radioactive",
    "cosmic_dreamworlds_radioactive_(liquid_glass_skin)": "cosmic_dreamworlds_radioactive",
    "cosmic dreamworlds radioactive (liquid glass skin)": "cosmic_dreamworlds_radioactive",
    "cosmic dreamworlds radioactive": "cosmic_dreamworlds_radioactive",
    "cosmic_radioactive": "cosmic_dreamworlds_radioactive",
    "radioactive": "cosmic_dreamworlds_radioactive",
    "orbital_void_reflux": "orbital_void_reflux",
    "orbital_void_reflux_(liquid_glass_skin)": "orbital_void_reflux",
    "orbital void reflux (liquid glass skin)": "orbital_void_reflux",
    "orbital void reflux": "orbital_void_reflux",
    "orbital_void": "orbital_void_reflux",
    "void_reflux": "orbital_void_reflux",
    "orbital": "orbital_void_reflux",
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
    "default": "dreamworlds",
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
}

CLI_THEME: Optional[str] = None
WEB_THEME_FILE = Path.home() / ".config" / "mix-manager" / "web_companion_theme"
_tracklist_cache: Dict[str, str] = {}


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

    return "cosmic_supernova" if "cosmic_supernova" in THEMES else "dreamworlds"


def get_theme_palette(cli_theme: Optional[str] = None) -> Dict[str, Any]:
    tid = get_current_theme_id(cli_theme)
    return THEMES.get(tid, THEMES["dreamworlds"])


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
# STREAMING SERVICE STATE
#
# This process is an HTTP streamer only. It deliberately contains no IPC to a
# local audio player, because it must never cause sound on the host machine.
# ------------------------------------------------------------------------------
CONFIG_DIR = Path.home() / ".config" / "mix-manager"

AUDIO_EXTENSIONS = (".flac", ".wav", ".mp3", ".ogg", ".m4a", ".aac", ".aiff", ".wma")
AUDIO_CONTENT_TYPES = {
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".aiff": "audio/aiff",
    ".wma": "audio/x-ms-wma",
}
RESUME_FILE = CONFIG_DIR / "playback_resume.json"
PLAYLIST_DIR = CONFIG_DIR / "playlists"

_autoplay_done = threading.Event()
_autoplay_result: Dict[str, Any] = {"status": "pending", "detail": ""}


# ------------------------------------------------------------------------------
# MP ASSISTANT BRIDGE (MP Harmony Agent)
#
# The MP Harmony Agent listens on 127.0.0.1:11435, so a phone on the LAN cannot
# reach it directly. Everything below proxies the agent's OpenAI-compatible API
# through this server, which is already bound to 0.0.0.0. That keeps the phone
# talking to one origin and one port, and keeps speech audio on the same path.
# ------------------------------------------------------------------------------
ASSISTANT_URL = os.environ.get("MP_ASSISTANT_URL", "http://127.0.0.1:11435").rstrip("/")
ASSISTANT_TIMEOUT = float(os.environ.get("MP_ASSISTANT_TIMEOUT", "600"))


def assistant_url() -> str:
    """Agent base URL. Read per call so config.env edits apply without a restart."""
    configured = os.environ.get("MP_ASSISTANT_URL") or _config_value("MP_ASSISTANT_URL", "")
    return (configured or ASSISTANT_URL).rstrip("/")


def assistant_enabled() -> bool:
    """The tab is on unless the operator turns it off in config.env."""
    return _env_flag("MP_ASSISTANT_ENABLED", True)


def assistant_request(path: str, payload: Optional[Dict[str, Any]] = None,
                      session_id: str = "", timeout: float = 5.0):
    """Make a JSON request to the MP Harmony Agent. Returns (status, body)."""
    url = assistant_url() + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if session_id:
        headers["X-Session-Id"] = session_id
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        try:
            return err.code, err.read()
        except Exception:
            return err.code, b""
    except Exception as err:
        return 0, json.dumps({"error": str(err)}).encode("utf-8")


def assistant_status() -> Dict[str, Any]:
    """Health summary used by the tab badge and the voice default."""
    if not assistant_enabled():
        return {"available": False, "status": "disabled",
                "detail": "Set MP_ASSISTANT_ENABLED=true in config.env to enable the tab."}
    code, body = assistant_request("/health", timeout=3.0)
    if code != 200:
        return {"available": False, "status": "offline", "port": PORT,
                "detail": "MP Harmony Agent is not answering on port 11435."}
    try:
        health = json.loads(body.decode("utf-8"))
    except Exception:
        health = {}
    return {
        "available": True,
        "status": "online",
        "agent": health.get("agent", "MP Harmony AI Agent"),
        "model": health.get("model", "gpt-oss-pinned:latest"),
        "voice": health.get("active_voice", "stephen_hawking_matrix"),
        "tools_count": health.get("tools_count", 0),
    }


def assistant_voices() -> List[Dict[str, str]]:
    """Voice list mirrors MP_Harmony_Agent.VOICES_MAP (kept in step manually)."""
    known = [
        ("stephen_hawking_matrix", "Stephen Hawking (Enters the Matrix) [Default]"),
        ("morpheus", "Morpheus (The Matrix - Master)"),
        ("morpheus_construct", "Morpheus (The Construct)"),
        ("morpheus_redpill", "Morpheus (Red Pill)"),
        ("morpheus_dojo", "Morpheus (Combat Dojo)"),
        ("morpheus_system", "Morpheus (The System)"),
        ("agent_smith", "Agent Smith (The Matrix)"),
        ("neo", "Neo (The Matrix)"),
        ("trinity", "Trinity (The Matrix)"),
        ("oracle", "The Oracle (The Matrix)"),
        ("zion", "Zion (Councillor Hamann)"),
    ]
    return [{"id": vid, "name": label} for vid, label in known]


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
        raw = read_config_env().get(name)
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


def scan_playlist_files() -> List[str]:
    """Return the playlist the companion should play from.

    Order of preference:
      1. WEB_COMPANION_PLAYLIST (explicit .m3u/.m3u8/.json file)
      2. Saved player playlist for the configured mode (MUSIC or MIX_ARCHIVE)
      3. Every audio file in the configured archive folders, newest first

    An empty list means "there is nothing to play" and autoplay is skipped.
    """
    explicit = _config_value("WEB_COMPANION_PLAYLIST", "").strip()
    if explicit:
        return _read_playlist_file(Path(os.path.expanduser(explicit)))

    mode = (_config_value("WEB_COMPANION_MODE", "MIX_ARCHIVE") or "MIX_ARCHIVE").upper()
    if mode == "MUSIC":
        for cand in (PLAYLIST_DIR / "DJ_Tracks_ALL_MUSIC_FOLDERS.json",
                     PLAYLIST_DIR / "DJ_Tracks_ALL_MUSIC_FOLDERS.m3u"):
            if cand.is_file():
                files = _read_playlist_file(cand)
                if files:
                    return files

    return scan_archive_files()


def _read_playlist_file(path: Path) -> List[str]:
    if not path.is_file():
        return []
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            raw = data.get("tracks") if isinstance(data, dict) else data
            paths: List[str] = []
            for item in raw or []:
                if isinstance(item, dict):
                    item = item.get("path") or item.get("file") or ""
                if isinstance(item, str) and is_audio_file(item):
                    paths.append(item)
            return [p for p in paths if os.path.isfile(p)]
        paths = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if is_audio_file(line):
                paths.append(line)
        return [p for p in paths if os.path.isfile(p)]
    except Exception:
        return []


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
    return dirs


def scan_archive_files() -> List[str]:
    """Newest-first audio files across the configured archive folders."""
    skip = {"__pycache__", "node_modules", ".git", "venv", ".venv"}
    found: List[Tuple[float, str]] = []
    seen = set()
    for root_dir in _archive_dirs():
        for root, subdirs, files in os.walk(root_dir):
            subdirs[:] = [d for d in subdirs if not d.startswith(".") and d not in skip]
            for name in files:
                if not is_audio_file(name):
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
                if st.st_size <= 0:
                    continue
                found.append((st.st_mtime, full))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found]


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


_queue_cache: Dict[str, Any] = {"files": [], "built_at": 0.0}
_queue_lock = threading.Lock()
QUEUE_TTL_SECONDS = 30.0


def build_stream_queue(force: bool = False) -> List[str]:
    """Resolve the play queue the phones will stream through.

    The result is cached briefly: the page polls several endpoints per second
    and each call would otherwise re-walk the whole archive. Callers that need
    to see a newly added file can pass force=True.
    """
    with _queue_lock:
        now = time.monotonic()
        files = _queue_cache["files"]
        fresh = files and (now - _queue_cache["built_at"]) < QUEUE_TTL_SECONDS
        if fresh and not force:
            return list(files)

    files = scan_playlist_files()
    if files and _env_flag("WEB_COMPANION_SHUFFLE", False):
        files = list(files)
        random.shuffle(files)

    with _queue_lock:
        _queue_cache["files"] = list(files)
        _queue_cache["built_at"] = time.monotonic()
    return files


def invalidate_stream_queue() -> None:
    with _queue_lock:
        _queue_cache["built_at"] = 0.0


def autoplay_once() -> Dict[str, Any]:
    """Pick the track to serve once, at startup. Never starts local playback."""
    if _autoplay_done.is_set():
        return dict(_autoplay_result)

    playlist = build_stream_queue()
    track, position = _pick_start_track(playlist)
    if not track:
        _autoplay_result.update(status="empty_playlist", detail="Playlist is empty")
        _autoplay_done.set()
        return dict(_autoplay_result)

    _autoplay_result.update(
        status="ready",
        detail=f"Streaming queue ready ({len(playlist)} mixes)",
        match=track,
        track=os.path.basename(track),
        position=round(position, 2),
        queue_len=len(playlist),
        verified=True,
        verified_position=round(position, 2),
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
        "shuffle": False,
        "repeat": "all",
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


def stream_position(state: Dict[str, Any]) -> float:
    """Elapsed seconds within the current file, accounting for active playback."""
    base = float(state.get("position_sec", 0.0) or 0.0)
    if state.get("playing") and state.get("started_at"):
        base += max(0.0, time.time() - float(state["started_at"]))
    return max(0.0, base)


def clamp_position(state: Dict[str, Any], pos: float) -> float:
    duration = float(state.get("duration_sec", 0.0) or 0.0)
    pos = max(0.0, float(pos))
    return min(pos, duration) if duration > 0 else pos


def current_stream_file() -> Tuple[str, Dict[str, Any]]:
    """Return the file the phone should currently be streaming."""
    queue = build_stream_queue()
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
        state["path"] = queue[idx]
    return queue[idx], state


def get_eq_status() -> Dict[str, Any]:
    active_file, state = current_stream_file()
    playing = bool(state.get("playing"))
    pos = stream_position(state)
    dur = float(state.get("duration_sec", 0.0) or 0.0)
    pct = round(pos / dur * 100.0, 1) if dur > 0 else 0.0
    return {
        "state": "playing" if playing else ("paused" if active_file else "stopped"),
        "position": format_seconds(pos),
        "duration": format_seconds(dur) if dur > 0 else "--:--",
        "position_sec": round(pos, 2),
        "duration_sec": round(dur, 2),
        "progress_pct": pct,
        "volume": int(state.get("volume", 100)),
        "pitch_percent": 0,
        "shuffle": bool(state.get("shuffle", False)),
        "repeat": str(state.get("repeat", "all")),
        "levels": [0.0] * 24,
        "peaks": [0.0] * 24,
    }


def format_seconds(value: Any) -> str:
    try:
        total = int(max(0.0, float(value)))
    except (TypeError, ValueError):
        total = 0
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


_duration_cache: Dict[str, Tuple[float, float, float]] = {}


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
        duration = float(soundfile.info(path).duration or 0.0)
    except Exception:
        duration = 0.0
    if duration <= 0.0:
        duration = _wav_duration(path)
    _duration_cache[path] = (st.st_mtime, st.st_size, duration)
    return duration


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


def get_playback_status(cli_theme: Optional[str] = None) -> Dict[str, Any]:
    active_theme = get_theme_palette(cli_theme)
    active_file, state = current_stream_file()
    queue = build_stream_queue()

    duration = probe_duration(active_file) if active_file else 0.0
    if duration > 0 and abs(float(state.get("duration_sec", 0.0) or 0.0) - duration) > 0.5:
        state["duration_sec"] = duration

    pos = clamp_position(state, stream_position(state))
    pct = round(pos / duration * 100.0, 1) if duration > 0 else 0.0
    playing = bool(state.get("playing")) and bool(active_file)

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
        "progress_pct": pct,
        "active_file": active_file,
        "format": Path(active_file).suffix.lstrip(".").upper() if active_file else "",
        "sample_rate": "",
        "channels": "Stereo",
        "volume": int(state.get("volume", 100)),
        "pitch_percent": 0,
        "pitch_factor": 1.0,
        "shuffle": bool(state.get("shuffle", False)),
        "repeat": str(state.get("repeat", "all")),
        "levels": [0.0] * 24,
        "peaks": [0.0] * 24,
        "mini_eq": "",
        "tracklist": tracklist,
        "tracklist_total": len(tracklist),
        "queue_len": len(queue),
        "queue_position": int(state.get("index", 0) or 0) + 1,
        "theme": active_theme,
    }


MINI_PLAYLIST_SIZE = 3


def get_mini_playlist(limit: int = MINI_PLAYLIST_SIZE) -> Dict[str, Any]:
    """A short window of the stream queue: the current mix plus the next ones.

    Only `limit` entries are returned, so the phone shows a compact playlist
    instead of the whole archive.
    """
    queue = build_stream_queue()
    if not queue:
        return {"items": [], "total": 0, "current_index": -1}

    state = load_stream_session()
    idx = int(state.get("index", 0) or 0)
    saved = str(state.get("path", "") or "")
    if saved and saved in queue:
        idx = queue.index(saved)
    idx = max(0, min(idx, len(queue) - 1))

    items: List[Dict[str, Any]] = []
    for offset in range(max(1, int(limit))):
        i = (idx + offset) % len(queue)
        path = queue[i]
        title, artist, album = clean_track_title("", path, {})
        items.append({
            "index": i,
            "title": title,
            "artist": artist,
            "file": path,
            "current": i == idx,
            "duration": format_seconds(probe_duration(path)),
            "duration_sec": round(probe_duration(path), 2),
        })
    return {"items": items, "total": len(queue), "current_index": idx}


def control_player(action: str, params: Optional[Dict[str, List[str]]] = None) -> bool:
    """Drive the stream session. The browser performs the actual playback."""
    params = params or {}
    if action in ("refresh", "rescan"):
        invalidate_stream_queue()
        return True
    queue = build_stream_queue()
    if not queue:
        return False

    with _stream_lock:
        state = load_stream_session()
        path, _ = current_stream_file()
        if path and state.get("path") != path:
            state["path"] = path
        if state.get("path"):
            try:
                state["index"] = queue.index(state["path"])
            except ValueError:
                state["index"] = 0
                state["path"] = queue[0]

        pos = clamp_position(state, stream_position(state))
        duration = float(state.get("duration_sec", 0.0) or 0.0) or probe_duration(state["path"])
        # Make the known length visible to clamp_position() before the action
        # runs, otherwise a fresh session (duration 0) lets a skip run past the
        # end of the mix and the reported clock overshoots.
        if duration > 0:
            state["duration_sec"] = duration

        def seek_to(target: float) -> None:
            state["position_sec"] = clamp_position(state, target)
            state["started_at"] = time.time() if state.get("playing") else 0.0

        def switch(step: int) -> None:
            state["index"] = (int(state.get("index", 0)) + step) % len(queue)
            state["path"] = queue[state["index"]]
            state["position_sec"] = 0.0
            state["started_at"] = time.time() if state.get("playing") else 0.0
            state["duration_sec"] = probe_duration(state["path"])

        if action in ("play", "resume"):
            state["playing"] = True
            state["started_at"] = time.time()
        elif action in ("pause", "play-pause", "toggle"):
            if state.get("playing"):
                state["playing"] = False
                state["position_sec"] = pos
                state["started_at"] = 0.0
            else:
                state["playing"] = True
                state["position_sec"] = pos
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
        elif action in ("shuffle", "toggle_shuffle"):
            state["shuffle"] = not bool(state.get("shuffle"))
        elif action in ("repeat", "cycle_repeat"):
            order = ["off", "all", "one"]
            cur = str(state.get("repeat", "all"))
            state["repeat"] = order[(order.index(cur) + 1) % len(order)] if cur in order else "all"
        elif action == "vol_up":
            state["volume"] = min(100, int(state.get("volume", 100)) + 5)
        elif action == "vol_down":
            state["volume"] = max(0, int(state.get("volume", 100)) - 5)
        elif action == "set_volume":
            state["volume"] = max(0, min(100, int(params.get("volume", [100])[0])))
        elif action == "mute":
            state["volume"] = 0
        elif action == "track_ended":
            # The phone finished the file: advance and keep the elapsed clock honest.
            switch(1)
            state["duration_sec"] = probe_duration(state["path"])
            state["playing"] = True
            state["started_at"] = time.time()
        else:
            return False

        if duration > 0:
            state["duration_sec"] = duration
        save_stream_session(state)
    return True


# ------------------------------------------------------------------------------
# DYNAMIC HTML TEMPLATE (SEAMLESS AUDIO STREAMING & SYNCED SEEKING)
# ------------------------------------------------------------------------------
def render_html_page(theme: Dict[str, Any]) -> str:
    t = theme
    is_rainbow = t.get("is_dynamic_rainbow", False) or t['id'] == "dynamic_rainbow_liquid_glass"
    
    rainbow_anim_css = """
        @keyframes rainbowShift {
            0% { filter: hue-rotate(0deg); }
            100% { filter: hue-rotate(360deg); }
        }
        .rainbow-animated {
            animation: rainbowShift 12s linear infinite;
        }
    """ if is_rainbow else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>MP Mix Manager — DJ Booth Remote</title>
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
        .theme-select {{
            appearance: none;
            -webkit-appearance: none;
            margin-top: 10px;
            padding: 8px 34px 8px 14px;
            border-radius: 12px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.3px;
            color: var(--text-main);
            background-color: var(--card-bg);
            background-image: linear-gradient(45deg, transparent 50%, var(--accent-secondary) 50%),
                              linear-gradient(135deg, var(--accent-secondary) 50%, transparent 50%);
            background-position: calc(100% - 18px) 52%, calc(100% - 12px) 52%;
            background-size: 6px 6px, 6px 6px;
            background-repeat: no-repeat;
            border: 1px solid var(--card-border);
            box-shadow: var(--card-shadow);
            cursor: pointer;
            max-width: 100%;
        }}
        .theme-select:focus {{
            outline: none;
            border-color: var(--accent-primary);
        }}
        .theme-select option {{
            background: #14141c;
            color: #ffffff;
        }}
        /* Primary tab bar: MP Liquid Player | MP Assistant */
        .tab-bar {{
            display: flex;
            width: 100%;
            max-width: 520px;
            gap: 6px;
            margin: 0 0 14px 0;
            padding: 5px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            box-shadow: var(--card-shadow);
            position: sticky;
            top: 8px;
            z-index: 500;
        }}
        .tab-btn {{
            flex: 1;
            appearance: none;
            -webkit-appearance: none;
            padding: 11px 8px;
            border: 1px solid transparent;
            border-radius: 10px;
            background: transparent;
            color: var(--text-dim);
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0.4px;
            text-transform: uppercase;
            cursor: pointer;
            transition: background 0.18s ease, color 0.18s ease;
        }}
        .tab-btn.active {{
            background: var(--btn-gradient);
            color: #fff;
            border-color: var(--accent-primary);
            box-shadow: var(--btn-shadow);
        }}
        .tab-panel {{
            /* The cards used to be direct flex children of <body>, which is what
               centred them. Keep that alignment now they sit inside a panel. */
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 100%;
        }}
        .tab-panel[hidden] {{
            display: none !important;
        }}
        /* MP Assistant chat */
        .assistant-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.07);
            flex-wrap: wrap;
        }}
        .assistant-title {{
            font-size: 0.95rem;
            font-weight: 800;
            color: var(--accent-secondary);
            letter-spacing: 0.5px;
        }}
        .assistant-status {{
            font-size: 0.7rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            padding: 3px 10px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid var(--card-border);
            color: var(--text-dim);
        }}
        .assistant-status.online {{
            background: var(--badge-bg);
            color: var(--badge-color);
            border-color: var(--badge-color);
        }}
        .assistant-status.offline {{
            background: rgba(255, 170, 0, 0.12);
            color: #ffaa00;
            border-color: #ffaa00;
        }}
        .assistant-voice-row {{
            display: flex;
            align-items: center;
            gap: 8px;
            width: 100%;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }}
        .assistant-voice-row label {{
            font-size: 0.72rem;
            font-weight: 700;
            color: var(--text-dim);
            letter-spacing: 0.4px;
        }}
        .assistant-voice-select {{
            flex: 1;
            min-width: 150px;
            appearance: none;
            -webkit-appearance: none;
            padding: 7px 30px 7px 12px;
            border-radius: 10px;
            font-size: 0.76rem;
            font-weight: 700;
            color: var(--text-main);
            background-color: rgba(255, 255, 255, 0.05);
            background-image: linear-gradient(45deg, transparent 50%, var(--accent-secondary) 50%),
                              linear-gradient(135deg, var(--accent-secondary) 50%, transparent 50%);
            background-position: calc(100% - 17px) 52%, calc(100% - 11px) 52%;
            background-size: 6px 6px, 6px 6px;
            background-repeat: no-repeat;
            border: 1px solid var(--card-border);
            cursor: pointer;
        }}
        .assistant-voice-select option {{
            background: #14141c;
            color: #ffffff;
        }}
        .assistant-log {{
            display: flex;
            flex-direction: column;
            gap: 10px;
            min-height: 220px;
            max-height: 46vh;
            overflow-y: auto;
            padding: 4px 2px 8px 2px;
            margin-bottom: 12px;
            scroll-behavior: smooth;
            -webkit-overflow-scrolling: touch;
        }}
        .msg {{
            padding: 10px 13px;
            border-radius: 14px;
            font-size: 0.86rem;
            line-height: 1.5;
            white-space: pre-wrap;
            word-break: break-word;
            max-width: 92%;
        }}
        .msg.user {{
            align-self: flex-end;
            background: var(--btn-gradient);
            color: #fff;
            border-bottom-right-radius: 4px;
            font-weight: 600;
        }}
        .msg.agent {{
            align-self: flex-start;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            border-bottom-left-radius: 4px;
        }}
        .msg.agent.error {{
            border-color: #ffaa00;
            color: #ffaa00;
            background: rgba(255, 170, 0, 0.08);
        }}
        .msg-hint {{
            align-self: center;
            font-size: 0.72rem;
            color: var(--text-dim);
            text-align: center;
            padding: 6px 10px;
            opacity: 0.85;
        }}
        .assistant-input-row {{
            display: flex;
            align-items: flex-end;
            gap: 8px;
            width: 100%;
            padding-top: 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }}
        .assistant-input {{
            flex: 1;
            resize: none;
            min-height: 46px;
            max-height: 130px;
            padding: 12px 14px;
            border-radius: 12px;
            border: 1px solid var(--card-border);
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-main);
            font-family: inherit;
            /* 16px keeps iOS Safari from zooming the page on focus. */
            font-size: 16px;
            line-height: 1.35;
        }}
        .assistant-input:focus {{
            outline: none;
            border-color: var(--accent-primary);
        }}
        .assistant-input::placeholder {{
            color: var(--text-dim);
            opacity: 0.7;
        }}
        .assistant-send {{
            background: var(--btn-gradient);
            color: #fff;
            border: none;
            box-shadow: var(--btn-shadow);
            padding: 12px 16px;
            font-size: 0.88rem;
            font-weight: 800;
            border-radius: 12px;
            min-width: 62px;
        }}
        .assistant-send:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        .assistant-actions {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            width: 100%;
            margin-top: 10px;
            flex-wrap: wrap;
        }}
        .assistant-actions button {{
            padding: 7px 12px;
            font-size: 0.74rem;
            border-radius: 9px;
            font-weight: 700;
        }}
        .voice-toggle {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 0.74rem;
            font-weight: 700;
            color: var(--text-dim);
        }}
        .assistant-audio {{
            /* Laid out but invisible. `display: none` can stop iOS Safari from
               playing the reply element at all, so it is moved off-screen
               instead of removed from the layout. */
            position: absolute;
            left: -9999px;
            width: 1px;
            height: 1px;
            opacity: 0;
            pointer-events: none;
        }}
        .assistant-replay {{
            display: inline-block;
            margin-top: 8px;
            padding: 6px 11px;
            font-size: 0.72rem;
            font-weight: 700;
            border-radius: 9px;
            border: 1px solid var(--card-border);
            background: rgba(255, 255, 255, 0.06);
            color: var(--accent-secondary);
            cursor: pointer;
        }}
        .assistant-replay:active {{
            transform: scale(0.97);
        }}
        .assistant-speaking {{
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.4px;
            color: var(--accent-secondary);
            opacity: 0.9;
        }}
        .mini-playlist {{
            margin-top: 14px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .mini-pl-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 0.7px;
            color: var(--text-dim);
            text-transform: uppercase;
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
        .assistant-card {{
            /* The chat log is the scrolling region, so the card itself must not
               be squeezed on a short iPhone viewport. */
            display: flex;
            flex-direction: column;
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
        /* Stream Activation Modal */
        #streamOverlay {{
            display: flex;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(5, 4, 15, 0.92);
            z-index: 9999;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 24px;
            text-align: center;
        }}
        .overlay-card {{
            background: var(--card-bg);
            border: 2px solid var(--accent-primary);
            border-radius: 20px;
            padding: 28px 20px;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 16px 50px rgba(0, 0, 0, 0.9), 0 0 30px rgba(176, 88, 255, 0.3);
        }}
        .overlay-card h2 {{
            margin: 0 0 10px 0;
            font-size: 1.4rem;
            background: var(--header-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 900;
        }}
        .overlay-card p {{
            font-size: 0.88rem;
            color: var(--text-dim);
            margin: 0 0 20px 0;
            line-height: 1.4;
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
        .overlay-skip {{
            margin-top: 14px;
            background: transparent;
            border: none;
            box-shadow: none;
            color: var(--text-dim);
            font-size: 0.8rem;
            font-weight: 700;
            padding: 8px;
            width: 100%;
            cursor: pointer;
            text-decoration: underline;
        }}
        .visualizer-container {{
            width: 100%;
            height: 56px;
            display: flex;
            align-items: flex-end;
            justify-content: center;
            gap: 3px;
            margin: 12px 0 16px 0;
            padding: 4px 6px;
            background: rgba(0, 0, 0, 0.35);
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.06);
            position: relative;
        }}
        .v-col {{
            flex: 1;
            max-width: 14px;
            height: 100%;
            display: flex;
            align-items: flex-end;
            position: relative;
        }}
        .v-bar {{
            width: 100%;
            height: 4%;
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
            bottom: 4%;
            pointer-events: none;
        }}
        .progress-box {{
            width: 100%;
            margin-bottom: 16px;
        }}
        .progress-bar-bg {{
            width: 100%;
            height: 9px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 5px;
            cursor: pointer;
            overflow: hidden;
            position: relative;
            touch-action: none;
        }}
        .progress-bar-fill {{
            height: 100%;
            width: 0%;
            background: var(--progress-fill);
            border-radius: 5px;
            transition: width 0.15s ease-out;
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
        .sec-controls {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            width: 100%;
            margin-top: 14px;
            padding-top: 12px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
        }}
        .control-group {{
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 1;
            justify-content: center;
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
    </style>
</head>
<body>
    <!-- Stream Activation Modal to Satisfy Mobile Autoplay Policies -->
    <div id="streamOverlay">
        <div class="overlay-card">
            <h2>🎧 MP Mix Stream</h2>
            <p>Tap below to engage mobile studio audio streaming and dashboard synchronization.</p>
            <button class="btn-activate-stream" onclick="activateMobileStream()">Engage Audio Stream</button>
            <button class="overlay-skip" onclick="dismissStreamOverlay()">Skip — use the assistant only</button>
        </div>
    </div>

    <div class="header">
        <h1>MP MIX MANAGER <span class="app-version">v{html.escape(VERSION)}</span></h1>
        <p>Studio & DJ Booth Mobile Web Companion</p>
        <div id="themeBadge" class="theme-pill">🎨 <span id="themeNameText">{html.escape(t['name'])}</span></div>
        <div>
            <select id="themeSelect" class="theme-select" onchange="changeTheme(this.value)"
                    aria-label="Choose theme" title="Change the companion theme">
                <option value="{html.escape(t['id'])}">{html.escape(t['name'])}</option>
            </select>
        </div>
        <div id="autoplayNotice" class="autoplay-notice" style="display: none;"></div>
    </div>

    <!-- Primary tabs: the player and the assistant share one page -->
    <div class="tab-bar" role="tablist" aria-label="MP Web Companion sections">
        <button class="tab-btn active" id="tabBtnPlayer" role="tab" aria-selected="true"
                aria-controls="panelPlayer" onclick="showTab('player')">🎧 MP Liquid Player</button>
        <button class="tab-btn" id="tabBtnAssistant" role="tab" aria-selected="false"
                aria-controls="panelAssistant" onclick="showTab('assistant')">🧠 MP Assistant</button>
    </div>

    <div class="tab-panel" id="panelPlayer" role="tabpanel" aria-labelledby="tabBtnPlayer">
    <div class="card now-playing">
        <div id="mobileAudioBanner" class="mobile-audio-banner">
            <h4>📱 Mobile Audio Stream Active</h4>
            <p>High-resolution FLAC/WAV streaming active</p>
        </div>

        <!-- HTML5 Audio Streaming Element -->
        <audio id="mobileAudioStream" preload="auto" style="display: none;"></audio>

        <div class="badges-row">
            <div id="playBadge" class="badge">STANDBY</div>
            <div id="shuffleBadge" class="badge badge-shuffle" onclick="sendAction('shuffle')">🔀 SHUFFLE OFF</div>
            <div id="sourceBadge" class="badge badge-source">CHECKING...</div>
        </div>

        <div class="track-info">
            <div id="trackTitle" class="track-title">Connecting to MP Mix Session...</div>
            <div id="trackArtist" class="track-artist">—</div>
            <div id="trackSpecs" class="track-specs">
                <span id="specFormat" class="spec-chip">FLAC / WAV</span>
                <span id="specRate" class="spec-chip">48 kHz</span>
                <span id="specChannels" class="spec-chip">Stereo</span>
            </div>
        </div>

        <!-- 24-Bar Audio Visualizer -->
        <div class="visualizer-container" id="visContainer">
            {"".join(f'<div class="v-col"><div class="v-bar" id="vb{i}"></div><div class="v-peak" id="vp{i}"></div></div>' for i in range(24))}
        </div>

        <!-- Progress & Time -->
        <div class="progress-box">
            <div class="progress-bar-bg" id="progressBg" onclick="seekClick(event)">
                <div class="progress-bar-fill" id="progressBar"></div>
            </div>
            <div class="time-row">
                <span id="posTime">00:00</span>
                <span id="durTime">--:--</span>
            </div>
        </div>

        <!-- Primary Controls -->
        <div class="controls-row">
            <button class="btn-sub" onclick="skipBy(-30)">⏪ 30s</button>
            <button onclick="sendAction('previous')">⏮</button>
            <button class="btn-main" id="btnPlayPause" onclick="handlePlayPauseClick()">⏯ Play</button>
            <button onclick="sendAction('next')">⏭</button>
            <button class="btn-sub" onclick="skipBy(30)">30s ⏩</button>
        </div>

        <!-- Mode Controls -->
        <div class="mode-controls-row">
            <button class="btn-sub btn-mode" id="btnShuffle" onclick="sendAction('shuffle')">
                <span>🔀</span> Shuffle: <span id="shufText">OFF</span>
            </button>
            <button class="btn-sub btn-mode" id="btnRepeat" onclick="sendAction('repeat')">
                <span>🔁</span> Repeat: <span id="repText">ALL</span>
            </button>
        </div>

        <!-- Secondary Controls -->
        <div class="sec-controls">
            <div class="control-group">
                <button class="btn-mini" onclick="sendAction('vol_down')">🔉 -</button>
                <span class="control-pill" id="volPill">Vol: 85%</span>
                <button class="btn-mini" onclick="sendAction('vol_up')">+ 🔊</button>
                <button class="btn-mini" onclick="sendAction('mute')">🔇</button>
            </div>
            <div class="control-group">
                <button class="btn-mini" onclick="sendAction('pitch_down')">[-]</button>
                <span class="control-pill" id="pitchPill">Pitch: 0%</span>
                <button class="btn-mini" onclick="sendAction('pitch_up')">[+]</button>
                <button class="btn-mini" onclick="sendAction('reset_pitch')">0</button>
            </div>
        </div>
        <!-- Mini Playlist: the current mix plus the next two -->
        <div class="mini-playlist" id="miniPlaylist">
            <div class="mini-pl-head">
                <span>🎵 Up Next</span>
                <span id="miniPlCount">3</span>
            </div>
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
    </div><!-- /panelPlayer -->

    <!-- MP Assistant: live MP Harmony prompt -->
    <div class="tab-panel" id="panelAssistant" role="tabpanel" aria-labelledby="tabBtnAssistant" hidden>
        <div class="card assistant-card">
            <div class="assistant-head">
                <span class="assistant-title">🧠 MP ASSISTANT</span>
                <span class="assistant-status" id="assistantStatus">CHECKING…</span>
            </div>
            <div class="assistant-voice-row">
                <label for="assistantVoice">Voice</label>
                <select id="assistantVoice" class="assistant-voice-select"
                        aria-label="Assistant voice" title="Default voice is Stephen Hawking">
                    <option value="stephen_hawking_matrix">Stephen Hawking (Enters the Matrix)</option>
                </select>
            </div>
            <div class="assistant-log" id="assistantLog">
                <div class="msg-hint">Ask MP Harmony anything. Replies are spoken in Stephen Hawking's
                    voice unless you choose another one above.</div>
            </div>
            <div class="assistant-input-row">
                <textarea id="assistantInput" class="assistant-input" rows="1"
                          placeholder="Message MP Harmony…" aria-label="Message MP Harmony"></textarea>
                <button class="assistant-send" id="assistantSend" onclick="sendAssistantMessage()">Send</button>
            </div>
            <div class="assistant-actions">
                <label class="voice-toggle">
                    <input type="checkbox" id="assistantSpeak" checked>
                    🔊 Speak replies
                </label>
                <span class="assistant-speaking" id="assistantSpeaking" hidden>🔊 Speaking…</span>
                <button onclick="resetAssistantChat()">↺ New chat</button>
            </div>
            <audio id="assistantAudio" class="assistant-audio" preload="auto"
                   playsinline webkit-playsinline></audio>
        </div>
    </div>

    <div class="footer">
        MP Mix Archive Manager v{html.escape(VERSION)} • MPlanetarian (Dreamworlds Productions)
    </div>

    <script>
        let currentThemeId = "{t['id']}";
        let currentDurationSec = 0;
        let lastLoadedMix = "";
        let isPlayerActive = false;
        let audioStreamConfigured = false;
        let activeAudioFile = "";
        let audioSeekInFlight = false;
        let lastAudioSyncAt = 0;
        let lastServerPos = -1.0;
        let audioStreamEngaged = false;
        // A seek requested before the media metadata is known (fresh load, or a
        // file that is still buffering) is parked here and applied as soon as
        // the element has a timeline to scrub.
        let pendingSeek = null;

        function audioElIsPlaying() {{
            const audioEl = document.getElementById('mobileAudioStream');
            return Boolean(audioEl && !audioEl.paused && !audioEl.ended);
        }}

        // Seek the phone's element only once per request and clear the guard
        // when the element reports it actually moved. Without this the 100 ms
        // poll can re-issue the same seek while the previous one is settling.
        function seekAudioTo(pos) {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !isFinite(pos) || pos < 0) return;
            // readyState 0 = HAVE_NOTHING: there is no timeline yet, so writing
            // currentTime would be ignored. Remember it and retry on metadata.
            if (audioEl.readyState < 1) {{
                pendingSeek = pos;
                return;
            }}
            if (audioSeekInFlight && Math.abs(audioEl.currentTime - pos) < 0.5) return;
            try {{
                audioSeekInFlight = true;
                audioEl.currentTime = pos;
                lastAudioSyncAt = performance.now();
                setTimeout(() => {{ audioSeekInFlight = false; }}, 1500);
            }} catch (e) {{
                pendingSeek = pos;
                audioSeekInFlight = false;
            }}
        }}

        function applyPendingSeek() {{
            if (pendingSeek === null) return;
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || audioEl.readyState < 1) return;
            const pos = pendingSeek;
            pendingSeek = null;
            seekAudioTo(pos);
        }}

        // The 30s arrows act on the element that is actually playing (the
        // phone), then report the new offset to the server so the dashboard
        // clock and any other client stay in step.
        function skipBy(delta) {{
            const audioEl = document.getElementById('mobileAudioStream');
            const configured = Boolean(audioEl) && audioStreamConfigured;
            // No src assigned yet (the tap-to-listen overlay is still up): the
            // browser has no offset to move, so nudge the server clock and let
            // the element align to it when it engages.
            if (!configured) {{
                sendAction(delta < 0 ? 'seek_backward' : 'seek_forward');
                return;
            }}
            const hasTimeline = audioEl.readyState > 0 && isFinite(audioEl.currentTime);
            // While the file is still loading, stack presses on the offset we
            // already have queued rather than on a stale currentTime of 0.
            let base;
            if (hasTimeline) {{
                base = audioEl.currentTime;
            }} else if (pendingSeek !== null) {{
                base = pendingSeek;
            }} else {{
                base = Math.max(0, lastServerPos);
            }}
            const dur = (hasTimeline && isFinite(audioEl.duration) && audioEl.duration > 0)
                ? audioEl.duration
                : Number(currentDurationSec) || 0;
            let target = base + delta;
            if (target < 0) target = 0;
            if (dur > 0 && target > dur) target = Math.max(0, dur - 0.25);
            // seekAudioTo parks the request until metadata is available, so a
            // press during loading still lands once the timeline exists.
            seekAudioTo(target);
            sendAction('seek_to', {{ position: target.toFixed(2) }});
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            if (currentThemeId === 'dynamic_rainbow_liquid_glass') {{
                document.body.classList.add('rainbow-animated');
            }}
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl) {{
                audioEl.addEventListener('seeked', () => {{ audioSeekInFlight = false; }});
                // A skip pressed while the file was still loading has to be
                // replayed once the element finally has a timeline.
                audioEl.addEventListener('loadedmetadata', applyPendingSeek);
                audioEl.addEventListener('canplay', applyPendingSeek);
                // The phone decides when a mix ends, so tell the server which
                // file to serve next instead of waiting for a local player.
                audioEl.addEventListener('ended', () => {{
                    sendAction('track_ended');
                }});
            }}
            loadThemeList();
            fetchMiniPlaylist();
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
            document.body.classList.toggle('rainbow-animated', th.id === 'dynamic_rainbow_liquid_glass');
        }}

        async function loadThemeList() {{
            try {{
                const res = await fetch('/api/themes');
                const data = await res.json();
                const sel = document.getElementById('themeSelect');
                if (!sel || !data.themes) return;
                sel.innerHTML = '';
                data.themes.forEach(th => {{
                    const opt = document.createElement('option');
                    opt.value = th.id;
                    opt.textContent = th.name;
                    sel.appendChild(opt);
                }});
                sel.value = data.current || currentThemeId;
            }} catch (e) {{}}
        }}

        async function changeTheme(themeId) {{
            if (!themeId) return;
            try {{
                const res = await fetch('/api/set_theme?theme=' + encodeURIComponent(themeId));
                const data = await res.json();
                if (data && data.success && data.palette) {{
                    applyPalette(data.palette);
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
            const countEl = document.getElementById('miniPlCount');
            if (countEl) countEl.innerText = items.length + ' of ' + (data.total || 0);
            wrap.innerHTML = '';
            items.forEach((item, i) => {{
                const row = document.createElement('div');
                row.className = 'mini-pl-item' + (item.current ? ' current' : '');
                row.onclick = () => sendAction('select_index', {{ index: item.index }});

                const num = document.createElement('div');
                num.className = 'mini-pl-num';
                num.innerText = item.current ? '▶' : String(i + 1);

                const body = document.createElement('div');
                body.className = 'mini-pl-body';
                const title = document.createElement('div');
                title.className = 'mini-pl-title';
                title.innerText = item.title || 'Untitled Mix';
                const dur = document.createElement('div');
                dur.className = 'mini-pl-dur';
                dur.innerText = (item.artist || '') + (item.duration ? ' • ' + item.duration : '');
                body.appendChild(title);
                body.appendChild(dur);

                row.appendChild(num);
                row.appendChild(body);
                wrap.appendChild(row);
            }});
        }}

        async function fetchMiniPlaylist() {{
            try {{
                const res = await fetch('/api/playlist');
                renderMiniPlaylist(await res.json());
            }} catch (e) {{}}
        }}

        function activateMobileStream() {{
            const overlay = document.getElementById('streamOverlay');
            if (overlay) overlay.style.display = 'none';
            const banner = document.getElementById('mobileAudioBanner');
            if (banner) banner.style.display = 'block';
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl) {{
                if (activeAudioFile && !audioStreamConfigured) {{
                    audioEl.src = '/api/stream?file=' + encodeURIComponent(activeAudioFile);
                    audioStreamConfigured = true;
                }}
                // One-time alignment with the saved stream position; the
                // browser then owns playback. Never re-sync from here.
                if (isPlayerActive && lastServerPos > 0) {{
                    audioEl.addEventListener('loadedmetadata', function once() {{
                        audioEl.removeEventListener('loadedmetadata', once);
                        seekAudioTo(lastServerPos);
                    }});
                }}
                audioEl.play().then(() => {{
                    audioStreamEngaged = true;
                    console.log("Audio stream engaged successfully");
                }}).catch(e => {{
                    console.log("Stream play blocked:", e);
                    if (overlay) overlay.style.display = 'flex';
                }});
            }}
        }}

        function dismissStreamOverlay() {{
            // The assistant tab does not need audio, so let a phone open it
            // without being forced through the autoplay unlock first.
            const overlay = document.getElementById('streamOverlay');
            if (overlay) overlay.style.display = 'none';
        }}

        function handlePlayPauseClick() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl) {{
                if (!audioEl.paused) {{
                    audioEl.pause();
                }} else {{
                    audioEl.play().catch(e => {{
                        const overlay = document.getElementById('streamOverlay');
                        if (overlay) overlay.style.display = 'flex';
                    }});
                }}
            }}
            sendAction('play-pause');
        }}

        // Visualizer 60 FPS Physics
        const BANDS = 24;
        const currentLevels = new Float32Array(BANDS).fill(0.04);
        const targetLevels = new Float32Array(BANDS).fill(0.04);
        const currentPeaks = new Float32Array(BANDS).fill(0.04);
        const peakVels = new Float32Array(BANDS).fill(0.0);

        function animLoop() {{
            const attack = 0.52;
            const decay = 0.88;
            const gravity = 0.0022;

            for (let i = 0; i < BANDS; i++) {{
                const bar = document.getElementById('vb' + i);
                const peakEl = document.getElementById('vp' + i);
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

                const barPct = Math.min(100, Math.max(4, Math.round(currentLevels[i] * 100)));
                const peakPct = Math.min(98, Math.max(barPct, Math.round(currentPeaks[i] * 100)));

                bar.style.height = barPct + '%';
                if (peakEl) peakEl.style.bottom = peakPct + '%';
            }}
            requestAnimationFrame(animLoop);
        }}
        requestAnimationFrame(animLoop);

        async function seekClick(e) {{
            if (!currentDurationSec || currentDurationSec <= 0) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const pct = Math.max(0, Math.min(1, clickX / rect.width));
            const seekPos = pct * currentDurationSec;
            
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl && audioEl.duration) {{
                seekAudioTo(seekPos);
            }}

            sendAction('seek_to', {{ position: seekPos.toFixed(1) }});
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
            const btn = document.getElementById('btnRepeat');
            const txt = document.getElementById('repText');
            if (txt) txt.innerText = String(repMode || 'ALL').toUpperCase();
            if (btn) btn.classList.toggle('active', Boolean(repMode && repMode !== 'off'));
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
                const th = await res.json();
                if (th && th.id && th.id !== currentThemeId) {{
                    applyPalette(th);
                    const sel = document.getElementById('themeSelect');
                    if (sel) sel.value = th.id;
                }}
            }} catch (e) {{}}
        }}

        async function fetchEq() {{
            try {{
                const res = await fetch('/api/eq');
                const data = await res.json();
                // The phone is the only audio output, so the visualizer follows
                // its element. The server cannot observe audio itself.
                isPlayerActive = audioElIsPlaying();

                if (data.levels && data.levels.length) {{
                    for (let i = 0; i < BANDS && i < data.levels.length; i++) {{
                        targetLevels[i] = isPlayerActive ? Math.max(0.04, Math.min(1.0, data.levels[i])) : 0.04;
                    }}
                }}
                // No local audio analysis exists (the phone owns playback), so
                // synthesise a gentle spectrum while the stream is running.
                if (isPlayerActive) {{
                    const t = performance.now() / 1000;
                    for (let i = 0; i < BANDS; i++) {{
                        const base = 0.30 + 0.22 * Math.sin(t * 1.7 + i * 0.55);
                        const wobble = 0.14 * Math.sin(t * 3.1 + i * 1.15);
                        const tilt = 1.0 - (i / BANDS) * 0.55;
                        targetLevels[i] = Math.max(0.05, Math.min(0.95, (base + wobble) * tilt));
                    }}
                }}
                const audioEl = document.getElementById('mobileAudioStream');
                const hasLocalClock = audioEl && isFinite(audioEl.duration) && audioEl.duration > 0 && audioStreamConfigured;
                const shownPos = hasLocalClock ? audioEl.currentTime : Number(data.position_sec || 0);
                const shownDur = hasLocalClock ? audioEl.duration : Number(data.duration_sec || 0);

                if (hasLocalClock) {{
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
                if (data.volume !== undefined) {{
                    document.getElementById('volPill').innerText = 'Vol: ' + data.volume + '%';
                    const audioEl = document.getElementById('mobileAudioStream');
                    if (audioEl) audioEl.volume = Math.max(0, Math.min(1, data.volume / 100.0));
                }}
                if (data.pitch_percent !== undefined) {{
                    const p = data.pitch_percent;
                    document.getElementById('pitchPill').innerText = 'Pitch: ' + (p > 0 ? '+' : '') + p + '%';
                }}
                updateShuffleUI(Boolean(data.shuffle));
                if (data.repeat) updateRepeatUI(data.repeat);
            }} catch (e) {{}}
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
                disabled: '⏹ Streaming disabled',
                player_unavailable: '⚠ Stream source unavailable',
                player_unreachable: '⚠ Stream source unreachable',
                playback_unverified: '⚠ Stream not confirmed',
            }};
            el.innerText = labels[state.status] || state.status;
            el.className = 'autoplay-notice ' + (
                (state.status === 'ready' || state.status === 'playing') ? 'ok' :
                (state.status === 'empty_playlist' || state.status === 'disabled') ? '' : 'warn'
            );
            el.style.display = 'block';
            if (state.detail) el.title = state.detail;
        }}

        async function fetchStatus() {{
            try {{
                const res = await fetch('/api/status');
                const data = await res.json();

                document.getElementById('trackTitle').innerText = data.title || "No Mix Playing";
                document.getElementById('trackArtist').innerText = data.album ? (data.artist + " • " + data.album) : (data.artist || "MPlanetarian");

                const badge = document.getElementById('playBadge');
                const state = (data.state || "stopped").toLowerCase();
                badge.innerText = state.toUpperCase();
                
                if (state === 'playing') {{
                    badge.style.background = 'var(--badge-bg)';
                    badge.style.color = 'var(--badge-color)';
                    badge.style.borderColor = 'var(--badge-color)';
                    document.getElementById('btnPlayPause').innerText = '⏸ Pause';
                }} else {{
                    badge.style.background = 'rgba(255, 170, 0, 0.15)';
                    badge.style.color = '#ffaa00';
                    badge.style.borderColor = '#ffaa00';
                    document.getElementById('btnPlayPause').innerText = '⏯ Play';
                }}

                document.getElementById('sourceBadge').innerText = (data.player || "None").toUpperCase();
                currentDurationSec = data.duration_sec || 0;
                updateAutoplayNotice(data.autoplay);

                if (data.active_file && activeAudioFile !== data.active_file) {{
                    const wasPlaying = audioStreamEngaged && audioElIsPlaying();
                    activeAudioFile = data.active_file;
                    audioStreamConfigured = false;
                    lastServerPos = -1.0;
                    // A skip queued for the previous mix must not be replayed
                    // against the next file.
                    pendingSeek = null;
                    const audioEl = document.getElementById('mobileAudioStream');
                    if (audioEl) {{
                        audioEl.src = '/api/stream?file=' + encodeURIComponent(data.active_file);
                        audioStreamConfigured = true;
                        const resumeAt = Number(data.position_sec) || 0;
                        if (resumeAt > 0) {{
                            // Wait for metadata before seeking, otherwise the
                            // assignment is discarded by the new source load.
                            audioEl.addEventListener('loadedmetadata', function once() {{
                                audioEl.removeEventListener('loadedmetadata', once);
                                seekAudioTo(resumeAt);
                            }});
                        }}
                        audioEl.load();
                        // Keep playing across a server-side track change.
                        if (wasPlaying) {{
                            audioEl.play().catch(() => {{}});
                        }}
                    }}
                }}

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
            }} catch (e) {{}}
        }}

        async function sendAction(act, params = {{}}) {{
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

        setInterval(fetchEq, 100);
        setInterval(fetchStatus, 2000);
        setInterval(fetchTheme, 3000);
        setInterval(fetchMiniPlaylist, 15000);
        fetchEq();
        fetchStatus();
        fetchTheme();
        fetchMiniPlaylist();

        // ------------------------------------------------------------------
        // MP ASSISTANT TAB
        // The player keeps polling and playing while the assistant is open, so
        // a mix can keep running underneath the conversation.
        // ------------------------------------------------------------------
        let assistantOnline = false;
        let assistantBusy = false;
        let assistantSession = '';
        const DEFAULT_ASSISTANT_VOICE = 'stephen_hawking_matrix';

        function assistantSessionId() {{
            if (!assistantSession) {{
                const key = 'mpAssistantSession';
                try {{
                    assistantSession = localStorage.getItem(key) || '';
                }} catch (e) {{ assistantSession = ''; }}
                if (!assistantSession) {{
                    assistantSession = 'web-' + Date.now().toString(36) + '-' +
                        Math.random().toString(36).slice(2, 8);
                    try {{ localStorage.setItem(key, assistantSession); }} catch (e) {{}}
                }}
            }}
            return assistantSession;
        }}

        function showTab(name) {{
            const isAssistant = name === 'assistant';
            const playerPanel = document.getElementById('panelPlayer');
            const assistantPanel = document.getElementById('panelAssistant');
            const playerBtn = document.getElementById('tabBtnPlayer');
            const assistantBtn = document.getElementById('tabBtnAssistant');
            if (!playerPanel || !assistantPanel) return;

            playerPanel.hidden = isAssistant;
            assistantPanel.hidden = !isAssistant;
            if (playerBtn) {{
                playerBtn.classList.toggle('active', !isAssistant);
                playerBtn.setAttribute('aria-selected', String(!isAssistant));
            }}
            if (assistantBtn) {{
                assistantBtn.classList.toggle('active', isAssistant);
                assistantBtn.setAttribute('aria-selected', String(isAssistant));
            }}
            try {{ localStorage.setItem('mpCompanionTab', name); }} catch (e) {{}}
            // Keep the deep link shareable and survive a Safari reload.
            try {{
                if (history.replaceState) {{
                    history.replaceState(null, '', '#' + name);
                }}
            }} catch (e) {{}}
            if (isAssistant) {{
                // Entering the assistant tab is an explicit "no audio needed"
                // choice, so do not leave the autoplay gate covering the chat.
                dismissStreamOverlay();
                fetchAssistantStatus();
                // The tab switch is a real tap, so use it to unlock the reply
                // element before the model has even been asked anything.
                unlockAssistantAudio();
                const input = document.getElementById('assistantInput');
                if (input && assistantOnline) setTimeout(() => input.focus(), 60);
            }}
        }}

        function assistantVoiceChoice() {{
            const sel = document.getElementById('assistantVoice');
            return (sel && sel.value) || DEFAULT_ASSISTANT_VOICE;
        }}

        function setAssistantStatus(state, label) {{
            const el = document.getElementById('assistantStatus');
            if (!el) return;
            el.className = 'assistant-status ' + (state || '');
            el.innerText = label || state || 'CHECKING…';
        }}

        function appendAssistantMessage(role, text) {{
            const log = document.getElementById('assistantLog');
            if (!log) return null;
            const bubble = document.createElement('div');
            bubble.className = 'msg ' + (role === 'user' ? 'user' : 'agent');
            bubble.innerText = text || '';
            log.appendChild(bubble);
            log.scrollTop = log.scrollHeight;
            return bubble;
        }}

        function appendAssistantHint(text) {{
            const log = document.getElementById('assistantLog');
            if (!log) return null;
            const hint = document.createElement('div');
            hint.className = 'msg-hint';
            hint.innerText = text;
            log.appendChild(hint);
            log.scrollTop = log.scrollHeight;
            return hint;
        }}

        async function fetchAssistantStatus() {{
            try {{
                const res = await fetch('/api/assistant');
                const data = await res.json();
                assistantOnline = Boolean(data.available);
                if (assistantOnline) {{
                    setAssistantStatus('online', 'ONLINE');
                }} else if (data.status === 'disabled') {{
                    setAssistantStatus('offline', 'DISABLED');
                }} else {{
                    setAssistantStatus('offline', 'OFFLINE');
                }}
                if (data.voices && data.voices.length) {{
                    const sel = document.getElementById('assistantVoice');
                    if (sel && sel.options.length !== data.voices.length) {{
                        const saved = sel.value || DEFAULT_ASSISTANT_VOICE;
                        sel.innerHTML = '';
                        data.voices.forEach(v => {{
                            const opt = document.createElement('option');
                            opt.value = v.id;
                            opt.textContent = v.name;
                            sel.appendChild(opt);
                        }});
                        sel.value = saved;
                    }}
                }}
                if (data.voice) {{
                    const sel = document.getElementById('assistantVoice');
                    if (sel && !sel.dataset.touched) sel.value = data.voice;
                }}
                return data;
            }} catch (e) {{
                assistantOnline = false;
                setAssistantStatus('offline', 'UNREACHABLE');
                return null;
            }}
        }}

        function assistantAudioEl() {{
            return document.getElementById('assistantAudio');
        }}

        // Pause the mix while the assistant talks, then put it back.
        function duckMixForSpeech(isSpeaking) {{
            const mix = document.getElementById('mobileAudioStream');
            const badge = document.getElementById('assistantSpeaking');
            if (badge) badge.hidden = !isSpeaking;
            if (!mix) return;
            if (isSpeaking) {{
                if (!mix.paused) {{
                    mix.dataset.duckedByAssistant = '1';
                    mix.pause();
                }}
            }} else if (mix.dataset.duckedByAssistant === '1') {{
                delete mix.dataset.duckedByAssistant;
                mix.play().catch(() => {{}});
            }}
        }}

        function splitForSpeech(text) {{
            // Piper is slow on very long input, and Safari caps URL length.
            // Speak sentence-sized chunks back to back instead.
            const clean = (text || '').replace(/\\s+/g, ' ').trim();
            if (!clean) return [];
            const parts = clean.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [clean];
            const chunks = [];
            let current = '';
            for (const part of parts) {{
                if ((current + part).length > 240 && current) {{
                    chunks.push(current.trim());
                    current = part;
                }} else {{
                    current += ' ' + part;
                }}
            }}
            if (current.trim()) chunks.push(current.trim());
            return chunks;
        }}

        // ------------------------------------------------------------------
        // SPEECH UNLOCK (mobile autoplay policy)
        //
        // A phone only lets audio start inside a real tap. The model takes
        // several seconds to answer, so by the time the reply is ready that
        // tap is long gone and `play()` rejects with NotAllowedError, leaving
        // a text-only reply. Answering the tap with a mute priming clip marks
        // the element as user-activated, so later programmatic playback (the
        // actual spoken reply) is allowed. Every tap on the assistant tab also
        // re-primes, which keeps it working after a reload or a long pause.
        // ------------------------------------------------------------------
        const SILENT_WAV = 'data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YSADAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==';
        let assistantAudioUnlocked = false;
        let assistantSpeechUrl = '';
        let assistantSpeechToken = 0;
        let assistantLastPrimeAt = 0;
        let assistantSpeaking = false;

        function unlockAssistantAudio() {{
            const el = assistantAudioEl();
            if (!el) return Promise.resolve(false);
            // Never interrupt a reply that is already speaking.
            if (assistantSpeaking) return Promise.resolve(true);
            if (assistantAudioUnlocked && Date.now() - assistantLastPrimeAt < 60000) {{
                return Promise.resolve(true);
            }}
            // A phone fires pointerdown and touchstart for the same tap; only
            // the first one needs to prime the element.
            if (Date.now() - assistantLastPrimeAt < 600) {{
                return Promise.resolve(assistantAudioUnlocked);
            }}
            assistantLastPrimeAt = Date.now();
            return new Promise(resolve => {{
                const restore = () => {{
                    el.removeEventListener('playing', onPlaying);
                    el.removeEventListener('error', onError);
                }};
                const onPlaying = () => {{
                    restore();
                    // Release the element at once; the priming clip has no
                    // audible content and must not delay the real reply.
                    try {{ el.pause(); }} catch (e) {{}}
                    assistantAudioUnlocked = true;
                    resolve(true);
                }};
                const onError = () => {{
                    restore();
                    assistantAudioUnlocked = false;
                    resolve(false);
                }};
                el.addEventListener('playing', onPlaying);
                el.addEventListener('error', onError);
                try {{
                    // The priming clip must not duck the mix or show the
                    // speaking badge, so mute any reply handler for its run.
                    el.onplay = null;
                    el.src = SILENT_WAV;
                    el.play().then(() => {{
                        // Some browsers resolve without firing `playing`.
                        if (!assistantAudioUnlocked) {{
                            restore();
                            try {{ el.pause(); }} catch (e) {{}}
                            assistantAudioUnlocked = true;
                        }}
                        resolve(true);
                    }}).catch(() => {{
                        restore();
                        assistantAudioUnlocked = false;
                        resolve(false);
                    }});
                }} catch (e) {{
                    restore();
                    resolve(false);
                }}
            }});
        }}

        function stopAssistantSpeech() {{
            assistantSpeechToken += 1;
            assistantSpeaking = false;
            const el = assistantAudioEl();
            if (el) {{
                try {{ el.pause(); }} catch (e) {{}}
                el.onplay = null;
            }}
            duckMixForSpeech(false);
        }}

        function speakAssistantReply(text, bubble) {{
            const speak = document.getElementById('assistantSpeak');
            if (!speak || !speak.checked) return;
            const audioEl = assistantAudioEl();
            if (!audioEl) return;

            const chunks = splitForSpeech(text);
            if (!chunks.length) return;
            const token = ++assistantSpeechToken;
            let index = 0;
            assistantSpeaking = true;

            const finish = () => {{
                if (token !== assistantSpeechToken) return;
                assistantSpeaking = false;
                duckMixForSpeech(false);
            }};

            const offerReplay = (retry) => {{
                if (token !== assistantSpeechToken || !bubble) return;
                if (bubble.querySelector('.assistant-replay')) return;
                const replay = document.createElement('button');
                replay.className = 'assistant-replay';
                replay.type = 'button';
                replay.innerText = '▶ Speak reply';
                // The retry runs inside this tap, so playback is allowed.
                replay.onclick = () => {{
                    replay.remove();
                    retry();
                }};
                bubble.appendChild(document.createElement('br'));
                bubble.appendChild(replay);
            }};

            const playNext = () => {{
                if (token !== assistantSpeechToken) return;
                if (index >= chunks.length) {{
                    finish();
                    return;
                }}
                const body = JSON.stringify({{
                    text: chunks[index],
                    voice: assistantVoiceChoice(),
                }});
                index += 1;
                fetch('/api/assistant/speech', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: body,
                }}).then(res => {{
                    if (!res.ok) throw new Error('speech ' + res.status);
                    return res.blob();
                }}).then(blob => {{
                    if (token !== assistantSpeechToken) return null;
                    if (assistantSpeechUrl) URL.revokeObjectURL(assistantSpeechUrl);
                    assistantSpeechUrl = URL.createObjectURL(blob);
                    audioEl.onended = () => {{
                        if (token !== assistantSpeechToken) return;
                        playNext();
                    }};
                    audioEl.onerror = () => {{
                        if (token !== assistantSpeechToken) return;
                        finish();
                    }};
                    audioEl.src = assistantSpeechUrl;
                    return audioEl.play().then(() => {{
                        assistantAudioUnlocked = true;
                    }}).catch(err => {{
                        // Autoplay was blocked (no tap, or the unlock did not
                        // stick). Stop the queue and surface a one-tap retry so
                        // the reply is never silently text-only.
                        if (token !== assistantSpeechToken) return;
                        assistantAudioUnlocked = err && err.name === 'NotAllowedError'
                            ? false
                            : assistantAudioUnlocked;
                        assistantSpeaking = false;
                        duckMixForSpeech(false);
                        offerReplay(() => speakAssistantReply(text, bubble));
                    }});
                }}).catch(() => {{
                    if (token !== assistantSpeechToken) return;
                    finish();
                }});
            }};

            audioEl.onplay = () => duckMixForSpeech(true);
            duckMixForSpeech(true);
            playNext();
        }}

        function resetAssistantChat() {{
            assistantSession = '';
            try {{ localStorage.removeItem('mpAssistantSession'); }} catch (e) {{}}
            const log = document.getElementById('assistantLog');
            if (log) {{
                log.innerHTML = '';
                appendAssistantHint('New chat started with MP Harmony.');
            }}
            stopAssistantSpeech();
            const audioEl = assistantAudioEl();
            if (audioEl) {{
                try {{ audioEl.pause(); }} catch (e) {{}}
                audioEl.removeAttribute('src');
            }}
            if (assistantSpeechUrl) {{
                try {{ URL.revokeObjectURL(assistantSpeechUrl); }} catch (e) {{}}
                assistantSpeechUrl = '';
            }}
            duckMixForSpeech(false);
        }}

        function setAssistantBusy(busy) {{
            assistantBusy = busy;
            const btn = document.getElementById('assistantSend');
            if (btn) {{
                btn.disabled = busy;
                btn.innerText = busy ? '…' : 'Send';
            }}
        }}

        async function sendAssistantMessage() {{
            if (assistantBusy) return;
            const input = document.getElementById('assistantInput');
            if (!input) return;
            const message = input.value.trim();
            if (!message) return;

            // The send itself is a gesture. Unlock playback now so the reply
            // can be spoken when it arrives several seconds from now.
            unlockAssistantAudio();

            if (!assistantOnline) {{
                const status = await fetchAssistantStatus();
                if (!status || !status.available) {{
                    appendAssistantHint(
                        'MP Harmony Agent is not running. Start it from the manager: ' +
                        'AI Assistant & Local LLM Servers Suite, then option 4 (Manage MP Harmony AI Agent).'
                    );
                    return;
                }}
            }}

            input.value = '';
            input.style.height = 'auto';
            appendAssistantMessage('user', message);
            const bubble = appendAssistantMessage('agent', '▍ thinking…');
            setAssistantBusy(true);

            let reply = '';
            let started = false;
            try {{
                const res = await fetch('/api/assistant/chat', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        message: message,
                        session: assistantSessionId(),
                    }}),
                }});
                if (!res.ok || !res.body) {{
                    let detail = '';
                    try {{ detail = (await res.json()).error || ''; }} catch (e) {{}}
                    throw new Error(detail || ('HTTP ' + res.status));
                }}

                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {{
                    const chunk = await reader.read();
                    if (chunk.done) break;
                    buffer += decoder.decode(chunk.value, {{ stream: true }});
                    const frames = buffer.split('\\n\\n');
                    buffer = frames.pop();
                    for (const frame of frames) {{
                        for (const line of frame.split('\\n')) {{
                            const trimmed = line.trim();
                            if (!trimmed.startsWith('data:')) continue;
                            const payload = trimmed.slice(5).trim();
                            if (!payload || payload === '[DONE]') continue;
                            let obj;
                            try {{ obj = JSON.parse(payload); }} catch (e) {{ continue; }}
                            const delta = obj.choices && obj.choices[0] &&
                                obj.choices[0].delta && obj.choices[0].delta.content;
                            if (delta) {{
                                if (!started) {{
                                    started = true;
                                    bubble.innerText = '';
                                }}
                                reply += delta;
                                bubble.innerText = reply;
                                const log = document.getElementById('assistantLog');
                                if (log) log.scrollTop = log.scrollHeight;
                            }}
                        }}
                    }}
                }}
            }} catch (err) {{
                bubble.classList.add('error');
                bubble.innerText = 'Could not reach MP Harmony: ' + (err.message || err);
                setAssistantBusy(false);
                return;
            }}

            if (!reply.trim()) {{
                bubble.classList.add('error');
                bubble.innerText = 'MP Harmony returned no text. Check the agent console for tool output.';
            }} else {{
                bubble.innerText = reply;
                speakAssistantReply(reply, bubble);
            }}
            setAssistantBusy(false);
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            const savedTab = (location.hash || '').replace('#', '');
            let storedTab = '';
            try {{ storedTab = localStorage.getItem('mpCompanionTab') || ''; }} catch (e) {{}}
            showTab(savedTab === 'assistant' || storedTab === 'assistant' ? 'assistant' : 'player');

            // Prime the reply element on the first tap anywhere in the
            // assistant panel. Phones only allow audio from a user gesture,
            // and the reply arrives long after the send tap has expired.
            const assistantPanel = document.getElementById('panelAssistant');
            if (assistantPanel) {{
                const prime = () => {{ unlockAssistantAudio(); }};
                assistantPanel.addEventListener('pointerdown', prime);
                assistantPanel.addEventListener('touchstart', prime, {{ passive: true }});
            }}

            const voiceSel = document.getElementById('assistantVoice');
            if (voiceSel) {{
                voiceSel.addEventListener('change', () => {{ voiceSel.dataset.touched = '1'; }});
            }}
            const speakBox = document.getElementById('assistantSpeak');
            if (speakBox) {{
                speakBox.addEventListener('change', () => {{
                    if (!speakBox.checked) stopAssistantSpeech();
                }});
            }}
            const input = document.getElementById('assistantInput');
            if (input) {{
                input.addEventListener('input', () => {{
                    input.style.height = 'auto';
                    input.style.height = Math.min(130, input.scrollHeight) + 'px';
                }});
                input.addEventListener('keydown', (ev) => {{
                    // Enter sends, Shift+Enter makes a new line (matches the TUI).
                    if (ev.key === 'Enter' && !ev.shiftKey) {{
                        ev.preventDefault();
                        sendAssistantMessage();
                    }}
                }});
            }}
            fetchAssistantStatus();
            setInterval(() => {{
                if (!document.getElementById('panelAssistant').hidden) fetchAssistantStatus();
            }}, 20000);
        }});
    </script>
</body>
</html>
"""


# ------------------------------------------------------------------------------
# HTTP SERVER & API HANDLER (WITH RANGE HEADER SUPPORT FOR HTML5 STREAMING)
# ------------------------------------------------------------------------------
class CompanionHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
        elif url.path in ("/api/status", "/api/theme", "/api/eq", "/api/autoplay",
                          "/api/themes", "/api/set_theme", "/api/playlist",
                          "/api/assistant", "/api/assistant/speech"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        parsed_query = urllib.parse.parse_qs(url.query)

        if url.path in ("/", "/index.html"):
            current_theme = get_theme_palette(CLI_THEME)
            html_content = render_html_page(current_theme)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))
        elif url.path == "/api/eq":
            eq_data = get_eq_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(eq_data).encode("utf-8"))
        elif url.path == "/api/status":
            status = get_playback_status(CLI_THEME)
            status["autoplay"] = autoplay_state()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode("utf-8"))
        elif url.path == "/api/autoplay":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(autoplay_state()).encode("utf-8"))
        elif url.path == "/api/theme":
            theme = get_theme_palette(CLI_THEME)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(theme).encode("utf-8"))
        elif url.path == "/api/themes":
            payload = {
                "current": get_current_theme_id(CLI_THEME),
                "themes": available_themes(),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/set_theme":
            requested = parsed_query.get("theme", [""])[0]
            chosen = set_web_theme(requested) if requested else None
            success = chosen is not None
            payload = {
                "success": success,
                "theme": chosen or get_current_theme_id(CLI_THEME),
                "palette": get_theme_palette(CLI_THEME) if success else {},
            }
            self.send_response(200 if success else 400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/playlist":
            payload = get_mini_playlist()
            payload["shuffle"] = bool(load_stream_session().get("shuffle", False))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/assistant":
            payload = assistant_status()
            if payload.get("available"):
                payload["voices"] = assistant_voices()
                payload["enabled"] = True
            else:
                payload["voices"] = assistant_voices()
                payload["enabled"] = assistant_enabled()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/assistant/speech":
            self._serve_assistant_speech(parsed_query)
        elif url.path == "/api/stream":
            file_path = parsed_query.get("file", [""])[0]
            if file_path and os.path.isfile(file_path):
                self._serve_audio_stream(file_path)
                return
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(404)
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
            self.send_header("Access-Control-Allow-Origin", "*")
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
        return start, min(end, file_size - 1)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/control":
            params = urllib.parse.parse_qs(url.query)
            action = params.get("action", [""])[0]
            success = control_player(action, params)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "action": action}).encode("utf-8"))
        elif url.path == "/api/assistant/chat":
            self._proxy_assistant_chat()
        elif url.path == "/api/assistant/speech":
            self._serve_assistant_speech()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_assistant_speech(self, parsed_query=None) -> None:
        """Proxy MP Harmony's TTS back to the phone as a WAV body."""
        query = parsed_query or {}
        text = query.get("text", [""])[0]
        voice = query.get("voice", [""])[0]
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                data = {}
            text = str(data.get("text", text) or "")
            voice = str(data.get("voice", voice) or "")
        text = text.strip()
        if not text:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"No text supplied"}')
            return

        code, audio = assistant_request(
            "/v1/audio/speech",
            {"input": text, "voice": voice or "stephen_hawking_matrix"},
            timeout=120.0,
        )
        if code == 200 and audio:
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(audio)
        else:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"error":"Speech synthesis failed"}')

    def _proxy_assistant_chat(self) -> None:
        """Stream MP Harmony's SSE reply straight through to the phone.

        Rewriting the stream would lose tokens from the agent's final channel,
        so the bytes are forwarded as they arrive and only the terminating
        [DONE] frame is checked for.
        """
        if not assistant_enabled():
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"MP Assistant is disabled"}')
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            data = {}

        message = str(data.get("message", "")).strip()
        session_id = str(data.get("session", "") or "web-companion")
        if not message:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"No message supplied"}')
            return

        payload = {
            "model": "mp-harmony-agent",
            "stream": True,
            "messages": [{"role": "user", "content": message}],
            "user": session_id,
        }
        req = urllib.request.Request(
            assistant_url() + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "X-Session-Id": session_id,
            },
            method="POST",
        )
        try:
            upstream = urllib.request.urlopen(req, timeout=ASSISTANT_TIMEOUT)
        except Exception as err:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "MP Harmony Agent is not answering on port 11435.",
                "detail": str(err),
            }).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

        try:
            while True:
                line = upstream.readline()
                if not line:
                    break
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The phone navigated away or switched tabs; that is not an error.
            pass
        except Exception:
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    def log_message(self, format, *args):
        pass


class CompanionServer(http.server.ThreadingHTTPServer):
    """Same threading server, but with a backlog sized for a polling client.

    The stdlib default is 5 pending connections. The dashboard polls several
    endpoints per second and mobile browsers open parallel range requests for
    audio, so bursts are normal rather than exceptional.
    """
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def handle_error(self, request, client_address) -> None:
        """A client hanging up is normal, not a server fault.

        Phones close tabs, scrub, and time out mid-poll constantly. Only real
        server-side failures should reach the journal.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def main():
    global CLI_THEME
    default_host = os.environ.get("WEB_COMPANION_HOST", "0.0.0.0")
    default_port = int(os.environ.get("WEB_COMPANION_PORT", str(PORT)))

    parser = argparse.ArgumentParser(description="MP Mix Manager Mobile Web Companion")
    parser.add_argument("--host", "-H", type=str, default=default_host)
    parser.add_argument("--port", "-p", type=int, default=default_port)
    parser.add_argument("--theme", "-t", type=str, default=None)
    parser.add_argument("--stream-status", action="store_true",
                        help="Print the resolved stream queue as JSON and exit")
    args = parser.parse_args()

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

    CLI_THEME = args.theme
    active_theme = get_theme_palette(CLI_THEME)
    lan_ip = get_local_ip()
    host_display = lan_ip if args.host in ("0.0.0.0", "", "::") else args.host

    server_address = (args.host, args.port)
    try:
        httpd = CompanionServer(server_address, CompanionHandler)
    except OSError as err:
        if err.errno in (98, 48, 10048):
            print(f"\n======================================================================")
            print(f"  🌌 MP MIX ARCHIVE MANAGER — MOBILE WEB COMPANION (ACTIVE)")
            print(f"======================================================================")
            print(f"  • Local Machine:  http://localhost:{args.port}")
            print(f"  • Phone / Tablet: http://{host_display}:{args.port}")
            print(f"======================================================================\n")
            return 0
        raise

    # Resolve the stream queue in the background so the HTTP server is
    # reachable immediately. This only catalogs files; it never plays anything.
    stream_thread = start_autoplay_worker()

    print(f"\n======================================================================")
    print(f"  🌌 MP MIX ARCHIVE MANAGER — MOBILE WEB COMPANION")
    print(f"======================================================================")
    print(f"  • Active Theme:   {active_theme['name']} ({active_theme['id']})")
    print(f"  • Local Machine:  http://localhost:{args.port}")
    print(f"  • Phone / Tablet: http://{host_display}:{args.port}")
    print(f"  • Audio Output:   STREAM ONLY (no local device)")
    print(f"======================================================================\n")

    def _report_stream_queue() -> None:
        stream_thread.join()
        state = autoplay_state()
        if state.get("status") == "ready":
            print(f"  ▶ STREAM QUEUE READY: {state.get('detail', '')}", flush=True)
            print(f"  • First mix:      {state.get('track', '')}", flush=True)
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
