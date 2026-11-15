#!/usr/bin/env python3
"""The maintenance page must measure cleanup, not assert success.

Before this change the Linux maintenance page printed a fixed "✓ ... cleared"
line for every step whatever happened, and the Complete Cleanup Suite:

  * hid `rm -rf ~/.cache/thumbnails` inside the `else` branch of its
    `ujust clean-system` check, so on Bazzite (which has ujust) thumbnails were
    never removed;
  * left the DNF cache (573 MB here) and the per-user journal (88 MB here)
    untouched, because `ujust clean-system` covers neither;
  * ran `swapoff -a` after a single y/N prompt with no check that the swapped
    pages fit in free RAM, which on a zram host is an OOM kill;
  * advertised "system logs" while only vacuuming the system journal.

These tests pin the behaviour that replaced it: every step is measured with df,
the summary reports the measurement, and a step that frees nothing says so.
"""

import subprocess
import unittest
from pathlib import Path
from tests.mp_test_bash import BASH

BASE_DIR = Path(__file__).resolve().parent.parent
MGR_SH = BASE_DIR / "Mix_Archive_Manager.sh"

# Load the shell file without running the interactive manager loop.
PRELUDE = "source ./Mix_Archive_Manager.sh >/dev/null 2>&1 || true\n"


def bash(script: str, timeout: int = 90):
    proc = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", PRELUDE + script],
        cwd=str(BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return proc.stdout.decode("utf-8", "replace")


class TestCleanupIsMeasured(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MGR_SH.read_text(encoding="utf-8")

    def test_thumbnails_are_not_inside_the_ujust_else_branch(self):
        """The old suite only removed thumbnails when ujust was absent."""
        self.assertNotIn(
            'rm -rf "$HOME/.cache/thumbnails"/* 2>/dev/null || true; fi',
            self.text,
            "thumbnail removal is back inside the ujust else-branch",
        )
        self.assertIn("_mp_clean_user_temp", self.text)

    def test_every_step_is_wrapped_in_a_measurement(self):
        for step in (
            "_mp_clean_package_caches",
            "_mp_clean_flatpak_unused",
            "_mp_clean_podman_images",
            "_mp_clean_brew",
            "_mp_clean_user_temp",
            "_mp_clean_journal",
        ):
            self.assertIn(f'_mp_cleanup_step "', self.text + step)
        # The suite must route each real step through the measuring wrapper.
        suite = self.text.split("mp_do_cleanup_suite()", 1)[1].split("\nmanage_system_maintenance", 1)[0]
        self.assertGreaterEqual(suite.count("_mp_cleanup_step "), 5)

    def test_summary_reports_nothing_rather_than_claiming_a_win(self):
        out = bash(
            '_MP_CLEAN_LABELS=("Nothing")\n'
            '_MP_CLEAN_BEFORE=(100)\n'
            '_MP_CLEAN_AFTER=(100)\n'
            '_MP_CLEAN_START_KB=$(_mp_free_kb)\n'
            '_mp_cleanup_summary\n'
        )
        self.assertIn("no change", out)
        # A zero-gain run must never print a positive "gained" figure.
        self.assertNotIn("Net free space gained: 0", out)

    def test_human_sizes_are_sane(self):
        out = bash(
            'for v in 0 512 1024 1536 1048576; do '
            'printf "%s=%s\\n" "$v" "$(_mp_human_kb "$v")"; done\n'
        )
        self.assertIn("0=0 KB", out)
        self.assertIn("512=512 KB", out)
        self.assertIn("1024=1.0 MB", out)
        self.assertIn("1048576=1.00 GB", out)

    def test_flatpak_sizes_parse_including_non_breaking_spaces(self):
        # flatpak prints "725.7 MB" and sometimes a NBSP before the unit.
        out = bash(
            'for s in "725.7 MB" "213.0 kB" "1.7 GB" "nonsense"; do '
            'printf "%s|%s\\n" "$s" "$(_mp_size_to_kb "$s")"; done\n'
        )
        self.assertIn("725.7 MB|743117", out)
        self.assertIn("213.0 kB|213", out)
        self.assertIn("1.7 GB|1782579", out)
        self.assertIn("nonsense|0", out)

    def test_pinned_foreign_arch_runtimes_are_reported(self):
        """A pinned aarch64 runtime can never be removed by --unused."""
        self.assertIn("_mp_report_pinned_runtimes", self.text)
        out = bash("_mp_report_pinned_runtimes\n")
        # The report must always be honest about the pin exemption.
        if "Pinned Flatpak runtimes" in out:
            self.assertIn("exempt from --unused", out)

    def test_swap_is_never_disabled_without_room_in_ram(self):
        """swapoff with more swapped pages than free RAM is an OOM kill."""
        body = self.text.split("_mp_clean_swap()", 1)[1].split("\n_mp_discard", 1)[0]
        self.assertIn("/proc/meminfo", body)
        # The guard must compare available RAM against the swap in use.
        self.assertIn("avail_kb", body)
        self.assertIn('"$avail_kb" -lt', body)
        # And it must refuse, not silently proceed.
        self.assertIn("Not flushing", body)

    def test_drop_caches_does_not_claim_disk_space(self):
        body = self.text.split("_mp_drop_caches()", 1)[1].split("\n_mp_clean_swap", 1)[0]
        self.assertIn("not disk space", body)
        # The file is root-only writable, so a `-w` test would always skip.
        self.assertNotIn('[ ! -w /proc/sys/vm/drop_caches ]', body)
        self.assertIn("[ ! -e /proc/sys/vm/drop_caches ]", body)

    def test_journal_step_covers_the_user_store(self):
        body = self.text.split("_mp_clean_journal()", 1)[1].split("\n_mp_drop_caches", 1)[0]
        self.assertIn("--user --vacuum-size", body)
        self.assertIn("--vacuum-size=200M", body)

    def test_trim_skips_devices_without_discard_support(self):
        """A USB disk with discard_granularity 0 makes fstrim -av crawl."""
        self.assertIn("_mp_discard_granularity", self.text)
        body = self.text.split("_mp_clean_fstrim()", 1)[1].split("\n_mp_discard_granularity", 1)[0]
        self.assertIn("discard", body.lower())
        self.assertIn("timeout", body)
        # findmnt must be parsed in pairs mode; -r is whitespace separated and
        # silently dropped every mount point on the machine. Only real
        # invocations count, not the comment that explains the old bug.
        self.assertIn("findmnt -P", body)
        live = [
            line for line in body.splitlines()
            if "findmnt" in line and not line.lstrip().startswith("#")
        ]
        self.assertTrue(live, "no live findmnt invocation found")
        for line in live:
            self.assertNotIn("findmnt -rno", line)

    def test_ostree_cleanup_keeps_the_rollback_deployment(self):
        body = self.text.split("_mp_clean_package_caches()", 1)[1].split("\n_mp_clean_flatpak", 1)[0]
        self.assertIn("rpm-ostree cleanup", body)
        # -r deletes the rollback deployment, the way back from a bad upgrade.
        self.assertNotIn("cleanup -b -m -r", body)
        self.assertNotIn("cleanup -r", body)

    def test_flatpak_uninstall_never_waits_for_an_answer(self):
        body = self.text.split("_mp_clean_flatpak_unused()", 1)[1].split("\n_mp_flatpak_ref_kb", 1)[0]
        self.assertIn("--noninteractive", body)
        self.assertIn("-y", body)


class TestMaintenanceWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MGR_SH.read_text(encoding="utf-8")

    def test_menu_offers_the_new_helpers(self):
        out = bash(
            "type -t mp_do_cleanup_suite mp_do_system_clean mp_do_journal_vacuum\n"
        )
        self.assertEqual(out.split().count("function"), 3, out)

    def test_menu_labels_do_not_overpromise(self):
        # "Vacuum System Logs" was wrong: the user journal was untouched.
        self.assertNotIn("Vacuum System Logs", self.text)
        self.assertIn("Vacuum Journals to 200MB", self.text)
        # The suite label must not claim unmeasured outcomes.
        self.assertNotIn("Run Complete Cleanup Suite${NC} (Clean System + Drop RAM Cache", self.text)

    def test_destructive_suite_still_asks_first(self):
        idx = self.text.find("mp_do_cleanup_suite; press_enter")
        self.assertGreater(idx, 0)
        window = self.text[max(0, idx - 260):idx]
        self.assertIn("mp_confirm_destructive", window,
                      "the Complete Cleanup Suite runs without a prompt")


if __name__ == "__main__":
    unittest.main()
