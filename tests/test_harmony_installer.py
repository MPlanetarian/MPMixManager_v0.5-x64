"""The optional MP Harmony Agent install path.

MP Harmony Agent is a separate project. These tests pin the contract that lets
MP Mix Manager offer it without ever depending on it:

  * the agent source is not shipped inside the manager tree,
  * the installer offers it with a Yes default and honours an explicit No,
  * the launcher and manager scripts resolve ~/OpenAI/MP_Harmony_Agent and
    never the removed bundle,
  * a missing agent produces an actionable message rather than a bare error.
"""

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
AGENT_INSTALLER = ROOT / "scripts" / "install_harmony_agent.sh"
LAUNCHER = ROOT / "scripts" / "launch_mp_harmony.sh"
MANAGER = ROOT / "scripts" / "manage_harmony_agent.sh"
BUNDLE = ROOT / "MP_Harmony_Agent"


def _env_extra(**values):
    env = os.environ.copy()
    env.update(values)
    return env


class TestBundleIsGone(unittest.TestCase):
    def test_agent_is_not_bundled_in_the_manager_tree(self):
        self.assertFalse(
            BUNDLE.exists(),
            "MP_Harmony_Agent/ must not live inside MP Mix Manager; "
            "the agent is a separate project installed by "
            "scripts/install_harmony_agent.sh",
        )

    def test_no_script_runs_the_bundled_copy(self):
        for path in (LAUNCHER, MANAGER):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "MP_Harmony_Agent/MP_Harmony_Agent.py",
                text,
                f"{path.name} still resolves the removed bundle",
            )

    def test_scripts_point_at_the_separate_checkout(self):
        for path in (LAUNCHER, MANAGER):
            text = path.read_text(encoding="utf-8")
            self.assertIn("OpenAI/MP_Harmony_Agent", text, path.name)


class TestInstallerScript(unittest.TestCase):
    def test_shell_parses(self):
        result = subprocess.run(["bash", "-n", str(AGENT_INSTALLER)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_path_command_reports_the_default_location(self):
        result = subprocess.run(
            ["bash", str(AGENT_INSTALLER), "path"],
            capture_output=True,
            text=True,
            env=_env_extra(HARMONY_DIR=""),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OpenAI/MP_Harmony_Agent", result.stdout.strip())

    def test_status_reports_a_missing_agent_without_crashing(self):
        missing = ROOT / "no-such-harmony-dir-for-this-test"
        result = subprocess.run(
            ["bash", str(AGENT_INSTALLER), "status"],
            capture_output=True,
            text=True,
            env=_env_extra(HARMONY_DIR=str(missing)),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stdout)

    def test_unknown_command_is_a_usage_error(self):
        result = subprocess.run(
            ["bash", str(AGENT_INSTALLER), "definitely-not-a-command"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)


class TestInstallerIntegration(unittest.TestCase):
    def test_install_step_defaults_to_yes_and_accepts_no(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("MP Harmony Agent (Optional)", text)
        self.assertIn("Install the MP Harmony Agent? [Y/n]", text)
        # The default branch must be Yes: only an explicit n/N/no declines.
        self.assertIn("n|N|no|NO) MP_HARMONY_WANT=0", text)
        self.assertIn("MP_INSTALL_HARMONY", text)

    def test_opt_out_flags_are_documented_and_parsed(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("--no-harmony", text)
        self.assertIn("--install-harmony", text)
        self.assertIn('MP_INSTALL_HARMONY="never"', text)
        self.assertIn('MP_INSTALL_HARMONY="always"', text)

    def test_installer_shell_parses(self):
        result = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestMissingAgentMessage(unittest.TestCase):
    def test_manager_explains_how_to_install(self):
        result = subprocess.run(
            ["bash", str(MANAGER), "status"],
            capture_output=True,
            text=True,
            env=_env_extra(HARMONY_DIR=str(ROOT / "no-such-dir-for-this-test")),
        )
        combined = result.stdout + result.stderr
        # Status on a missing directory must not traceback or hang.
        self.assertNotIn("Traceback", combined)

    def test_launcher_hint_names_the_installer(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("install_harmony_agent.sh install", text)

    def test_manager_hint_names_the_installer(self):
        text = MANAGER.read_text(encoding="utf-8")
        self.assertIn("install_harmony_agent.sh install", text)


class TestSystemdUnit(unittest.TestCase):
    def test_unit_command_targets_the_checkout(self):
        text = AGENT_INSTALLER.read_text(encoding="utf-8")
        self.assertIn("WorkingDirectory=$TARGET", text)
        self.assertIn("ExecStart=$py $TARGET/$ENTRY", text)


if __name__ == "__main__":
    unittest.main()
