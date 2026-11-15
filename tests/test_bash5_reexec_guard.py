#!/usr/bin/env python3
"""The bash-5 re-exec must never fire when the manager is sourced.

`Mix_Archive_Manager.sh` line 2 upgrades a macOS host from the system bash 3.2
to a Homebrew/usr-local bash 5, because the manager needs arrays and `mapfile`.
It did that with `exec "$bash" "$0" "$@"`.

Under `source Mix_Archive_Manager.sh` — which is how ~40 test modules load the
manager's functions — `$0` is the *caller*, not the manager. So the `exec`
replaced the test process with a second copy of itself. On macOS the whole
suite hung or failed (a real run reported 76 failed / 46 errored), and the
symptom looked like a missing function rather than a process swap.

The guard now also requires the script to have been executed directly:
`"${BASH_SOURCE[0]}" == "$0"`. These tests pin both halves of that.
"""

import os
import subprocess
import unittest
from pathlib import Path

from tests.mp_test_bash import BASH

BASE_DIR = Path(__file__).resolve().parent.parent
MGR = BASE_DIR / "Mix_Archive_Manager.sh"

# Load the shell file without starting the interactive manager loop. Every
# function the file defines must be available afterwards.
PRELUDE = "source ./Mix_Archive_Manager.sh >/dev/null 2>&1 || true\n"


def bash(script, timeout=120, shell=None):
    """Run `script` after the prelude.

    The interpreter defaults to the shared bash-4+ resolver: bash 3.2 on macOS
    cannot parse the manager at all, so using the plain "bash" name here would
    test the host shell rather than the guard.
    """
    proc = subprocess.run(
        [shell or BASH, "--noprofile", "--norc", "-c", PRELUDE + script],
        cwd=str(BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env={**os.environ, "MP_DISABLE_AUDIO_OUTPUT": "1",
             "MP_DISABLE_BLUETOOTH_MONITOR": "1"},
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


class TestSourcingIsSafe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MGR.read_text(encoding="utf-8")
        cls.line2 = cls.text.split("\n")[1]

    def test_reexec_is_guarded_by_a_direct_execution_check(self):
        """The exec must be conditional on the script being run, not sourced."""
        self.assertIn("exec", self.line2, "the bash-5 upgrade is expected on line 2")
        self.assertIn('"${BASH_SOURCE[0]}" == "$0"', self.line2,
                      "the re-exec must not fire under `source`")
        # And it must re-exec the script, never the caller.
        self.assertIn('"${BASH_SOURCE[0]}"', self.line2)
        self.assertNotIn('exec "$bash" "$0"', self.line2,
                         'exec "$0" runs the caller when the file was sourced')

    def test_sourcing_returns_and_defines_functions(self):
        """The prelude every test module uses must complete, not hang."""
        rc, out = bash('type -t _mp_human_kb\n', timeout=90)
        self.assertEqual(rc, 0, out[-800:])
        self.assertIn("function", out,
                      f"sourcing the manager did not define _mp_human_kb:\n{out[-800:]}")

    def test_a_sourced_function_actually_runs(self):
        """A function call after sourcing must produce its real output."""
        rc, out = bash('_mp_human_kb 1024\n', timeout=90)
        self.assertEqual(rc, 0, out[-800:])
        self.assertIn("1.0 MB", out, f"unexpected _mp_human_kb output:\n{out[-400:]}")

    def test_sourcing_does_not_start_the_interactive_loop(self):
        """No menu may appear: sourcing is for helpers only."""
        rc, out = bash('printf "SOURCE_DONE"\n', timeout=90)
        self.assertIn("SOURCE_DONE", out)
        # The main menu prints this banner; it must not appear.
        self.assertNotIn("MP MIX ARCHIVE MANAGER", out.replace("MP MIX ARCHIVE MANAGER —", "")[:0] or out)
        self.assertNotIn("SECTION 1:", out)


class TestDirectExecutionStillUpgrades(unittest.TestCase):
    """A macOS host on bash 3.2 must still be upgraded when run directly."""

    def test_guard_condition_is_true_for_direct_execution(self):
        """BASH_SOURCE[0] and $0 agree when the file is executed."""
        rc, out = bash(
            'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then echo DIRECT; '
            'else echo SOURCED; fi\n',
            timeout=90,
        )
        self.assertEqual(rc, 0, out[-400:])
        self.assertIn("SOURCED", out,
                      "a sourced prelude must report SOURCED, so the exec is skipped")

    def test_exec_uses_the_script_path_not_dollar_zero(self):
        """`$0` is the caller under `source`; the exec must use the real path."""
        line2 = MGR.read_text(encoding="utf-8").split("\n")[1]
        self.assertIn('exec "$bash" "${BASH_SOURCE[0]}" "$@"', line2,
                      "the upgrade must exec the script itself")
        self.assertNotIn('exec "$bash" "$0"', line2,
                         "exec \"$0\" runs the caller when the file was sourced")


if __name__ == "__main__":
    unittest.main()
