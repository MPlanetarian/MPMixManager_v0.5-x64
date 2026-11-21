#!/usr/bin/env python3
"""A burst of arrow presses must step the menu once per press.

The menu used to drain the input buffer after every arrow to hide a Windows
console echo. That also swallowed the presses a user makes while the first
repaint is still running: five quick Downs moved the highlight only once.
"""

import subprocess
import unittest
from pathlib import Path

from tests.mp_test_bash import BASH

ROOT = Path(__file__).resolve().parent.parent

HARNESS = r"""
source ./Mix_Archive_Manager.sh </dev/null >/dev/null 2>&1
_resize_pending() { return 1; }
_gamepad_take() { return 1; }
_nav_pending=""
_NAV_WIN_CON=0
_NAV_ARROW_AT=""
_defer_buffered_vertical_arrows
for _i in 1 2 3 4 5; do
    key=""
    read_nav_key key
    printf '%s\n' "$key"
done
"""


class MenuArrowBurstTest(unittest.TestCase):
    def test_five_quick_presses_produce_five_down_keys(self):
        result = subprocess.run(
            [BASH, "--noprofile", "--norc", "-c", HARNESS],
            cwd=str(ROOT),
            input=b"\x1b[B" * 5,
            capture_output=True,
            timeout=60,
        )
        output = result.stdout.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            [line for line in output.splitlines() if line.strip()],
            ["DOWN"] * 5,
        )


if __name__ == "__main__":
    unittest.main()
