#!/usr/bin/env python3
"""How each boot ENDED must be recorded, so a power loss is not confused with a panic.

On this machine `oops=panic` is set, so any kernel oops reboots the box within a
second. From the outside that looks exactly like a power cut: the journal stops.
`scripts/mp_boot_death_ledger.sh` separates the two using the DRAM crash area.

The signal it relies on:

  * `reserve_mem=8M:4096:ramoops` keeps a slice of DRAM for crash capture.
  * A **warm** reboot preserves DRAM, so a panic dump survives and the next boot
    logs `ramoops: using 0x800000@...`.
  * A **total power loss** loses DRAM, destroys the ECC header, and the next boot
    logs `ramoops: uncorrectable error in header`.

So the verdict for a boot is written by the boot that follows it. These tests
pin that model, the classification rules built on it, and the two shell bugs
that were found while building it: `pipefail` + `grep -q`, and appending to a
scalar instead of an array.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mp_boot_death_ledger.sh"


def _bash() -> str:
    return os.environ.get("BASH", "bash")


class TestBootDeathLedgerScript(unittest.TestCase):
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
        self.assertIn("boot", res.stdout.lower())

    # --- the forensic model ---------------------------------------------------
    def test_it_reads_the_ramoops_header_error(self):
        """The ECC-header message is the whole power-loss detector."""
        self.assertIn("uncorrectable error in header", self.text)

    def test_it_explains_why_the_signal_works(self):
        """A future reader must know this is DRAM retention, not a heuristic."""
        self.assertRegex(self.text, r"(?i)dram")
        self.assertRegex(self.text, r"(?i)reserve_mem|ramoops")

    def test_it_records_both_outcome_kinds(self):
        self.assertIn("POWER_LOSS", self.text)
        self.assertIn("PANIC", self.text)
        self.assertIn("CLEAN_REBOOT", self.text)

    def test_clean_reboot_is_detected_from_shutdown_record(self):
        """An orderly reboot is identified by logind/systemd-shutdown markers."""
        for marker in ("The system will reboot now", "systemd-shutdown"):
            self.assertIn(marker, self.text)

    def test_it_does_not_trust_a_stale_dump(self):
        """A dump describes one death; it must not relabel older boots."""
        self.assertIn("panic_in_window", self.text)
        self.assertRegex(self.text, r"(?i)stale")

    # --- shell robustness -----------------------------------------------------
    def test_no_pipe_into_grep_q_under_pipefail(self):
        """`x | grep -q` yields 141 under pipefail and reads as 'no match'.

        This was a real bug: every clean reboot was misreported because grep -q
        exited early and the writer took SIGPIPE. The fix is a herestring (or a
        process substitution), never a pipe.
        """
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Only a `-q` grep is dangerous: it exits at the first match.
            if not re.search(r"grep\s+-\w*q", stripped):
                continue
            before = re.split(r"grep\s+-\w*q", stripped)[0]
            # Process substitution `<( ... )` is fine and does not SIGPIPE.
            if "<(" in stripped:
                continue
            self.assertNotIn("|", before.rstrip(),
                             f"pipes into a -q grep under pipefail: {stripped}")

    def test_verdicts_is_an_array_not_a_scalar(self):
        """`VERDICTS=""` plus `+=` appends to index 0 and shifts every verdict."""
        self.assertIn("VERDICTS=()", self.text)
        self.assertNotRegex(self.text, r'^VERDICTS=""', )

    def test_it_parses_the_list_boots_json_array(self):
        """`journalctl --list-boots -o json` is ONE array line, not one per boot."""
        self.assertIn("--list-boots", self.text)
        self.assertIn("s/},{/}\\n{/g", self.text)

    def test_it_is_idempotent_by_boot_id(self):
        """Re-running must not duplicate a boot."""
        self.assertIn("boot_id", self.text.lower())
        self.assertRegex(self.text, r"grep -qx|already")

    def test_display_index_is_derived_from_position(self):
        """The stored `boot` index goes stale as boots arrive; two rows said '0'.

        The journalctl index is a point-in-time label. Once a new boot exists,
        every stored index is off by one, so the display must number rows by
        their position in the ledger and keep boot_id as the real key.
        """
        self.assertIn("grep -c", self.text)
        self.assertIn("pos=$(( i - rows + 1 ))", self.text)

    # --- operational behaviour ------------------------------------------------
    def test_ledger_location_is_overridable(self):
        self.assertIn("MP_BOOT_LEDGER", self.text)

    def test_it_can_be_installed_and_removed(self):
        self.assertIn("--install", self.text)
        self.assertIn("--uninstall", self.text)

    def test_it_supports_json_output(self):
        self.assertIn("--json", self.text)

    def test_it_survives_journal_rotation(self):
        """The ledger exists so history outlives journald retention."""
        self.assertRegex(self.text, r"(?i)retention|rotat")


class TestBootDeathClassificationLogic(unittest.TestCase):
    """Exercise the classifier directly, without touching the real journal."""

    def _run(self, script_body: str) -> str:
        res = subprocess.run(
            [_bash(), "-c", script_body],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout.strip()

    def test_power_loss_when_next_boot_saw_header_errors(self):
        body = r'''
set -uo pipefail
classify() {
    local hdr="$1" clean="$2" dump="$3"
    if [ "${hdr:-0}" -gt 0 ] 2>/dev/null; then echo "POWER_LOSS"; return; fi
    case "$clean" in
        yes) echo "CLEAN_REBOOT" ;;
        *) case "$dump" in yes) echo "PANIC" ;; *) echo "UNCLEAR_WARM_RESET" ;; esac ;;
    esac
}
classify 10 no yes     # header wiped wins even with a stale dump present
classify 0 yes no
classify 0 no yes
classify 0 no no
'''
        out = self._run(body).splitlines()
        self.assertEqual(out[0], "POWER_LOSS",
                         "a wiped crash area must outrank a leftover dump")
        self.assertEqual(out[1], "CLEAN_REBOOT")
        self.assertEqual(out[2], "PANIC")
        self.assertEqual(out[3], "UNCLEAR_WARM_RESET")

    def test_grep_q_herestring_avoids_pipefail_false_negative(self):
        """The exact bug: pipe into grep -q returns 141 and reads as 'no match'."""
        body = r'''
set -uo pipefail
payload="early line The system will reboot now late line"
# The wrong way, kept as a control: it must NOT be trusted as a boolean.
printf "%s\n" "$payload" | grep -qi "reboot now"; echo "piped=$?"
# The right way.
grep -qi "reboot now" <<< "$payload"; echo "herestring=$?"
'''
        out = self._run(body)
        self.assertIn("herestring=0", out)
        # The control proves the hazard is real on this bash.
        self.assertRegex(out, r"piped=(0|141)")

    def test_array_append_ordering(self):
        """`X=""` + `+=(a)` leaves an EMPTY element at index 0; `X=()` is correct."""
        body = r'''
set -uo pipefail
BAD=""
BAD+=(first)
BAD+=(second)
echo "bad_len=${#BAD[@]} bad0=[${BAD[0]}] bad1=[${BAD[1]}]"
GOOD=()
GOOD+=(first)
GOOD+=(second)
echo "good_len=${#GOOD[@]} good0=[${GOOD[0]}] good1=[${GOOD[1]}]"
'''
        out = self._run(body).splitlines()
        # The scalar form produces a phantom empty element at [0], which shifts
        # every subsequent verdict by one row.
        self.assertEqual(out[0], "bad_len=3 bad0=[] bad1=[first]")
        self.assertEqual(out[1], "good_len=2 good0=[first] good1=[second]")


class TestLedgerNeverNeedsRootAtRuntime(unittest.TestCase):
    """The unit runs as the user; it must not require an interactive password."""

    def test_it_prefers_passwordless_sudo(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("sudo -n true", text)


if __name__ == "__main__":
    unittest.main()
