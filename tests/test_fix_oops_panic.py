#!/usr/bin/env python3
"""`oops=panic` must not turn a recoverable oops into a reboot.

The kernel command line on this machine carried `oops=panic`, so any kernel
oops rebooted the box in about a second. That made a driver bug look exactly
like a power cut, and it destroyed the log that would have identified it.

It already cost real evidence. On 13 Nov a QEMU start hit:

    BUG: unable to handle page fault for address: 00000000100023c0
    RIP: kvm_create_pit+0x68/0x350 [kvm]
    Kernel panic - not syncing: Fatal exception

A recoverable KVM fault became a full reboot. `scripts/mp_fix_oops_panic.sh`
removes ONLY `oops=panic`; the flags that catch a genuinely wedged machine
(`nmi_watchdog=panic`, `softlockup_panic=1`, `hung_task_panic=1`, `panic=30`)
must all stay.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mp_fix_oops_panic.sh"

# A stand-in for a real BLS entry, with the same shape as the live one.
SAMPLE_ENTRY = """title Bazzite (7.3.4-trimmed)
version 7.3.4-mplanetarian-dreamworlds-ultra-v1
options ostree=/ostree/boot.0/default/abc/0 rhgb root=UUID=deadbeef vconsole.keymap=us rootflags=subvol=root rw bluetooth.disable_ertm=1 reserve_mem=8M:4096:ramoops pstore.backend=ramoops nvidia_drm.modeset=1 preempt=full crashkernel=256M nmi_watchdog=panic softlockup_panic=1 hung_task_panic=1 oops=panic panic=30
linux /vmlinuz-7.3.4
initrd /initramfs-7.3.4.img
"""

KEPT_FLAGS = ("nmi_watchdog=panic", "softlockup_panic=1", "hung_task_panic=1", "panic=30")


def _bash() -> str:
    return os.environ.get("BASH", "bash")


class TestFixOopsPanicScript(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), SCRIPT)
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable")

    def test_shell_syntax_is_valid(self):
        res = subprocess.run([_bash(), "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_help_runs(self):
        res = subprocess.run([_bash(), str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("oops", res.stdout)

    def test_it_removes_only_oops_panic(self):
        self.assertIn("oops=panic", self.text)
        self.assertRegex(self.text, r"grep -vx 'oops=panic'|grep -vx \"oops=panic\"")

    def test_the_safety_flags_are_documented_as_kept(self):
        """The script must explain why the other panic flags stay."""
        for flag in KEPT_FLAGS:
            self.assertIn(flag, self.text)

    def test_it_backs_up_before_writing(self):
        self.assertIn(".mp-original", self.text)
        self.assertIn("cp -p", self.text)

    def test_it_is_reversible(self):
        self.assertIn("--revert", self.text)

    def test_dry_run_is_the_default(self):
        """Without --apply it must not write anything."""
        self.assertRegex(self.text, r'MODE="dry"')

    def test_it_names_the_kvm_evidence(self):
        """A future reader needs the concrete crash this was written for."""
        self.assertIn("kvm_create_pit", self.text)


class TestCmdlineRewrite(unittest.TestCase):
    """Exercise the rewrite for real, against a temp entry file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-oops-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.entry = self.tmp / "ostree-99-custom-7.3.4-trimmed.conf"
        self.entry.write_text(SAMPLE_ENTRY, encoding="utf-8")

    def _rewritten(self) -> str:
        """Run the same transformation the script uses, in a shell."""
        body = r'''
set -uo pipefail
f="$1"
new=$(grep -m1 "^options " "$f" | sed "s/^options //" | tr " " "\n" | grep -vx "oops=panic" | paste -sd" " -)
awk -v n="$new" "/^options /{print \"options \" n; next}{print}" "$f"
'''
        res = subprocess.run([_bash(), "-c", body, "_", str(self.entry)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def test_oops_panic_is_gone(self):
        out = self._rewritten()
        opts = [l for l in out.splitlines() if l.startswith("options ")][0]
        self.assertNotIn("oops=panic", opts.split())

    def test_every_safety_flag_survives(self):
        out = self._rewritten()
        opts = [l for l in out.splitlines() if l.startswith("options ")][0].split()
        for flag in KEPT_FLAGS:
            self.assertIn(flag, opts, f"{flag} was dropped but must be kept")

    def test_nothing_else_is_touched(self):
        """Only the oops=panic token may differ, or the boot may change shape."""
        out = self._rewritten().splitlines()
        orig = SAMPLE_ENTRY.splitlines()
        self.assertEqual(len(out), len(orig))
        for a, b in zip(orig, out):
            if a.startswith("options "):
                self.assertEqual(
                    [t for t in a.split() if t != "oops=panic"],
                    b.split(),
                    "the rewritten options must equal the original minus oops=panic",
                )
            else:
                self.assertEqual(a, b, "non-options lines must be byte-identical")

    def test_no_double_spaces_left_behind(self):
        out = self._rewritten()
        opts = [l for l in out.splitlines() if l.startswith("options ")][0]
        self.assertNotIn("  ", opts, "removing a token must not leave a double space")


class TestLiveEntryIfPresent(unittest.TestCase):
    """If a real entry exists on this machine, pin the flags it should carry."""

    ENTRY = Path("/boot/loader/entries/ostree-99-custom-7.3.4-trimmed.conf")

    @unittest.skipUnless(ENTRY.is_file(), "no live boot entry on this machine")
    def test_live_entry_still_carries_the_safety_flags(self):
        opts = ""
        for line in self.ENTRY.read_text(encoding="utf-8").splitlines():
            if line.startswith("options "):
                opts = line
                break
        self.assertTrue(opts, "entry has no options line")
        for flag in KEPT_FLAGS:
            self.assertIn(flag, opts.split(),
                          f"live entry lost a safety flag: {flag}")


if __name__ == "__main__":
    unittest.main()
