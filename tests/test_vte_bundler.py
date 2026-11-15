#!/usr/bin/env python3
"""Tests for the VTE 2.91 bundler (scripts/mp_vte_local.sh).

Bazzite and other immutable images do not ship vte291, and a user cannot layer
it on without a reboot. MP Liquid Glass embeds a Vte terminal, so without the
typelib both backends fail with "Namespace Vte not available" and the window
never opens. `mp_vte_local.sh` downloads the distribution's own package and
unpacks the two files into ~/.local/lib/mp-liquid-glass.

These tests pin the contract the launchers depend on:

  * `--check` is a silent, exit-code-only probe;
  * `--print-env` emits exactly the two exports, and nothing when absent;
  * the install path is wired into install.sh;
  * the launchers and local_vte.py agree on the directory;
  * a failure is reported, never silently treated as success.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BASE_DIR / "scripts" / "mp_vte_local.sh"
INSTALL = BASE_DIR / "install.sh"
LAUNCH = BASE_DIR / "scripts" / "launch_liquid_glass.sh"
LOCAL_VTE = BASE_DIR / "scripts" / "local_vte.py"

HAVE_BASH = bool(os.environ.get("BASH") or True)


def run(args, env=None, timeout=120):
    """Run the bundler with a clean-ish environment and capture both streams."""
    base = dict(os.environ)
    base.pop("MP_VTE_FORCE", None)
    base.pop("MP_VTE_NO_NETWORK", None)
    if env:
        base.update(env)
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(BASE_DIR),
        env=base,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


class TestVteBundlerContract(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file(), f"missing {SCRIPT}")
        self.assertTrue(os.access(SCRIPT, os.X_OK), "mp_vte_local.sh must be executable")

    def test_bash_syntax_is_valid(self):
        proc = subprocess.run(["bash", "-n", str(SCRIPT)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))

    def test_check_is_silent_and_exit_code_only(self):
        """--check must not print: callers use it as a boolean."""
        rc, out, err = run(["--check"])
        self.assertIn(rc, (0, 1), "--check documents 0/1 only")
        self.assertEqual(out.strip(), "", f"--check wrote to stdout: {out!r}")

    def test_print_env_is_empty_when_bundle_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = run(["--print-env"], env={"MP_VTE_BASE": tmp})
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "",
                             "an absent bundle must export nothing, not a broken path")

    def test_print_env_exports_both_variables_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib"
            ty = Path(tmp) / "girepository-1.0"
            lib.mkdir()
            ty.mkdir()
            (ty / "Vte-2.91.typelib").write_bytes(b"\x00")
            (lib / "libvte-2.91.so.0.1").write_bytes(b"\x00")
            (lib / "libvte-2.91.so.0").symlink_to("libvte-2.91.so.0.1")
            rc, out, _ = run(["--print-env"], env={"MP_VTE_BASE": tmp})
            self.assertEqual(rc, 0)
            self.assertIn("LD_LIBRARY_PATH", out)
            self.assertIn("GI_TYPELIB_PATH", out)
            self.assertIn(tmp, out)
            # Both must be eval-able shell.
            proc = subprocess.run(["bash", "-c", f"{out}\nprintf '%s' \"$GI_TYPELIB_PATH\""],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env={**os.environ, "MP_VTE_BASE": tmp}, check=False)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("girepository-1.0", proc.stdout.decode())

    def test_offline_mode_refuses_instead_of_pretending(self):
        """MP_VTE_NO_NETWORK must fail loudly, not report a fake success."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = run(["--install"],
                               env={"MP_VTE_BASE": tmp, "MP_VTE_NO_NETWORK": "1"})
            self.assertEqual(rc, 1, "offline install must report failure")
            self.assertIn("Offline", out + err)

    def test_unknown_option_is_an_error(self):
        rc, _, err = run(["--nonsense"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown option", err)


class TestVteWiring(unittest.TestCase):
    """The installer and every launcher must agree on the bundle location."""

    BASE_RE = re.compile(r"mp-liquid-glass")

    def test_installer_invokes_the_bundler(self):
        text = INSTALL.read_text(encoding="utf-8")
        self.assertIn("scripts/mp_vte_local.sh", text,
                      "install.sh must call the VTE bundler")
        # It must ask before downloading, and must not abort the install when
        # the download fails (a network hiccup is not a fatal install error).
        self.assertIn("--check", text)
        self.assertIn("--install", text)

    def test_installer_treats_vte_failure_as_non_fatal(self):
        text = INSTALL.read_text(encoding="utf-8")
        block = text.split("mp_vte_local.sh", 1)[1][:2000]
        self.assertIn("case", block, "the installer must branch on the bundler's exit code")
        self.assertIn("could not be installed automatically", block,
                      "a failed download should warn, not abort")

    def test_launchers_use_the_same_directory(self):
        for path in (LAUNCH, BASE_DIR / "MP_Gui.sh"):
            self.assertTrue(path.is_file(), f"missing {path}")
            text = path.read_text(encoding="utf-8")
            self.assertIn("mp-liquid-glass", text,
                          f"{path.name} must put the bundle on the loader path")

    def test_python_helper_uses_the_same_directory(self):
        text = LOCAL_VTE.read_text(encoding="utf-8")
        self.assertIn('"mp-liquid-glass"', text)
        self.assertIn("MP_VTE_REEXEC", text)
        # The re-exec guard must prevent an infinite loop.
        self.assertIn("def reexec_with_local_vte", text)

    def test_bundler_ships_the_runtime_sibling(self):
        """vte291 links libsimdutf on current Fedora; it must be fetched too."""
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("simdutf", text,
                      "the bundler must fetch libsimdutf or the library will not load")
        self.assertIn("ldd", text,
                      "the bundler must verify SONAME resolution rather than assume it")

    def test_bundler_rejects_foreign_architecture(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("i686", text,
                      "an i686 RPM must never overwrite the x86_64 library")


if __name__ == "__main__":
    unittest.main()
