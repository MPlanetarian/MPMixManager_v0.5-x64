"""Backend selection for the MP Visualizer.

The Liquid Glass *player* backend and the *visualizer* backend are separate
choices. ``LIQUID_GLASS_BACKEND`` picks the player host (OpenGL by default,
Vulkan when configured). The visualizer follows that same setting when it is
running as a window inside the player or MP Gui, because the drawing context
is the host's. When the visualizer runs standalone it picks for itself:

``VISUALIZER_BACKEND``
    ``auto`` (default), ``opengl``, ``vulkan``, or ``metal``.
    ``auto`` uses Metal on macOS when a Metal device is present, Vulkan when a
    working loader and at least one physical device are present, and OpenGL
    otherwise.

The user's configured Liquid Glass backend is honoured first: an explicit
``opengl`` there selects the OpenGL visualizer, and ``vulkan`` selects the
Vulkan one. An explicit ``VISUALIZER_BACKEND`` always wins. When nothing is
configured, macOS gets Metal (native, no MoltenVK in the way), an NVIDIA GPU
that advertises Vulkan gets Vulkan, and everything else falls back to OpenGL.

Why Metal is preferred over Vulkan on macOS: Vulkan only exists there through
MoltenVK, which is a translation layer over Metal. Using Metal directly is the
same GPU with one less layer, and it needs no Homebrew Vulkan packages.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_ENV = Path.home() / ".config" / "mix-manager" / "config.env"

BACKEND_OPENGL = "opengl"
BACKEND_VULKAN = "vulkan"
BACKEND_METAL = "metal"
BACKEND_AUTO = "auto"

NVIDIA_VENDOR_ID = 0x10DE

_ALIASES = {
    "gl": BACKEND_OPENGL,
    "opengl": BACKEND_OPENGL,
    "ogl": BACKEND_OPENGL,
    "vk": BACKEND_VULKAN,
    "vulkan": BACKEND_VULKAN,
    "moltenvk": BACKEND_VULKAN,
    "mtl": BACKEND_METAL,
    "metal": BACKEND_METAL,
    "auto": BACKEND_AUTO,
    "": BACKEND_AUTO,
}


def _read_config_value(key: str, path: Optional[Path] = None) -> str:
    """Read ``KEY=value`` from config.env without importing the manager."""
    candidate = CONFIG_ENV if path is None else Path(path)
    try:
        text = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    prefix = key + "="
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        return value.strip("\"'")
    return ""


def normalise(value: Optional[str]) -> str:
    """Map a backend name, including ``gl``/``vk`` aliases, to a canonical one."""
    return _ALIASES.get((value or "").strip().lower(), BACKEND_AUTO)


def configured_backend(path: Optional[Path] = None) -> str:
    """The backend the user configured for the *visualizer*.

    ``VISUALIZER_BACKEND`` is the visualizer's own setting and wins outright.

    ``LIQUID_GLASS_BACKEND`` describes the player window's glass renderer,
    which is a different surface with a different set of constraints, so it is
    only treated as a visualizer preference where that choice can actually
    work:

    * on macOS the standalone visualizer defaults to Metal. A player
      configured for OpenGL or Vulkan is about its own window, and honouring
      it here would drag the visualizer onto MoltenVK or onto an OpenGL path
      that needs PyOpenGL, which is not part of the macOS dependencies. So on
      macOS the player's setting does not select the standalone backend.
    * elsewhere the historical behaviour stands: the player's choice is the
      visualizer's default, because it is the same GPU path the user already
      validated for the player.
    """
    explicit = os.environ.get("VISUALIZER_BACKEND", "").strip()
    if explicit:
        return normalise(explicit)
    explicit = _read_config_value("VISUALIZER_BACKEND", path)
    if explicit:
        return normalise(explicit)

    if sys.platform == "darwin":
        # Let auto pick Metal; the player's glass backend is not our decision.
        return BACKEND_AUTO

    value = os.environ.get("LIQUID_GLASS_BACKEND", "").strip()
    if not value:
        value = _read_config_value("LIQUID_GLASS_BACKEND", path)
    if not value:
        return BACKEND_AUTO
    candidate = normalise(value)
    if candidate in (BACKEND_OPENGL, BACKEND_VULKAN):
        return candidate
    return BACKEND_AUTO


def _load_loader():
    """Load the Vulkan loader without importing the heavy visualizer module.

    ``mp_liquid_visualizer_vulkan`` pulls in GLFW, the launch screen and the
    preset library, any of which can be missing on a machine that is only being
    *installed*. The probe must still answer, so it loads libvulkan directly
    and only falls back to the module's own loader helper if that fails.
    """
    import ctypes
    import os
    import sys

    candidates = []
    sdk = os.environ.get("VULKAN_SDK")
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        if sdk:
            candidates.append(os.path.join(sdk, "Bin", "vulkan-1.dll"))
        candidates += ["vulkan-1.dll", os.path.join(root, "System32", "vulkan-1.dll")]
    elif sys.platform == "darwin":
        if sdk:
            candidates += [os.path.join(sdk, "lib", n)
                           for n in ("libMoltenVK.dylib", "libvulkan.1.dylib")]
        candidates += [
            "/opt/homebrew/lib/libvulkan.1.dylib",
            "/opt/homebrew/lib/libvulkan.dylib",
            "/opt/homebrew/lib/libMoltenVK.dylib",
            "libvulkan.1.dylib", "libvulkan.dylib", "libMoltenVK.dylib",
        ]
    else:
        candidates += [
            "libvulkan.so.1", "libvulkan.so",
            "/usr/lib64/libvulkan.so.1",
            "/usr/lib/x86_64-linux-gnu/libvulkan.so.1",
        ]

    for name in candidates:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue

    # Last resort: ask the visualizer module, which knows about vendored copies.
    try:
        from mp_liquid_visualizer_vulkan import load_vulkan_dll
    except ImportError:
        try:
            from scripts.mp_liquid_visualizer_vulkan import load_vulkan_dll
        except ImportError:
            return None
    try:
        return load_vulkan_dll()
    except Exception:
        return None


def vulkan_devices() -> list:
    """Physical devices Vulkan can present with. Empty when Vulkan is unusable."""
    loader = _load_loader()
    if loader is None:
        return []
    try:
        return probe_devices(loader)
    except Exception:
        return []


def nvidia_vulkan_available() -> bool:
    """True when an NVIDIA GPU advertises a working Vulkan device.

    This is the "pick Vulkan on install" signal: the user wants Vulkan to be
    the default on NVIDIA hardware, and the vendor id is the reliable way to
    tell an NVIDIA driver from Mesa's software rasteriser or an Intel iGPU.
    """
    return any(int(d.get("vendor_id", 0)) == NVIDIA_VENDOR_ID for d in vulkan_devices())


def vulkan_available() -> bool:
    """True when a Vulkan loader and at least one physical device are present.

    No window or surface is created. A GPU that advertises Vulkan but cannot
    present is still a Vulkan GPU, and the renderer reports a surface failure
    itself. A loader alone is not enough: Windows ships ``vulkan-1.dll`` with
    no ICD behind it, and Linux can have libvulkan installed with no driver.
    """
    return bool(vulkan_devices())


def probe_devices(loader) -> list:
    """Every physical device Vulkan can see, as ``{vendor_id, device_type, name}``.

    Returns an empty list when there is no loader, no instance, or no driver.

    ``vkEnumeratePhysicalDevices`` is an *instance* command: the loader only
    exposes it through ``vkGetInstanceProcAddr(instance, ...)``. Asking for it
    with a ``NULL`` instance returns nothing, which made the old probe report
    "no GPU" on every Linux machine with a perfectly good NVIDIA driver and
    silently forced the installer onto OpenGL.
    """
    import ctypes
    from ctypes import (POINTER, byref, c_char, c_char_p, c_int32, c_uint32,
                        c_void_p, addressof, cast, string_at)

    VK_SUCCESS = 0

    def _proc(instance, name):
        addr = loader.vkGetInstanceProcAddr
        addr.restype = c_void_p
        addr.argtypes = [c_void_p, c_char_p]
        return addr(instance, name.encode())

    class ApplicationInfo(ctypes.Structure):
        _fields_ = [
            ("sType", c_uint32), ("pNext", c_void_p), ("pApplicationName", c_char_p),
            ("applicationVersion", c_uint32), ("pEngineName", c_char_p),
            ("engineVersion", c_uint32), ("apiVersion", c_uint32),
        ]

    class InstanceInfo(ctypes.Structure):
        _fields_ = [
            ("sType", c_uint32), ("pNext", c_void_p), ("flags", c_uint32),
            ("pApplicationInfo", c_void_p), ("enabledLayerCount", c_uint32),
            ("ppEnabledLayerNames", c_void_p), ("enabledExtensionCount", c_uint32),
            ("ppEnabledExtensionNames", c_void_p),
        ]

    app = ApplicationInfo(0, None, b"MP Visualizer probe", 1, b"probe", 1, (1 << 22))
    info = InstanceInfo(1, None, 0, ctypes.cast(byref(app), c_void_p), 0, None, 0, None)
    create_addr = _proc(None, "vkCreateInstance")
    if not create_addr:
        return []
    create = ctypes.CFUNCTYPE(c_int32, c_void_p, c_void_p, POINTER(c_void_p))(create_addr)
    instance = c_void_p()
    if create(ctypes.cast(byref(info), c_void_p), None, byref(instance)) != VK_SUCCESS or not instance:
        return []
    try:
        enum_addr = _proc(instance, "vkEnumeratePhysicalDevices")
        if not enum_addr:
            return []
        enum = ctypes.CFUNCTYPE(c_int32, c_void_p, POINTER(c_uint32), POINTER(c_void_p))(enum_addr)
        count = c_uint32(0)
        if enum(instance, byref(count), None) != VK_SUCCESS or not count.value:
            return []
        handles = (c_void_p * count.value)()
        if enum(instance, byref(count), handles) != VK_SUCCESS:
            return []

        props_addr = _proc(instance, "vkGetPhysicalDeviceProperties")
        if not props_addr:
            return [{} for _ in range(count.value)]
        # VkPhysicalDeviceProperties begins with apiVersion, driverVersion,
        # vendorID, deviceID, deviceType, then a 256-byte deviceName.
        props = ctypes.CFUNCTYPE(None, c_void_p, c_void_p)(props_addr)

        devices = []
        for handle in handles:
            buf = (c_char * 1024)()
            props(handle, buf)
            words = cast(buf, POINTER(c_uint32 * 5)).contents
            devices.append({
                "api": int(words[0]),
                "driver": int(words[1]),
                "vendor_id": int(words[2]),
                "device_id": int(words[3]),
                "device_type": int(words[4]),
                "name": string_at(addressof(buf) + 20, 256).split(b"\0", 1)[0].decode("utf-8", "replace"),
            })
        return devices
    finally:
        destroy_addr = _proc(instance, "vkDestroyInstance")
        if destroy_addr:
            ctypes.CFUNCTYPE(None, c_void_p, c_void_p)(destroy_addr)(instance, None)


def metal_available() -> bool:
    """True when a Metal device can be created on this host.

    Delegates to the renderer module so the probe and the renderer cannot
    disagree about what "Metal works here" means.
    """
    try:
        from mp_liquid_visualizer_metal import metal_available as probe
    except ImportError:
        try:
            from scripts.mp_liquid_visualizer_metal import metal_available as probe
        except ImportError:
            return False
    try:
        return bool(probe())
    except Exception:
        return False


def resolve(force: Optional[str] = None, *, probe: bool = True,
            config_path: Optional[Path] = None) -> str:
    """Return the backend the standalone visualizer should draw with.

    ``force`` is the ``--backend`` command line value. It may be ``auto``.

    ``auto`` (the default when nothing is configured) picks Metal on macOS with
    a usable device, Vulkan for an NVIDIA GPU that actually advertises a Vulkan
    device, and OpenGL for everything else. Those are the install defaults this
    project wants: macOS users get the native API with no MoltenVK in between,
    NVIDIA users get the Vulkan pipeline, and everyone else gets the broadly
    compatible OpenGL one.
    """
    requested = normalise(force if force else configured_backend(config_path))
    if requested in (BACKEND_OPENGL, BACKEND_VULKAN, BACKEND_METAL):
        return requested
    if not probe:
        return BACKEND_METAL if sys.platform == "darwin" else BACKEND_VULKAN
    if sys.platform == "darwin" and metal_available():
        return BACKEND_METAL
    return BACKEND_VULKAN if nvidia_vulkan_available() else BACKEND_OPENGL


def renderer_class(backend: str):
    """The renderer class for a canonical backend name."""
    canonical = normalise(backend)
    if canonical == BACKEND_METAL:
        try:
            from mp_liquid_visualizer_metal import LiquidVisualizerMetalRenderer as cls
        except ImportError:
            from scripts.mp_liquid_visualizer_metal import LiquidVisualizerMetalRenderer as cls
        return cls
    if canonical == BACKEND_OPENGL:
        try:
            from liquid_player_visualizer import LiquidVisualizerOpenGLRenderer as cls
        except ImportError:
            from scripts.liquid_player_visualizer import LiquidVisualizerOpenGLRenderer as cls
        return cls
    try:
        from mp_liquid_visualizer_vulkan import LiquidVisualizerVulkanRenderer as cls
    except ImportError:
        from scripts.mp_liquid_visualizer_vulkan import LiquidVisualizerVulkanRenderer as cls
    return cls


def recommended_backend() -> str:
    """The backend the installer should write into a fresh config.env.

    Metal on macOS, Vulkan for an NVIDIA GPU that advertises Vulkan, OpenGL
    everywhere else. This is deliberately narrower than ``resolve()``:
    ``resolve()`` honours whatever is configured, while this answers "what
    should a first install of this machine get?"
    """
    if sys.platform == "darwin" and metal_available():
        return BACKEND_METAL
    return BACKEND_VULKAN if nvidia_vulkan_available() else BACKEND_OPENGL


def set_config_backend(path, backend: str) -> bool:
    """Set ``LIQUID_GLASS_BACKEND`` in a config.env, keeping every other line.

    Returns True when the file was changed. The key is replaced in place when
    present (so comments and ordering survive) and appended when absent. A file
    written on Windows keeps its CRLF endings.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    replacement = f'LIQUID_GLASS_BACKEND="{normalise(backend)}"'
    done = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("LIQUID_GLASS_BACKEND="):
            if line.strip() == replacement:
                return False
            lines[index] = replacement
            done = True
            break
    if not done:
        lines.append(replacement)
    updated = newline.join(lines)
    if text.endswith(("\n", "\r\n")):
        updated += newline
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def _main(argv=None) -> int:
    """Tiny CLI so the installer can ask the same question the app asks.

    ``--recommend-backend`` prints ``vulkan`` or ``opengl`` and is what
    install.sh calls. ``--apply-config FILE...`` writes that answer into the
    given config.env files. ``--json`` prints the full probe for support
    reports.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(description="MP Visualizer backend selection")
    parser.add_argument("--recommend-backend", action="store_true",
                        help="print the backend a fresh install should use")
    parser.add_argument("--resolve", action="store_true",
                        help="print the backend the app would run right now")
    parser.add_argument("--devices", action="store_true", help="print the Vulkan devices found")
    parser.add_argument("--apply-config", nargs="+", default=[], metavar="FILE",
                        help="write LIQUID_GLASS_BACKEND into these config.env files")
    parser.add_argument("--backend", default="", choices=("", "auto", "opengl", "vulkan", "gl", "vk"),
                        help="backend to apply with --apply-config (default: recommended)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    chosen = normalise(args.backend) if args.backend else recommended_backend()
    if chosen == BACKEND_AUTO:
        chosen = recommended_backend()

    if args.apply_config:
        changed = [str(path) for path in args.apply_config if set_config_backend(path, chosen)]
        if args.json:
            print(json.dumps({"backend": chosen, "changed": changed}, indent=2))
        else:
            print(chosen)
        return 0

    if args.devices or args.json:
        devices = vulkan_devices()
        payload = {
            "backend": recommended_backend(),
            "resolved": resolve(),
            "vulkan": bool(devices),
            "nvidia_vulkan": nvidia_vulkan_available(),
            "devices": devices,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"recommended: {payload['backend']}")
            print(f"resolved:    {payload['resolved']}")
            for device in devices:
                print(f"  - {device.get('name', '?')} "
                      f"(vendor 0x{int(device.get('vendor_id', 0)):04x})")
        return 0
    if args.resolve:
        print(resolve())
        return 0
    print(recommended_backend())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
