#!/usr/bin/env python3
"""The Web Companion service survives reinstall, rename and test installs.

The macOS companion lives in /Applications, which every install of MP Mix
Manager shares. Two defects made that dangerous, and both were found by the
cross-platform audit:

  1. The bundle baked in the tree it was built from and nothing compared it
     with the current install, so `start` from a new tree silently served the
     old one. Running the audit on a test install graded the production copy.
  2. The launchd agent's WorkingDirectory did the same, so even a rebuilt
     bundle ran with the wrong cwd.

These tests pin the identification logic without needing macOS.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANION = ROOT / "scripts" / "mix_web_companion.sh"
AGENT = Path(__file__).resolve().parent / "MP_TESTER_AGENT.py"
_AGENT_CANDIDATES = [
    ROOT.parent / "Documents" / "BASH_SCRIPTS" / "MP_TESTER_AGENT.py",
    Path.home() / "Documents" / "BASH_SCRIPTS" / "MP_TESTER_AGENT.py",
]


def agent_source() -> str | None:
    for candidate in _AGENT_CANDIDATES:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


class TestCompanionBundleIdentity(unittest.TestCase):
    def setUp(self):
        self.text = COMPANION.read_text(encoding="utf-8")

    def test_shell_script_parses(self):
        result = subprocess.run(["bash", "-n", str(COMPANION)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bundle_records_the_tree_it_was_built_from(self):
        self.assertIn("install_dir", self.text)
        self.assertIn("Contents/Resources/install_dir", self.text)

    def test_start_rebuilds_a_bundle_from_another_tree(self):
        """A shared /Applications bundle must not outlive its install tree."""
        self.assertIn("bundle_matches_app_dir", self.text)
        start = self.text.split("start_service() {", 1)[1].split("\nstop_service", 1)[0]
        self.assertIn("bundle_matches_app_dir", start)
        self.assertIn("write_launchd_plist", start)

    def test_launchd_working_directory_follows_the_install(self):
        self.assertIn("<key>WorkingDirectory</key>", self.text)
        match = re.search(r"<key>WorkingDirectory</key>\s*\n\s*<string>([^<]*)</string>", self.text)
        self.assertIsNotNone(match)
        value = match.group(1)
        self.assertIn("$APP_DIR", value, "WorkingDirectory must not be a fixed path")

    def test_no_hard_coded_version_path_anywhere(self):
        for needle in ("MPMixManager_v0.4-x64", "MPMixManager_v0.5-x64"):
            self.assertNotIn(needle, self.text, f"hard-coded install path: {needle}")

    def test_bundle_identity_helper_is_used_by_status_too(self):
        """`status` should say when the running bundle belongs to another tree."""
        status = self.text.split("show_status() {", 1)[1].split("\ncase ", 1)[0]
        self.assertTrue(
            "bundle_matches_app_dir" in status or "install_dir" in status,
            "status cannot tell the user the bundle points at a stale tree",
        )


class TestTestAgentRecognisesTheBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = agent_source()
        if source is None:
            raise unittest.SkipTest("MP_TESTER_AGENT.py not available")
        cls.source = source

    def test_companion_match_includes_the_bundle_binary(self):
        self.assertIn("mp-web-companion", self.source)
        self.assertIn("mix_web_companion.py", self.source)

    def test_a_running_service_is_stopped_before_measuring_a_launch(self):
        """Otherwise `start` is a no-op and the audit grades the old process."""
        self.assertIn("were already running; stopped them first", self.source)
        self.assertIn("close_service(component, root, env)", self.source)


if __name__ == "__main__":
    unittest.main()


class TestTrayStartupDoesNotAbort(unittest.TestCase):
    """The tray must not crash when it autostarts before the compositor.

    On Fedora the tray is autostarted at login. Qt treats a missing display as
    fatal (qAbort), so the process died with SIGABRT on every boot and systemd
    kept restarting it. The fix waits for the socket and exits 0 when there is
    still no desktop, which is a clean skip rather than a crash loop.
    """

    TRAY = ROOT / "scripts" / "mp_system_tray.py"

    def test_source_has_the_readiness_gate(self):
        text = self.TRAY.read_text(encoding="utf-8")
        self.assertIn("def display_server_ready", text)
        gate = text.index("if not display_server_ready():")
        qt = text.index("QApplication([])")
        self.assertLess(gate, qt, "the display check must run before QApplication")

    def test_exits_zero_without_a_display(self):
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": tmp,
                "XDG_RUNTIME_DIR": tmp,
            }
            result = subprocess.run(
                [sys.executable, str(self.TRAY), "--stay"],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertNotIn("Aborted", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_real_desktop_still_starts_the_tray(self):
        """Guards against the readiness gate locking out a working session."""
        text = self.TRAY.read_text(encoding="utf-8")
        body = text.split("def display_server_ready", 1)[1].split("def run_tray", 1)[0]
        self.assertIn("WAYLAND_DISPLAY", body)
        self.assertIn("DISPLAY", body)
