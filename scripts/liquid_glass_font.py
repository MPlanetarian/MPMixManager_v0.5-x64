"""VTE font for MP Liquid Player, MP Gui, and MP Liquid Mixer.

The shipped face is the generic Monospace family at 11 pt. Hack stays in
the recommended list, and a saved font, an environment variable, or
--font still replaces this default.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

HACK_FONT = "Hack 11"
FALLBACK_FONT = "Monospace 11"
DEFAULT_LIQUID_GLASS_FONT = FALLBACK_FONT


def hack_font_installed() -> bool:
    """True when fontconfig has the Hack family. Other platforms stay false."""
    if not sys.platform.startswith("linux"):
        return False
    fc_list = shutil.which("fc-list")
    if not fc_list:
        return False
    try:
        proc = subprocess.run(
            [fc_list, ":family=Hack", "file"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def default_liquid_glass_font() -> str:
    """Monospace 11. A saved font, the environment, or --font still wins."""
    return DEFAULT_LIQUID_GLASS_FONT
