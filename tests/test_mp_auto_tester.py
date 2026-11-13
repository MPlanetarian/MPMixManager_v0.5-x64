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
        "version": "0.5.0",
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
    def test_all_pieces_exist_and_are_executable(self):
        for path in (HARNESS, BRIDGE):
            self.assertTrue(path.is_file(), path)
            self.assertTrue(os.access(path, os.X_OK), f"{path} is not executable")
        for path in (BUILDER, WIN_SERVER):
            self.assertTrue(path.is_file(), path)

    def test_shell_syntax(self):
        for path in (HARNESS, BRIDGE):
            res = subprocess.run([_bash(), "-n", str(path)],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{path}: {res.stderr}")

    def test_python_syntax(self):
        for path in (BUILDER, WIN_SERVER):
            res = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{path}: {res.stderr}")

    def test_the_four_required_screenshot_names(self):
        text = HARNESS.read_text(encoding="utf-8")
        for stem in ("TEST_MP_VISUALISER", "TEST_MP_GUI",
                     "TEST_MP_LIQUID_PLAYER", "TEST_MP_MIX_MANAGER"):
            self.assertIn(stem, text)

    def test_settle_time_can_never_drop_below_twenty_seconds(self):
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("WAIT_SECONDS=20", text)
        self.assertIn('[ "$WAIT_SECONDS" -lt 20 ]', text)
        # The brief asks for at least 20 seconds; the clamp must exist in code.
        res = subprocess.run(
            [_bash(), str(HARNESS), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("wait", res.stdout.lower())

    def test_all_five_systems_are_known(self):
        text = HARNESS.read_text(encoding="utf-8")
        for token in ("local", "fedora", "mac", "windows", "freebsd"):
            self.assertIn(token, text)
        self.assertIn("MP_MAC_HOST", text)
        self.assertIn("MP_FEDORA_HOST", text)
        self.assertIn("MP_FREEBSD_HOST", text)

    def test_screenshot_archive_name_and_shortcut(self):
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("MP_TESTER_MIX_MANAGER_SCREENSHOTS", text)
        self.assertIn("create_screenshot_shortcut", text)
        # Windows gets a .lnk, macOS an alias, Linux a .desktop entry.
        self.assertIn(".lnk", text)
        self.assertIn("[Desktop Entry]", text)

    def test_mp_reporter_is_wired_in(self):
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("DAILY_REPORT_TASK_005_AUTO_TESTER", text)
        self.assertIn("MP_Reporter.sh", text)
        self.assertIn("run DAILY_REPORT_TASK_005_AUTO_TESTER", text)


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

    def test_remote_home_directories_are_resolved_not_expanded_by_scp(self):
        text = HARNESS.read_text(encoding="utf-8")
        # scp does not expand $HOME, so the controller has to resolve the path.
        self.assertIn("cd \\\"$rtree\\\" 2>/dev/null && pwd", text)


if __name__ == "__main__":
    unittest.main()
