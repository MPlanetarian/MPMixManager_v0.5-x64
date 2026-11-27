#!/usr/bin/env python3
"""Refresh the Windows lines of the release SHA256SUMS asset.

``SHA256SUMS`` is a release asset like any other, so it has to be replaced the
same way: delete, then upload. Only the two Windows entries are rewritten; the
Linux, macOS and Raspberry Pi hashes are carried over untouched.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_windows_msi import (  # noqa: E402
    API, REPO, TAG, asset_id, delete_asset, release_id, request, stored_token, upload,
)

WINDOWS_ASSETS = (
    "MP_Mix_Manager_Windows_x64.msi",
    "MP_Mix_Manager_Windows_x86.msi",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else r"X:\mp-build\out")
    user, token = stored_token()
    print(f"authenticated as {user}")
    rel = release_id(token)

    # Fetch the current SHA256SUMS.
    status, body = request(f"{API}/repos/{REPO}/releases/{rel}/assets", token)
    if status != 200:
        raise SystemExit(f"cannot list assets: {status}")
    ident = None
    for asset in body:
        if asset["name"] == "SHA256SUMS":
            ident = int(asset["id"])
            url = asset["browser_download_url"]
            break
    if ident is None:
        raise SystemExit("SHA256SUMS asset not found")

    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as resp:
        current = resp.read().decode("utf-8", "replace").splitlines()
    print(f"current SHA256SUMS has {len(current)} lines")

    new_hashes = {name: sha256(out_dir / name) for name in WINDOWS_ASSETS}
    lines: list[str] = []
    replaced: set[str] = set()
    for line in current:
        parts = line.split()
        if len(parts) >= 2 and parts[-1] in new_hashes:
            name = parts[-1]
            lines.append(f"{new_hashes[name]}  {name}")
            replaced.add(name)
        else:
            lines.append(line)
    for name, digest in new_hashes.items():
        if name not in replaced:
            lines.append(f"{digest}  {name}")

    updated = "\n".join(lines) + "\n"
    temp = Path(out_dir) / "SHA256SUMS"
    temp.write_text(updated, encoding="utf-8", newline="\n")

    print("deleting old SHA256SUMS")
    if not delete_asset(token, ident):
        raise SystemExit("could not delete SHA256SUMS")
    print("uploading updated SHA256SUMS")
    result = upload(token, rel, temp, "text/plain")
    print(f"  -> id {result.get('id')} size={result.get('size')}")

    print("\nWindows lines now:")
    for line in updated.splitlines():
        if any(name in line for name in WINDOWS_ASSETS):
            print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
