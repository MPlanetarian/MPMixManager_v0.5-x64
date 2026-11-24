#!/usr/bin/env python3
"""
mp_auto_responder.py - MP Auto Responder GitHub project monitor
Part of MP_Reporter & MP_Mix_Manager (Dreamworlds Productions)

What it does
    Watches a public GitHub repository's issue tracker on a fixed interval
    (twenty minutes by default) and dispatches a briefing for every tick:

      1. A TEXT report   - every meaningful update in the window plus a
                           project-progress summary, attached as a .txt file.
      2. An AUDIO report - a spoken briefing in the MP Reporter voice, saved
                           as an MP3 and attached with its transcript.
      3. A LOCAL ANNOUNCEMENT - the same MP3 is played on this workstation
                           (one announcement at a time, through the shared
                           speech lock) so the progress is heard every tick.
      4. An HTML email   - dark MP Reporter dashboard, sent over the SMTP
                           account in ~/MP_REPORTER/email_config.env.

    Unlike the OpenCode progress reporter, a tick is never skipped for being
    quiet: the project-progress summary is the point, so a briefing is sent
    every twenty minutes whether or not anything changed.

    The monitor ships disabled with no repository configured. Set
    AUTO_RESPONDER_GITHUB_REPO to the owner/name (or github.com URL) of the
    project you want to watch and set AUTO_RESPONDER_ENABLED to true before
    installing the timer. With no repository configured the monitor makes no
    GitHub calls and announces nothing.

What counts as an update
    The repository issues endpoint is read with ``since=<last cursor>``. Every
    item updated in the window is compared with the previous snapshot:

      * a new issue or pull request,
      * an issue closed or reopened,
      * a pull request merged or closed,
      * new comments (the comment-count delta),
      * a retitled item,
      * any other update (pushes, reviews, labels) - listed compactly as
        "updated" so the report truly covers everything.

Progress
    Open/closed issue counts, open/merged/closed-unmerged pull request counts,
    an overall completion percentage, the last 24 hours of activity and the
    change since the previous briefing are all read from the GitHub search API.
    Counts carry forward when a search call is rate-limited, so one throttled
    request never turns the dashboard into zeroes.

Rate limiting
    One dispatch per interval bucket, enforced through the state file, plus a
    process lock so a manual ``--force`` cannot race a timer tick. A GitHub
    token is taken from AUTO_RESPONDER_GITHUB_TOKEN, GH_TOKEN/GITHUB_TOKEN or
    ``gh auth token``; without one the public 60-request hourly limit applies
    and the reporter simply reports less often.

Usage
    mp_auto_responder.py --send        Send if this interval bucket is due
    mp_auto_responder.py --force       Send now, ignoring the bucket
    mp_auto_responder.py --dry-run     Build everything, print it, send nothing
    mp_auto_responder.py --status      Show configuration, progress and history
    mp_auto_responder.py --print       Print the text report for this window
"""

import argparse
import base64
import fcntl
import json
import os
import re
import shutil
import smtplib
import socket
import ssl
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email import encoders
from email import utils as email_utils
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape
from pathlib import Path

# No repository ships preconfigured: the monitor stays dormant until
# AUTO_RESPONDER_GITHUB_REPO names an owner/name or github.com URL to watch.
DEFAULT_REPO = ""
DEFAULT_INTERVAL_MINUTES = 20
DEFAULT_MAX_LISTED = 2000
DEFAULT_MAX_SPOKEN = 8
LOG_NAME = "MP_Reporter_Email.log"
STATE_NAME = "auto_responder_state.json"
USER_AGENT = "MP-Auto-Responder/1.0 (+https://github.com/MPlanetarian)"
SEARCH_API = "https://api.github.com/search/issues"
API_ROOT = "https://api.github.com"


# ------------------------------------------------------------------------------
# Path / configuration resolution
# ------------------------------------------------------------------------------

def _repo_root() -> Path:
    """The MP Mix Manager checkout this script lives in (…/scripts/..)."""
    return Path(__file__).resolve().parent.parent


def resolve_reporter_dir() -> Path:
    """Directory holding email_config.env, data/, TTS_AUDIO/ and REPORTS/."""
    env = os.environ.get("MP_REPORTER_DIR")
    if env:
        return Path(env).expanduser()
    installed = Path.home() / "MP_REPORTER"
    if (installed / "email_config.env").is_file():
        return installed
    return _repo_root()


REPORTER_DIR = resolve_reporter_dir()
CONFIG_FILE = REPORTER_DIR / "email_config.env"
ALT_CONFIG_FILE = Path.home() / ".config" / "mp-reporter" / "email_config.env"
DATA_DIR = REPORTER_DIR / "data"
STATE_FILE = DATA_DIR / STATE_NAME
LOG_FILE = REPORTER_DIR / LOG_NAME
REPORTS_DIR = REPORTER_DIR / "REPORTS" / "AUTO_RESPONDER"
TTS_DIR = REPORTER_DIR / "TTS_AUDIO" / "AUTO_RESPONDER"


def log_message(level: str, msg: str):
    """Log to stdout and the shared MP Reporter email log."""
    line = "[%s] [%s] [auto-responder] %s" % (
        datetime.now().isoformat(), level.upper(), msg)
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


CONFIG_DEFAULTS = {
    # Shared mail account
    "EMAIL_REPORTS_ENABLED": "true",
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "SMTP_USE_TLS": "true",
    "SMTP_USE_SSL": "false",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "EMAIL_SENDER_NAME": "MP Reporter (Mathew K John)",
    "EMAIL_SENDER_ADDR": "",
    "EMAIL_RECIPIENTS": "",
    "TTS_VOICE": "stephen_hawking_matrix",
    "PLAY_ALERT_ON_CONFIRMATION": "true",
    "CONFIRMATION_ALERT_SOUND":
        "/usr/share/sounds/freedesktop/stereo/complete.oga",
    # MP Auto Responder
    "AUTO_RESPONDER_ENABLED": "false",
    "AUTO_RESPONDER_EMAIL_ENABLED": "true",
    "AUTO_RESPONDER_GITHUB_REPO": DEFAULT_REPO,
    "AUTO_RESPONDER_INTERVAL_MINUTES": str(DEFAULT_INTERVAL_MINUTES),
    "AUTO_RESPONDER_RECIPIENTS": "",
    "AUTO_RESPONDER_SUBJECT_PREFIX": "MP Auto Responder - Project",
    "AUTO_RESPONDER_GITHUB_TOKEN": "",
    "AUTO_RESPONDER_TTS_ENABLED": "true",
    "AUTO_RESPONDER_ATTACH_TEXT": "true",
    "AUTO_RESPONDER_ATTACH_TRANSCRIPT": "true",
    "AUTO_RESPONDER_ATTACH_AUDIO": "true",
    "AUTO_RESPONDER_ANNOUNCE_AUDIO": "true",
    "AUTO_RESPONDER_ALERT_SOUND": "true",
    "AUTO_RESPONDER_MAX_LISTED_UPDATES": str(DEFAULT_MAX_LISTED),
    "AUTO_RESPONDER_MAX_SPOKEN_UPDATES": str(DEFAULT_MAX_SPOKEN),
}


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    for path in (CONFIG_FILE, ALT_CONFIG_FILE):
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key in cfg:
                        cfg[key] = val
        except Exception as exc:
            log_message("WARN", "could not read %s: %s" % (path, exc))
    for key in cfg:
        if key in os.environ:
            cfg[key] = os.environ[key]
    return cfg


def truthy(value, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def cfg_int(cfg: dict, key: str, fallback: int) -> int:
    try:
        value = int(str(cfg.get(key, fallback)).strip())
        return value if value > 0 else fallback
    except Exception:
        return fallback


def interval_minutes(cfg: dict) -> int:
    return cfg_int(cfg, "AUTO_RESPONDER_INTERVAL_MINUTES",
                   DEFAULT_INTERVAL_MINUTES)


def repo_slug(cfg: dict) -> str:
    """The configured owner/name slug, or "" when no project is configured."""
    slug = (cfg.get("AUTO_RESPONDER_GITHUB_REPO") or "").strip()
    slug = slug.replace("https://github.com/", "").strip("/")
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug


def recipients(cfg: dict) -> list:
    raw = (cfg.get("AUTO_RESPONDER_RECIPIENTS")
           or cfg.get("EMAIL_RECIPIENTS") or "")
    return [part.strip() for part in raw.split(",") if part.strip()]


# ------------------------------------------------------------------------------
# GitHub access
# ------------------------------------------------------------------------------

def resolve_github_token(cfg: dict) -> str:
    """Configured token first, then the environment, then the gh CLI."""
    for key in ("AUTO_RESPONDER_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = (cfg.get(key) or os.environ.get(key) or "").strip()
        if value:
            return value
    for candidate in ("gh", "/home/linuxbrew/.linuxbrew/bin/gh",
                      "/usr/local/bin/gh", "/usr/bin/gh"):
        binary = shutil.which(candidate) if "/" not in candidate else candidate
        if not binary or not Path(binary).exists():
            continue
        try:
            out = subprocess.run([binary, "auth", "token"], timeout=15,
                                 capture_output=True, text=True)
            token = (out.stdout or "").strip()
            if out.returncode == 0 and token:
                return token
        except Exception:
            continue
    return ""


def _headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _http_json(url: str, token: str, timeout: float = 30.0):
    """GET a GitHub URL and return (payload, headers, final_url)."""
    req = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw), dict(resp.headers), resp.geturl()


def github_get(path: str, params: dict, token: str, timeout: float = 30.0):
    url = API_ROOT + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _http_json(url, token, timeout)


def github_paginate(path: str, params: dict, token: str,
                    max_pages: int = 20) -> tuple:
    """Follow Link rel=next and return (items, complete, newest_updated).

    ``complete`` is False when the page cap stopped the walk, so the caller can
    keep the cursor conservative instead of pretending it saw everything.
    """
    items, page, complete = [], 1, True
    params = dict(params)
    params["per_page"] = 100
    while page <= max_pages:
        params["page"] = page
        payload, headers, _url = github_get(path, params, token)
        if not isinstance(payload, list):
            raise ValueError("unexpected GitHub payload: %s" % type(payload))
        items.extend(payload)
        link = headers.get("Link") or headers.get("link") or ""
        if 'rel="next"' not in link:
            break
        page += 1
    else:
        complete = False
    newest = ""
    for item in items:
        stamp = str(item.get("updated_at") or "")
        if stamp > newest:
            newest = stamp
    return items, complete, newest


def search_total(query: str, token: str, cache: dict = None) -> int:
    """Total count for one search query; None when GitHub refuses the call."""
    if cache is not None and query in cache:
        return cache[query]
    params = {"q": query, "per_page": 1}
    try:
        payload, _headers_, _url = github_get("/search/issues", params, token)
        total = int(payload.get("total_count") or 0)
        if cache is not None:
            cache[query] = total
        return total
    except Exception as exc:
        log_message("WARN", "search failed (%s): %s"
                    % (query.split(" ")[0], exc))
        return None


def collect_repo(token: str, slug: str) -> dict:
    try:
        payload, _headers_, _url = github_get("/repos/" + slug, {}, token)
        return {
            "name": payload.get("full_name") or slug,
            "description": payload.get("description") or "",
            "stars": int(payload.get("stargazers_count") or 0),
            "forks": int(payload.get("forks_count") or 0),
            "watchers": int(payload.get("subscribers_count") or 0),
            "default_branch": payload.get("default_branch") or "",
            "pushed_at": str(payload.get("pushed_at") or ""),
            "html_url": payload.get("html_url")
            or ("https://github.com/" + slug),
        }
    except Exception as exc:
        log_message("WARN", "could not read repository %s: %s" % (slug, exc))
        return {"name": slug, "description": "", "stars": 0, "forks": 0,
                "watchers": 0, "default_branch": "", "pushed_at": "",
                "html_url": "https://github.com/" + slug}


def previous_search_counts(state: dict) -> dict:
    snapshot = state.get("snapshot") or {}
    keys = ("issues_open", "issues_closed", "prs_open", "prs_closed",
            "prs_merged", "created_24h", "closed_24h", "merged_24h")
    return {key: snapshot.get(key) for key in keys
            if isinstance(snapshot.get(key), int)}


def collect_progress(token: str, slug: str, state: dict) -> dict:
    """Project completion counts, with the last good value as a fallback."""
    previous = previous_search_counts(state)
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache = {}
    queries = {
        "issues_open": "repo:%s type:issue state:open" % slug,
        "issues_closed": "repo:%s type:issue state:closed" % slug,
        "prs_open": "repo:%s type:pr state:open" % slug,
        "prs_closed": "repo:%s type:pr state:closed" % slug,
        "prs_merged": "repo:%s type:pr is:merged" % slug,
        "created_24h": "repo:%s created:>=%s" % (slug, day_ago),
        "closed_24h": "repo:%s closed:>=%s" % (slug, day_ago),
        "merged_24h": "repo:%s is:merged merged:>=%s" % (slug, day_ago),
    }
    counts, fallback = {}, False
    for key, query in queries.items():
        value = search_total(query, token, cache)
        if value is None:
            value = previous.get(key, 0)
            if key in previous:
                fallback = True
        counts[key] = int(value or 0)
        if not token:
            time.sleep(1.0)

    issues_total = counts["issues_open"] + counts["issues_closed"]
    prs_decided = counts["prs_merged"] + max(
        0, counts["prs_closed"] - counts["prs_merged"])
    prs_total = counts["prs_open"] + counts["prs_closed"]
    completed = counts["issues_closed"] + counts["prs_merged"]
    all_total = issues_total + prs_total
    counts.update({
        "issues_total": issues_total,
        "prs_unmerged_closed": max(
            0, counts["prs_closed"] - counts["prs_merged"]),
        "prs_total": prs_total,
        "prs_decided": prs_decided,
        "items_total": all_total,
        "items_completed": completed,
        "issues_resolved_pct": _pct(counts["issues_closed"], issues_total),
        "prs_merged_pct": _pct(counts["prs_merged"], prs_total),
        "overall_pct": _pct(completed, all_total),
        "open_remaining": counts["issues_open"] + counts["prs_open"],
        "fallback": fallback,
        "captured_at": now.isoformat(),
    })

    deltas = {}
    for key, old in previous.items():
        if isinstance(old, int):
            deltas[key] = counts.get(key, 0) - old
    counts["deltas"] = deltas
    return counts


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * part / total, 1)


def blank_progress() -> dict:
    """Zeroed progress so --status never calls GitHub with no repository."""
    return {
        "issues_open": 0, "issues_closed": 0, "prs_open": 0,
        "prs_closed": 0, "prs_merged": 0, "created_24h": 0,
        "closed_24h": 0, "merged_24h": 0, "issues_total": 0,
        "prs_total": 0, "items_total": 0, "items_completed": 0,
        "issues_resolved_pct": 0.0, "prs_merged_pct": 0.0,
        "overall_pct": 0.0, "open_remaining": 0, "deltas": {},
        "fallback": False,
    }


# ------------------------------------------------------------------------------
# Update discovery
# ------------------------------------------------------------------------------

def parse_timestamp(value) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def item_record(raw: dict) -> dict:
    pull = raw.get("pull_request") or {}
    return {
        "number": int(raw.get("number") or 0),
        "title": str(raw.get("title") or "").strip(),
        "url": str(raw.get("html_url") or ""),
        "author": str((raw.get("user") or {}).get("login") or ""),
        "state": str(raw.get("state") or "open"),
        "is_pr": bool(raw.get("pull_request")),
        "merged": bool(pull.get("merged_at")),
        "draft": bool(raw.get("draft")),
        "comments": int(raw.get("comments") or 0),
        "labels": [str((lab or {}).get("name") or "")
                   for lab in (raw.get("labels") or [])][:8],
        "created": str(raw.get("created_at") or ""),
        "updated": str(raw.get("updated_at") or ""),
        "closed_at": str(raw.get("closed_at") or ""),
    }


def classify(previous: dict, current: dict, window_start=None) -> list:
    """The meaningful update kinds for one item since the last snapshot.

    A closed item that was never in the baseline (the baseline only seeds open
    items) is not brand new just because it was commented on. Creation time
    decides: anything created before the window is an update, not an arrival.
    """
    kinds = []
    if previous is None:
        created = parse_timestamp(current.get("created"))
        if (window_start is not None
                and created < window_start - timedelta(minutes=10)):
            return ["updated"]
        return ["new"]
    if previous.get("state") != current.get("state"):
        if current.get("merged"):
            kinds.append("merged")
        elif current.get("state") == "closed":
            kinds.append("closed")
        elif current.get("state") == "open":
            kinds.append("reopened")
    elif current.get("merged") and not previous.get("merged"):
        kinds.append("merged")
    if current.get("comments", 0) > previous.get("comments", 0):
        kinds.append("comments")
    if (previous.get("title") or "") != (current.get("title") or ""):
        kinds.append("retitled")
    if not kinds:
        kinds.append("updated")
    return kinds


def collect_updates(state: dict, token: str, slug: str,
                    window_start: datetime) -> tuple:
    """Fetch everything updated since the cursor and reduce it to records."""
    saw = state.get("items") or {}
    since = (window_start - timedelta(seconds=90)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    try:
        raw_items, complete, _newest = github_paginate(
            "/repos/%s/issues" % slug,
            {"state": "all", "since": since, "sort": "updated",
             "direction": "asc"},
            token, max_pages=20)
    except Exception as exc:
        log_message("ERROR", "could not read issues: %s" % exc)
        return [], {}, False, False

    updates, fresh = [], {}
    for raw in raw_items:
        record = item_record(raw)
        if not record["number"]:
            continue
        fresh[str(record["number"])] = record
        previous = saw.get(str(record["number"]))
        kinds = classify(previous, record, window_start)
        comment_delta = 0
        if previous is not None:
            comment_delta = max(
                0, record["comments"] - int(previous.get("comments") or 0))
        updates.append({
            "number": record["number"],
            "title": record["title"],
            "url": record["url"],
            "author": record["author"],
            "state": record["state"],
            "is_pr": record["is_pr"],
            "merged": record["merged"],
            "draft": record["draft"],
            "labels": record["labels"],
            "comment_delta": comment_delta,
            "kinds": kinds,
        })
    # An item updated twice in one page pair is kept at its newest state.
    deduped = {}
    for update in updates:
        deduped[update["number"]] = update
    updates = sorted(deduped.values(), key=lambda u: u["number"], reverse=True)
    log_message("INFO", "read %d updated item(s) since %s (complete=%s)"
                % (len(updates), since, complete))
    return updates, fresh, complete, True


def collect_baseline(token: str, slug: str, max_pages: int = 15) -> tuple:
    """Seed every open issue and pull request before the first delta.

    Without this crawl an older open item that happens to update next window
    would be announced as brand new. Only open items are needed: a closed item
    can only reappear through a reopen, which reads as new once and is rare.
    """
    try:
        raw_items, complete, _newest = github_paginate(
            "/repos/%s/issues" % slug,
            {"state": "open", "sort": "updated", "direction": "desc"},
            token, max_pages=max_pages)
    except Exception as exc:
        log_message("ERROR", "could not build the baseline: %s" % exc)
        return {}, False
    fresh = {}
    for raw in raw_items:
        record = item_record(raw)
        if record["number"]:
            fresh[str(record["number"])] = record
    log_message("INFO", "baseline seeded with %d open item(s) (complete=%s)"
                % (len(fresh), complete))
    return fresh, complete


def categorize(updates: list) -> dict:
    groups = {
        "new_issues": [], "closed_issues": [], "reopened_issues": [],
        "new_prs": [], "merged_prs": [], "closed_prs": [],
        "comment_items": [], "updated_prs": [], "updated_issues": [],
        "retitled": [],
    }
    for update in updates:
        kinds = update["kinds"]
        is_pr = update["is_pr"]
        if "new" in kinds:
            groups["new_prs" if is_pr else "new_issues"].append(update)
            continue
        if "merged" in kinds:
            groups["merged_prs"].append(update)
        elif "closed" in kinds:
            groups["closed_prs" if is_pr else "closed_issues"].append(update)
        elif "reopened" in kinds:
            groups["reopened_issues"].append(update)
        elif "comments" in kinds:
            groups["comment_items"].append(update)
        elif is_pr:
            groups["updated_prs"].append(update)
        else:
            groups["updated_issues"].append(update)
        if "retitled" in kinds:
            groups["retitled"].append(update)
    return groups


def build_report_data(cfg: dict, state: dict, token: str, slug: str,
                      window_start: datetime, window_end: datetime) -> dict:
    repo = collect_repo(token, slug)
    progress = collect_progress(token, slug, state)
    baseline = not bool(state.get("items"))
    if baseline:
        fresh, complete = collect_baseline(token, slug)
        updates = []
        reachable = bool(fresh)
    else:
        updates, fresh, complete, reachable = collect_updates(
            state, token, slug, window_start)
    groups = categorize(updates)
    counts = {
        "new_issues": len(groups["new_issues"]),
        "closed_issues": len(groups["closed_issues"]),
        "reopened_issues": len(groups["reopened_issues"]),
        "new_prs": len(groups["new_prs"]),
        "merged_prs": len(groups["merged_prs"]),
        "closed_prs": len(groups["closed_prs"]),
        "comments": len(groups["comment_items"]),
        "updated_prs": len(groups["updated_prs"]),
        "updated_issues": len(groups["updated_issues"]),
        "total": len(updates),
    }
    return {
        "generated": window_end,
        "window_start": window_start,
        "window_end": window_end,
        "window_minutes": max(
            1, int(round((window_end - window_start).total_seconds() / 60.0))),
        "hostname": socket.gethostname() or "workstation",
        "repo": repo,
        "progress": progress,
        "updates": updates,
        "groups": groups,
        "counts": counts,
        "fresh_items": fresh,
        "fetch_complete": complete,
        "github_reachable": reachable,
        "baseline": baseline,
        "tracked_open": len(fresh),
    }


# ------------------------------------------------------------------------------
# Report composition
# ------------------------------------------------------------------------------

def _snippet(text: str, limit: int = 120) -> str:
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "..."


def _num(value) -> str:
    try:
        return "{:,}".format(int(value))
    except Exception:
        return str(value)


def _signed(value) -> str:
    try:
        value = int(value)
    except Exception:
        return ""
    if value == 0:
        return "no change"
    return "%+d" % value


def _delta_text(progress: dict, key: str) -> str:
    deltas = progress.get("deltas") or {}
    if key not in deltas:
        return ""
    return " (%s)" % _signed(deltas.get(key))


def _update_line(update: dict) -> str:
    kind = "PR" if update["is_pr"] else "issue"
    flags = []
    if update.get("merged"):
        flags.append("merged")
    elif update["state"] == "closed":
        flags.append("closed")
    elif update["state"] == "open" and "reopened" in update["kinds"]:
        flags.append("reopened")
    if update.get("draft"):
        flags.append("draft")
    if update.get("comment_delta"):
        flags.append("+%d comment%s" % (
            update["comment_delta"],
            "" if update["comment_delta"] == 1 else "s"))
    if "retitled" in update["kinds"]:
        flags.append("retitled")
    tag = ("[" + ", ".join(flags) + "] ") if flags else ""
    author = (" by " + update["author"]) if update.get("author") else ""
    return "#%d %s%s%s  %s" % (
        update["number"], tag, update["title"], author, kind)


def build_text_report(data: dict, max_listed: int) -> str:
    repo = data["repo"]
    progress = data["progress"]
    counts = data["counts"]
    stamp = data["generated"].strftime("%A %d %B %Y at %H:%M:%S")
    lines = [
        "MP AUTO RESPONDER - GITHUB PROJECT BRIEFING",
        "=" * 72,
        "Project    : %s" % repo["name"],
        "About      : %s" % (repo.get("description") or "no description"),
        "Repository : %s" % repo.get("html_url", ""),
        "Generated  : %s" % stamp,
        "Workstation: %s" % data["hostname"],
        "Window     : last %d minute(s), %s to %s UTC" % (
            data["window_minutes"],
            data["window_start"].strftime("%H:%M:%S"),
            data["window_end"].strftime("%H:%M:%S")),
        "Next       : in %d minutes" % data["window_minutes"],
        "",
        "PROJECT PROGRESS",
        "-" * 72,
        "  Issues resolved    : %s of %s (%.1f%%)%s" % (
            _num(progress["issues_closed"]), _num(progress["issues_total"]),
            progress["issues_resolved_pct"],
            _delta_text(progress, "issues_closed")),
        "  Pull requests      : %s merged of %s (%.1f%%)%s" % (
            _num(progress["prs_merged"]), _num(progress["prs_total"]),
            progress["prs_merged_pct"],
            _delta_text(progress, "prs_merged")),
        "  Overall completion : %.1f%% (%s of %s tracked items)" % (
            progress["overall_pct"], _num(progress["items_completed"]),
            _num(progress["items_total"])),
        "  Open work remaining: %s issues + %s pull requests" % (
            _num(progress["issues_open"]), _num(progress["prs_open"])),
        "  Last 24 hours      : %s opened, %s closed, %s merged" % (
            _num(progress["created_24h"]), _num(progress["closed_24h"]),
            _num(progress["merged_24h"])),
    ]
    if progress.get("fallback"):
        lines.append("  Notice             : a GitHub search call was throttled; "
                     "one or more counts carried forward.")
    if not data.get("fetch_complete"):
        lines.append("  Notice             : the update page cap was reached; "
                     "the next briefing continues from this cursor.")
    if not data.get("github_reachable"):
        lines.append("  Notice             : GitHub was unreachable; this "
                     "briefing repeats the last known progress.")
    lines.append("")

    if data.get("baseline"):
        lines.append("BASELINE ESTABLISHED")
        lines.append("-" * 72)
        lines.append("  First briefing: tracking %s open issue(s) and pull "
                     "request(s)." % _num(data.get("tracked_open", 0)))
        lines.append("  The first change delta is announced in %d minutes."
                     % data["window_minutes"])
        if not data.get("fetch_complete"):
            lines.append("  Notice: the baseline page cap was reached; "
                         "remaining open items join the tracking store as "
                         "they update.")
    else:
        lines.append("UPDATES IN THIS WINDOW: %s item(s)"
                     % _num(counts["total"]))
        lines.append("-" * 72)
        sections = [
            ("New issues", data["groups"]["new_issues"], "new_issues"),
            ("Issues closed", data["groups"]["closed_issues"], "closed_issues"),
            ("Issues reopened", data["groups"]["reopened_issues"],
             "reopened_issues"),
            ("New pull requests", data["groups"]["new_prs"], "new_prs"),
            ("Pull requests merged", data["groups"]["merged_prs"], "merged_prs"),
            ("Pull requests closed (unmerged)", data["groups"]["closed_prs"],
             "closed_prs"),
            ("New comments", data["groups"]["comment_items"], "comments"),
            ("Other pull request updates", data["groups"]["updated_prs"],
             "updated_prs"),
            ("Other issue updates", data["groups"]["updated_issues"],
             "updated_issues"),
        ]
        listed = 0
        for heading, items, key in sections:
            if not items:
                continue
            lines.append("")
            lines.append("%s (%d)" % (heading, len(items)))
            for update in items:
                if listed >= max_listed:
                    break
                lines.append("  " + _update_line(update))
                listed += 1
            if listed >= max_listed and len(items) > listed:
                lines.append("  ... list capped at %d; %d update(s) omitted "
                             "from this attachment."
                             % (max_listed, counts["total"] - listed))
        if not counts["total"]:
            lines.append("  No issue or pull request updates were detected in "
                         "this window.")
        if counts["total"] > listed:
            lines.append("")
            lines.append("  %d of %d update(s) listed; the rest were omitted "
                         "by the configured cap." % (listed, counts["total"]))
    lines.append("")
    lines.append("=" * 72)
    if data.get("email_enabled", True):
        lines.append("Text report and audio transcript attached. The audio "
                     "briefing is")
        lines.append("also announced on the workstation. Sent by MP Auto "
                     "Responder.")
    else:
        lines.append("Email delivery is disabled. This text report and audio "
                     "transcript are")
        lines.append("saved on the workstation and the audio briefing is "
                     "announced locally.")
    return "\n".join(lines) + "\n"


def _spoken_highlights(data: dict, limit: int) -> list:
    groups = data["groups"]
    picks = (groups["merged_prs"] + groups["closed_issues"]
             + groups["new_issues"] + groups["new_prs"])
    lines = []
    for update in picks[:limit]:
        label = "pull request" if update["is_pr"] else "issue"
        verb = ("merged" if update.get("merged")
                else "closed" if update["state"] == "closed" else "opened")
        lines.append("%s %d, %s, %s" % (
            label, update["number"], verb, _snippet(update["title"], 110)))
    return lines


def build_speech_script(data: dict, max_spoken: int) -> str:
    progress = data["progress"]
    counts = {
        "total": 0, "new_issues": 0, "closed_issues": 0,
        "reopened_issues": 0, "new_prs": 0, "merged_prs": 0,
        "closed_prs": 0, "comments": 0, "updated_prs": 0,
        "updated_issues": 0,
    }
    counts.update(data.get("counts") or {})
    when = data["generated"].strftime("%H:%M")
    repo_name = data["repo"]["name"].split("/")[-1]
    progress_sentence = (
        "Project progress: %.1f percent of issues resolved, %s of %s. "
        "%.1f percent of pull requests merged, %s of %s. Overall completion "
        "stands at %.1f percent, with %s open items remaining." % (
            progress["issues_resolved_pct"],
            _num(progress["issues_closed"]), _num(progress["issues_total"]),
            progress["prs_merged_pct"],
            _num(progress["prs_merged"]), _num(progress["prs_total"]),
            progress["overall_pct"], _num(progress["open_remaining"])))
    activity_sentence = (
        "In the last twenty four hours the project opened %s items, closed "
        "%s, and merged %s pull requests." % (
            _num(progress["created_24h"]), _num(progress["closed_24h"]),
            _num(progress["merged_24h"])))
    greeting = ("Greetings Mathew. This is the MP Auto Responder with your %s "
                "project briefing at %s." % (repo_name, when))
    if data.get("baseline"):
        parts = [
            greeting,
            ("This is the first briefing, but GitHub could not be reached, so "
             "the tracking baseline is not seeded yet. I will retry in %d "
             "minutes." % data["window_minutes"])
            if not data.get("github_reachable") else
            ("This is the first briefing. I have established a baseline of %s "
             "open items, so the next briefing can report exactly what "
             "changed." % _num(data.get("tracked_open", 0))),
            progress_sentence,
            activity_sentence,
        ]
    else:
        parts = [
            greeting,
            "%s update%s in the last %d minutes: %d new issue%s, %d issue%s "
            "closed, %d new pull request%s, %d merged, and %d other update%s." % (
                _num(counts["total"]), "" if counts["total"] == 1 else "s",
                data["window_minutes"],
                counts["new_issues"], "" if counts["new_issues"] == 1 else "s",
                counts["closed_issues"],
                "" if counts["closed_issues"] == 1 else "s",
                counts["new_prs"], "" if counts["new_prs"] == 1 else "s",
                counts["merged_prs"],
                counts["updated_prs"] + counts["updated_issues"]
                + counts["comments"] + counts["reopened_issues"],
                "" if (counts["updated_prs"] + counts["updated_issues"]
                      + counts["comments"] + counts["reopened_issues"]) == 1
                else "s"),
            progress_sentence,
            activity_sentence,
        ]
    highlights = [] if data.get("baseline") else _spoken_highlights(
        data, max_spoken)
    if highlights:
        parts.append("Highlights. " + "; ".join(highlights) + ".")
    elif not counts["total"] and not data.get("baseline"):
        parts.append("No changes were detected in this window, so the project "
                     "stands where it did twenty minutes ago.")
    if not data.get("github_reachable"):
        parts.append("GitHub could not be reached this time, so these figures "
                     "repeat the last known progress and the monitor will "
                     "retry on its next run.")
    if data.get("email_enabled", True):
        parts.append("The full report and the transcript of this announcement "
                     "are attached to your email. End of transmission.")
    else:
        parts.append("Email delivery is disabled. The full report and the "
                     "transcript are saved on the workstation. End of "
                     "transmission.")
    return " ".join(parts)


def build_html_report(data: dict, text_name: str, audio_name: str,
                      transcript_name: str) -> str:
    progress = data["progress"]
    counts = data["counts"]
    repo = data["repo"]

    def rows(items, verb):
        if not items:
            return ("<tr><td colspan=\"3\" style=\"padding:12px;color:#64748b;"
                    "font-size:12px;\">None.</td></tr>")
        out = []
        for update in items[:25]:
            flags = []
            if update.get("comment_delta"):
                flags.append("+%d comments" % update["comment_delta"])
            if "retitled" in update["kinds"]:
                flags.append("retitled")
            meta = (" &bull; " + ", ".join(flags)) if flags else ""
            out.append(
                "<tr><td style=\"padding:8px 10px;color:#38bdf8;font-size:12px;"
                "font-family:monospace;\">#%d</td>"
                "<td style=\"padding:8px 10px;color:#e2e8f0;font-size:12px;\">%s"
                "<div style=\"color:#64748b;font-size:11px;\">%s%s</div></td>"
                "<td style=\"padding:8px 10px;color:#94a3b8;font-size:11px;\">%s</td></tr>"
                % (update["number"], html_escape(_snippet(update["title"], 150)),
                   html_escape(verb), meta,
                   html_escape(update.get("author") or "")))
        if len(items) > 25:
            out.append("<tr><td colspan=\"3\" style=\"padding:8px 10px;"
                       "color:#64748b;font-size:11px;\">... and %d more in the "
                       "attached report.</td></tr>" % (len(items) - 25))
        return "\n".join(out)

    section_specs = [
        ("New issues", data["groups"]["new_issues"], "opened"),
        ("Issues closed", data["groups"]["closed_issues"], "closed"),
        ("New pull requests", data["groups"]["new_prs"], "opened"),
        ("Pull requests merged", data["groups"]["merged_prs"], "merged"),
        ("Pull requests closed", data["groups"]["closed_prs"], "closed"),
        ("New comments", data["groups"]["comment_items"], "commented"),
        ("Other pull request updates", data["groups"]["updated_prs"], "updated"),
    ]
    sections_html = []
    for heading, items, verb in section_specs:
        if not items:
            continue
        sections_html.append("""
    <div style="color:#38bdf8;font-size:13px;font-weight:700;margin:18px 0 6px;">%s (%d)</div>
    <table width="100%%" cellpadding="0" cellspacing="0" style="background-color:#0b1120;border:1px solid #1e293b;border-radius:8px;">
      %s
    </table>""" % (html_escape(heading), len(items), rows(items, verb)))
    if data.get("baseline"):
        sections_html = ["<div style=\"background-color:#0c1a2e;border:1px "
                         "solid #0369a1;border-radius:8px;padding:14px;"
                         "margin-top:18px;color:#cbd5e1;font-size:12px;\">"
                         "<b style=\"color:#38bdf8;\">Baseline established.</b> "
                         "Tracking %s open items. The first change delta is "
                         "announced in %d minutes.</div>"
                         % (_num(data.get("tracked_open", 0)),
                            data["window_minutes"])]
    elif not sections_html:
        sections_html.append(
            "<div style=\"color:#94a3b8;font-size:13px;margin-top:16px;\">"
            "No issue or pull request updates in this window.</div>")

    return """<html><body style="margin:0;padding:0;background-color:#0b1120;">
<table width="100%%" cellpadding="0" cellspacing="0" style="background-color:#0b1120;padding:24px;">
<tr><td align="center">
<table width="760" cellpadding="0" cellspacing="0" style="background-color:#0f172a;border:1px solid #1e293b;border-radius:12px;overflow:hidden;">
  <tr><td style="background-color:#0c1a2e;padding:22px 28px;border-bottom:1px solid #1e293b;">
    <div style="color:#06b6d4;font-size:11px;letter-spacing:2px;font-weight:700;">MP AUTO RESPONDER</div>
    <div style="color:#f8fafc;font-size:20px;font-weight:700;margin-top:4px;">%(repo)s &mdash; project briefing</div>
    <div style="color:#94a3b8;font-size:12px;margin-top:6px;">%(stamp)s &bull; %(host)s &bull; last %(mins)d minutes</div>
  </td></tr>
  <tr><td style="padding:22px 28px;">
    <div style="color:#94a3b8;font-size:12px;margin-bottom:14px;">%(about)s</div>
    <table width="100%%" cellpadding="0" cellspacing="0">
      <tr>
        <td width="25%%" style="padding:10px;background-color:#0b1120;border:1px solid #1e293b;border-radius:8px;">
          <div style="color:#38bdf8;font-size:26px;font-weight:700;">%(overall).1f%%</div>
          <div style="color:#94a3b8;font-size:11px;">overall completion</div>
        </td>
        <td width="25%%" style="padding:10px;background-color:#0b1120;border:1px solid #1e293b;border-radius:8px;">
          <div style="color:#22c55e;font-size:26px;font-weight:700;">%(issuepct).1f%%</div>
          <div style="color:#94a3b8;font-size:11px;">%(issues_closed)s of %(issues_total)s issues resolved</div>
        </td>
        <td width="25%%" style="padding:10px;background-color:#0b1120;border:1px solid #1e293b;border-radius:8px;">
          <div style="color:#a78bfa;font-size:26px;font-weight:700;">%(prpct).1f%%</div>
          <div style="color:#94a3b8;font-size:11px;">%(prs_merged)s of %(prs_total)s PRs merged</div>
        </td>
        <td width="25%%" style="padding:10px;background-color:#0b1120;border:1px solid #1e293b;border-radius:8px;">
          <div style="color:#fbbf24;font-size:26px;font-weight:700;">%(updates)s</div>
          <div style="color:#94a3b8;font-size:11px;">updates this window</div>
        </td>
      </tr>
    </table>
    <div style="color:#cbd5e1;font-size:12px;line-height:1.8;margin-top:16px;">
      Open work: <b>%(open_issues)s</b> issues + <b>%(open_prs)s</b> pull requests.
      Last 24 hours: <b>%(created24)s</b> opened, <b>%(closed24)s</b> closed,
      <b>%(merged24)s</b> merged.
    </div>
    %(sections)s
    <div style="background-color:#0c1a2e;border:1px solid #0369a1;border-radius:8px;padding:14px;margin-top:18px;">
      <div style="color:#38bdf8;font-size:12px;font-weight:700;margin-bottom:4px;">Attachments</div>
      <div style="color:#cbd5e1;font-size:12px;line-height:1.6;">
        1. <b>%(text_name)s</b> &mdash; the complete text report.<br/>
        2. <b>%(transcript_name)s</b> &mdash; the transcript of the audio briefing.<br/>
        3. <b>%(audio_name)s</b> &mdash; the spoken briefing announced on the workstation.
      </div>
    </div>
  </td></tr>
  <tr><td style="background-color:#090d16;padding:14px 28px;color:#64748b;font-size:11px;text-align:center;">
    Dreamworlds Productions &bull; MP Auto Responder &bull; dispatched every %(mins)d minutes
  </td></tr>
</table>
</td></tr></table>
</body></html>""" % {
        "repo": html_escape(repo["name"]),
        "stamp": data["generated"].strftime("%A %d %B %Y, %H:%M"),
        "host": html_escape(data["hostname"]),
        "mins": data["window_minutes"],
        "about": html_escape(repo.get("description") or ""),
        "overall": progress["overall_pct"],
        "issuepct": progress["issues_resolved_pct"],
        "prpct": progress["prs_merged_pct"],
        "issues_closed": _num(progress["issues_closed"]),
        "issues_total": _num(progress["issues_total"]),
        "prs_merged": _num(progress["prs_merged"]),
        "prs_total": _num(progress["prs_total"]),
        "updates": _num(counts["total"]),
        "open_issues": _num(progress["issues_open"]),
        "open_prs": _num(progress["prs_open"]),
        "created24": _num(progress["created_24h"]),
        "closed24": _num(progress["closed_24h"]),
        "merged24": _num(progress["merged_24h"]),
        "sections": "\n".join(sections_html),
        "text_name": html_escape(text_name),
        "transcript_name": html_escape(transcript_name),
        "audio_name": html_escape(audio_name),
    }


# ------------------------------------------------------------------------------
# Audio: synthesis, playback, speech lock
# ------------------------------------------------------------------------------

def _load_reporter_helpers():
    """Reuse the MP Reporter's Piper/espeak synthesis when it is importable."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mp_opencode_progress_reporter as helper  # type: ignore
        return helper
    except Exception as exc:
        log_message("WARN", "MP Reporter TTS helper unavailable: %s" % exc)
        return None


def synthesize_audio(text: str, out_prefix: Path) -> dict:
    helper = _load_reporter_helpers()
    if helper is not None:
        try:
            result = helper.synthesize_audio(text, out_prefix)
            # Piper writes an intermediate WAV beside the MP3. A 20-minute
            # cadence would otherwise keep several gigabytes of duplicate
            # speech in TTS_AUDIO, so only the MP3 is archived.
            for candidate in (out_prefix.with_suffix(".wav"),
                              out_prefix.with_name(out_prefix.name + "_raw.wav")):
                try:
                    candidate.unlink()
                except Exception:
                    pass
            return result
        except Exception as exc:
            log_message("WARN", "MP Reporter synthesis failed: %s" % exc)
    return _fallback_synthesize(text, out_prefix)


def _fallback_synthesize(text: str, out_prefix: Path) -> dict:
    """espeak-ng + ffmpeg, used only when the reporter helper is absent."""
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg = shutil.which("ffmpeg")
    if not espeak:
        return {"mp3": None, "wav": None, "size_kb": 0}
    raw = out_prefix.with_name(out_prefix.name + "_raw.wav")
    wav = out_prefix.with_suffix(".wav")
    mp3 = out_prefix.with_suffix(".mp3")
    text_file = out_prefix.with_suffix(".txt")
    text_file.write_text(text, encoding="utf-8")
    try:
        subprocess.run([espeak, "-v", "en-us+m3", "-s", "110", "-p", "19",
                        "-f", str(text_file), "-w", str(raw)],
                       check=True, capture_output=True, timeout=300)
    except Exception as exc:
        log_message("ERROR", "espeak synthesis failed: %s" % exc)
        return {"mp3": None, "wav": None, "size_kb": 0}
    if ffmpeg:
        chain = ("bass=g=12:f=95:w=0.6, equalizer=f=2400:t=q:w=1.2:g=2, "
                 "aecho=0.8:0.7:50|100:0.3|0.2")
        try:
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(raw),
                            "-af", chain, "-codec:a", "libmp3lame",
                            "-b:a", "32k", "-ac", "1", str(mp3)],
                           check=True, capture_output=True, timeout=300)
        except Exception:
            try:
                shutil.copy2(raw, mp3)
            except Exception:
                pass
    else:
        try:
            shutil.copy2(raw, mp3)
        except Exception:
            pass
    for transient in (raw, wav):
        try:
            transient.unlink()
        except Exception:
            pass
    size_kb = round(mp3.stat().st_size / 1024, 1) if mp3.exists() else 0
    return {"mp3": mp3 if mp3.exists() else None, "wav": None,
            "size_kb": size_kb}


def _speech_lock(timeout: float = 900.0):
    """The machine-wide lock that allows one announcement at a time."""
    import contextlib
    helper = _load_reporter_helpers()
    if helper is not None:
        try:
            return helper._speech_lock(timeout=timeout)
        except Exception:
            pass
    return contextlib.nullcontext()


def play_audio(path: Path, timeout: float = 420.0) -> bool:
    """Announce the briefing locally, serialised behind the speech lock."""
    if not path or not Path(path).exists():
        return False
    players = [
        ("pw-play", []),
        ("paplay", []),
        ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
        ("aplay", ["-q"]),
    ]
    with _speech_lock(timeout=300.0):
        for name, args in players:
            binary = shutil.which(name)
            if not binary:
                continue
            try:
                subprocess.run([binary] + args + [str(path)], timeout=timeout,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                return True
            except Exception:
                continue
    log_message("WARN", "no audio player could announce %s" % path)
    return False


def play_confirmation_alert(sound_file: str, timeout: float = 120.0):
    path = Path(sound_file or "/usr/share/sounds/freedesktop/stereo/complete.oga")
    if not path.exists():
        return
    with _speech_lock(timeout=timeout):
        for name, args in (("pw-play", []), ("paplay", []), ("aplay", ["-q"])):
            binary = shutil.which(name)
            if not binary:
                continue
            try:
                subprocess.run([binary] + args + [str(path)], timeout=10,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue


# ------------------------------------------------------------------------------
# State / rate limiting
# ------------------------------------------------------------------------------

def load_state() -> dict:
    default = {
        "last_bucket": None,
        "last_sent_timestamp": None,
        "last_cursor": None,
        "total_emails_sent": 0,
        "total_announcements": 0,
        "items": {},
        "snapshot": {},
        "history": [],
    }
    try:
        if STATE_FILE.is_file():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                default.update(data)
    except Exception as exc:
        log_message("WARN", "could not read state file: %s" % exc)
    return default


def save_state(state: dict):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:
        log_message("ERROR", "could not save state file: %s" % exc)


def current_bucket(minutes: int) -> int:
    return int(time.time() // max(60, minutes * 60))


def bucket_due(state: dict, minutes: int, force: bool = False) -> bool:
    if force:
        return True
    return state.get("last_bucket") != current_bucket(minutes)


def window_bounds(state: dict, minutes: int) -> tuple:
    """The window since the previous briefing, clamped to 4 intervals."""
    end = datetime.now(timezone.utc)
    try:
        last = parse_timestamp(state.get("last_sent_timestamp"))
        start = last
        if start < end - timedelta(minutes=minutes * 4):
            start = end - timedelta(minutes=minutes)
    except Exception:
        start = end - timedelta(minutes=minutes)
    if start >= end:
        start = end - timedelta(minutes=minutes)
    return start, end


def prune_items(items: dict, keep_days: int = 30) -> dict:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = {}
    for key, record in items.items():
        if record.get("state") == "open" or str(record.get("updated") or "") >= cutoff:
            kept[key] = record
    return kept


def merge_fresh_items(state: dict, fresh: dict) -> dict:
    items = dict(state.get("items") or {})
    items.update(fresh)
    return prune_items(items)


# ------------------------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------------------------

def execute_dispatch(force: bool = False, dry_run: bool = False) -> bool:
    cfg = load_config()
    # EMAIL_REPORTS_ENABLED is the master switch for the MP Reporter's other
    # mail dispatchers. The Auto Responder has its own delivery switch so it can
    # keep announcing on the workstation while every mailbox stays quiet.
    if not truthy(cfg.get("AUTO_RESPONDER_ENABLED"), False):
        log_message("INFO", "MP Auto Responder is disabled")
        return False

    if not repo_slug(cfg):
        log_message("INFO", "no repository configured in "
                            "AUTO_RESPONDER_GITHUB_REPO; nothing to monitor")
        return True

    minutes = interval_minutes(cfg)
    lock_path = STATE_FILE.parent / "auto_responder.lock"
    lock_handle = None
    if not dry_run:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = open(lock_path, "w")
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, BlockingIOError, OSError):
            if lock_handle is not None:
                lock_handle.close()
            log_message("INFO", "another MP Auto Responder dispatch is "
                                "already running; skipping")
            return True
    try:
        return _dispatch_locked(cfg, minutes, force, dry_run)
    finally:
        if lock_handle is not None:
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)
                lock_handle.close()
            except Exception:
                pass


def _dispatch_locked(cfg: dict, minutes: int, force: bool,
                     dry_run: bool) -> bool:
    state = load_state()
    if not dry_run and not bucket_due(state, minutes, force):
        log_message("INFO", "this %d-minute window has already been reported; "
                            "skipping" % minutes)
        return True

    slug = repo_slug(cfg)
    token = resolve_github_token(cfg)
    log_message("INFO", "reading %s (GitHub token %s)"
                % (slug, "present" if token else "absent"))
    run_started = datetime.now(timezone.utc)
    start, end = window_bounds(state, minutes)
    data = build_report_data(cfg, state, token, slug, start, end)
    data["token_present"] = bool(token)
    email_enabled = truthy(cfg.get("AUTO_RESPONDER_EMAIL_ENABLED"), True)
    data["email_enabled"] = email_enabled

    stamp = end.strftime("%Y-%m-%d_%H-%M-%S")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    text_path = REPORTS_DIR / ("MP_Auto_Responder_%s.txt" % stamp)
    text_report = build_text_report(
        data, cfg_int(cfg, "AUTO_RESPONDER_MAX_LISTED_UPDATES",
                      DEFAULT_MAX_LISTED))
    text_path.write_text(text_report, encoding="utf-8")

    transcript_path = None
    audio = {"mp3": None, "wav": None, "size_kb": 0}
    script = build_speech_script(
        data, cfg_int(cfg, "AUTO_RESPONDER_MAX_SPOKEN_UPDATES",
                      DEFAULT_MAX_SPOKEN))
    transcript_path = REPORTS_DIR / ("MP_Auto_Responder_Transcript_%s.txt"
                                     % stamp)
    transcript_path.write_text(script + "\n", encoding="utf-8")
    if truthy(cfg.get("AUTO_RESPONDER_TTS_ENABLED"), True):
        audio_path = TTS_DIR / ("MP_Auto_Responder_Audio_%s" % stamp)
        log_message("INFO", "synthesizing the spoken project briefing...")
        audio = synthesize_audio(script, audio_path)
        if audio["mp3"]:
            log_message("OK", "audio briefing ready: %s (%s KB)"
                        % (audio["mp3"].name, audio["size_kb"]))
        else:
            audio["mp3"] = None
            log_message("WARN", "audio briefing unavailable")

    text_name = text_path.name
    transcript_name = transcript_path.name
    audio_name = audio["mp3"].name if audio["mp3"] else "(audio unavailable)"
    html_report = build_html_report(data, text_name, audio_name,
                                    transcript_name)
    to_list = recipients(cfg)

    if dry_run:
        log_message("OK", "[DRY-RUN] updates=%d overall=%.1f%% text=%s "
                          "transcript=%s audio=%s email=%s recipients=%s"
                    % (data["counts"]["total"], data["progress"]["overall_pct"],
                       text_name, transcript_name, audio_name,
                       "on" if email_enabled else "off",
                       ", ".join(to_list) or "(none)"))
        print()
        print(text_report)
        return True

    if email_enabled:
        if not to_list:
            log_message("ERROR", "no recipients configured in email_config.env")
            return False
        password = (cfg.get("SMTP_PASSWORD") or "").strip()
        if not password:
            log_message("ERROR", "SMTP password is not configured; run "
                                 "mp_email_manager.sh configure-password")
            return False
        subject = "%s - %s (%d update%s, %.1f%% complete)" % (
            cfg.get("AUTO_RESPONDER_SUBJECT_PREFIX")
            or "MP Auto Responder - Project",
            end.strftime("%d %b %Y %H:%M"),
            data["counts"]["total"],
            "" if data["counts"]["total"] == 1 else "s",
            data["progress"]["overall_pct"])

        msg = MIMEMultipart("mixed")
        msg["From"] = "%s <%s>" % (
            cfg.get("EMAIL_SENDER_NAME", "MP Reporter"),
            cfg.get("EMAIL_SENDER_ADDR") or cfg.get("SMTP_USER"))
        msg["To"] = ", ".join(to_list)
        msg["Date"] = email_utils.formatdate(localtime=True)
        msg["Subject"] = subject

        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(text_report, "plain", "utf-8"))
        alternative.attach(MIMEText(html_report, "html", "utf-8"))
        msg.attach(alternative)

        def attach(path: Path, maintype: str, subtype: str):
            if not path or not Path(path).exists():
                return
            with open(path, "rb") as fh:
                part = MIMEBase(maintype, subtype)
                part.set_payload(fh.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment",
                            filename=Path(path).name)
            msg.attach(part)

        if truthy(cfg.get("AUTO_RESPONDER_ATTACH_TEXT"), True):
            attach(text_path, "text", "plain")
        if truthy(cfg.get("AUTO_RESPONDER_ATTACH_TRANSCRIPT"), True):
            attach(transcript_path, "text", "plain")
        if audio["mp3"] and truthy(cfg.get("AUTO_RESPONDER_ATTACH_AUDIO"), True):
            attach(audio["mp3"], "audio", "mpeg")

        user = cfg.get("SMTP_USER") or cfg.get("EMAIL_SENDER_ADDR")
        host = cfg.get("SMTP_HOST") or "smtp.gmail.com"
        port = cfg_int(cfg, "SMTP_PORT", 587)
        use_ssl = truthy(cfg.get("SMTP_USE_SSL"), False) or port == 465
        try:
            context = ssl.create_default_context()
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, context=context,
                                          timeout=60)
            else:
                server = smtplib.SMTP(host, port, timeout=60)
            server.ehlo()
            if not use_ssl and truthy(cfg.get("SMTP_USE_TLS"), True):
                server.starttls(context=context)
                server.ehlo()
            server.login(user, password)
            server.sendmail(user, to_list, msg.as_string())
            server.quit()
        except Exception as exc:
            text = str(exc)
            if ("535" in text or "BadCredentials" in text
                    or "not accepted" in text):
                log_message("ERROR", "SMTP rejected the credentials; a Google "
                                     "App Password is required for Gmail")
            else:
                log_message("ERROR", "SMTP send failed (%s): %s" % (host, exc))
            return False

        log_message("OK", "briefing emailed to %s (%d update(s), overall "
                          "%.1f%%)"
                    % (", ".join(to_list), data["counts"]["total"],
                       data["progress"]["overall_pct"]))
    else:
        log_message("OK", "email delivery is disabled; this briefing is "
                          "announcement-only (%d update(s), overall %.1f%%)"
                    % (data["counts"]["total"],
                       data["progress"]["overall_pct"]))

    # Record the dispatch before the announcement. A long narration can never
    # take the bucket or the cursor down with it if the service is stopped.
    state["last_bucket"] = current_bucket(minutes)
    state["last_sent_timestamp"] = end.isoformat()
    if data["github_reachable"] and (data["fetch_complete"]
                                     or data.get("baseline")):
        state["last_cursor"] = iso(run_started)
    if email_enabled:
        state["total_emails_sent"] = int(state.get("total_emails_sent") or 0) + 1
    announcing = bool(audio["mp3"] and truthy(
        cfg.get("AUTO_RESPONDER_ANNOUNCE_AUDIO"), True))
    if announcing:
        state["total_announcements"] = int(
            state.get("total_announcements") or 0) + 1
    state["snapshot"] = {
        key: data["progress"].get(key)
        for key in ("issues_open", "issues_closed", "prs_open", "prs_closed",
                    "prs_merged", "created_24h", "closed_24h", "merged_24h")
    }
    state["snapshot"]["captured_at"] = end.isoformat()
    state["items"] = merge_fresh_items(state, data["fresh_items"])
    history = state.get("history") or []
    history.append({
        "timestamp": datetime.now().isoformat(),
        "updates": data["counts"]["total"],
        "overall_pct": data["progress"]["overall_pct"],
        "text_file": text_name,
        "transcript_file": transcript_name,
        "audio_file": audio["mp3"].name if audio["mp3"] else None,
        "recipients": to_list if email_enabled else [],
        "delivery": "email" if email_enabled else "announcement",
        "status": "sent" if email_enabled else "announced",
    })
    state["history"] = history[-200:]
    save_state(state)
    prune_archives()

    # Announce on the workstation after delivery and durable state, so a failed
    # send never leaves a spoken report with no email.
    if truthy(cfg.get("AUTO_RESPONDER_ALERT_SOUND"), True):
        play_confirmation_alert(cfg.get("CONFIRMATION_ALERT_SOUND"))
    if announcing:
        log_message("INFO", "announcing the briefing on this workstation...")
        play_audio(audio["mp3"])
    return True


def prune_archives(keep: int = 144):
    """Keep two days of twenty-minute briefings, then prune the oldest."""
    for directory, pattern in (
        (REPORTS_DIR, "MP_Auto_Responder_*.txt"),
        (TTS_DIR, "MP_Auto_Responder_Audio_*"),
    ):
        try:
            files = sorted(directory.glob(pattern),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            continue
        for stale in files[keep:]:
            try:
                stale.unlink()
            except Exception:
                pass


# ------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------

def print_status():
    cfg = load_config()
    state = load_state()
    minutes = interval_minutes(cfg)
    slug = repo_slug(cfg)
    token = resolve_github_token(cfg) if slug else ""
    progress = collect_progress(token, slug, state) if slug else blank_progress()
    print("=" * 72)
    print("     MP AUTO RESPONDER - GITHUB PROJECT MONITOR")
    print("=" * 72)
    print("  Enabled:            %s" % (
        "YES" if truthy(cfg.get("AUTO_RESPONDER_ENABLED"), False) else "NO"))
    print("  Repository:         %s" % (slug or "(not configured)"))
    if not slug:
        print("  Note:               set AUTO_RESPONDER_GITHUB_REPO in "
              "email_config.env")
        print("                      to a project owner/name before enabling "
              "the monitor.")
    print("  Interval:           every %d minutes" % minutes)
    print("  Email delivery:     %s" % (
        "YES" if truthy(cfg.get("AUTO_RESPONDER_EMAIL_ENABLED"), True)
        else "NO (announcement only)"))
    print("  Recipients:         %s" % (", ".join(recipients(cfg)) or "(none)"))
    print("  GitHub token:       %s" % ("present" if token else
                                       "absent (public rate limit)"))
    print("  Text report:        %s" % (
        "attached" if truthy(cfg.get("AUTO_RESPONDER_ATTACH_TEXT"), True)
        else "body only"))
    print("  Audio transcript:   %s" % (
        "attached" if truthy(cfg.get("AUTO_RESPONDER_ATTACH_TRANSCRIPT"), True)
        else "not attached"))
    print("  Audio briefing:     %s" % (
        "MP3 + local announcement"
        if truthy(cfg.get("AUTO_RESPONDER_TTS_ENABLED"), True) else "disabled"))
    print("  Report directory:   %s" % REPORTS_DIR)
    print("  Audio directory:    %s" % TTS_DIR)
    print("-" * 72)
    print("  This window:        %s"
          % ("DUE" if bucket_due(state, minutes) else "already sent"))
    print("  Last sent:          %s" % (state.get("last_sent_timestamp") or "never"))
    print("  Total emails sent:  %s" % state.get("total_emails_sent", 0))
    print("  Announcements:      %s" % state.get("total_announcements", 0))
    print("  Tracked items:      %s" % len(state.get("items") or {}))
    print("  Issues resolved:    %.1f%% (%s of %s)" % (
        progress["issues_resolved_pct"],
        _num(progress["issues_closed"]), _num(progress["issues_total"])))
    print("  Pull requests:      %.1f%% merged (%s of %s)" % (
        progress["prs_merged_pct"],
        _num(progress["prs_merged"]), _num(progress["prs_total"])))
    print("  Overall completion: %.1f%% (%s of %s)" % (
        progress["overall_pct"], _num(progress["items_completed"]),
        _num(progress["items_total"])))
    print("  Open remaining:     %s issues + %s pull requests" % (
        _num(progress["issues_open"]), _num(progress["prs_open"])))
    print("=" * 72)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="MP Auto Responder GitHub project monitor "
                    "(text + audio + email, every 20 minutes)")
    parser.add_argument("--send", action="store_true",
                        help="send when the current interval bucket is due")
    parser.add_argument("--force", action="store_true",
                        help="send now, ignoring the interval bucket")
    parser.add_argument("--dry-run", action="store_true",
                        help="build everything and print it, without email")
    parser.add_argument("--status", action="store_true",
                        help="show configuration, progress and history")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the text report for this window")
    args = parser.parse_args(argv)

    if args.status:
        print_status()
        return 0
    if args.print_only:
        cfg = load_config()
        state = load_state()
        slug = repo_slug(cfg)
        if not slug:
            print("MP Auto Responder: no repository configured in "
                  "AUTO_RESPONDER_GITHUB_REPO; nothing to report.")
            return 0
        start, end = window_bounds(state, interval_minutes(cfg))
        data = build_report_data(cfg, state, resolve_github_token(cfg),
                                 slug, start, end)
        print(build_text_report(
            data, cfg_int(cfg, "AUTO_RESPONDER_MAX_LISTED_UPDATES",
                          DEFAULT_MAX_LISTED)))
        return 0
    if args.dry_run:
        return 0 if execute_dispatch(force=True, dry_run=True) else 1
    if args.force:
        return 0 if execute_dispatch(force=True) else 1
    return 0 if execute_dispatch(force=False) else 1


if __name__ == "__main__":
    sys.exit(main())
