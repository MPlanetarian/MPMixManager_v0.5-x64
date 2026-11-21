#!/usr/bin/env python3
"""Windows 10 smoke tests for the Mix Archive Manager menu.

The manager is a full-screen TUI, so the tests split the work in two:

  * Static checks prove every entry in the live ``main_opts`` array has a
    dispatch branch and that every function those branches call is defined.
  * Headless checks run the manager's one-shot entry points (which use the
    same helpers as the menu rows) and fail on a Python traceback, a shell
    syntax error, ``command not found`` or an unbound variable.

Run with Git Bash first on PATH on Windows so ``bash`` is not the WSL stub.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "Mix_Archive_Manager.sh"
TIMEOUT = 45


def _git_bash() -> str:
    found = shutil.which("bash")
    if found and "windowsapps" in found.lower():
        found = None
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
        Path(r"C:\msys64\usr\bin\bash.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return "bash"


BASH = _git_bash()


def _run(args: list[str], timeout: int = TIMEOUT, env: dict | None = None,
         python: bool = False):
    launcher = sys.executable if python else BASH
    return subprocess.run(
        [launcher, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def _main_options() -> list[tuple[str, str]]:
    text = MANAGER.read_text(encoding="utf-8", errors="replace")
    start = text.index("main_opts=(")
    i = start + len("main_opts=(")
    depth, quote, esc = 1, None, False
    while i < len(text) and depth:
        ch = text[i]
        if quote:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                quote = None
        else:
            if ch in ("\"", "'"):
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        i += 1
    block = text[start:i]
    options = []
    for item in re.findall(r'"([^"]*)"', block):
        if "|" not in item:
            continue
        key, rest = item.split("|", 1)
        key = key.strip()
        if key in ("", "SECTION", "SEP"):
            continue
        options.append((key, rest.split("|", 1)[0]))
    return options


def _dispatch_text() -> str:
    text = MANAGER.read_text(encoding="utf-8", errors="replace")
    start = text.index("main_opts=(")
    # The dispatch case follows the array in the same main loop. Stop at the
    # closing of the main while loop, which is the first line that is just fi.
    tail = text[start:]
    end = tail.find("\nfi\n")
    return tail[: end if end > 0 else len(tail)]


def _defined_functions() -> set[str]:
    text = MANAGER.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{", text))
    names |= set(re.findall(r"(?m)^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*[({]", text))
    return names


class TestMenuStructure(unittest.TestCase):
    def test_shell_syntax(self):
        proc = _run(["-n", str(MANAGER)])
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_menu_has_the_expected_number_of_entries(self):
        options = _main_options()
        # 37 rows today; the guard catches a row that silently disappeared.
        self.assertGreaterEqual(len(options), 35, options)
        self.assertEqual(len({key for key, _ in options}), len(options),
                         "duplicate menu key")

    def test_every_menu_row_has_a_dispatch_branch(self):
        dispatch = _dispatch_text()
        whole = MANAGER.read_text(encoding="utf-8", errors="replace")
        # AI is opened by Shift+A from any menu, not by the numeric case.
        keyboard = {"AI": "launch_mp_harmony_from_menu"}
        missing = []
        for key, _label in _main_options():
            if key in keyboard:
                if keyboard[key] not in whole:
                    missing.append(key)
                continue
            # Keys are matched as a whole case token: 1), 1|..., ;1|..., etc.
            pattern = r"(?m)(?:^|[;|])\s*" + re.escape(key) + r"(?:\||\))"
            if not re.search(pattern, dispatch):
                missing.append(key)
        self.assertEqual(missing, [], "menu rows with no dispatch branch: " + ", ".join(missing))

    def test_every_dispatched_helper_is_defined(self):
        dispatch = _dispatch_text()
        defined = _defined_functions()
        referenced = set()
        for match in re.finditer(r"(?:^|[;|])\s*[0-9A-Za-z_*|\-]+\s*\)\s*([A-Za-z_][A-Za-z0-9_]*)",
                                 dispatch):
            name = match.group(1)
            if name.startswith(("manage_", "configure_", "launch_", "show_", "run_",
                                "play_", "press_", "inspect_", "generate_", "export_",
                                "rename_", "find_", "enter_", "mp_")):
                referenced.add(name)
        missing = sorted(name for name in referenced if name not in defined)
        self.assertEqual(missing, [], "menu helpers with no definition: " + ", ".join(missing))


class TestHeadlessMenuActions(unittest.TestCase):
    """These are the non-interactive forms of the menu's own actions."""

    def _assert_clean(self, proc, label: str):
        output = (proc.stdout or "") + (proc.stderr or "")
        lowered = output.lower()
        for marker in ("traceback (most recent call last)", "syntax error",
                       "command not found", "unbound variable", "nameerror",
                       "typeerror", "attributeerror"):
            self.assertNotIn(marker, lowered, f"{label}:\n{output}")
        self.assertEqual(proc.returncode, 0, f"{label} exited {proc.returncode}:\n{output}")

    def test_version(self):
        proc = _run([str(MANAGER), "--version"])
        self._assert_clean(proc, "--version")
        self.assertIn("MP Mix Archive Manager", proc.stdout)

    def test_help(self):
        proc = _run([str(MANAGER), "--help"])
        self._assert_clean(proc, "--help")
        self.assertIn("USAGE", (proc.stdout + proc.stderr).upper())

    def test_rescan_archive(self):
        proc = _run([str(MANAGER), "--rescan-archive"], timeout=90)
        self._assert_clean(proc, "--rescan-archive")
        self.assertIn("RESCAN_STATUS=", proc.stdout)

    def test_reporter_status(self):
        proc = _run([str(MANAGER), "--report-status"])
        self._assert_clean(proc, "--report-status")

    def test_beszel_status(self):
        proc = _run([str(MANAGER), "--beszel-status"])
        self._assert_clean(proc, "--beszel-status")

    def test_ollama_status(self):
        proc = _run([str(MANAGER), "--ollama-status"])
        self._assert_clean(proc, "--ollama-status")

    def test_app_selection_parser(self):
        proc = _run([str(ROOT / "scripts" / "mp_app_selection.py"), "--list"],
                    python=True)
        self._assert_clean(proc, "app selection --list")
        for app in ("manager", "gui", "liquid", "visualizer"):
            self.assertIn(app, proc.stdout)

    def test_visualizer_backend_resolver(self):
        proc = _run([str(ROOT / "scripts" / "visualizer_backend.py"), "--resolve"],
                    python=True)
        self._assert_clean(proc, "visualizer backend --resolve")
        self.assertIn(proc.stdout.strip(), ("opengl", "vulkan", "metal", "auto"))


if __name__ == "__main__":
    unittest.main()
