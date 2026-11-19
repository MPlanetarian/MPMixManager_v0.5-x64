#!/usr/bin/env python3
# ==============================================================================
# mp_raspberry_pi.py — Raspberry Pi (and general low-power ARM) platform tuning.
# ------------------------------------------------------------------------------
# The Broadcom VideoCore VI (Pi 4) and VideoCore VII (Pi 5) expose OpenGL 3.1
# and OpenGL ES 3.1 through Mesa's V3D driver, never desktop OpenGL 3.3. The
# application asked for a 3.3 core context and compiled `#version 330` shaders,
# so the glass window could not open at all on a stock Raspberry Pi OS image.
#
# This module is the single place that knows:
#   * whether the machine is a Raspberry Pi, and which generation,
#   * the highest GLSL version a context can actually compile here,
#   * what the renderer, the player, and the visualizer should default to on a
#     low-power GPU (smaller windows, capped raymarch resolution, fewer steps),
#   * how to read the SoC temperature so the visualizer can back off.
#
# Everything is best-effort: on a normal PC every helper returns the value the
# application used before this file existed. Import is stdlib-only and cheap.
# ==============================================================================
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = [
    "is_raspberry_pi",
    "visualizer_supported",
    "visualizer_unsupported_reason",
    "is_arm_linux",
    "pi_model",
    "pi_generation",
    "pi_memory_mb",
    "is_low_power_gpu",
    "should_cap_frame_rate",
    "has_no_render_node",
    "gpu_is_software_rendered",
    "preferred_glsl_version",
    "adapt_glsl",
    "gl_create_attempts",
    "default_env",
    "apply_default_env",
    "soc_temperature_c",
    "thermal_state",
    "visualizer_limits",
    "summary",
]

# Model strings from /proc/device-tree/model and /proc/cpuinfo, lowercased.
_PI_MODEL_MARKERS = ("raspberry pi", "bcm2835", "bcm2711", "bcm2712", "compute module")


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_device_tree_model() -> str:
    raw = _read_text("/proc/device-tree/model").replace("\x00", "").strip()
    if raw:
        return raw
    for line in _read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith(("model", "hardware")):
            _k, _, value = line.partition(":")
            if value.strip():
                return value.strip()
    return ""


_MODEL_CACHE: Optional[str] = None


def pi_model() -> str:
    """The board name, e.g. 'Raspberry Pi 5 Model B Rev 1.0', or '' off a Pi."""
    global _MODEL_CACHE
    if _MODEL_CACHE is None:
        model = _read_device_tree_model()
        if model and not any(m in model.lower() for m in _PI_MODEL_MARKERS):
            model = ""
        _MODEL_CACHE = model
    return _MODEL_CACHE


def is_arm_linux() -> bool:
    """True on 64-bit or 32-bit ARM Linux, whether or not it is a Pi."""
    if sys.platform != "linux":
        return False
    machine = platform.machine().lower()
    return machine in ("aarch64", "arm64", "armv7l", "armv6l", "armv8l")


def is_raspberry_pi() -> bool:
    """True when the kernel reports Broadcom Pi hardware.

    A Pi 5 running a plain Debian or Ubuntu image still reports the board in
    the device tree, so this stays true. Set MP_FORCE_RASPBERRY_PI=1 to test
    the tuning on another machine and MP_FORCE_RASPBERRY_PI=0 to turn it off.
    """
    override = os.environ.get("MP_FORCE_RASPBERRY_PI", "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return True
    if override in ("0", "false", "no", "off"):
        return False
    return bool(pi_model())


def visualizer_supported() -> bool:
    """False on Raspberry Pi, where the MP Visualizer is not offered at all.

    A Pi's VideoCore GPU cannot raymarch the twelve-scene SDF at a usable rate
    no matter how the quality is tuned, and the window competes with audio
    decoding for the same SoC. Rather than ship a control that opens a window
    the board cannot drive, the visualizer is refused on this platform in every
    launcher (console, MP Gui, the Liquid Player, the mixer, the Web Companion
    and the tray).

    Set ``MP_FORCE_VISUALIZER_ON_PI=1`` to override it for a deliberate test of
    the renderer on that hardware; it is not documented as a user setting.
    """
    if (os.environ.get("MP_FORCE_VISUALIZER_ON_PI") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return not is_raspberry_pi()


def visualizer_unsupported_reason() -> str:
    """One sentence a launcher can show instead of opening the window."""
    return ("The MP Visualizer is not available on Raspberry Pi: the VideoCore GPU "
            "cannot run the 3D raymarch at a usable frame rate. Every other MP app "
            "works normally.")


def pi_generation() -> int:
    """5, 4, 3, 2, 1, 0 (Pi Zero), or -1 when it is not a Pi.

    Used to decide how hard the raymarch may work. A Pi 5 is roughly an
    order of magnitude faster than a Pi 4 on fragment-heavy work, and a
    Pi 3 or older cannot run the full visualizer at playable speed.
    """
    model = pi_model().lower()
    if not model:
        return -1
    if "raspberry pi 5" in model or "compute module 5" in model or "bcm2712" in model:
        return 5
    if "raspberry pi 4" in model or "compute module 4" in model or "bcm2711" in model:
        return 4
    if "raspberry pi 3" in model or "compute module 3" in model or "bcm2837" in model:
        return 3
    if "raspberry pi 2" in model or "bcm2836" in model:
        return 2
    if "raspberry pi zero 2" in model:
        return 3
    if "raspberry pi zero" in model or "raspberry pi 1" in model or "bcm2835" in model:
        return 0
    return -1


def pi_memory_mb() -> int:
    """Installed RAM in MiB, or 0 when it cannot be read."""
    for line in _read_text("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            m = re.search(r"(\d+)", line)
            if m:
                return int(m.group(1)) // 1024
    return 0


def is_low_power_gpu() -> bool:
    """True for the GPUs the *render-resolution* tuning exists for.

    A Pi (any generation) and any ARM Linux box without a discrete GPU get the
    conservative raymarch sizes. Deliberately narrower than
    `should_cap_frame_rate()`: downscaling the visualizer is a Pi-shaped
    answer to a Pi-shaped problem, and a desktop with a slow-but-working
    driver should still draw at its own resolution.

    An explicit MP_LOW_POWER_GPU overrides the guess in either direction.
    """
    override = os.environ.get("MP_LOW_POWER_GPU", "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return True
    if override in ("0", "false", "no", "off"):
        return False
    return is_raspberry_pi()


def should_cap_frame_rate() -> bool:
    """True when the render loop should be paced below the display refresh.

    Two different machines need this for the same reason — the raymarch
    cannot hold 60 Hz — but they need nothing else in common:

      * a Raspberry Pi, whose VideoCore GPU is an order of magnitude slower
        than a discrete card;
      * a desktop with **no hardware render node**, where Mesa falls back to
        ``llvmpipe`` and every pixel is computed on the CPU. A 2010 MacBook
        Pro (GeForce 320M) on Bazzite is exactly this case: only ``card0``
        exists, so the visualizer and the liquid-glass player run on two
        CPU cores.

    The second group is what `has_no_render_node()` detects. Capping the frame
    rate is the whole fix there — the resolution stays the user's choice.
    """
    override = os.environ.get("MP_LOW_POWER_GPU", "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return True
    if override in ("0", "false", "no", "off"):
        return False
    return is_low_power_gpu() or has_no_render_node()


def has_no_render_node() -> bool:
    """True on Linux when DRI exposes no render node (software rasterisation).

    A healthy GPU driver creates ``/dev/dri/renderD128`` (or higher) beside
    ``card0``. Its absence means Mesa cannot bind a hardware driver and will
    draw with ``llvmpipe`` on the CPU. Only Linux is probed; macOS and Windows
    do not have this layout and return False so they are never second-guessed.
    """
    if sys.platform != "linux":
        return False
    dri = Path("/dev/dri")
    try:
        if not dri.is_dir():
            return False
        # card0 without renderD* is the software-rendering shape.
        if any(dri.glob("renderD*")):
            return False
        return (dri / "card0").exists()
    except OSError:
        return False


def gpu_is_software_rendered() -> bool:
    """True when OpenGL is being serviced by llvmpipe/softpipe/swrast.

    ``renderer`` from python3-pyOpenGL is only consulted when it is already
    importable and a context exists; failure to import is not an error, it
    just means this machine cannot be classified this way.
    """
    try:
        from OpenGL import GL  # type: ignore
        name = GL.glGetString(GL.GL_RENDERER)
        text = (name or b"").decode("utf-8", "replace") if isinstance(name, bytes) else str(name or "")
    except Exception:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in ("llvmpipe", "softpipe", "swrast", "software rasterizer"))


# ------------------------------------------------------------------------------
# GLSL versions
# ------------------------------------------------------------------------------

# The order a GLFW window creation is attempted in. Each entry is
# (major, minor, profile) where profile is "core", "compat", or "" (no hint).
# On a Pi 4/5 the first entry fails and the second succeeds; on a PC the first
# succeeds so nothing changes.
_GL_ATTEMPTS_DESKTOP: Tuple[Tuple[int, int, str], ...] = (
    (3, 3, "core"),
    (3, 1, "core"),
    (3, 1, "compat"),
    (3, 0, "compat"),
    (0, 0, ""),
)
_GL_ATTEMPTS_LOW_POWER: Tuple[Tuple[int, int, str], ...] = (
    # V3D publishes OpenGL 3.1. Ask for it as core first, then as a
    # compatibility profile (some Mesa builds only expose the latter), and
    # only then fall back to a 3.0 context.
    (3, 1, "core"),
    (3, 1, "compat"),
    (3, 0, "compat"),
    (3, 3, "core"),
    (0, 0, ""),
)


def gl_create_attempts() -> Tuple[Tuple[int, int, str], ...]:
    """Context versions to try, in order, for this machine."""
    return _GL_ATTEMPTS_LOW_POWER if is_low_power_gpu() else _GL_ATTEMPTS_DESKTOP


def preferred_glsl_version(gl_major: int, gl_minor: int) -> str:
    """Highest GLSL version tag a context can compile, e.g. '330', '140'.

    GLSL versions map to GL versions as GLSL 1.40 = OpenGL 3.1, 1.50 = 3.2,
    3.30 = 3.3, 4.10 = 4.1. The shaders in this project only need 1.40 or
    above, because that is where `in`/`out` and `texture()` became core.
    """
    if (gl_major, gl_minor) >= (4, 1):
        return "410"
    if (gl_major, gl_minor) >= (3, 3):
        return "330"
    if (gl_major, gl_minor) >= (3, 2):
        return "150"
    if (gl_major, gl_minor) >= (3, 1):
        return "140"
    if (gl_major, gl_minor) >= (3, 0):
        return "130"
    return "120"


_LAYOUT_LOCATION = re.compile(r"^[ \t]*layout[ \t]*\([ \t]*location[ \t]*=[ \t]*(\d+)[ \t]*\)[ \t]*(.*)$", re.M)
_VERSION_LINE = re.compile(r"^[ \t]*#version[ \t]+\d+[^\n]*$", re.M)


def adapt_glsl(source: str, version: str) -> str:
    """Rewrite GLSL 3.30 source so it compiles on an older GLSL version.

    Only two constructs in this project need rewriting when the version drops
    below 3.30:

      * `layout(location = N)` on vertex inputs, which arrived with
        GL_ARB_explicit_attrib_location in GL 3.3. The attribute index is
        returned in `attribute_locations`, and the caller binds it before
        linking with glBindAttribLocation.
      * `#version 330 core`, replaced with the requested version. `core` was
        only valid from 1.50 on, so it is dropped for 1.40.

    Everything else the shaders use (`in`/`out`, `texture()`, structs,
    constant-bound loops, integer `%`) is valid from GLSL 1.30 and needs no
    change. The function is a no-op for version 330 and above.
    """
    if version in ("410", "330"):
        return source

    def _drop_layout(match: re.Match) -> str:
        # Keep the declaration, drop the layout qualifier. The caller binds
        # the location explicitly, which is what a 3.3 core profile would do
        # implicitly.
        return match.group(2)

    body = _LAYOUT_LOCATION.sub(_drop_layout, source)
    if _VERSION_LINE.search(body):
        body = _VERSION_LINE.sub(f"#version {version}", body, count=1)
    else:
        body = f"#version {version}\n" + body
    return body


def glsl_attribute_locations(source: str) -> List[Tuple[str, int]]:
    """Return [(attribute_name, location)] declared with layout(location=...).

    The renderer calls this before compiling the downlevelled source so it can
    reproduce the explicit binding on a GL 3.1 driver.
    """
    found: List[Tuple[str, int]] = []
    for line in source.splitlines():
        match = _LAYOUT_LOCATION.match(line)
        if not match:
            continue
        location, rest = match.group(1), match.group(2)
        name_match = re.search(r"\b(?:in|attribute)\s+\w+\s+(\w+)\s*;", rest)
        if name_match:
            found.append((name_match.group(1), int(location)))
    return found


# ------------------------------------------------------------------------------
# Performance defaults
# ------------------------------------------------------------------------------

def default_env() -> Dict[str, str]:
    """Environment defaults this machine should run with.

    Only keys the user has not already set are returned, so an explicit
    `MP_VISUALIZER_GL_LONG_EDGE=800 ./manager.sh` still wins. On a PC the
    dict is empty.
    """
    if not is_low_power_gpu():
        return {}

    gen = pi_generation()
    memory = pi_memory_mb()
    env: Dict[str, str] = {}

    # The raymarch is the single most expensive thing the suite draws. At the
    # native framebuffer size a Pi 4 renders it at single-digit frames per
    # second, so cap the long edge and let the shader upscale. A Pi 5 copes
    # with more, and 8 GB boards are not memory constrained.
    if gen >= 5 and (memory == 0 or memory >= 8000):
        long_edge, steps = "1280", "64"
    elif gen >= 5:
        long_edge, steps = "960", "56"
    elif gen == 4:
        long_edge, steps = "640", "44"
    else:
        long_edge, steps = "480", "32"

    env.setdefault("MP_VISUALIZER_GL_LONG_EDGE", long_edge)
    env.setdefault("MP_VISUALIZER_GL_MARCH_STEPS", steps)

    # Cap the MP Visualizer at 30 FPS. A Pi GPU cannot sustain 60 Hz on this
    # raymarch, and an uncapped loop just produces uneven frames and heat.
    env.setdefault("MP_VISUALIZER_FPS_CAP", "30")

    # Software GL (llvmpipe) appears when a Pi has no working V3D driver. It is
    # an order of magnitude slower again, so the visualizer defaults to its
    # cheap preset rather than a 12-scene blend.
    env.setdefault("MP_VISUALIZER_SAFE_PRESET", "1")

    # The Bluetooth router forks pactl five times a second. A Pi without a
    # Bluetooth adapter pays that for nothing, and the audio engine starts it
    # unconditionally.
    if not _has_bluetooth_adapter():
        env.setdefault("MP_DISABLE_BLUETOOTH_MONITOR", "1")

    return env


def apply_default_env() -> Dict[str, str]:
    """Apply `default_env()` to os.environ without overwriting real values."""
    applied: Dict[str, str] = {}
    for key, value in default_env().items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def _has_bluetooth_adapter() -> bool:
    """True when the kernel exposes at least one Bluetooth HCI device."""
    try:
        return any(Path("/sys/class/bluetooth").glob("hci*"))
    except OSError:
        return False


def visualizer_limits() -> Dict[str, int]:
    """Resolved visualizer limits for this machine.

    Mirrors the environment keys in `default_env()` so a caller that has
    already imported the module does not have to re-parse os.environ.
    """
    env = default_env()
    env.update({k: v for k, v in os.environ.items() if k.startswith("MP_VISUALIZER_")})

    def _int(key: str, fallback: int) -> int:
        try:
            return int(str(env.get(key, fallback)).strip())
        except (TypeError, ValueError):
            return fallback

    return {
        "long_edge": _int("MP_VISUALIZER_GL_LONG_EDGE", 0),
        "march_steps": _int("MP_VISUALIZER_GL_MARCH_STEPS", 0),
        "safe_preset": _int("MP_VISUALIZER_SAFE_PRESET", 0),
        "fps_cap": _int("MP_VISUALIZER_FPS_CAP", 0),
    }


def visualizer_fps_cap() -> int:
    """Frame rate the MP Visualizer should not exceed, or 0 for no cap.

    A desktop renders at the display's refresh rate. A Raspberry Pi renders
    the raymarch on a GPU an order of magnitude slower than a discrete card,
    so chasing 60 Hz produces uneven frames and a hot SoC. 30 FPS is smooth
    enough for the visualizer and leaves headroom for audio decoding.

    ``MP_VISUALIZER_FPS_CAP`` overrides it (``0`` disables the cap). The value
    is clamped to 10..240 so a typo cannot freeze the window, while still
    allowing the 30 / 60 / 120 / 144 choices the Settings menu offers and a
    240 Hz panel.
    """
    explicit = (os.environ.get("MP_VISUALIZER_FPS_CAP") or "").strip()
    if explicit:
        try:
            value = int(explicit)
        except ValueError:
            value = -1
        if value <= 0:
            return 0
        return max(10, min(240, value))
    return 30 if should_cap_frame_rate() else 0


def swap_interval_for_cap(monitor_hz: int, fps_cap: int) -> int:
    """GLFW swap interval that paces ``monitor_hz`` to at most ``fps_cap``.

    With FIFO vsync the swap blocks until the next refresh, so an interval of
    2 halves the frame rate on a 60 Hz panel. Dividing is exact only when the
    refresh is a multiple of the cap; when it is not, the loop's own deadline
    handles the remainder and this returns 1 so the driver does not add extra
    blocking on top.
    """
    if fps_cap <= 0 or monitor_hz <= 0:
        return 1
    if monitor_hz <= fps_cap:
        return 1
    divisor = int(round(monitor_hz / float(fps_cap)))
    if divisor < 1:
        divisor = 1
    # Only trust an exact divisor; otherwise the caller paces by deadline.
    if abs(monitor_hz - divisor * fps_cap) > max(1, fps_cap // 10):
        return 1
    return min(divisor, 4)


# ------------------------------------------------------------------------------
# Thermals
# ------------------------------------------------------------------------------

def soc_temperature_c() -> Optional[float]:
    """SoC temperature in °C, or None when the sensor is unavailable."""
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        kind = _read_text(str(zone / "type")).strip().lower()
        if kind and "soc" not in kind and "cpu" not in kind and "bcm" not in kind:
            continue
        raw = _read_text(str(zone / "temp")).strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # Millidegrees on Linux thermal zones.
        return value / 1000.0 if value > 1000 else float(value)
    if is_raspberry_pi() and _have("vcgencmd"):
        try:
            out = subprocess.run(
                ["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2
            ).stdout
            match = re.search(r"temp=([0-9.]+)", out)
            if match:
                return float(match.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def thermal_state() -> str:
    """'ok', 'warm' (>= 70 °C), 'hot' (>= 80 °C) or 'unknown'.

    The visualizer uses this to drop its resolution and step count before the
    Pi starts throttling, which otherwise looks like a sudden freeze.
    """
    temp = soc_temperature_c()
    if temp is None:
        return "unknown"
    if temp >= 80.0:
        return "hot"
    if temp >= 70.0:
        return "warm"
    return "ok"


def _have(binary: str) -> bool:
    from shutil import which

    return which(binary) is not None


def summary() -> str:
    """One line for the launch logs and the Dev Utility manifest."""
    if not is_raspberry_pi():
        return "Not a Raspberry Pi"
    temp = soc_temperature_c()
    return (
        f"{pi_model() or 'Raspberry Pi'}"
        f" | gen {pi_generation()}"
        f" | {pi_memory_mb() or '?'} MB"
        f" | GLSL max {preferred_glsl_version(3, 1) if is_low_power_gpu() else '330'}"
        f" | SoC {f'{temp:.1f} C' if temp is not None else 'n/a'}"
    )


if __name__ == "__main__":
    import json

    print(json.dumps({
        "raspberry_pi": is_raspberry_pi(),
        "arm_linux": is_arm_linux(),
        "model": pi_model(),
        "generation": pi_generation(),
        "memory_mb": pi_memory_mb(),
        "low_power_gpu": is_low_power_gpu(),
        "gl_attempts": [list(a) for a in gl_create_attempts()],
        "default_env": default_env(),
        "visualizer_limits": visualizer_limits(),
        "thermal": thermal_state(),
        "summary": summary(),
    }, indent=2))
