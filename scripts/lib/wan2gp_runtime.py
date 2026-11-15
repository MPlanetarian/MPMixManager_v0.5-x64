#!/usr/bin/env python3
"""
MP Mix Manager - WAN2GP / Infinite Talker runtime resolver.

The four batch helpers (flux, ltx, infinite talk, tts) each carried their own
copy of the same search: look for the WAN2GP application directory, then for
the interpreter inside its venv, then for the output folder. Every copy had a
different order and every copy included at least one absolute path belonging to
one particular machine (`/var/home/mplanetarian/pinokio/...`,
`/run/media/mplanetarian/GAMES1/...`, `X:/WAN2GP/...`).

That made the feature look portable and behave otherwise: on any other install
the search fell through to a path that does not exist and the batch script
failed with "WAN2GP not found" even though the user had configured it.

This module is the single resolver. Order is explicit and config first:

  1. ``WAN2GP_DIR`` / ``WAN2GP_PYTHON`` / ``WAN2GP_OUTPUTS`` from the
     environment or ``config.env``.
  2. The conventional locations under the current user's home
     (``~/pinokio/api/wan.git/app`` and ``~/WAN2GP/Wan2GP``).
  3. A Windows drive root only when the path actually exists.

Nothing here assumes a username, a drive letter or a mount point.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# Candidate application directories, relative to a base directory.
_APP_RELATIVE_CANDIDATES = (
    "pinokio/api/wan.git/app",
    "WAN2GP/Wan2GP",
    "wan2gp",
    "Wan2GP",
)

# Executable names to look for inside an app directory's venv.
_VENV_RELATIVE_PYTHONS = (
    "venv/bin/python",
    "venv/bin/python3",
    "venv/Scripts/python.exe",
    "conda_envs/wan2gp/python.exe",
)


def _read_config() -> Dict[str, str]:
    """Read config.env without executing it.

    The batch helpers used to ``source`` the file from a bash prelude; a Python
    process cannot, and should not, run arbitrary shell. Parsing ``KEY=value``
    is enough and cannot execute anything.
    """
    values: Dict[str, str] = {}
    script_dir = Path(__file__).resolve().parent
    candidates: List[Path] = []
    explicit = os.environ.get("MP_CONFIG", "")
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([
        script_dir / "config.env",
        script_dir.parent / "config.env",
        Path.home() / ".config" / "mix-manager" / "config.env",
    ])
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                values[key.strip()] = value
            break
        except OSError:
            continue
    return values


def setting(name: str, default: str = "") -> str:
    """Environment first, then config.env, then ``default``."""
    value = os.environ.get(name)
    if value:
        return str(value).strip()
    return _read_config().get(name, default) or default


def _first_existing(candidates: Iterable[Path]) -> Optional[Path]:
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def app_root() -> Optional[Path]:
    """Directory that contains ``wgp.py``, or None when WAN2GP is not installed."""
    configured = setting("WAN2GP_DIR")
    if configured:
        # An explicit setting is authoritative. Falling through to a different
        # tree when the configured one is missing would run somebody else's
        # install, and the user would have no idea why their path was ignored.
        candidate = Path(configured).expanduser()
        return candidate if candidate.exists() else None

    candidates: List[Path] = []

    bases = [Path.home()]
    # A portable install beside the app tree is a common layout.
    bases.append(Path(__file__).resolve().parent.parent)

    for base in bases:
        for relative in _APP_RELATIVE_CANDIDATES:
            candidates.append(base / relative)

    # Windows drive roots, only when the drive letter really exists. This keeps
    # a Linux/macOS host from stat-ing a path it cannot have.
    if sys.platform == "win32":
        for letter in "CDEX":
            candidates.append(Path(f"{letter}:/WAN2GP/Wan2GP"))
            candidates.append(Path(f"{letter}:/pinokio/api/wan.git/app"))

    return _first_existing(candidates)


def python_bin(root: Optional[Path] = None) -> Optional[Path]:
    """Interpreter that can import WAN2GP, preferring an explicit setting."""
    configured = setting("WAN2GP_PYTHON")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return candidate

    root = root or app_root()
    if root:
        for relative in _VENV_RELATIVE_PYTHONS:
            candidate = root / relative
            if candidate.exists():
                return candidate

    # Fall back to the interpreter running this process: WAN2GP may be
    # installed into the same environment (pip install -e .).
    return Path(sys.executable)


def outputs_dir(root: Optional[Path] = None) -> Path:
    """Where generated media is written. Always returns a usable path."""
    configured = setting("WAN2GP_OUTPUTS") or setting("WAN2GP_INFTLKBATCH_DIR")
    if configured:
        return Path(configured).expanduser()
    root = root or app_root()
    if root:
        return root / "outputs"
    return Path.home() / "WAN2GP_OUTPUTS"


def flux_batch_dir(root: Optional[Path] = None) -> Path:
    configured = setting("WAN2GP_FLUX_BATCH_DIR")
    if configured:
        return Path(configured).expanduser()
    root = root or app_root()
    base = root if root else Path.home()
    return base / "FLUX_BATCH"


def ltx_batch_dir(root: Optional[Path] = None) -> Path:
    configured = setting("WAN2GP_LTX_BATCH_DIR")
    if configured:
        return Path(configured).expanduser()
    root = root or app_root()
    base = root if root else Path.home()
    return base / "LTX_BATCH"


def stop_script() -> Optional[Path]:
    """The wan2gp.sh that knows how to stop the server, if it can be found."""
    configured = setting("WAN2GP_STOP_SCRIPT")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return candidate
    script_dir = Path(__file__).resolve().parent
    for candidate in (script_dir / "wan2gp.sh", script_dir.parent / "wan2gp.sh"):
        if candidate.exists():
            return candidate
    return None


def summary() -> Dict[str, str]:
    """Human-readable resolution, for a --status flag and for log headers."""
    root = app_root()
    interpreter = python_bin(root)
    return {
        "app_root": str(root) if root else "(not found)",
        "python": str(interpreter) if interpreter else "(not found)",
        "outputs": str(outputs_dir(root)),
        "flux_batch": str(flux_batch_dir(root)),
        "ltx_batch": str(ltx_batch_dir(root)),
        "installed": "yes" if root else "no",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(summary(), indent=2))
