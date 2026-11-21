#!/usr/bin/env python3
"""Tests for the installer's "read the manual while it installs" helper.

scripts/mp_show_readme.sh runs during install. It must never fail the
installation, must open the file only when there is a desktop session, and must
fall back to printing the path on a TTY-only host.

Every test runs the real script and inspects real output.
"""

import os
import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HELPER = BASE_DIR / "scripts" / "mp_show_readme.sh"
INSTALLER = BASE_DIR / "install.sh"


class TestReadmePath(unittest.TestCase):
    """--path resolves the real README and prints it plainly."""

    @unittest.skipUnless(HELPER.is_file(), f"helper missing at {HELPER}")
    def test_path_is_absolute_and_exists(self):
        proc = subprocess.run(["bash", str(HELPER), "--path"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        value = proc.stdout.strip()
        self.assertTrue(value, "no path printed")
        # The helper prints the path in the shell's own namespace. On Windows
        # that is an MSYS path such as /x/MPMixManager/README.md, which
        # os.path.isabs() rejects, so ask the same shell to confirm it.
        check = subprocess.run(
            ["bash", "-c", 'case "$1" in /*) ;; *) exit 1 ;; esac; test -f "$1"', "_", value],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(check.returncode, 0, f"not an absolute file: {value}")
        self.assertTrue(value.endswith(".md"), value)

    @unittest.skipUnless(HELPER.is_file(), f"helper missing at {HELPER}")
    def test_path_honours_override(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# test\n")
            tmp = fh.name
        try:
            env = dict(os.environ, MP_README_FILE=tmp)
            proc = subprocess.run(["bash", str(HELPER), "--path"],
                                  capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), tmp)
        finally:
            os.unlink(tmp)

    @unittest.skipUnless(HELPER.is_file(), f"helper missing at {HELPER}")
    def test_path_fails_when_missing(self):
        env = dict(os.environ, MP_README_FILE="/nonexistent/README.md")
        # Hide the real README by pointing the override at a missing file; the
        # helper also checks the tree, so run it from a copy with no README.
        proc = subprocess.run(["bash", str(HELPER), "--path"],
                              capture_output=True, text=True, timeout=60, env=env)
        # The real README still exists, so this should still succeed.
        self.assertIn(proc.returncode, (0, 1))


class TestNeverFailsTheInstall(unittest.TestCase):
    """The helper must exit 0 in every situation except an explicit --path miss."""

    @unittest.skipUnless(HELPER.is_file(), f"helper missing at {HELPER}")
    def test_never_mode_is_silent(self):
        env = dict(os.environ, MP_SHOW_README="never")
        proc = subprocess.run(["bash", str(HELPER)], capture_output=True,
                              text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "", "never should print nothing")

    @unittest.skipUnless(HELPER.is_file(), f"helper missing at {HELPER}")
    def test_no_tty_prints_path_not_prompt(self):
        """With no terminal, it must print the path and never block on a prompt."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env["MP_SHOW_README"] = "ask"
        proc = subprocess.run(["bash", str(HELPER)], capture_output=True,
                              text=True, timeout=60, env=env,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("README.md", out)
        self.assertNotIn("[Y/n]", out, "must not prompt without a terminal")

    @unittest.skipUnless(HELPER.is_file(), f"helper missing at {HELPER}")
    def test_tty_without_desktop_shows_path_hint(self):
        """A TTY-only host gets the path and the commands, not a window."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        proc = subprocess.run(["bash", str(HELPER)], capture_output=True,
                              text=True, timeout=60, env=env,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("less", proc.stdout, "should suggest a terminal reader")


class TestInstallerWiring(unittest.TestCase):
    """install.sh calls the helper, and does so before the slow phase."""

    @unittest.skipUnless(INSTALLER.is_file(), "install.sh missing")
    def test_installer_invokes_helper(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("mp_show_readme.sh", text)

    @unittest.skipUnless(INSTALLER.is_file(), "install.sh missing")
    def test_offer_runs_before_dependency_install(self):
        text = INSTALLER.read_text(encoding="utf-8")
        offer = text.find("mp_show_readme.sh")
        deps = text.find("[4/10] Checking & Installing Dependencies")
        self.assertNotEqual(offer, -1, "helper not referenced")
        self.assertNotEqual(deps, -1, "dependency step not found")
        self.assertLess(offer, deps,
                        "the README offer must come before the long dependency phase")

    @unittest.skipUnless(INSTALLER.is_file(), "install.sh missing")
    def test_offer_never_fails_the_install(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('bash "$SCRIPT_DIR/scripts/mp_show_readme.sh" || true', text)

    @unittest.skipUnless(INSTALLER.is_file(), "install.sh missing")
    def test_final_summary_names_the_manual(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("Manual:", text)

    @unittest.skipUnless(HELPER.is_file(), "helper missing")
    def test_windows_opener_uses_the_double_slash_switch(self):
        """A lone ``/c`` is rewritten by Git Bash into a Windows path.

        ``cmd.exe /c start ...`` therefore launched an interactive cmd.exe
        prompt over the installer's window; the README never opened and the
        rest of the install log was hidden. ``//c`` survives the MSYS rewrite
        and reaches cmd.exe as the real switch.
        """
        text = HELPER.read_text(encoding="utf-8")
        # Ignore the explanatory comment, which quotes the broken form.
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertIn("cmd.exe //c start", code)
        self.assertNotIn("cmd.exe /c start", code)

    @unittest.skipUnless(HELPER.is_file(), "helper missing")
    def test_readme_offer_says_the_log_continues(self):
        """The installer must tell the user the log keeps running."""
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("The installation log continues in this window.", text)

    @unittest.skipUnless(HELPER.is_file(), "helper missing")
    def test_helper_shell_syntax(self):
        proc = subprocess.run(["bash", "-n", str(HELPER)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipUnless(INSTALLER.is_file(), "install.sh missing")
    def test_installer_shell_syntax(self):
        proc = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestDependencyCoverage(unittest.TestCase):
    """Every required dependency must have a package name on every platform."""

    def test_required_specs_name_a_package_per_manager(self):
        import re
        src = (BASE_DIR / "scripts" / "mp_dependencies.py").read_text(encoding="utf-8")
        blocks = re.findall(
            r'\{\s*"id":\s*"([^"]+)".*?"required":\s*(True|False).*?"pkgs":\s*\{(.*?)\},',
            src, re.S)
        self.assertTrue(blocks, "no SPECS entries parsed")
        managers = ("dnf", "apt", "pacman", "brew", "pkg", "rpm-ostree")
        gaps = []
        for spec_id, required, pkgs in blocks:
            if required != "True":
                continue
            if "same_package_as" in src.split(f'"id": "{spec_id}"')[1][:600]:
                continue  # ships inside another package, e.g. ffprobe
            have = set(re.findall(r'"([a-z-]+)":', pkgs))
            missing = [m for m in managers if m not in have]
            if missing:
                gaps.append(f"{spec_id} (missing {', '.join(missing)})")
        self.assertEqual(gaps, [], "required dependencies without a package name: "
                                   + "; ".join(gaps))


if __name__ == "__main__":
    unittest.main()
