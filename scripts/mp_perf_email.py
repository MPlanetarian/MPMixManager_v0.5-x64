#!/usr/bin/env python3
"""
mp_perf_email.py - Package and email performance reports + audio summaries
==========================================================================

Collects the newest performance run, benchmark-mode run, soak result and
comparison report, attaches them (with any generated WAV audio summaries) and
sends one email through the same SMTP configuration the MP Reporter uses.

Credential lookup order (first hit wins):
  1. --config PATH
  2. $MP_REPORTER_CONFIG
  3. ~/.config/mp-reporter/email_config.env   (symlink the MP Reporter uses)
  4. <repo>/email_config.env

Nothing is sent unless SMTP_PASSWORD is present. The script prints exactly
where it looked, so a missing credential is a clear message instead of a
silent no-op.

Usage
-----
    ./scripts/mp_perf_email.py --check            # verify SMTP auth only
    ./scripts/mp_perf_email.py --dry-run          # build the message, do not send
    ./scripts/mp_perf_email.py                    # send the newest report
    ./scripts/mp_perf_email.py --to a@b.c --to d@e.f
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = ROOT / "REPORTS" / "PERF"

CONFIG_CANDIDATES = [
    Path.home() / ".config" / "mp-reporter" / "email_config.env",
    ROOT / "email_config.env",
]

# Recipients used by the MP Reporter; overridable with --to.
DEFAULT_RECIPIENTS = [
    "mplanetarian@mplanetarian.com",
    "serotine@gmail.com",
    "MathewKJohn2026@gmail.com",
]


def _read_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def load_config(explicit: str = "") -> Tuple[Dict[str, str], str]:
    """Return (config, source description)."""
    tried: List[str] = []
    if explicit:
        tried.append(explicit)
        cfg = _read_env_file(Path(explicit).expanduser())
        if cfg:
            return cfg, explicit
    env_path = os.environ.get("MP_REPORTER_CONFIG", "")
    if env_path:
        tried.append(env_path)
        cfg = _read_env_file(Path(env_path).expanduser())
        if cfg:
            return cfg, env_path
    for cand in CONFIG_CANDIDATES:
        tried.append(str(cand))
        if cand.is_file():
            cfg = _read_env_file(cand)
            if cfg:
                return cfg, str(cand)
    return {}, "none of: " + ", ".join(tried)


def smtp_settings(cfg: Dict[str, str]) -> Dict[str, Any]:
    return {
        "host": cfg.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(cfg.get("SMTP_PORT", "587") or 587),
        "user": cfg.get("SMTP_USER", ""),
        "password": cfg.get("SMTP_PASSWORD", ""),
        "use_ssl": str(cfg.get("SMTP_USE_SSL", "false")).lower() in ("true", "1", "yes"),
        "use_tls": str(cfg.get("SMTP_USE_TLS", "true")).lower() in ("true", "1", "yes"),
    }


def check_smtp(settings: Dict[str, Any]) -> Tuple[bool, str]:
    if not settings["password"]:
        return False, "SMTP_PASSWORD is not set"
    host, port = settings["host"], settings["port"]
    try:
        if settings["use_ssl"] or port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as srv:
                srv.login(settings["user"], settings["password"])
        else:
            with smtplib.SMTP(host, port, timeout=30) as srv:
                srv.ehlo()
                if settings["use_tls"]:
                    srv.starttls(context=ssl.create_default_context())
                    srv.ehlo()
                srv.login(settings["user"], settings["password"])
        return True, f"authenticated as {settings['user']}"
    except Exception as exc:  # noqa: BLE001 - report any SMTP failure verbatim
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# report discovery
# ---------------------------------------------------------------------------
def newest_reports() -> Dict[str, Optional[Path]]:
    """Newest file of each report kind, judged by modification time."""
    found: Dict[str, Optional[Path]] = {
        "perf": None,
        "benchmark-mode": None,
        "soak": None,
        "comparison": None,
    }
    if not REPORT_ROOT.is_dir():
        return found
    best: Dict[str, Tuple[float, Path]] = {}
    for path in REPORT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        kind = None
        if name == "benchmark-mode.json":
            kind = "benchmark-mode"
        elif name == "soak.json":
            kind = "soak"
        elif name == "perf.json":
            kind = "perf"
        elif name.endswith(".md") and "compar" in name:
            kind = "comparison"
        if kind is None:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if kind not in best or mtime > best[kind][0]:
            best[kind] = (mtime, path)
    for kind, (_, path) in best.items():
        found[kind] = path
    return found


def collect_attachments(extra: List[str]) -> List[Path]:
    files: List[Path] = []
    for path in newest_reports().values():
        if path is not None:
            files.append(path)
    # Any WAV produced by the TTS step that sits beside the reports.
    if REPORT_ROOT.is_dir():
        wavs = sorted(REPORT_ROOT.rglob("*.wav"), key=lambda p: p.stat().st_mtime)
        files.extend(wavs[-3:])
    for item in extra:
        p = Path(item).expanduser()
        if p.is_file():
            files.append(p)
    # De-duplicate while keeping order.
    seen, unique = set(), []
    for p in files:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def build_message(sender: str, recipients: List[str],
                  attachments: List[Path], tag: str) -> EmailMessage:
    msg = EmailMessage()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg["Subject"] = f"MP Mix Manager — Performance Test Report {tag} ({stamp})"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    body = [
        "MP Mix Manager — Performance Test Report",
        "",
        f"Generated: {stamp}",
        f"Host     : {os.uname().nodename if hasattr(os, 'uname') else 'windows'}",
        "",
        "Attached:",
    ]
    for path in attachments:
        try:
            size_kb = path.stat().st_size / 1024
        except OSError:
            size_kb = 0
        body.append(f"  - {path.name}  ({size_kb:.0f} KB)")

    body += [
        "",
        "The JSON reports hold the raw measurements. Open them with",
        "  ./scripts/mp_perf_compare.py --list",
        "to see the run history, or",
        "  ./scripts/mp_perf_compare.py --before A.json --after B.json",
        "for a side-by-side delta.",
        "",
        "-- MP Mix Manager performance harness",
    ]
    msg.set_content("\n".join(body))

    for path in attachments:
        ctype, encoding = mimetypes.guess_type(path.name)
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        try:
            data = path.read_bytes()
        except OSError:
            continue
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def send(msg: EmailMessage, settings: Dict[str, Any]) -> None:
    host, port = settings["host"], settings["port"]
    if settings["use_ssl"] or port == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as srv:
            srv.login(settings["user"], settings["password"])
            srv.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as srv:
            srv.ehlo()
            if settings["use_tls"]:
                srv.starttls(context=ssl.create_default_context())
                srv.ehlo()
            srv.login(settings["user"], settings["password"])
            srv.send_message(msg)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mp_perf_email.py",
        description="Email the newest MP performance reports.",
    )
    ap.add_argument("--config", default="", help="path to email_config.env")
    ap.add_argument("--to", action="append", default=None,
                    help="recipient (repeatable); defaults to the MP Reporter list")
    ap.add_argument("--attach", action="append", default=[],
                    help="extra file to attach (repeatable)")
    ap.add_argument("--tag", default="", help="label in the subject")
    ap.add_argument("--check", action="store_true", help="only verify SMTP auth")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the message and list attachments without sending")
    args = ap.parse_args(argv)

    cfg, source = load_config(args.config)
    settings = smtp_settings(cfg)

    print(f"config   : {source}")
    print(f"smtp     : {settings['host']}:{settings['port']} "
          f"(ssl={settings['use_ssl']} tls={settings['use_tls']})")
    print(f"user     : {settings['user'] or '<unset>'}")
    print(f"password : {'set' if settings['password'] else 'MISSING'}")

    if args.check or not settings["password"]:
        ok, detail = check_smtp(settings)
        print(f"auth     : {'OK' if ok else 'FAILED'} — {detail}")
        if not ok:
            print()
            print("To fix: add SMTP_PASSWORD to")
            print(f"  {source if source != 'none' else CONFIG_CANDIDATES[0]}")
            return 1
        if args.check:
            return 0

    recipients = args.to or DEFAULT_RECIPIENTS
    attachments = collect_attachments(args.attach)
    if not attachments:
        print("no reports found under REPORTS/PERF — run a benchmark first")
        return 1

    sender = cfg.get("SMTP_USER") or (recipients[0] if recipients else "")
    msg = build_message(sender, recipients, attachments, args.tag)

    print()
    print(f"to       : {', '.join(recipients)}")
    print(f"subject  : {msg['Subject']}")
    print(f"files    : {len(attachments)}")
    for path in attachments:
        print(f"  - {path}")

    if args.dry_run:
        print()
        print("dry run: message built, not sent")
        return 0

    print()
    print("sending…")
    try:
        send(msg, settings)
    except Exception as exc:  # noqa: BLE001
        print(f"send failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("sent OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
