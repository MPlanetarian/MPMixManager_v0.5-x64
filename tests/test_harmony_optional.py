"""MP Harmony must be invisible when it is not installed.

MP Harmony Agent is an optional, separately installed project. If it is absent
the menu system must keep working exactly as before: no cleared screen, no
terminal mode change, no prompting to start Ollama, no model download, and no
dead key hints. These tests pin that contract for every entry point.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "Mix_Archive_Manager.sh"
LAUNCHER = ROOT / "scripts" / "launch_mp_harmony.sh"

MISSING_DIR = ROOT / "no-such-harmony-dir-for-this-test"


def _manager_text():
    return MANAGER.read_text(encoding="utf-8", errors="replace")


def _function_body(text, name):
    """Return the source of a top-level shell function."""
    start = text.index(name + "() {")
    depth = 0
    i = text.index("{", start)
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise AssertionError(f"unterminated function {name}")


def _bash():
    """A real bash, not the Windows WSL stub that shadows `bash` on PATH.

    On a Linux host or under Git Bash, `bash` is correct. On plain Windows the
    first `bash.exe` on PATH can be the WSL launcher, which refuses to run when
    WSL is not installed, so the Git Bash copy is preferred when present.
    """
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if os.path.isfile(candidate):
            return candidate
    return "bash"


BASH = _bash()


def _run_script(script, env_extra=None, timeout=60):
    """Write a script to a temp file and run it.

    A file avoids the Windows command-line length limit, which a manager-sized
    function library would otherwise hit.
    """
    env = os.environ.copy()
    env.update(env_extra or {})
    handle, path = tempfile.mkstemp(prefix="mp-hm-", suffix=".sh")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(script)
        return subprocess.run(
            [BASH, "--noprofile", "--norc", path],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _run_manager_snippet(snippet, env_extra=None):
    """Source the manager's function library, then run a snippet."""
    text = _manager_text()
    # The guard that separates the function library from the launcher.
    marker = 'if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then'
    head = text[:text.index(marker)]
    env_extra = dict(env_extra or {})
    env_extra["HOME"] = tempfile.mkdtemp(prefix="mp-hm-home-")
    script = head + "\n" + snippet + "\n"
    return _run_script(script, env_extra)


class TestPresenceHelpers(unittest.TestCase):
    def test_helpers_exist_at_top_level(self):
        text = _manager_text()
        for name in ("mp_harmony_agent_dir", "mp_harmony_agent_installed",
                     "mp_harmony_agent_notice", "mp_harmony_menu_gate"):
            self.assertRegex(text, r"(?m)^" + name + r"\(\) \{", name)

    def test_missing_agent_reports_not_installed(self):
        snippet = (
            'mp_harmony_agent_installed && echo INSTALLED || echo MISSING\n'
            'mp_harmony_agent_dir 2>/dev/null || echo NO_DIR\n'
        )
        result = _run_manager_snippet(snippet, {"HARMONY_DIR": str(MISSING_DIR)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MISSING", result.stdout)
        self.assertIn("NO_DIR", result.stdout)

    def test_notice_names_the_installer_and_stays_short(self):
        snippet = 'mp_harmony_agent_notice\n'
        result = _run_manager_snippet(snippet, {"HARMONY_DIR": str(MISSING_DIR)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not installed", result.stdout)
        self.assertIn("install_harmony_agent.sh install", result.stdout)
        self.assertIn("unaffected", result.stdout)

    def test_gate_returns_failure_without_a_tty(self):
        # No tty in the test harness: the gate must not block on a key press.
        snippet = 'mp_harmony_menu_gate; echo "gate=$?"\n'
        result = _run_manager_snippet(snippet, {"HARMONY_DIR": str(MISSING_DIR)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gate=1", result.stdout)

    def test_installed_agent_passes_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "MP_Harmony_Agent.py").write_text("# stub\n", encoding="utf-8")
            snippet = 'mp_harmony_agent_installed && echo OK; mp_harmony_menu_gate && echo GATE_OK\n'
            result = _run_manager_snippet(snippet, {"HARMONY_DIR": tmp})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("OK", result.stdout)
            self.assertIn("GATE_OK", result.stdout)


class TestMenuDoesNotDependOnHarmony(unittest.TestCase):
    def test_shift_a_checks_presence_before_clearing_the_screen(self):
        text = _manager_text()
        handler = text[text.index("CHAR:A)"):]
        handler = handler[:handler.index(";;")]
        self.assertIn("mp_harmony_agent_installed", handler)
        # The presence check must come before the screen teardown.
        self.assertLess(
            handler.index("mp_harmony_agent_installed"),
            handler.index("cleanup_submenu_screen"),
        )

    def test_launcher_gates_the_agent_before_stty(self):
        body = _function_body(_manager_text(), "launch_mp_harmony_from_menu")
        self.assertIn("mp_harmony_menu_gate", body)
        self.assertLess(body.index("mp_harmony_menu_gate"), body.index("stty sane"))

    def test_harmony_menu_requires_the_agent(self):
        body = _function_body(_manager_text(), "manage_harmony_agent")
        self.assertIn("mp_harmony_menu_gate", body)
        # status and install must still be reachable to guide a new user.
        self.assertIn("status|install|setup|path|deps|unit", body)

    def test_tracklist_narration_requires_the_agent(self):
        body = _function_body(_manager_text(), "narrate_tracklist_with_harmony")
        self.assertIn("mp_harmony_menu_gate", body)

    def test_shift_a_hint_is_conditional(self):
        text = _manager_text()
        self.assertIn('_harmony_hint=", [Shift+A] MP Harmony"', text)
        self.assertIn("[B] Bash${_harmony_hint}", text)
        # The hard-coded hint that advertised a missing feature is gone.
        self.assertNotIn("[B] Bash, [Shift+A] MP Harmony, [C] Theme", text)

    def test_bash_prompt_only_offers_shift_a_when_installed(self):
        text = _manager_text()
        self.assertIn("MP_MANAGER_HARMONY_AVAILABLE", text)
        self.assertIn("MP_MANAGER_HARMONY_HINT", text)
        # A at an empty prompt must not exit the shell when nothing can open.
        body = _function_body(text, "enter_manager_bash_terminal")
        self.assertIn('${MP_MANAGER_HARMONY_AVAILABLE:-0}" = "1"', body)


class TestLauncherWhenAgentMissing(unittest.TestCase):
    def _run_launcher(self, env_extra):
        env = os.environ.copy()
        env.update(env_extra)
        env["MP_HARMONY_NO_PAUSE"] = "1"
        # An empty HOME, so a real ~/OpenAI/MP_Harmony_Agent on the machine
        # running the tests cannot satisfy the presence check.
        env["HOME"] = tempfile.mkdtemp(prefix="mp-hm-launch-home-")
        env["HARMONY_DIR"] = str(MISSING_DIR)
        return subprocess.run(
            [BASH, str(LAUNCHER)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_missing_agent_does_not_start_ollama_or_offer_a_model(self):
        result = self._run_launcher({"MP_HARMONY_FORCE_DOWN": "1"})
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 6, out)
        self.assertIn("not installed", out)
        self.assertIn("install_harmony_agent.sh install", out)
        # None of the Ollama/model prompt path may run for a missing agent.
        self.assertNotIn("Start Ollama now?", out)
        self.assertNotIn("You would be installing", out)
        self.assertNotIn("Ollama is not running, so MP Harmony was not started", out)

    def test_missing_agent_message_points_at_the_expected_folder(self):
        result = self._run_launcher({})
        self.assertIn(str(MISSING_DIR), result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
