#!/usr/bin/env python3
"""The install tree is resolved at launch, never hard-coded.

These tests cover scripts/lib/mp_install_dir.sh, bin/mp-desktop-exec, and the
Linux .desktop entries that call the helper, so a moved or renamed tree, or a
new version number, keeps working.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB_SH = ROOT / "scripts" / "lib" / "mp_install_dir.sh"
LIB_PY = ROOT / "scripts" / "lib" / "mp_install_dir.py"
HELPER = ROOT / "bin" / "mp-desktop-exec"

sys.path.insert(0, str(ROOT / "scripts" / "lib"))

DESKTOP_ENTRIES = (
    ROOT / "MP_Liquid.desktop",
    ROOT / "desktop" / "MP_Liquid_Glass_Audio_Player.desktop",
    ROOT / "desktop" / "Kill_All_MP_Audio_Players.desktop",
    ROOT / "desktop" / "Mix_Archive_Manager.desktop",
    ROOT / "Mix_Archive_Manager.desktop",
)


class TestInstallDirResolver(unittest.TestCase):
    def test_scripts_do_not_hard_code_a_home_or_version_path(self):
        """No launcher may point at /var/home/<user>/MP...vX.Y again."""
        offenders = []
        candidates = [
            ROOT / "Mix_Archive_Manager.sh",
            ROOT / "scripts" / "launch_liquid_glass.sh",
            ROOT / "scripts" / "launch-liquid-glass",
            ROOT / "bin" / "mix-archive-manager",
            ROOT / "bin" / "launch-manager",
            ROOT / "bin" / "launch-manager-fullscreen",
            *DESKTOP_ENTRIES,
        ]
        for path in candidates:
            text = path.read_text(encoding="utf-8")
            for needle in ("/var/home/mplanetarian/MP", "/home/mplanetarian/MP"):
                if needle in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {needle}")
        self.assertEqual(offenders, [])

    def test_shell_resolver_finds_the_tree_through_install_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "RenamedTree"
            (tree / "scripts" / "lib").mkdir(parents=True)
            (tree / "Mix_Archive_Manager.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            shutil.copy2(LIB_SH, tree / "scripts" / "lib" / "mp_install_dir.sh")
            config = Path(tmp) / ".config" / "mix-manager"
            config.mkdir(parents=True)
            (config / "install_dir").write_text(f"{tree}\n", encoding="utf-8")
            env = dict(os.environ, HOME=tmp)
            # The test runner pins MP_MIX_MANAGER_DIR at the deployed tree;
            # these cases deliberately resolve a different temp tree.
            env.pop("MP_MIX_MANAGER_DIR", None)
            res = subprocess.run(
                ["bash", "-c", f'. "{tree}/scripts/lib/mp_install_dir.sh"; mp_find_install_dir'],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), str(tree))

    def test_shell_resolver_follows_the_manager_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "AnyName"
            (tree / "scripts" / "lib").mkdir(parents=True)
            (tree / "Mix_Archive_Manager.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            shutil.copy2(LIB_SH, tree / "scripts" / "lib" / "mp_install_dir.sh")
            local_bin = Path(tmp) / ".local" / "bin"
            local_bin.mkdir(parents=True)
            (local_bin / "manager").symlink_to(tree / "Mix_Archive_Manager.sh")
            env = dict(os.environ, HOME=tmp)
            # The test runner pins MP_MIX_MANAGER_DIR at the deployed tree;
            # these cases deliberately resolve a different temp tree.
            env.pop("MP_MIX_MANAGER_DIR", None)
            res = subprocess.run(
                ["bash", "-c", f'. "{tree}/scripts/lib/mp_install_dir.sh"; mp_find_install_dir'],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), str(tree))

    def test_python_resolver_matches(self):
        from mp_install_dir import find_install_dir

        found = find_install_dir()
        self.assertIsNotNone(found)
        self.assertEqual(Path(found).resolve(), ROOT.resolve())

    def test_desktop_exec_launches_the_named_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "Tree"
            (tree / "scripts").mkdir(parents=True)
            (tree / "Mix_Archive_Manager.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            script = tree / "scripts" / "hello.sh"
            script.write_text('#!/bin/sh\necho "ARGV:$*"\n', encoding="utf-8")
            script.chmod(0o755)
            lib_dir = Path(tmp) / ".local" / "bin"
            lib_dir.mkdir(parents=True)
            shutil.copy2(HELPER, lib_dir / "mp-desktop-exec")
            (lib_dir / "mp-desktop-exec").chmod(0o755)
            config = Path(tmp) / ".config" / "mix-manager"
            (config / "lib").mkdir(parents=True)
            (config / "install_dir").write_text(f"{tree}\n", encoding="utf-8")
            shutil.copy2(LIB_SH, config / "lib" / "mp_install_dir.sh")
            env = dict(os.environ, HOME=tmp)
            # The test runner pins MP_MIX_MANAGER_DIR at the deployed tree;
            # these cases deliberately resolve a different temp tree.
            env.pop("MP_MIX_MANAGER_DIR", None)
            res = subprocess.run(
                [str(lib_dir / "mp-desktop-exec"), "hello.sh", "one", "two"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "ARGV:one two")

    def test_desktop_entries_use_the_helper_not_a_path(self):
        for path in DESKTOP_ENTRIES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("mp-desktop-exec", text)
                self.assertNotIn("Path=/var/home", text)
                self.assertNotIn("Icon=/var/home", text)


if __name__ == "__main__":
    unittest.main()
