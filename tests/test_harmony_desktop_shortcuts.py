"""Desktop shortcuts for MP Harmony and MP Reporter.

These are separate programs, so install_desktop_shortcuts.py does not own
their entries. This suite pins the contract of the script that does:

  * every shortcut is a valid .desktop file,
  * each one actually opens a terminal, so a click is never silent,
  * each Exec target exists and the command it runs is real,
  * removal and listing work, and re-running is idempotent.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install_harmony_desktop_shortcuts.sh"

EXPECTED = [
    "MP Harmony Agent (Chat)",
    "MP Harmony - Start Server",
    "MP Harmony - Stop Server",
    "MP Harmony - Status",
    "MP Harmony - Web HUD",
    "MP Reporter Suite",
    "MP Reporter - Run Daily Now",
    "MP Reporter - Status",
    "MP Reporter - View Latest",
]


def _run(args, env_extra=None, timeout=60):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


class ShortcutTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-shortcuts-"))
        self.desktop = self.tmp / "Desktop"
        self.apps = self.tmp / "applications"
        self.desktop.mkdir()
        self.env = {
            "MP_DESKTOP_DIR": str(self.desktop),
            "MP_APPLICATIONS_DIR": str(self.apps),
            "HOME": os.environ.get("HOME", str(Path.home())),
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self):
        return _run(["bash", str(INSTALLER)], env_extra=self.env)

    def entry(self, name: str) -> Path:
        return self.desktop / f"{name}.desktop"


class TestInstall(ShortcutTestBase):
    def test_installer_parses(self):
        result = _run(["bash", "-n", str(INSTALLER)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_shortcuts_are_created(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in EXPECTED:
            self.assertTrue(self.entry(name).is_file(), f"{name} was not created")

    def test_every_entry_is_a_valid_desktop_file(self):
        self.install()
        validator = shutil.which("desktop-file-validate")
        if not validator:
            self.skipTest("desktop-file-validate is not installed")
        for name in EXPECTED:
            result = _run([validator, str(self.entry(name))])
            self.assertEqual(result.returncode, 0,
                             f"{name}: {result.stdout}{result.stderr}")

    def test_every_entry_launches_a_terminal(self):
        """Terminal=false with a bare sh -c would run with no window at all."""
        self.install()
        for name in EXPECTED:
            if "Web HUD" in name:
                continue  # opens a browser, not a terminal
            text = self.entry(name).read_text(encoding="utf-8")
            self.assertIn("konsole", text, f"{name} does not open a terminal")
            self.assertIn("Terminal=false", text)

    def test_exec_targets_exist_and_are_scripts(self):
        self.install()
        for name in EXPECTED:
            text = self.entry(name).read_text(encoding="utf-8")
            if "Web HUD" in name:
                self.assertIn("http://127.0.0.1:11435", text)
                continue
            # Every manager script named in Exec must exist in this tree.
            # Do not pin the directory's name: a test bed deploys the same
            # tree under its own path (MP_AUTO_TESTER_TREE, a worktree, ...).
            self.assertIn(f"{ROOT.name}/scripts/", text, name)
            for token in text.split():
                if token.endswith(".sh'"):
                    script = Path(token.strip("'\""))
                    self.assertTrue(script.is_file(), f"{name}: missing {script}")

    def test_reinstall_is_idempotent(self):
        self.install()
        before = {n: self.entry(n).read_text(encoding="utf-8") for n in EXPECTED}
        self.install()
        for name in EXPECTED:
            self.assertEqual(before[name], self.entry(name).read_text(encoding="utf-8"),
                             f"{name} changed on a second run")

    def test_entries_are_mirrored_into_the_application_menu(self):
        self.install()
        for name in EXPECTED:
            self.assertTrue((self.apps / f"{name}.desktop").is_file(), name)


class TestRemoveAndList(ShortcutTestBase):
    def test_remove_deletes_every_entry(self):
        self.install()
        result = _run(["bash", str(INSTALLER), "--remove"], env_extra=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in EXPECTED:
            self.assertFalse(self.entry(name).exists(), f"{name} survived removal")

    def test_list_reports_presence(self):
        result = _run(["bash", str(INSTALLER), "--list"], env_extra=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("absent", result.stdout)
        self.install()
        result = _run(["bash", str(INSTALLER), "--list"], env_extra=self.env)
        self.assertIn("present", result.stdout)

    def test_unknown_action_is_a_usage_error(self):
        result = _run(["bash", str(INSTALLER), "definitely-not-a-command"],
                      env_extra=self.env)
        self.assertEqual(result.returncode, 2)


class TestReporterCommandsAreReal(ShortcutTestBase):
    """Each shortcut runs a command that the target script actually supports."""

    def test_daily_report_uses_a_real_task_id(self):
        self.install()
        text = self.entry("MP Reporter - Run Daily Now").read_text(encoding="utf-8")
        self.assertIn("DAILY_REPORT_TASK_001", text)
        # The old shortcut passed "daily", which is not a task file.
        self.assertNotIn("run daily", text)

    def test_reporter_status_uses_the_scheduled_reporter(self):
        self.install()
        text = self.entry("MP Reporter - Status").read_text(encoding="utf-8")
        self.assertIn("mp_scheduled_reporter.sh", text)
        self.assertIn("--status", text)

    def test_harmony_shortcuts_use_the_manager(self):
        self.install()
        for name, verb in (
            ("MP Harmony Agent (Chat)", "chat"),
            ("MP Harmony - Start Server", "start"),
            ("MP Harmony - Stop Server", "stop"),
            ("MP Harmony - Status", "status"),
        ):
            text = self.entry(name).read_text(encoding="utf-8")
            self.assertIn("manage_harmony_agent.sh", text, name)
            self.assertIn(f" {verb}", text, name)


class TestInstallerHook(unittest.TestCase):
    def test_manager_installer_calls_the_shortcut_script(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("install_harmony_desktop_shortcuts.sh", text)

    def test_shortcuts_are_gated_on_the_optional_agent(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        marker = text.index("install_harmony_desktop_shortcuts.sh")
        window = text[max(0, marker - 400):marker]
        self.assertIn("MP_HARMONY_WANT", window,
                      "shortcuts must only be installed when the agent was accepted")


if __name__ == "__main__":
    unittest.main()
