#!/usr/bin/env python3
"""Fast sidecar cache for the generated DJ_Tracks_*.json playlists.

A combined music playlist for a large library is tens of megabytes of JSON.
json.loads on that takes most of a second, and the MP Audio Player used to pay
it every time a window opened. This module stores the same rows in a flat,
separator-delimited form beside the source file. Decoding it is several times
faster than JSON and needs no third-party package.

The sidecar is derived data. It is validated against the source file's size and
mtime, and any problem (missing, truncated, older version, unreadable) makes the
reader return None so the caller falls back to the JSON itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

CACHE_SUFFIX = ".mpcache"
CACHE_VERSION = 1
CACHE_FIELDS = ("name", "title", "path", "folder", "subpath", "ext", "size_mb", "duration_fmt", "mtime")
FIELD_SEP, ROW_SEP = "\x1f", "\x00"


def cache_path(playlist_path: Union[Path, str]) -> Path:
    p = Path(playlist_path)
    return p.with_name(p.name + CACHE_SUFFIX)


def signature(playlist_path: Path) -> Tuple[int, int]:
    """Size and modification time used to prove a sidecar matches its playlist.

    Nanosecond mtime, because a regenerated playlist can land in the same second
    with the same byte size and must still invalidate the old cache.
    """
    try:
        st = playlist_path.stat()
        return int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
    except OSError:
        return -1, -1


def _decode(blob: bytes) -> Optional[List[Dict[str, Any]]]:
    field_sep = FIELD_SEP.encode("utf-8")
    rows: List[Dict[str, Any]] = []
    append = rows.append
    try:
        for record in blob.split(ROW_SEP.encode("utf-8")):
            if not record:
                continue
            fields = record.split(field_sep)
            if len(fields) != len(CACHE_FIELDS):
                return None
            row = {name: value.decode("utf-8") for name, value in zip(CACHE_FIELDS, fields)}
            row["size_mb"] = float(row.get("size_mb") or 0.0)
            row["mtime"] = float(row.get("mtime") or 0.0)
            append(row)
    except (UnicodeDecodeError, ValueError):
        return None
    return rows


def read(playlist_path: Union[Path, str]) -> Optional[List[Dict[str, Any]]]:
    """Decode the sidecar for this playlist, or None when it cannot be trusted."""
    p = Path(playlist_path)
    size, mtime = signature(p)
    if size < 0:
        return None
    try:
        blob = cache_path(p).read_bytes()
    except OSError:
        return None
    header, _, body = blob.partition(b"\n")
    if header != f"{CACHE_VERSION}\t{size}\t{mtime}".encode("utf-8"):
        return None
    return _decode(body)


def write(playlist_path: Union[Path, str], rows: Sequence[Dict[str, Any]]) -> bool:
    """Write the sidecar beside the playlist. Failures are never fatal."""
    p = Path(playlist_path)
    size, mtime = signature(p)
    if size < 0:
        return False
    target = cache_path(p)
    sep, row_sep = FIELD_SEP.encode("utf-8"), ROW_SEP.encode("utf-8")
    try:
        body = row_sep.join(
            sep.join(str(row.get(name, "")).encode("utf-8") for name in CACHE_FIELDS)
            for row in rows
        )
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as fh:
            fh.write(f"{CACHE_VERSION}\t{size}\t{mtime}\n".encode("utf-8"))
            fh.write(body)
        tmp.replace(target)
        return True
    except Exception:
        drop(p)
        return False


def drop(playlist_path: Union[Path, str]) -> None:
    """Remove a stale sidecar. Used when the playlist JSON is rewritten."""
    try:
        cache_path(playlist_path).unlink(missing_ok=True)
    except OSError:
        pass


def load_json(playlist_path: Union[Path, str]) -> Optional[List[Dict[str, Any]]]:
    """Read a playlist .json, preferring the sidecar.

    Returned rows always carry ``duration_fmt``. A cache miss parses the JSON and
    refreshes the sidecar so the next launch is fast.
    """
    p = Path(playlist_path)
    rows = read(p)
    if rows is None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        for row in data:
            row.setdefault("duration_fmt", "00:00")
        write(p, data)
        return data
    for row in rows:
        row.setdefault("duration_fmt", "00:00")
    return rows


def refresh(playlist_path: Union[Path, str]) -> bool:
    """Rebuild the sidecar from the JSON without returning the rows."""
    return load_json(playlist_path) is not None
