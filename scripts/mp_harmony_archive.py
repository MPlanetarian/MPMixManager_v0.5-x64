#!/usr/bin/env python3
# ==============================================================================
# mp_harmony_archive.py — register MP Harmony speech into the MP Reporter archive.
# ------------------------------------------------------------------------------
# MP Harmony writes each spoken reply as a .wav/.txt pair in its own
# audio_responses/ folder. MP Reporter browses spoken output from
# MP_REPORTER/TTS_AUDIO/<bucket>/, so without this step Harmony speech is
# invisible to the reporting suite.
#
# This module copies new Harmony pairs into TTS_AUDIO/HARMONY and keeps a small
# manifest so a re-run only copies what is new. It is safe to run repeatedly and
# safe to run while the agent is writing: a pair is only copied once both files
# exist and the WAV has a complete RIFF header.
#
# Usage:
#   mp_harmony_archive.py sync            Copy new pairs into TTS_AUDIO/HARMONY
#   mp_harmony_archive.py sync --dry-run  Show what would be copied
#   mp_harmony_archive.py status          Count archived vs pending pairs
#   mp_harmony_archive.py prune --days 30 Delete archived copies older than N days
#
# Library:
#   from mp_harmony_archive import sync_archive, archive_status
# ==============================================================================
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
DEFAULT_AUDIO_DIR = Path(
    os.environ.get("HARMONY_AUDIO_DIR", "")
    or (HOME / "OpenAI" / "MP_Harmony_Agent" / "audio_responses")
)
DEFAULT_REPORTER_DIR = Path(os.environ.get("MP_REPORTER_DIR", "") or (HOME / "MP_REPORTER"))
BUCKET = "HARMONY"
MANIFEST_NAME = ".harmony_archive.json"


def _archive_dir(reporter_dir: Path) -> Path:
    return reporter_dir / "TTS_AUDIO" / BUCKET


def _manifest_path(reporter_dir: Path) -> Path:
    return _archive_dir(reporter_dir) / MANIFEST_NAME


def _load_manifest(reporter_dir: Path) -> Dict[str, Any]:
    path = _manifest_path(reporter_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except Exception:
        pass
    return {"entries": {}}


def _save_manifest(reporter_dir: Path, data: Dict[str, Any]) -> None:
    path = _manifest_path(reporter_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _wav_is_complete(path: Path) -> bool:
    """A WAV is only copied once it has data past the 44-byte RIFF header."""
    try:
        size = path.stat().st_size
        if size <= 44:
            return False
        with open(path, "rb") as handle:
            header = handle.read(12)
        return header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    except Exception:
        return False


def discover_pairs(audio_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every usable Harmony speech pair, oldest first."""
    audio_dir = audio_dir or DEFAULT_AUDIO_DIR
    if not audio_dir.is_dir():
        return []

    pairs: List[Dict[str, Any]] = []
    for txt in sorted(audio_dir.glob("*.txt")):
        stem = txt.stem
        wav = audio_dir / f"{stem}.wav"
        if not wav.is_file() or not _wav_is_complete(wav):
            continue
        try:
            st = wav.stat()
        except Exception:
            continue
        snippet = ""
        try:
            snippet = txt.read_text(encoding="utf-8", errors="replace").strip()[:200]
        except Exception:
            pass
        pairs.append({
            "id": stem,
            "wav": wav,
            "txt": txt,
            "mtime": st.st_mtime,
            "size": st.st_size,
            "snippet": " ".join(snippet.split()),
        })
    pairs.sort(key=lambda p: p["mtime"])
    return pairs


def sync_archive(audio_dir: Optional[Path] = None,
                 reporter_dir: Optional[Path] = None,
                 dry_run: bool = False,
                 limit: Optional[int] = None) -> Dict[str, Any]:
    """Copy new Harmony pairs into TTS_AUDIO/HARMONY. Returns a summary dict."""
    audio_dir = audio_dir or DEFAULT_AUDIO_DIR
    reporter_dir = reporter_dir or DEFAULT_REPORTER_DIR
    archive = _archive_dir(reporter_dir)
    manifest = _load_manifest(reporter_dir)
    entries = manifest.setdefault("entries", {})

    pairs = discover_pairs(audio_dir)
    pending = [p for p in pairs if p["id"] not in entries]
    if limit is not None:
        pending = pending[-limit:] if limit > 0 else pending

    copied = 0
    failed = 0
    for pair in pending:
        stem = pair["id"]
        if dry_run:
            print(f"  would copy: {stem}")
            copied += 1
            continue
        try:
            archive.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pair["wav"], archive / f"{stem}.wav")
            shutil.copy2(pair["txt"], archive / f"{stem}.txt")
            entries[stem] = {
                "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "mtime": pair["mtime"],
                "size": pair["size"],
            }
            copied += 1
        except Exception as exc:
            failed += 1
            print(f"  failed: {stem}: {exc}", file=sys.stderr)

    if not dry_run and copied:
        manifest["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save_manifest(reporter_dir, manifest)

    return {
        "audio_dir": str(audio_dir),
        "archive_dir": str(archive),
        "total_pairs": len(pairs),
        "already_archived": len(pairs) - len(pending),
        "pending": len(pending),
        "copied": copied,
        "failed": failed,
        "dry_run": dry_run,
    }


def archive_status(audio_dir: Optional[Path] = None,
                   reporter_dir: Optional[Path] = None) -> Dict[str, Any]:
    audio_dir = audio_dir or DEFAULT_AUDIO_DIR
    reporter_dir = reporter_dir or DEFAULT_REPORTER_DIR
    archive = _archive_dir(reporter_dir)
    manifest = _load_manifest(reporter_dir)
    entries = manifest.get("entries", {})
    pairs = discover_pairs(audio_dir)
    archived_files = len(list(archive.glob("*.wav"))) if archive.is_dir() else 0
    return {
        "audio_dir": str(audio_dir),
        "archive_dir": str(archive),
        "harmony_pairs": len(pairs),
        "manifest_entries": len(entries),
        "archived_wavs": archived_files,
        "pending": sum(1 for p in pairs if p["id"] not in entries),
        "last_sync": manifest.get("last_sync", "never"),
        "audio_dir_exists": audio_dir.is_dir(),
        "archive_dir_exists": archive.is_dir(),
    }


def prune_archive(reporter_dir: Optional[Path] = None,
                  days: int = 30,
                  dry_run: bool = False) -> Dict[str, Any]:
    """Delete archived Harmony copies older than `days`, keeping the originals."""
    reporter_dir = reporter_dir or DEFAULT_REPORTER_DIR
    archive = _archive_dir(reporter_dir)
    if not archive.is_dir() or days <= 0:
        return {"removed": 0, "kept": 0, "dry_run": dry_run}

    cutoff = time.time() - (days * 86400)
    manifest = _load_manifest(reporter_dir)
    entries = manifest.get("entries", {})
    removed = kept = 0
    for wav in sorted(archive.glob("*.wav")):
        try:
            if wav.stat().st_mtime < cutoff:
                if not dry_run:
                    wav.unlink(missing_ok=True)
                    (archive / f"{wav.stem}.txt").unlink(missing_ok=True)
                    entries.pop(wav.stem, None)
                removed += 1
            else:
                kept += 1
        except Exception:
            kept += 1
    if not dry_run and removed:
        _save_manifest(reporter_dir, manifest)
    return {"removed": removed, "kept": kept, "dry_run": dry_run}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive MP Harmony speech into the MP Reporter audio folder.")
    parser.add_argument("action", nargs="?", default="sync",
                        choices=["sync", "status", "prune"])
    parser.add_argument("--audio-dir", type=Path, default=None,
                        help=f"Harmony audio_responses (default: {DEFAULT_AUDIO_DIR})")
    parser.add_argument("--reporter-dir", type=Path, default=None,
                        help=f"MP Reporter root (default: {DEFAULT_REPORTER_DIR})")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                        help="Copy at most N new pairs (newest first)")
    parser.add_argument("--days", type=int, default=30,
                        help="prune: delete archived copies older than N days")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.action == "status":
        result = archive_status(args.audio_dir, args.reporter_dir)
    elif args.action == "prune":
        result = prune_archive(args.reporter_dir, days=args.days, dry_run=args.dry_run)
    else:
        result = sync_archive(args.audio_dir, args.reporter_dir,
                              dry_run=args.dry_run, limit=args.limit)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if args.action == "status":
        print(f"Harmony audio:   {result['audio_dir']}")
        print(f"Reporter archive: {result['archive_dir']}")
        print(f"Pairs at source:  {result['harmony_pairs']}")
        print(f"Archived pairs:   {result['manifest_entries']} (manifest), "
              f"{result['archived_wavs']} wav files")
        print(f"Pending:          {result['pending']}")
        print(f"Last sync:        {result['last_sync']}")
    elif args.action == "prune":
        verb = "would remove" if result["dry_run"] else "removed"
        print(f"Prune: {verb} {result['removed']}, kept {result['kept']}")
    else:
        verb = "would copy" if result["dry_run"] else "copied"
        print(f"Harmony archive: {verb} {result['copied']} new pair(s); "
              f"{result['already_archived']} already archived, "
              f"{result['failed']} failed")
        print(f"Destination: {result['archive_dir']}")
    return 0 if result.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
