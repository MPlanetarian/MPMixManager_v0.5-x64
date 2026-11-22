#!/usr/bin/env python3
"""rclone must be found even when the launcher's PATH does not have it.

A .desktop launch inherits the systemd user PATH
(/usr/local/sbin:/usr/local/bin:/usr/bin), so `shutil.which("rclone")` returned
None although rclone was installed in ~/.local/bin. Every SMB share then
reported "rclone is not installed or SMB host is not configured in config.env."
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import music_folders_manager as mfm


def _config(root, rclone_path=""):
    return {
        "smb_user": "", "smb_password": "", "legacy_host": "",
        "rclone_path": rclone_path,
        "mount_root": Path(root) / "MUSIC_SMB", "playlists_dir": Path(root),
        "base_dir": Path(root),
    }


def _folder():
    return {
        "type": "smb", "id": "DATAMAC1", "name": "DATAMAC1", "host": "192.0.2.10",
        "share": "DATAMAC1", "subpath": "", "user": "",
    }


def _fake_rclone(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class TestRcloneLookup(unittest.TestCase):
    def setUp(self):
        self._saved = (mfm._RCLONE_BIN_CACHE, mfm._RCLONE_BIN_CHECKED)
        mfm._RCLONE_BIN_CACHE = None
        mfm._RCLONE_BIN_CHECKED = False

    def tearDown(self):
        mfm._RCLONE_BIN_CACHE, mfm._RCLONE_BIN_CHECKED = self._saved

    def test_configured_path_wins_when_path_has_no_rclone(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = _fake_rclone(Path(tmp) / "rclone")
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False), \
                 patch.object(mfm.shutil, "which", return_value=None), \
                 patch.object(mfm, "load_music_config", return_value=_config(tmp, str(binary))):
                self.assertEqual(mfm.rclone_bin(), str(binary))

    def test_falls_back_to_the_user_bin_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = _fake_rclone(Path(tmp) / ".local" / "bin" / "rclone")
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "MUSIC_RCLONE_PATH": ""}, clear=False), \
                 patch.object(mfm.shutil, "which", return_value=None), \
                 patch.object(mfm, "load_music_config", return_value=_config(tmp)), \
                 patch.object(mfm.Path, "home", return_value=Path(tmp)):
                self.assertEqual(mfm.rclone_bin(), str(binary))

    def test_missing_rclone_is_reported_on_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, config = _folder(), _config(tmp)
            with patch.object(mfm, "rclone_bin", return_value=None), \
                 patch.object(mfm, "access_path", return_value=(None, "OFFLINE")), \
                 patch.object(mfm, "_rclone_remote") as remote:
                ok, msg = mfm.connect_folder(folder, config, interactive=False)
            remote.assert_not_called()
            self.assertFalse(ok)
            self.assertIn("rclone is not installed", msg)
            self.assertNotIn("config.env", msg)


if __name__ == "__main__":
    unittest.main()
