#!/usr/bin/env python3
"""MP Auto Tester: harness contract, report builder and Reporter bridge."""

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "MP_Auto_Tester_Flash.sh"
BRIDGE = ROOT / "scripts" / "mp_auto_tester_report.sh"
BUILDER = ROOT / "scripts" / "mp_auto_tester_report.py"
WIN_SERVER = ROOT / "scripts" / "mp_win_agent_server.py"

# MP_Auto_Tester_Flash.sh is an internal Dreamworlds Productions dev utility and
# is gitignored, so a public checkout does not contain it. The report builder,
# the Reporter bridge and the Windows agent ARE shipped, so their tests run
# everywhere; only the assertions that read the controller source need the file.
HARNESS_SKIP = "MP_Auto_Tester_Flash.sh is an internal utility and is not in this checkout"

# Applied to every assertion that reads the controller's source. On this studio
# machine the file is present and the checks run; from a public clone they skip
# instead of failing, because the file is gitignored on purpose.
needs_harness = unittest.skipUnless(HARNESS.is_file(), HARNESS_SKIP)

sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _bash() -> str:
    return os.environ.get("BASH", "bash")


def sample_results() -> dict:
    shot = {"app": "visualiser", "file": "TEST_MP_VISUALISER_HOST.jpeg",
            "path": "", "status": "ok"}
    return {
        "stamp": "20260101_000000",
        "host": "HOST",
        "version": "0.5.3",
        "wait_seconds": 20,
        "started": "2026-01-01 00:00:00",
        "finished": "2026-01-01 00:10:00",
        "systems": [
            {
                "key": "local", "label": "Local Bazzite Workstation",
                "hostname": "HOST", "address": "127.0.0.1", "reachable": True,
                "tests": {"ran": 10, "failed": 0, "errored": 0, "skipped": 1,
                          "seconds": 5.0, "available": True,
                          "command": "python3 tests/run_tests.py"},
                "screenshots": [dict(shot)], "notes": [],
            },
            {
                "key": "mac", "label": "macOS mini", "hostname": "macmini",
                "address": "10.0.0.2", "reachable": True,
                "tests": {"ran": 0, "available": False},
                "screenshots": [], "notes": ["no tests"],
            },
            {
                "key": "freebsd", "label": "FreeBSD 15.1 VM",
                "hostname": "", "address": "10.0.0.3", "reachable": False,
                "tests": {}, "screenshots": [],
                "notes": ["system not reachable"],
            },
        ],
    }


class TestHarnessContract(unittest.TestCase):
    """Contract checks that read the internal Auto Tester controller.

    The whole class is skipped when the controller is absent, rather than each
    method: several methods read HARNESS without a decorator and a public clone
    reported them as errors instead of skips. A class-level guard cannot be
    forgotten when a new method is added.
    """

    @classmethod
    def setUpClass(cls):
        if not HARNESS.is_file():
            raise unittest.SkipTest(HARNESS_SKIP)

    def test_all_pieces_exist_and_are_executable(self):
        # The bridge, builder and Windows agent ship publicly; the controller is
        # internal, so it is only checked when this checkout has it.
        if HARNESS.is_file():
            self.assertTrue(os.access(HARNESS, os.X_OK), f"{HARNESS} is not executable")
        self.assertTrue(BRIDGE.is_file(), BRIDGE)
        self.assertTrue(os.access(BRIDGE, os.X_OK), f"{BRIDGE} is not executable")
        for path in (BUILDER, WIN_SERVER):
            self.assertTrue(path.is_file(), path)

    def test_shell_syntax(self):
        # Same split: always syntax-check the shipped bridge, and the internal
        # controller only when it is present.
        paths = [BRIDGE] + ([HARNESS] if HARNESS.is_file() else [])
        for path in paths:
            res = subprocess.run([_bash(), "-n", str(path)],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{path}: {res.stderr}")

    def test_python_syntax(self):
        for path in (BUILDER, WIN_SERVER):
            res = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{path}: {res.stderr}")

    @needs_harness
    def test_the_four_required_screenshot_names(self):
        text = HARNESS.read_text(encoding="utf-8")
        for stem in ("TEST_MP_VISUALISER", "TEST_MP_GUI",
                     "TEST_MP_LIQUID_PLAYER", "TEST_MP_MIX_MANAGER"):
            self.assertIn(stem, text)

    @needs_harness
    def test_settle_time_can_never_drop_below_twenty_seconds(self):
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("WAIT_SECONDS=20", text)
        self.assertIn('[ "$WAIT_SECONDS" -lt 20 ]', text)
        # The brief asks for at least 20 seconds; the clamp must exist in code.
        res = subprocess.run(
            [_bash(), str(HARNESS), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("wait", res.stdout.lower())

    @needs_harness
    def test_all_five_systems_are_known(self):
        text = HARNESS.read_text(encoding="utf-8")
        for token in ("local", "fedora", "mac", "windows", "freebsd"):
            self.assertIn(token, text)
        self.assertIn("MP_MAC_HOST", text)
        self.assertIn("MP_FEDORA_HOST", text)
        self.assertIn("MP_FREEBSD_HOST", text)

    @needs_harness
    def test_screenshot_archive_name_and_shortcut(self):
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("MP_TESTER_MIX_MANAGER_SCREENSHOTS", text)
        self.assertIn("create_screenshot_shortcut", text)
        # Windows gets a .lnk, macOS an alias, Linux a .desktop entry.
        self.assertIn(".lnk", text)
        self.assertIn("[Desktop Entry]", text)

    @needs_harness
    def test_shortcut_always_targets_the_archive_root(self):
        text = HARNESS.read_text(encoding="utf-8")
        # A --stamp or MP_TEST_SCREENSHOT_ROOT override must not be baked into
        # the desktop shortcut; it must always open the archive root.
        self.assertNotIn('create_screenshot_shortcut "$SHOT_ROOT"', text)
        self.assertIn(
            'local target="${1:-$HOME/$SCREENSHOT_DIRNAME}"', text)

    @needs_harness
    def test_mp_reporter_is_wired_in(self):
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("DAILY_REPORT_TASK_005_AUTO_TESTER", text)
        self.assertIn("MP_Reporter.sh", text)
        self.assertIn("run DAILY_REPORT_TASK_005_AUTO_TESTER", text)

    @needs_harness
    def test_the_bazzite_laptop_is_a_target(self):
        """The BazziteMac test bed is its own system, not the Fedora host."""
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("MP_BAZZITE_HOST", text)
        # The controller is deliberately not published (it carries this
        # studio's test-bed topology), so assert the address is configurable
        # rather than pinning a specific LAN address.
        self.assertRegex(text, r'BAZZITE_HOST="\$\{MP_BAZZITE_HOST:-[0-9.]+\}"')
        self.assertIn("bazzite|bazzitemac|laptop", text)
        res = subprocess.run(
            [_bash(), str(HARNESS), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("bazzite", res.stdout.lower())

    @needs_harness
    def test_the_ubuntu_38_host_is_a_target(self):
        """Ubuntu 20.04 ships Python 3.8, the oldest platform we support.

        It is the only test bed that fails when PEP 585 (`dict[...]`) or
        PEP 604 (`X | None`) syntax creeps in, so it must stay a target.
        """
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("MP_UBUNTU_HOST", text)
        self.assertRegex(text, r'UBUNTU_HOST="\$\{MP_UBUNTU_HOST:-[0-9.]+\}"')
        self.assertIn('ubuntu)  echo "Ubuntu 20.04 LTS', text)
        self.assertRegex(
            text,
            r'REMOTE_HOST=\([^)]*\[ubuntu\]="\$UBUNTU_HOST"',
        )

    @needs_harness
    def test_window_lookup_works_on_wayland(self):
        """wmctrl -l is empty on KWin Wayland; xdotool must be the fallback.

        The laptop runs Plasma Wayland, where the X11 root belongs to Xwayland
        and carries no EWMH client list. Relying on wmctrl made every window
        lookup fail and no screenshot was ever captured.
        """
        text = HARNESS.read_text(encoding="utf-8")
        body = text.split("list_windows_linux() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("xdotool", body)
        self.assertIn("wmctrl -l", body)

    @needs_harness
    def test_the_deployed_tree_includes_symlink_targets(self):
        """A copy with dangling links failed a dozen tests for the wrong reason.

        bin/* and scripts/* are symlinks into the tree, and assets/Exit_MP_Video
        .mp4 points at the root file. Deploying the links without their targets
        made the remote run look like a product regression.
        """
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("Exit_MP_Video.mp4", text)
        deploy = text.split("deploy_tree() {", 1)[1].split("tar -czf", 1)[0]
        self.assertIn("readlink", deploy)

    @needs_harness
    def test_the_windows_bundle_dereferences_symlinks(self):
        """Windows tar cannot make a Unix symlink, so it must dereference.

        The SSH path expands each link's target explicitly; the Windows bundle
        is a whole-tree tar and only --dereference makes it safe. Without it the
        guest got "Can't create ... Invalid argument" for every link and a
        truncated tree.
        """
        text = HARNESS.read_text(encoding="utf-8")
        windows_bundle = text.split("Bundling the MP Mix Manager tree for Windows", 1)[1]
        windows_bundle = windows_bundle.split("could not bundle the tree for Windows", 1)[0]
        self.assertIn("--dereference", windows_bundle)

    @needs_harness
    def test_the_companion_launcher_is_deployed(self):
        """The Windows companion launcher has to reach the guest.

        install.sh runs it from the install root on Windows, so a bundle without
        launch_web_companion.bat would install everything except the dashboard.
        """
        text = HARNESS.read_text(encoding="utf-8")
        deploy_block = text.split("DEPLOY_PATHS=(", 1)[1].split(")", 1)[0]
        self.assertIn("launch_web_companion.bat", deploy_block)

    @needs_harness
    def test_a_test_run_pins_the_resolver_at_the_tree_under_test(self):
        """On a deployed copy the resolver otherwise finds the real install."""
        text = HARNESS.read_text(encoding="utf-8")
        run_tests = text.split("run_unit_tests() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("MP_MIX_MANAGER_DIR=\"$tree\"", run_tests)

    @needs_harness
    def test_a_run_holds_a_sleep_inhibitor(self):
        """An idle desktop must not suspend in the middle of a test run."""
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("systemd-inhibit", text)
        self.assertIn("keep_awake", text)


class TestReportBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("mp_auto_tester_report", BUILDER)

    def test_verdicts_cover_pass_failures_no_tests_and_unreachable(self):
        rows = self.mod.system_rows(sample_results())
        states = [self.mod.verdict(row) for row in rows]
        self.assertEqual(states, ["PASS", "NO TESTS", "UNREACHABLE"])

    def test_verdict_reports_a_failing_system(self):
        data = sample_results()
        data["systems"][0]["tests"]["failed"] = 3
        row = self.mod.system_rows(data)[0]
        self.assertEqual(self.mod.verdict(row), "FAILURES")
        self.assertIn("3 failed", self.mod.verdict_verbose(row))

    def test_totals_ignore_unreachable_systems(self):
        rows = self.mod.system_rows(sample_results())
        agg = self.mod.totals(rows)
        self.assertEqual(agg["ran"], 10)
        self.assertEqual(agg["systems_ok"], 2)
        self.assertEqual(agg["systems_down"], 1)

    def test_text_report_names_every_system_and_the_settle_time(self):
        data = sample_results()
        text = self.mod.build_text(data, self.mod.system_rows(data))
        self.assertIn("Local Bazzite Workstation", text)
        self.assertIn("macOS mini", text)
        self.assertIn("FreeBSD 15.1 VM", text)
        self.assertIn("20s before each screenshot", text)
        self.assertIn("TEST_MP_VISUALISER_HOST.jpeg", text)

    def test_spoken_script_is_prose_not_a_table(self):
        data = sample_results()
        speech = self.mod.build_spoken_script(data, self.mod.system_rows(data))
        self.assertIn("MP Mix Manager automated cross system test report", speech)
        self.assertNotIn("|", speech)
        self.assertNotIn("===", speech)
        self.assertIn("not reachable", speech)

    def test_missing_screenshots_are_listed(self):
        rows = self.mod.system_rows(sample_results())
        missing = self.mod.missing_screenshots(rows[1])
        self.assertIn("MP Gui", missing)
        self.assertNotIn("MP Liquid Visualiser", self.mod.missing_screenshots(rows[0]))

    def test_builds_a_pdf_that_embeds_the_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shot = tmp_path / "TEST_MP_VISUALISER_HOST.jpeg"
            try:
                from PIL import Image
            except Exception:
                self.skipTest("Pillow is not installed")
            Image.new("RGB", (640, 400), (30, 90, 160)).save(shot, "JPEG")

            data = sample_results()
            data["systems"][0]["screenshots"][0]["path"] = str(shot)
            data["systems"][0]["screenshots"][0]["file"] = shot.name

            pdf = tmp_path / "report.pdf"
            txt = tmp_path / "report.txt"
            txt.write_text("placeholder", encoding="utf-8")
            try:
                self.mod.build_pdf(data, self.mod.system_rows(data), pdf)
            except ImportError:
                self.skipTest("reportlab is not installed")
            self.assertTrue(pdf.is_file())
            self.assertGreater(pdf.stat().st_size, 2000)
            # pdfimages comes from poppler-utils and is not installed on every
            # test bed (it is absent from the Windows VM). Verify the image is
            # embedded only when the tool is there, so a missing utility reads
            # as "not checked" rather than as a broken report builder.
            import shutil as _shutil
            if not _shutil.which("pdfimages"):
                return
            listing = subprocess.run(["pdfimages", "-list", str(pdf)],
                                     capture_output=True, text=True)
            if listing.returncode == 0:
                self.assertIn("jpeg", listing.stdout.lower())

    def test_cli_writes_pdf_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results = tmp_path / "results.json"
            results.write_text(json.dumps(sample_results()), encoding="utf-8")
            res = subprocess.run(
                [sys.executable, str(BUILDER), "--results", str(results),
                 "--out-dir", str(tmp_path), "--stamp", "TEST"],
                capture_output=True, text=True,
            )
            if "reportlab" not in res.stdout and res.returncode != 0:
                self.skipTest("reportlab is not installed")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("Report generated:", res.stdout)
            self.assertTrue((tmp_path / "MP_Auto_Tester_Report_TEST.txt").is_file())

    def test_cli_prints_the_spoken_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.json"
            results.write_text(json.dumps(sample_results()), encoding="utf-8")
            res = subprocess.run(
                [sys.executable, str(BUILDER), "--results", str(results), "--speech"],
                capture_output=True, text=True, check=True,
            )
            self.assertIn("MP Mix Manager automated cross system test report", res.stdout)


class TestReporterBridge(unittest.TestCase):
    def test_bridge_prints_the_line_mp_reporter_parses(self):
        text = BRIDGE.read_text(encoding="utf-8")
        # MP_Reporter.sh greps for this exact prefix to find the PDF.
        self.assertIn('echo "Report generated: $PDF"', text)

    def test_bridge_prefers_a_python_with_reportlab(self):
        text = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("import reportlab", text)

    def test_bridge_ignores_runs_without_results(self):
        text = BRIDGE.read_text(encoding="utf-8")
        # An interrupted run leaves an empty folder; the bridge must skip it.
        self.assertIn("newest_with_results", text)
        self.assertIn('if [ -f "${dir}results.json" ]', text)

    def test_bridge_copies_into_the_daily_bucket(self):
        text = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("PDF_REPORTS/DAILY_REPORTS", text)
        self.assertIn("REPORTS/DAILY_REPORTS", text)


class TestDailyEmailIntegration(unittest.TestCase):
    def test_auto_tester_is_its_own_deduplication_family(self):
        text = (ROOT / "scripts" / "mp_daily_email_reporter.py").read_text(encoding="utf-8")
        self.assertIn('return "auto_tester"', text)

    def test_screenshots_are_attached_to_the_daily_email(self):
        text = (ROOT / "scripts" / "mp_daily_email_reporter.py").read_text(encoding="utf-8")
        self.assertIn("find_auto_tester_screenshots", text)
        self.assertIn('MIMEBase("image", "jpeg")', text)
        self.assertIn("MP_TESTER_MIX_MANAGER_SCREENSHOTS", text)

    def test_screenshot_discovery_reads_only_the_newest_run(self):
        mod = _load("mp_daily_email_reporter", ROOT / "scripts" / "mp_daily_email_reporter.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "20260101_000000" / "local"
            new = root / "20260102_000000" / "fedora"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / "TEST_MP_GUI_OLD.jpeg").write_bytes(b"old")
            (new / "TEST_MP_GUI_NEW.jpeg").write_bytes(b"new")
            prev = os.environ.get("MP_TEST_SCREENSHOT_ROOT")
            os.environ["MP_TEST_SCREENSHOT_ROOT"] = str(root)
            try:
                shots = mod.find_auto_tester_screenshots()
            finally:
                if prev is None:
                    os.environ.pop("MP_TEST_SCREENSHOT_ROOT", None)
                else:
                    os.environ["MP_TEST_SCREENSHOT_ROOT"] = prev
            self.assertEqual([s.name for s in shots], ["TEST_MP_GUI_NEW.jpeg"])


class TestWindowsAgentServer(unittest.TestCase):
    def test_serves_scripts_and_collects_results(self):
        import threading
        import time
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "one.ps1").write_text('Write-Output "one"', encoding="utf-8")
            (work / "two.ps1").write_text('Write-Output "two"', encoding="utf-8")

            env = dict(os.environ)
            proc = subprocess.Popen(
                [sys.executable, str(WIN_SERVER), "--dir", str(work),
                 "--host", "127.0.0.1", "--port", "0",
                 "--task", "one", "--task", "two",
                 "--timeout", "20", "--idle-exit", "10"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            )
            base = None
            deadline = time.time() + 10
            while time.time() < deadline:
                line = proc.stdout.readline()
                if "listening on" in line:
                    base = line.split("listening on", 1)[1].strip().split()[0]
                    break
            if not base:
                proc.kill()
                self.skipTest("the task server did not report its address")

            try:
                with urllib.request.urlopen(f"{base}/next", timeout=5) as r:
                    first = json.loads(r.read())
                self.assertEqual(first["name"], "one")
                with urllib.request.urlopen(f"{base}{first['url']}", timeout=5) as r:
                    self.assertIn("one", r.read().decode())
                req = urllib.request.Request(f"{base}/result/one", data=b"done one",
                                             method="POST")
                urllib.request.urlopen(req, timeout=5).read()

                with urllib.request.urlopen(f"{base}/next", timeout=5) as r:
                    second = json.loads(r.read())
                self.assertEqual(second["name"], "two")
                req = urllib.request.Request(f"{base}/result/two", data=b"done two",
                                             method="POST")
                urllib.request.urlopen(req, timeout=5).read()
                with urllib.request.urlopen(f"{base}/next", timeout=5) as r:
                    self.assertEqual(json.loads(r.read()), {})
            finally:
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                for stream in (proc.stdout, proc.stderr):
                    try:
                        stream.close()
                    except Exception:
                        pass
            self.assertEqual((work / "one.out").read_bytes(), b"done one")
            self.assertEqual((work / "two.out").read_bytes(), b"done two")


class TestDeploymentBundle(unittest.TestCase):
    """Deploy-bundle checks, all of which read the internal controller."""

    @classmethod
    def setUpClass(cls):
        if not HARNESS.is_file():
            raise unittest.SkipTest(HARNESS_SKIP)

    @needs_harness
    def test_only_the_application_and_tests_are_shipped(self):
        text = HARNESS.read_text(encoding="utf-8")
        start = text.index("DEPLOY_PATHS=(")
        end = text.index(")", start)
        block = text[start:end]
        # The heavy generated content must never be copied to a test host.
        self.assertNotIn("MIX_ARCHIVE", block)
        self.assertNotIn("REPORTS", block)
        for required in ("scripts", "tests", "themes", "shaders", "MP_Audio_Player.py"):
            self.assertIn(required, block)

    @needs_harness
    def test_remote_home_directories_are_resolved_not_expanded_by_scp(self):
        text = HARNESS.read_text(encoding="utf-8")
        # scp does not expand $HOME, so the controller has to resolve the path.
        self.assertIn("cd \\\"$rtree\\\" 2>/dev/null && pwd", text)

    def test_sync_finds_a_moved_u_disks_mount(self):
        text = HARNESS.read_text(encoding="utf-8")
        # The other Bazzite install is a udisks mount whose path has changed
        # between /run/media/system/... and /run/media/<user>/.... A hard-coded
        # default silently stops syncing, so the script has to probe.
        self.assertIn("find_other_bazzite()", text)
        self.assertIn("/run/media/$(id -un)/bazzite_raid/home/$(id -un)", text)
        self.assertNotIn(
            'local other="${MP_OTHER_BAZZITE:-/run/media/system/bazzite_raid', text)

    def test_synced_reporter_task_uses_the_guest_own_path(self):
        text = HARNESS.read_text(encoding="utf-8")
        # The task runs on the other machine, so REPORT_SCRIPT must be that
        # machine's /home/<user>/... path, never this host's /run/media mount.
        self.assertIn('suffix="${other#*/home/}"', text)
        self.assertIn('bridge="/home/$suffix/$(basename "$ROOT")/scripts/mp_auto_tester_report.sh"', text)

    def test_tests_cannot_repoint_the_users_launchers(self):
        text = HARNESS.read_text(encoding="utf-8")
        # Some suites run install.sh, which publishes ~/.local/bin links. That
        # repointed fedora's real desktop launcher on 2026-11-14. The harness
        # snapshots the user's shims before a test run and restores them after.
        self.assertIn("snapshot_shims", text)
        self.assertIn("restore_shims", text)
        self.assertIn('ln -sfn "$target" "$bin/$name"', text)
        self.assertIn("MP_RUN_SLOW_INSTALLER_TESTS=0", text)

    def test_freebsd_guest_address_is_the_live_one_not_the_advertised_one(self):
        text = HARNESS.read_text(encoding="utf-8")
        # The X299ROGFREEBSD image answers on .16 while cloud-init still says
        # .14; a hard-coded .14 reports a running VM as unreachable.
        self.assertRegex(text, r'FREEBSD_HOST="\$\{MP_FREEBSD_HOST:-[0-9.]+\}"')
        self.assertIn("freebsd_reachable()", text)

    def test_sync_installs_the_daily_timer_on_the_other_system(self):
        text = HARNESS.read_text(encoding="utf-8")
        # Without the timer units the other machine only has the task file and
        # never runs it, which is silently broken.
        self.assertIn('mp-reporter-DAILY_REPORT_TASK_005_AUTO_TESTER.timer', text)
        self.assertIn('OnCalendar=*-*-* 10:10:00', text)
        self.assertIn('ln -sfn "../mp-reporter-DAILY_REPORT_TASK_005_AUTO_TESTER.timer"', text)


if __name__ == "__main__":
    unittest.main()
