"""A minimal system PATH that still finds the shell and the base tools.

Several tests replace PATH on purpose so a fake ffplay or ffmpeg wins and a
developer's ~/.local/bin cannot leak in. They used to hard-code ``/usr/bin:/bin``,
which assumes a Linux layout; on FreeBSD and Apple Silicon the shell and the
media tools live in /usr/local/bin or /opt/homebrew/bin. Include those prefixes
when they exist so the same test describes the same environment everywhere.
"""

import os

_CANDIDATES = ("/usr/bin", "/bin", "/usr/local/bin", "/usr/local/sbin",
               "/opt/homebrew/bin")

SYSTEM_PATH = os.pathsep.join(p for p in _CANDIDATES if os.path.isdir(p))


def system_path(*leading):
    """Return *leading* directories followed by the system prefixes."""
    parts = [str(p) for p in leading]
    parts.extend(SYSTEM_PATH.split(os.pathsep))
    return os.pathsep.join(parts)
