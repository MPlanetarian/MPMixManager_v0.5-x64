#!/usr/bin/env python3
"""Pin a KDE login / lock-screen wallpaper to Centered // Scaled.

Used by ``set_login_screen_wallpaper.sh``; also runnable on its own.

The org.kde.image wallpaper plugin stores its positioning as a bare integer in
``FillMode``:

    0 Stretch              1 PreserveAspectFit   2 PreserveAspectCrop
    3 Tile                 4 TileVertically      5 TileHorizontally      6 Pad

"Centered // Scaled" is **1**: the whole image fits inside every monitor and is
centred, with ``Color`` filling the letterbox bars. ``3`` tiles it, ``2`` fills
the screen by cropping, and leaving the key out falls back to the plugin default.
Because the value is an integer, a wrong one renders fine on the monitor it was
set on and then tiles or crops on a screen with a different aspect ratio -- which
is exactly how a login screen ends up Tiled.

Only the ``[Greeter]`` and ``[Greeter][Wallpaper][org.kde.image][General]``
groups are rewritten. Every other section, comment and ordering is preserved, so
re-running is a no-op.
"""

from __future__ import annotations

import argparse
import os
import sys

GREETER = "Greeter"
WP_GROUP = "Greeter][Wallpaper][org.kde.image][General"

FILLMODE_CENTERED_SCALED = "1"

DEFAULT_KEYS = {"WallpaperPlugin": "org.kde.image"}
IMAGE_KEYS = {
    "FillMode": FILLMODE_CENTERED_SCALED,
    "Blur": "false",          # no blur behind the letterbox bars
    "Color": "#000000",       # letterbox colour
    "ForceImageAnimation": "false",
}


def parse(text: str) -> list[tuple[str | None, list[str]]]:
    """Split an INI file into (group header or None for the preamble, body)."""
    blocks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            blocks.append((stripped[1:-1], []))
        else:
            blocks[-1][1].append(line)
    return blocks


def render(blocks: list[tuple[str | None, list[str]]]) -> str:
    out: list[str] = []
    for name, body in blocks:
        if name is not None:
            if out and out[-1].strip():
                out.append("")
            out.append(f"[{name}]")
        out.extend(body)
    text = "\n".join(out)
    return text.strip("\n") + "\n" if text.strip() else ""


def set_keys(blocks, group: str, updates: dict[str, str]) -> None:
    for name, body in blocks:
        if name != group:
            continue
        seen: set[str] = set()
        new_body: list[str] = []
        for line in body:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith(("#", ";")):
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    new_body.append(f"{key}={updates[key]}")
                    seen.add(key)
                    continue
            new_body.append(line)
        # Keep the blank line that separates this group from the next one.
        trailing: list[str] = []
        while new_body and not new_body[-1].strip():
            trailing.append(new_body.pop())
        for key, value in updates.items():
            if key not in seen:
                new_body.append(f"{key}={value}")
        new_body.extend(trailing[:1])
        body[:] = new_body
        return
    blocks.append((group, [f"{k}={v}" for k, v in updates.items()]))


def order_groups(blocks, order: list[str]) -> None:
    """Reorder the named groups in place, leaving every other block where it is."""
    slots = [i for i, (name, _) in enumerate(blocks) if name in order]
    if len(slots) < 2:
        return
    ranked = sorted((blocks[i] for i in slots), key=lambda b: order.index(b[0]))
    for slot, block in zip(slots, ranked):
        blocks[slot] = block


def update(path: str, image: str | None, dry_run: bool) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            original = handle.read()
    except FileNotFoundError:
        original = ""

    blocks = parse(original)
    set_keys(blocks, GREETER, dict(DEFAULT_KEYS))
    updates = dict(IMAGE_KEYS)
    if image:
        updates["Image"] = image
        updates["PreviewImage"] = image
    set_keys(blocks, WP_GROUP, updates)
    order_groups(blocks, [GREETER, WP_GROUP])

    result = render(blocks)
    if result == original:
        return "unchanged"
    if not dry_run:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(result)
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="kscreenlockerrc / plasmalogin.conf files")
    parser.add_argument("--image", help="wallpaper path or file:// URL to pin (optional)")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    failed = 0
    for path in args.paths:
        if not os.path.exists(path) and not args.dry_run:
            print(f"missing: {path}", file=sys.stderr)
            failed += 1
            continue
        try:
            print(f"{update(path, args.image, args.dry_run)}: {path}")
        except (PermissionError, OSError) as exc:
            print(f"failed: {path}: {exc}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
