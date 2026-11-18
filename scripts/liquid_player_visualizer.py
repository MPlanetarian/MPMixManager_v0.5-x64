"""In-player MP Visualizer for the Liquid Glass window.

The player writes a small state file. The OpenGL host reads it and draws the
same preset shader the standalone MP Liquid Visualizer uses, behind the
terminal text. A text guard keeps that text readable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from ctypes import POINTER, byref, c_double, c_float, c_int, c_uint32, c_uint64, c_void_p, cast
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

if sys.platform == "darwin":
    # Must be set before the first OpenGL import. See the macOS host.
    os.environ.setdefault("PYOPENGL_ERROR_CHECKING", "0")
elif sys.platform.startswith("linux"):
    # The standalone visualizer creates an X11 (GLX) window, so PyOpenGL must
    # bind GLX. Without this it picks EGL, finds no context for the GLX
    # surface, and raises "Attempt to retrieve context when no valid context"
    # before the first frame. Must be set before OpenGL is imported.
    os.environ.setdefault("PYOPENGL_PLATFORM", "glx")

CONFIG_DIR = Path.home() / ".config" / "mix-manager"

# Raspberry Pi / low-power ARM tuning, shared with the player and visualizer.
try:
    import mp_raspberry_pi
except ImportError:  # imported as scripts.liquid_player_visualizer
    from scripts import mp_raspberry_pi  # type: ignore

_LOW_POWER_GPU = mp_raspberry_pi.is_low_power_gpu()

# GLFW window hints. These live here because the standalone OpenGL visualizer
# creates its own window. The numeric values are GLFW's, and VISIBLE/DECORATED
# are easy to transpose: passing them the wrong way round creates a hidden
# window that never appears and cannot be positioned.
GLFW_CLIENT_API = 0x00022001
GLFW_CONTEXT_VERSION_MAJOR = 0x00022002
GLFW_CONTEXT_VERSION_MINOR = 0x00022003
GLFW_OPENGL_PROFILE = 0x00022008
GLFW_OPENGL_FORWARD_COMPAT = 0x00022006
GLFW_RESIZABLE = 0x00020003
GLFW_VISIBLE = 0x00020004
GLFW_DECORATED = 0x00020005
GLFW_FOCUSED = 0x00020001
GLFW_ICONIFIED = 0x00020002
GLFW_PLATFORM = 0x00050003
GLFW_PLATFORM_X11 = 0x00060004
GLFW_OPENGL_API = 0x00030001
GLFW_OPENGL_CORE_PROFILE = 0x00032001
_DEFAULT_STATE = CONFIG_DIR / "liquid_player_visualizer.json"

try:
    from visualizer_monitor_span import ROLE_SPAWNED, MonitorSpanMixin
except ImportError:
    from scripts.visualizer_monitor_span import ROLE_SPAWNED, MonitorSpanMixin
try:
    from visualizer_random_mode import RANDOM_PRESET_INTERVAL, RandomPresetMixin
except ImportError:
    from scripts.visualizer_random_mode import RANDOM_PRESET_INTERVAL, RandomPresetMixin
try:
    from visualizer_window_shape import DEFAULT_CORNER_RADIUS, RoundedWindowShaper
except ImportError:
    from scripts.visualizer_window_shape import DEFAULT_CORNER_RADIUS, RoundedWindowShaper
try:
    from visualizer_player_bond import PlayerBondMixin
except ImportError:
    from scripts.visualizer_player_bond import PlayerBondMixin
try:
    from visualizer_z_cycle import (
        WINDOW_MODE_DEFAULT,
        WINDOW_MODE_FULLSCREEN,
        WINDOW_MODE_HALF,
        ZWindowCycleMixin,
    )
except ImportError:
    from scripts.visualizer_z_cycle import (
        WINDOW_MODE_DEFAULT,
        WINDOW_MODE_FULLSCREEN,
        WINDOW_MODE_HALF,
        ZWindowCycleMixin,
    )
_CACHE: Dict[str, Any] = {"path": "", "mtime": None, "state": None}

_TEXT_GUARD = """
    if (uTextGuard > 0.5) {
        float luma = dot(col, vec3(0.2126, 0.7152, 0.0722));
        col *= mix(0.92, 0.42, smoothstep(0.18, 0.84, luma));
        col = mix(vec3(0.015, 0.020, 0.034), col, 0.80);
        float well = smoothstep(0.0, 0.07, vUv.x) * smoothstep(1.0, 0.93, vUv.x)
                   * smoothstep(0.0, 0.05, vUv.y) * smoothstep(1.0, 0.94, vUv.y);
        col = mix(col, col * 0.70 + vec3(0.008, 0.010, 0.018), well * 0.50);
    }
"""

_GL_HEADER = """#version 330 core
in vec2 vUv;
out vec4 outColor;
uniform vec4 u_res;
uniform vec4 u_audio0;
uniform vec4 u_audio1;
uniform vec4 u_audio2;
uniform vec4 u_bands0;
uniform vec4 u_bands1;
uniform vec4 u_bands2;
uniform vec4 u_bands3;
uniform vec4 u_preset;
uniform vec4 u_stream;
uniform sampler2D uCoverTex;
uniform float uTextGuard;
"""

_UNIFORM_NAMES = (
    "u_res", "u_audio0", "u_audio1", "u_audio2",
    "u_bands0", "u_bands1", "u_bands2", "u_bands3",
    "u_preset", "u_stream", "uCoverTex", "uTextGuard",
)


def state_path() -> Path:
    override = (os.environ.get("MP_LIQUID_VISUALIZER_STATE") or "").strip()
    return Path(override).expanduser() if override else _DEFAULT_STATE


def _inactive(preset_id: int = 0) -> Dict[str, Any]:
    return {
        "active": False,
        # ``visible`` is what the player's Visualization button controls. It is
        # deliberately separate from ``active``: ``active`` asks the visualizer
        # to sit behind the player at all, while ``visible`` maps or unmaps the
        # one window the player owns. An older state file, or a writer that
        # does not know the key, reads as visible so nothing disappears.
        "visible": True,
        # ``gui_visible`` is MP Gui's own Visualization button. The two hosts
        # share this file but own separate companion processes, so a single
        # flag let the player's button blank the menu's background (and the
        # reverse). Each window follows only its own host's key.
        "gui_visible": True,
        "preset_id": int(preset_id),
        "preset_key": "cosmic_dreamworlds_liquid",
        "preset_name": "Cosmic Dreamworlds (Liquid)",
    }


def visualizer_presets() -> List[Dict[str, Any]]:
    from mp_liquid_visualizer_vulkan import VISUALIZER_PRESETS
    return list(VISUALIZER_PRESETS)


def preset_by_id(preset_id: int) -> Optional[Dict[str, Any]]:
    want = int(preset_id)
    for preset in visualizer_presets():
        if int(preset.get("id", -1)) == want:
            return preset
    return None


def mark_player_visualizer_attached(preset: Optional[Dict[str, Any]] = None,
                                    visible: Optional[bool] = None) -> Dict[str, Any]:
    """Mark the companion visualizer active *before* its process starts.

    The player spawns the visualizer, and the visualizer starts polling
    immediately. If the state file were written afterwards, the first poll
    would read ``active: false`` and the window would never bond. Writing it
    first means the very first frame already knows it belongs behind the
    player. A preset is chosen when the caller does not supply one.

    ``visible`` is the Visualization button's map/unmap flag. The Liquid Player
    passes ``True`` on a fresh launch so a window is shown. ``None`` keeps
    whatever is on disk, which is what MP Gui and the launcher scripts want:
    starting the menu must not un-hide a background the player turned off.
    """
    if preset is None:
        try:
            from mp_liquid_visualizer_vulkan import get_active_preset
        except ImportError:
            from scripts.mp_liquid_visualizer_vulkan import get_active_preset
        try:
            preset = get_active_preset()
        except Exception:
            preset = None
    return write_player_visualizer_state(True, preset or {}, visible=visible)


def read_player_visualizer_state() -> Dict[str, Any]:
    path = state_path()
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        state = _inactive()
        _CACHE.update(path=key, mtime=None, state=state)
        return dict(state)
    if _CACHE.get("path") == key and _CACHE.get("mtime") == mtime and isinstance(_CACHE.get("state"), dict):
        return dict(_CACHE["state"])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    state = _inactive(int(raw.get("preset_id") or 0))
    state["active"] = bool(raw.get("active"))
    # A state file written before this key existed, or by a window that only
    # ever cared about ``active``, must keep showing its window. Only an
    # explicit false hides anything.
    state["visible"] = raw.get("visible") is not False
    # MP Gui's button keeps its own flag for the same reason: the two hosts
    # share this file, and one host's button must not unmap the other's window.
    state["gui_visible"] = raw.get("gui_visible") is not False
    if raw.get("preset_key"):
        state["preset_key"] = str(raw.get("preset_key"))
    if raw.get("preset_name"):
        state["preset_name"] = str(raw.get("preset_name"))
    _CACHE.update(path=key, mtime=mtime, state=state)
    return dict(state)


def write_player_visualizer_state(active: bool, preset: Dict[str, Any],
                                  visible: Optional[bool] = None,
                                  gui_visible: Optional[bool] = None) -> Dict[str, Any]:
    """Publish the visualizer state the player and the companion window share.

    ``active`` asks the companion to sit behind the player. ``visible`` is the
    Visualization button's on/off: hidden only unmaps the one window the
    player owns, and never a window the user opened with Z or from the desktop
    shortcut.

    ``gui_visible`` is the same button on MP Gui, kept as a second key because
    both hosts own a separate companion process but share this file. Writing
    one host's flag must not blank the other host's background.

    ``visible``/``gui_visible`` of ``None`` keep the value already on disk, so
    a writer that only changes the preset (a key pressed in a free visualizer
    window, the theme sync) cannot un-hide a companion the user turned off. A
    first write with no file yet follows ``active``, which is the pre-existing
    behaviour.
    """
    if visible is None or gui_visible is None:
        try:
            current = read_player_visualizer_state()
        except Exception:
            current = {}
        if visible is None:
            visible = current.get("visible") is not False if current else bool(active)
        if gui_visible is None:
            gui_visible = current.get("gui_visible") is not False if current else bool(active)
    state = {
        "active": bool(active),
        "visible": bool(visible),
        "gui_visible": bool(gui_visible),
        "preset_id": int(preset.get("id", 0)),
        "preset_key": str(preset.get("key") or "cosmic_dreamworlds_liquid"),
        "preset_name": str(preset.get("name") or "Cosmic Dreamworlds (Liquid)"),
    }
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    _CACHE.update(path=str(path), mtime=mtime, state=dict(state))
    # Keep the standalone visualizer on the same theme. A test that points
    # MP_LIQUID_VISUALIZER_STATE at a temp file must not rewrite the user's preset.
    if active and path == _DEFAULT_STATE:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            (CONFIG_DIR / "visualizer_preset").write_text(state["preset_key"] + "\n", encoding="utf-8")
        except OSError:
            pass
    return dict(state)


# The player window stays at its own size. On macOS the raymarch long edge
# is 768, so a 1094×734 window is drawn at 768×515 and scaled up. A
# full-window march of this shader is about 9 fps through MoltenVK.
# Linux marches at the framebuffer size, one raymarch pixel per window
# pixel, so the picture stays sharp. Windows keeps the 224 cap.
# MP_VISUALIZER_GL_LONG_EDGE still overrides the long edge.
# MP_VISUALIZER_GL_MARCH_STEPS clamps to 24..84.
MACOS_FULLSCREEN_SIZE = (1920, 1080)
_MACOS_LONG_EDGE = 768
_UNFOLDED_LONG_EDGE = 224
_FULL_MARCH_STEPS = 84
_MACOS_MARCH_STEPS = 64
_MARCH_LOOP = "for (int i = 0; i < 84; i++)"
_SOURCE_CACHE: Dict[str, str] = {}
_PRESET_SPIRV = ROOT / "shaders" / "presets"
_SPIRV_MAGIC = b"\x03\x02\x23\x07"

_UPSCALE_FRAG = """#version 330 core
in vec2 vUv;
out vec4 outColor;
uniform sampler2D uTex;
void main() {
    outColor = vec4(texture(uTex, vUv).rgb, 1.0);
}
"""


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


def macos_visualizer_long_edge() -> int:
    return _env_int("MP_VISUALIZER_GL_LONG_EDGE", _MACOS_LONG_EDGE, 160, 4096)


def macos_visualizer_march_steps() -> int:
    return _env_int("MP_VISUALIZER_GL_MARCH_STEPS", _MACOS_MARCH_STEPS, 24, _FULL_MARCH_STEPS)


def linux_visualizer_long_edge() -> int:
    """Long-edge cap for the Linux raymarch.

    Linux used to march exactly at the framebuffer size. That is crisp on a
    desktop GPU and unusable on a Raspberry Pi, where the 84-step
    twelve-scene SDF runs at single-digit frames per second. The cap stays 0
    (no cap) on a normal desktop and takes the value from
    ``mp_raspberry_pi`` on a Pi. An explicit MP_VISUALIZER_GL_LONG_EDGE
    always wins because the env is consulted first.
    """
    if (os.environ.get("MP_VISUALIZER_GL_LONG_EDGE") or "").strip():
        return macos_visualizer_long_edge()
    if not _LOW_POWER_GPU:
        return 0
    return mp_raspberry_pi.visualizer_limits()["long_edge"] or 640


def visualizer_march_steps() -> int:
    """March steps for this host.

    macOS historically used 64 and Windows/Linux 84. A Pi uses the lower
    value from mp_raspberry_pi so the frame budget is reachable.
    """
    if sys.platform == "darwin":
        return macos_visualizer_march_steps()
    if _LOW_POWER_GPU:
        return _env_int("MP_VISUALIZER_GL_MARCH_STEPS",
                        mp_raspberry_pi.visualizer_limits()["march_steps"] or 44,
                        16, _FULL_MARCH_STEPS)
    return _env_int("MP_VISUALIZER_GL_MARCH_STEPS", _FULL_MARCH_STEPS, 24, _FULL_MARCH_STEPS)


def macos_liquid_player_window_size(
    cols: int = 120, rows: int = 40, char_w: int = 9, char_h: int = 18, margin: int = 7
) -> tuple:
    """Point size of the macOS Liquid Player window.

    120×40 cells at 9×18 px, plus the 7 px bezel on each side: 1094×734.
    """
    return (
        int(cols) * int(char_w) + 2 * int(margin),
        int(rows) * int(char_h) + 2 * int(margin),
    )


_PLAYER_WINDOW_TITLE = "MP Audio Player (Liquid Glass"
_GUI_WINDOW_TITLE = "MP Mix Manager (Liquid Glass"
_WINDOW_GEOM_RE = re.compile(r"(\d+)x(\d+)[+-]\d+[+-]\d+")


def linux_liquid_player_window_size(
    cols: int = 120, rows: int = 40, char_w: int = 9, char_h: int = 18, margin: int = 7, slider: int = 44
) -> tuple:
    """Pixel size of the Linux OpenGL Liquid Player before the font is measured.

    120×40 cells at 9×18 px, a 7 px bezel on each side, and the 44 px
    transparency slider: 1094×778. The host then refits to the real VTE cell.
    """
    return (
        int(cols) * int(char_w) + 2 * int(margin),
        int(rows) * int(char_h) + 2 * int(margin) + int(slider),
    )


def _parse_titled_window_size(tree_text: str, title: str) -> Optional[tuple]:
    """Largest mapped window whose title contains `title`."""
    best = None
    for line in (tree_text or "").splitlines():
        if title not in line:
            continue
        match = _WINDOW_GEOM_RE.search(line)
        if not match:
            continue
        size = (int(match.group(1)), int(match.group(2)))
        if size[0] < 64 or size[1] < 64:
            continue
        if best is None or size[0] * size[1] > best[0] * best[1]:
            best = size
    return best


def parse_liquid_player_window_size(tree_text: str) -> Optional[tuple]:
    """Largest mapped MP Liquid Player window in an xwininfo -root -tree listing."""
    return _parse_titled_window_size(tree_text, _PLAYER_WINDOW_TITLE)


def parse_liquid_gui_window_size(tree_text: str) -> Optional[tuple]:
    """Largest mapped MP Gui window in an xwininfo -root -tree listing."""
    return _parse_titled_window_size(tree_text, _GUI_WINDOW_TITLE)


def _read_xwininfo_tree() -> str:
    try:
        result = subprocess.run(
            ["xwininfo", "-root", "-tree"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def player_window_size_path() -> Path:
    override = (os.environ.get("MP_LIQUID_PLAYER_WINDOW_FILE") or "").strip()
    if override:
        return Path(override)
    return CONFIG_DIR / "liquid_player_window.json"


def gui_window_size_path() -> Path:
    override = (os.environ.get("MP_LIQUID_GUI_WINDOW_FILE") or "").strip()
    if override:
        return Path(override)
    return CONFIG_DIR / "liquid_gui_window.json"


# (path -> last token written). Skips rewriting an unchanged record. Keyed by
# path because a host publishes sizes and geometry through the same module, and
# a single shared token made the second write look like a duplicate.
_PUBLISHED_CACHE: Dict[str, tuple] = {}


def _publish_window_size(
    path: Path,
    width: int,
    height: int,
    pid: Optional[int] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    wid: Optional[int] = None,
) -> None:
    """Record an outer window size so the visualizer can match it.

    ``wid`` is the host's own X11 window id. The bonded visualizer stacks
    itself against that exact window, so it does not have to guess which of
    several players (or the player and MP Gui) it belongs behind.
    """
    try:
        width_i = int(width)
        height_i = int(height)
        pid_i = int(os.getpid() if pid is None else pid)
    except (TypeError, ValueError):
        return
    if width_i < 64 or height_i < 64 or pid_i <= 0:
        return
    if x is None or y is None or wid is None:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            x = int(previous["x"]) if x is None and "x" in previous else x
            y = int(previous["y"]) if y is None and "y" in previous else y
            wid = int(previous["wid"]) if wid is None and "wid" in previous else wid
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    try:
        x_i = int(x) if x is not None else None
        y_i = int(y) if y is not None else None
        wid_i = int(wid) if wid is not None else None
    except (TypeError, ValueError):
        x_i = y_i = wid_i = None
    token = (width_i, height_i, pid_i, x_i, y_i, wid_i)
    key = str(path)
    if _PUBLISHED_CACHE.get(key) == token:
        return
    payload = {"width": width_i, "height": height_i, "pid": pid_i}
    if x_i is not None and y_i is not None:
        payload.update(x=x_i, y=y_i)
    if wid_i:
        payload["wid"] = wid_i
    body = json.dumps(payload) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return
    _PUBLISHED_CACHE[key] = token


def publish_liquid_player_window_size(width: int, height: int, pid: Optional[int] = None) -> None:
    """Record the Liquid Player's outer window so the visualizer can match it."""
    _publish_window_size(player_window_size_path(), width, height, pid)


def publish_liquid_player_window_geometry(x: int, y: int, width: int, height: int,
                                          pid: Optional[int] = None,
                                          wid: Optional[int] = None) -> None:
    """Record the player's outer position and size for the bonded visualizer."""
    _publish_window_size(player_window_size_path(), width, height, pid, x, y, wid)


def read_published_player_window_geometry() -> Optional[tuple]:
    """Return (x, y, width, height) while the publishing player is alive."""
    return _read_published_window_geometry(player_window_size_path())


def publish_liquid_gui_window_size(width: int, height: int, pid: Optional[int] = None) -> None:
    """Record the MP Gui window so a visualizer started with it can match it."""
    _publish_window_size(gui_window_size_path(), width, height, pid)


def publish_liquid_gui_window_geometry(x: int, y: int, width: int, height: int,
                                       pid: Optional[int] = None,
                                       wid: Optional[int] = None) -> None:
    """Record MP Gui's outer position and size for its bonded companion.

    The GUI companion sits behind MP Gui, not behind whichever Liquid Player
    happens to be open, so it needs the GUI's own rectangle. Before this only a
    size was published, which let a GUI companion keep the size but stack
    against the wrong host window.
    """
    _publish_window_size(gui_window_size_path(), width, height, pid, x, y, wid)


def read_published_gui_window_geometry() -> Optional[tuple]:
    """Return (x, y, width, height) for MP Gui while that process lives."""
    return _read_published_window_geometry(gui_window_size_path())


def _read_published_window_geometry(path: Path) -> Optional[tuple]:
    """(x, y, width, height) for a publisher that is still running."""
    record = _read_published_window_record(path)
    if record is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload["x"]), int(payload["y"]), record[0], record[1]
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


def read_published_player_window_id() -> int:
    """X11 window id the Liquid Player published, or 0 when unknown."""
    return _read_published_window_id(player_window_size_path())


def read_published_gui_window_id() -> int:
    """X11 window id MP Gui published, or 0 when unknown."""
    return _read_published_window_id(gui_window_size_path())


def _read_published_window_id(path: Path) -> int:
    """The ``wid`` in a published record, while its publisher is alive.

    An old record with no ``wid`` returns 0 and the bond falls back to the
    title lookup, so a half-upgraded tree keeps working.
    """
    if _read_published_window_record(path) is None:
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload.get("wid") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _read_published_window_record(path: Path) -> Optional[tuple]:
    """(width, height, mtime) for a publisher process that is still running."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        mtime = path.stat().st_mtime
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    try:
        width = int(payload["width"])
        height = int(payload["height"])
        pid = int(payload.get("pid") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if width < 64 or height < 64 or not _pid_alive(pid):
        return None
    return width, height, float(mtime)


def read_published_player_window_record() -> Optional[tuple]:
    """(width, height, mtime) for a player process that is still running."""
    return _read_published_window_record(player_window_size_path())


def read_published_gui_window_record() -> Optional[tuple]:
    """(width, height, mtime) for an MP Gui process that is still running."""
    return _read_published_window_record(gui_window_size_path())


def read_published_player_window_size() -> Optional[tuple]:
    record = read_published_player_window_record()
    if not record:
        return None
    return int(record[0]), int(record[1])


def read_published_gui_window_size() -> Optional[tuple]:
    record = read_published_gui_window_record()
    if not record:
        return None
    return int(record[0]), int(record[1])


def _glass_host_running(manager: bool) -> Optional[bool]:
    """True/False for a running Liquid Glass host, None when it can't be seen.

    Manager and player are separate. ``None`` means this platform has no
    ``/proc`` (Windows, macOS): the host is simply not observable from here,
    which is different from "not running" and must not be read as the latter.
    """
    root = Path("/proc")
    if not root.is_dir():
        return None
    # MP Liquid Mixer hosts the same companion behind its own full-screen
    # window, so it counts as a player host. It is never the manager.
    player_marker = b"MP_Audio_Player_Liquid_Glass"
    mixer_marker = b"MP_Liquid_Mixer"
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            command = (root / entry / "cmdline").read_bytes()
        except OSError:
            continue
        if manager:
            if player_marker in command and b"--manager" in command:
                return True
            continue
        if (player_marker in command and b"--manager" not in command) or mixer_marker in command:
            return True
    return False


def liquid_player_host_running() -> Optional[bool]:
    """True when a Liquid Player host is up. The Manager GUI does not count."""
    return _glass_host_running(False)


def liquid_gui_host_running() -> Optional[bool]:
    """True when MP Gui's Liquid Glass host is up."""
    return _glass_host_running(True)


def _iter_visualizer_processes():
    """Yield ``(pid, environ)`` for every MP Visualizer process."""
    root = Path("/proc")
    if not root.is_dir():
        return
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        proc = root / entry
        try:
            command = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if b"MP_Liquid_Visualizer" not in command and b"mp_liquid_visualizer_vulkan" not in command:
            continue
        try:
            environ = (proc / "environ").read_bytes()
        except OSError:
            environ = b""
        yield int(entry), environ


def companion_visualizer_running(for_gui: bool) -> bool:
    """True when the companion belonging to this host kind is already up.

    The two hosts keep separate companions. A single "some visualizer exists"
    check made each one skip its own spawn whenever the other was up: open the
    Liquid Player, then MP Gui, and the GUI never got a background window. The
    companion is identified by the flags its host sets — ``COMPANION=1`` plus
    ``MATCH_GUI=1`` for MP Gui, ``COMPANION=1`` alone for the player — so a
    free window opened with Z (``DETACHED=1``) never counts either.
    """
    want_gui = b"MP_VISUALIZER_MATCH_GUI=1"
    have_companion = b"MP_VISUALIZER_COMPANION=1"
    for _pid, environ in _iter_visualizer_processes():
        flags = environ.split(b"\x00")
        if have_companion not in flags:
            continue
        if (want_gui in flags) == bool(for_gui):
            return True
    return False


def _wait_for_published_window(
    read_record,
    parse_mapped,
    default_host,
    timeout: float,
    idle_timeout: float,
    stable_for: float,
    *,
    require_mapped: bool = True,
    now=None,
    wall_now=None,
    sleep=None,
    host_running=None,
    read_tree=None,
) -> Optional[tuple]:
    """Fitted window size, once the published record and the mapped window agree."""
    import time
    clock = time.monotonic if now is None else now
    wall = time.time if wall_now is None else wall_now
    pause = time.sleep if sleep is None else sleep
    running = default_host if host_running is None else host_running
    tree_of = _read_xwininfo_tree if read_tree is None else read_tree
    start = clock()
    seen = None
    seen_at = start
    while True:
        record = read_record()
        current = clock()
        size = None
        settled = False
        if record:
            size = (int(record[0]), int(record[1]))
            if size != seen:
                seen = size
                seen_at = current
            age = wall() - float(record[2])
            settled = age >= float(stable_for) or (current - seen_at) >= float(stable_for)
        mapped_ok = True
        if settled and size and require_mapped:
            mapped = parse_mapped(tree_of() or "")
            mapped_ok = mapped == size
        if settled and size and mapped_ok:
            return size
        elapsed = current - start
        host_up = bool(running())
        if host_up and elapsed >= float(timeout):
            return seen
        if not host_up and elapsed >= float(idle_timeout):
            return seen
        pause(0.05)


def wait_for_published_player_window_size(
    timeout: float = 5.0,
    idle_timeout: float = 0.45,
    stable_for: float = 0.3,
    *,
    require_mapped: bool = True,
    now=None,
    wall_now=None,
    sleep=None,
    host_running=None,
    read_tree=None,
) -> Optional[tuple]:
    """Fitted Liquid Player size, once the on-screen window matches it.

    The player opens at a 9×18 guess, then refits to the real VTE cell.
    A visualizer that reads the first size stays smaller. This waits until
    the published size has settled and, on the desktop, until the mapped
    window agrees. No player returns None after idle_timeout.
    """
    return _wait_for_published_window(
        read_published_player_window_record,
        parse_liquid_player_window_size,
        liquid_player_host_running,
        timeout,
        idle_timeout,
        stable_for,
        require_mapped=require_mapped,
        now=now,
        wall_now=wall_now,
        sleep=sleep,
        host_running=host_running,
        read_tree=read_tree,
    )


def wait_for_published_gui_window_size(
    timeout: float = 8.0,
    idle_timeout: float = 8.0,
    stable_for: float = 0.3,
    *,
    require_mapped: bool = True,
    now=None,
    wall_now=None,
    sleep=None,
    host_running=None,
    read_tree=None,
) -> Optional[tuple]:
    """Fitted MP Gui size, once the on-screen window matches it.

    MP Gui starts the visualizer before its own window exists, then refits
    that window to the real VTE cell. The idle wait covers that startup.
    """
    return _wait_for_published_window(
        read_published_gui_window_record,
        parse_liquid_gui_window_size,
        liquid_gui_host_running,
        timeout,
        idle_timeout,
        stable_for,
        require_mapped=require_mapped,
        now=now,
        wall_now=wall_now,
        sleep=sleep,
        host_running=host_running,
        read_tree=read_tree,
    )


def visualizer_matches_gui() -> bool:
    """True when this visualizer was started by MP Gui and should copy that window."""
    return os.environ.get("MP_VISUALIZER_MATCH_GUI") == "1"


def linux_visualizer_window_size(tree_text: Optional[str] = None) -> tuple:
    """Linux MP Visualizer size: the fitted Liquid Player, or that player's formula.

    An explicit window-tree listing is parsed as given. The live lookup
    prefers the size the player published after measuring its font.
    """
    if tree_text is None:
        published = read_published_player_window_size()
        if published:
            return published
        tree_text = _read_xwininfo_tree()
    found = parse_liquid_player_window_size(tree_text or "")
    if found:
        return found
    return linux_liquid_player_window_size()


def linux_gui_visualizer_window_size(tree_text: Optional[str] = None) -> tuple:
    """Linux visualizer size when MP Gui started it: that GUI window.

    The grid matches the Liquid Player, so the formula fallback is the same
    120×40 size. A live lookup prefers the size MP Gui published after
    measuring its font, not a Liquid Player that happens to be open.
    """
    if tree_text is None:
        published = read_published_gui_window_size()
        if published:
            return published
        tree_text = _read_xwininfo_tree()
    found = parse_liquid_gui_window_size(tree_text or "")
    if found:
        return found
    return linux_liquid_player_window_size()


def linux_visualizer_match_window_size(tree_text: Optional[str] = None) -> tuple:
    """Player size, or the MP Gui size when this process was started with MP Gui."""
    if visualizer_matches_gui():
        return linux_gui_visualizer_window_size(tree_text)
    return linux_visualizer_window_size(tree_text)


def macos_player_grid(width: int, height: int, char_w: int = 9, char_h: int = 18, margin: int = 7) -> tuple:
    """Character cells that fill a macOS player window of this pixel size."""
    cols = max(20, (int(width) - 2 * int(margin)) // int(char_w))
    rows = max(8, (int(height) - 2 * int(margin)) // int(char_h))
    return cols, rows


def macos_fullscreen_size(work_w: int, work_h: int) -> tuple:
    """Fullscreen pixels. 1920x1080 is the highest, and a smaller screen stays smaller."""
    cap_w, cap_h = MACOS_FULLSCREEN_SIZE
    return min(max(2, int(work_w)), cap_w), min(max(2, int(work_h)), cap_h)


def _renderer_is_fullscreen(renderer) -> bool:
    host = getattr(renderer, "host_ref", None)
    if host is not None and getattr(host, "is_maximized", False):
        return True
    return bool(getattr(renderer, "is_fullscreen", False))


def _scale_long_edge(rw: int, rh: int, cap: int) -> tuple:
    """Scale down so the long edge is at most cap. A smaller image stays as it is."""
    rw, rh = max(2, int(rw)), max(2, int(rh))
    cap = max(2, int(cap))
    longest = max(rw, rh)
    if longest <= cap:
        return rw, rh
    scale = cap / float(longest)
    return max(2, int(round(rw * scale))), max(2, int(round(rh * scale)))


def march_target_size(
    width: int,
    height: int,
    long_edge: Optional[int] = None,
    *,
    platform: Optional[str] = None,
) -> tuple:
    """Raymarch pixels for a window of this size.

    Linux uses the framebuffer as it is, so a 1920×1200 window marches at
    1920×1200 and is not scaled up. macOS keeps a 768 long edge: a 1094×734
    window marches at 768×515. Windows keeps the 224 cap.
    ``MP_VISUALIZER_GL_LONG_EDGE`` still overrides the long edge.
    """
    host = sys.platform if platform is None else platform
    width_i, height_i = max(2, int(width)), max(2, int(height))
    if long_edge is not None or bool((os.environ.get("MP_VISUALIZER_GL_LONG_EDGE") or "").strip()):
        cap = macos_visualizer_long_edge() if long_edge is None else max(160, int(long_edge))
        return _scale_long_edge(width_i, height_i, cap)
    if host == "darwin":
        return _scale_long_edge(width_i, height_i, macos_visualizer_long_edge())
    if host.startswith("linux"):
        cap = linux_visualizer_long_edge()
        if cap:
            return _scale_long_edge(width_i, height_i, cap)
        return width_i, height_i
    return _scale_long_edge(width_i, height_i, _UNFOLDED_LONG_EDGE)


def fullscreen_render_cap(width: int, height: int, fullscreen: bool) -> tuple:
    """Cap a fullscreen render at 1080p on a 4K panel, keeping the aspect ratio.

    A 4K desktop that fills the screen but does not change the panel's mode (a
    compositor that ignores the mode request, a TV left at 2160p) hands the
    renderer a 3840x2160 surface. Raymarching that is four times the pixels of
    1080p for a picture that is scaled back up anyway, so both backends draw the
    scene at 1080p and blit it to the surface instead. A surface that is already
    1080p or smaller is returned unchanged.
    """
    width, height = max(2, int(width)), max(2, int(height))
    if not fullscreen or (width < 3840 and height < 2160):
        return width, height
    scale = min(1920.0 / width, 1080.0 / height)
    return max(2, int(round(width * scale))), max(2, int(round(height * scale)))


def visualizer_internal_size(
    fw: int,
    fh: int,
    ww: Optional[int] = None,
    wh: Optional[int] = None,
    *,
    macos: Optional[bool] = None,
    long_edge: Optional[int] = None,
    fullscreen: bool = False,
) -> tuple:
    """Pixel size of the raymarch.

    On macOS the long edge stops at 768, and that image is scaled onto the
    window. A 1094×734 window marches at 768×515. A larger framebuffer uses
    the same cap. Other platforms use the framebuffer as it is.
    ``fullscreen`` does not raise the cap.
    MP_VISUALIZER_GL_LONG_EDGE still overrides the long edge.
    """
    del fullscreen
    fw_i = max(2, int(fw))
    fh_i = max(2, int(fh))
    if macos is None:
        macos = sys.platform == "darwin"
    # An explicit long edge applies on every platform, as it already does for the
    # Vulkan march. Without one, only macOS caps the march.
    explicit_cap = long_edge is not None or bool((os.environ.get("MP_VISUALIZER_GL_LONG_EDGE") or "").strip())
    if not macos and not explicit_cap:
        return fw_i, fh_i
    if explicit_cap:
        if ww is None or wh is None:
            rw, rh = fw_i, fh_i
        else:
            rw = min(fw_i, max(2, int(ww)))
            rh = min(fh_i, max(2, int(wh)))
        cap = macos_visualizer_long_edge() if long_edge is None else max(160, int(long_edge))
        return _scale_long_edge(rw, rh, cap)
    return _scale_long_edge(fw_i, fh_i, macos_visualizer_long_edge())


# The CHROMATIC STORM preset blends six scenes. The crossfade holds one scene
# at weight 1.0 and fades the next one in, so at most two weights are ever
# nonzero. Guarding each scene keeps the faded-out scenes out of the march.
#
# This is applied to the OpenGL GLSL source only. shaders/mp_liquid_visualizer.frag
# is shared with the Vulkan path, whose SPIR-V modules and manifest digest are
# generated from it, so that file is deliberately left alone.
_SWEEP_SEQUENCE_NAMES = (
    "csSeqClamshell", "csSeqHelix", "csSeqTwin", "csSeqGyre", "csSeqComet", "csSeqNova",
)


def _guard_faded_sequences(body: str) -> str:
    """Skip CHROMATIC STORM scenes whose crossfade weight is exactly zero."""
    import re

    names = "|".join(_SWEEP_SEQUENCE_NAMES)
    # Whitespace around the index and the assignment moved between revisions of
    # the shader, so match it loosely instead of pinning one spelling.
    pattern = re.compile(
        r"^    seq\[(\d+)\]\s*=\s*(" + names + r")\(p\);$", re.MULTILINE
    )
    return pattern.sub(
        lambda m: (
            f"    seq[{m.group(1)}] = vec2(0.0, 1.0);\n"
            f"    if (w[{m.group(1)}] > 0.0) seq[{m.group(1)}] = {m.group(2)}(p);"
        ),
        body,
    )


def opengl_visualizer_source() -> str:
    """GLSL 330 port of shaders/mp_liquid_visualizer.frag, with a text guard."""
    cached = _SOURCE_CACHE.get("generic")
    if cached is not None:
        return cached
    raw = (ROOT / "shaders" / "mp_liquid_visualizer.frag").read_text(encoding="utf-8")
    start = raw.find("const float PI")
    if start < 0:
        raise RuntimeError("MP Visualizer shader is missing its prelude")
    body = raw[start:]
    for src, dst in (
        ("u.bands0", "u_bands0"),
        ("u.bands1", "u_bands1"),
        ("u.bands2", "u_bands2"),
        ("u.bands3", "u_bands3"),
        ("u.audio0", "u_audio0"),
        ("u.audio1", "u_audio1"),
        ("u.audio2", "u_audio2"),
        ("u.preset", "u_preset"),
        ("u.stream", "u_stream"),
        ("u.res", "u_res"),
    ):
        body = body.replace(src, dst)
    body = _guard_faded_sequences(body)
    needle = "outColor = vec4(col, 1.0);"
    if needle not in body:
        raise RuntimeError("MP Visualizer shader is missing its output")
    body = body.replace(needle, _TEXT_GUARD + "\n    " + needle, 1)
    source = _GL_HEADER + body
    _SOURCE_CACHE["generic"] = source
    return source


def specialize_vulkan_source(source: str, preset_id: int, march_steps: Optional[int] = None) -> str:
    """Fold ``u.preset.x`` so MoltenVK drops the other scenes from the march."""
    preset = f"({float(int(preset_id)):.1f})"
    specialized = source.replace("u.preset.x", preset)
    steps = _FULL_MARCH_STEPS if march_steps is None else int(march_steps)
    if steps != _FULL_MARCH_STEPS and _MARCH_LOOP in specialized:
        specialized = specialized.replace(_MARCH_LOOP, f"for (int i = 0; i < {steps}; i++)", 1)
    return specialized


def _tool_path(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    for cand in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if os.access(cand, os.X_OK):
            return cand
    return None


def _visualizer_source_digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _read_preset_manifest() -> Dict[str, Any]:
    try:
        raw = json.loads((_PRESET_SPIRV / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _compile_folded_spirv(source: str, preset_id: int, steps: int, dest: Path) -> Optional[Path]:
    compiler = _tool_path("glslangValidator")
    if not compiler:
        return None
    folded = specialize_vulkan_source(source, preset_id, steps)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_path = dest.with_suffix(".frag")
    tmp_spv = dest.with_suffix(".tmp.spv")
    src_path.write_text(folded, encoding="utf-8")
    try:
        result = subprocess.run(
            [compiler, "-V", "--target-env", "vulkan1.0", "-o", str(tmp_spv), str(src_path)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0 or not tmp_spv.is_file():
            return None
        optimizer = _tool_path("spirv-opt")
        if optimizer:
            optimized = dest.with_suffix(".opt.spv")
            opt = subprocess.run(
                [optimizer, "-O", str(tmp_spv), "-o", str(optimized)],
                capture_output=True, timeout=60, check=False,
            )
            if opt.returncode == 0 and optimized.is_file():
                optimized.replace(tmp_spv)
        data = tmp_spv.read_bytes()
        if not data.startswith(_SPIRV_MAGIC):
            return None
        tmp_spv.replace(dest)
        return dest
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        src_path.unlink(missing_ok=True)
        if tmp_spv.exists() and tmp_spv != dest:
            tmp_spv.unlink(missing_ok=True)


def vulkan_preset_spirv(preset_id: int, march_steps: Optional[int] = None) -> Optional[Path]:
    """SPIR-V for one preset, with the other scenes folded out.

    The shipped modules match the shader at a 64-step march. A newer shader
    is compiled into the user cache when glslangValidator is installed.
    """
    steps = macos_visualizer_march_steps() if march_steps is None else int(march_steps)
    preset_id = int(preset_id)
    try:
        source = (ROOT / "shaders" / "mp_liquid_visualizer.frag").read_text(encoding="utf-8")
    except OSError:
        return None
    digest = _visualizer_source_digest(source)
    cache = CONFIG_DIR / "visualizer-spirv" / f"p{preset_id}-s{steps}-{digest}.spv"
    if cache.is_file() and cache.stat().st_size > 16:
        return cache
    shipped = _PRESET_SPIRV / f"p{preset_id}.frag.spv"
    meta = _read_preset_manifest()
    shipped_ok = (
        shipped.is_file()
        and steps == _MACOS_MARCH_STEPS
        and meta.get("source") == digest
        and int(meta.get("steps", -1)) == steps
    )
    if shipped_ok:
        return shipped
    compiled = _compile_folded_spirv(source, preset_id, steps, cache)
    if compiled:
        return compiled
    if shipped.is_file() and steps == _MACOS_MARCH_STEPS:
        # The shipped module was built from a different revision of the GLSL and
        # no compiler is available to rebuild it. Rendering the old module beats
        # rendering nothing, but it silently draws the previous shader, so say so
        # rather than letting a stale manifest look like a working fix.
        print(f"[LiquidVisualizer] Warning: {shipped.name} was built from a different "
              f"mp_liquid_visualizer.frag (shipped {meta.get('source', 'unknown')}, "
              f"current {digest}); install glslangValidator to rebuild the preset shaders.",
              file=sys.stderr)
        return shipped
    return None


def specialize_visualizer_source(source: str, preset_id: int, march_steps: Optional[int] = None) -> str:
    """Fold the preset id to a constant so the other scenes drop out of the program.

    The fragment shader carries every preset. Apple's compiler keeps the whole
    program live when the id is a uniform, and that register pressure is what
    makes the march slow. Pitch and dynamic time stay in the preset uniform.

    The Vulkan shader spells the accessor ``u.preset.x`` while the OpenGL port
    rewrites it to ``u_preset.x``, so both spellings must be folded or the
    preset scenes never collapse out of the program.
    """
    preset = f"({float(int(preset_id)):.1f})"
    specialized = source
    for accessor in ("u.preset.x", "u_preset.x"):
        if accessor in specialized:
            specialized = specialized.replace(accessor, preset)
    steps = _FULL_MARCH_STEPS if march_steps is None else int(march_steps)
    if steps != _FULL_MARCH_STEPS and _MARCH_LOOP in specialized:
        specialized = specialized.replace(_MARCH_LOOP, f"for (int i = 0; i < {steps}; i++)", 1)
    return specialized


def march_advance(bridge, dt: float) -> None:
    """Advance the scene clock, freezing the geometry while audio is paused.

    The MP Visualizer must "freeze instantly in-place on pause/stop". The
    raymarch time comes from ``accum_time``, so while the player is not
    playing we creep that time forward slowly instead of jumping to a new
    scene. Both the Vulkan and OpenGL renderers use this so a pause looks
    identical on either backend.
    """
    if not getattr(bridge, "is_playing", False):
        step = max(0.0, float(dt)) * 0.45
        bridge.accum_time += step
        bridge.dynamic_time += step


def _window_points(renderer) -> Optional[tuple]:
    getter = getattr(renderer, "window_size", None)
    if not callable(getter):
        return None
    try:
        ww, wh = getter()
        ww_i, wh_i = int(ww), int(wh)
    except (TypeError, ValueError):
        return None
    if ww_i < 2 or wh_i < 2:
        return None
    return ww_i, wh_i


def _loc(locs: Dict[str, int], name: str) -> int:
    try:
        value = int(locs.get(name, -1))
    except (TypeError, ValueError):
        return -1
    return value


class PlayerVisualizerLayer:
    """Draws the MP Visualizer preset into the liquid-glass framebuffer."""

    def __init__(self) -> None:
        self._prog = 0
        self._locs: Dict[str, int] = {}
        self._programs: Dict[int, tuple] = {}
        self._tex = 0
        self._tex_key = ""
        self._bridge = None
        self._last = 0.0
        self._error = ""
        self._fbo = 0
        self._color = 0
        self._target_size: tuple = (0, 0)
        self._upscale = 0
        self._upscale_tex = -1
        self._failed: set = set()
        # Thermal back-off state for low-power ARM boards.
        self._thermal_scale = 1.0
        self._thermal_checked = 0.0
        # Filled by Renderer.open()/_init_gl(); shader stages compile against
        # this version, and the GLFW context ladder records what was granted.
        self.requested_gl = (3, 3, "core")
        self.glsl_version = "330"

    def _thermally_limited_size(self, rw: int, rh: int) -> tuple:
        """Shrink the march size while the SoC is hot.

        A Pi throttles at 80-85 C and the visualizer loses most of its frame
        rate before that. At 70 C the rendered size is halved, at 80 C it is
        quartered. The check itself runs at most once every 10 seconds.
        """
        import time
        now = time.monotonic()
        if now - self._thermal_checked >= 10.0:
            self._thermal_checked = now
            state = mp_raspberry_pi.thermal_state()
            self._thermal_scale = {"hot": 0.5, "warm": 0.75}.get(state, 1.0)
        if self._thermal_scale >= 1.0:
            return rw, rh
        return max(2, int(rw * self._thermal_scale)), max(2, int(rh * self._thermal_scale))

    def draw(self, renderer, fw: int, fh: int) -> bool:
        state = read_player_visualizer_state()
        if not state.get("active") or state.get("visible") is False:
            return False
        return self.draw_with_state(renderer, fw, fh, state)

    def draw_with_state(self, renderer, fw: int, fh: int, state: Dict[str, Any],
                        bridge=None, text_guard: Optional[bool] = None) -> bool:
        """Draw ``state``'s preset into the bound framebuffer.

        The OpenGL host and the standalone OpenGL visualizer both call this.
        The standalone renderer passes its own already-updated ``bridge`` so
        audio telemetry is read once per frame and shared by both paths.

        ``text_guard`` dims the scene so white terminal text stays readable.
        That is only wanted when the visualizer is drawn *behind the player's
        text*. A standalone visualizer window has no text over it, and the
        guard washed the whole picture out to grey, so the standalone renderer
        passes ``False``. The default stays on, which is what every existing
        caller (the in-player overlay) already expected.
        """
        if not state.get("active") or state.get("visible") is False:
            return False
        if text_guard is None:
            text_guard = True
        try:
            return self._draw(renderer, fw, fh, state, bridge=bridge,
                              text_guard=text_guard)
        except Exception as exc:
            if not self._error:
                self._error = str(exc)
                print(f"MP Visualizer background unavailable: {exc}", file=sys.stderr)
            return False

    def _draw(self, renderer, fw: int, fh: int, state: Dict[str, Any], bridge=None,
              text_guard: bool = True) -> bool:
        import time
        from OpenGL import GL

        preset_id = int(state.get("preset_id") or 0)
        if not self._compile(GL, renderer, preset_id):
            return False
        self._ensure_cover(GL, state)
        # A caller-supplied bridge is updated by the caller, so the audio
        # telemetry is read once per frame and not twice.
        externally_managed = bridge is not None
        bridge = self._ensure_bridge() if bridge is None else bridge
        now = time.monotonic()
        dt = 0.016 if self._last <= 0.0 else max(0.0, min(0.05, now - self._last))
        self._last = now
        if not externally_managed:
            bridge.update(dt)
        march_advance(bridge, dt)

        fw_i, fh_i = max(2, int(fw)), max(2, int(fh))
        points = _window_points(renderer)
        fullscreen = _renderer_is_fullscreen(renderer)
        if points is None:
            rw, rh = visualizer_internal_size(fw_i, fh_i, fullscreen=fullscreen)
        else:
            rw, rh = visualizer_internal_size(fw_i, fh_i, points[0], points[1], fullscreen=fullscreen)
        # Screensaver: fill the screen but never march above the capped size.
        cap = getattr(renderer, "_saver_render", None)
        if cap:
            try:
                cap_w, cap_h = max(2, int(cap[0])), max(2, int(cap[1]))
                if rw > cap_w or rh > cap_h:
                    scale = min(cap_w / rw, cap_h / rh)
                    rw = max(2, int(round(rw * scale)))
                    rh = max(2, int(round(rh * scale)))
            except (TypeError, ValueError, IndexError):
                pass
        # A 4K fullscreen surface is drawn at 1080p and scaled up, the same
        # policy the Vulkan renderer applies. Without this the OpenGL backend
        # raymarched four times the pixels on a 4K desktop.
        rw, rh = fullscreen_render_cap(rw, rh, fullscreen)
        # Thermal back-off. A Raspberry Pi throttles at 80-85 C, which shows up
        # as a sudden frame-rate collapse rather than a clear warning. Halve the
        # march resolution once the SoC is warm and again when it is hot, so the
        # picture keeps moving instead of stuttering. The check is rate-limited
        # because reading sysfs from the draw path is otherwise wasteful.
        if _LOW_POWER_GPU:
            rw, rh = self._thermally_limited_size(rw, rh)
        if (rw, rh) != (fw_i, fh_i) and self._ensure_target(GL, rw, rh) and self._ensure_upscale(GL):
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
            GL.glViewport(0, 0, rw, rh)
            try:
                self._draw_scene(GL, renderer, rw, rh, bridge, text_guard=text_guard)
            finally:
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
                GL.glViewport(0, 0, fw_i, fh_i)
            self._draw_upscale(GL, renderer)
            return True
        self._draw_scene(GL, renderer, fw_i, fh_i, bridge, text_guard=text_guard)
        return True

    def _draw_scene(self, GL, renderer, rw: int, rh: int, bridge,
                    text_guard: bool = True) -> None:
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glUseProgram(self._prog)
        locs = self._locs
        bands = list(bridge.bands16) + [0.0] * 16
        playing = 1.0 if bridge.is_playing else 0.0
        self._uniform4(GL, _loc(locs, "u_res"), float(rw), float(rh), float(bridge.accum_time), playing)
        self._uniform4(GL, _loc(locs, "u_audio0"), bridge.sub_bass, bridge.bass, bridge.low_mid, bridge.mid)
        self._uniform4(GL, _loc(locs, "u_audio1"), bridge.high_mid, bridge.treble, bridge.air, bridge.rms_energy)
        self._uniform4(GL, _loc(locs, "u_audio2"), bridge.beat_pulse, bridge.bass_phase, bridge.mid_phase, bridge.high_phase)
        self._uniform4(GL, _loc(locs, "u_bands0"), bands[0], bands[1], bands[2], bands[3])
        self._uniform4(GL, _loc(locs, "u_bands1"), bands[4], bands[5], bands[6], bands[7])
        self._uniform4(GL, _loc(locs, "u_bands2"), bands[8], bands[9], bands[10], bands[11])
        self._uniform4(GL, _loc(locs, "u_bands3"), bands[12], bands[13], bands[14], bands[15])
        # The preset id used to be hard-coded 0.0 here, so every preset looked
        # like Cosmic Dreamworlds on the OpenGL backend and the Dynamic Waves
        # rivers never drew at all.
        preset_id = float(getattr(renderer, "_preset_id_float", 0.0) or 0.0)
        self._uniform4(GL, _loc(locs, "u_preset"), preset_id, float(bridge.pitch_factor), 1.0, float(bridge.dynamic_time))
        try:
            import pid_stream
            sid, sprime, sheat, sserial = pid_stream.stream_vector(
                "pid" if getattr(renderer, "screensaver", False) else "audio",
                int(preset_id), float(bridge.accum_time), 1.0 / 60.0,
                bridge=bridge, key=f"gl-{id(renderer)}",
            )
        except Exception:
            sid = sprime = sheat = sserial = 0.0
        self._uniform4(GL, _loc(locs, "u_stream"), sid, sprime, sheat, sserial)
        cover = _loc(locs, "uCoverTex")
        if cover >= 0:
            GL.glUniform1i(cover, 0)
        guard = _loc(locs, "uTextGuard")
        if guard >= 0:
            GL.glUniform1f(guard, 1.0 if text_guard else 0.0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        GL.glBindVertexArray(renderer._vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)

    def _uniform4(self, GL, loc: int, x, y, z, w) -> None:
        if loc < 0:
            return
        GL.glUniform4f(loc, float(x), float(y), float(z), float(w))

    def _compile(self, GL, renderer=None, preset_id: int = 0) -> bool:
        preset_id = int(preset_id)
        if preset_id in self._failed:
            return False
        cached = self._programs.get(preset_id)
        if cached is None:
            try:
                vert_src = (ROOT / "shaders" / "liquid_glass_gl.vert").read_text(encoding="utf-8")
                # Desktop defaults to the full 84 steps; macOS and a Pi use their
                # reduced count. MP_VISUALIZER_GL_MARCH_STEPS applies everywhere.
                steps = visualizer_march_steps()
                frag_src = specialize_visualizer_source(opengl_visualizer_source(), preset_id, steps)
                prog, locs = self._link_program(GL, vert_src, frag_src, _UNIFORM_NAMES)
                if _loc(locs, "u_res") < 0:
                    raise RuntimeError("MP Visualizer shader uniforms did not link")
                cached = (prog, locs)
                self._programs[preset_id] = cached
            except Exception:
                # A failed compile used to run again on the next frame. Apple's
                # compiler spends a long time rejecting this shader.
                self._failed.add(preset_id)
                raise
        self._prog, self._locs = cached
        if not self._tex:
            self._tex = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_MIRRORED_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_MIRRORED_REPEAT)
            self._upload_solid(GL, 12, 40, 96)
        return True

    def _link_program(self, GL, vert_src: str, frag_src: str, names) -> tuple:
        vs = self._compile_stage(GL, GL.GL_VERTEX_SHADER, vert_src)
        fs = self._compile_stage(GL, GL.GL_FRAGMENT_SHADER, frag_src)
        prog = GL.glCreateProgram()
        GL.glAttachShader(prog, vs)
        GL.glAttachShader(prog, fs)
        # GLSL 1.40 has no explicit attribute locations. Bind the two the
        # renderer uses so glVertexAttribPointer(0/1) keeps working.
        try:
            for _name, _index in mp_raspberry_pi.glsl_attribute_locations(vert_src):
                GL.glBindAttribLocation(prog, _index, _name)
        except Exception:
            pass
        # A single fragment output normally binds to 0 implicitly; say it
        # explicitly for the stricter 3.1 core profile.
        try:
            GL.glBindFragDataLocation(prog, 0, "outColor")
        except Exception:
            pass
        GL.glLinkProgram(prog)
        GL.glDeleteShader(vs)
        GL.glDeleteShader(fs)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            log = GL.glGetProgramInfoLog(prog)
            if isinstance(log, bytes):
                log = log.decode("utf-8", errors="replace")
            raise RuntimeError(str(log))
        locs = {name: int(GL.glGetUniformLocation(prog, name)) for name in names}
        return int(prog), locs

    def _compile_stage(self, GL, stage, source: str):
        """Compile one stage at the version this context supports.

        Pre-adapting matters: Mesa 22.3 accepts a `#version 330` shader under a
        3.1 context with COMPILE_STATUS set, but drops `main` so the link fails
        with "vertex shader lacks `main'". Compiling the adapted source
        directly avoids that. `adapt_glsl` is a no-op at 3.30 and above.
        """
        version = self.glsl_version or "330"
        prepared = source if version in ("330", "410") else mp_raspberry_pi.adapt_glsl(source, version)

        shader = GL.glCreateShader(stage)
        GL.glShaderSource(shader, prepared)
        GL.glCompileShader(shader)
        if GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            return shader

        log = self._shader_log(GL, shader)
        GL.glDeleteShader(shader)
        raise RuntimeError(log)

    def _shader_log(self, GL, shader) -> str:
        log = GL.glGetShaderInfoLog(shader)
        if isinstance(log, bytes):
            log = log.decode("utf-8", errors="replace")
        return str(log)

    def _ensure_target(self, GL, rw: int, rh: int) -> bool:
        if self._fbo and self._target_size == (rw, rh):
            return True
        if not self._fbo:
            self._fbo = int(GL.glGenFramebuffers(1))
            self._color = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._color)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._color)
        internal = getattr(GL, "GL_RGBA8", GL.GL_RGBA)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, internal, int(rw), int(rh), 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self._color, 0
        )
        GL.glDrawBuffer(GL.GL_COLOR_ATTACHMENT0)
        complete = int(GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)) == int(GL.GL_FRAMEBUFFER_COMPLETE)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        if not complete:
            return False
        self._target_size = (int(rw), int(rh))
        return True

    def _ensure_upscale(self, GL) -> bool:
        if self._upscale:
            return True
        vert_src = (ROOT / "shaders" / "liquid_glass_gl.vert").read_text(encoding="utf-8")
        prog, locs = self._link_program(GL, vert_src, _UPSCALE_FRAG, ("uTex",))
        self._upscale = prog
        self._upscale_tex = _loc(locs, "uTex")
        return True

    def _draw_upscale(self, GL, renderer) -> None:
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glUseProgram(self._upscale)
        if self._upscale_tex >= 0:
            GL.glUniform1i(self._upscale_tex, 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._color)
        GL.glBindVertexArray(renderer._vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)

    def _ensure_bridge(self):
        if self._bridge is None:
            from mp_liquid_visualizer_vulkan import AudioTelemetryBridge
            self._bridge = AudioTelemetryBridge()
        return self._bridge

    def _ensure_cover(self, GL, state: Dict[str, Any]) -> None:
        key = f"{state.get('preset_id')}:{state.get('preset_key')}"
        if key == self._tex_key and self._tex:
            return
        preset = preset_by_id(int(state.get("preset_id") or 0)) or {
            "id": state.get("preset_id") or 0,
            "key": state.get("preset_key"),
            "name": state.get("preset_name"),
            "cover_paths": [],
        }
        width, height, pixels = 64, 64, b""
        try:
            from mp_liquid_visualizer_vulkan import load_preset_cover_rgba
            width, height, pixels = load_preset_cover_rgba(preset, tex_size=512)
        except Exception:
            pixels = b""
        if not pixels:
            self._upload_solid(GL, 18, 72, 140)
        else:
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, width, height, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, pixels)
        self._tex_key = key

    def _upload_solid(self, GL, r: int, g: int, b: int) -> None:
        """Bind a flat 2x2 colour when the artwork cannot be read."""
        pixel = bytes((r, g, b, 255)) * 4
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, 2, 2, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, pixel)

    def release(self) -> None:
        """Free the GL objects this layer owns, on the current context."""
        try:
            from OpenGL import GL
        except Exception:
            return
        for prog in [self._prog, self._upscale, *(p for p, _ in self._programs.values())]:
            if prog:
                try:
                    GL.glDeleteProgram(int(prog))
                except Exception:
                    pass
        for tex in (self._tex, self._color):
            if tex:
                try:
                    GL.glDeleteTextures([int(tex)])
                except Exception:
                    pass
        if self._fbo:
            try:
                GL.glDeleteFramebuffers(1, [int(self._fbo)])
            except Exception:
                pass
        self._prog = self._upscale = self._tex = self._color = self._fbo = 0
        self._programs.clear()
        self._failed.clear()
        self._tex_key = ""
        self._target_size = (0, 0)


class LiquidVisualizerOpenGLRenderer(MonitorSpanMixin, RandomPresetMixin, PlayerBondMixin,
                                     ZWindowCycleMixin):
    """Standalone MP Visualizer window drawn with OpenGL instead of Vulkan.

    This is the OpenGL twin of ``LiquidVisualizerVulkanRenderer``. It exists
    so the standalone visualizer runs on machines without a Vulkan driver,
    and when the configured Liquid Glass backend is OpenGL. It shares the
    preset shader, the telemetry bridge, and the window sizing helpers with
    the Vulkan renderer, so every preset behaves the same on both.
    """

    # The public surface below mirrors LiquidVisualizerVulkanRenderer.
    preset: Dict[str, Any]
    title = "MP Liquid Visualizer"

    @staticmethod
    def _saver_fullscreen_supported() -> bool:
        """Whether a per-monitor saver screen may switch to fullscreen.

        On Wayland the client cannot change its own fullscreen state without
        the compositor's agreement. GLFW's request there crashes the window on
        some drivers, so the saver places the window with KWin instead and
        skips the mode switch. On X11 the switch is the normal path.
        """
        try:
            import wayland_window_place as place
            return not place.is_wayland()
        except Exception:
            return True

    def __init__(self, preset_name: Optional[str] = None, fullscreen: bool = False,
                 target_4k: bool = False,
                 span_bounds: Optional[tuple] = None, span_role: str = "",
                 saver_render: Optional[tuple] = None):
        from mp_liquid_visualizer_vulkan import (
            WINDOW_MODE_DEFAULT, _RAND_KEYS, get_active_preset,
        )
        self.preset = get_active_preset(preset_name)
        # A preset named on the command line is a deliberate choice, so the
        # player-bond sync must not drag this window back to the player's
        # preset on its first poll. Without the pin, ``--preset 12`` opened the
        # player's saved preset instead, which is why the Dynamic Waves pair
        # used to be unreachable from a bonded window on this backend.
        self._preset_pinned = (bool(preset_name)
                               and str(preset_name).strip().lower() not in _RAND_KEYS)
        self.fullscreen_on_start = bool(fullscreen or target_4k)
        self.target_4k = bool(target_4k)
        self.should_close = False
        self.is_fullscreen = False
        self.screensaver = False
        self._wake_event = False
        # R toggles random mode: one new random preset per minute.
        self.init_random_mode()
        self._saver_render = None
        self._saver_bounds = None
        self._saver_cursor_primed = False
        # Bond to the Liquid Player. The companion window the player starts is
        # locked behind it; a window opened later stays free.
        self._init_player_bond()
        self._user_selected_window_mode = False
        self._windowed_size = (700, 700)
        self.width, self.height = 700, 700
        self.cursor = (0.0, 0.0)
        self.mouse_down = False
        self.window_mode = WINDOW_MODE_DEFAULT
        # The shape the window had when the user first pressed Z; the third
        # press restores it exactly (size and position).
        self._z_original_size = None
        self._z_original_pos = None
        self._alive = False
        self._glfw = None
        self._window = None
        self._layer = PlayerVisualizerLayer()
        self._bridge = None
        self._last = 0.0
        self._vao = 0
        self._vbo = 0
        self._cur_pos = (72, 64)
        self._size = (700, 700)
        # F10/F11 monitor spanning and the screensaver render cap.
        self._init_span_state(span_bounds, span_role)
        self._saver_render = tuple(saver_render) if saver_render else None
        if span_bounds:
            bx, by, bw, bh = (int(v) for v in span_bounds)
            cover = min(bw, bh)
            self._size = (cover, cover)
            self._cur_pos = (bx + (bw - cover) // 2, by + (bh - cover) // 2)
        self._on_key_cb = None
        self._on_mouse_cb = None
        self._on_cursor_cb = None
        # Left-drag moves the borderless window, matching the Vulkan renderer
        # and the Liquid Player glass. Without this the visualizer could not be
        # moved at all once it opened.
        self._drag_active = False
        self._drag_origin = (0, 0)
        self._drag_press_screen = (0, 0)
        self._last_click_at = 0.0
        self._last_click_pos: Optional[tuple] = None
        # Same rounded silhouette as the Liquid Player window.
        self._shaper = RoundedWindowShaper(DEFAULT_CORNER_RADIUS)

    # -- identity ---------------------------------------------------------
    @property
    def summary(self) -> str:
        return (
            f"MP_Liquid_Visualizer OpenGL device: {self.renderer_name} "
            f"(vendor: {self.vendor_name}, version: {self.gl_version}) | "
            f"Preset: {self.preset['name']} | Window: {self.width}x{self.height}"
        )

    # -- native window handles --------------------------------------------
    def win32_window(self) -> int:
        g, w = getattr(self, "_glfw", None), getattr(self, "_window", None)
        if g is None or not w or not sys.platform.startswith("win"):
            return 0
        try:
            return int(g.glfwGetWin32Window(w) or 0)
        except Exception:
            return 0

    def cocoa_window(self) -> int:
        g, w = getattr(self, "_glfw", None), getattr(self, "_window", None)
        if g is None or not w or sys.platform != "darwin":
            return 0
        try:
            return int(g.glfwGetCocoaWindow(w) or 0)
        except Exception:
            return 0

    def x11_window(self) -> int:
        g, w = getattr(self, "_glfw", None), getattr(self, "_window", None)
        if g is None or not w or sys.platform.startswith("win") or sys.platform == "darwin":
            return 0
        try:
            return int(g.glfwGetX11Window(w) or 0)
        except Exception:
            return 0

    def x11_display(self):
        g, w = getattr(self, "_glfw", None), getattr(self, "_window", None)
        if g is None or not w or sys.platform.startswith("win") or sys.platform == "darwin":
            return None
        try:
            return g.glfwGetX11Display(w)
        except Exception:
            return None

    def _apply_window_shape(self) -> bool:
        """Clip the window to the Liquid Player's rounded corners.

        The bonded visualizer sits behind the player at the same size, so an
        unclipped square window showed black wedges in the player's corners.
        Fullscreen and screensaver windows stay rectangular.
        """
        shaper = getattr(self, "_shaper", None)
        window = getattr(self, "_window", None)
        if shaper is None or not window:
            return False
        if self.is_fullscreen or getattr(self, "_saver_bounds", None) or self.screensaver:
            # Fullscreen and screensaver windows fill their area, so keep the
            # plain rectangle.
            return shaper.apply(width=1, height=1, enabled=False,
                                x11_window=self.x11_window(), x11_display=self.x11_display(),
                                win32_window=self.win32_window(), cocoa_window=self.cocoa_window())
        width, height = self.window_size()
        return shaper.apply(
            width=width,
            height=height,
            enabled=True,
            x11_window=self.x11_window(),
            x11_display=self.x11_display(),
            win32_window=self.win32_window(),
            cocoa_window=self.cocoa_window(),
        )

    # -- lifecycle --------------------------------------------------------
    def open(self):
        from mp_liquid_visualizer_vulkan import load_glfw_dll
        glfw = load_glfw_dll()
        self._glfw = glfw
        self._bind_glfw(glfw)
        # Pin GLFW to X11. The Liquid Player and MP Gui are X11 hosts (GLFW and
        # GTK), and their windows are positioned by xwininfo/wmctrl. A Wayland
        # backend window has no X11 handle, so it cannot be placed and is
        # invisible to every geometry helper here.
        if sys.platform.startswith("linux") and hasattr(glfw, "glfwInitHint"):
            try:
                glfw.glfwInitHint(GLFW_PLATFORM, GLFW_PLATFORM_X11)
            except Exception:
                pass
        if not glfw.glfwInit():
            raise RuntimeError("glfwInit failed")

        # Walk down the context ladder until the driver accepts one. A Pi 4/5
        # V3D driver tops out at OpenGL 3.1 core, where a fixed 3.3 request
        # returned no window at all.
        saver_bounds = getattr(self, "_saver_bounds", None)
        span_bounds = getattr(self, "_span_bounds", None)
        # A locked companion opens straight onto the player it belongs behind,
        # so it never flashes in the corner and then snaps into place.
        bond_geometry = None if (saver_bounds or span_bounds) else self._bond_initial_geometry()
        if saver_bounds:
            # Screensaver: fill the assigned monitor's desktop rect exactly.
            ww = max(64, int(saver_bounds[2]))
            wh = max(64, int(saver_bounds[3]))
        elif span_bounds:
            # A spawned F10/F11 window starts on its own monitor, then fills it
            # after the two-second delay.
            ww = max(64, int(self._size[0]))
            wh = max(64, int(self._size[1]))
        elif bond_geometry:
            ww, wh = max(64, int(bond_geometry[2])), max(64, int(bond_geometry[3]))
        else:
            ww, wh = self._initial_size()
        self._size = (ww, wh)
        self._windowed_size = (ww, wh)
        self.width, self.height = ww, wh

        window = 0
        # An owned companion the button has already turned off opens unmapped:
        # the first frame would otherwise show the scene before the poll hides
        # it again. A free window, a GUI companion and a screensaver window
        # always open visible.
        start_visible = 0 if self._bond_should_start_hidden() else 1
        for major, minor, profile in mp_raspberry_pi.gl_create_attempts():
            glfw.glfwDefaultWindowHints()
            hints = [
                (GLFW_CLIENT_API, GLFW_OPENGL_API),
                (GLFW_DECORATED, 0),
                (GLFW_VISIBLE, start_visible),
                (GLFW_FOCUSED, 1),
                (GLFW_RESIZABLE, 0),
            ]
            if major:
                hints.insert(1, (GLFW_CONTEXT_VERSION_MAJOR, major))
                hints.insert(2, (GLFW_CONTEXT_VERSION_MINOR, minor))
            if profile == "core":
                hints.insert(3, (GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE))
            for hint, value in hints:
                glfw.glfwWindowHint(hint, value)
            if sys.platform == "darwin":
                glfw.glfwWindowHint(GLFW_OPENGL_FORWARD_COMPAT, 1)
            if hasattr(glfw, "glfwWindowHintString"):
                try:
                    glfw.glfwWindowHintString(0x00024001, b"MP_Visualizer")
                    glfw.glfwWindowHintString(0x00024002, b"mp_visualizer")
                except Exception:
                    pass
            window = glfw.glfwCreateWindow(ww, wh, self.title.encode("utf-8"), None, None)
            if window:
                self.requested_gl = (major, minor, profile)
                break

        if not window:
            raise RuntimeError(
                "GLFW could not open an OpenGL window for the MP Visualizer. "
                "Install the GPU driver (on Raspberry Pi OS: sudo apt install "
                "libglfw3 mesa-utils libgl1-mesa-dri) and check X11/XWayland."
            )
        self._window = window
        # Keep the map state in step with the hint that opened it, so the
        # first visibility poll does not try to hide an already hidden window.
        self._companion_visible = bool(start_visible)
        if saver_bounds:
            ox, oy = int(saver_bounds[0]), int(saver_bounds[1])
        elif span_bounds:
            ox, oy = int(self._cur_pos[0]), int(self._cur_pos[1])
        elif bond_geometry:
            ox, oy = int(bond_geometry[0]), int(bond_geometry[1])
        else:
            ox, oy = self._initial_offset()
        glfw.glfwSetWindowPos(window, ox, oy)
        self._cur_pos = (ox, oy)
        self._bind_input()
        glfw.glfwMakeContextCurrent(window)
        try:
            # Divide vsync when the display is faster than the frame cap, so a
            # 60 Hz panel on a Raspberry Pi blocks in the driver instead of
            # spinning in the loop. The loop deadline handles any remainder.
            from liquid_glass_opengl import monitor_refresh_hz
            glfw.glfwSwapInterval(
                mp_raspberry_pi.swap_interval_for_cap(
                    monitor_refresh_hz(glfw, window),
                    mp_raspberry_pi.visualizer_fps_cap(),
                )
            )
        except Exception:
            pass
        # libX11 is needed for the root pointer while dragging, and for the
        # XShape mask that matches the player silhouette.
        if sys.platform.startswith("linux"):
            try:
                import ctypes
                from ctypes import POINTER, c_char_p
                x11 = ctypes.CDLL("libX11.so.6")
                x11.XOpenDisplay.restype = c_void_p
                x11.XOpenDisplay.argtypes = [c_char_p]
                x11.XDefaultRootWindow.restype = c_uint64
                x11.XDefaultRootWindow.argtypes = [c_void_p]
                x11.XQueryPointer.argtypes = [
                    c_void_p, c_uint64,
                    POINTER(c_uint64), POINTER(c_uint64),
                    POINTER(c_int), POINTER(c_int),
                    POINTER(c_int), POINTER(c_int), POINTER(c_uint32),
                ]
                self._x11 = x11
            except Exception as exc:
                if os.environ.get("MP_VISUALIZER_DEBUG"):
                    print(f"MP Visualizer: X11 setup failed: {exc}", file=sys.stderr)
                self._x11 = None
        self._init_gl()
        # The draw layer compiles the shaders, so it has to know which GLSL
        # version this context accepts before its first frame. Without this a
        # Pi context got `#version 330` and failed to draw.
        self._layer.glsl_version = self.glsl_version
        self._alive = True
        self._apply_window_shape()
        if self.fullscreen_on_start:
            self.toggle_fullscreen()
        if self._span_role == "spawned":
            # The two-second clock starts when the window is visible.
            import time as _time
            self._span_fullscreen_at = _time.monotonic() + 2.0
        print(self.summary, flush=True)
        return self

    def _initial_size(self) -> tuple:
        from mp_liquid_visualizer_vulkan import _IS_MAC, _IS_LINUX, WINDOW_WIDTH
        if _IS_LINUX:
            try:
                from liquid_player_visualizer import linux_visualizer_match_window_size
            except ImportError:
                from scripts.liquid_player_visualizer import linux_visualizer_match_window_size
            return tuple(int(v) for v in linux_visualizer_match_window_size())
        if _IS_MAC:
            try:
                from liquid_player_visualizer import macos_liquid_player_window_size
            except ImportError:
                from scripts.liquid_player_visualizer import macos_liquid_player_window_size
            return tuple(int(v) for v in macos_liquid_player_window_size())
        return (WINDOW_WIDTH, WINDOW_WIDTH)

    def _initial_offset(self) -> tuple:
        from mp_liquid_visualizer_vulkan import _IS_LINUX, window_launch_origin
        if _IS_LINUX:
            # Centre the visualizer instead of dropping it in the corner. The
            # window matches the Liquid Player, which is itself centred, so a
            # corner offset used to leave the pair hanging off the right edge.
            from desktop_launch_screen import main_window_origin
            return main_window_origin(int(self._size[0]), int(self._size[1]), 72, 64)
        x, y = window_launch_origin(160, 80)
        return (int(x), int(y))

    def _bind_glfw(self, g) -> None:
        from ctypes import POINTER, c_char_p, c_int, c_uint64, c_void_p
        g.glfwInit.restype = c_int
        # GLFW 3.4+ only. Raspberry Pi OS Bookworm ships 3.3, where an
        # unconditional attribute write raises AttributeError at startup.
        if hasattr(g, "glfwInitHint"):
            g.glfwInitHint.argtypes = [c_int, c_int]
        g.glfwWindowHint.argtypes = [c_int, c_int]
        g.glfwDefaultWindowHints.argtypes = []
        if hasattr(g, "glfwWindowHintString"):
            g.glfwWindowHintString.argtypes = [c_int, c_char_p]
        g.glfwCreateWindow.restype = c_void_p
        g.glfwCreateWindow.argtypes = [c_int, c_int, c_char_p, c_void_p, c_void_p]
        g.glfwDestroyWindow.argtypes = [c_void_p]
        g.glfwMakeContextCurrent.argtypes = [c_void_p]
        g.glfwSwapInterval.argtypes = [c_int]
        g.glfwSwapBuffers.argtypes = [c_void_p]
        g.glfwPollEvents.argtypes = []
        g.glfwWindowShouldClose.argtypes = [c_void_p]
        g.glfwWindowShouldClose.restype = c_int
        g.glfwSetWindowShouldClose.argtypes = [c_void_p, c_int]
        g.glfwGetWindowSize.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
        g.glfwGetFramebufferSize.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
        g.glfwGetWindowPos.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
        g.glfwSetWindowPos.argtypes = [c_void_p, c_int, c_int]
        g.glfwSetWindowSize.argtypes = [c_void_p, c_int, c_int]
        g.glfwSetWindowSizeLimits.argtypes = [c_void_p, c_int, c_int, c_int, c_int]
        g.glfwSetMouseButtonCallback.argtypes = [c_void_p, c_void_p]
        g.glfwSetCursorPosCallback.argtypes = [c_void_p, c_void_p]
        g.glfwSetKeyCallback.argtypes = [c_void_p, c_void_p]
        if hasattr(g, "glfwSetWindowAttrib"):
            g.glfwSetWindowAttrib.argtypes = [c_void_p, c_int, c_int]
        if hasattr(g, "glfwGetWindowAttrib"):
            g.glfwGetWindowAttrib.argtypes = [c_void_p, c_int]
            g.glfwGetWindowAttrib.restype = c_int
        # The Visualization button hides/shows the owned companion. Binding
        # them here rather than per call keeps the ctypes signature in one
        # place; a missing symbol simply leaves the flag inert.
        if hasattr(g, "glfwHideWindow"):
            g.glfwHideWindow.argtypes = [c_void_p]
        if hasattr(g, "glfwShowWindow"):
            g.glfwShowWindow.argtypes = [c_void_p]
        if sys.platform.startswith("win"):
            if hasattr(g, "glfwGetWin32Window"):
                g.glfwGetWin32Window.argtypes = [c_void_p]
                g.glfwGetWin32Window.restype = c_void_p
        elif sys.platform == "darwin":
            if hasattr(g, "glfwGetCocoaWindow"):
                g.glfwGetCocoaWindow.argtypes = [c_void_p]
                g.glfwGetCocoaWindow.restype = c_void_p
        else:
            g.glfwGetX11Window.argtypes = [c_void_p]
            g.glfwGetX11Window.restype = c_uint64
            if hasattr(g, "glfwGetX11Display"):
                g.glfwGetX11Display.argtypes = [c_void_p]
                g.glfwGetX11Display.restype = c_void_p
        # Monitor discovery and placement, used by the F10/F11 monitor span.
        from mp_liquid_visualizer_vulkan import GLFWvidmode
        g.glfwGetPrimaryMonitor.restype = c_void_p
        g.glfwGetMonitors.restype = POINTER(c_void_p)
        g.glfwGetMonitors.argtypes = [POINTER(c_int)]
        g.glfwGetMonitorPos.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
        g.glfwGetVideoMode.argtypes = [c_void_p]
        g.glfwGetVideoMode.restype = POINTER(GLFWvidmode)
        g.glfwGetMonitorName.argtypes = [c_void_p]
        g.glfwGetMonitorName.restype = c_char_p
        g.glfwSetWindowMonitor.argtypes = [c_void_p, c_void_p, c_int, c_int, c_int, c_int, c_int]

    def apply_saver_geometry(self) -> None:
        """Resize and move an already-open window onto its screensaver monitor."""
        bounds = getattr(self, "_saver_bounds", None)
        if not bounds or not getattr(self, "_window", None):
            return
        g = getattr(self, "_glfw", None)
        if not g:
            return
        from ctypes import c_int
        bx, by = int(bounds[0]), int(bounds[1])
        bw = max(64, int(bounds[2]))
        bh = max(64, int(bounds[3]))
        try:
            g.glfwSetWindowSizeLimits(self._window, 0, 0, 0x7FFFFFFF, 0x7FFFFFFF)
        except Exception:
            pass
        try:
            g.glfwSetWindowSize(self._window, bw, bh)
            g.glfwSetWindowPos(self._window, bx, by)
        except Exception:
            pass
        self.width, self.height = bw, bh
        self._size = (bw, bh)
        self._windowed_size = (bw, bh)
        self._cur_pos = (bx, by)

    def _bind_input(self) -> None:
        from ctypes import CFUNCTYPE, c_double, c_int, c_void_p
        self._on_key_cb = CFUNCTYPE(None, c_void_p, c_int, c_int, c_int, c_int)(self._on_key)
        self._on_mouse_cb = CFUNCTYPE(None, c_void_p, c_int, c_int, c_int)(self._on_mouse)
        self._on_cursor_cb = CFUNCTYPE(None, c_void_p, c_double, c_double)(self._on_cursor)
        g, w = self._glfw, self._window
        g.glfwSetKeyCallback(w, cast(self._on_key_cb, c_void_p))
        if hasattr(g, "glfwSetMouseButtonCallback"):
            g.glfwSetMouseButtonCallback(w, cast(self._on_mouse_cb, c_void_p))
        if hasattr(g, "glfwSetCursorPosCallback"):
            g.glfwSetCursorPosCallback(w, cast(self._on_cursor_cb, c_void_p))

    def _on_key(self, _w, key, _sc, action, _mods) -> None:
        if action != 1:  # GLFW_PRESS
            return
        if self.screensaver:
            # Screensaver swallows every keypress: the first one only wakes it.
            self._wake_event = True
            self.request_close()
            return
        if key == 256:  # ESCAPE
            self.request_close()
        elif key == 81:  # Q
            # A bonded companion belongs to the player. Vulkan and Metal leave
            # it up; Q only closes a free window. Escape still closes it.
            if not self._player_bonded:
                self.request_close()
        elif key == 90:  # Z
            # The locked companion owns its geometry: it stays behind the
            # player and never cycles to half size or fullscreen.
            if not self._bond_is_locked():
                self._cycle_window_mode_for_user()
        elif key == 72:  # H: hidden mode (hide or restore every MP window)
            try:
                import mp_hidden_mode as hidden
                hidden.toggle()
            except Exception:
                pass
        elif key == 82:  # R: toggle random mode
            self.toggle_random_mode()
        elif 49 <= key <= 57:  # 1..9
            from mp_liquid_visualizer_vulkan import presets_by_slot
            slots = presets_by_slot()
            idx = key - 49
            if 0 <= idx < len(slots):
                self.switch_preset(slots[idx])
        elif key in (61, 334, 45, 333):  # =/+ or -/-
            from mp_liquid_visualizer_vulkan import cycle_preset
            forward = key in (61, 334)
            nxt = cycle_preset(self.preset, 1 if forward else -1)
            if nxt is not None:
                self.switch_preset(nxt)
        elif key in (263, 262):  # Left / Right
            # Same wrap-around preset stepping as the Vulkan window. This is the
            # only place an arrow changes a preset: the MP Liquid Player keeps
            # Left/Right for ±10 s seeking in every state.
            from mp_liquid_visualizer_vulkan import cycle_preset
            nxt = cycle_preset(self.preset, 1 if key == 262 else -1)
            if nxt is not None:
                self.switch_preset(nxt)
        else:
            # F10 spans every other monitor; F11 does it with random presets.
            self.handle_span_key(key)

    def _on_mouse(self, _w, button, action, _mods) -> None:
        if self.screensaver:
            if action == 1:
                self._wake_event = True
                self.request_close()
            return
        if button != 0:
            return
        self.mouse_down = action == 1
        if not self.mouse_down:
            self._drag_active = False
            return
        rx, ry = self._screen_pointer()
        if self._take_double_click(rx, ry):
            # A double-click is a window action, never the start of a drag.
            self._drag_active = False
            # The locked companion belongs behind the player; it must not cover
            # it by going fullscreen.
            if not self._bond_is_locked():
                self.toggle_fullscreen()
            return
        if not self.is_fullscreen and not self._bond_is_locked():
            self._drag_active = True
            self._drag_origin = self.window_pos()
            self._drag_press_screen = (rx, ry)
        else:
            self._drag_active = False

    def _screen_pointer(self) -> tuple:
        """Pointer position in screen coordinates, for drag tracking."""
        rx, ry = self.root_pointer()
        if rx or ry:
            return int(rx), int(ry)
        wx, wy = self.window_pos()
        return int(wx + self.cursor[0]), int(wy + self.cursor[1])

    def root_pointer(self) -> tuple:
        """Pointer position from the X server, or (0, 0) when unavailable."""
        if not sys.platform.startswith("linux"):
            return (0, 0)
        x11 = getattr(self, "_x11", None)
        display = self.x11_display()
        if not x11 or not display:
            return (0, 0)
        try:
            from ctypes import POINTER
            root, child = c_uint64(), c_uint64()
            rx, ry = c_int(), c_int()
            wx, wy = c_int(), c_int()
            mask = c_uint32()
            if x11.XQueryPointer(
                display,
                x11.XDefaultRootWindow(display),
                byref(root),
                byref(child),
                byref(rx),
                byref(ry),
                byref(wx),
                byref(wy),
                byref(mask),
            ):
                return int(rx.value), int(ry.value)
        except Exception:
            pass
        return (0, 0)

    def _take_double_click(self, x: int, y: int) -> bool:
        """Return True when this press completes a double-click, and consume it."""
        import time as _time
        now = _time.monotonic()
        last, at = self._last_click_at, self._last_click_pos
        if last > 0.0 and at is not None and now - last < 0.45:
            if abs(x - at[0]) <= 35 and abs(y - at[1]) <= 35:
                self._last_click_at, self._last_click_pos = 0.0, None
                return True
        self._last_click_at, self._last_click_pos = now, (int(x), int(y))
        return False

    def _on_cursor(self, _w, x, y) -> None:
        if self.screensaver:
            # GLFW reports a position as soon as the window appears; ignore it.
            if self._saver_cursor_primed:
                self._wake_event = True
                self.request_close()
            self._saver_cursor_primed = True
            return
        self.cursor = (float(x), float(y))
        if self._drag_active and self.mouse_down and not self.is_fullscreen:
            rx, ry = self.root_pointer()
            if rx or ry:
                self.set_window_pos(
                    int(self._drag_origin[0] + (rx - self._drag_press_screen[0])),
                    int(self._drag_origin[1] + (ry - self._drag_press_screen[1])),
                )

    def _init_gl(self) -> None:
        import numpy as np
        from OpenGL import GL
        self.vendor_name = (GL.glGetString(GL.GL_VENDOR) or b"Unknown").decode("utf-8", "replace")
        self.renderer_name = (GL.glGetString(GL.GL_RENDERER) or b"OpenGL").decode("utf-8", "replace")
        self.gl_version = (GL.glGetString(GL.GL_VERSION) or b"Unknown").decode("utf-8", "replace")
        # Shader stages compile against this version. A Raspberry Pi driver
        # reports 3.1 and rejects `#version 330`, so ask for GLSL 1.40 there.
        # GL_VERSION wins when it advertises more than the granted context, as
        # Mesa does when it answers a 3.0 compat request on a 3.1 driver.
        major, minor = getattr(self, "requested_gl", (3, 3, "core"))[:2]
        import re as _re
        match = _re.search(r"(\d+)\.(\d+)", self.gl_version)
        if match:
            gl_major, gl_minor = int(match.group(1)), int(match.group(2))
            if (gl_major, gl_minor) > (major, minor):
                major, minor = gl_major, gl_minor
        if not major:
            major, minor = (gl_major, gl_minor) if match else (3, 3)
        self.glsl_version = mp_raspberry_pi.preferred_glsl_version(major, minor)
        quad = np.array([
            -1.0, 1.0, 0.0, 0.0,
            -1.0, -1.0, 0.0, 1.0,
            1.0, -1.0, 1.0, 1.0,
            -1.0, 1.0, 0.0, 0.0,
            1.0, -1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 0.0,
        ], dtype=np.float32)
        self._vao = int(GL.glGenVertexArrays(1))
        self._vbo = int(GL.glGenBuffers(1))
        GL.glBindVertexArray(self._vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, quad.nbytes, quad, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, c_void_p(0))
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, c_void_p(8))
        GL.glBindVertexArray(0)

    # -- drawing ----------------------------------------------------------
    def draw(self, bridge, capture: bool = False):
        from OpenGL import GL
        if not self.poll():
            return None
        # A minimized or hidden window shows no frame, so it is not rendered.
        if not capture and self.view_hidden():
            return None
        # Random mode advances here so it follows the real render clock.
        self.poll_random_mode()
        fw, fh = self.framebuffer_size()
        if fw < 2 or fh < 2:
            return None
        # Match the Liquid Player's rounded corners on every resize, window
        # mode change, and fullscreen toggle. The shaper caches, so the usual
        # frame pays nothing.
        self._apply_window_shape()
        state = {
            "active": True,
            "preset_id": int(self.preset.get("id") or 0),
            "preset_key": self.preset.get("key"),
            "preset_name": self.preset.get("name"),
        }
        GL.glViewport(0, 0, fw, fh)
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if not self._layer.draw_with_state(self, fw, fh, state, bridge=bridge,
                                           text_guard=False):
            raise RuntimeError(self._layer._error or "MP Visualizer could not draw with OpenGL")
        pixels = None
        if capture:
            raw = GL.glReadPixels(0, 0, fw, fh, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            pixels = (fw, fh, bytes(raw))
        self._glfw.glfwSwapBuffers(self._window)
        return pixels

    # -- window state -----------------------------------------------------
    def view_hidden(self) -> bool:
        """True while the window is minimized or hidden; see the Vulkan renderer."""
        g, w = getattr(self, "_glfw", None), getattr(self, "_window", None)
        if g is None or not w or not hasattr(g, "glfwGetWindowAttrib"):
            return False
        try:
            return (g.glfwGetWindowAttrib(w, GLFW_ICONIFIED) == 1
                    or g.glfwGetWindowAttrib(w, GLFW_VISIBLE) == 0)
        except Exception:
            return False

    def poll(self) -> bool:
        g, w = self._glfw, self._window
        if not g or not w:
            return not self.should_close
        g.glfwPollEvents()
        if g.glfwWindowShouldClose(w):
            self.should_close = True
        # Follow the Liquid Player behind it. This is what keeps the companion
        # window locked to the player through Normal, Mini and Micro.
        self._sync_player_bond()
        self.poll_monitor_span()
        return not self.should_close

    def window_size(self) -> tuple:
        from ctypes import byref, c_int
        w, h = c_int(), c_int()
        self._glfw.glfwGetWindowSize(self._window, byref(w), byref(h))
        return max(1, int(w.value)), max(1, int(h.value))

    def framebuffer_size(self) -> tuple:
        from ctypes import byref, c_int
        w, h = c_int(), c_int()
        self._glfw.glfwGetFramebufferSize(self._window, byref(w), byref(h))
        return max(1, int(w.value)), max(1, int(h.value))

    def window_pos(self) -> tuple:
        return self._cur_pos

    def set_window_pos(self, x: int, y: int) -> None:
        self._cur_pos = (int(x), int(y))
        self._glfw.glfwSetWindowPos(self._window, int(x), int(y))

    def request_close(self) -> None:
        self.should_close = True
        if self._window:
            self._glfw.glfwSetWindowShouldClose(self._window, 1)

    def toggle_fullscreen(self) -> bool:
        from mp_liquid_visualizer_vulkan import RENDER_1080P_WIDTH, RENDER_1080P_HEIGHT
        g, w = self._glfw, self._window
        if not g or not w:
            self.is_fullscreen = not self.is_fullscreen
            return self.is_fullscreen
        if not self.is_fullscreen:
            self._saved_pos = self.window_pos()
            fw, fh = RENDER_1080P_WIDTH, RENDER_1080P_HEIGHT
            try:
                mon = self.span_monitor() or g.glfwGetPrimaryMonitor()
                mode = g.glfwGetVideoMode(mon) if hasattr(g, "glfwGetVideoMode") else None
                if mode:
                    md = mode.contents
                    # A 4K panel stays at 1080p, matching the Vulkan renderer.
                    if md.width and md.height and (md.width < 3840 or md.height < 2160):
                        fw, fh = int(md.width), int(md.height)
            except Exception:
                pass
            self.is_fullscreen = True
            try:
                g.glfwSetWindowSizeLimits(w, -1, -1, -1, -1)
            except Exception:
                pass
            filled = False
            if self._span_bounds and mon and hasattr(g, "glfwSetWindowMonitor"):
                # Fill exactly the monitor this window was assigned to, using
                # that monitor's own mode rather than the global default.
                try:
                    md = g.glfwGetVideoMode(mon).contents
                    g.glfwSetWindowMonitor(w, mon, 0, 0, int(md.width), int(md.height), int(md.refreshRate or 0))
                    fw, fh = int(md.width), int(md.height)
                    filled = True
                except Exception:
                    filled = False
            if not filled:
                try:
                    g.glfwSetWindowPos(w, 0, 0)
                    g.glfwSetWindowSize(w, fw, fh)
                except Exception:
                    pass
            self.width, self.height = fw, fh
        else:
            x, y = getattr(self, "_saved_pos", self._cur_pos)
            ww, wh = self._size
            self.is_fullscreen = False
            try:
                g.glfwSetWindowSize(w, ww, wh)
                g.glfwSetWindowPos(w, int(x), int(y))
            except Exception:
                pass
            self._cur_pos = (int(x), int(y))
            self.width, self.height = ww, wh
        return self.is_fullscreen

    # -- ZWindowCycleMixin primitives -------------------------------------
    def _z_window_size(self) -> tuple:
        return int(self._size[0]), int(self._size[1])

    def _z_window_pos(self) -> tuple:
        return self.window_pos()

    def _z_apply_windowed(self, width: int, height: int, x: int, y: int) -> None:
        """Place the window at width/height/x/y and leave fullscreen.

        This backend's ``toggle_fullscreen`` fills the monitor by moving and
        resizing a borderless window rather than by a GLFW monitor mode, so
        leaving it is the same two calls in reverse.
        """
        g, w = self._glfw, self._window
        self.is_fullscreen = False
        self._resize_window(width, height)
        if g and w:
            try:
                g.glfwSetWindowPos(w, int(x), int(y))
                # The size limit follows the chosen mode, or the next frame
                # would put the window back to the shape it had before Z.
                g.glfwSetWindowSizeLimits(w, int(width), int(height),
                                          int(width), int(height))
            except Exception:
                pass
        self._cur_pos = (int(x), int(y))
        self._resize = True

    def _resize_window(self, width: int, height: int) -> None:
        g, w = self._glfw, self._window
        width, height = max(64, int(width)), max(64, int(height))
        self._size = (width, height)
        self._windowed_size = (width, height)
        self.width, self.height = width, height
        if not g or not w:
            return
        try:
            g.glfwSetWindowSizeLimits(w, -1, -1, -1, -1)
            g.glfwSetWindowSize(w, width, height)
        except Exception:
            pass

    def _set_windowed_size(self, width: int, height: int, apply: bool = False) -> None:
        """Set (and optionally apply) the windowed size.

        Required by ``PlayerBondMixin``: a bonded companion takes the Liquid
        Player's size, including its Mini/Micro sizes. ``_size`` is this
        renderer's own name for the windowed size, so keep the two in step.
        """
        width, height = max(64, int(width)), max(64, int(height))
        self._size = (width, height)
        if apply and not self.is_fullscreen:
            self._resize_window(width, height)
        else:
            self.width, self.height = width, height

    def _apply_bonded_size_limits(self, width: int, height: int) -> None:
        """Pin the size while bonded, so the window cannot be resized away."""
        g, w = self._glfw, self._window
        if not g or not w:
            return
        try:
            g.glfwSetWindowSizeLimits(w, int(width), int(height), int(width), int(height))
        except Exception:
            pass

    def _release_bonded_size_limits(self) -> None:
        g, w = self._glfw, self._window
        if not g or not w:
            return
        try:
            g.glfwSetWindowSizeLimits(w, -1, -1, -1, -1)
        except Exception:
            pass

    def switch_preset(self, preset: Dict[str, Any]) -> None:
        previous = self.preset.get("key")
        self.preset = dict(preset)
        if self.preset.get("key") == previous:
            return
        # A bonded companion is the same visualizer the MP Liquid Player shows.
        # Publish the choice so the player's next bond poll does not switch the
        # preset straight back. A spawned monitor-span window and the
        # screensaver are viewers, so neither writes anything.
        if getattr(self, "_span_role", "") == ROLE_SPAWNED or getattr(self, "screensaver", False):
            return
        self._publish_bonded_preset(self.preset)

    # -- close ------------------------------------------------------------
    def close(self) -> None:
        self.shutdown_monitor_span()
        try:
            self._layer.release()
        except Exception:
            pass
        try:
            from OpenGL import GL
            if self._vao:
                GL.glDeleteVertexArrays(1, [self._vao])
            if self._vbo:
                GL.glDeleteBuffers(1, [self._vbo])
        except Exception:
            pass
        if self._window and self._glfw:
            try:
                self._glfw.glfwDestroyWindow(self._window)
                self._glfw.glfwTerminate()
            except Exception:
                pass
        self._window = None
        self._alive = False

    def _upload_solid(self, GL, r: int, g: int, b: int) -> None:
        pixel = bytes((r, g, b, 255)) * 4
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, 2, 2, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, pixel)


def gui_visualizer_visible() -> bool:
    """True when MP Gui's Visualization button is on.

    A state file written before the key existed, or by a host that does not
    know it, reads as visible so an upgraded build never hides a background
    the user did not turn off.
    """
    try:
        return read_player_visualizer_state().get("gui_visible") is not False
    except Exception:
        return True


def set_gui_visualizer_visible(visible: bool) -> Dict[str, Any]:
    """Turn MP Gui's companion background on or off.

    Only ``gui_visible`` changes: ``active`` still says the window belongs
    behind MP Gui, so the companion is unmapped rather than un-bonded or
    closed, and pressing the button again maps the same window back with its
    geometry and preset intact. The preset is kept so a toggle never changes
    the picture the user chose.
    """
    current = read_player_visualizer_state()
    preset = {
        "id": int(current.get("preset_id") or 0),
        "key": str(current.get("preset_key") or "cosmic_dreamworlds_liquid"),
        "name": str(current.get("preset_name") or "Cosmic Dreamworlds (Liquid)"),
    }
    return write_player_visualizer_state(True, preset, visible=None,
                                         gui_visible=bool(visible))


def _visualizer_state_cli(argv: Optional[List[str]] = None) -> int:
    """``--gui-visible`` / ``--set-gui-visible on|off`` for the shell menus.

    MP Gui is a bash menu, so it cannot import this module in-process. This
    keeps the file format in one place instead of re-implementing the JSON
    read and write in shell.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ["--gui-visible"]:
            print("on" if gui_visualizer_visible() else "off")
            return 0
        if args[:1] == ["--set-gui-visible"]:
            wanted = (args[1] if len(args) > 1 else "").strip().lower()
            if wanted in ("on", "true", "1", "show"):
                set_gui_visualizer_visible(True)
            elif wanted in ("off", "false", "0", "hide"):
                set_gui_visualizer_visible(False)
            else:
                print("usage: --set-gui-visible on|off", file=sys.stderr)
                return 2
            return 0
        if args[:1] == ["--toggle-gui-visible"]:
            set_gui_visualizer_visible(not gui_visualizer_visible())
            return 0
    except Exception as exc:
        print(f"visualizer state: {exc}", file=sys.stderr)
        return 1
    print("usage: --gui-visible | --set-gui-visible on|off | --toggle-gui-visible",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_visualizer_state_cli())
