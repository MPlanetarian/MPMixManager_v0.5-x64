#!/usr/bin/env python3
"""The console launcher must open even when archive storage is offline.

`bin/mix-archive-manager` used to wait 15 s for MIX_ARCHIVE_DIR and then exit 1
when the drive was not mounted, so a forgotten mount blocked the whole manager.
These tests run the real launcher against a throw-away tree and a stub manager.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "bin" / "mix-archive-manager"


def _make_tree(root: Path, archive_dir: Optional[str]) -> Path:
    """Create a fake install tree with a manager that reports its cwd."""
    tree = root / "InstallTree"
    (tree / "bin").mkdir(parents=True)
    dest = tree / "bin" / "mix-archive-manager"
    dest.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    dest.chmod(0o755)
    (tree / "Mix_Archive_Manager.sh").write_text(
        "#!/usr/bin/env bash\n"
        'echo "MANAGER STARTED pwd=$(pwd) args=$*"\n',
        encoding="utf-8",
    )
    if archive_dir is not None:
        (tree / "config.env").write_text(
            f'MIX_ARCHIVE_DIR="{archive_dir}"\n'
            'MIX_ARCHIVE_CONFIGURED="true"\n',
            encoding="utf-8",
        )
    return tree


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["MP_ARCHIVE_MOUNT_WAIT"] = "0"
    env.pop("MIX_ARCHIVE_DIR", None)
    return subprocess.run(
        ["bash", str(tree / "bin" / "mix-archive-manager"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


class TestLauncherWithUnmountedStorage(unittest.TestCase):
    def test_missing_archive_starts_in_the_application_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _make_tree(Path(tmp), "/definitely/not/mounted/MIX_ARCHIVE")
            proc = _run(tree)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("MANAGER STARTED", proc.stdout)
            self.assertIn(f"pwd={tree.resolve()}", proc.stdout)
            self.assertIn("is not mounted", proc.stdout)

    def test_mounted_archive_still_starts_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "MIX_ARCHIVE"
            archive.mkdir()
            tree = _make_tree(root, str(archive))
            proc = _run(tree)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(f"pwd={archive.resolve()}", proc.stdout)

    def test_no_archive_configuration_starts_in_the_application_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _make_tree(Path(tmp), None)
            proc = _run(tree)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(f"pwd={tree.resolve()}", proc.stdout)


if __name__ == "__main__":
    unittest.main()
