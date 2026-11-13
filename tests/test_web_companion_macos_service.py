"""Tests for the macOS launchd install of the Web Companion.

macOS TCC judges a background job by its *responsible process*. The original
bundle entry point was a `#!/bin/bash ... exec python3` shim: `exec` replaces
the process image, so the archive read was attributed to
`com.apple.python3` and a Full Disk Access grant recorded against
"MP Web Companion.app" never applied. The phone then loaded a dashboard whose
Engage Audio Stream button played an empty <audio> element.

These tests pin the two halves of the fix:

* the bundle ships a native launcher that stays resident as Python's parent, so
  the bundle remains the responsible process;
* the wrapper reports a privacy block from what the *service* sees, not from
  what the Terminal running `status` can read.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "scripts" / "mix_web_companion.sh"


class TestBundleLauncher(unittest.TestCase):
    def setUp(self):
        self.text = WRAPPER.read_text(encoding="utf-8")

    def test_the_bundle_lives_in_the_system_applications_folder(self):
        """Finder's sidebar "Applications" is /Applications, not ~/Applications.

        A bundle in ~/Applications is invisible to a user who opens the folder
        from the sidebar, which is exactly where they go to find the app for the
        privacy pane. /Applications is group-writable by admin, so an admin
        account can install there without a password; ~/Applications stays as
        the fallback for a non-admin account.
        """
        self.assertIn("pick_mac_bundle()", self.text)
        self.assertIn("/Applications", self.text)
        self.assertIn('MAC_BUNDLE="$(pick_mac_bundle)"', self.text)
        fn = self.text[self.text.index("pick_mac_bundle()") :]
        fn = fn[: fn.index("MAC_BUNDLE=")]
        self.assertIn("for dir in /Applications", fn)
        self.assertIn("-w", fn)

    def test_stale_bundle_copies_are_removed(self):
        """Two copies of one bundle id make macOS resolve the wrong one."""
        self.assertIn("LEGACY_MAC_BUNDLES=", self.text)
        self.assertIn("remove_legacy_bundles()", self.text)
        self.assertIn('remove_legacy_bundles "$bundle"', self.text)
        self.assertIn('remove_legacy_bundles "$MAC_BUNDLE"', self.text)

    def test_the_bundle_is_registered_with_launch_services(self):
        """Otherwise the app can be absent from the privacy pane until login."""
        self.assertIn("register_bundle()", self.text)
        self.assertIn("lsregister", self.text)
        self.assertIn("register_bundle \"$bundle\"", self.text)

    def test_the_bundle_declares_version_keys_and_an_icon(self):
        """A version-less, icon-less bundle reads as a stray file in the picker."""
        fn = self.text[self.text.index("write_launchd_plist()") :]
        fn = fn[: fn.index("write_service()")]
        self.assertIn("<key>CFBundleVersion</key>", fn)
        self.assertIn("<key>CFBundleInfoDictionaryVersion</key>", fn)
        self.assertIn("CFBundleIconFile", fn)

    def test_a_native_launcher_is_compiled_for_the_bundle(self):
        self.assertIn("compile_bundle_launcher()", self.text)
        self.assertIn("compile_bundle_launcher \"$bundle\" \"$macos_dir/mp-web-companion\"", self.text)

    def test_launcher_keeps_the_bundle_as_parent_of_python(self):
        """It must fork and wait, never exec, or the bundle identity is lost."""
        fn = self.text[self.text.index("compile_bundle_launcher()") :]
        fn = fn[: fn.index("write_launchd_plist()")]
        self.assertIn("waitpid", fn)
        self.assertIn("fork()", fn)
        # exec must only happen in the child, after the fork.
        self.assertNotIn('\nexec ', fn)
        self.assertIn('execl("$PYTHON_BIN"', fn)

    def test_launcher_is_adhoc_signed_with_the_bundle_identifier(self):
        fn = self.text[self.text.index("compile_bundle_launcher()") :]
        fn = fn[: fn.index("write_launchd_plist()")]
        self.assertIn("codesign --force --sign -", fn)
        self.assertIn("com.mplanetarian.mp-web-companion", fn)

    def test_missing_clang_keeps_a_working_server(self):
        """A shell shim that runs is better than a bundle that cannot start."""
        fn = self.text[self.text.index("compile_bundle_launcher()") :]
        fn = fn[: fn.index("write_launchd_plist()")]
        self.assertIn("command -v clang", fn)
        self.assertIn("keeping the shell entry point", fn)

    def test_the_shell_shim_still_exists_as_the_fallback(self):
        self.assertIn('exec "$PYTHON_BIN" "$PY_SCRIPT"', self.text)

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_the_generated_launcher_builds_and_propagates_the_exit_code(self):
        """Compile the real C source from the wrapper and run it end to end."""
        binary, tmp_path = self._build_real_launcher(
            "import os, sys\n"
            "print('ppid', os.getppid())\n"
            "sys.exit(3)\n"
        )
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        # The child's status must survive the resident parent.
        self.assertEqual(run.returncode, 3, run.stderr)
        # The launcher reopens its own log (a LaunchServices-started app gets
        # "/" as cwd and no launchd StandardOutPath), so the child's output
        # lands there rather than on the inherited stdout.
        self.assertIn("ppid", (tmp_path / "bundle.log").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("clang"), "clang not installed")
    def test_the_launcher_forwards_stop_to_python(self):
        """`launchctl bootout` sends SIGTERM to the launcher only.

        If it is not forwarded, Python is orphaned and keeps port 8888 bound:
        the restart looks successful while the old server still answers.
        """
        binary, tmp_path = self._build_real_launcher(
            "import signal, sys, time\n"
            "def bye(sig, frm):\n"
            "    print('child got signal', sig, flush=True)\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, bye)\n"
            "print('child ready', flush=True)\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        log = tmp_path / "bundle.log"
        launcher = subprocess.Popen(
            [str(binary)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        try:
            self.assertIn("child ready", self._wait_for(log, "child ready"))
            child = subprocess.run(
                ["pgrep", "-P", str(launcher.pid)], capture_output=True, text=True
            ).stdout.split()
            self.assertTrue(child, "launcher did not start a child")
            launcher.terminate()
            launcher.wait(timeout=15)
            # The child must be gone, not orphaned.
            time.sleep(0.5)
            still_there = subprocess.run(
                ["kill", "-0", child[0]], capture_output=True
            ).returncode == 0
            self.assertFalse(still_there, "child survived the launcher's SIGTERM")
        finally:
            if launcher.poll() is None:
                launcher.kill()
            if launcher.stdout:
                launcher.stdout.close()
            subprocess.run(["pkill", "-9", "-f", "time.sleep(0.1)"], capture_output=True)

    def _wait_for(self, path: Path, needle: str, timeout: float = 20.0) -> str:
        """Wait for a line to appear in the launcher's log.

        The launcher redirects its own stdout/stderr into $LOG_FILE, so the
        child's output is no longer readable from the Popen pipe.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            if needle in text:
                return text
            time.sleep(0.1)
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _build_real_launcher(self, child_source: str):
        """Compile the wrapper's own C source against a stub child script."""
        fn = self.text[self.text.index("compile_bundle_launcher()") :]
        fn = fn[: fn.index("write_launchd_plist()")]
        c_source = re.search(r'cat >"\$src" <<EOF\n(.*?)\nEOF', fn, re.S)
        self.assertIsNotNone(c_source, "C source block not found")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        script = tmp_path / "child.py"
        script.write_text(child_source, encoding="utf-8")
        src = c_source.group(1)
        # The launcher chdirs to $APP_DIR and reopens $LOG_FILE (LaunchServices
        # starts an app with "/" as its working directory and ignores launchd's
        # StandardOutPath), so the harness has to substitute those too or every
        # case fails at chdir before Python ever starts.
        log_file = tmp_path / "bundle.log"
        src = (
            src.replace("$PYTHON_BIN", "/usr/bin/python3")
            .replace("$PY_SCRIPT", str(script))
            .replace("$HOST", "127.0.0.1")
            .replace("$PORT", "8899")
            .replace("$APP_DIR", str(tmp_path))
            .replace("$LOG_FILE", str(log_file))
        )
        c_file = tmp_path / "launcher.c"
        c_file.write_text(src, encoding="utf-8")
        binary = tmp_path / "launcher"
        build = subprocess.run(
            ["clang", "-O2", "-Wall", "-Wextra", "-o", str(binary), str(c_file)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(build.returncode, 0, build.stderr)
        return binary, tmp_path


class TestPrivacyWarningAsksTheService(unittest.TestCase):
    def setUp(self):
        self.text = WRAPPER.read_text(encoding="utf-8")
        self.fn = self.text[self.text.index("macos_privacy_warning()") :]
        self.fn = self.fn[: self.fn.index("enable_autostart()")]

    def test_it_reads_the_live_status_endpoint(self):
        """Terminal's own FDA grant made a local listdir() probe lie."""
        self.assertIn("/api/status", self.fn)
        self.assertIn("urllib.request", self.fn)

    def test_it_reports_the_full_disk_access_detail_to_the_user(self):
        self.assertIn("Full Disk Access", self.fn)
        self.assertIn("library_detail", self.fn)

    def test_it_names_removable_volumes_for_a_usb_archive(self):
        """tccd's own log names SystemPolicyRemovableVolumes for this bundle."""
        self.assertIn("Removable Volumes", self.fn)

    def test_it_names_the_interpreter_as_a_second_grant(self):
        self.assertIn("approve this interpreter too", self.fn)


if __name__ == "__main__":
    unittest.main()
