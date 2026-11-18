#!/usr/bin/env python3
"""Read and write every playlist file type the MP Audio Player understands.

The player used to write only ``.m3u`` and read only the generated
``DJ_Tracks_*.json`` / ``.m3u8`` pairs. A playlist the user made somewhere else
(``.pls`` from another player, an ``.xspf`` exported by VLC, a ``.m3u8`` from a
phone) could not be opened, and an export was always one format.

This module is the single implementation both directions use:

  * ``read_playlist(path)`` returns player rows (``name``, ``title``, ``path``,
    ``ext``, ``size_mb``, ``duration_fmt``, ``mtime``) whatever the container.
  * ``write_playlist(path, tracks)`` chooses the container from the file
    extension, so a user who types ``set.xspf`` gets real XSPF rather than an
    M3U body with the wrong suffix.

Supported containers: ``.m3u``, ``.m3u8``, ``.xspf``, ``.pls`` and ``.json``.
Relative entries are resolved against the playlist's own folder, ``file://``
locations are decoded (``%20`` and friends), and remote URLs are kept in the
row but cannot be played by the local engine, so callers that need a file on
disk filter them with ``is_local_audio``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
from xml.sax.saxutils import escape as xml_escape

PLAYLIST_EXTENSIONS: Tuple[str, ...] = (".m3u", ".m3u8", ".xspf", ".pls", ".json")
PLAYLIST_EXTENSION_SET = frozenset(PLAYLIST_EXTENSIONS)

FORMAT_LABELS: Dict[str, str] = {
    ".m3u": "M3U",
    ".m3u8": "M3U8 (UTF-8)",
    ".xspf": "XSPF (XML Shareable Playlist)",
    ".pls": "PLS",
    ".json": "JSON (MP Mix Manager)",
}

# Ordered exactly as the CLI export window presents them (keys 1-5).
FORMAT_ORDER: Tuple[str, ...] = (".m3u", ".m3u8", ".xspf", ".pls", ".json")

_XSPF_NS = "http://xspf.org/ns/0/"


def extension_of(path: Union[str, os.PathLike, None]) -> str:
    try:
        return os.path.splitext(str(path))[1].lower()
    except (TypeError, ValueError):
        return ""


def is_playlist_path(path: Union[str, os.PathLike, None]) -> bool:
    return extension_of(path) in PLAYLIST_EXTENSION_SET


def format_for(path: Union[str, os.PathLike, None], fmt: Optional[str] = None) -> str:
    """Resolve the container to use: an explicit format first, then the suffix."""
    wanted = str(fmt or "").strip().lower()
    if wanted and not wanted.startswith("."):
        wanted = "." + wanted
    if wanted in PLAYLIST_EXTENSION_SET:
        return wanted
    ext = extension_of(path)
    return ext if ext in PLAYLIST_EXTENSION_SET else ".m3u8"


def target_path(path: Union[str, os.PathLike, None], fmt: Optional[str] = None) -> Path:
    """Return the destination with a supported playlist suffix.

    An explicit format wins; otherwise a supported suffix on the typed path is
    kept as-is (so ``mix.pls`` writes PLS even when the default is M3U8).
    """
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("Destination path cannot be empty")
    p = Path(os.path.expanduser(raw))
    chosen = format_for(p, fmt)
    if extension_of(p) != chosen or not is_playlist_path(p):
        p = p.with_suffix(chosen)
    return p


def _duration_fmt(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _seconds_from_fmt(text: str) -> float:
    text = str(text or "").strip()
    if not text:
        return 0.0
    try:
        if ":" in text:
            parts = [float(p) for p in text.split(":")]
            value = 0.0
            for part in parts:
                value = value * 60.0 + part
            return max(0.0, value)
        return max(0.0, float(text))
    except (TypeError, ValueError):
        return 0.0


def _unquote_location(location: str) -> str:
    text = str(location or "").strip().strip("\x00")
    if not text:
        return ""
    low = text.lower()
    if low.startswith("file:"):
        parsed = urllib.parse.urlparse(text)
        path = urllib.parse.unquote(parsed.path or "")
        # file:///C:/Music -> C:/Music on Windows.
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path
    return urllib.parse.unquote(text)


def _is_remote(text: str) -> bool:
    return str(text or "").lower().startswith(("http://", "https://", "rtsp://", "mms://"))


def _resolve_entry(entry: str, base: Path) -> str:
    text = _unquote_location(entry)
    if not text or _is_remote(text):
        return text
    text = os.path.expanduser(text)
    if not os.path.isabs(text) and base:
        text = os.path.join(str(base), text)
    try:
        text = os.path.normpath(text)
    except (TypeError, ValueError):
        pass
    return text


def is_local_audio(row: Dict[str, Any]) -> bool:
    path = str((row or {}).get("path") or "")
    return bool(path) and not _is_remote(path) and os.path.isfile(path)


def _row_for(path: str, title: str = "", seconds: float = 0.0) -> Dict[str, Any]:
    name = os.path.basename(path) or path
    ext = extension_of(path).lstrip(".").upper() or "AUDIO"
    try:
        stat = os.stat(path)
        size_mb = stat.st_size / 1048576.0
        mtime = stat.st_mtime
    except OSError:
        size_mb, mtime = 0.0, time.time()
    folder = os.path.basename(os.path.dirname(path)) if os.path.dirname(path) else ""
    return {
        "name": name,
        "title": str(title or os.path.splitext(name)[0]),
        "path": path,
        "folder": folder.upper(),
        "ext": ext,
        "size_mb": size_mb,
        "duration_fmt": _duration_fmt(seconds),
        "duration_sec": float(seconds or 0.0),
        "mtime": mtime,
    }


def _normalise_row(raw: Dict[str, Any], base: Path) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    path = str(raw.get("path") or raw.get("file") or raw.get("location") or "").strip()
    if not path:
        return None
    path = _resolve_entry(path, base)
    seconds = float(raw.get("duration_sec") or 0.0)
    if not seconds:
        seconds = _seconds_from_fmt(str(raw.get("duration_fmt") or ""))
    row = _row_for(path, str(raw.get("title") or raw.get("name") or ""), seconds)
    # Keep a supplied display name when the file is missing (size 0 path).
    for field in ("name", "title", "folder", "ext", "size_mb", "mtime"):
        if raw.get(field) not in (None, ""):
            value = raw[field]
            if field == "size_mb":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            elif field == "mtime":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            elif field == "ext":
                value = str(value).upper()
            elif field == "folder":
                value = str(value).upper()
            else:
                value = str(value)
            row[field] = value
    if raw.get("duration_fmt"):
        row["duration_fmt"] = str(raw["duration_fmt"])
    return row


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
def _read_m3u(text: str, base: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    title, seconds = "", 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            payload = line[8:]
            if "," in payload:
                dur, title = payload.split(",", 1)
                seconds = _seconds_from_fmt(dur)
            else:
                title, seconds = payload, 0.0
            continue
        if line.startswith("#"):
            continue
        path = _resolve_entry(line, base)
        if path:
            rows.append(_row_for(path, title, seconds))
        title, seconds = "", 0.0
    return rows


def _read_pls(text: str, base: Path) -> List[Dict[str, Any]]:
    entries: Dict[int, Dict[str, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        for prefix in ("file", "title", "length"):
            if key.startswith(prefix) and key[len(prefix):].isdigit():
                index = int(key[len(prefix):])
                entries.setdefault(index, {})[prefix] = value
                break
    rows: List[Dict[str, Any]] = []
    for index in sorted(entries):
        item = entries[index]
        path = _resolve_entry(item.get("file", ""), base)
        if not path:
            continue
        seconds = 0.0
        try:
            seconds = max(0.0, float(item.get("length", 0) or 0))
        except ValueError:
            pass
        rows.append(_row_for(path, item.get("title", ""), seconds))
    return rows


def _read_xspf(text: str, base: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return rows

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    for node in root.iter():
        if local(node.tag) != "track":
            continue
        location, title, seconds = "", "", 0.0
        for child in list(node):
            name = local(child.tag)
            value = (child.text or "").strip()
            if name == "location":
                location = value
            elif name == "title":
                title = value
            elif name == "duration" and value:
                try:
                    seconds = max(0.0, float(value) / 1000.0)
                except ValueError:
                    seconds = 0.0
        path = _resolve_entry(location, base)
        if path:
            rows.append(_row_for(path, title, seconds))
    return rows


def _read_json(text: str, base: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("tracks") or data.get("items") or []
    if not isinstance(data, list):
        return []
    rows: List[Dict[str, Any]] = []
    for raw in data:
        row = _normalise_row(raw, base)
        if row:
            rows.append(row)
    return rows


def read_playlist(path: Union[str, os.PathLike]) -> List[Dict[str, Any]]:
    """Read a playlist of any supported type into player rows.

    Raises ``OSError`` when the file cannot be read and ``ValueError`` when the
    container is not one of the supported playlist types. An empty list means
    the file was readable but named no tracks.
    """
    p = Path(str(path)).expanduser()
    ext = extension_of(p)
    if ext not in PLAYLIST_EXTENSION_SET:
        raise ValueError(f"Unsupported playlist type: {ext or '(none)'}")
    text = p.read_text(encoding="utf-8", errors="replace")
    base = p.parent
    if ext in (".m3u", ".m3u8"):
        return _read_m3u(text, base)
    if ext == ".pls":
        return _read_pls(text, base)
    if ext == ".xspf":
        return _read_xspf(text, base)
    return _read_json(text, base)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def _clean_title(value: Any, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _row_path(row: Dict[str, Any]) -> str:
    return str((row or {}).get("path") or "").strip()


def _row_title(row: Dict[str, Any]) -> str:
    path = _row_path(row)
    return _clean_title((row or {}).get("title") or (row or {}).get("name"), os.path.splitext(os.path.basename(path))[0])


def _row_seconds(row: Dict[str, Any]) -> float:
    try:
        return max(0.0, float((row or {}).get("duration_sec") or 0.0))
    except (TypeError, ValueError):
        return _seconds_from_fmt(str((row or {}).get("duration_fmt") or ""))


def _write_m3u(p: Path, tracks: Sequence[Dict[str, Any]], utf8_tag: bool) -> None:
    lines = ["#EXTM3U"]
    if utf8_tag:
        lines.append("#EXTENC:UTF-8")
    for row in tracks:
        path = _row_path(row)
        if not path:
            continue
        seconds = _row_seconds(row)
        lines.append(f"#EXTINF:{int(seconds) if seconds > 0 else -1},{_row_title(row)}")
        lines.append(path)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_xspf(p: Path, tracks: Sequence[Dict[str, Any]]) -> None:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<playlist version="1" xmlns="{_XSPF_NS}">',
        "  <title>MP Audio Player Playlist</title>",
        "  <creator>MP Mix Manager</creator>",
        "  <trackList>",
    ]
    for row in tracks:
        path = _row_path(row)
        if not path:
            continue
        if _is_remote(path):
            location = path
        else:
            try:
                location = "file://" + urllib.parse.quote(os.path.abspath(path))
            except (OSError, ValueError):
                location = "file://" + urllib.parse.quote(path)
        out.append("    <track>")
        out.append(f"      <location>{xml_escape(location)}</location>")
        out.append(f"      <title>{xml_escape(_row_title(row))}</title>")
        seconds = _row_seconds(row)
        if seconds > 0:
            out.append(f"      <duration>{int(round(seconds * 1000))}</duration>")
        out.append("    </track>")
    out += ["  </trackList>", "</playlist>"]
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def _write_pls(p: Path, tracks: Sequence[Dict[str, Any]]) -> None:
    usable = [row for row in tracks if _row_path(row)]
    out = ["[playlist]", f"NumberOfEntries={len(usable)}"]
    for index, row in enumerate(usable, start=1):
        out.append(f"File{index}={_row_path(row)}")
        out.append(f"Title{index}={_row_title(row)}")
        seconds = _row_seconds(row)
        out.append(f"Length{index}={int(seconds) if seconds > 0 else -1}")
    out.append("Version=2")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def _write_json(p: Path, tracks: Sequence[Dict[str, Any]]) -> None:
    rows: List[Dict[str, Any]] = []
    for row in tracks:
        path = _row_path(row)
        if not path:
            continue
        rows.append({
            "name": os.path.basename(path) or path,
            "title": _row_title(row),
            "path": path,
            "folder": str((row or {}).get("folder") or "").upper(),
            "ext": (extension_of(path).lstrip(".").upper() or str((row or {}).get("ext") or "AUDIO")).upper(),
            "size_mb": float((row or {}).get("size_mb") or 0.0),
            "duration_fmt": str((row or {}).get("duration_fmt") or _duration_fmt(_row_seconds(row))),
            "mtime": float((row or {}).get("mtime") or time.time()),
        })
    p.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_playlist(path: Union[str, os.PathLike], tracks: Iterable[Dict[str, Any]],
                   fmt: Optional[str] = None) -> Path:
    """Write ``tracks`` in the requested container and return the real path.

    The destination folder is created when it does not exist yet. The format
    comes from ``fmt`` when given, otherwise from the destination suffix; an
    unsupported suffix is replaced with ``.m3u8``.
    """
    rows = [dict(row) for row in (tracks or []) if isinstance(row, dict)]
    p = target_path(path, fmt)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = format_for(p, fmt)
    if ext == ".m3u":
        _write_m3u(p, rows, utf8_tag=False)
    elif ext == ".m3u8":
        _write_m3u(p, rows, utf8_tag=True)
    elif ext == ".xspf":
        _write_xspf(p, rows)
    elif ext == ".pls":
        _write_pls(p, rows)
    else:
        _write_json(p, rows)
    return p


def supported_filter_zones() -> Dict[str, str]:
    """The same extension list, formatted for kdialog/zenity/win dialogs."""
    globs = " ".join(f"*{ext}" for ext in PLAYLIST_EXTENSIONS)
    return {
        "kdialog": f"{globs} | Playlist Files ({' '.join(PLAYLIST_EXTENSIONS)})",
        "zenity": f"Playlist Files | {globs}",
        "windows": "Playlist Files|" + ";".join(f"*{ext}" for ext in PLAYLIST_EXTENSIONS) + "|All Files (*.*)|*.*",
        "macos": ", ".join(ext.lstrip(".") for ext in PLAYLIST_EXTENSIONS),
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    print("Supported playlist types:")
    for ext in PLAYLIST_EXTENSIONS:
        print(f"  {ext:<6} {FORMAT_LABELS.get(ext, '')}")
