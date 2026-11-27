#!/usr/bin/env python3
"""Replace the Windows MSI release assets on the v0.5.4 GitHub release.

GitHub release assets are immutable by name, so replacing one means deleting the
old asset and uploading the new file. Both steps run over the REST API using the
credential git already has stored, so no token has to be pasted anywhere.

    python scripts/publish_windows_msi.py --out-dir X:/mp-build/out

The script prints the resulting asset ids, sizes and digests, and fails loudly
if a delete or upload does not return what GitHub says it should.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "MPlanetarian/MPMixManager_v0.5-x64"
TAG = "v0.5.4"
API = "https://api.github.com"

ASSETS = {
    "MP_Mix_Manager_Windows_x64.msi": "application/x-msi",
    "MP_Mix_Manager_Windows_x86.msi": "application/x-msi",
}


def stored_token() -> tuple[str, str]:
    """Read the GitHub credential git already stores for this host."""
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git credential fill failed: {proc.stderr.strip()}")
    user = ""
    token = ""
    for line in proc.stdout.splitlines():
        if line.startswith("username="):
            user = line.split("=", 1)[1]
        elif line.startswith("password="):
            token = line.split("=", 1)[1]
    if not token:
        raise SystemExit("no GitHub token in the stored credential")
    return user, token


def request(url: str, token: str, method: str = "GET", data: bytes | None = None,
            content_type: str = "application/json") -> tuple[int, dict]:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "mp-build",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        return exc.code, {"error": detail}


def release_id(token: str) -> int:
    status, body = request(f"{API}/repos/{REPO}/releases/tags/{TAG}", token)
    if status != 200:
        raise SystemExit(f"cannot read release {TAG}: {status} {body}")
    return int(body["id"])


def asset_id(token: str, rel: int, name: str) -> int | None:
    status, body = request(f"{API}/repos/{REPO}/releases/{rel}/assets", token)
    if status != 200:
        raise SystemExit(f"cannot list assets: {status} {body}")
    for asset in body:
        if asset["name"] == name:
            return int(asset["id"])
    return None


def delete_asset(token: str, ident: int) -> bool:
    status, _ = request(f"{API}/repos/{REPO}/releases/assets/{ident}", token,
                        method="DELETE")
    return status in (204, 200)


def upload(token: str, rel: int, path: Path, content_type: str) -> dict:
    url = (f"https://uploads.github.com/repos/{REPO}/releases/{rel}/assets"
           f"?name={path.name}")
    data = path.read_bytes()
    status, body = request(url, token, method="POST", data=data,
                           content_type=content_type)
    if status not in (200, 201):
        raise SystemExit(f"upload {path.name} failed: {status} {body}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    out = Path(args.out_dir)
    missing = [n for n in ASSETS if not (out / n).is_file()]
    if missing:
        raise SystemExit(f"missing built packages in {out}: {missing}")

    user, token = stored_token()
    print(f"authenticated as {user}")

    rel = release_id(token)
    print(f"release {TAG} id={rel}")

    for name, content_type in ASSETS.items():
        path = out / name
        size_mb = path.stat().st_size / 1_048_576
        existing = asset_id(token, rel, name)
        if existing is not None:
            print(f"  deleting old {name} (id {existing})")
            if not delete_asset(token, existing):
                raise SystemExit(f"could not delete asset {existing}")
        print(f"  uploading {name} ({size_mb:.1f} MiB)")
        body = upload(token, rel, path, content_type)
        print(f"    -> id {body.get('id')} state={body.get('state')} "
              f"size={body.get('size')} digest={body.get('digest')}")

    print("\nfinal Windows assets on the release:")
    status, assets = request(f"{API}/repos/{REPO}/releases/{rel}/assets", token)
    for asset in sorted(assets, key=lambda a: a["name"]):
        if asset["name"] in ASSETS:
            print(f"  {asset['name']:<34} {asset['size']:>12,}  "
                  f"{asset['digest']}  {asset['browser_download_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
