"""Tests for the MP Dev Utility (Linux packaging, amd64 + arm64).

The utility lives in the BASH_SCRIPTS mirror, so these tests skip cleanly when
it is not present next to the repository. Every test runs the real script and
inspects real output; nothing is mocked.
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BASH_SCRIPTS = Path(os.environ.get("MP_BASH_SCRIPTS", str(BASE_DIR.parent / "Documents" / "BASH_SCRIPTS")))
UTILITY = BASH_SCRIPTS / "MP_Dev_Utility.sh"

HAVE_BASH = shutil.which("bash") is not None
HAVE_AR = shutil.which("ar") is not None
HAVE_TAR = shutil.which("tar") is not None

BASH = shutil.which("bash") or "bash"


@unittest.skipUnless(UTILITY.is_file(), f"packaging utility not found at {UTILITY}")
class TestPackageDevUtility(unittest.TestCase):
    """Behavioural tests against the real packaging utility."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mp_dev_utility_test_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def run_util(self, *args, expect=0):
        proc = subprocess.run(
            [BASH, str(UTILITY), *args],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if expect == 0:
            self.assertEqual(
                proc.returncode, 0,
                f"utility failed ({proc.returncode})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )
        else:
            self.assertNotEqual(proc.returncode, 0, "utility was expected to fail")
        return proc

    def source_tree(self, version="0.4.3 Stable (Liquid)", name="src"):
        root = self.tmp / name
        if root.exists():
            return root
        (root / "scripts").mkdir(parents=True)
        (root / "assets").mkdir()
        (root / "bin").mkdir()
        (root / "VERSION").write_text(version + "\n")
        (root / "Mix_Archive_Manager.sh").write_text("#!/usr/bin/env bash\necho manager\n")
        (root / "manager.sh").write_text("#!/usr/bin/env bash\necho manager\n")
        (root / "MP_Gui.sh").write_text("#!/usr/bin/env bash\necho gui\n")
        (root / "LICENSE").write_text("MIT\n")
        (root / "config.env").write_text("SECRET_TOKEN=do-not-ship\n")
        (root / "scripts" / "helper.sh").write_text("#!/usr/bin/env bash\necho h\n")
        (root / "assets" / "mp_liquid_glass_icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        (root / "assets" / "mp_liquid_glass_icon.svg").write_text("<svg/>")
        # Noise that must never be packaged.
        (root / "MIX_ARCHIVE").mkdir()
        (root / "MIX_ARCHIVE" / "big.flac").write_bytes(b"x" * 64)
        (root / "logs").mkdir()
        (root / "logs" / "run.log").write_text("noise\n")
        (root / "scripts" / "__pycache__").mkdir()
        (root / "scripts" / "__pycache__" / "junk.pyc").write_bytes(b"junk")
        return root

    @staticmethod
    def elf_bytes(machine):
        # Minimal ELF64 header: e_ident, e_type EXEC, e_machine, e_version.
        ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
        return ident + bytes([2, 0]) + bytes([machine & 0xFF, machine >> 8]) + b"\x00" * 32

    # -- architecture selection -------------------------------------------
    def test_arch_all_builds_both_debs(self):
        src = self.source_tree()
        out = self.tmp / "out_both"
        proc = self.run_util(
            "--source", str(src), "--output", str(out),
            "--targets", "deb", "--arch", "all", "--yes",
        )
        self.assertIn("Arches:  amd64 arm64", proc.stdout)
        self.assertTrue((out / "mp-mix-manager_0.4.3_amd64.deb").is_file())
        self.assertTrue((out / "mp-mix-manager_0.4.3_arm64.deb").is_file())

    def test_arch_arm64_only(self):
        src = self.source_tree()
        out = self.tmp / "out_arm_only"
        self.run_util(
            "--source", str(src), "--output", str(out),
            "--targets", "deb,portable", "--arch", "arm64", "--yes",
        )
        self.assertTrue((out / "mp-mix-manager_0.4.3_arm64.deb").is_file())
        self.assertTrue((out / "MP_Mix_Manager_0.4.3_arm64_portable.tar.gz").is_file())
        self.assertFalse((out / "mp-mix-manager_0.4.3_amd64.deb").exists())

    def test_invalid_arch_fails(self):
        proc = self.run_util("--arch", "sparc", expect=1)
        self.assertIn("Unsupported architecture", proc.stderr)

    def test_arch_all_portable_and_arch_package(self):
        src = self.source_tree()
        out = self.tmp / "out_formats"
        self.run_util(
            "--source", str(src), "--output", str(out),
            "--targets", "portable,arch", "--arch", "all", "--yes",
        )
        self.assertTrue((out / "MP_Mix_Manager_0.4.3_amd64_portable.tar.gz").is_file())
        self.assertTrue((out / "MP_Mix_Manager_0.4.3_arm64_portable.tar.gz").is_file())

        with tarfile.open(out / "MP_Mix_Manager_0.4.3-1-aarch64.pkg.tar.zst") as tf:
            pkginfo = tf.extractfile(".PKGINFO").read().decode()
        self.assertIn("arch = aarch64", pkginfo)
        with tarfile.open(out / "MP_Mix_Manager_0.4.3-1-x86_64.pkg.tar.zst") as tf:
            pkginfo = tf.extractfile(".PKGINFO").read().decode()
        self.assertIn("arch = x86_64", pkginfo)

    # -- artifact structure ----------------------------------------------
    @unittest.skipUnless(HAVE_AR and HAVE_TAR, "ar and tar are required")
    def test_deb_metadata_per_arch(self):
        src = self.source_tree()
        out = self.tmp / "out_deb_meta"
        self.run_util(
            "--source", str(src), "--output", str(out),
            "--targets", "deb", "--arch", "all", "--yes",
        )
        for arch in ("amd64", "arm64"):
            deb = out / f"mp-mix-manager_0.4.3_{arch}.deb"
            work = self.tmp / f"extract_{arch}"
            work.mkdir(exist_ok=True)
            subprocess.run(["ar", "x", str(deb)], cwd=work, check=True)

            members = subprocess.run(
                ["ar", "t", str(deb)], capture_output=True, text=True, check=True
            ).stdout.split()
            self.assertEqual(members, ["debian-binary", "control.tar.xz", "data.tar.xz"])

            control = subprocess.run(
                ["tar", "-xJf", "control.tar.xz", "-O", "./control"],
                cwd=work, capture_output=True, text=True, check=True,
            ).stdout
            self.assertIn("Package: mp-mix-manager", control)
            self.assertIn(f"Architecture: {arch}", control)
            self.assertIn("Version: 0.4.3", control)
            self.assertIn("Section: sound", control)

            note = subprocess.run(
                ["tar", "-xJf", "data.tar.xz", "-O",
                 "./usr/share/doc/mp-mix-manager/architecture.txt"],
                cwd=work, capture_output=True, text=True, check=True,
            ).stdout
            self.assertIn(f"Package architecture: {arch}", note)

            data_list = subprocess.run(
                ["tar", "-tJf", "data.tar.xz"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            for expected in (
                "./opt/mp-mix-manager/manager.sh",
                "./usr/bin/mp-mix-manager",
                "./usr/share/applications/org.mplanetarian.MPMixManager.desktop",
            ):
                self.assertIn(expected, data_list)
            # Credentials and build noise must never be shipped.
            self.assertFalse(any(n.endswith("/config.env") for n in data_list))
            self.assertFalse(any("MIX_ARCHIVE" in n for n in data_list))
            self.assertFalse(any("__pycache__" in n for n in data_list))
            self.assertFalse(any(n.startswith("./logs/") for n in data_list))

    def test_portable_launcher_is_relocatable(self):
        src = self.source_tree()
        out = self.tmp / "out_reloc"
        self.run_util(
            "--source", str(src), "--output", str(out),
            "--targets", "portable", "--arch", "arm64", "--yes",
        )
        tarball = out / "MP_Mix_Manager_0.4.3_arm64_portable.tar.gz"
        extract = self.tmp / "reloc_extract"
        if extract.exists():
            shutil.rmtree(extract)
        extract.mkdir()
        with tarfile.open(tarball) as tf:
            tf.extractall(extract)

        roots = [p for p in extract.iterdir() if p.is_dir()]
        self.assertEqual(len(roots), 1)
        root = roots[0]
        self.assertTrue((root / "ARCHITECTURE.txt").is_file())
        self.assertIn("arm64", (root / "ARCHITECTURE.txt").read_text())

        launcher = (root / "bin" / "mp-mix-manager").read_text()
        self.assertNotIn(str(self.tmp), launcher, "launcher must not bake in a build path")
        self.assertIn("ROOT", launcher)

        # The tree must work after being renamed or moved.
        moved = self.tmp / "reloc_moved"
        if moved.exists():
            shutil.rmtree(moved)
        shutil.move(str(root), str(moved))
        proc = subprocess.run(
            [str(moved / "bin" / "mp-mix-manager")],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Without a terminal the launcher prefers the GUI and falls back to the
        # console manager; either stub proves it resolved the moved tree.
        self.assertIn(proc.stdout.strip(), {"manager", "gui"})

    # -- native binary guard ---------------------------------------------
    def test_foreign_elf_rejected_for_arm64(self):
        src = self.source_tree()
        (src / "bin" / "native-helper").write_bytes(self.elf_bytes(0x3E))  # x86-64
        out = self.tmp / "out_elf"
        try:
            proc = self.run_util(
                "--source", str(src), "--output", str(out),
                "--targets", "deb", "--arch", "arm64", "--yes", expect=1,
            )
            self.assertIn("foreign-architecture", proc.stderr)
            self.assertIn("native-helper", proc.stderr)
        finally:
            (src / "bin" / "native-helper").unlink(missing_ok=True)

    def test_native_binary_accepted_for_matching_arch(self):
        src = self.source_tree()
        (src / "bin" / "native-helper").write_bytes(self.elf_bytes(0xB7))  # arm64
        out = self.tmp / "out_elf_ok"
        try:
            proc = self.run_util(
                "--source", str(src), "--output", str(out),
                "--targets", "deb", "--arch", "arm64", "--yes",
            )
            self.assertNotIn("foreign-architecture", proc.stderr)
            self.assertTrue((out / "mp-mix-manager_0.4.3_arm64.deb").is_file())
        finally:
            (src / "bin" / "native-helper").unlink(missing_ok=True)

    def test_allow_native_binaries_override(self):
        src = self.source_tree()
        (src / "bin" / "native-helper").write_bytes(self.elf_bytes(0x3E))
        out = self.tmp / "out_elf_override"
        try:
            proc = self.run_util(
                "--source", str(src), "--output", str(out),
                "--targets", "deb", "--arch", "arm64", "--yes",
                "--allow-native-binaries",
            )
            self.assertIn("Foreign-architecture binaries included", proc.stdout)
            self.assertTrue((out / "mp-mix-manager_0.4.3_arm64.deb").is_file())
        finally:
            (src / "bin" / "native-helper").unlink(missing_ok=True)

    # -- manifest and CLI ------------------------------------------------
    def test_build_manifest_lists_arches_and_artifacts(self):
        src = self.source_tree()
        out = self.tmp / "out_manifest"
        self.run_util(
            "--source", str(src), "--output", str(out),
            "--targets", "deb", "--arch", "all", "--yes",
        )
        manifest = (out / "BUILD-MANIFEST.txt").read_text()
        self.assertIn("Target arches:   amd64 arm64", manifest)
        self.assertIn("mp-mix-manager_0.4.3_amd64.deb", manifest)
        self.assertIn("mp-mix-manager_0.4.3_arm64.deb", manifest)

    def test_help_exits_cleanly(self):
        proc = self.run_util("--help")
        self.assertIn("--arch LIST", proc.stdout)
        self.assertIn("--allow-native-binaries", proc.stdout)
        self.assertIn("--targets LIST", proc.stdout)

    def test_unknown_option_fails(self):
        proc = self.run_util("--definitely-not-an-option", expect=1)
        self.assertIn("Unknown option", proc.stderr)

    def test_shell_syntax_is_valid(self):
        proc = subprocess.run([BASH, "-n", str(UTILITY)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
