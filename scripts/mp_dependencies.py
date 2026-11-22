#!/usr/bin/env python3
# ==============================================================================
# mp_dependencies.py — one place that knows every MP Mix Manager dependency.
# ------------------------------------------------------------------------------
# The installer, the reinstall helper, and the test harness all ask this file
# what is missing and how to install it, so the answers cannot drift apart.
#
#   mp_dependencies.py --probe                 human-readable table
#   mp_dependencies.py --probe --json          machine-readable report
#   mp_dependencies.py --install [--yes]       install whatever is missing
#   mp_dependencies.py --verify                exit 1 when a hard dependency is absent
#   mp_dependencies.py --requirements FILE     extra Python requirements file
#
# Options that change what is attempted:
#   --python PATH      interpreter to probe/install into (default: best found)
#   --no-system        never call a system package manager
#   --no-pip           never call pip
#   --optional         also install optional (non-critical) dependencies
#   --dry-run          print the commands that would run, change nothing
#   --json             machine-readable output for --probe/--verify
#
# Environment:
#   MP_SUDO_PASSWORD   password for sudo when a terminal is not available
#   MP_PKG_ASSUME_YES  treat as --yes
#   MP_SKIP_SYSTEM_PKGS=1, MP_SKIP_PIP=1  disable one installer path
#
# Exit status: 0 all hard dependencies satisfied, 1 something is still missing,
#              2 for usage errors. --install exits 1 when a hard dependency could
#              not be installed, so the installer can report the truth.
# ==============================================================================
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------------------
# What the application actually imports.
#
# `required` means the primary windows (MP Mix Manager, MP Gui, MP Liquid
# Player, MP Liquid Visualizer) are broken without it. Everything else powers a
# secondary feature and is reported but never fails the install.
#
# `pkgs` maps a package manager to the package that provides it:
#   dnf / apt / pacman / zypper / brew / pkg / winget / rpm-ostree
# `pip` is the name to pass to pip when no system package is used.
# ------------------------------------------------------------------------------
SPECS: List[Dict[str, Any]] = [
    # ---- command-line tools ---------------------------------------------------
    {
        "id": "ffmpeg",
        "kind": "binary",
        "required": True,
        "purpose": "Audio/video conversion, encoding and integrity checks",
        "pkgs": {
            "dnf": ["ffmpeg-free", "ffmpeg"],
            "apt": ["ffmpeg"],
            "pacman": ["ffmpeg"],
            "zypper": ["ffmpeg"],
            "brew": ["ffmpeg"],
            "pkg": ["ffmpeg"],
            "winget": ["Gyan.FFmpeg"],
            "rpm-ostree": ["ffmpeg-free"],
        },
    },
    {
        "id": "ffprobe",
        "kind": "binary",
        "required": True,
        "purpose": "Stream duration and codec inspection",
        # Shipped inside the ffmpeg package on every platform.
        "same_package_as": "ffmpeg",
        "pkgs": {},
    },
    {
        "id": "flac",
        "kind": "binary",
        # Not required: encoding and integrity checks both go through ffmpeg
        # (see Make_SOF_FLAC_CONVERSION.sh and Verify_FLAC_Files.sh). The flac
        # CLI only adds metaflac-based tagging, and Windows does not ship it.
        "required": False,
        "purpose": "metaflac tagging and native FLAC stream checks",
        "pkgs": {
            "rpm-ostree": ["flac"],

            "dnf": ["flac"],
            "apt": ["flac"],
            "pacman": ["flac"],
            "zypper": ["flac"],
            "brew": ["flac"],
            "pkg": ["flac"],
            "winget": ["Xiph.Flac"],
        },
    },
    {
        "id": "sox",
        "kind": "binary",
        "required": False,
        "purpose": "Spectrograms and high-resolution signal processing",
        "pkgs": {
            "rpm-ostree": ["sox"],

            "dnf": ["sox"],
            "apt": ["sox"],
            "pacman": ["sox"],
            "zypper": ["sox"],
            "brew": ["sox"],
            "pkg": ["sox"],
        },
    },
    {
        "id": "rclone",
        "kind": "binary",
        "required": False,
        "purpose": "Google Drive / cloud backup synchronisation",
        "pkgs": {
            "rpm-ostree": ["rclone"],

            "dnf": ["rclone"],
            "apt": ["rclone"],
            "pacman": ["rclone"],
            "zypper": ["rclone"],
            "brew": ["rclone"],
            "pkg": ["rclone"],
            "winget": ["Rclone.Rclone"],
        },
    },
    {
        "id": "jq",
        "kind": "binary",
        "required": False,
        "purpose": "JSON parsing for cliamp status and inspection",
        "pkgs": {
            "rpm-ostree": ["jq"],

            "dnf": ["jq"],
            "apt": ["jq"],
            "pacman": ["jq"],
            "zypper": ["jq"],
            "brew": ["jq"],
            "pkg": ["jq"],
            "winget": ["jqlang.jq"],
        },
    },
    {
        "id": "wmctrl",
        "kind": "binary",
        "required": False,
        "purpose": "Close other desktop windows safely (menu 31)",
        "pkgs": {
            "rpm-ostree": ["wmctrl"],

            "dnf": ["wmctrl"],
            "apt": ["wmctrl"],
            "pacman": ["wmctrl"],
            "brew": ["wmctrl"],
        },
    },

    # ---- Python runtime modules ----------------------------------------------
    {
        "id": "numpy",
        "kind": "module",
        "module": "numpy",
        "required": True,
        "purpose": "Player DSP, visualizer maths, spectrogram analysis",
        "pkgs": {
            "dnf": ["python3-numpy"],
            "apt": ["python3-numpy"],
            "pacman": ["python-numpy"],
            "zypper": ["python3-numpy"],
            "brew": ["numpy"],
            "pkg": ["py311-numpy", "py39-numpy"],
            "rpm-ostree": ["python3-numpy"],
        },
        "pip": "numpy",
    },
    {
        "id": "PyGObject",
        "kind": "module",
        "module": "gi",
        "required": True,
        "platforms": ["linux", "macos", "freebsd"],
        "purpose": "Gtk 3 window used by MP Gui and the Liquid Glass players",
        "pkgs": {
            "dnf": ["python3-gobject", "gtk3"],
            "apt": ["python3-gi", "gir1.2-gtk-3.0"],
            "pacman": ["python-gobject", "gtk3"],
            "zypper": ["python3-gobject", "gtk3"],
            "brew": ["pygobject3", "gtk+3"],
            "pkg": ["py311-gobject3", "py39-gobject3", "gtk3"],
            "rpm-ostree": ["python3-gobject", "gtk3"],
        },
        # Deliberately no pip entry: PyGObject needs system headers and a
        # matching girepository, so a pip build fails on most machines.
        "pip": "",
        "note": "system package only",
    },
    {
        "id": "PyOpenGL",
        "kind": "module",
        "module": "OpenGL",
        "required": True,
        "purpose": "OpenGL renderer for MP Liquid Player and the visualizer",
        "pkgs": {
            "brew": ["python-opengl"],

            "dnf": ["python3-pyopengl"],
            "apt": ["python3-opengl"],
            "pacman": ["python-opengl"],
            "zypper": ["python3-opengl"],
            "pkg": ["py311-opengl", "py39-opengl"],
            "rpm-ostree": ["python3-pyopengl"],
        },
        "pip": "PyOpenGL",
    },
    {
        # GLFW creates the X11 window and the OpenGL context for MP Gui, the
        # Liquid Player, and the MP Visualizer. It is loaded through ctypes, so
        # Python cannot import-test it; the binary probe covers it instead.
        "id": "GLFW",
        "kind": "binary",
        "binary": "libglfw.so.3",
        "required": True,
        "purpose": "OpenGL window and context for MP Gui, MP Liquid Player and the Visualizer",
        "pkgs": {
            "dnf": ["glfw"],
            "apt": ["libglfw3"],
            "pacman": ["glfw"],
            "zypper": ["glfw-devel"],
            "brew": ["glfw"],
            "pkg": ["glfw"],
            "rpm-ostree": ["glfw"],
        },
        "pip": "glfw",
        "note": "system package preferred; pip wheel is a fallback on headless images",
    },
    {
        "id": "sounddevice",
        "kind": "module",
        "module": "sounddevice",
        "required": True,
        "purpose": "Audio output in MP Audio Player",
        "pkgs": {
            "dnf": ["python3-sounddevice", "portaudio"],
            "apt": ["python3-sounddevice", "libportaudio2"],
            "pacman": ["python-sounddevice", "portaudio"],
            "zypper": ["python3-sounddevice", "portaudio"],
            "brew": ["portaudio"],
            "pkg": ["py311-sounddevice", "portaudio"],
            "rpm-ostree": ["python3-sounddevice", "portaudio"],
        },
        "pip": "sounddevice",
    },
    {
        "id": "pillow",
        "kind": "module",
        "module": "PIL",
        "required": True,
        "purpose": "Shortcut icons, cover art, on-screen text rendering",
        "pkgs": {
            "brew": ["pillow"],

            "dnf": ["python3-pillow"],
            "apt": ["python3-pil"],
            "pacman": ["python-pillow"],
            "zypper": ["python3-Pillow"],
            "pkg": ["py311-pillow", "py39-pillow"],
            "rpm-ostree": ["python3-pillow"],
        },
        "pip": "pillow",
    },
    {
        "id": "soundfile",
        "kind": "module",
        "module": "soundfile",
        "required": False,
        "purpose": "Exact mix durations in the Web Companion and player",
        "pkgs": {
            "zypper": ["python3-soundfile", "libsndfile1"],

            "dnf": ["python3-soundfile", "libsndfile"],
            "apt": ["python3-soundfile", "libsndfile1"],
            "pacman": ["python-soundfile", "libsndfile"],
            "brew": ["libsndfile"],
            "pkg": ["py311-soundfile", "libsndfile"],
            "rpm-ostree": ["python3-soundfile", "libsndfile"],
        },
        "pip": "soundfile",
    },
    {
        "id": "pyaudiowpatch",
        "kind": "module",
        "module": "pyaudiowpatch",
        "required": False,
        "purpose": "WASAPI loopback recording in the Windows MP Liquid Mixer",
        "pkgs": {},
        "pip": "PyAudioWPatch",
        "note": "Windows only; the mixer records with sounddevice or disables REC without it",
    },
    {
        "id": "psutil",
        "kind": "module",
        "module": "psutil",
        "required": False,
        "purpose": "Process and transfer monitors (menu 26)",
        "pkgs": {
            "zypper": ["python3-psutil"],

            "dnf": ["python3-psutil"],
            "apt": ["python3-psutil"],
            "pacman": ["python-psutil"],
            "brew": [],
            "pkg": ["py311-psutil", "py39-psutil"],
            "rpm-ostree": ["python3-psutil"],
        },
        "pip": "psutil",
    },
    {
        "id": "rich",
        "kind": "module",
        "module": "rich",
        "required": False,
        "purpose": "Colourised output in the monitor scripts",
        "pkgs": {
            "zypper": ["python3-rich"],

            "dnf": ["python3-rich"],
            "apt": ["python3-rich"],
            "pacman": ["python-rich"],
            "pkg": ["py311-rich", "py39-rich"],
            "rpm-ostree": ["python3-rich"],
        },
        "pip": "rich",
    },
    {
        "id": "reportlab",
        "kind": "module",
        "module": "reportlab",
        "required": False,
        "purpose": "PDF reports and the printed manual",
        "pkgs": {
            "zypper": ["python3-reportlab"],

            "dnf": ["python3-reportlab"],
            "apt": ["python3-reportlab"],
            "pacman": ["python-reportlab"],
            "pkg": ["py311-reportlab", "py39-reportlab"],
            "rpm-ostree": ["python3-reportlab"],
        },
        "pip": "reportlab",
    },
    {
        "id": "matplotlib",
        "kind": "module",
        "module": "matplotlib",
        "required": False,
        "purpose": "Charts inside the generated reports",
        "pkgs": {
            "zypper": ["python3-matplotlib"],

            "dnf": ["python3-matplotlib"],
            "apt": ["python3-matplotlib"],
            "pacman": ["python-matplotlib"],
            "pkg": ["py311-matplotlib", "py39-matplotlib"],
            "rpm-ostree": ["python3-matplotlib"],
        },
        "pip": "matplotlib",
    },
    {
        "id": "PySide6",
        "kind": "module",
        "module": "PySide6",
        "required": False,
        "purpose": "Shared system tray icon",
        "pkgs": {
            "dnf": ["python3-pyside6"],
            "apt": ["python3-pyside6.qtwidgets"],
            "pacman": ["pyside6"],
            "pkg": ["py311-qt6-pyqt"],
            "rpm-ostree": ["python3-pyside6"],
        },
        "pip": "PySide6",
    },
    {
        "id": "mutagen",
        "kind": "module",
        "module": "mutagen",
        "required": False,
        "purpose": "Metadata tagging when metaflac is absent",
        "pkgs": {
            "zypper": ["python3-mutagen"],

            "dnf": ["python3-mutagen"],
            "apt": ["python3-mutagen"],
            "pacman": ["python-mutagen"],
            "brew": ["mutagen"],
            "pkg": ["py311-mutagen", "py39-mutagen"],
            "rpm-ostree": ["python3-mutagen"],
        },
        "pip": "mutagen",
    },
    {
        "id": "pypdf",
        "kind": "module",
        "module": "pypdf",
        "required": False,
        "purpose": "Merges report PDFs attached to scheduled email reports",
        "pkgs": {
            "dnf": ["python3-pypdf"],
            "apt": ["python3-pypdf"],
            "pacman": ["python-pypdf"],
            "zypper": ["python3-pypdf"],
            "pkg": ["py311-pypdf", "py39-pypdf"],
            "rpm-ostree": ["python3-pypdf"],
        },
        "pip": "pypdf",
    },
    {
        "id": "ultralytics",
        "kind": "module",
        "module": "ultralytics",
        "required": False,
        "optional_heavy": True,
        "purpose": "YOLOv8 people detection helper script (not used by the app)",
        "pkgs": {},
        "pip": "ultralytics",
        "note": "installs PyTorch (~2 GB); enable with --optional",
    },

    # ---- GTK typelibs (not importable modules) -------------------------------
    {
        "id": "VTE 2.91",
        "kind": "typelib",
        "namespace": "Vte",
        "version": "2.91",
        "required": False,
        "platforms": ["linux", "freebsd"],
        "purpose": "Live terminal embedded in the glass window",
        "pkgs": {
            "dnf": ["vte291-gtk4"],
            "apt": ["gir1.2-vte-2.91"],
            "pacman": ["vte3"],
            "zypper": ["vte-lang", "libvte-2_91-0"],
            "pkg": ["vte3"],
            "rpm-ostree": ["vte291-gtk4"],
        },
        "pip": "",
    },
]

# The Python packages the application declares for itself. Kept beside SPECS so
# a plain `pip install -r requirements.txt` and this engine agree.
REQUIREMENTS_FILE = ROOT / "requirements.txt"


# ------------------------------------------------------------------------------
# platform detection
# ------------------------------------------------------------------------------
def host_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system in {"windows", "cygwin", "msys"}:
        return "windows"
    if system == "freebsd":
        return "freebsd"
    release = platform.release().lower()
    if os.environ.get("WSL_DISTRO_NAME") or "microsoft" in release:
        return "wsl"
    return "linux"


def os_release() -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_atomic_host() -> bool:
    """True on rpm-ostree / Bazzite style immutable images."""
    if shutil.which("rpm-ostree") is None:
        return False
    release = os_release()
    ident = (release.get("ID", "") + " " + release.get("VARIANT_ID", "")).lower()
    if "bazzite" in ident or "silverblue" in ident or "kinoite" in ident:
        return True
    ostree = "/run/ostree-booted"
    return os.path.exists(ostree)


def package_manager() -> str:
    """The manager this host should use, or "" when there is none."""
    kind = host_os()
    if kind == "macos":
        return "brew" if which_binary("brew") else ""
    if kind in {"windows", "wsl"}:
        if kind == "windows":
            return "winget" if which_binary("winget") else ""
        for name, manager in (("apt-get", "apt"), ("dnf", "dnf"), ("pacman", "pacman")):
            if which_binary(name):
                return manager
        return ""
    if kind == "freebsd":
        return "pkg" if which_binary("pkg") else ""
    # Linux
    if is_atomic_host():
        # A layered package needs a reboot, so prefer Homebrew for binaries and
        # a virtualenv for modules: both take effect immediately.
        return "brew" if which_binary("brew") else "rpm-ostree"
    for name, manager in (
        ("dnf", "dnf"),
        ("dnf5", "dnf"),
        ("apt-get", "apt"),
        ("pacman", "pacman"),
        ("zypper", "zypper"),
    ):
        if which_binary(name):
            return manager
    return ""


def module_manager() -> str:
    """Manager that owns the *interpreter's* modules, or "" to use pip.

    An immutable image ships gi/numpy for the system python. Homebrew's python
    is a different interpreter, so a brew install would not be visible to the
    launcher that runs /usr/bin/python3. Modules therefore go through pip into a
    virtualenv on those hosts instead of the package manager.
    """
    if host_os() in {"windows", "wsl"}:
        return ""
    if is_atomic_host():
        return ""
    return package_manager()


# ------------------------------------------------------------------------------
# interpreter selection
# ------------------------------------------------------------------------------
def extra_bin_dirs() -> List[str]:
    """Directories that hold tools but are absent from a minimal PATH.

    An SSH session, a systemd unit, or a desktop launcher started from a file
    manager does not read ~/.zshrc, so /opt/homebrew/bin is not on PATH even
    though ffmpeg is installed there. Probing only PATH reported working tools
    as missing and made the installer try to install them again.
    """
    kind = host_os()
    if kind == "windows":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            r"C:\ffmpeg\bin",
            os.path.join(program_files, "ffmpeg", "bin"),
            r"C:\ProgramData\chocolatey\bin",
            os.path.join(local, "Microsoft", "WindowsApps"),
            os.path.join(local, "Programs", "Python", "Python313", "Scripts"),
            r"C:\Program Files\Git\usr\bin",
        ]
    elif kind == "macos":
        candidates = [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
            "/opt/local/bin",
            "/Library/Apple/usr/bin",
        ]
    else:
        candidates = [
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/usr/local/sbin",
            "/home/linuxbrew/.linuxbrew/bin",
            "/var/lib/flatpak/exports/bin",
            os.path.expanduser("~/.local/bin"),
            os.path.expanduser("~/.local/share/mp-mix-manager/python-env/bin"),
        ]
    return [d for d in candidates if d and os.path.isdir(d)]


def which_binary(name: str) -> str:
    """shutil.which plus the prefixes a minimal PATH forgets."""
    found = shutil.which(name)
    if found:
        return found
    suffixes = [""]
    if host_os() == "windows":
        suffixes += [".exe", ".cmd", ".bat"]
    for directory in extra_bin_dirs():
        for suffix in suffixes:
            candidate = os.path.join(directory, name + suffix)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return ""


def which_library(name: str) -> str:
    """Locate a shared library that Python loads through ctypes.

    A ctypes-loaded library never appears on PATH and cannot be import-tested,
    so `which_binary` misses it. Check the loader cache first, then the
    multiarch and vendor directories used by Debian, Fedora, Arch, Homebrew and
    the macOS frameworks.

    macOS needs its own names: the Linux spec says `libglfw.so.3`, but Homebrew
    ships `libglfw.3.dylib` (and `libglfw.dylib` as the symlink) and never a
    `.so`. Probing the Linux name on a Mac reported GLFW missing even though
    `brew list glfw` was satisfied, which failed the install on a machine that
    was already correct.
    """
    if host_os() == "windows":
        return _which_windows_library(name)
    if host_os() == "macos":
        return _which_dylib(name)
    cache = "/sbin/ldconfig"
    if not os.path.exists(cache):
        cache = shutil.which("ldconfig") or ""
    if cache:
        try:
            proc = subprocess.run([cache, "-p"], capture_output=True, text=True, timeout=15)
            for line in (proc.stdout or "").splitlines():
                if name in line and "=>" in line:
                    return line.rsplit("=>", 1)[1].strip()
        except (OSError, subprocess.SubprocessError):
            pass
    multiarch = platform.machine()
    for directory in (
        "/usr/lib",
        "/usr/lib64",
        "/usr/local/lib",
        f"/usr/lib/{multiarch}-linux-gnu",
        f"/usr/lib/{multiarch}-linux-musl",
        "/lib",
        "/lib64",
        f"/lib/{multiarch}-linux-gnu",
        os.path.join(os.path.expanduser("~"), ".local", "lib"),
    ):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return ""


def _which_windows_library(name: str) -> str:
    """Find a Windows DLL by any of its reasonable filenames.

    The GLFW spec names `libglfw.so.3`, which is the Linux spelling. On Windows
    the PyPI `glfw` package ships `glfw3.dll` inside its own package directory
    and there is no `glfw.dll` on PATH, so probing the Linux name reported GLFW
    missing on a machine where `glfw.init()` already returned 1.
    """
    # "libglfw.so.3" and "libglfw.3.dylib" both mean the library "glfw",
    # version 3. Strip an optional extension, then an optional trailing version,
    # in either order, then the Windows-breaking "lib" prefix.
    stem = name
    version = ""
    for extension in (".dll", ".dylib", ".so"):
        if stem.endswith(extension):
            stem = stem[: -len(extension)]
            break
        marker = extension + "."
        if marker in stem:
            head, _, tail = stem.partition(marker)
            if tail.isdigit():
                stem, version = head, tail
            break
    head, _, tail = stem.rpartition(".")
    if head and tail.isdigit():
        stem, version = head, tail
    if stem.lower().startswith("lib"):
        stem = stem[3:]
    candidates = [name]
    for candidate in (
        f"{stem}.dll",
        f"{stem}{version}.dll" if version else "",
        f"lib{stem}.dll",
        f"lib{stem}{version}.dll" if version else "",
        stem,
        f"lib{stem}",
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        located = which_binary(candidate)
        if located:
            return located
    # A Python package can carry its own DLL; `import glfw` then works with no
    # system copy at all, which is the normal Windows install.
    for directories in _windows_package_dirs():
        for candidate in candidates:
            path = os.path.join(directories, candidate)
            if os.path.isfile(path):
                return path
    return ""


def _windows_package_dirs() -> List[str]:
    """site-packages folders where a package ships its own DLLs."""
    found: List[str] = []
    # The launchers prefer <install>/venv, and that is where pip puts the
    # PyPI glfw wheel. Without this directory the engine installed glfw3.dll
    # into the venv and then still reported GLFW missing, so an install looked
    # successful while MP Gui exited with "Could not load GLFW on Windows".
    try:
        site = venv_path() / "Lib" / "site-packages"
        if site.is_dir():
            found.append(str(site / "glfw"))
            found.append(str(site))
    except (NameError, OSError):
        pass
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    for base in (
        os.path.join(local, "Programs", "Python"),
        program_files,
        os.path.join(program_files, "Python"),
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "anaconda3"),
    ):
        if not base or not os.path.isdir(base):
            continue
        try:
            for entry in os.listdir(base):
                if not entry.lower().startswith("python"):
                    continue
                site = os.path.join(base, entry, "Lib", "site-packages")
                if os.path.isdir(site):
                    found.append(os.path.join(site, "glfw"))
                    found.append(site)
        except OSError:
            continue
    return [d for d in found if os.path.isdir(d)]


def _dylib_names(name: str) -> List[str]:
    """Every filename a dylib can have, from a Linux or a macOS spelling."""
    names: List[str] = []

    def add(value: str) -> None:
        if value and value not in names:
            names.append(value)

    add(name)
    # Split "libglfw.so.3" / "libglfw.3.dylib" into a stem and a version.
    stem = name
    version = ""
    for suffix in (".dylib", ".so"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    rest = stem.rsplit(".", 1)
    if len(rest) == 2 and rest[1].isdigit():
        stem, version = rest
    # The stem must not keep a ".so" that was already consumed above.
    stem = stem[:-3] if stem.endswith(".so") else stem
    # libglfw -> libglfw.dylib, libglfw.3.dylib, libglfw.3.5.dylib
    add(f"{stem}.dylib")
    if version:
        add(f"{stem}.{version}.dylib")
        add(f"{stem}.{version}")
    # libglfw.so.3 -> libglfw.so.3 / libglfw.so
    add(f"{stem}.so")
    if version:
        add(f"{stem}.so.{version}")
    add(stem)
    return names


def _which_dylib(name: str) -> str:
    """Find a macOS dylib by any of its reasonable filenames."""
    directories = [
        "/opt/homebrew/lib",
        "/usr/local/lib",
        "/opt/local/lib",
        "/usr/lib",
        os.path.join(os.path.expanduser("~"), ".local", "lib"),
    ]
    for candidate_name in _dylib_names(name):
        located = which_binary(candidate_name)
        if located:
            return located
        for directory in directories:
            candidate = os.path.join(directory, candidate_name)
            if os.path.isfile(candidate):
                return candidate
    return ""


def _works(path: str, minimum: Tuple[int, int] = (3, 8)) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        proc = subprocess.run(
            [path, "-c", "import sys; raise SystemExit(0 if sys.version_info >= %r else 1)" % (minimum,)],
            capture_output=True,
            timeout=30,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# One interpreter start answers every module question. Starting python once per
# module cost about a second and a half on a cold cache; this is the hot path of
# the installer, so it is batched.
#
# Two tiers, because `find_spec` alone lies:
#   * optional modules are checked with find_spec, which does not execute them.
#     Importing ultralytics pulls in PyTorch and took over two seconds, and an
#     optional feature that is present but broken must not fail an install.
#   * required modules are actually imported. A wheel can be found by find_spec
#     and still be unusable: numpy 2.x never sets a pre-2.0 x86-64-v2 baseline,
#     so on a Core 2 Duo `import numpy` dies with "your machine doesn't support
#     X86_V2" while find_spec happily reports it installed. The installer then
#     claimed 8/8 present and the player crashed on launch. Only a real import
#     can tell the difference.
PROBE_CODE = r"""
import importlib, importlib.util, json, sys

modules = json.loads(sys.argv[1])
typelibs = json.loads(sys.argv[2])
out = {}
for item in modules:
    if isinstance(item, str):
        name, mode = item, "optional"
    else:
        name, mode = item[0], item[1]
    try:
        spec = importlib.util.find_spec(name)
    except BaseException:
        spec = None
    if spec is None:
        out[name] = False
        continue
    if mode != "required":
        # Not executed on purpose: keeps heavy optional packages cheap.
        out[name] = True
        continue
    try:
        importlib.import_module(name)
        out[name] = True
    except BaseException:
        # Found but unimportable: a broken build, a missing shared library or a
        # CPU baseline this machine cannot run. Treat it as missing so the
        # installer installs the distro package instead of declaring success.
        out[name] = False
try:
    import gi
    for namespace, version in typelibs:
        try:
            gi.require_version(namespace, version)
            importlib.import_module("gi.repository." + namespace)
            out[namespace] = True
        except BaseException:
            out[namespace] = False
except BaseException:
    for namespace, _version in typelibs:
        out.setdefault(namespace, False)
print(json.dumps(out))
"""


def probe_modules(
    python: str,
    modules: Sequence[str],
    typelibs: Sequence[Tuple[str, str]],
    required: Sequence[str] = (),
) -> Dict[str, bool]:
    """Import every module and typelib once, in one interpreter.

    Modules named in *required* are really imported; the rest are only checked
    for a spec, so a slow optional package never delays the probe.
    """
    if not python:
        return {}
    wanted = set(required)
    payload = [[name, "required" if name in wanted else "optional"] for name in modules]
    try:
        proc = subprocess.run(
            [python, "-c", PROBE_CODE, json.dumps(payload), json.dumps([list(t) for t in typelibs])],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return {str(k): bool(v) for k, v in json.loads(line).items()}
        except json.JSONDecodeError:
            return {}
    return {}


def _module_state(python: str, module: str, namespace: str = "", version: str = "") -> str:
    """Return 'ok' or 'missing' for one module or typelib."""
    if namespace:
        state = probe_modules(python, [], [(namespace, version)])
        return "ok" if state.get(namespace) else "missing"
    state = probe_modules(python, [module], [], required=[module])
    return "ok" if state.get(module) else "missing"


def python_candidates() -> List[str]:
    """Interpreters to consider, best first.

    Order matters: on macOS /usr/bin/python3 is Apple's Command Line Tools
    Python, which has none of the GTK stack, while Homebrew's python3 has
    PyGObject. Picking the system one first built a venv that could not open
    MP Gui. So capability beats location, and choose_python() re-checks.
    """
    kind = host_os()
    seen: List[str] = []
    existing = os.environ.get("MP_PYTHON", "").strip()
    if existing:
        seen.append(existing)
    # The venv the launchers prefer, before any base interpreter.
    env_dir = venv_path()
    for name in ("bin/python3", "bin/python", "Scripts/python.exe"):
        candidate = env_dir / name
        if candidate.exists():
            seen.append(str(candidate))
    if kind == "windows":
        local = os.environ.get("LOCALAPPDATA", "")
        for version in ("313", "312", "311", "310"):
            seen.append(rf"{local}\Programs\Python\Python{version}\python.exe")
        seen += [r"C:\Program Files\Python313\python.exe"]
    if kind == "macos":
        # Homebrew first: it carries PyGObject, gtk+3 and the audio modules.
        seen += [
            "/opt/homebrew/bin/python3",
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/usr/local/bin/python3",
            "/usr/local/bin/python3.13",
        ]
    for name in ("python3", "python"):
        found = which_binary(name)
        if found and "WindowsApps" not in found:
            seen.append(found)
    for fixed in (
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
        "/home/linuxbrew/.linuxbrew/bin/python3",
    ):
        if os.path.exists(fixed):
            seen.append(fixed)
    unique: List[str] = []
    for item in seen:
        if item and item not in unique:
            unique.append(item)
    return unique


def choose_python(prefer_gtk: bool = True) -> str:
    """Best interpreter: can import gi when GTK matters, else any 3.8+."""
    candidates = [p for p in python_candidates() if _works(p)]
    if prefer_gtk and host_os() in {"linux", "freebsd", "macos"}:
        for candidate in candidates:
            if _module_state(candidate, "gi") == "ok":
                return candidate
    if candidates:
        return candidates[0]
    return ""


# ------------------------------------------------------------------------------
# probing
# ------------------------------------------------------------------------------
def _applies(spec: Dict[str, Any]) -> bool:
    platforms = spec.get("platforms")
    if not platforms:
        return True
    return host_os() in platforms


def probe(python: str, include_optional: bool = True) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    modules: List[str] = []
    required_modules: List[str] = []
    typelibs: List[Tuple[str, str]] = []
    for spec in SPECS:
        if not _applies(spec):
            continue
        if spec["kind"] == "module" and spec.get("module") not in modules:
            modules.append(spec["module"])
            # Hard dependencies are really imported: a wheel can be present and
            # still unusable (numpy on a pre-AVX CPU). See PROBE_CODE.
            if spec.get("required"):
                required_modules.append(spec["module"])
        elif spec["kind"] == "typelib":
            typelibs.append((spec["namespace"], spec["version"]))

    state = probe_modules(python, modules, typelibs, required=required_modules)

    for spec in SPECS:
        if not _applies(spec):
            continue
        entry: Dict[str, Any] = {
            "id": spec["id"],
            "kind": spec["kind"],
            "required": bool(spec.get("required")),
            "purpose": spec.get("purpose", ""),
            "note": spec.get("note", ""),
        }
        if spec["kind"] == "binary":
            # A `binary` key names a shared object that Python loads through
            # ctypes (GLFW). Those never appear on PATH and cannot be
            # import-tested, so the loader cache is searched instead.
            probe_name = spec.get("binary") or spec["id"]
            finder = which_library if spec.get("binary") else which_binary
            found = finder(probe_name)
            entry["found"] = bool(found)
            entry["path"] = found or ""
        elif spec["kind"] == "typelib":
            entry["found"] = bool(state.get(spec["namespace"]))
        else:
            entry["found"] = bool(state.get(spec["module"]))
            entry["module"] = spec["module"]
        results.append(entry)
    if not include_optional:
        # Optional entries stay in the report; callers decide whether to act.
        pass
    return results


def missing(results: Sequence[Dict[str, Any]], optional: bool) -> List[Dict[str, Any]]:
    return [r for r in results if not r["found"] and (optional or r["required"])]


# ------------------------------------------------------------------------------
# installing
# ------------------------------------------------------------------------------
def run(cmd: Sequence[str], dry_run: bool, capture: bool = True) -> Tuple[int, str]:
    printable = " ".join(cmd)
    if dry_run:
        print(f"  would run: {printable}")
        return 0, ""
    print(f"  running:   {printable}")
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=capture,
            text=True,
            errors="replace",
            timeout=3600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def sudo_prefix(dry_run: bool) -> Optional[List[str]]:
    """[] when no elevation is needed, ['sudo',...] when it is, None if blocked."""
    if host_os() in {"windows", "wsl"}:
        return []
    if os.geteuid() == 0:
        return []
    password = os.environ.get("MP_SUDO_PASSWORD", "")
    if which_binary("sudo") is None:
        return None
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return ["sudo", "-n"]
    if password:
        return ["sudo", "-S", "-p", ""]
    if sys.stdin.isatty():
        # Prompt once, then every later sudo call is non-interactive.
        print("  administrator access is needed for system packages")
        if subprocess.run(["sudo", "-v"]).returncode == 0:
            return ["sudo", "-n"]
    return None


def venv_path() -> Path:
    """Where the launchers look for the MP Python environment.

    Windows launchers (scripts/windows_find_python.cmd) check <install>/venv
    before anything else, so the venv has to live there. macOS and Linux read
    ~/.local/share/mp-mix-manager/python-env (see resolve_mp_python.sh and
    ensure_mp_python_env.sh).
    """
    if host_os() == "windows":
        return ROOT / "venv"
    return Path.home() / ".local" / "share" / "mp-mix-manager" / "python-env"


def ensure_venv(python: str, dry_run: bool) -> str:
    """Create or reuse the per-user venv the launchers already look for."""
    env_dir = venv_path()
    env_py = env_dir / ("Scripts/python.exe" if host_os() == "windows" else "bin/python3")
    if env_py.exists() and _works(str(env_py)):
        return str(env_py)
    if dry_run:
        print(f"  would run:   {python} -m venv --system-site-packages {env_dir}")
        return str(env_py)
    print(f"  creating:    {env_dir}")
    env_dir.parent.mkdir(parents=True, exist_ok=True)
    rc, output = run([python, "-m", "venv", "--system-site-packages", str(env_dir)], dry_run=False)
    if rc != 0 or not env_py.exists():
        for line in output.strip().splitlines()[-4:]:
            print(f"  {DIM}{line}{NC}")
        return ""
    # make sure pip is inside the venv even when the base interpreter has none
    if subprocess.run([str(env_py), "-m", "pip", "--version"],
                      capture_output=True).returncode != 0:
        run([str(env_py), "-m", "ensurepip", "--upgrade"], dry_run=False)
    return str(env_py)


def install_command(manager: str, packages: Sequence[str]) -> List[str]:
    if manager == "dnf":
        binary = which_binary("dnf5") or which_binary("dnf") or "dnf"
        return [binary, "install", "-y", "--skip-unavailable", *packages]
    if manager == "apt":
        return ["apt-get", "install", "-y", "-o", "Dpkg::Options::=--force-confdef", *packages]
    if manager == "pacman":
        return ["pacman", "-S", "--needed", "--noconfirm", *packages]
    if manager == "zypper":
        return ["zypper", "--non-interactive", "install", *packages]
    if manager == "brew":
        # `which_binary` deliberately searches /opt/homebrew/bin because a bare
        # SSH session has a minimal PATH. Returning the bare word "brew" threw
        # that away: the command was built as ["brew", "install", ...] and the
        # probe already knew the real path, so a Mac install failed with
        # FileNotFoundError: 'brew' on a machine where brew was installed.
        return [which_binary("brew") or "brew", "install", *packages]
    if manager == "pkg":
        return ["pkg", "install", "-y", *packages]
    if manager == "rpm-ostree":
        # --apply-live takes effect without a reboot.
        return ["rpm-ostree", "install", "--allow-inactive", "--apply-live", *packages]
    if manager == "winget":
        cmd: List[str] = ["winget", "install", "--silent",
                          "--accept-package-agreements",
                          "--accept-source-agreements"]
        for package in packages:
            cmd += ["--id", package, "--exact"]
        return cmd
    return []


def system_install_command(
    manager: str,
    packages: Sequence[str],
    dry_run: bool,
    interactive: bool,
) -> Tuple[List[str], Optional[List[str]]]:
    """Build the install command, adding sudo where the manager needs it."""
    base = install_command(manager, packages)
    if not base:
        return [], ["no install command for {0}".format(manager)]
    if manager in {"dnf", "apt", "pacman", "zypper", "pkg", "rpm-ostree"}:
        prefix = sudo_prefix(dry_run)
        if prefix is None:
            return [], [
                "administrator access unavailable; run this yourself:",
                "  sudo " + " ".join(base),
            ]
        base = prefix + base
    if manager == "brew":
        os.environ.setdefault("HOMEBREW_NO_AUTO_UPDATE", "1")
        os.environ.setdefault("HOMEBREW_NO_INSTALL_CLEANUP", "1")
    if not interactive and manager == "apt":
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    return base, []


def install_binaries(
    targets: Sequence[Dict[str, Any]],
    manager: str,
    dry_run: bool,
    interactive: bool,
) -> Tuple[bool, List[str]]:
    packages: List[str] = []
    for entry in targets:
        spec = next((s for s in SPECS if s["id"] == entry["id"]), None)
        if spec is None or spec.get("same_package_as"):
            # ffprobe ships inside ffmpeg, so never ask for it separately.
            continue
        for package in spec.get("pkgs", {}).get(manager, []):
            if package not in packages:
                packages.append(package)
    if not packages:
        return False, ["no {0} package name known".format(manager or "supported")]
    cmd, problem = system_install_command(manager, packages, dry_run, interactive)
    if problem:
        return False, problem
    rc, output = run(cmd, dry_run)
    if rc != 0:
        return False, tail_lines(output, rc)
    return True, []


def install_modules(
    python: str,
    targets: Sequence[Dict[str, Any]],
    manager: str,
    dry_run: bool,
    interactive: bool,
) -> Tuple[bool, List[str], str]:
    """Install Python modules into a venv the launchers already search.

    PyGObject is deliberately excluded: it needs system headers plus a matching
    girepository, so it must come from the distro. If it is missing and the
    package manager cannot help, the installer reports the exact command.
    """
    pip_targets = [t for t in targets if _pip_name(t)]
    system_targets = [t for t in targets if t["id"] == "PyGObject"]

    notes: List[str] = []
    if system_targets and manager and manager != "brew":
        ok, extra = install_binaries(system_targets, manager, dry_run, interactive)
        if not ok:
            notes.extend(extra)

    if not pip_targets:
        return False, notes, python
    if not python:
        return False, notes + ["no Python interpreter to install into"], python

    # Modules always go into the per-user venv: on Windows the launchers look
    # for <install>/venv first, on macOS/Linux for
    # ~/.local/share/mp-mix-manager/python-env. Writing to the base interpreter
    # would either need root or be invisible to the launchers.
    target_python = ensure_venv(python, dry_run)

    packages = [_pip_name(t) for t in pip_targets if _pip_name(t)]
    if not packages:
        return False, notes, target_python
    if dry_run:
        print("  would run:   {0} -m pip install {1}".format(target_python, " ".join(packages)))
        return True, notes, target_python
    rc, output = run(
        [target_python, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", *packages],
        dry_run=False,
    )
    if rc != 0:
        notes.extend(tail_lines(output, rc))
        return False, notes, target_python

    # A wheel can install cleanly and still be unusable on this CPU. numpy 2.x
    # is built for an x86-64-v2 baseline, so on a Core 2 Duo it installs, then
    # every import raises "your machine doesn't support X86_V2". Report that as
    # a failure so the caller can try the distro build, which is compiled for
    # the distribution's own baseline and does run.
    broken = _unimportable(target_python, [t["module"] for t in pip_targets if t.get("module")])
    if broken:
        notes.append("installed but not importable on this CPU: " + ", ".join(broken))
        return False, notes, target_python
    return True, notes, target_python


def _unimportable(python: str, modules: Sequence[str]) -> List[str]:
    """Modules that are installed yet raise when imported."""
    if not python or not modules:
        return []
    state = probe_modules(python, list(modules), [], required=list(modules))
    return [name for name in modules if not state.get(name)]


def install_distro_modules(
    targets: Sequence[Dict[str, Any]],
    manager: str,
    dry_run: bool,
    interactive: bool,
    python: str,
) -> Tuple[bool, List[str]]:
    """Replace pip copies with the distribution's build of the same module.

    Used only after pip produced something that cannot be imported. The pip
    copy must be removed first, because it sits earlier on sys.path than the
    distro site-packages and would keep shadowing it inside the venv.
    """
    if not manager or manager == "brew":
        return False, ["no distribution package manager to fall back to"]
    notes: List[str] = []
    for entry in targets:
        pip_name = _pip_name(entry)
        if pip_name and python and not dry_run:
            # Best effort: a module that was never installed is fine here.
            run([python, "-m", "pip", "uninstall", "-y", "--quiet", pip_name], dry_run=False)
    ok, extra = install_binaries(targets, manager, dry_run, interactive)
    if not ok:
        notes.extend(extra)
        return False, notes
    # rpm-ostree --apply-live may still need a moment before the files appear.
    if not dry_run and python:
        broken = _unimportable(python, [t["module"] for t in targets if t.get("module")])
        if broken:
            notes.append(
                "the distribution package is installed but a new login is required "
                "before it is visible: " + ", ".join(broken)
            )
            return False, notes
    return True, notes


def tail_lines(output: str, rc: int, count: int = 6) -> List[str]:
    lines = [line for line in (output or "").strip().splitlines() if line.strip()]
    return lines[-count:] or ["exit status {0}".format(rc)]


def _pip_name(entry: Dict[str, Any]) -> str:
    spec = next((s for s in SPECS if s["id"] == entry["id"]), None)
    return (spec or {}).get("pip", "") or ""


def requirements_entries(path: Path) -> List[str]:
    """Parse a requirements file into bare package names."""
    names: List[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return names
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";"):
            if separator in line:
                line = line.split(separator, 1)[0]
        line = line.strip()
        if line and line not in names:
            names.append(line)
    return names


# ------------------------------------------------------------------------------
# reporting
# ------------------------------------------------------------------------------
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
GREEN = "\033[0;32m" if COLOR else ""
RED = "\033[0;31m" if COLOR else ""
YELLOW = "\033[0;33m" if COLOR else ""
DIM = "\033[2m" if COLOR else ""
BOLD = "\033[1m" if COLOR else ""
NC = "\033[0m" if COLOR else ""


def print_report(results: Sequence[Dict[str, Any]], python: str, optional: bool) -> None:
    hard = [r for r in results if r["required"]]
    soft = [r for r in results if not r["required"]]
    ok_hard = sum(1 for r in hard if r["found"])
    print(f"{BOLD}Runtime dependencies{NC}  ({python or 'no interpreter found'})")
    for entry in hard:
        state = f"{GREEN}OK     {NC}" if entry["found"] else f"{RED}MISSING{NC}"
        detail = entry.get("path", "") or entry.get("module", "") or ""
        print(f"  [{state}] {entry['id']:<14} {DIM}{detail}{NC}")
    if soft:
        print(f"{BOLD}Optional features{NC}")
        for entry in soft:
            if entry["found"]:
                state = f"{GREEN}OK     {NC}"
            elif optional:
                state = f"{YELLOW}MISSING{NC}"
            else:
                state = f"{DIM}not set{NC}"
            print(f"  [{state}] {entry['id']:<14} {DIM}{entry['purpose']}{NC}")
    satisfied = ok_hard == len(hard)
    verdict = f"{GREEN}all required dependencies present{NC}" if satisfied else \
              f"{RED}{len(hard) - ok_hard} required dependency(ies) missing{NC}"
    print(f"  {BOLD}Result:{NC} {verdict}  ({ok_hard}/{len(hard)})")


# ------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MP Mix Manager dependency engine")
    parser.add_argument("--probe", action="store_true", help="report dependency state")
    parser.add_argument("--install", action="store_true", help="install what is missing")
    parser.add_argument("--verify", action="store_true", help="exit 1 if a hard dependency is missing")
    parser.add_argument("--python-path", action="store_true",
                        help="print the interpreter the launchers should use")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--python", default="", help="interpreter to probe and install into")
    parser.add_argument("--requirements", default=str(REQUIREMENTS_FILE))
    parser.add_argument("--no-system", action="store_true", help="never use a package manager")
    parser.add_argument("--no-pip", action="store_true", help="never use pip")
    parser.add_argument("--optional", action="store_true", help="install optional dependencies too")
    parser.add_argument("--yes", "-y", action="store_true", help="no prompts")
    parser.add_argument("--dry-run", action="store_true", help="print commands, change nothing")
    args = parser.parse_args(argv)

    if args.python_path:
        # One answer, used by the installer, the reinstall helper and the test
        # harness, so nothing can disagree about which interpreter has gi/numpy.
        chosen = args.python or choose_python()
        print(chosen)
        return 0 if chosen else 1

    if not (args.probe or args.install or args.verify):
        parser.error("one of --probe, --install, --verify, --python-path is required")

    python = args.python or choose_python()
    if python and not _works(python):
        python = choose_python()
    results = probe(python)

    if os.environ.get("MP_SKIP_SYSTEM_PKGS") == "1":
        args.no_system = True
    if os.environ.get("MP_SKIP_PIP") == "1":
        args.no_pip = True
    if os.environ.get("MP_PKG_ASSUME_YES") == "1":
        args.yes = True

    wanted = missing(results, args.optional)
    installed: List[str] = []
    still_failing: List[str] = []

    if args.install and wanted:
        manager = "" if args.no_system else package_manager()
        binaries = [r for r in wanted if r["kind"] in {"binary", "typelib"}]
        modules = [r for r in wanted if r["kind"] == "module"]

        if binaries:
            ok, notes = install_binaries(binaries, manager, args.dry_run, args.yes)
            if ok:
                installed.extend(r["id"] for r in binaries)
            else:
                still_failing.extend(r["id"] for r in binaries)
                for note in notes:
                    print(f"  {DIM}{note}{NC}")
            # Some binary dependencies also ship as a pip wheel. GLFW is the
            # important one: Windows has no platform package for it in every
            # manager (winget has no id at all), but the PyPI `glfw` wheel
            # carries glfw3.dll. Without this fallback `--install` finished
            # with GLFW still missing and the GUI died silently at launch.
            pip_binaries = [
                r for r in binaries
                if r["id"] in still_failing and _pip_name(r)
            ]
            if pip_binaries and not args.no_pip:
                ok, notes, used_python = install_modules(
                    python, pip_binaries, "", args.dry_run, args.yes
                )
                if used_python and _works(used_python):
                    python = used_python
                if ok:
                    installed.extend(r["id"] for r in pip_binaries)
                else:
                    for note in notes:
                        print(f"  {DIM}{note}{NC}")

        if modules:
            if args.no_pip:
                still_failing.extend(r["id"] for r in modules)
            else:
                ok, notes, used_python = install_modules(
                    python, modules, module_manager(), args.dry_run, args.yes
                )
                if used_python and _works(used_python):
                    # Report against the interpreter that now holds the modules,
                    # which is the venv the launchers will actually start.
                    python = used_python
                if ok:
                    installed.extend(r["id"] for r in modules)
                else:
                    still_failing.extend(r["id"] for r in modules)
                    for note in notes:
                        print(f"  {DIM}{note}{NC}")
                    # pip can install a wheel this CPU cannot run (numpy's
                    # x86-64-v2 baseline on a Core 2 Duo). The distribution
                    # builds for its own baseline, so try that before giving up.
                    retry = [r for r in modules if r["id"] not in installed]
                    if retry and manager and manager != "brew":
                        print(f"  {DIM}trying the distribution build: {manager}{NC}")
                        ok2, notes2 = install_distro_modules(
                            retry, manager, args.dry_run, args.yes, python
                        )
                        if ok2:
                            installed.extend(r["id"] for r in retry)
                        for note in notes2:
                            print(f"  {DIM}{note}{NC}")

        results = probe(python)
        # Anything still absent after the install path ran is a real failure.
        still_failing = [r["id"] for r in missing(results, False)]

    if args.json:
        payload = {
            "platform": host_os(),
            "package_manager": package_manager(),
            "python": python,
            "dependencies": results,
            "requirements_file": str(args.requirements),
            "requirements": requirements_entries(Path(args.requirements)),
            "installed": installed,
            "still_missing": [r["id"] for r in missing(results, False)],
            "ok": not missing(results, False),
        }
        print(json.dumps(payload, indent=2))
    else:
        print_report(results, python, args.optional)

    unmet = missing(results, False)
    if unmet and not args.json:
        print()
        print(f"{YELLOW}Install these to finish the setup:{NC}")
        manager = package_manager()
        for entry in unmet:
            spec = next((s for s in SPECS if s["id"] == entry["id"]), None)
            packages = (spec or {}).get("pkgs", {}).get(manager, [])
            pip_name = (spec or {}).get("pip", "")
            if packages:
                # Name the manager that would actually be used, so a Windows
                # user is not told to run a pip package as a winget id.
                hint = f" ({manager} {' '.join(packages)})"
            elif pip_name:
                hint = f" (pip install {pip_name})"
            else:
                hint = ""
            print(f"  - {entry['id']}: {entry['purpose']}{hint}")

    return 0 if not unmet else 1


if __name__ == "__main__":
    raise SystemExit(main())
