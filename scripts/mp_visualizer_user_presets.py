#!/usr/bin/env python3
"""User-generated MP Visualizer presets.

The MP Visualizer ships thirteen hand-authored presets inside
``shaders/mp_liquid_visualizer.frag``.  The MP Visualisation Generator writes
*new* presets as self-contained shader packages (Vulkan SPIR-V, OpenGL GLSL and
Metal MSL) into the user's config directory.  This module is the small, shared
registry both the generator and the visualizer use:

    ~/.config/mix-manager/visualizer-presets/
        registry.json
        <preset-key>/
            preset.json
            cover.png
            preset.frag              (Vulkan GLSL, source of truth)
            preset.frag.spv          (Vulkan SPIR-V)
            preset.opengl.frag       (GLSL 330, used by the OpenGL backend)
            preset.metal             (Metal Shading Language)
            preview.png
            export.mpvispreset       (created on demand)

Nothing here imports PIL, GTK or Vulkan.  The visualizers import this module at
startup, so it must stay cheap and must never fail because an optional package
is missing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONFIG_DIR = Path.home() / ".config" / "mix-manager"
USER_PRESET_ROOT = CONFIG_DIR / "visualizer-presets"
REGISTRY_FILE = USER_PRESET_ROOT / "registry.json"
BASE_ID = 1000
PACKAGE_EXT = ".mpvispreset"
GENERATOR_VERSION = "1.0"


# ---------------------------------------------------------------------------
# registry primitives
# ---------------------------------------------------------------------------
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def key_from_name(name: str) -> str:
    """Filesystem- and CLI-safe preset key, always prefixed with ``user_``."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    if not slug:
        slug = "generated_preset"
    if len(slug) > 64:
        slug = slug[:64].rstrip("_")
    return slug if slug.startswith("user_") else f"user_{slug}"


def _ensure_root() -> None:
    USER_PRESET_ROOT.mkdir(parents=True, exist_ok=True)


def load_registry() -> Dict[str, Any]:
    _ensure_root()
    try:
        raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    presets = raw.get("presets")
    if not isinstance(presets, list):
        presets = []
    return {"version": 1, "presets": presets}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_registry(registry: Dict[str, Any]) -> None:
    _ensure_root()
    data = {"version": 1, "updated": _now(), "presets": list(registry.get("presets") or [])}
    _atomic_write(REGISTRY_FILE, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def next_preset_id(registry: Optional[Dict[str, Any]] = None) -> int:
    registry = registry if registry is not None else load_registry()
    used = {
        int(p.get("id", 0))
        for p in registry.get("presets", [])
        if isinstance(p, dict) and str(p.get("id", "")).isdigit()
    }
    return max([BASE_ID - 1, *used]) + 1


# ---------------------------------------------------------------------------
# preset records
# ---------------------------------------------------------------------------
def preset_directory(key: str) -> Path:
    return USER_PRESET_ROOT / str(key)


def _read_preset_json(directory: Path) -> Dict[str, Any]:
    try:
        data = json.loads((directory / "preset.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}


def _entry_to_preset(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn one registry entry into the dict the visualizer expects."""
    key = str(entry.get("key") or "")
    if not key:
        return None
    directory = preset_directory(key)
    if not directory.is_dir():
        return None
    meta = _read_preset_json(directory)
    if not meta:
        meta = dict(entry)
    cover = entry.get("cover") or meta.get("cover") or "cover.png"
    cover_path = directory / str(cover)
    cover_paths = [cover_path]
    # A package imported from an older install may name the artwork
    # differently; fall back to the first image in the folder.
    if not cover_path.is_file():
        for candidate in sorted(directory.glob("cover.*")):
            if candidate.is_file():
                cover_paths = [candidate]
                break
    try:
        preset_id = int(entry.get("id") or meta.get("id") or 0)
    except (TypeError, ValueError):
        preset_id = 0
    if preset_id < BASE_ID:
        preset_id = BASE_ID
    name = str(entry.get("name") or meta.get("name") or key)
    album = str(entry.get("album") or meta.get("album") or f"User Generated — {name}")
    preset: Dict[str, Any] = {
        "id": preset_id,
        "key": key,
        "name": name,
        "album": album,
        "bandcamp_url": str(meta.get("bandcamp_url") or ""),
        "cover_paths": cover_paths,
        "user_preset": True,
        "preset_dir": str(directory),
        "opengl_source": str(directory / "preset.opengl.frag"),
        "metal_source": str(directory / "preset.metal"),
        "vulkan_source": str(directory / "preset.frag"),
        "vulkan_spv": str(directory / "preset.frag.spv"),
        "style": meta.get("style", "Hybrid"),
        "generator_version": meta.get("generator_version", GENERATOR_VERSION),
        "installed": entry.get("installed") or meta.get("installed") or "",
        "_aliases": tuple(
            str(a).lower()
            for a in (meta.get("aliases") or entry.get("aliases") or [])
            if str(a).strip()
        ),
    }
    return preset


def load_user_presets() -> List[Dict[str, Any]]:
    """Every installed user preset, newest last."""
    registry = load_registry()
    presets: List[Dict[str, Any]] = []
    seen = set()
    for entry in registry.get("presets", []):
        if not isinstance(entry, dict):
            continue
        preset = _entry_to_preset(entry)
        if preset is None or preset["key"] in seen:
            continue
        seen.add(preset["key"])
        presets.append(preset)
    presets.sort(key=lambda p: (str(p.get("installed") or ""), p["id"]))
    return presets


def find_user_preset(key_or_id: Any) -> Optional[Dict[str, Any]]:
    target = str(key_or_id or "").strip().lower()
    if not target:
        return None
    for preset in load_user_presets():
        if target == str(preset["key"]).lower() or target == str(preset["id"]):
            return preset
        if target == str(preset["name"]).lower():
            return preset
        aliases = {str(a).lower() for a in preset.get("_aliases") or ()}
        if target in aliases:
            return preset
    return None


def _upsert_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    registry = load_registry()
    presets = registry.setdefault("presets", [])
    key = entry["key"]
    for index, existing in enumerate(presets):
        if isinstance(existing, dict) and existing.get("key") == key:
            presets[index] = {**existing, **entry}
            save_registry(registry)
            return entry
    presets.append(entry)
    save_registry(registry)
    return entry


def register_preset(directory: Path, meta: Dict[str, Any], *, activate: bool = False) -> Dict[str, Any]:
    """Register a preset directory that already contains its shader files."""
    directory = Path(directory)
    key = str(meta.get("key") or directory.name)
    if not key.startswith("user_"):
        key = key_from_name(key)
    registry = load_registry()
    existing = next(
        (p for p in registry.get("presets", [])
         if isinstance(p, dict) and p.get("key") == key),
        None,
    )
    entry: Dict[str, Any] = {
        "id": int(meta.get("id") or (existing or {}).get("id") or next_preset_id(registry)),
        "key": key,
        "name": str(meta.get("name") or key),
        "album": str(meta.get("album") or f"User Generated — {meta.get('name') or key}"),
        "cover": str(meta.get("cover") or "cover.png"),
        "style": str(meta.get("style") or "Hybrid"),
        "generator_version": str(meta.get("generator_version") or GENERATOR_VERSION),
        "installed": str(meta.get("installed") or (existing or {}).get("installed") or _now()),
        "aliases": list(meta.get("aliases") or (existing or {}).get("aliases") or []),
    }
    meta = {**meta, **entry}
    _atomic_write(directory / "preset.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    _upsert_entry(entry)
    if activate:
        set_active_key(key)
    return entry


def set_active_key(key: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(CONFIG_DIR / "visualizer_preset", str(key).strip() + "\n")


def active_key() -> str:
    try:
        return (CONFIG_DIR / "visualizer_preset").read_text(encoding="utf-8").strip()
    except OSError:
        return "cosmic_dreamworlds_liquid"


def remove_preset(key: str) -> bool:
    registry = load_registry()
    presets = [
        p for p in registry.get("presets", [])
        if not (isinstance(p, dict) and p.get("key") == key)
    ]
    if len(presets) == len(registry.get("presets", [])):
        return False
    registry["presets"] = presets
    save_registry(registry)
    shutil.rmtree(preset_directory(key), ignore_errors=True)
    if active_key() == key:
        set_active_key("cosmic_dreamworlds_liquid")
    return True


# ---------------------------------------------------------------------------
# backend lookups used by the patched visualizer backends
# ---------------------------------------------------------------------------
def _preset_from_arg(preset_or_id: Any) -> Optional[Dict[str, Any]]:
    if isinstance(preset_or_id, dict):
        if preset_or_id.get("user_preset"):
            return preset_or_id
        if int(preset_or_id.get("id", 0) or 0) >= BASE_ID:
            return find_user_preset(preset_or_id.get("id"))
        return None
    return find_user_preset(preset_or_id)


def vulkan_spirv_for(preset_or_id: Any) -> Optional[Path]:
    preset = _preset_from_arg(preset_or_id)
    if not preset:
        return None
    path = Path(str(preset.get("vulkan_spv") or ""))
    return path if path.is_file() and path.stat().st_size > 16 else None


def vulkan_source_for(preset_or_id: Any) -> Optional[Path]:
    preset = _preset_from_arg(preset_or_id)
    if not preset:
        return None
    path = Path(str(preset.get("vulkan_source") or ""))
    return path if path.is_file() else None


def opengl_source_for(preset_or_id: Any) -> Optional[str]:
    preset = _preset_from_arg(preset_or_id)
    if not preset:
        return None
    path = Path(str(preset.get("opengl_source") or ""))
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def metal_source_for(preset_or_id: Any) -> Optional[str]:
    preset = _preset_from_arg(preset_or_id)
    if not preset:
        return None
    path = Path(str(preset.get("metal_source") or ""))
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# export / import packages
# ---------------------------------------------------------------------------
_PACKAGE_FILES = (
    "preset.json",
    "cover.png",
    "preset.frag",
    "preset.frag.spv",
    "preset.opengl.frag",
    "preset.metal",
    "preview.png",
    "palette.json",
)


def export_package(key: str, destination: Path) -> Path:
    """Write a portable ``.mpvispreset`` zip for one installed preset."""
    preset = find_user_preset(key)
    if preset is None:
        raise FileNotFoundError(f"No installed user preset named {key!r}")
    directory = Path(str(preset["preset_dir"]))
    destination = Path(destination).expanduser()
    if destination.suffix.lower() != PACKAGE_EXT:
        destination = destination.with_suffix(PACKAGE_EXT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    meta = _read_preset_json(directory)
    meta = {
        **meta,
        "id": preset["id"],
        "key": preset["key"],
        "name": preset["name"],
        "album": preset["album"],
        "style": preset.get("style", "Hybrid"),
        "generator_version": preset.get("generator_version", GENERATOR_VERSION),
        "package_version": 1,
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("preset.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        for name in _PACKAGE_FILES:
            source = directory / name
            if source.is_file() and name != "preset.json":
                package.write(source, arcname=name)
    return destination


def import_package(package_path: Path, *, activate: bool = False) -> Dict[str, Any]:
    """Install a ``.mpvispreset`` zip, renaming on collision."""
    package_path = Path(package_path).expanduser()
    if not package_path.is_file():
        raise FileNotFoundError(str(package_path))
    with tempfile.TemporaryDirectory(prefix="mpvis-import-") as temp:
        staging = Path(temp)
        with zipfile.ZipFile(package_path, "r") as package:
            for member in package.infolist():
                name = member.filename
                if not name or name.startswith("/") or ".." in Path(name).parts:
                    continue
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member, "r") as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        meta = _read_preset_json(staging)
        if not meta:
            raise ValueError("The package has no preset.json")
        base_key = key_from_name(str(meta.get("key") or meta.get("name") or "imported_preset"))
        key = base_key
        counter = 2
        while preset_directory(key).exists():
            key = f"{base_key}_{counter}"
            counter += 1
        meta["key"] = key
        if not (staging / str(meta.get("cover") or "cover.png")).is_file():
            for candidate in sorted(staging.glob("cover.*")):
                meta["cover"] = candidate.name
                break
        if not (staging / "preset.metal").is_file():
            meta["metal_available"] = False
        destination = preset_directory(key)
        destination.mkdir(parents=True, exist_ok=True)
        for item in staging.iterdir():
            if item.is_file():
                shutil.copy2(item, destination / item.name)
        meta["installed"] = _now()
        meta.pop("id", None)  # a new install gets a fresh id
        return register_preset(destination, meta, activate=activate)


# ---------------------------------------------------------------------------
# tiny CLI used by Mix_Archive_Manager.sh and the generator
# ---------------------------------------------------------------------------
def _cmd_list() -> int:
    for preset in load_user_presets():
        print(f"{preset['id']}\t{preset['key']}\t{preset['name']}")
    return 0


def _cmd_list_menu() -> int:
    for preset in load_user_presets():
        print(f"{preset['key']}|{preset['name']}|{preset.get('album', '')}")
    return 0


def _cmd_label(key: str) -> int:
    preset = find_user_preset(key)
    print(preset["name"] if preset else "")
    return 0 if preset else 1


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__ or "")
        return 0
    command = args[0]
    if command in {"--list", "list"}:
        return _cmd_list()
    if command in {"--list-menu", "list-menu"}:
        return _cmd_list_menu()
    if command in {"--label", "label"} and len(args) >= 2:
        return _cmd_label(args[1])
    if command in {"--set-active", "set-active"} and len(args) >= 2:
        if find_user_preset(args[1]) is None:
            print(f"Unknown user preset: {args[1]}", file=sys.stderr)
            return 1
        set_active_key(args[1])
        return 0
    if command in {"--export", "export"} and len(args) >= 3:
        print(export_package(args[1], Path(args[2])))
        return 0
    if command in {"--import", "import"} and len(args) >= 2:
        entry = import_package(Path(args[1]))
        print(f"{entry['id']}\t{entry['key']}\t{entry['name']}")
        return 0
    if command in {"--remove", "remove"} and len(args) >= 2:
        return 0 if remove_preset(args[1]) else 1
    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
