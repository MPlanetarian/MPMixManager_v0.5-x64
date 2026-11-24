"""Whether a GTK/GLFW window can be created on this host.

Importing Gtk succeeds without a display, but constructing a widget aborts the
process inside GLib instead of raising. A test bed reached over SSH has no
DISPLAY or WAYLAND_DISPLAY, so GUI tests must skip before they build one.
"""

import os
import sys

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_QT_PLATFORMS = ("darwin", "win32")
WINDOWED_HOST = HAS_DISPLAY or sys.platform.startswith(_QT_PLATFORMS)
