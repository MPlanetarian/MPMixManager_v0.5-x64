#!/usr/bin/env python3
"""Move MP Liquid Mixer recordings into the primary MIX_ARCHIVE.

The mixer records into ``MP_AUDIOWAV_REC_OUTPUT``. Export copies nothing: it
moves every finished WAV/FLAC part to the place the archive manager expects:

    *.wav  -> <MIX_ARCHIVE>/
    *.flac -> <MIX_ARCHIVE>/FLAC_CONVERTED_OUTPUTS/

A destination with the same name is never overwritten; the new file gets a
numeric suffix instead. Cross-filesystem archives work because ``shutil.move``
falls back to copy-and-delete.

The primary archive is the one in ``MIX_ARCHIVE_DIR`` (environment first, then
``config.env``). If that is not configured, nothing is moved and the caller is
told that a primary folder must be set up first.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import mixer_recording

WAV_SUFFIXES = {".wav"}
FLAC_SUFFIXES = {".flac"}
AUDIO_SUFFIXES = WAV_SUFFIXES | FLAC_SUFFIXES

FLAC_DIR_NAME = "FLAC_CONVERTED_OUTPUTS"


def _config_value(key: str, path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def configured_archive_values(base: Optional[Path] = None,
                              config_dir: Optional[Path] = None) -> List[str]:
    """Every spelling of MIX_ARCHIVE_DIR this install has, in priority order."""
    base = Path(base or mixer_recording.PROJECT_ROOT)
    config_dir = Path(config_dir or mixer_recording.CONFIG_DIR)
    values: List[str] = []
    env = os.environ.get("MIX_ARCHIVE_DIR", "").strip()
    if env:
        values.append(env)
    for cfg in (base / "config.env", config_dir / "config.env"):
        value = _config_value("MIX_ARCHIVE_DIR", cfg)
        if value:
            values.append(value)
    seen = set()
    unique: List[str] = []
    for value in values:
        expanded = os.path.expandvars(os.path.expanduser(value.strip().strip('"').strip("'")))
        if expanded and expanded not in seen:
            seen.add(expanded)
            unique.append(expanded)
    return unique


def resolve_primary_archive(base: Optional[Path] = None,
                            config_dir: Optional[Path] = None) -> Dict[str, object]:
    """The primary MIX_ARCHIVE folder, or why there is not one.

    ``problem`` is ``""`` on success, ``"unset"`` when no folder was ever
    configured, and ``"missing"`` when a configured path is not a directory
    (an unplugged drive, for example). Nothing is created here: exporting to a
    guessed path would hide a broken archive setting.
    """
    values = configured_archive_values(base, config_dir)
    if not values:
        return {"path": None, "problem": "unset", "configured": ""}
    for value in values:
        path = Path(value)
        if path.is_dir():
            # Keep the configured spelling (for example /mnt/...), so the
            # notification names the path the user recognises; .absolute()
            # still gives move() a stable target.
            return {"path": path.absolute(), "problem": "", "configured": value}
    return {"path": None, "problem": "missing", "configured": values[0]}


def recording_dirs(base: Optional[Path] = None, config_dir: Optional[Path] = None,
                   archive: Optional[Path] = None) -> List[Path]:
    """Every place a mixer recording can live, newest layouts first.

    Recordings made before a MIX_ARCHIVE was configured sit in the install tree
    or the config directory, so export scans those too instead of only the
    current recording folder.
    """
    base = Path(base or mixer_recording.PROJECT_ROOT)
    config_dir = Path(config_dir or mixer_recording.CONFIG_DIR)
    candidates = []
    if archive is not None:
        candidates.append(Path(archive) / mixer_recording.SUBDIR)
    candidates.extend([
        base / mixer_recording.SUBDIR,
        config_dir / mixer_recording.SUBDIR,
    ])
    found: List[Path] = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        found.append(resolved)
    return found


def find_recordings(dirs: List[Path]) -> List[Path]:
    """Finished WAV/FLAC files in the recording folders, without duplicates."""
    found: List[Path] = []
    seen = set()
    for directory in dirs:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file() or entry.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                resolved = entry.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(entry)
    return found


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def export_recordings(base: Optional[Path] = None,
                      config_dir: Optional[Path] = None) -> Dict[str, object]:
    """Move every recorded WAV/FLAC into the primary archive.

    Returns ``action`` as one of ``moved``, ``empty``, ``no_archive``,
    ``missing_archive``, plus ``moved``/``failed`` lists and the destination
    paths. The caller decides how to notify; nothing here shows a window.
    """
    resolved = resolve_primary_archive(base, config_dir)
    archive = resolved["path"]
    if archive is None:
        action = "no_archive" if resolved["problem"] == "unset" else "missing_archive"
        return {
            "action": action,
            "archive": None,
            "configured": resolved.get("configured", ""),
            "moved": [],
            "failed": [],
            "wav": 0,
            "flac": 0,
        }
    archive = Path(archive)
    flac_dir = archive / FLAC_DIR_NAME
    files = find_recordings(recording_dirs(base, config_dir, archive))
    if not files:
        return {
            "action": "empty",
            "archive": archive,
            "moved": [],
            "failed": [],
            "wav": 0,
            "flac": 0,
        }
    moved: List[Path] = []
    failed: List[Dict[str, str]] = []
    wav_count = flac_count = 0
    for source in files:
        is_flac = source.suffix.lower() in FLAC_SUFFIXES
        destination_dir = flac_dir if is_flac else archive
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = _unique_destination(destination_dir / source.name)
            shutil.move(str(source), str(destination))
        except OSError as exc:
            failed.append({"file": str(source), "error": str(exc)})
            continue
        moved.append(destination)
        if is_flac:
            flac_count += 1
        else:
            wav_count += 1
    return {
        "action": "moved" if moved else "failed",
        "archive": archive,
        "moved": moved,
        "failed": failed,
        "wav": wav_count,
        "flac": flac_count,
    }


def notify(title: str, message: str, icon: str = "audio-x-generic") -> bool:
    """Post through the MP Tray's own desktop-notification helper."""
    try:
        import mp_system_tray
        return bool(mp_system_tray.notify(title, message, icon))
    except Exception:
        pass
    sender = shutil.which("notify-send")
    if not sender:
        return False
    try:
        import subprocess
        subprocess.run(
            [sender, "-a", "MP Mix Manager", "-i", icon, title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        return True
    except Exception:
        return False


def notify_export(result: Dict[str, object]) -> bool:
    """The user-facing message for one export result."""
    action = str(result.get("action") or "")
    archive = result.get("archive")
    if action == "no_archive":
        return notify(
            "MP Liquid Mixer",
            "Set up a primary MIX_ARCHIVE folder before you can export mixes.",
            "dialog-warning",
        )
    if action == "missing_archive":
        configured = str(result.get("configured") or "the configured path")
        return notify(
            "MP Liquid Mixer",
            f"The primary MIX_ARCHIVE folder was not found at {configured}. "
            "Reconnect it or set it up before exporting mixes.",
            "dialog-warning",
        )
    if action == "empty":
        return notify(
            "MP Liquid Mixer",
            "No recorded mixes were found to export.",
            "dialog-information",
        )
    if action == "failed":
        return notify(
            "MP Liquid Mixer",
            f"Could not move any recorded mixes. Check permissions for {archive}.",
            "dialog-error",
        )
    wav = int(result.get("wav") or 0)
    flac = int(result.get("flac") or 0)
    counts = []
    if wav:
        counts.append(f"{wav} WAV")
    if flac:
        counts.append(f"{flac} FLAC")
    summary = " and ".join(counts) if counts else "recorded"
    failed = len(result.get("failed") or [])
    if failed:
        message = (
            f"Moved {summary} file(s) to your primary MIX_ARCHIVE folder:\n{archive}\n"
            f"{failed} file(s) could not be moved."
        )
    else:
        message = (
            f"All recorded mixes have been moved to your primary MIX_ARCHIVE folder:\n{archive}\n"
            f"{summary} file(s)."
        )
    return notify("MP Liquid Mixer", message, "document-save")
