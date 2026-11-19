#!/usr/bin/env python3
"""Reset or uninstall a subset of the four MP applications.

Reset clears that app's saved window and playback settings and leaves it
installed. Uninstall removes its shortcuts. Saved settings are deleted only
when asked, so a later install can come back with them.

The install tree, music, mixes, playlists, favorites, backups and converted
audio are never deleted.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mp_app_selection as selection  # noqa: E402
from install_desktop_shortcuts import (  # noqa: E402
    SHORTCUT_NAMES,
    _remove_deselected,
    apps_dir_for,
    desktop_dir_for,
    detect_platform,
    selected_names,
)

# Files that belong to one app. Shared files are listed under the globs below
# and are removed only when every app that uses them is being cleared.
APP_STATE = {
    "manager": (
        "header_cache.env",
        ".header_refresh_stamp",
    ),
    "gui": (
        "liquid_gui_window.json",
        "liquid_glass.log",
        "liquid_glass_tracklist.log",
        ".liquid_glass_welcomed",
    ),
    "liquid": (
        "liquid_player_window.json",
        "bass_boost",
        "eq_preset",
        "eq_style",
        "eq_active_bands.json",
        "custom_eq_presets.json",
        ".first_run_eq_initialized",
        ".first_run_sample_loaded",
    ),
    "visualizer": (
        "liquid_player_visualizer.json",
    ),
}

# Glob pattern -> the apps that share those files.
SHARED_GLOBS = {
    "liquid_ui_size.*": ("gui", "liquid"),
    "audio_volume.cache*": ("liquid",),
    # MP Black Glass panes and the visualizers they attach publish one small
    # record per live process. They belong to no single app: any of the four
    # windows can open a pane, so they go with the last one uninstalled.
    "black_glass": ("gui", "liquid", "visualizer"),
    "black_glass_window.*.json": ("gui", "liquid", "visualizer"),
    "black_glass_visualizer.*.json": ("gui", "liquid", "visualizer"),
    "black_glass_visualizer_state.*.json": ("gui", "liquid", "visualizer"),
}

# Removed from the config directory only when every app is uninstalled and the
# user asked to delete settings. The copy in the install tree is never touched.
FULL_UNINSTALL_SETTINGS = ("config.env",)

# User data. A "delete settings" answer does not mean delete the library.
NEVER_DELETE = {
    "music_folders.json",
    "favorites.json",
    "favorites.m3u8",
    "top_5_tracks.json",
    "installed_apps",
    "FLAC_CONVERTED_OUTPUTS",
    "MP3_CONVERTED_OUTPUTS",
    "WAV_CONVERTED_OUTPUTS",
    "MP4_CONVERTED_OUTPUTS",
    "CONVERTED_WAV_FILES",
    "MIX_ARCHIVE",
    "COVERS",
    "SPEK_OUTPUTS",
    "MP_AUDIOWAV_REC_OUTPUT",
    "TOP_5_TRACKS",
    "backups",
    "exports",
    "mounts",
    "playlists",
    "PLAYLISTS_GENERATED",
    "lib",
}

TRAY_PATHS = (
    Path(".config") / "autostart" / "mp-mix-manager-tray.desktop",
    Path(".local") / "share" / "applications" / "mp-mix-manager-tray.desktop",
    Path("Library") / "LaunchAgents" / "com.mplanetarian.mp-mix-manager-tray.plist",
    Path("AppData") / "Roaming" / "Microsoft" / "Windows" / "Start Menu"
    / "Programs" / "Startup" / "MP Mix Manager Tray.lnk",
)


def parse_targets(text: str) -> list[str]:
    """The apps named by the uninstaller. An unknown word is an error.

    ``all`` is every app. Anything else must be a real spelling of one or more
    apps. A typo must not silently select all four.
    """
    raw = str(text).strip().lower()
    if raw in ("all", "everything", "full"):
        return list(selection.COMPONENTS)
    if not raw:
        raise SystemExit("Choose at least one app: all, manager, gui, liquid, visualizer")
    found: set[str] = set()
    for token in raw.replace(",", " ").replace(";", " ").split():
        ident = selection.identify(token)
        if ident is None:
            raise SystemExit(
                f"Unknown app {token!r}. Use all, manager, gui, liquid, or visualizer."
            )
        found.add(ident)
    if not found:
        raise SystemExit("Choose at least one app.")
    return [ident for ident in selection.COMPONENTS if ident in found]


def _inside(path: Path, root: Path) -> bool:
    """True when `path` is `root` or a file inside it.

    A symlink is judged by where the link itself sits, not by the file it
    points at, so a link out of the config directory is unlinked and the
    target is left alone.
    """
    try:
        root_r = root.resolve()
        parent = path.parent.resolve()
        parent.relative_to(root_r)
    except (OSError, ValueError):
        return False
    if path.is_symlink():
        return True
    try:
        path.resolve().relative_to(root_r)
        return True
    except (OSError, ValueError):
        return False


def _remove(path: Path, root: Path, dry_run: bool, label: str) -> None:
    # This interpreter's pathlib has no Path.lexists. os.path.lexists sees a
    # dangling symlink and treats a missing path as absent.
    if not os.path.lexists(path):
        return
    if path.name in NEVER_DELETE:
        print(f"Kept {path.name}")
        return
    if not _inside(path, root):
        print(f"Refused {path}")
        return
    if dry_run:
        print(f"Would remove {label}: {path}")
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return
    except OSError as exc:
        print(f"Could not remove {path}: {exc}")
        return
    print(f"Removed {label}: {path}")


def _survivors(installed: list[str], clearing: list[str]) -> set[str]:
    drop = set(clearing)
    return {ident for ident in installed if ident not in drop}


def state_paths(config_dir: Path, clearing: list[str], installed: list[str]) -> list[Path]:
    """Settings files to delete for `clearing`, leaving files other apps use."""
    paths: list[Path] = []
    for ident in clearing:
        for name in APP_STATE.get(ident, ()):
            paths.append(config_dir / name)
    staying = _survivors(installed, clearing)
    for pattern, owners in SHARED_GLOBS.items():
        if not set(owners) & set(clearing):
            continue
        if set(owners) & staying:
            continue
        paths.extend(sorted(config_dir.glob(pattern)))
    return paths


def _shortcut_keep(remain: list[str]) -> tuple[str, ...]:
    if not remain:
        return tuple()
    # selected_names() also keeps the Linux-only extras, so uninstalling one of
    # the four apps cannot delete the mixer shortcut.
    return selected_names(remain, detect_platform())


def _drop_shortcuts(desktop: Path, apps: Path, remain: list[str], dry_run: bool) -> None:
    """Delete shortcuts for every app that is not staying."""
    keep = _shortcut_keep(remain)
    for directory in (desktop, apps):
        if not directory.is_dir():
            continue
        if dry_run:
            for name in SHORTCUT_NAMES:
                if name in keep:
                    continue
                for suffix in (".desktop", ".lnk", ".command"):
                    target = directory / f"{name}{suffix}"
                    if os.path.lexists(target):
                        print(f"Would remove shortcut: {target}")
                bundle = directory / f"{name}.app"
                if bundle.exists():
                    print(f"Would remove shortcut: {bundle}")
            continue
        _remove_deselected(directory, keep)
        print(f"Updated shortcuts in {directory}")


def _pid_alive(pid: str) -> bool:
    if not pid or not str(pid).isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def apply_changes(
    *,
    mode: str,
    targets: list[str],
    delete_config: bool,
    config_dir: Path,
    desktop: Path,
    apps_dir: Path,
    home: Path,
    root: Path,
    dry_run: bool,
    video_pid: str = "",
) -> list[str]:
    """Do the reset or uninstall. Returns the apps that stay installed."""
    try:
        config_dir.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit(f"Refusing to edit the install tree: {config_dir}")

    state_file = config_dir / selection.STATE_FILE
    # A missing file means all four, matching the rest of the suite.
    current = selection.read(state_file)
    video_alive = "yes" if _pid_alive(video_pid) else "no"
    print(f"Exit video playing: {video_alive}")

    if mode == "reset":
        for path in state_paths(config_dir, targets, current):
            _remove(path, config_dir, dry_run, "setting")
        names = ", ".join(selection.labels(targets))
        print(f"Reset settings for {names}. The apps stay installed.")
        return list(current)

    remain = [ident for ident in current if ident not in set(targets)]
    _drop_shortcuts(desktop, apps_dir, remain, dry_run)

    if delete_config:
        for path in state_paths(config_dir, targets, remain):
            _remove(path, config_dir, dry_run, "setting")
        if not remain:
            for name in FULL_UNINSTALL_SETTINGS:
                _remove(config_dir / name, config_dir, dry_run, "setting")
            _remove(config_dir / "instances", config_dir, dry_run, "setting")
    else:
        print("Kept saved settings for a later install.")

    if not dry_run:
        selection.write(remain, state_file, allow_empty=True)
    if remain:
        print(f"Still installed: {', '.join(selection.labels(remain))}")
    else:
        print("No MP applications remain installed.")
        for relative in TRAY_PATHS:
            _remove(home / relative, home, dry_run, "tray")

    return remain


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reset or uninstall MP applications.")
    parser.add_argument("--mode", required=True, choices=("reset", "uninstall"))
    parser.add_argument("--apps", required=True, help="all, or manager,gui,liquid,visualizer")
    parser.add_argument("--keep-config", action="store_true")
    parser.add_argument("--delete-config", action="store_true")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--desktop", default="")
    parser.add_argument("--apps-dir", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--root", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--video-pid", default="")
    parser.add_argument("--result-file", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.keep_config and args.delete_config:
        raise SystemExit("Choose either --keep-config or --delete-config.")
    delete_config = bool(args.delete_config)
    if args.mode == "uninstall" and not args.keep_config and not args.delete_config:
        delete_config = False

    targets = parse_targets(args.apps)
    config_dir = Path(args.config_dir)
    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    platform_name = detect_platform()
    desktop = Path(args.desktop) if args.desktop else desktop_dir_for(platform_name)
    apps_dir = Path(args.apps_dir) if args.apps_dir else apps_dir_for(platform_name)
    home = Path(args.home) if args.home else Path.home()

    delay = os.environ.get("MP_UNINSTALL_WORK_SECONDS", "").strip()
    if delay:
        import time
        time.sleep(float(delay))

    remain = apply_changes(
        mode=args.mode,
        targets=targets,
        delete_config=delete_config,
        config_dir=config_dir,
        desktop=desktop,
        apps_dir=apps_dir,
        home=home,
        root=root,
        dry_run=args.dry_run,
        video_pid=args.video_pid,
    )
    if args.result_file:
        text = "none" if not remain else " ".join(remain)
        alive = "yes" if _pid_alive(args.video_pid) else "no"
        Path(args.result_file).write_text(
            f"remaining={text}\nvideo_alive={alive}\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
