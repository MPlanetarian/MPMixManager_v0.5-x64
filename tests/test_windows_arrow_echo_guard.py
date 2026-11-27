#!/usr/bin/env python3
"""The Windows arrow-echo guard has to work, not just exist.

conhost echoes a lone CR (or an A/B) right after an Up/Down arrow. The menu
swallows that byte with ``_nav_just_arrowed`` so it is not read as ENTER,
which would activate whichever row is highlighted.

Three bugs kept that from working on Windows 10:

* ``(( 10#now - 10#at ... ))`` read the literal digits "now"/"at" instead of
  the variables, so every arrow printed
  ``((: 10#now: value too great for base`` over the menu frame and the guard
  returned "not an arrow";
* ``read_nav_key`` stamped ``_NAV_ARROW_AT=1`` after parsing an arrow, so even
  correct arithmetic could not see a timestamp;
* the blocking Windows read ignored ``_nav_pending``, so the extra presses a
  fast double-tap queued were thrown away.

The result was a menu that could not be navigated: an arrow painted an error
and then its echo CR activated a row.
"""

import subprocess
import unittest
from pathlib import Path

from tests.mp_test_bash import BASH

ROOT = Path(__file__).resolve().parent.parent
MGR = ROOT / "Mix_Archive_Manager.sh"

PREAMBLE = r"""
source ./Mix_Archive_Manager.sh </dev/null >/dev/null 2>&1
_resize_pending() { return 1; }
_gamepad_take() { return 1; }
_NAV_WIN_CON=1
"""


def _bash(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", PREAMBLE + body],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestArrowEchoGuard(unittest.TestCase):
    def test_guard_reads_the_variables_not_their_names(self):
        """A live timestamp must fire the guard, with no stderr noise."""
        res = _bash(
            """
            _NAV_ARROW_AT="${EPOCHREALTIME:-0}"
            if _nav_just_arrowed; then echo "GUARD=1"; else echo "GUARD=0"; fi
            """
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "GUARD=1")
        self.assertEqual(res.stderr.strip(), "",
                         f"the guard must not print: {res.stderr!r}")

    def test_guard_never_writes_to_stderr(self):
        """"value too great for base" used to paint over every menu frame."""
        res = _bash(
            """
            : > /tmp/mp_guard_err.txt
            for _i in 1 2 3 4 5; do
                _NAV_ARROW_AT="${EPOCHREALTIME:-0}"
                _nav_just_arrowed 2>>/tmp/mp_guard_err.txt || true
            done
            echo "BYTES=$(wc -c < /tmp/mp_guard_err.txt | tr -d ' ')"
            """
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("BYTES=0", res.stdout)

    def test_guard_expires_so_a_real_key_still_arrives(self):
        """Only the echo inside the window is swallowed; real keys are not."""
        res = _bash(
            """
            _NAV_ARROW_AT="1"                      # far in the past
            if _nav_just_arrowed; then echo "FIRED"; else echo "EXPIRED"; fi
            """
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "EXPIRED")

    def test_arrow_parser_records_a_timestamp(self):
        """_NAV_ARROW_AT must hold a clock value, never the literal "1"."""
        res = _bash(
            """
            _nav_pending=$'\\033[B'
            _NAV_ARROW_AT=""
            key=""
            read_nav_key key
            printf 'KEY=%s\\n' "$key"
            case "$_NAV_ARROW_AT" in
                1|"") echo "STAMP=BAD" ;;
                *)    echo "STAMP=OK" ;;
            esac
            """
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("KEY=DOWN", res.stdout)
        self.assertIn("STAMP=OK", res.stdout)

    def test_pending_bytes_are_consumed_on_the_windows_path(self):
        """A queued press must survive the blocking Windows read."""
        res = _bash(
            """
            _nav_pending=$'\\033[B\\033[A'
            _NAV_ARROW_AT=""
            for _i in 1 2; do
                key=""
                read_nav_key key
                printf '%s\\n' "$key"
            done
            """
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            [line for line in res.stdout.splitlines() if line.strip()],
            ["DOWN", "UP"],
        )

    def test_echo_cr_after_an_arrow_is_a_tick_not_enter(self):
        """The exact failure: an echoed CR activated a row."""
        res = _bash(
            """
            _nav_pending=$'\\r'
            _NAV_ARROW_AT="${EPOCHREALTIME:-0}"
            key=""
            read_nav_key key
            printf 'KEY=%s\\n' "$key"
            """
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("KEY=TICK", res.stdout)
        self.assertNotIn("KEY=ENTER", res.stdout)


class TestNoRegressionInSource(unittest.TestCase):
    def test_no_bare_variable_ten_hash_arithmetic(self):
        """``10#name`` is a literal, not an expansion; it must not come back.

        Comment lines are skipped: the explanatory comment above the guard
        quotes the broken form on purpose.
        """
        text = MGR.read_text(encoding="utf-8")
        code = [
            line for line in text.splitlines()
            if not line.lstrip().startswith("#")
        ]
        joined = "\n".join(code)
        self.assertNotIn("10#now", joined)
        self.assertNotIn("10#at", joined)

    def test_arrow_parser_never_stamps_a_constant(self):
        text = MGR.read_text(encoding="utf-8")
        self.assertNotIn("_NAV_ARROW_AT=1", text)


if __name__ == "__main__":
    unittest.main()
