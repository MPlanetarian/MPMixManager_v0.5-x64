#!/usr/bin/env python3
"""
MP Mix Manager - Studio & DJ Booth Mobile Web Companion
Lightweight, standalone HTTP & REST server providing a modern, theme-adaptive
web dashboard accessible from phones, tablets, and remote browsers on the local LAN.
Displays live mix playback, tracklists, EQ visualizers, and remote playback controls.
Automatically synchronizes with the active theme currently running in Manager.
"""

import argparse
import hashlib
import hmac
import html
import http.server
import json
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
from typing import Any, Dict, List, Optional, Tuple

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

# Every server launch gets a fresh id. A dashboard that sees it change knows
# the companion was restarted and refreshes so the phone resumes the stream
# without a manual reload. The tray also posts a notice to /api/announce just
# before it stops the service, so phones see why they are reconnecting.
INSTANCE_ID = f"{int(time.time())}-{os.getpid()}"
_announce_message: Optional[str] = None
_announce_at = 0.0
_announce_lock = threading.Lock()
ANNOUNCE_TTL_SECONDS = 45.0
ANNOUNCE_MESSAGE_DEFAULT = "MP Audio Web Companion has been restarted"


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
# DESKTOP LAUNCH PAD (Linux)
#
# The dashboard runs as a user service, so the phone can be the only thing in
# front of the user. These four buttons start the desktop suite, show whether
# each app is already live, and close all four without stopping the tray icon
# or this companion.
# ------------------------------------------------------------------------------
try:
    import mp_suite_control as suite_control
except ImportError:  # The companion is also importable from the repo root.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mp_suite_control as suite_control  # type: ignore[no-redef]
    except ImportError:
        suite_control = None  # type: ignore[assignment]


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


def launch_pad_close_all() -> Dict[str, Any]:
    if suite_control is None:
        return {"success": False, "action": "unsupported", "remaining": []}
    try:
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

# Authentication and containment. The token is created on first run and lives
# mode 0600 beside the other companion state; `--rotate-token` replaces it.
COMPANION_TOKEN = ""
COMPANION_CIDRS: List[Any] = []
COMPANION_REQUIRE_AUTH = True
# Requests that change state must carry this header. A cross-origin form
# cannot set a custom header without a preflight, and the bearer token makes
# that preflight fail, so this is the second half of the CSRF defence.
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
# The Favorites playlist is shared with the MP Audio Player, which writes it on
# every [H] mark, so the companion only ever reads it.
FAVORITES_FILE = CONFIG_DIR / "favorites.json"

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
_queue_source_state: Dict[str, Any] = {"fallback": False, "detail": ""}
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
        return not _wav_has_no_audio_frames(path)
    if ext == ".flac":
        return not _flac_has_no_audio_frames(path)
    return True


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
    if target == LIBRARY_MODE_MUSIC:
        # The mix archive needs no preparation, but a music library on SMB does.
        # This runs inside the explicit tap, which the dashboard already paints
        # with a "Switching to Music…" state, so waiting here is safe and it
        # makes the very first Music tap list tracks instead of mixes.
        ensure_music_folders_mounted(wait=True)
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
        files = scan_music_files()
        if files:
            return files
        # An unconfigured or unreachable music library must not leave the
        # companion with nothing to stream, so fall through to the archive.
        return scan_archive_files()

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

    A share such as \\\\192.168.1.138\\MUSIC001 only becomes a filesystem path
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

    mounted = False

    def _mount_all() -> bool:
        nonlocal mounted
        try:
            try:
                import music_folders_manager as mfm
            except ImportError:  # imported as scripts.mix_web_companion
                from scripts import music_folders_manager as mfm
            config = mfm.load_music_config()
            stop_at = time.time() + max(0.0, deadline_sec)
            for folder in folders:
                if time.time() >= stop_at:
                    break
                try:
                    # connect_folder is a no-op when the share is already
                    # answering, and it remounts a wedged one.
                    ok, _detail = mfm.connect_folder(folder, config, interactive=False)
                    mounted = mounted or bool(ok)
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            with _music_mount_lock:
                _music_mount_in_flight = False
        return mounted

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


def scan_music_files() -> List[str]:
    """The MUSIC library: generated playlist first, then the folder roots.

    The combined playlist holds tens of thousands of paths in a ~30 MB JSON
    file, so a re-parse on every library switch made the Mix/Music buttons feel
    dead. The parsed result is reused until the playlist file changes.

    An empty result is re-scanned every MUSIC_SCAN_RETRY_TTL seconds: a share
    that was not mounted when the playlist was read leaves every path
    unresolved, and the playlist file itself never changes when it mounts.
    Without the retry, Music mode stayed on the archive fallback for the life
    of the process and the phone kept listing mixes.
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
                return list(cached_files)

    files: List[str] = []
    for cand in music_playlist_candidates():
        if not cand.is_file():
            continue
        files = _read_playlist_file(cand)
        if files:
            break
    if not files:
        # The generated playlist exists but every path inside it is unresolved:
        # its share is not mounted. Mount on demand, then read it again, so the
        # first Music tap after a reboot fills the queue instead of reporting an
        # offline library.
        if ensure_music_folders_mounted():
            for cand in music_playlist_candidates():
                if not cand.is_file():
                    continue
                files = _read_playlist_file(cand)
                if files:
                    break
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
    """
    paths: List[str] = []
    seen = set()
    for cand in (FAVORITES_FILE, FAVORITES_FILE.with_suffix(".m3u8")):
        if not cand.is_file():
            continue
        for path in _read_playlist_file(cand):
            key = _favorite_key(path)
            if not key or key in seen:
                continue
            seen.add(key)
            paths.append(path)
        if paths:
            break
    return paths


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
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found]


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
            return [p for p in paths if is_streamable_file(p)]
        paths = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if is_audio_file(line):
                paths.append(line)
        # A saved playlist can still name a mix that was replaced by a
        # header-only WAV stub, so the same frame check the archive scan uses
        # applies here. Otherwise the queue hands the browser a dead file.
        return [p for p in paths if is_streamable_file(p)]
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
    return _scan_dirs(_archive_dirs())


def _archive_dirs_permission_denied() -> str:
    """First configured archive folder that exists but cannot be listed.

    macOS privacy protection (TCC) refuses a background job access to external
    volumes, and the failure is a plain PermissionError rather than a missing
    path. Returning the folder lets the dashboard name the folder to approve.

    This must use the bounded probe: under launchd the denied read can block
    instead of raising, and a plain os.listdir here hung the whole status
    request (the phone saw an empty reply).
    """
    for folder in _archive_dirs():
        if _dir_readable(folder):
            return ""
        if Path(folder).is_dir():
            return str(folder)
    return ""


# Directories already found unreadable, with the time of the check. A TCC
# denial can either raise PermissionError or block inside the kernel waiting
# for an approval dialog that a background job cannot show. Probing once and
# remembering the answer keeps the dashboard responsive instead of letting
# every status poll wedge on the same folder for the life of the process.
_BLOCKED_DIRS: Dict[str, float] = {}
_BLOCKED_TTL = 60.0


def _dir_readable(path: Any, timeout: float = 2.0) -> bool:
    """True when the directory can be listed, giving up after `timeout`.

    The listing runs on a daemon thread because a privacy-blocked read does not
    always return an error: it can park indefinitely. A hung probe only leaks
    that one thread and never blocks the caller.
    """
    key = str(path)
    now = time.time()
    cached_at = _BLOCKED_DIRS.get(key)
    if cached_at is not None and (now - cached_at) < _BLOCKED_TTL:
        return False

    result = {"ok": False, "done": False}

    def _probe():
        try:
            os.listdir(key)
            result["ok"] = True
        except PermissionError:
            result["ok"] = False
        except OSError:
            result["ok"] = False
        finally:
            result["done"] = True

    worker = threading.Thread(target=_probe, name="mp-dir-probe", daemon=True)
    worker.start()
    worker.join(max(0.1, timeout))
    if not result["done"]:
        _BLOCKED_DIRS[key] = now
        return False
    if not result["ok"]:
        _BLOCKED_DIRS[key] = now
    else:
        _BLOCKED_DIRS.pop(key, None)
    return bool(result["ok"])


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


def build_stream_queue() -> List[str]:
    """Resolve the play queue the phones will stream through.

    Scanning the music playlist stats tens of thousands of paths, so the result
    is cached briefly. Without this every 2-second status poll would re-walk the
    library.
    """
    global _queue_cache

    mode = get_library_mode()
    now = time.time()
    cached = _queue_cache
    if cached and cached.get("mode") == mode and (now - cached.get("built_at", 0.0)) < QUEUE_CACHE_TTL:
        return list(cached.get("files") or ())

    with _queue_build_lock:
        # Another request may have finished the scan while this one waited.
        now = time.time()
        cached = _queue_cache
        if cached and cached.get("mode") == mode and (now - cached.get("built_at", 0.0)) < QUEUE_CACHE_TTL:
            return list(cached.get("files") or ())

        if mode == LIBRARY_MODE_MUSIC:
            files = scan_music_files()
            # An unmounted SMB share leaves the generated playlist full of paths
            # that no longer resolve. Falling back keeps audio flowing, but the
            # status call reports it so the phone is not silently mislabelled:
            # a fallback queue must never look like the Music library.
            _queue_source_state.update({
                "fallback": not files,
                "detail": "" if files else (
                    "Music library offline - the music folders are not mounted, "
                    "streaming Mix Archive"
                ),
            })
            if not files:
                files = scan_archive_files()
        elif mode == LIBRARY_MODE_FAVORITES:
            files = scan_favorite_files()
            # An empty Favorites playlist is a normal state, not a broken
            # library, so it is reported as empty instead of borrowed from
            # another mode. Marking tracks with [H] in the player fills it.
            _queue_source_state.update({
                "fallback": False,
                "detail": "" if files else "No favorites yet - press H in the player to add one",
            })
        else:
            _queue_source_state.update({"fallback": False, "detail": ""})
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
    worker thread while the caller answers from the old copy. Only a cold cache
    (no queue for this mode yet) builds synchronously, which is the cost the
    very first request always paid anyway.
    """
    mode = get_library_mode()
    cached = _queue_cache
    if cached and cached.get("mode") == mode:
        if (time.time() - cached.get("built_at", 0.0)) >= QUEUE_CACHE_TTL:
            _refresh_queue_in_background()
        return list(cached.get("files") or ())
    return build_stream_queue()


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


def current_stream_file(cached: bool = False) -> Tuple[str, Dict[str, Any]]:
    """Return the file the phone should currently be streaming.

    `cached` is for the polling endpoints: they must answer instantly even
    while a large library scan runs, so they read the last good queue.
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
    return queue[idx], state


def get_eq_status() -> Dict[str, Any]:
    # The host player is the authority on what is on air, so the visualizer and
    # the progress bar must read its clock, not the phone-facing stream cursor.
    host = host_player_payload()
    if host is not None:
        return {
            "state": host["state"],
            "position": host["position"],
            "duration": host["duration"],
            "position_sec": host["position_sec"],
            "duration_sec": host["duration_sec"],
            "progress_pct": host["progress_pct"],
            "volume": host["volume"],
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
    if duration <= 0.0:
        # soundfile lives in the user site-packages, which a systemd start may
        # not resolve. ffprobe reads the same header and is package-managed.
        duration = _ffprobe_duration(path)
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
        raw = sock.recv(200000)
        if raw:
            response = json.loads(raw.decode("utf-8", errors="ignore"))
            if isinstance(response, dict):
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
            sock.settimeout(0.5)
            sock.connect(str(socket_path))
        else:
            sock = socket.create_connection(
                ("127.0.0.1", int(port_file.read_text().strip())), 0.5
            )
            sock.settimeout(0.5)
        sock.sendall(json.dumps({"cmd": cmd, **kwargs}).encode("utf-8"))
        raw = sock.recv(4096)
        if raw:
            response = json.loads(raw.decode("utf-8", errors="ignore"))
            return response.get("status") == "ok"
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
        "pitch_percent": int(data.get("pitch_percent", 0) or 0),
        "pitch_factor": float(data.get("pitch_factor", 1.0) or 1.0),
        "shuffle": bool(data.get("shuffle", False)),
        "repeat": str(data.get("repeat", "all")),
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


def get_playback_status(cli_theme: Optional[str] = None) -> Dict[str, Any]:
    active_theme = get_theme_palette(cli_theme)

    # The running host player is the authority on what is on air. The stream
    # session below is only the phone-facing queue cursor used when nothing is
    # playing on this machine at all.
    host = host_player_payload()
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
        "mode": get_library_mode(),
        "mode_label": MODE_LABELS.get(get_library_mode(), "Library"),
        "library_fallback": bool(_queue_source_state.get("fallback")),
        "library_detail": _queue_source_state.get("detail", ""),
        "theme": active_theme,
    }


MINI_PLAYLIST_SIZE = 5
# Five rows are visible on a phone; the extra entries are what the list scrolls
# through, so a long mix queue is browsable without loading the whole library.
MINI_PLAYLIST_WINDOW = 20


def get_mini_playlist(limit: Optional[int] = None) -> Dict[str, Any]:
    """A short window of the stream queue: the current track plus the next ones.

    The dashboard renders `show` rows inside a scrollable box and the server
    sends up to `limit` (default MINI_PLAYLIST_WINDOW) entries, so five tracks
    are visible at once and the rest can be scrolled. `total` is the full queue
    length and is what the dashboard shows as the track count.
    """
    queue = build_stream_queue_cached()
    mode = get_library_mode()
    show = MINI_PLAYLIST_SIZE
    if not queue:
        return {
            "items": [],
            "total": 0,
            "show": show,
            "current_index": -1,
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, "Library"),
            "library_fallback": bool(_queue_source_state.get("fallback")),
            "library_detail": _queue_source_state.get("detail", ""),
            "modes": [{"id": mid, "label": label} for mid, label in MODE_LABELS.items()],
        }

    try:
        window = int(limit) if limit is not None else MINI_PLAYLIST_WINDOW
    except (TypeError, ValueError):
        window = MINI_PLAYLIST_WINDOW
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
    host = host_player_payload()
    if host and host.get("active_file"):
        host_file = str(host["active_file"])
        if host_file in queue:
            idx = queue.index(host_file)
    idx = max(0, min(idx, len(queue) - 1))

    items: List[Dict[str, Any]] = []
    # Stop at the end of the queue instead of wrapping: a repeating window
    # looks broken once it is scrollable.
    for offset in range(min(window, len(queue))):
        i = (idx + offset)
        if i >= len(queue):
            break
        path = queue[i]
        title, artist, album = clean_track_title("", path, {})
        duration = probe_duration(path)
        items.append({
            "index": i,
            "title": title,
            "artist": artist,
            "file": path,
            "current": i == idx,
            "duration": format_seconds(duration),
            "duration_sec": round(duration, 2),
        })
    return {
        "items": items,
        "total": len(queue),
        "show": show,
        "current_index": idx,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, "Library"),
        "library_fallback": bool(_queue_source_state.get("fallback")),
        "library_detail": _queue_source_state.get("detail", ""),
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


def control_player(action: str, params: Optional[Dict[str, List[str]]] = None) -> bool:
    """Drive playback. The running host player owns the audio, so a live one is
    forwarded to it; otherwise the browser performs the playback of its stream."""
    params = params or {}
    if action in ("random", "play_random", "play_random_track"):
        return bool(play_random_track().get("success"))

    # A running MP Audio Player is what the dashboard is reporting, so the
    # transport buttons must act on that player. Without this the title would
    # follow the host while every tap silently edited the unused stream session.
    host, _source = host_player_status()
    if host is not None:
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
        if action == "report_position":
            # The host's own clock is authoritative, so a phone clock report is
            # irrelevant and must never be written over it.
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
        duration = float(state.get("duration_sec", 0.0) or 0.0) or probe_duration(state["path"])

        def seek_to(target: float) -> None:
            state["position_sec"] = clamp_position(state, target)
            state["started_at"] = time.time() if state.get("playing") else 0.0

        def switch(step: int) -> None:
            n = len(queue)
            if n <= 0:
                return
            # Shuffle must actually shuffle. This used to be a plain +1 walk, so
            # the flag only changed the badge: enabling shuffle still played the
            # queue in order. Pick a random entry instead, never repeating the
            # one already playing.
            if state.get("shuffle") and n > 1:
                current = str(state.get("path", "") or "")
                try:
                    avoid = queue.index(current) if current in queue else int(state.get("index", 0) or 0)
                except ValueError:
                    avoid = int(state.get("index", 0) or 0)
                choices = [i for i in range(n) if i != avoid]
                state["index"] = random.choice(choices) if choices else 0
            else:
                state["index"] = (int(state.get("index", 0)) + step) % n
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
          'onclick="closeAllApps()" title="Close all four apps, keep the tray icon"'
        + disabled + '>'
          '<span class="launch-icon" aria-hidden="true">⏻</span>'
          '<span class="launch-label">Close All</span>'
          '</button>'
        '</div>'
        # MP Visualizer: one window, owned by this dashboard. Press to open,
        # press again for fullscreen, press a third time to close it.
        '<button type="button" class="launch-btn launch-visualizer" id="launchVisualizer" '
        'data-label="' + visualizer_label + '" onclick="launchVisualizer()" '
        'aria-pressed="false" aria-live="polite" title="Open MP Visualizer on the computer"'
        + disabled + '>'
        '<span class="launch-icon" aria-hidden="true">🌌</span>'
        '<span class="launch-label">MP Visualizer</span>'
        '<span class="launch-state" id="launchVisualizerState">Off</span>'
        '</button>'
        '<div class="launchpad-note" id="launchPadNote">'
        'Tap a button to open that app on the computer.'
        '</div>'
        '</div>'
    )


# ------------------------------------------------------------------------------
# DYNAMIC HTML TEMPLATE (SEAMLESS AUDIO STREAMING & SYNCED SEEKING)
# ------------------------------------------------------------------------------
def render_html_page(theme: Dict[str, Any]) -> str:
    t = theme
    launch_pad_html = render_launch_pad_html()

    # Dynamic Liquid Glass [Rainbow Colours]: the body carries a slowly rotating
    # hue while the glass cards breathe, so the whole dashboard cycles through
    # the spectrum instead of sitting on one static accent colour.
    #
    # This block is always emitted, even when the page loads on another theme.
    # Every rule is scoped to a body class, so it stays inert until the theme is
    # selected. Previously it was only emitted when the rainbow theme was active
    # at render time, which meant switching to it from the theme menu added the
    # class but had no keyframes or rules to animate - the colours only started
    # cycling after a full page reload.
    rainbow_anim_css = """
        @keyframes rainbowShift {
            0% { background-position: 0% 50%; }
            100% { background-position: 300% 50%; }
        }
        @keyframes dynamicRainbowHue {
            0% { filter: hue-rotate(0deg) saturate(1.15); }
            50% { filter: hue-rotate(180deg) saturate(1.35); }
            100% { filter: hue-rotate(360deg) saturate(1.15); }
        }
        @keyframes dynamicRainbowBreath {
            0%, 100% {
                box-shadow: 0 14px 40px rgba(176, 88, 255, 0.45),
                            inset 0 0 24px rgba(0, 242, 254, 0.16);
                border-color: rgba(176, 88, 255, 0.45);
            }
            50% {
                box-shadow: 0 18px 52px rgba(255, 45, 142, 0.55),
                            inset 0 0 34px rgba(254, 225, 64, 0.22);
                border-color: rgba(0, 242, 254, 0.60);
            }
        }
        /* The hue rotation belongs on a dedicated background layer, not on
           <body>. Filtering the body re-rasterises every glyph and card on each
           frame, which is the single most expensive thing iOS Safari does here
           and a direct cause of the dashboard going unresponsive after a while.
           A fixed pseudo-element keeps the cycling colours while the text stays
           crisp and un-filtered. */
        body.dynamic-rainbow-glass-active::before {
            content: "";
            position: fixed;
            inset: 0;
            z-index: -1;
            pointer-events: none;
            background: radial-gradient(circle at 20% 15%, rgba(176, 88, 255, 0.30), transparent 55%),
                        radial-gradient(circle at 80% 25%, rgba(0, 242, 254, 0.22), transparent 55%),
                        radial-gradient(circle at 50% 90%, rgba(255, 45, 142, 0.26), transparent 60%);
            animation: dynamicRainbowHue 24s linear infinite;
            will-change: filter;
        }
        body.dynamic-rainbow-glass-active .card {
            animation: dynamicRainbowBreath 6s ease-in-out infinite;
            backdrop-filter: blur(18px) saturate(1.35);
            -webkit-backdrop-filter: blur(18px) saturate(1.35);
            /* Keep the painter inside the card's own box so a border/shadow
               animation cannot invalidate the whole page. */
            contain: paint;
        }
        body.dynamic-rainbow-glass-active .header h1 {
            background: linear-gradient(90deg,
                #b058ff, #ff2d8e, #fee140, #00f2fe, #b058ff);
            background-size: 300% 100%;
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: rainbowShift 12s linear infinite;
        }
        /* Animations cost battery and CPU on a phone that is only listening, so
           they stop entirely while the tab is in the background. */
        body.dynamic-rainbow-glass-frozen::before,
        body.dynamic-rainbow-glass-frozen .card,
        body.dynamic-rainbow-glass-frozen .header h1 {
            animation: none !important;
        }
        @media (prefers-reduced-motion: reduce) {
            body.dynamic-rainbow-glass-active::before,
            body.dynamic-rainbow-glass-active .card,
            body.dynamic-rainbow-glass-active .header h1 {
                animation: none;
            }
        }
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, maximum-scale=1.0, user-scalable=no">
    <title>MP Liquid Player (Web) — DJ Booth Remote</title>
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
            touch-action: none;
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
        /* Short viewports (landscape phones, split screens): shrink the card
           instead of scrolling it, so the engage button stays centred and on
           screen no matter how little height there is. */
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
<body>
    <div id="restartNotice" class="restart-notice" role="status" aria-live="polite">
        <span class="restart-spinner" aria-hidden="true"></span>
        <span id="restartNoticeText">MP Audio Web Companion has been restarted — reconnecting…</span>
    </div>
    <!-- Stream Activation Modal to Satisfy Mobile Autoplay Policies -->
    <div id="streamOverlay" class="show">
        <div class="overlay-card">
            <h2>🎧 MP Mix Stream</h2>
            <p>Tap below to engage mobile studio audio streaming and dashboard synchronization.</p>
            <p id="streamOverlayDetail" class="stream-overlay-detail" style="display: none;"></p>
            <button class="btn-activate-stream" onclick="activateMobileStream()">Engage Audio Stream</button>
        </div>
    </div>

    <!-- Top tabs. The stream keeps playing while the assistant tab is open,
         so the listener can switch back at any time without re-engaging it. -->
    <div class="app-tabs" role="tablist" aria-label="MP Web Companion sections">
        <button type="button" class="app-tab active" id="tabPlayer" role="tab"
                aria-selected="true" aria-controls="panelPlayer"
                onclick="switchAppTab('player')">
            <span class="app-tab-icon" aria-hidden="true">🎧</span> MP Liquid Player
        </button>
        <button type="button" class="app-tab" id="tabAssistant" role="tab"
                aria-selected="false" aria-controls="panelAssistant"
                onclick="switchAppTab('assistant')">
            <span class="app-tab-icon" aria-hidden="true">🧠</span> MP Assistant
            <span class="app-tab-badge" aria-hidden="true"></span>
        </button>
    </div>

    <div class="header">
        <h1>MP LIQUID PLAYER (WEB) <span class="app-version">v{html.escape(VERSION)}</span></h1>
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
                    <audio id="assistantSpeech" playsinline preload="auto" style="display: none;"></audio>
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
        // Restart detection. The server reports a fresh instance id on every
        // launch; a change means the companion was stopped and started again.
        let serverInstance = "";
        let restartNoticeShown = false;
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
            if (duckedMixVolume === null) duckedMixVolume = mix.volume;
            try {{
                mix.volume = Math.max(0.08, duckedMixVolume * 0.25);
            }} catch (e) {{}}
        }}

        function restoreMixAfterSpeech() {{
            const mix = document.getElementById('mobileAudioStream');
            if (mix && duckedMixVolume !== null) {{
                try {{ mix.volume = duckedMixVolume; }} catch (e) {{}}
            }}
            duckedMixVolume = null;
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

        // A 100 ms silent WAV played inside the user's tap unlocks the element
        // for the reply that arrives seconds later.
        const SILENT_WAV = 'data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YSADAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==';

        function unlockSpeechElement() {{
            const audio = speechElement();
            if (!audio || audio.__unlocked) return;
            try {{
                audio.__unlocked = true;
                audio.__unlockSrc = SILENT_WAV;
                audio.src = SILENT_WAV;
                audio.play().catch(() => {{}});
            }} catch (e) {{}}
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

        // The host's clock is only a fallback: it cannot hear the mix and it
        // free-runs while the service sits idle. Once the phone is actually
        // playing, send it the true position every few seconds so a later
        // listener resumes where this one left off instead of at a drifted
        // point near the end of the file.
        let lastPositionReportAt = 0;

        function reportPlaybackPosition() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !audioStreamEngaged || !audioElIsPlaying()) return;
            if (!isFinite(audioEl.currentTime) || audioEl.currentTime <= 0) return;
            const now = performance.now();
            if (now - lastPositionReportAt < 4000) return;
            lastPositionReportAt = now;
            fetch('/api/control?action=report_position&position=' +
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

        function skipBy(delta) {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl) return;
            const dur = Number(audioEl.duration);
            const configured = isFinite(dur) && dur > 0;
            let target;
            if (!configured) {{
                // No timeline yet: stack on the parked offset, and tell the
                // server so its bookkeeping clock stays in step.
                let base;
                if (pendingSeek === null) {{
                    base = Number(audioEl.currentTime || 0);
                }} else if (pendingSeek !== null) {{
                    base = pendingSeek;
                }}
                pendingSeek = Math.max(0, base + delta);
                sendAction(delta > 0 ? 'seek_forward' : 'seek_backward');
                return;
            }}
            target = Number(audioEl.currentTime || 0) + delta;
            if (target < 0) target = 0;
            if (dur > 0 && target > dur) target = dur - 0.25;
            seekAudioTo(target);
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
            if (currentThemeId === 'dynamic_rainbow_liquid_glass') {{
                document.body.classList.add('dynamic-rainbow-glass-active');
            }}
            // The gate is shown in the initial markup, before any script runs.
            // Apply the no-scroll state now so the first paint is already
            // locked and the engage button is centred.
            renderStreamOverlay();
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl) {{
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
                    sendAction('track_ended');
                }});
                // The tray stopped the service while this mix was playing, OR
                // the browser cannot decode the file the queue handed it. The
                // URL may be dead, so re-attach once to the fresh instance
                // instead of leaving a silent player behind.
                //
                // The attempt counter is the important part: a track that is
                // genuinely unplayable (a header-only WAV, a truncated
                // conversion) fails the same way every time, and an unbounded
                // "reconnect" is what produced the permanently flickering
                // screen and the restart banner that never cleared. Two
                // attempts are enough to ride out a real service restart.
                audioEl.addEventListener('error', () => {{
                    if (!activeAudioFile) return;
                    const now = performance.now();
                    const sameFile = (mediaRecoveryFile === activeAudioFile);
                    if (mediaRecoveryUsed && (now - lastMediaErrorAt) < 5000) return;
                    if (sameFile && mediaRecoveryAttempts >= MEDIA_RECOVERY_LIMIT) {{
                        // Stop retrying and say what is actually wrong.
                        mediaRecoveryUsed = true;
                        lastMediaErrorAt = now;
                        const label = activeAudioFile.split('/').pop() || activeAudioFile;
                        showStreamOverlay(
                            'This mix cannot be played on this device (' + label + '). ' +
                            'It is empty or damaged on the computer — pick another mix ' +
                            'or re-export it.');
                        return;
                    }}
                    if (!sameFile) {{
                        mediaRecoveryFile = activeAudioFile;
                        mediaRecoveryAttempts = 0;
                    }}
                    mediaRecoveryAttempts += 1;
                    mediaRecoveryUsed = true;
                    lastMediaErrorAt = now;
                    showRestartNotice('MP Audio Web Companion has been restarted — reconnecting…');
                    reattachToRestartedServer();
                }});
            }}
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
            document.body.classList.toggle('dynamic-rainbow-glass-active', th.id === 'dynamic_rainbow_liquid_glass');
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
            const show = Math.max(1, Number(data && data.show) || 5);
            // Music asked for but the archive is being streamed instead: the
            // rows below are mixes, so they must not be presented as tracks
            // from the selected library.
            const fellBack = Boolean(data && data.library_fallback);

            const countEl = document.getElementById('miniPlCount');
            if (countEl) {{
                // Track count for the whole selected library, not just the
                // rows that fit on a phone screen.
                countEl.innerText = total + (total === 1 ? ' track' : ' tracks') +
                    (fellBack ? ' (from Mix Archive)' : '');
                countEl.title = Math.min(items.length, show) + ' shown of ' + total +
                                ' in this library · scroll for more';
            }}

            const titleEl = document.getElementById('miniPlTitle');
            if (titleEl) {{
                const label = fellBack ? 'Mix Archive (Music unavailable)'
                                       : ((data && data.mode_label) || 'Library');
                titleEl.innerText = '🎵 Up Next · ' + label;
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
            // snapping back to the top while the user is browsing.
            const scrollTop = wrap.scrollTop;
            wrap.innerHTML = '';

            if (!items.length) {{
                const empty = document.createElement('div');
                empty.className = 'mini-pl-empty';
                empty.innerText = (data && data.mode === 'FAVORITES')
                    ? 'No favourites yet — press H in the player to add one'
                    : 'No audio found in this library';
                wrap.appendChild(empty);
                return;
            }}

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
                title.innerText = item.title || 'Untitled Track';
                const dur = document.createElement('div');
                dur.className = 'mini-pl-dur';
                dur.innerText = (item.artist || '') + (item.duration ? ' • ' + item.duration : '');
                body.appendChild(title);
                body.appendChild(dur);

                row.appendChild(num);
                row.appendChild(body);
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

        async function fetchMiniPlaylist() {{
            try {{
                const res = await fetch('/api/playlist');
                const data = await res.json();
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

        function engageStreamFromServer(resumePos) {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl) return;
            if (!activeAudioFile) {{
                // /api/stream?file= would 404 here, which is indistinguishable
                // from a dead button on the phone. Explain the real cause.
                showStreamOverlay(streamUnavailableReason ||
                    'No audio is queued on the host. Start a mix in MP Audio Player, then tap again.');
                return;
            }}
            if (activeAudioFile && !audioStreamConfigured) {{
                audioEl.src = '/api/stream?file=' + encodeURIComponent(activeAudioFile);
                audioStreamConfigured = true;
            }}
            lastEngageAt = performance.now();
            // One-time alignment with the saved stream position; the browser
            // then owns playback. Never re-sync from here.
            const startAt = Number(resumePos);
            if (isFinite(startAt) && startAt > 0) {{
                audioEl.addEventListener('loadedmetadata', function once() {{
                    audioEl.removeEventListener('loadedmetadata', once);
                    seekAudioToWithResume(startAt, true);
                }});
            }}
            audioEl.play().then(() => {{
                audioStreamEngaged = true;
                // Playback really started, so the file is good: clear the
                // recovery budget for it and drop any leftover error notice.
                // The guard is the duration: a header-only file can resolve
                // play() and then end immediately, and the ended handler would
                // otherwise restart it forever.
                if (isFinite(audioEl.duration) && audioEl.duration > 0) {{
                    mediaRecoveryAttempts = 0;
                    mediaRecoveryUsed = false;
                    mediaRecoveryFile = activeAudioFile || "";
                }}
                hideRestartNotice();
                hideStreamOverlay();
            }}).catch(() => {{
                showStreamOverlay(streamUnavailableReason);
            }});
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
                // file. Move on instead of restarting it again.
                quickEndRestarts = 0;
                sendAction('track_ended');
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
        let streamOverlayWanted = true;

        function renderStreamOverlay() {{
            const overlay = document.getElementById('streamOverlay');
            if (!overlay) return;
            const visible = streamOverlayWanted && activeTab === 'player';
            overlay.classList.toggle('show', visible);
            // Lock the page behind the gate: a swipe would otherwise scroll the
            // dashboard and leave the engage button below the fold.
            document.body.classList.toggle('overlay-active', visible);
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
            renderStreamOverlay();
        }}

        // The overlay is the first thing a phone sees, so it explains the host
        // state on its own instead of waiting for a tap that would go nowhere.
        function syncStreamOverlayDetail() {{
            if (!streamOverlayWanted) return;
            if (activeAudioFile) return;
            showStreamOverlay(streamUnavailableReason ||
                'Waiting for the host to queue a mix…');
        }}

        function hideStreamOverlay() {{
            streamOverlayWanted = false;
            renderStreamOverlay();
            const banner = document.getElementById('mobileAudioBanner');
            if (banner) banner.style.display = 'block';
        }}

        let streamAutoStarted = false;
        // True when the dashboard is mirroring a running MP Audio Player. In
        // that mode the host owns the audio: the phone must not open its own
        // stream, or two devices render the mix out of step with each other.
        let hostPlayerActive = false;
        let hostPlayerFile = "";

        // Called once the dashboard knows which file the server is streaming.
        function maybeAutoStartStream() {{
            if (streamAutoStarted) return;
            if (!activeAudioFile) return;
            // A live host player is the source of truth and is already making
            // sound where it should. Auto-engaging here would start a second,
            // unsynchronised copy on the phone.
            if (hostPlayerActive) {{
                hideStreamOverlay();
                return;
            }}
            streamAutoStarted = true;
            engageStreamFromServer(lastServerResume);
        }}

        // Keep the local element pointed at the host's file so a listener can
        // opt in with "Engage Audio Stream", without playing automatically.
        function syncHostPlayerFile() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (!audioEl || !activeAudioFile) return;
            if (hostPlayerFile === activeAudioFile && audioStreamConfigured) return;
            hostPlayerFile = activeAudioFile;
            audioEl.src = '/api/stream?file=' + encodeURIComponent(activeAudioFile);
            audioStreamConfigured = true;
            const resumeAt = Number(lastServerResume) || 0;
            if (resumeAt > 0) {{
                audioEl.addEventListener('loadedmetadata', function once() {{
                    audioEl.removeEventListener('loadedmetadata', once);
                    seekAudioToWithResume(resumeAt, true);
                }});
            }}
            audioEl.load();
        }}

        function activateMobileStream() {{
            engageStreamFromServer(lastServerResume);
        }}

        function handlePlayPauseClick() {{
            const audioEl = document.getElementById('mobileAudioStream');
            if (audioEl) {{
                if (!audioEl.paused) {{
                    audioEl.pause();
                }} else if (!activeAudioFile) {{
                    showStreamOverlay(streamUnavailableReason ||
                        'No audio is queued on the host. Start a mix in MP Audio Player, then tap again.');
                    return;
                }} else {{
                    audioEl.play().catch(e => {{
                        showStreamOverlay(streamUnavailableReason);
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

            if (document.hidden) {{
                // A hidden tab paints nothing; keeping the 60 fps loop alive only
                // burns battery and competes with the poll timers when Safari
                // throttles background work.
                requestAnimationFrame(animLoop);
                return;
            }}

            // Synthesise the spectrum here, once per frame, instead of asking
            // the server for it 10 times a second. The bars follow the phone's
            // own audio element, which the host cannot observe.
            if (isPlayerActive) {{
                const t = performance.now() / 1000;
                for (let i = 0; i < BANDS; i++) {{
                    const base = 0.30 + 0.22 * Math.sin(t * 1.7 + i * 0.55);
                    const wobble = 0.14 * Math.sin(t * 3.1 + i * 1.15);
                    const tilt = 1.0 - (i / BANDS) * 0.55;
                    targetLevels[i] = Math.max(0.05, Math.min(0.95, (base + wobble) * tilt));
                }}
            }}

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
                const res = await fetch('/api/eq');
                if (!res.ok) throw new Error('eq ' + res.status);
                const data = await res.json();
                // The phone is the only audio output, so the visualizer follows
                // its element. The server cannot observe audio itself.
                isPlayerActive = audioElIsPlaying();

                if (data.levels && data.levels.length) {{
                    for (let i = 0; i < BANDS && i < data.levels.length; i++) {{
                        targetLevels[i] = isPlayerActive ? Math.max(0.04, Math.min(1.0, data.levels[i])) : 0.04;
                    }}
                }}
                // The spectrum is synthesised locally by animLoop(). There is no
                // local audio analysis to fetch - the phone owns playback - so
                // polling the server for it would only add load.
                const audioEl = document.getElementById('mobileAudioStream');
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
                    // Do not fight the assistant's temporary duck while speech
                    // is playing; the user's level is restored afterwards.
                    if (audioEl && duckedMixVolume === null) {{
                        audioEl.volume = Math.max(0, Math.min(1, data.volume / 100.0));
                    }}
                }}
                if (data.pitch_percent !== undefined) {{
                    const p = data.pitch_percent;
                    document.getElementById('pitchPill').innerText = 'Pitch: ' + (p > 0 ? '+' : '') + p + '%';
                }}
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
            if (state.status === 'disabled') {{
                return 'Streaming is disabled on the host (WEB_COMPANION_AUTOPLAY).';
            }}
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
                disabled: '⏹ Streaming disabled',
                player_unavailable: '⚠ Stream source unavailable',
                player_unreachable: '⚠ Stream source unreachable',
                playback_unverified: '⚠ Stream not confirmed',
            }};
            el.innerText = labels[state.status] || state.status;
            // A phone has no hover, so the cause must be on screen, not only in
            // the tooltip: "No mixes found" is wrong when the reason is a macOS
            // privacy block on the archive volume.
            if (state.detail && state.status !== 'ready' && state.status !== 'playing') {{
                el.innerText += ' — ' + state.detail;
            }}
            el.className = 'autoplay-notice ' + (
                (state.status === 'ready' || state.status === 'playing') ? 'ok' :
                (state.status === 'empty_playlist' || state.status === 'disabled') ? '' : 'warn'
            );
            el.style.display = 'block';
            if (state.detail) el.title = state.detail;
        }}

        function showRestartNotice(message) {{
            const el = document.getElementById('restartNotice');
            const text = document.getElementById('restartNoticeText');
            if (text && message) text.innerText = message;
            if (el) el.classList.add('show');
            restartNoticeShown = true;
        }}

        function hideRestartNotice() {{
            const el = document.getElementById('restartNotice');
            if (el) el.classList.remove('show');
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
            showRestartNotice('MP Audio Web Companion is restarting — reconnecting…');
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
                const res = await fetch('/api/status');
                const data = await res.json();
                statusFailureCount = 0;

                observeAnnouncement(data.announcement, data.announcement_at);

                // A new instance id means Stop/Start ran while this page was
                // open. Resume on the new instance without a reload so the
                // phone keeps playing after one reconnect.
                if (data.instance) {{
                    if (serverInstance && data.instance !== serverInstance) {{
                        serverInstance = data.instance;
                        showRestartNotice('MP Audio Web Companion has been restarted — reconnecting…');
                        reattachToRestartedServer();
                        setTimeout(() => hideRestartNotice(), 4000);
                        return;
                    }}
                    serverInstance = data.instance;
                }}
                if (restartNoticeShown) hideRestartNotice();

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
                streamUnavailableReason = streamUnavailableDetail(data);
                syncStreamOverlayDetail();

                // A running host player means the workstation is the output.
                // Mirror its file for an opt-in listen, but never autoplay.
                const wasHostActive = hostPlayerActive;
                hostPlayerActive = Boolean(data.host_player);
                if (!hostPlayerActive && wasHostActive) {{
                    hostPlayerFile = "";
                }}

                if (data.active_file && activeAudioFile !== data.active_file) {{
                    const wasPlaying = audioStreamEngaged && audioElIsPlaying();
                    activeAudioFile = data.active_file;
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
                    const audioEl = document.getElementById('mobileAudioStream');
                    if (audioEl) {{
                        audioEl.src = '/api/stream?file=' + encodeURIComponent(data.active_file);
                        audioStreamConfigured = true;
                        // A track change is followed by the same rule as the
                        // first engage: resume only from a safe point, never
                        // from the tail of a file that ran unattended.
                        const resumeAt = Number(data.resume_sec) || 0;
                        if (resumeAt > 0) {{
                            // Wait for metadata before seeking, otherwise the
                            // assignment is discarded by the new source load.
                            audioEl.addEventListener('loadedmetadata', function once() {{
                                audioEl.removeEventListener('loadedmetadata', once);
                                seekAudioToWithResume(resumeAt, true);
                            }});
                        }}
                        audioEl.load();
                        // Keep playing across a server-side track change, but
                        // only when the phone is the one playing. With a live
                        // host player the phone stays silent unless engaged.
                        if (wasPlaying && !hostPlayerActive) {{
                            audioEl.play().catch(() => {{}});
                        }}
                    }}
                }} else if (audioStreamEngaged === false) {{
                    // Not engaged yet: keep the starting point current so the
                    // tap that does engage uses the newest safe position.
                    lastServerResume = Number(data.resume_sec) || 0;
                }}
                if (hostPlayerActive) syncHostPlayerFile();
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
            btn.classList.toggle('running', running);
            btn.classList.toggle('fullscreen', running && fullscreen);
            btn.setAttribute('aria-pressed', running ? 'true' : 'false');
            const label = document.getElementById('launchVisualizerState');
            if (label) {{
                label.innerText = !running ? 'Off' : (fullscreen ? 'Fullscreen' : 'Windowed');
            }}
            btn.title = !running
                ? 'Open MP Visualizer on the computer'
                : (fullscreen
                    ? 'MP Visualizer is fullscreen — press to close it'
                    : 'MP Visualizer is open — press for fullscreen');
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
                if (data && data.success) {{
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
            if (paused) {{
                stopPolls();
            }} else {{
                // Catch up immediately: the page was frozen while hidden and
                // the displayed state is now stale.
                startPolls();
                fetchStatus();
                fetchEq();
            }}
        }}

        document.addEventListener('visibilitychange', () => {{
            setPollingPaused(document.hidden);
        }});
        window.addEventListener('pagehide', () => setPollingPaused(true));
        window.addEventListener('pageshow', (e) => {{
            if (e.persisted) setPollingPaused(false);
        }});

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

    # Security headers collected during a request, appended by end_headers so
    # every response path gets them without touching each handler.
    _extra_headers: List[Tuple[str, str]] = []

    def send_response(self, code, message=None):
        self._extra_headers = sec.security_headers()
        super().send_response(code, message)

    def end_headers(self):
        try:
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

    def _authorized(self, path: str, query: dict) -> bool:
        if not COMPANION_REQUIRE_AUTH:
            return True
        if sec.is_authorized(self.headers, COMPANION_TOKEN, path, query):
            return True
        presented = sec.extract_presented_token(self.headers, query)
        if COMPANION_TOKEN and presented and hmac.compare_digest(
            presented, COMPANION_TOKEN
        ):
            # A bookmarked `?token=` link is promoted to a cookie so the phone
            # keeps working after the URL is cleaned from the address bar.
            self._extra_headers = sec.security_headers()
            self._extra_headers.extend(
                sec.auth_challenge_headers(COMPANION_TOKEN, path)
            )
        body = json.dumps({
            "error": "unauthorized",
            "detail": "This companion requires an access token. Open the URL "
                      "printed by the service, or set MP_COMPANION_TOKEN.",
        }).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("WWW-Authenticate", 'Bearer realm="MP Mix Manager"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        return False

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
        if url.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
        elif url.path in ("/api/status", "/api/theme", "/api/eq", "/api/autoplay",
                          "/api/themes", "/api/set_theme", "/api/playlist", "/api/mode",
                          "/api/launchpad", "/api/announce", "/api/assistant/status",
                          "/api/assistant/voices", "/api/assistant/chat",
                          "/api/assistant/speech", "/api/assistant/start",
                          "/api/assistant/abort"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        parsed_query = urllib.parse.parse_qs(url.query)

        if not self._client_allowed():
            return
        if not self._authorized(url.path, parsed_query):
            return

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
            self.end_headers()
            self.wfile.write(json.dumps(eq_data).encode("utf-8"))
        elif url.path == "/api/status":
            status = get_playback_status(CLI_THEME)
            status["autoplay"] = autoplay_state()
            status["instance"] = INSTANCE_ID
            announcement = current_announcement()
            if announcement:
                status["announcement"] = announcement["message"]
                status["announcement_at"] = announcement["at"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode("utf-8"))
        elif url.path == "/api/autoplay":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(autoplay_state()).encode("utf-8"))
        elif url.path == "/api/theme":
            theme = get_theme_palette(CLI_THEME)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(theme).encode("utf-8"))
        elif url.path == "/api/themes":
            payload = {
                "current": get_current_theme_id(CLI_THEME),
                "themes": available_themes(),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
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
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
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
            }
            self.send_response(200 if chosen else 400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/playlist":
            payload = get_mini_playlist()
            payload["shuffle"] = bool(load_stream_session().get("shuffle", False))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/play-random":
            payload = play_random_track()
            status = 200 if payload.get("success") else 409
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/launchpad":
            payload = launch_pad_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/announce":
            # The tray posts here just before it stops the service; a dashboard
            # polling this endpoint can show the notice even if /api/status is
            # already unreachable.
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"announcement": current_announcement()}
            ).encode("utf-8"))
        elif url.path == "/api/assistant/status":
            payload = harmony_status(force="1" in parsed_query.get("force", ["0"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/assistant/voices":
            current = harmony_status().get("voice", HARMONY_DEFAULT_VOICE)
            payload = {
                "current": current,
                "current_label": harmony_voice_label(current),
                "default": HARMONY_DEFAULT_VOICE,
                "voice_speed": harmony_voice_speed(),
                "voices": [{"id": vid, "label": label} for vid, label in HARMONY_VOICES.items()],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
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
            self.send_response(200 if payload.get("success") else 501)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
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
            safe_path = sec.safe_stream_path(requested, _archive_dirs())
            if safe_path is not None:
                self._serve_audio_stream(str(safe_path))
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
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
            # Refuse a file with no audio frames up front. Serving its 44-byte
            # header as a 200 makes the <audio> element raise a media error,
            # which the dashboard historically read as "the companion
            # restarted" and retried forever. A 404 is unambiguous: the page
            # stops and can explain that this mix is empty on the computer.
            if not is_streamable_file(file_path):
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(json.dumps({
                        "error": "no_audio_frames",
                        "detail": "The mix file has no playable audio.",
                    }).encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
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

        if not self._client_allowed():
            return
        if not self._authorized(url.path, parsed_query):
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
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "action": action}).encode("utf-8"))
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
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"success": queued is not None, "announcement": queued}
            ).encode("utf-8"))
        elif url.path in ("/api/launchpad/launch", "/api/launchpad/close",
                          "/api/launchpad/visualizer"):
            params = urllib.parse.parse_qs(url.query)
            if url.path.endswith("/close"):
                payload = launch_pad_close_all()
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
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_response(404)
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


def main():
    global CLI_THEME, COMPANION_TOKEN, COMPANION_CIDRS, COMPANION_REQUIRE_AUTH
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
    parser.add_argument("--print-token", action="store_true",
                        help="Print the companion access token and exit")
    parser.add_argument("--rotate-token", action="store_true",
                        help="Replace the access token, then start")
    parser.add_argument("--no-auth", action="store_true",
                        help="Disable token authentication (not recommended)")
    parser.add_argument("--allow-cidr", type=str, default="",
                        help="Comma-separated CIDRs allowed to connect")
    args = parser.parse_args()

    COMPANION_TOKEN = sec.ensure_token(CONFIG_DIR, rotate=args.rotate_token)
    COMPANION_REQUIRE_AUTH = not args.no_auth
    cidr_config = args.allow_cidr or _config_value("WEB_COMPANION_ALLOW_CIDR", "")
    COMPANION_CIDRS = sec.parse_cidr_list(cidr_config)

    if args.print_token:
        print(COMPANION_TOKEN)
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
        """URL that carries the token once, so a phone can just tap it."""
        base = f"http://{host}:{args.port}/"
        if COMPANION_REQUIRE_AUTH and COMPANION_TOKEN:
            return f"{base}?token={COMPANION_TOKEN}"
        return base

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
    if COMPANION_REQUIRE_AUTH:
        print(f"  • Access Token:   {sec.redact(COMPANION_TOKEN)} "
              f"(stored in {sec.token_path(CONFIG_DIR)})")
    else:
        print("  • Access Token:   DISABLED (--no-auth) — any device on the "
              "network can control this workstation")
    if COMPANION_CIDRS:
        print(f"  • Allowed CIDRs:  {', '.join(str(n) for n in COMPANION_CIDRS)}")
    if not sec.is_loopback_bind(args.host):
        print("  ⚠  Listening on a network interface. The URL above contains a "
              "secret; treat it like a password.")
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
