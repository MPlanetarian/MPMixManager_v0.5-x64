#!/usr/bin/env python3
"""Replace release assets for v0.5.4 and rewrite SHA256SUMS for every platform.

Unlike scripts/publish_windows_msi.py this does not depend on a stored git
credential, so it runs on a build host that only has a token. Every asset is
deleted then re-uploaded, because GitHub asset names are immutable.

    GH_TOKEN=... python3 publish_release_assets.py --dir <build output> [--dry-run]

SHA256SUMS is regenerated from the *published* assets: each line is
recomputed from the bytes actually on the release, so the file can never
disagree with what a user downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "MPlanetarian/MPMixManager_v0.5-x64"
TAG = "v0.5.4"
API = "https://api.github.com"

# name -> content type
CONTENT_TYPES = {
    ".msi": "application/x-msi",
    ".pkg": "application/octet-stream",
    ".deb": "application/vnd.debian.binary-package",
    ".rpm": "application/x-rpm",
    ".zst": "application/zstd",
    ".apk": "application/octet-stream",
    ".snap": "application/octet-stream",
    ".flatpak": "application/octet-stream",
    ".AppImage": "application/octet-stream",
    ".gz": "application/gzip",
    ".txt": "text/plain",
    ".pub": "text/plain",
    "": "application/octet-stream",
}


def token() -> str:
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not t:
        raise SystemExit("set GH_TOKEN")
    return t.strip()


def request(url: str, tok: str, method: str = "GET", data: bytes | None = None,
            content_type: str = "application/json") -> tuple[int, dict]:
    headers = {
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "mp-release",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")}


def release(tok: str) -> dict:
    status, body = request(f"{API}/repos/{REPO}/releases/tags/{TAG}", tok)
    if status != 200:
        raise SystemExit(f"cannot read {TAG}: {status} {body}")
    return body


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            d.update(block)
    return d.hexdigest()


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=1800) as resp:
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="directory holding the new artifacts")
    ap.add_argument("--only", nargs="*", default=None,
                    help="explicit asset names to replace (default: every artifact in --dir)")
    ap.add_argument("--delete", nargs="*", default=None,
                    help="asset names to remove from the release (e.g. a stale signing key)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-sums", action="store_true")
    args = ap.parse_args()

    out = Path(args.dir)
    if not out.is_dir():
        raise SystemExit(f"not a directory: {out}")

    if args.only:
        names = list(args.only)
    else:
        names = sorted(
            p.name for p in out.iterdir()
            if p.is_file() and not p.name.startswith(".")
            and p.name not in {"SHA256SUMS"}
        )
    missing = [n for n in names if not (out / n).is_file()]
    if missing:
        raise SystemExit(f"missing in {out}: {missing}")

    print(f"to publish ({len(names)}):")
    for n in names:
        print(f"  {n:48} {(out / n).stat().st_size:>13,}")

    if args.dry_run:
        if args.delete:
            print(f"\nwould delete: {', '.join(args.delete)}")
        print("\ndry run, nothing changed")
        return 0

    tok = token()
    rel = release(tok)
    rel_id = int(rel["id"])
    print(f"\nrelease {TAG} id={rel_id}")

    existing = {a["name"]: a["id"] for a in rel["assets"]}

    for name in (args.delete or []):
        if name not in existing:
            print(f"  no such asset, nothing to delete: {name}")
            continue
        print(f"  delete {name} (id {existing[name]})")
        st, _ = request(f"{API}/repos/{REPO}/releases/assets/{existing[name]}",
                        tok, method="DELETE")
        if st not in (200, 204):
            raise SystemExit(f"delete {name} failed: {st}")

    for name in names:
        path = out / name
        if name in existing:
            print(f"  delete old {name} (id {existing[name]})")
            st, _ = request(f"{API}/repos/{REPO}/releases/assets/{existing[name]}",
                            tok, method="DELETE")
            if st not in (200, 204):
                raise SystemExit(f"delete {name} failed: {st}")
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        url = (f"https://uploads.github.com/repos/{REPO}/releases/{rel_id}/assets"
               f"?name={name}")
        print(f"  upload {name} ({path.stat().st_size/1048576:.1f} MiB) as {ctype}")
        st, body = request(url, tok, method="POST", data=path.read_bytes(),
                           content_type=ctype)
        if st not in (200, 201):
            raise SystemExit(f"upload {name} failed: {st} {body}")
        print(f"    -> id {body.get('id')} state={body.get('state')} "
              f"size={body.get('size')} digest={body.get('digest')}")

    if args.skip_sums:
        print("\nskipping SHA256SUMS")
        return 0

    # Rebuild SHA256SUMS from what is actually published. GitHub reports a
    # sha256 digest for every asset, so the file can be rebuilt from the API
    # without pulling hundreds of megabytes back down; an asset without a
    # digest is downloaded and hashed instead.
    st, assets = request(f"{API}/repos/{REPO}/releases/{rel_id}/assets", tok)
    if st != 200:
        raise SystemExit(f"cannot list assets: {st}")

    # GitHub computes an upload's digest asynchronously, so a fresh asset can
    # briefly report null. Wait for the ones we just replaced.
    for attempt in range(20):
        missing = [a["name"] for a in assets
                   if a["name"] in names
                   and not (a.get("digest") or "").startswith("sha256:")]
        if not missing:
            break
        print(f"  waiting for digests: {', '.join(missing)}")
        time.sleep(3)
        st, assets = request(f"{API}/repos/{REPO}/releases/{rel_id}/assets", tok)

    print(f"\nrecomputing SHA256SUMS from {len(assets)} published assets")
    lines: list[str] = []
    for asset in sorted(assets, key=lambda a: a["name"]):
        name = asset["name"]
        if name == "SHA256SUMS":
            continue
        digest = (asset.get("digest") or "").strip()
        if digest.startswith("sha256:"):
            hexdigest = digest.split(":", 1)[1].lower()
            how = "api"
        else:
            hexdigest = sha256_bytes(download(asset["browser_download_url"]))
            how = "downloaded"
        lines.append(f"{hexdigest}  {name}")
        print(f"  {hexdigest}  {name}  ({how})")

    payload = ("\n".join(lines) + "\n").encode()
    su = out / "SHA256SUMS"
    su.write_bytes(payload)

    old = next((a for a in assets if a["name"] == "SHA256SUMS"), None)
    if old:
        print("  delete old SHA256SUMS")
        request(f"{API}/repos/{REPO}/releases/assets/{old['id']}", tok, method="DELETE")
    url = (f"https://uploads.github.com/repos/{REPO}/releases/{rel_id}/assets"
           f"?name=SHA256SUMS")
    st, body = request(url, tok, method="POST", data=payload, content_type="text/plain")
    if st not in (200, 201):
        raise SystemExit(f"upload SHA256SUMS failed: {st} {body}")
    print(f"  -> SHA256SUMS id {body.get('id')} sha256 {sha256_bytes(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
