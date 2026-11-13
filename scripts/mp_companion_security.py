#!/usr/bin/env python3
"""
MP Mix Manager - Web Companion request security.

The companion serves a music library, playback controls and an agent proxy to
every device on the local network. Until 0.5.1 it did so with no authentication
of any kind, and `/api/stream?file=` accepted any absolute path, so anyone on
the same Wi-Fi could read `config.env`, an SSH private key, or `/etc/passwd`
with a plain GET.

This module is the single place that decides whether a request is allowed:

* **Bearer token** - a per-install secret is generated on first run and stored
  mode 0600 next to the other companion state. Every request must present it
  (header, `?token=`, or the `mp_companion_token` cookie).
* **Archive containment** - a stream path must resolve inside one of the
  configured library roots and must carry an audio suffix. Symlinks are
  resolved before the check, so `archive/../../etc/passwd` and a planted
  symlink both fail.
* **LAN allowlist** - an optional CIDR list, so a workstation on a hotel or
  café network can refuse every device it does not know by name.
* **CSRF defence** - a custom `X-MP-Companion` header is required on mutating
  requests. A cross-origin form cannot set a header without a preflight, and
  the token makes the preflight fail.

Nothing here imports the companion, so it is cheap to unit-test on its own.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# Audio suffixes that may leave the workstation over HTTP. Deliberately a
# local constant rather than the full conversion-format registry: the phone
# only ever decodes a subset, and a hard allowlist cannot be widened by a
# mis-edited config file.
STREAMABLE_SUFFIXES = frozenset({
    ".flac", ".wav", ".wave", ".aif", ".aiff", ".caf", ".au", ".w64",
    ".mp3", ".m4a", ".alac", ".aac", ".ogg", ".oga", ".opus", ".wv",
    ".tta", ".mka", ".ac3", ".eac3", ".dts", ".mp2", ".voc", ".amr",
    ".wma", ".dsf", ".dff",
})

TOKEN_FILENAME = "companion.token"
TOKEN_BYTES = 32
TOKEN_ENV = "MP_COMPANION_TOKEN"
TOKEN_COOKIE = "mp_companion_token"
REQUIRED_HEADER = "X-MP-Companion"

# One year, so a phone that was trusted once does not have to re-enter the
# token after every reboot. The token itself is revocable by deleting the file.
TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

AUTH_EXEMPT_PATHS = frozenset({"/healthz"})
AUTH_EXEMPT_PREFIXES = ("/static/", "/assets/")


# ------------------------------------------------------------------------------
# TOKEN STORAGE
# ------------------------------------------------------------------------------

def token_path(config_dir: Path) -> Path:
    return Path(config_dir) / TOKEN_FILENAME


def _write_private(path: Path, value: str) -> None:
    """Write `value` so only the owner can read it.

    `write_text` then `chmod` leaves a short window where the file exists with
    the process umask (typically 0644, world-readable). Creating it 0600 first
    closes that window, which matters because this is the only secret guarding
    the whole library.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(tmp), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value.strip() + "\n")
    finally:
        try:
            os.chmod(str(tmp), 0o600)
        except OSError:
            pass
    tmp.replace(path)
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass


def load_token(config_dir: Path) -> str:
    """Read the stored token, or return "" when none has been created yet."""
    path = token_path(config_dir)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def ensure_token(config_dir: Path, rotate: bool = False) -> str:
    """Return the install's token, creating (or rotating) it when needed.

    An explicit `MP_COMPANION_TOKEN` in the environment always wins, so a
    container or systemd unit can inject the value without a writable config
    directory.
    """
    injected = (os.environ.get(TOKEN_ENV) or "").strip()
    if injected:
        return injected
    if not rotate:
        existing = load_token(config_dir)
        if existing:
            return existing
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _write_private(token_path(config_dir), token)
    return token


# ------------------------------------------------------------------------------
# AUTHENTICATION
# ------------------------------------------------------------------------------

def extract_presented_token(headers: Any, query: Optional[dict] = None) -> str:
    """Pull a token from the Authorization header, a query parameter or cookie.

    The header is checked first because a query parameter leaks into shell
    history, browser history and any `Referer` a third-party page receives.
    The query/cookie forms exist so a phone can be handed a bookmark URL once.
    """
    try:
        auth = (headers.get("Authorization") or "").strip()
    except Exception:
        auth = ""
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
        if candidate:
            return candidate
    if query:
        for key in ("token", "access_token"):
            values = query.get(key) or []
            if values and values[0]:
                return str(values[0])
    try:
        cookies = headers.get("Cookie") or ""
    except Exception:
        cookies = ""
    for part in cookies.split(";"):
        name, _, value = part.strip().partition("=")
        if name == TOKEN_COOKIE and value:
            return value.strip()
    return ""


def is_authorized(
    headers: Any,
    expected: str,
    path: str,
    query: Optional[dict] = None,
) -> bool:
    """True when this request may proceed.

    Comparison is constant-time so a remote client cannot recover the token a
    character at a time from response timing.
    """
    if not expected:
        # No token configured means the operator disabled auth on purpose
        # (e.g. an isolated lab network). The server logs a loud warning.
        return True
    if path in AUTH_EXEMPT_PATHS or path.startswith(AUTH_EXEMPT_PREFIXES):
        return True
    presented = extract_presented_token(headers, query)
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


def auth_challenge_headers(token: str, path: str = "/") -> List[Tuple[str, str]]:
    """Headers sent on a successful login so the phone remembers the token."""
    if not token:
        return []
    cookie = (
        f"{TOKEN_COOKIE}={token}; Path=/; Max-Age={TOKEN_COOKIE_MAX_AGE}; "
        "SameSite=Lax; HttpOnly"
    )
    return [("Set-Cookie", cookie)]


# ------------------------------------------------------------------------------
# CSRF
# ------------------------------------------------------------------------------

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def has_csrf_header(headers: Any) -> bool:
    """A cross-origin form cannot add a custom header without a preflight."""
    try:
        return bool((headers.get(REQUIRED_HEADER) or "").strip())
    except Exception:
        return False


def origin_is_acceptable(headers: Any) -> bool:
    """Reject a mutating request whose Origin is another web site.

    The dashboard is served by this same server, so a same-origin request has
    no Origin header, or one whose host matches the `Host` header. Comparing
    only the host (not the scheme) keeps a phone that reached us by IP working
    even behind a reverse proxy that terminates TLS.
    """
    try:
        origin = (headers.get("Origin") or "").strip()
        host = (headers.get("Host") or "").strip()
    except Exception:
        return True
    if not origin:
        return True
    if origin in ("null",):
        return False
    try:
        from urllib.parse import urlparse
        origin_host = urlparse(origin).netloc
    except Exception:
        return False
    if not origin_host:
        return False
    return origin_host.lower() == host.lower()


# ------------------------------------------------------------------------------
# NETWORK ALLOWLIST
# ------------------------------------------------------------------------------

def parse_cidr_list(raw: str) -> List[Any]:
    """Parse a comma/space separated CIDR list, ignoring unusable entries."""
    networks = []
    for chunk in (raw or "").replace(";", ",").split(","):
        for token in chunk.split():
            token = token.strip()
            if not token:
                continue
            try:
                networks.append(ipaddress.ip_network(token, strict=False))
            except ValueError:
                continue
    return networks


def client_ip(client_address: Any) -> str:
    try:
        host = client_address[0]
    except (TypeError, IndexError):
        return ""
    if not isinstance(host, str):
        return ""
    # An IPv4-mapped IPv6 address must be unwrapped before a 127.0.0.1/8
    # comparison, otherwise a loopback check on a dual-stack socket fails.
    if host.startswith("::ffff:"):
        host = host[7:]
    return host


def is_allowed_client(client_address: Any, networks: Sequence[Any]) -> bool:
    """True when the peer is inside the allowlist (an empty list allows all)."""
    if not networks:
        return True
    host = client_ip(client_address)
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for network in networks:
        try:
            if address in network:
                return True
        except TypeError:
            continue
    return False


# ------------------------------------------------------------------------------
# STREAM CONTAINMENT
# ------------------------------------------------------------------------------

def _is_within(candidate: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            if candidate == root or root in candidate.parents:
                return True
        except (OSError, ValueError):
            continue
    return False


def resolve_roots(roots: Iterable[Any]) -> List[Path]:
    """Resolve configured library roots once, dropping anything unusable."""
    resolved: List[Path] = []
    seen = set()
    for root in roots:
        try:
            candidate = Path(str(root)).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(candidate)
    return resolved


def safe_stream_path(
    file_path: Any,
    roots: Iterable[Any],
    suffixes: Iterable[str] = STREAMABLE_SUFFIXES,
) -> Optional[Path]:
    """Return the resolved path when it is safe to stream, otherwise None.

    This is the fix for the unauthenticated arbitrary-file read: the previous
    handler called `os.path.isfile()` on the raw query value and served
    whatever it named. Every rejection reason returns the same `None`, so the
    caller can answer 404 for "missing" and "forbidden" alike and a remote
    client cannot use the status code as a filesystem oracle.
    """
    if not file_path:
        return None
    try:
        candidate = Path(str(file_path)).expanduser()
    except (TypeError, ValueError):
        return None
    if not candidate.is_absolute():
        return None
    try:
        # `resolve` follows every symlink, so a link planted inside the
        # archive that points at ~/.ssh/id_ed25519 resolves outside the roots
        # and is rejected here rather than at open() time.
        candidate = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return None

    allowed = resolve_roots(roots)
    if not allowed:
        # With no configured library there is nothing legitimate to serve.
        # Failing closed keeps a companion started from the wrong directory
        # from exposing the whole filesystem.
        return None
    if not _is_within(candidate, allowed):
        return None
    try:
        suffix = candidate.suffix.lower()
    except (OSError, ValueError):
        return None
    allowed_suffixes = {str(s).lower() for s in suffixes}
    if suffix not in allowed_suffixes:
        return None
    try:
        if not candidate.is_file():
            return None
    except OSError:
        return None
    return candidate


# ------------------------------------------------------------------------------
# RESPONSE HARDENING
# ------------------------------------------------------------------------------

def security_headers(nonce: str = "") -> List[Tuple[str, str]]:
    """Baseline headers for every response.

    The dashboard is one self-contained page with inline script and style, so
    the CSP needs a nonce rather than `'self'` alone. `frame-ancestors 'none'`
    and `X-Frame-Options` stop a malicious page from framing the controls, and
    `Referrer-Policy: no-referrer` keeps a `?token=` bookmark from leaking to
    an outbound link the user taps.
    """
    script_src = f"'nonce-{nonce}'" if nonce else "'self'"
    csp = (
        "default-src 'self'; "
        f"script-src {script_src} 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return [
        ("Content-Security-Policy", csp),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
    ]


def validate_mutation(headers: Any) -> Optional[str]:
    """Return an error code when a mutating request must be refused."""
    if not has_csrf_header(headers):
        return "missing_csrf_header"
    if not origin_is_acceptable(headers):
        return "cross_origin"
    return None


def default_host() -> str:
    """Loopback unless the operator explicitly opts into LAN exposure.

    Binding 0.0.0.0 by default put an unauthenticated library on every
    coffee-shop Wi-Fi the laptop joined. Phone access is a deliberate act, so
    it now needs `WEB_COMPANION_HOST=0.0.0.0` (or `--host`).
    """
    configured = (os.environ.get("WEB_COMPANION_HOST") or "").strip()
    return configured or "127.0.0.1"


def is_loopback_bind(host: str) -> bool:
    try:
        address = ipaddress.ip_address((host or "").strip())
    except ValueError:
        return (host or "").strip() in ("localhost",)
    return address.is_loopback


def redact(value: str) -> str:
    """Shorten a secret for a log line (never print the full token)."""
    value = str(value or "")
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "…" + value[-4:]


def now() -> float:
    return time.time()
