#!/usr/bin/env python3
"""Hidden Mode — press H to hide every MP window, press H again to unhide.

Pressing **H** while any of the four MP desktop shortcuts is focused puts the
suite into Hidden Mode: every MP window is hidden and a tray notification says
so. Pressing **H** again brings them all back.

Why this is a KWin script and not plain window calls
---------------------------------------------------
On a Plasma Wayland session — the default here — the four GTK hosts are native
Wayland clients, so `wmctrl` and `xprop` cannot see them at all. There is also
no "hide this window but keep the keyboard" call available to the application:
KWin moves focus away from a window as soon as it is minimized or its opacity
drops to zero. A focused app therefore cannot receive the `H` that is meant to
undo the hide.

The solution is to do both halves inside the compositor:

* a short-lived KWin script minimizes every MP window, and
* that same script registers a **global** ``H`` shortcut which un-minimizes
  them.

KWin keeps global shortcuts active even when every client window is minimized,
so the second ``H`` always reaches the compositor. The script is unloaded after
it restores the windows, so ``H`` returns to being a normal character key.

Verified on KWin 6.7.5 (Plasma 6, Wayland): hide with four MP windows open,
press H, all four come back.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

# The suite's window titles. Every host appends its backend to the title, e.g.
# "MP Audio Player (Liquid Glass - Vulkan)" or "MP Mix Manager (Liquid Glass)",
# so matching is always by prefix.
HIDDEN_WINDOW_TITLES = (
    "MP Audio Player",
    "MP Mix Manager",
    "MP Mix Archive Manager",
    "MP Liquid Visualizer",
    "Mix Tracklist Viewer",
)

STATE_DIR_NAME = "mix-manager"
STATE_FILE_NAME = "mp_hidden_mode.json"

NOTIFICATION_TITLE = "MP Mix Manager — Hidden Mode"
NOTIFICATION_BODY = (
    "MP Mix Manager is now operating in Hidden Mode. "
    "Press H again to unhide every MP window."
)
NOTIFICATION_BODY_VISIBLE = "MP Mix Manager is visible again."

# One script handles both halves: it hides on load and registers H to unhide.
_SCRIPT_NAME = "mp_hidden_mode"
_HIDE_SCRIPT_NAME = "mp_hidden_mode_hide"


# ------------------------------------------------------------------------------
# STATE
# ------------------------------------------------------------------------------
def config_root(config_dir: Optional[Path] = None) -> Path:
    if config_dir is not None:
        return Path(config_dir)
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / STATE_DIR_NAME


def state_path(config_dir: Optional[Path] = None) -> Path:
    return config_root(config_dir) / STATE_FILE_NAME


def read_state(config_dir: Optional[Path] = None) -> dict:
    try:
        data = json.loads(state_path(config_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_state(payload: dict, config_dir: Optional[Path] = None) -> None:
    path = state_path(config_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def is_hidden(config_dir: Optional[Path] = None) -> bool:
    """True while Hidden Mode is armed.

    The restore key lives in the compositor, so the application never sees the
    press that ends Hidden Mode and cannot clear its own flag. The armed KWin
    script is therefore the source of truth; the saved flag is only a fallback
    for a session where KWin scripting is unavailable.
    """
    if kwin_available():
        # The unhide shortcut belongs to the hide script, so it is loaded for
        # exactly as long as Hidden Mode is on. Both it and that sense are
        # cleared together, whether the restore came from this process or from
        # the H key handled inside the compositor.
        return _script_loaded(_SCRIPT_NAME)
    return bool(read_state(config_dir).get("hidden"))


# ------------------------------------------------------------------------------
# KWIN
# ------------------------------------------------------------------------------
def _qdbus() -> Optional[str]:
    for name in ("qdbus6", "qdbus", "qdbus-qt6"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run(argv: Sequence[str], timeout: float = 5.0) -> str:
    try:
        done = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except Exception:
        return ""
    return done.stdout or ""


def kwin_available() -> bool:
    """True when this session has a KWin we can drive over D-Bus."""
    if not sys.platform.startswith("linux"):
        return False
    qdbus = _qdbus()
    if not qdbus:
        return False
    return "org.kde.kwin.Scripting" in _run([qdbus, "org.kde.KWin", "/Scripting"])


def _matching_helper() -> str:
    titles = json.dumps(list(HIDDEN_WINDOW_TITLES))
    return f"""
var MP_HIDDEN_TITLES = {titles};
function mpHiddenMatches(win) {{
    var caption = String((win && win.caption) || "");
    for (var i = 0; i < MP_HIDDEN_TITLES.length; i++) {{
        if (caption.indexOf(MP_HIDDEN_TITLES[i]) === 0) {{ return true; }}
    }}
    return false;
}}
"""


def _hide_script() -> str:
    """Minimize every MP window and arm a global H that puts them back."""
    return _matching_helper() + """
registerShortcut("MpHiddenModeUnhide", "MP Hidden Mode: unhide", "H", function () {
    var windows = workspace.windowList();
    var restored = 0;
    var first = null;
    for (var i = 0; i < windows.length; i++) {
        if (!mpHiddenMatches(windows[i])) { continue; }
        windows[i].minimized = false;
        if (first === null) { first = windows[i]; }
        restored++;
    }
    if (first) { workspace.activeWindow = first; }
    console.warn("MP_HIDDEN_MODE|unhidden|" + restored);
    callDBus("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
             "unloadScript", "mp_hidden_mode");
});
(function () {
    var windows = workspace.windowList();
    var hidden = 0;
    for (var i = 0; i < windows.length; i++) {
        if (!mpHiddenMatches(windows[i])) { continue; }
        windows[i].minimized = true;
        hidden++;
    }
    console.warn("MP_HIDDEN_MODE|hidden|" + hidden);
})();
"""


def _show_script() -> str:
    return _matching_helper() + """
(function () {
    var windows = workspace.windowList();
    var shown = 0;
    var first = null;
    for (var i = 0; i < windows.length; i++) {
        if (!mpHiddenMatches(windows[i])) { continue; }
        windows[i].minimized = false;
        if (first === null) { first = windows[i]; }
        shown++;
    }
    if (first) { workspace.activeWindow = first; }
    console.warn("MP_HIDDEN_MODE|shown|" + shown);
})();
"""


def _run_kwin_script(body: str, name: str) -> bool:
    qdbus = _qdbus()
    if not qdbus:
        return False
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, prefix="mp_hidden_mode_"
        )
        handle.write(body)
        handle.close()
        loaded = _run(
            [
                qdbus,
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                handle.name,
                name,
            ]
        ).strip()
        if not loaded.lstrip("-").isdigit() or int(loaded) < 0:
            return False
        _run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start"])
        return True
    except Exception:
        return False
    finally:
        if handle is not None:
            try:
                os.unlink(handle.name)
            except Exception:
                pass


def _unload_kwin_script(name: str) -> None:
    qdbus = _qdbus()
    if not qdbus:
        return
    _run(
        [
            qdbus,
            "org.kde.KWin",
            "/Scripting",
            "org.kde.kwin.Scripting.unloadScript",
            name,
        ]
    )


def _script_loaded(name: str) -> bool:
    qdbus = _qdbus()
    if not qdbus:
        return False
    return _run(
        [
            qdbus,
            "org.kde.KWin",
            "/Scripting",
            "org.kde.kwin.Scripting.isScriptLoaded",
            name,
        ]
    ).strip().lower() == "true"


def _x11_fallback(hide: bool) -> bool:
    """X11 sessions: `wmctrl` can see these windows, so use it directly."""
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        return False
    changed = False
    flag = "add,hidden" if hide else "remove,hidden"
    for line in _run([wmctrl, "-lp"]).splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        win_id, title = parts[0], parts[4].strip()
        if not any(title.startswith(p) for p in HIDDEN_WINDOW_TITLES):
            continue
        _run([wmctrl, "-i", "-r", win_id, "-b", flag])
        changed = True
    return changed


# ------------------------------------------------------------------------------
# PUBLIC TOGGLE
# ------------------------------------------------------------------------------
def hide_windows(config_dir: Optional[Path] = None) -> dict:
    applied = _run_kwin_script(_hide_script(), _SCRIPT_NAME)
    if not applied:
        applied = _x11_fallback(True)
    payload = {"hidden": True, "applied": bool(applied)}
    write_state(payload, config_dir)
    return payload


def show_windows(config_dir: Optional[Path] = None) -> dict:
    applied = _run_kwin_script(_show_script(), _HIDE_SCRIPT_NAME)
    if applied:
        # The unhide shortcut belongs to the hide script; drop it with the
        # windows it was waiting for, and unload the one-shot show script too.
        _unload_kwin_script(_SCRIPT_NAME)
        _unload_kwin_script(_HIDE_SCRIPT_NAME)
    else:
        applied = _x11_fallback(False)
    payload = {"hidden": False, "applied": bool(applied)}
    write_state(payload, config_dir)
    return payload


def toggle(config_dir: Optional[Path] = None, notify: bool = True) -> dict:
    """Flip Hidden Mode, post the notice, and return the new state."""
    result = show_windows(config_dir) if is_hidden(config_dir) else hide_windows(config_dir)
    if notify:
        hidden = bool(result.get("hidden"))
        announce(NOTIFICATION_BODY if hidden else NOTIFICATION_BODY_VISIBLE, hidden=hidden)
    return result


# ------------------------------------------------------------------------------
# NOTIFICATION
# ------------------------------------------------------------------------------
def announce(message: str = NOTIFICATION_BODY, hidden: bool = True) -> bool:
    """Post the Hidden Mode notice.

    `notify-send` reaches the desktop notification service the tray icon also
    uses, so the notice appears next to the tray with the MP icon even when the
    tray process is not the sender.
    """
    state = read_state()
    state.setdefault("hidden", hidden)
    state["notice"] = {"title": NOTIFICATION_TITLE, "body": message, "pending": True}
    write_state(state)
    return _notify_send(message)


def _notify_send(message: str) -> bool:
    notify_send = shutil.which("notify-send")
    if not notify_send:
        return False
    try:
        subprocess.run(
            [
                notify_send,
                "-a",
                "MP Mix Manager",
                "-i",
                "audio-x-generic",
                NOTIFICATION_TITLE,
                message,
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return True
    except Exception:
        return False


# ------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "toggle"
    if action in {"status", "--status"}:
        print("hidden" if is_hidden() else "visible")
        return 0
    if action in {"hide", "--hide"}:
        result = hide_windows()
        announce(NOTIFICATION_BODY, hidden=True)
    elif action in {"show", "--show", "unhide"}:
        result = show_windows()
        announce(NOTIFICATION_BODY_VISIBLE, hidden=False)
    else:
        result = toggle()
    print("hidden" if result.get("hidden") else "visible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
