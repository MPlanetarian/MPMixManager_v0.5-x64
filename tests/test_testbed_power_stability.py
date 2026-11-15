#!/usr/bin/env python3
"""The BazziteMac test bed must stay awake while tests are running.

The laptop used to power itself off in the middle of a run. The journals from
the affected boots show what it really was:

    19:58:32  systemd-logind: suspend requested from client PID 4906
              ('org_kde_powerde') (unit user@1000.service)
    23:26:18  kernel: Freezing user space processes

There is no thermal event, no watchdog and no power-button record: KDE
PowerDevil asked logind to idle-suspend, logind froze user space, and on this
2010 MacBook Pro the resume never completes, so the machine looks dead until it
is power-cycled. `scripts/mp_testbed_power_stability.sh` makes that impossible.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mp_testbed_power_stability.sh"


def _bash() -> str:
    return os.environ.get("BASH", "bash")


class TestTestbedPowerStability(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file(), SCRIPT)
        self.assertTrue(os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable")

    def test_shell_syntax_is_valid(self):
        res = subprocess.run([_bash(), "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_it_blocks_every_suspend_trigger(self):
        """Lid, power key, suspend key, hibernate key and idle all ignored."""
        for setting in (
            "HandleLidSwitch=ignore",
            "HandleLidSwitchExternalPower=ignore",
            "HandlePowerKey=ignore",
            "HandleSuspendKey=ignore",
            "IdleAction=ignore",
        ):
            self.assertIn(setting, self.text)

    def test_it_disables_sleep_in_logind_itself(self):
        self.assertIn("AllowSuspend=no", self.text)
        self.assertIn("AllowHibernation=no", self.text)

    def test_it_masks_the_sleep_targets(self):
        self.assertIn("systemctl mask", self.text)
        for target in ("sleep.target", "suspend.target", "hibernate.target"):
            self.assertIn(target, self.text)

    def test_it_tells_powerdevil_not_to_suspend(self):
        """The actual caller in the journal is org_kde_powerde."""
        self.assertIn("powermanagementprofilesrc", self.text)
        self.assertIn("suspendType", self.text)

    def test_everything_is_reversible(self):
        self.assertIn("--revert", self.text)
        revert = self.text.split("if [ \"$REVERT\" -eq 1 ]; then", 1)[1]
        self.assertIn("/etc/systemd/logind.conf.d/90-mp-testbed-no-suspend.conf", revert)
        self.assertIn("unmask", revert)


if __name__ == "__main__":
    unittest.main()
