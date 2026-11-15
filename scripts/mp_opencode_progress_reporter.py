#!/usr/bin/env python3
"""
mp_opencode_progress_reporter.py - MP Reporter OpenCode Agent Progress Briefing
Part of MP_Reporter & MP_Mix_Manager (Dreamworlds Productions)

What it does
    Every ten minutes (configurable) this reporter walks every *running*
    OpenCode agent session, works out what each one did since the previous
    briefing, and emails two reports to the configured recipients:

      1. A TEXT report  - a plain-text email body plus a .txt attachment.
      2. An AUDIO report - a Stephen Hawking Matrix voice MP3 attachment.

    Session state comes from the running OpenCode service over its local HTTP
    API. When the service is not reachable the reporter falls back to a
    read-only scan of ~/.local/share/opencode/opencode.db, so a briefing is
    still produced while OpenCode is restarting. Both paths use the same rule
    as OpenCode's own /api/session/active: a session is running when it is not
    archived and went busy after it last went idle.

Rate limiting
    One email per interval bucket (ten minutes by default), enforced through a
    bucket key in the state file, so a duplicated timer tick cannot double
    send. --force overrides the bucket for a manual dispatch.

Usage
    mp_opencode_progress_reporter.py --send        Send if this bucket is due
    mp_opencode_progress_reporter.py --force       Send now, ignoring the bucket
    mp_opencode_progress_reporter.py --dry-run     Build both reports, no email
    mp_opencode_progress_reporter.py --status      Show configuration & history
    mp_opencode_progress_reporter.py --print       Print the text report only
"""

import os
import sys
import re
import json
import time
import shutil
import socket
import sqlite3
import base64
import argparse
import platform
import subprocess
import textwrap
import fcntl
import urllib.error
import urllib.parse
import urllib.request
import smtplib
from datetime import datetime, timedelta
from pathlib import Path
from email import utils as email_utils
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from html import escape as html_escape

IS_MACOS = platform.system() == "Darwin"

HOME = Path.home()
DEFAULT_INTERVAL_MINUTES = 10
DEFAULT_MAX_SESSIONS = 12
DEFAULT_MAX_MESSAGES = 12
LOG_NAME = "MP_Reporter_Email.log"
STATE_NAME = "opencode_progress_state.json"


# ------------------------------------------------------------------------------
# Path / configuration resolution
# ------------------------------------------------------------------------------

def _repo_root() -> Path:
    """The MP Mix Manager checkout this script lives in (…/scripts/..)."""
    return Path(__file__).resolve().parent.parent


def resolve_reporter_dir() -> Path:
    """Directory holding email_config.env, data/, TTS_AUDIO/ and REPORTS/.

    MP_REPORTER is the installed reporter home used by the existing daily
    engine and the systemd units. A checkout that has not been installed into
    MP_REPORTER falls back to its own root so the module still works.
    """
    env = os.environ.get("MP_REPORTER_DIR")
    if env:
        return Path(env).expanduser()
    installed = HOME / "MP_REPORTER"
    if (installed / "email_config.env").is_file():
        return installed
    return _repo_root()


REPORTER_DIR = resolve_reporter_dir()
CONFIG_FILE = REPORTER_DIR / "email_config.env"
ALT_CONFIG_FILE = HOME / ".config" / "mp-reporter" / "email_config.env"
DATA_DIR = REPORTER_DIR / "data"
STATE_FILE = DATA_DIR / STATE_NAME
LOG_FILE = REPORTER_DIR / LOG_NAME
REPORTS_DIR = REPORTER_DIR / "REPORTS" / "OPENCODE_PROGRESS"
TTS_DIR = REPORTER_DIR / "TTS_AUDIO" / "OPENCODE_PROGRESS"

OPENCODE_STATE = Path(os.environ.get("MP_OPENCODE_STATE") or
                      (HOME / ".local" / "state" / "opencode" / "service.json"))
OPENCODE_DB = Path(os.environ.get("MP_OPENCODE_DB") or
                   (HOME / ".local" / "share" / "opencode" / "opencode.db"))


def log_message(level: str, msg: str):
    """Log to stdout and the shared MP Reporter email log."""
    line = "[%s] [%s] [opencode-progress] %s" % (
        datetime.now().isoformat(), level.upper(), msg)
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def load_config() -> dict:
    """Read email_config.env into a dict inside the same defaults as the
    daily engine, then overlay the OpenCode progress settings."""
    cfg = {
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
        # OpenCode progress briefing
        "OPENCODE_PROGRESS_ENABLED": "true",
        "OPENCODE_PROGRESS_INTERVAL_MINUTES": str(DEFAULT_INTERVAL_MINUTES),
        "OPENCODE_PROGRESS_RECIPIENTS": "",
        "OPENCODE_PROGRESS_SUBJECT_PREFIX": "OpenCode Agent Progress",
        "OPENCODE_PROGRESS_TTS_ENABLED": "true",
        "OPENCODE_PROGRESS_SEND_WHEN_IDLE": "true",
        "OPENCODE_PROGRESS_INCLUDE_TOOL_CALLS": "true",
        "OPENCODE_PROGRESS_MAX_SESSIONS": str(DEFAULT_MAX_SESSIONS),
        "OPENCODE_PROGRESS_MAX_MESSAGES": str(DEFAULT_MAX_MESSAGES),
        "OPENCODE_PROGRESS_ATTACH_TEXT": "true",
        "OPENCODE_PROGRESS_ATTACH_AUDIO": "true",
        "OPENCODE_PROGRESS_ALERT_SOUND": "true",
    }
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
    return cfg_int(cfg, "OPENCODE_PROGRESS_INTERVAL_MINUTES",
                   DEFAULT_INTERVAL_MINUTES)


def recipients(cfg: dict) -> list:
    raw = cfg.get("OPENCODE_PROGRESS_RECIPIENTS") or cfg.get("EMAIL_RECIPIENTS") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


# ------------------------------------------------------------------------------
# OpenCode session discovery (HTTP API first, read-only DB fallback)
# ------------------------------------------------------------------------------

def _auth_header(password: str) -> dict:
    token = base64.b64encode(("opencode:" + password).encode()).decode()
    return {"Authorization": "Basic " + token}


def opencode_endpoint() -> dict:
    """URL + auth for the running OpenCode service, or {} when unavailable."""
    try:
        info = json.loads(OPENCODE_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    url = str(info.get("url") or "").rstrip("/")
    password = str(info.get("password") or "")
    if not url or not password:
        return {}
    return {"url": url, "headers": _auth_header(password)}


def _api_get(endpoint: dict, path: str, params: dict = None, timeout: float = 10.0):
    url = endpoint["url"] + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=endpoint["headers"])
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _snippet(text: str, limit: int = 240) -> str:
    """Collapse whitespace so a markdown reply fits on one report line."""
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _tool_summary(part: dict) -> str:
    """One-line description of a tool call part."""
    name = str(part.get("name") or "tool")
    state = part.get("state") or {}
    payload = state.get("input") or {}
    detail = ""
    if isinstance(payload, dict):
        for key in ("command", "filePath", "path", "pattern", "url", "query"):
            if payload.get(key):
                detail = str(payload[key]).splitlines()[0]
                break
    status = str(state.get("status") or "")
    text = name
    if detail:
        text += ": " + _snippet(detail, 90)
    if status and status not in ("completed", "running"):
        text += " [" + status + "]"
    return text


def _message_progress(message: dict) -> dict:
    """Turn one OpenCode message into the fields the report needs."""
    texts, tools, reasoning = [], [], []
    for part in message.get("content") or []:
        kind = part.get("type")
        if kind == "text" and part.get("text"):
            texts.append(part["text"])
        elif kind == "tool":
            tools.append(_tool_summary(part))
        elif kind == "reasoning" and part.get("text"):
            reasoning.append(part["text"])
    return {
        "id": message.get("id") or "",
        "type": message.get("type") or "",
        "created": int((message.get("time") or {}).get("created") or 0),
        "text": "\n".join(texts).strip(),
        "tools": tools,
        "reasoning": "\n".join(reasoning).strip(),
        "agent": message.get("agent") or "",
    }


def _live_sessions_via_api(endpoint: dict) -> list:
    """Running sessions plus their newest messages, from the service API."""
    active = (_api_get(endpoint, "/api/session/active") or {}).get("data") or {}
    running_ids = [sid for sid, state in active.items()
                   if str((state or {}).get("type")) == "running"]
    if not running_ids:
        return []

    listing = (_api_get(endpoint, "/api/session",
                        {"limit": 200}) or {}).get("data") or []
    by_id = {item.get("id"): item for item in listing}

    sessions = []
    for sid in running_ids:
        info = by_id.get(sid) or {}
        if not info:
            try:
                info = (_api_get(endpoint, "/api/session/" + sid) or {}).get("data") or {}
            except Exception:
                info = {}
        messages = []
        try:
            payload = _api_get(endpoint, "/api/session/%s/message" % sid,
                               {"limit": 40, "order": "desc"})
            messages = [_message_progress(m) for m in (payload.get("data") or [])]
        except Exception as exc:
            log_message("WARN", "could not read messages for %s: %s" % (sid, exc))
        sessions.append(_session_record(sid, info, messages))
    return sessions


def _session_record(sid: str, info: dict, messages: list) -> dict:
    timing = info.get("time") or {}
    tokens = info.get("tokens") or {}
    parent_id = info.get("parentID")
    return {
        "id": sid,
        "title": info.get("title") or sid,
        "agent": info.get("agent") or "",
        "model": (info.get("model") or {}).get("id") or "",
        "parent_id": parent_id,
        "directory": (info.get("location") or {}).get("directory") or "",
        "created": int(timing.get("created") or 0),
        "updated": int(timing.get("updated") or 0),
        "busy": int(timing.get("busy") or 0),
        "tokens_in": int(tokens.get("input") or 0),
        "tokens_out": int(tokens.get("output") or 0),
        "messages": messages,
        "source": "api",
    }


def _db_rows(query: str, params: tuple = ()) -> list:
    """Read-only query against the OpenCode database (never creates it)."""
    uri = "file:%s?mode=ro" % urllib.parse.quote(str(OPENCODE_DB))
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _db_tables() -> set:
    try:
        return {row["name"] for row in _db_rows(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception:
        return set()


def _live_sessions_via_db() -> list:
    """Fallback for when the OpenCode service is down or restarting.

    A session is running when it was busy after it last went idle and is not
    archived. That is the rule OpenCode's own ``/api/session/active`` endpoint
    applies, so the fallback reports the same sessions as the live API.

    ``idle_outcome`` is deliberately not part of the test. It describes the
    *previous* idle and OpenCode leaves it set when a session starts another
    turn, so filtering on it hid every session resumed after a finished task.
    ``time_busy > time_idle`` already proves a turn is in flight.
    ``time_suspended`` is ignored for the same reason as before: OpenCode sets
    it on sessions that are alive but parked, and those still count.
    """
    tables = _db_tables()
    if "session_v2" in tables:
        session_table, message_table = "session_v2", "session_message"
    else:
        session_table, message_table = "session", "message"

    if session_table == "session_v2":
        extra = ("time_busy, time_idle, idle_outcome, "
                 "time_suspended, time_archived")
        activity = ("time_archived IS NULL "
                    "AND time_busy IS NOT NULL "
                    "AND (time_idle IS NULL OR time_busy > time_idle)")
    else:
        extra = "time_updated AS time_busy, NULL AS time_idle, NULL AS idle_outcome"
        activity = "time_archived IS NULL"

    stale_seconds = 2 * 60 * 60
    cutoff_ms = int(time.time() * 1000) - stale_seconds * 1000
    rows = _db_rows(
        "SELECT id, parent_id, title, agent, model, directory, "
        "       tokens_input, tokens_output, time_created, time_updated, "
        "       %s FROM %s "
        "WHERE %s AND time_updated >= ? "
        "ORDER BY time_updated DESC" % (extra, session_table, activity),
        (cutoff_ms,))
    sessions = []
    for row in rows:
        try:
            model = json.loads(row["model"]) if row["model"] else {}
        except Exception:
            model = {}
        info = {
            "id": row["id"],
            "title": row["title"],
            "agent": row["agent"],
            "model": model,
            "parentID": row["parent_id"],
            "location": {"directory": row["directory"]},
            "tokens": {"input": row["tokens_input"],
                       "output": row["tokens_output"]},
            "time": {"created": row["time_created"],
                     "updated": row["time_updated"],
                     "busy": row["time_busy"]},
        }
        if message_table == "session_message":
            query = ("SELECT type, data FROM session_message "
                     "WHERE session_id = ? ORDER BY seq DESC LIMIT 40")
        else:
            query = ("SELECT data, 'assistant' AS type FROM message "
                     "WHERE session_id = ? ORDER BY time_created DESC LIMIT 40")
        try:
            parts = _db_rows(query, (row["id"],))
        except Exception:
            parts = []
        messages = []
        for part in parts:
            try:
                data = json.loads(part["data"])
            except Exception:
                continue
            data.setdefault("type", part["type"])
            messages.append(_message_progress(data))
        record = _session_record(row["id"], info, messages)
        record["source"] = "database"
        sessions.append(record)
    return sessions


def collect_running_sessions(limit: int) -> tuple:
    """Return (sessions, source, note) for every running OpenCode session."""
    endpoint = opencode_endpoint()
    if endpoint:
        try:
            sessions = _live_sessions_via_api(endpoint)
            return sessions[:limit], "opencode api", ""
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as exc:
            log_message("WARN", "OpenCode API unreachable (%s); using database" % exc)
            note = "OpenCode API was unreachable; session data came from the database."
    else:
        note = "OpenCode service registration was missing; session data came from the database."
    try:
        sessions = _live_sessions_via_db()
        return sessions[:limit], "opencode.db", note
    except Exception as exc:
        log_message("ERROR", "could not read OpenCode sessions: %s" % exc)
        return [], "unavailable", "Neither the OpenCode API nor its database could be read."


# ------------------------------------------------------------------------------
# Report composition
# ------------------------------------------------------------------------------

def _fmt_duration(ms: int) -> str:
    if ms <= 0:
        return "unknown"
    total = int(ms // 1000)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    if minutes:
        return "%dm %02ds" % (minutes, seconds)
    return "%ds" % seconds


def _fmt_clock(ms: int) -> str:
    if not ms:
        return "unknown"
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _session_activity(session: dict, window_start: float) -> dict:
    """Split a session's recent messages into new-since-last and latest."""
    messages = session.get("messages") or []
    new_messages = [m for m in messages if m["created"] / 1000.0 >= window_start]
    latest_text = ""
    latest_tools = []
    latest_at = 0
    for message in messages:
        if message["text"]:
            latest_text = message["text"]
            latest_at = message["created"]
            break
    if messages and messages[0].get("tools"):
        latest_tools = messages[0]["tools"]
    new_tools = []
    for message in new_messages:
        new_tools.extend(message.get("tools") or [])
    return {
        "new_messages": len(new_messages),
        "new_tool_calls": len(new_tools),
        "tools": latest_tools,
        "new_tools": new_tools,
        "latest_text": latest_text,
        "latest_at": latest_at,
        "last_prompt": next((m["text"] for m in reversed(messages)
                             if m["type"] == "user" and m["text"]), ""),
    }


def _collapse_tools(tools: list, limit: int = 5) -> list:
    """Group identical tool descriptions so a shell storm reads as one line."""
    counts, order = {}, []
    for tool in tools:
        if tool not in counts:
            counts[tool] = 0
            order.append(tool)
        counts[tool] += 1
    lines = []
    for tool in order[:limit]:
        count = counts[tool]
        lines.append(tool if count == 1 else "%s (x%d)" % (tool, count))
    if len(order) > limit:
        lines.append("… %d more tool call(s)" % (len(order) - limit))
    return lines


RECORD_DEFAULTS = {
    "id": "",
    "short_id": "",
    "title": "Untitled session",
    "parent_title": "",
    "agent": "agent",
    "model": "",
    "directory": "",
    "busy_seconds": 0,
    "last_activity": "unknown",
    "tokens_in": 0,
    "tokens_out": 0,
    "new_messages": 0,
    "new_tool_calls": 0,
    "latest_text": "",
    "latest_tools": [],
    "new_tools": [],
    "last_prompt": "",
}


def normalize_record(session: dict) -> dict:
    """Fill a report record's missing fields.

    The renderers are also used on partially built records (tests, ad-hoc
    callers), so they must never raise KeyError over a missing counter.
    """
    record = dict(RECORD_DEFAULTS)
    record.update({k: v for k, v in session.items() if v is not None})
    if not record["short_id"] and record["id"]:
        record["short_id"] = record["id"][:12]
    return record


def build_report_data(cfg: dict, window_start: float, window_end: float) -> dict:
    """Collect every running session and reduce it to report-ready records."""
    limit = cfg_int(cfg, "OPENCODE_PROGRESS_MAX_SESSIONS", DEFAULT_MAX_SESSIONS)
    msg_limit = cfg_int(cfg, "OPENCODE_PROGRESS_MAX_MESSAGES", DEFAULT_MAX_MESSAGES)
    sessions, source, note = collect_running_sessions(limit)

    titles = {s["id"]: s["title"] for s in sessions}
    records = []
    for session in sessions:
        activity = _session_activity(session, window_start)
        elapsed_ms = 0
        if session.get("busy"):
            elapsed_ms = int(time.time() * 1000) - int(session["busy"])
        records.append({
            "id": session["id"],
            "short_id": session["id"][:12],
            "title": session["title"],
            "parent_title": titles.get(session.get("parent_id")) or "",
            "agent": session.get("agent") or "agent",
            "model": session.get("model") or "",
            "directory": session.get("directory") or "",
            "busy_seconds": max(0, elapsed_ms // 1000),
            "last_activity": _fmt_clock(session.get("updated") or 0),
            "tokens_in": session.get("tokens_in") or 0,
            "tokens_out": session.get("tokens_out") or 0,
            "new_messages": activity["new_messages"],
            "new_tool_calls": activity["new_tool_calls"],
            "latest_text": activity["latest_text"],
            "latest_tools": activity["tools"][:msg_limit],
            "new_tools": activity["new_tools"],
            "last_prompt": activity["last_prompt"],
        })

    return {
        "generated": datetime.fromtimestamp(window_end),
        "window_start": datetime.fromtimestamp(window_start),
        "window_end": datetime.fromtimestamp(window_end),
        "window_minutes": max(1, int(round((window_end - window_start) / 60.0))),
        "hostname": socket.gethostname() or "workstation",
        "source": source,
        "note": note,
        "max_sessions": limit,
        "max_messages": msg_limit,
        "sessions": records,
    }


def build_text_report(data: dict) -> str:
    """The TEXT report: plain-text body, also written out as a .txt file."""
    stamp = data["generated"].strftime("%A %d %B %Y at %H:%M:%S")
    lines = [
        "MP REPORTER - OPENCODE AGENT PROGRESS BRIEFING",
        "=" * 68,
        "Generated : " + stamp,
        "Workstation: " + data["hostname"],
        "Window    : last %d minute(s), %s to %s" % (
            data["window_minutes"],
            data["window_start"].strftime("%H:%M:%S"),
            data["window_end"].strftime("%H:%M:%S")),
        "Source    : " + data["source"],
        "Running   : %d agent session(s)" % len(data["sessions"]),
    ]
    if data.get("note"):
        lines.append("Notice    : " + data["note"])
    lines.append("")

    sessions = [normalize_record(s) for s in data["sessions"]]
    if not sessions:
        lines.append("No OpenCode agent sessions are running right now.")
        lines.append("")
        lines.append("The next briefing follows in %d minutes."
                     % data["window_minutes"])
        return "\n".join(lines) + "\n"

    for index, session in enumerate(sessions, start=1):
        lines.append("-" * 68)
        lines.append("%d. %s" % (index, session["title"]))
        lines.append("-" * 68)
        lines.append("   Session ID : " + session["short_id"] + "…")
        if session["parent_title"]:
            lines.append("   Subagent of: " + session["parent_title"])
        lines.append("   Agent      : %s (%s)" % (session["agent"], session["model"] or "model unknown"))
        if session["directory"]:
            lines.append("   Directory  : " + session["directory"])
        lines.append("   Running for: %s" % _fmt_duration(session["busy_seconds"] * 1000))
        lines.append("   Last active: " + session["last_activity"])
        lines.append("   Tokens     : %s in / %s out"
                     % (f"{session['tokens_in']:,}", f"{session['tokens_out']:,}"))
        lines.append("   This window: %d new message(s), %d tool call(s)"
                     % (session["new_messages"], session["new_tool_calls"]))
        if not session["new_messages"] and not session["new_tool_calls"]:
            lines.append("   Status     : quiet this window (long task or waiting)")
        if session["new_tools"]:
            lines.append("   Recent work:")
            for tool in _collapse_tools(session["new_tools"]):
                lines.append("     - " + tool)
        if session["latest_text"]:
            lines.append("   Latest report:")
            for chunk in textwrap.wrap(_snippet(session["latest_text"], 480), 96):
                lines.append("     " + chunk)
        elif session["last_prompt"]:
            lines.append("   Working on:")
            for chunk in textwrap.wrap(_snippet(session["last_prompt"], 240), 96):
                lines.append("     " + chunk)
        lines.append("")

    lines.append("=" * 68)
    lines.append("Text report attached. Audio briefing attached in the Stephen")
    lines.append("Hawking Matrix voice. Sent by MP Reporter.")
    return "\n".join(lines) + "\n"


def build_speech_script(data: dict) -> str:
    """Short spoken script for the Stephen Hawking audio report.

    The audio names as many sessions as the text report lists. It used to stop
    at six regardless of the configured cap, so with seven or more sessions
    running the spoken briefing silently omitted some of them even though the
    email listed every one.
    """
    sessions = [normalize_record(s) for s in data["sessions"]]
    count = len(sessions)
    limit = int(data.get("max_sessions") or DEFAULT_MAX_SESSIONS)
    named = sessions[:limit] if limit > 0 else sessions
    when = data["generated"].strftime("%H:%M")
    if not sessions:
        return ("Greetings Mathew. This is Stephen Hawking with your OpenCode "
                "agent progress briefing at %s. No OpenCode agent sessions are "
                "currently running. The next progress briefing follows in %d "
                "minutes. End of transmission."
                % (when, data["window_minutes"]))

    parts = ["Greetings Mathew. This is Stephen Hawking with your OpenCode "
             "agent progress briefing at %s. %d agent session%s %s running, "
             "and here is the progress from the last %d minutes."
             % (when, count, "" if count == 1 else "s",
                "is" if count == 1 else "are", data["window_minutes"])]

    for index, session in enumerate(named, start=1):
        heading = session["title"]
        if session["parent_title"]:
            heading += ", a subagent of " + session["parent_title"]
        sentence = "Session %d, %s, running for %s" % (
            index, heading, _fmt_duration(session["busy_seconds"] * 1000))
        sentence += ", with %d new message%s and %d tool call%s." % (
            session["new_messages"], "" if session["new_messages"] == 1 else "s",
            session["new_tool_calls"], "" if session["new_tool_calls"] == 1 else "s")
        if session["latest_text"]:
            sentence += " Its latest words: " + _snippet(session["latest_text"], 180)
        elif session["last_prompt"]:
            sentence += " It is working on: " + _snippet(session["last_prompt"], 160)
        parts.append(sentence)

    if count > len(named):
        parts.append("There %s %d further session%s running."
                     % ("is" if count - len(named) == 1 else "are",
                        count - len(named),
                        "" if count - len(named) == 1 else "s"))
    parts.append("The complete text report is attached to your email. "
                 "End of transmission.")
    return " ".join(parts)


def build_html_report(data: dict) -> str:
    """Dark dashboard HTML matching the daily briefing's styling."""
    rows = []
    for raw in data["sessions"]:
        session = normalize_record(raw)
        tools = "<br/>".join(
            html_escape(item) for item in _collapse_tools(session["new_tools"], 4)) or "-"
        latest = (html_escape(_snippet(session["latest_text"], 320))
                  or html_escape(_snippet(session["last_prompt"], 200)) or "-")
        sub = ("<div style=\"color:#64748b;font-size:11px;\">subagent of %s</div>"
               % html_escape(session["parent_title"])) if session["parent_title"] else ""
        rows.append(
            "<tr>"
            "<td style=\"padding:10px;color:#e2e8f0;font-size:12px;\"><b>%s</b>%s"
            "<div style=\"color:#64748b;font-family:monospace;font-size:11px;\">%s…</div></td>"
            "<td style=\"padding:10px;color:#cbd5e1;font-size:12px;\">%s<br/>"
            "<span style=\"color:#64748b;\">%s</span></td>"
            "<td style=\"padding:10px;color:#cbd5e1;font-size:12px;\">%s<br/>"
            "<span style=\"color:#38bdf8;\">%s new msg / %s calls</span><br/>"
            "<span style=\"color:#64748b;font-size:11px;\">%s</span></td>"
            "<td style=\"padding:10px;color:#cbd5e1;font-size:11px;\">%s</td>"
            "</tr>" % (
                html_escape(session["title"]), sub,
                html_escape(session["short_id"]),
                html_escape(session["agent"]),
                html_escape(session["model"] or "model unknown"),
                _fmt_duration(session["busy_seconds"] * 1000),
                session["new_messages"], session["new_tool_calls"],
                latest, tools))

    if not rows:
        rows.append(
            "<tr><td colspan=\"4\" style=\"padding:18px;color:#94a3b8;"
            "font-size:13px;\">No OpenCode agent sessions are running right now.</td></tr>")

    note_html = ("<div style=\"color:#fbbf24;font-size:12px;margin-bottom:12px;\">"
                 "Notice: %s</div>" % html_escape(data["note"])) if data.get("note") else ""

    return """<html><body style="margin:0;padding:0;background-color:#0b1120;">
<table width="100%%" cellpadding="0" cellspacing="0" style="background-color:#0b1120;padding:24px;">
<tr><td align="center">
<table width="720" cellpadding="0" cellspacing="0" style="background-color:#0f172a;border:1px solid #1e293b;border-radius:12px;overflow:hidden;">
  <tr><td style="background-color:#0c1a2e;padding:22px 28px;border-bottom:1px solid #1e293b;">
    <div style="color:#06b6d4;font-size:11px;letter-spacing:2px;font-weight:700;">MP REPORTER</div>
    <div style="color:#f8fafc;font-size:20px;font-weight:700;margin-top:4px;">OpenCode Agent Progress Briefing</div>
    <div style="color:#94a3b8;font-size:12px;margin-top:6px;">%(stamp)s &bull; %(host)s &bull; last %(mins)d minutes</div>
  </td></tr>
  <tr><td style="padding:22px 28px;">
    %(note)s
    <div style="color:#38bdf8;font-size:33px;font-weight:700;">%(count)d</div>
    <div style="color:#94a3b8;font-size:12px;margin-bottom:18px;">agent session(s) running &bull; source: %(source)s</div>
    <table width="100%%" cellpadding="0" cellspacing="0" style="font-size:12px;background-color:#0b1120;border:1px solid #1e293b;border-radius:8px;">
      <tr style="background-color:#1e293b;color:#94a3b8;">
        <th align="left" style="padding:8px 10px;">Session</th>
        <th align="left" style="padding:8px 10px;">Agent</th>
        <th align="left" style="padding:8px 10px;">Progress</th>
        <th align="left" style="padding:8px 10px;">Recent work</th>
      </tr>
      %(rows)s
    </table>
    <div style="background-color:#0c1a2e;border:1px solid #0369a1;border-radius:8px;padding:14px;margin-top:18px;">
      <div style="color:#38bdf8;font-size:12px;font-weight:700;margin-bottom:4px;">Attachments</div>
      <div style="color:#cbd5e1;font-size:12px;line-height:1.6;">
        1. <b>%(text_name)s</b> &mdash; the full text progress report.<br/>
        2. <b>%(audio_name)s</b> &mdash; the audio briefing in the Stephen Hawking Matrix voice.
      </div>
    </div>
  </td></tr>
  <tr><td style="background-color:#090d16;padding:14px 28px;color:#64748b;font-size:11px;text-align:center;">
    Dreamworlds Productions &bull; MP Reporter &bull; dispatched every %(mins)d minutes
  </td></tr>
</table>
</td></tr></table>
</body></html>""" % {
        "stamp": data["generated"].strftime("%A %d %B %Y, %H:%M"),
        "host": data["hostname"],
        "mins": data["window_minutes"],
        "note": note_html,
        "count": len(data["sessions"]),
        "source": data["source"],
        "rows": "\n".join(rows),
        "text_name": data.get("text_name", "progress report"),
        "audio_name": data.get("audio_name", "audio briefing"),
    }


# ------------------------------------------------------------------------------
# Audio report (Stephen Hawking Matrix voice)
# ------------------------------------------------------------------------------

def find_espeak() -> str:
    for name in ("espeak-ng", "espeak"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _piper_jenny_model() -> Path:
    """Locate the local Piper Jenny model (offline British voice)."""
    import os as _os
    for d in (
        Path.home() / "voice-ai" / "voices",
        Path.home() / ".local" / "share" / "piper-voices",
        Path.home() / "MP_REPORTER" / "share" / "voices",
        Path("/usr/share/piper-voices"),
    ):
        try:
            if not d.is_dir():
                continue
            for entry in sorted(d.iterdir()):
                if entry.suffix == ".onnx" and "jenny" in entry.name.lower():
                    return entry
        except Exception:
            continue
    return None


def synthesize_piper_jenny(text: str, out_prefix: Path) -> dict:
    """Render the briefing with the local Piper Jenny voice (offline, no VRAM).

    Returns the same dict shape as synthesize_audio so callers do not care
    which engine produced the file.
    """
    model = _piper_jenny_model()
    if model is None:
        return {"mp3": None, "wav": None, "size_kb": 0}
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    wav_path = out_prefix.with_suffix(".wav")
    mp3_path = out_prefix.with_suffix(".mp3")
    try:
        from piper import PiperVoice
        import wave as _wave
        pv = PiperVoice.load(str(model))
        with _wave.open(str(wav_path), "wb") as wf:
            pv.synthesize_wav(text, wf)
    except Exception as exc:
        log_message("WARN", "Piper Jenny synthesis failed: %s" % exc)
        return {"mp3": None, "wav": None, "size_kb": 0}

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and wav_path.exists():
        try:
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(wav_path),
                            "-codec:a", "libmp3lame", "-b:a", "32k",
                            "-ac", "1", str(mp3_path)],
                           check=True, capture_output=True, timeout=300)
        except Exception:
            try:
                shutil.copy2(wav_path, mp3_path)
            except Exception:
                pass
    else:
        try:
            shutil.copy2(wav_path, mp3_path)
        except Exception:
            pass

    size_kb = round(mp3_path.stat().st_size / 1024, 1) if mp3_path.exists() else 0
    return {
        "mp3": mp3_path if mp3_path.exists() else None,
        "wav": None,
        "size_kb": size_kb,
    }


def synthesize_audio(text: str, out_prefix: Path) -> dict:
    """Piper Jenny first (offline), then espeak-ng -> Hawking filter -> MP3."""
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    piper_result = synthesize_piper_jenny(text, out_prefix)
    if piper_result.get("mp3"):
        return piper_result

    raw_wav = out_prefix.with_name(out_prefix.name + "_raw.wav")
    wav_path = out_prefix.with_suffix(".wav")
    mp3_path = out_prefix.with_suffix(".mp3")
    espeak = find_espeak()
    ffmpeg = shutil.which("ffmpeg")

    if not espeak:
        log_message("WARN", "no espeak-ng/espeak found; audio report skipped")
        return {"mp3": None, "wav": None, "size_kb": 0}

    text_file = out_prefix.with_suffix(".txt")
    text_file.write_text(text, encoding="utf-8")
    try:
        subprocess.run([espeak, "-v", "en-us+m3", "-s", "110", "-p", "19",
                        "-f", str(text_file), "-w", str(raw_wav)],
                       check=True, capture_output=True, timeout=300)
    except Exception as exc:
        log_message("ERROR", "espeak synthesis failed: %s" % exc)
        return {"mp3": None, "wav": None, "size_kb": 0}

    if ffmpeg and raw_wav.exists():
        chain = ("bass=g=12:f=95:w=0.6, equalizer=f=2400:t=q:w=1.2:g=2, "
                 "aecho=0.8:0.7:50|100:0.3|0.2")
        try:
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(raw_wav),
                            "-af", chain, str(wav_path)],
                           check=True, capture_output=True, timeout=300)
        except Exception:
            shutil.copy2(raw_wav, wav_path)
        # Speech at 32 kbps mono is transparent enough for a briefing and keeps
        # a ten-minute cadence from turning into a mailbox of megabyte files.
        try:
            subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(wav_path),
                            "-codec:a", "libmp3lame", "-b:a", "32k",
                            "-ac", "1", str(mp3_path)],
                           check=True, capture_output=True, timeout=300)
        except Exception:
            shutil.copy2(wav_path, mp3_path)
    elif raw_wav.exists():
        shutil.copy2(raw_wav, wav_path)
        shutil.copy2(raw_wav, mp3_path)

    # Only the MP3 is archived. A 10-minute cadence would otherwise leave
    # several hundred megabytes of intermediate WAVs per day in TTS_AUDIO.
    for transient in (raw_wav, wav_path):
        try:
            transient.unlink()
        except Exception:
            pass

    size_kb = round(mp3_path.stat().st_size / 1024, 1) if mp3_path.exists() else 0
    return {
        "mp3": mp3_path if mp3_path.exists() else None,
        "wav": None,
        "size_kb": size_kb,
    }


def prune_archives(keep: int = 288):
    """Keep the newest N briefings and drop older text/audio pairs.

    Default 288 files is two days at a ten-minute cadence, which is enough to
    review the day without letting the reporter grow without bound.
    """
    for directory, pattern in ((REPORTS_DIR, "MP_Reporter_OpenCode_Progress_*.txt"),
                               (TTS_DIR, "MP_Reporter_OpenCode_Progress_Audio_*")):
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


def play_confirmation_alert(sound_file: str):
    """Short audible confirmation once SMTP accepts the message.

    Waits for the shared speech lock first: the MP Audio Responder may be in the
    middle of a long narration, and a chime on top of it is exactly the overlap
    this lock exists to prevent. A short timeout means a stuck announcement can
    never block the email pipeline.
    """
    if IS_MACOS:
        candidate = Path("/System/Library/Sounds/Glass.aiff")
        if candidate.exists():
            try:
                subprocess.run(["afplay", str(candidate)], timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass
    path = Path(sound_file or "/usr/share/sounds/freedesktop/stereo/complete.oga")
    if not path.exists():
        return
    lock = _speech_lock(timeout=120.0)
    for player, args in (("pw-play", []), ("paplay", []), ("aplay", ["-q"])):
        binary = shutil.which(player)
        if not binary:
            continue
        try:
            with lock:
                subprocess.run([binary] + args + [str(path)], timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue


def _speech_lock(timeout: float = 900.0):
    """The machine-wide lock that allows one announcement at a time.

    Returns a context manager that is a no-op when the helper is unavailable,
    so a missing file never breaks the email path.
    """
    import contextlib
    import importlib.util as _ilu
    candidates = []
    env = os.environ.get("MP_SPEECH_LOCK_MODULE")
    if env:
        candidates.append(Path(env))
    root = _repo_root()
    candidates.append(root / "scripts" / "mp_speech_lock.py")
    for cand in candidates:
        try:
            if not cand.is_file():
                continue
            if "mp_speech_lock" not in sys.modules:
                spec = _ilu.spec_from_file_location("mp_speech_lock", cand)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)   # type: ignore[union-attr]
                sys.modules["mp_speech_lock"] = mod
            return sys.modules["mp_speech_lock"].SpeechLock(timeout=timeout)
        except Exception:
            continue
    return contextlib.nullcontext()


# ------------------------------------------------------------------------------
# State / rate limiting
# ------------------------------------------------------------------------------

def load_state() -> dict:
    default = {
        "last_bucket": None,
        "last_sent_timestamp": None,
        "total_emails_sent": 0,
        "last_sessions": [],
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
    end = time.time()
    try:
        last = datetime.fromisoformat(str(state.get("last_sent_timestamp")))
        start = last.timestamp()
        # Never claim a window older than one interval; after downtime that
        # would report hours of history as if it were the last ten minutes.
        if start < end - (minutes * 60 * 4):
            start = end - minutes * 60
    except Exception:
        start = end - minutes * 60
    if start >= end:
        start = end - minutes * 60
    return start, end


# ------------------------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------------------------

def execute_dispatch(force: bool = False, dry_run: bool = False) -> bool:
    cfg = load_config()
    if not truthy(cfg.get("EMAIL_REPORTS_ENABLED"), True):
        log_message("INFO", "email reporting is disabled in email_config.env")
        return False
    if not truthy(cfg.get("OPENCODE_PROGRESS_ENABLED"), True):
        log_message("INFO", "OpenCode progress briefings are disabled")
        return False

    minutes = interval_minutes(cfg)

    # A manual --force can land on top of a timer tick; one dispatch at a time
    # keeps the bucket and the state file honest. The lock is released by the
    # kernel when the process exits, so a crash never wedges the timer.
    lock_path = STATE_FILE.parent / "opencode_progress.lock"
    lock_handle = None
    if not dry_run:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = open(lock_path, "w")
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, BlockingIOError, OSError):
            if lock_handle is not None:
                lock_handle.close()
            log_message("INFO", "another progress briefing is already running; skipping")
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
    if not bucket_due(state, minutes, force):
        log_message("INFO", "this %d-minute window has already been reported; skipping"
                    % minutes)
        return True

    if truthy(cfg.get("OPENCODE_PROGRESS_SEND_WHEN_IDLE"), True) is False:
        current, _source, _note = collect_running_sessions(1)
        if not current:
            log_message("INFO", "no running sessions and idle briefings are off; skipping")
            return True
    start, end = window_bounds(state, minutes)
    data = build_report_data(cfg, start, end)
    log_message("INFO", "collected %d running session(s) from %s"
                % (len(data["sessions"]), data["source"]))

    stamp = datetime.fromtimestamp(end).strftime("%Y-%m-%d_%H-%M-%S")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    text_path = REPORTS_DIR / ("MP_Reporter_OpenCode_Progress_%s.txt" % stamp)

    text_report = build_text_report(data)
    text_path.write_text(text_report, encoding="utf-8")
    data["text_name"] = text_path.name

    audio = {"mp3": None, "wav": None, "size_kb": 0}
    if truthy(cfg.get("OPENCODE_PROGRESS_TTS_ENABLED"), True):
        script = build_speech_script(data)
        audio_path = TTS_DIR / ("MP_Reporter_OpenCode_Progress_Audio_%s" % stamp)
        log_message("INFO", "synthesizing Stephen Hawking audio briefing…")
        audio = synthesize_audio(script, audio_path)
        if audio["mp3"]:
            log_message("OK", "audio report ready: %s (%s KB)"
                        % (audio["mp3"].name, audio["size_kb"]))
        else:
            audio["mp3"] = None
    data["audio_name"] = audio["mp3"].name if audio["mp3"] else "(audio unavailable)"

    html_report = build_html_report(data)
    to_list = recipients(cfg)
    subject = "%s — %s (%d running)" % (
        cfg.get("OPENCODE_PROGRESS_SUBJECT_PREFIX") or "OpenCode Agent Progress",
        datetime.fromtimestamp(end).strftime("%d %b %Y %H:%M"),
        len(data["sessions"]))

    msg = MIMEMultipart("mixed")
    msg["From"] = "%s <%s>" % (cfg.get("EMAIL_SENDER_NAME", "MP Reporter"),
                               cfg.get("EMAIL_SENDER_ADDR") or cfg.get("SMTP_USER"))
    msg["To"] = ", ".join(to_list)
    msg["Date"] = email_utils.formatdate(localtime=True)
    msg["Subject"] = subject

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text_report, "plain", "utf-8"))
    alternative.attach(MIMEText(html_report, "html", "utf-8"))
    msg.attach(alternative)

    if truthy(cfg.get("OPENCODE_PROGRESS_ATTACH_TEXT"), True):
        attachment = MIMEBase("text", "plain")
        attachment.set_payload(text_report.encode("utf-8"))
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment",
                              filename=text_path.name)
        msg.attach(attachment)

    if audio["mp3"] and truthy(cfg.get("OPENCODE_PROGRESS_ATTACH_AUDIO"), True):
        with open(audio["mp3"], "rb") as fh:
            audio_mime = MIMEBase("audio", "mpeg")
            audio_mime.set_payload(fh.read())
        encoders.encode_base64(audio_mime)
        audio_mime.add_header("Content-Disposition", "attachment",
                              filename=audio["mp3"].name)
        msg.attach(audio_mime)

    if dry_run:
        log_message("OK", "[DRY-RUN] text=%s audio=%s sessions=%d recipients=%s"
                    % (text_path.name, data["audio_name"], len(data["sessions"]),
                       ", ".join(to_list) or "(none)"))
        print()
        print(text_report)
        return True

    if not to_list:
        log_message("ERROR", "no recipients configured in email_config.env")
        return False

    password = (cfg.get("SMTP_PASSWORD") or "").strip()
    if not password:
        log_message("ERROR", "SMTP password is not configured; run mp_email_manager.sh configure-password")
        return False

    host = cfg.get("SMTP_HOST") or "smtp.gmail.com"
    port = cfg_int(cfg, "SMTP_PORT", 587)
    user = cfg.get("SMTP_USER") or cfg.get("EMAIL_SENDER_ADDR")
    use_ssl = truthy(cfg.get("SMTP_USE_SSL"), False) or port == 465

    try:
        import ssl
        context = ssl.create_default_context()
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, context=context, timeout=40)
        else:
            server = smtplib.SMTP(host, port, timeout=40)
        server.ehlo()
        if not use_ssl and truthy(cfg.get("SMTP_USE_TLS"), True):
            server.starttls(context=context)
            server.ehlo()
        server.login(user, password)
        server.sendmail(user, to_list, msg.as_string())
        server.quit()
    except Exception as exc:
        text = str(exc)
        if "535" in text or "BadCredentials" in text or "not accepted" in text:
            log_message("ERROR", "SMTP rejected the credentials; a Google App "
                                 "Password is required for Gmail")
        else:
            log_message("ERROR", "SMTP send failed (%s): %s" % (host, exc))
        return False

    log_message("OK", "progress briefing sent to %s (%d session(s), %s)"
                % (", ".join(to_list), len(data["sessions"]), data["audio_name"]))

    if truthy(cfg.get("OPENCODE_PROGRESS_ALERT_SOUND"), True):
        play_confirmation_alert(cfg.get("CONFIRMATION_ALERT_SOUND"))

    state["last_bucket"] = current_bucket(minutes)
    state["last_sent_timestamp"] = datetime.fromtimestamp(end).isoformat()
    state["total_emails_sent"] = int(state.get("total_emails_sent") or 0) + 1
    state["last_sessions"] = [
        {"id": s["id"], "title": s["title"], "new_messages": s["new_messages"]}
        for s in data["sessions"]
    ]
    history = state.get("history") or []
    history.append({
        "timestamp": datetime.now().isoformat(),
        "sessions": len(data["sessions"]),
        "text_file": text_path.name,
        "audio_file": audio["mp3"].name if audio["mp3"] else None,
        "recipients": to_list,
        "status": "sent",
    })
    state["history"] = history[-200:]
    save_state(state)
    prune_archives()
    return True


def print_status():
    cfg = load_config()
    state = load_state()
    minutes = interval_minutes(cfg)
    endpoint = opencode_endpoint()
    due = bucket_due(state, minutes)
    print("=" * 70)
    print("     MP REPORTER — OPENCODE AGENT PROGRESS BRIEFING STATUS")
    print("=" * 70)
    print("  Enabled:            %s" % (
        "YES" if truthy(cfg.get("OPENCODE_PROGRESS_ENABLED"), True) else "NO"))
    print("  Interval:           every %d minutes" % minutes)
    print("  Recipients:         %s" % (", ".join(recipients(cfg)) or "(none)"))
    print("  Text report:        %s" % (
        "attached" if truthy(cfg.get("OPENCODE_PROGRESS_ATTACH_TEXT"), True) else "body only"))
    print("  Audio report:       %s" % (
        "Stephen Hawking MP3" if truthy(cfg.get("OPENCODE_PROGRESS_TTS_ENABLED"), True)
        else "disabled"))
    print("  Idle briefings:     %s" % (
        "sent" if truthy(cfg.get("OPENCODE_PROGRESS_SEND_WHEN_IDLE"), True)
        else "suppressed"))
    print("  OpenCode service:   %s" % (endpoint.get("url") or "not running"))
    print("  Report directory:   %s" % REPORTS_DIR)
    print("  Audio directory:    %s" % TTS_DIR)
    print("-" * 70)
    print("  This window:        %s" % ("DUE" if due else "already sent"))
    print("  Last sent:          %s" % (state.get("last_sent_timestamp") or "never"))
    print("  Total emails sent:  %s" % state.get("total_emails_sent", 0))
    if endpoint:
        try:
            current, source, _note = collect_running_sessions(
                cfg_int(cfg, "OPENCODE_PROGRESS_MAX_SESSIONS", DEFAULT_MAX_SESSIONS))
            print("  Running sessions:   %d (via %s)" % (len(current), source))
            for session in current:
                print("      • %s [%s]" % (session["title"], session["id"][:12]))
        except Exception as exc:
            print("  Running sessions:   unavailable (%s)" % exc)
    print("=" * 70)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="MP Reporter OpenCode agent progress briefing "
                    "(text + Stephen Hawking audio, every 10 minutes)")
    parser.add_argument("--send", action="store_true",
                        help="send when the current interval bucket is due")
    parser.add_argument("--force", action="store_true",
                        help="send now, ignoring the interval bucket")
    parser.add_argument("--dry-run", action="store_true",
                        help="build both reports and print the text, without email")
    parser.add_argument("--status", action="store_true",
                        help="show configuration, schedule and running sessions")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the text report for the current window")
    args = parser.parse_args(argv)

    if args.status:
        print_status()
        return 0
    if args.print_only:
        cfg = load_config()
        minutes = interval_minutes(cfg)
        start, end = window_bounds(load_state(), minutes)
        print(build_text_report(build_report_data(cfg, start, end)))
        return 0
    if args.dry_run:
        return 0 if execute_dispatch(force=True, dry_run=True) else 1
    if args.force:
        return 0 if execute_dispatch(force=True) else 1
    return 0 if execute_dispatch(force=False) else 1


if __name__ == "__main__":
    sys.exit(main())
