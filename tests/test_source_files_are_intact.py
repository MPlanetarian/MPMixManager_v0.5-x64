#!/usr/bin/env python3
"""Every shipped source file must still be readable source.

Reported from the Windows 10 live run: **Launch MP Visualizer** in MP Tray did
nothing. The BAT launcher exited 0 and the tray logged a successful spawn, but
no window ever appeared. The visualizer's own log was empty, because the
process died during import, long before any of its logging started.

The cause was not the launcher, the tray, or GLFW. ``scripts/liquid_glass_opengl.py``
had its last five lines replaced by NUL bytes, so importing it raised
``SyntaxError: source code string cannot contain null bytes``. The visualizer
calls ``liquid_glass_opengl.load_glfw_dll()`` whenever it opens a window, so a
single unreadable helper module closed every visualizer process silently.

Nothing in the suite caught it: the visualizer tests mock the GLFW loader, and
no test ever compiled the shipped modules as a group. These tests do, so a
truncated file, a NUL-filled tail, or a broken block is reported as what it is
instead of surfacing as "the menu item does nothing" on a live machine.

They are deliberately cheap: the Python check compiles only (no imports, so no
GPU, audio device or desktop is needed), and the shell check is ``bash -n``.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The modules the Windows tray launches. The tray starts each of these through
# a .bat/.cmd shortcut with pythonw.exe, which has no console: an import error
# in any of them is invisible, and the menu item simply appears to do nothing.
LAUNCH_TARGETS = (
    "MP_Liquid_Visualizer.py",
    "MP_Audio_Player_Liquid_Glass_OpenGL.py",
    "MP_Liquid_Mixer_Windows_10.py",
    "Mix_Archive_Manager.sh",
)

SOURCE_GLOBS = ("*.py", "scripts/*.py")


def _shipped_python_files():
    """Every Python module shipped at the root and in scripts/.

    Test files are included on purpose: a truncated test module also breaks
    discovery, and the failure mode (a whole test file silently missing from a
    run) is the same class of bug.
    """
    found = []
    for pattern in SOURCE_GLOBS:
        found.extend(sorted(ROOT.glob(pattern)))
    return [path for path in found if path.is_file()]


class TestNoShippedSourceIsCorrupt(unittest.TestCase):
    def test_no_python_source_contains_nul_bytes(self):
        """A NUL byte makes the file unimportable, whatever else it contains."""
        offenders = []
        for path in _shipped_python_files():
            try:
                data = path.read_bytes()
            except OSError as exc:  # pragma: no cover - permissions only
                offenders.append(f"{path.relative_to(ROOT)}: {exc}")
                continue
            if b"\x00" in data:
                first = data.index(b"\x00")
                offenders.append(
                    f"{path.relative_to(ROOT)}: NUL at byte {first} of {len(data)}"
                )
        self.assertEqual(offenders, [], "corrupt source files: " + "; ".join(offenders))

    def test_every_python_module_compiles(self):
        """Compile, do not import: no GPU, audio device or desktop is required.

        A compile of every module is what would have caught the truncated
        ``liquid_glass_opengl.py`` before the live test did. ``py_compile``
        reads the file exactly as the interpreter will, so a half-written tail
        or a broken indentation block fails here.
        """
        files = [path for path in _shipped_python_files() if path.name != "__init__.py"]
        self.assertTrue(files, "no Python sources were discovered")
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", *[str(p) for p in files]],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_launch_targets_exist_and_compile(self):
        """The four things the tray starts must be present and readable.

        This is the direct regression: a broken
        ``MP_Audio_Player_Liquid_Glass_OpenGL.py`` or
        ``MP_Liquid_Visualizer.py`` means a tray click that silently does
        nothing, which is exactly what the Windows 10 run reported.
        """
        missing = [name for name in LAUNCH_TARGETS if not (ROOT / name).is_file()]
        self.assertEqual(missing, [], f"tray launch targets are missing: {missing}")

        python_targets = [n for n in LAUNCH_TARGETS if n.endswith(".py")]
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", *[str(ROOT / n) for n in python_targets]],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_visualizer_glfw_helper_imports(self):
        """The exact module that was corrupt: it must import, not just compile.

        ``liquid_glass_opengl`` is imported lazily from inside
        ``load_glfw_dll()``, so a syntax error there is raised at the moment
        the window is created and looks like "the visualizer does not open".
        Importing it here is safe: the module has no side effects beyond
        setting ``PYOPENGL_PLATFORM``.
        """
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import liquid_glass_opengl  # noqa: F401
        except SyntaxError as exc:  # pragma: no cover - the regression
            self.fail(f"liquid_glass_opengl.py cannot be imported: {exc}")
        self.assertTrue(callable(liquid_glass_opengl.load_glfw_dll))

    def test_the_manager_shell_parses(self):
        """``bash -n`` on the manager: a broken script is a dead menu item."""
        bash = None
        for candidate in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
        ):
            if candidate.is_file():
                bash = str(candidate)
                break
        bash = bash or "bash"
        result = subprocess.run(
            [bash, "-n", str(ROOT / "Mix_Archive_Manager.sh")],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "WindowsApps" in (result.stderr or ""):
            self.skipTest("only the Microsoft Store bash stub is available here")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
