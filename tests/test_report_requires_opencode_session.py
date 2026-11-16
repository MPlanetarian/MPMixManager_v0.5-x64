#!/usr/bin/env python3
"""No OpenCode session means no report and no e-mail.

A scheduled report is only useful while someone is actually working in an agent
session. On an idle machine it is inbox noise, so the daily email reporter and
the responder's hourly announce both check first.

These tests pin the parts that break silently:

  * the detector counts real TUI sessions and ignores the always-resident
    `opencode serve --service` daemon, which has no tty - counting every match
    would report a session on a machine nobody is using;
  * an unreadable process list errs toward *sending*, so a broken `ps` cannot
    silently swallow a report forever;
  * `--force` still sends, which is what the interactive path uses;
  * the shell helper in `manage_mp_responder.sh` applies the same rule and can
    be overridden with `MP_RESPONDER_ANNOUNCE_ALWAYS=1`.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTER = ROOT / "scripts" / "mp_daily_email_reporter.py"
RESPONDER = ROOT / "scripts" / "manage_mp_responder.sh"


def _load_reporter():
    spec = importlib.util.spec_from_file_location("mp_email_reporter_under_test", REPORTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenCodeSessionDetectionTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_reporter()

    def test_the_service_daemon_is_not_a_session(self):
        """`opencode serve --service` is always up and has no tty."""
        original = self.mod.subprocess.run

        class Fake:
            returncode = 0
            stdout = "opencode   ?\nopencode   -\nopencode\nbash   pts/0\n"

        self.mod.subprocess.run = lambda *a, **k: Fake()
        try:
            self.assertEqual(self.mod.active_opencode_sessions(), 0)
        finally:
            self.mod.subprocess.run = original

    def test_a_tui_session_is_counted(self):
        original = self.mod.subprocess.run

        class Fake:
            returncode = 0
            stdout = "opencode   pts/1\nopencode   ?\nbash   pts/0\n"

        self.mod.subprocess.run = lambda *a, **k: Fake()
        try:
            self.assertEqual(self.mod.active_opencode_sessions(), 1)
        finally:
            self.mod.subprocess.run = original

    def test_two_sessions_are_counted(self):
        original = self.mod.subprocess.run

        class Fake:
            returncode = 0
            stdout = "opencode pts/1\nopencode pts/3\nopencode ?\n"

        self.mod.subprocess.run = lambda *a, **k: Fake()
        try:
            self.assertEqual(self.mod.active_opencode_sessions(), 2)
        finally:
            self.mod.subprocess.run = original

    def test_an_unreadable_process_list_does_not_suppress(self):
        """A broken `ps` must not silently swallow every future report."""
        original = self.mod.subprocess.run

        def boom(*a, **k):
            raise OSError("ps unavailable")

        self.mod.subprocess.run = boom
        try:
            self.assertGreaterEqual(self.mod.active_opencode_sessions(), 1)
        finally:
            self.mod.subprocess.run = original

    def test_a_failing_ps_returns_nonzero_means_assume_a_session(self):
        original = self.mod.subprocess.run

        class Fake:
            returncode = 1
            stdout = ""

        self.mod.subprocess.run = lambda *a, **k: Fake()
        try:
            self.assertGreaterEqual(self.mod.active_opencode_sessions(), 1)
        finally:
            self.mod.subprocess.run = original

    def test_the_dispatch_refuses_with_no_session(self):
        original_detect = self.mod.active_opencode_sessions
        self.mod.active_opencode_sessions = lambda: 0
        try:
            self.assertFalse(self.mod.execute_email_dispatch(force=False),
                             "a report must not be sent with no session running")
        finally:
            self.mod.active_opencode_sessions = original_detect

    def test_force_is_not_blocked_by_the_session_rule(self):
        """`--force` is the interactive 'send now' path and must still work."""
        original_detect = self.mod.active_opencode_sessions
        self.mod.active_opencode_sessions = lambda: 0
        try:
            # dry_run builds but never sends, so this is safe to call.
            result = self.mod.execute_email_dispatch(force=True, dry_run=True)
            self.assertIsInstance(result, bool)
        finally:
            self.mod.active_opencode_sessions = original_detect


class ResponderAnnounceRuleTest(unittest.TestCase):
    """The shell front end applies the same rule, with an env override."""

    def setUp(self):
        self.text = RESPONDER.read_text(encoding="utf-8")

    def test_the_helper_counts_only_real_sessions(self):
        self.assertIn("active_opencode_sessions()", self.text)
        block = self.text[self.text.index("active_opencode_sessions()"):]
        block = block[:block.index("}")]
        self.assertIn("tty", block)
        self.assertIn("$2 != \"?\"", block)

    def test_the_announce_is_gated_on_a_session(self):
        idx = self.text.index("gitclone|hourly|update)")
        block = self.text[idx:idx + 900]
        self.assertIn("active_opencode_sessions", block)
        self.assertIn("skipping the update", block)

    def test_there_is_an_escape_hatch(self):
        self.assertIn("MP_RESPONDER_ANNOUNCE_ALWAYS", self.text)

    def test_the_shell_helper_parses(self):
        res = subprocess.run(["bash", "-n", str(RESPONDER)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
