#!/usr/bin/env python3
"""An offline share must never abort a Music Folders launch.

Screenshot_20261118_152207.png: a share whose disk was pulled on the server left
a dead rclone FUSE mountpoint behind. Option 1 (ALL folders) then died inside
pathlib's mkdir() with an unhandled OSError instead of skipping that folder.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import music_folders_manager as mfm


def _folder(fid="MUSIC003", share="EXT_004_P1"):
    return {
        "type": "smb", "id": fid, "name": fid, "host": "192.0.2.13",
        "share": share, "subpath": "MUSIC003" if fid == "MUSIC003" else "", "user": "",
    }


def _config(root):
    return {
        "smb_user": "", "smb_password": "", "legacy_host": "",
        "mount_root": Path(root) / "MUSIC_SMB", "playlists_dir": Path(root),
        "base_dir": Path(root),
    }


class TestDeadMountDetection(unittest.TestCase):
    def test_mount_lookup_follows_a_symlinked_parent(self):
        # Fedora layouts symlink /home -> /var/home, so /proc/mounts records the
        # resolved path while config.env points at the symlinked one.
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "var_home" / "MUSIC_SMB"
            real.mkdir(parents=True)
            link = Path(tmp) / "home"
            link.symlink_to(Path(tmp) / "var_home")
            mounted = real / "MUSIC002"
            mounted.mkdir()
            entry = f":smb{{x}}:MUSIC002 {mounted.resolve()} fuse.rclone rw 0 0\n"
            with patch("builtins.open", return_value=io.StringIO(entry)):
                self.assertTrue(mfm._is_mountpoint(link / "MUSIC_SMB" / "MUSIC002"))
                self.assertFalse(mfm._is_mountpoint(link / "MUSIC_SMB" / "MUSIC003"))


class TestDeadMountRecovery(unittest.TestCase):
    def test_mkdir_recovers_after_a_dead_fuse_mount_is_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "MUSIC_SMB" / "MUSIC003"
            md.mkdir(parents=True)
            folder, config = _folder(), _config(tmp)
            with patch.object(mfm, "_is_mountpoint", return_value=True), \
                 patch.object(mfm.Path, "mkdir", side_effect=FileExistsError(17, "File exists", str(md))), \
                 patch.object(mfm, "disconnect_folder", return_value=(True, "Unmounted")) as release:
                ok, msg = mfm._prepare_mount_dir(md, folder, config)
            release.assert_called_once()
            # Second mkdir still raises because the patch is unconditional, but
            # the failure must be a message, never an exception.
            self.assertFalse(ok)
            self.assertIn("Cannot prepare mount point", msg)

    def test_a_plain_file_mountpoint_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "MUSIC_SMB" / "MUSIC003"
            md.parent.mkdir(parents=True)
            md.write_text("not a directory")
            with patch.object(mfm, "disconnect_folder") as release:
                ok, msg = mfm._prepare_mount_dir(md, _folder(), _config(tmp))
            release.assert_not_called()
            self.assertFalse(ok)
            self.assertIn("Cannot prepare mount point", msg)


class TestConnectFolderOffline(unittest.TestCase):
    def test_unresponsive_share_reports_offline_and_leaves_no_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, config = _folder(), _config(tmp)
            calls = {"n": 0}

            def mountpoint(p):
                # Not mounted at first, then the rclone mount appears but never answers.
                calls["n"] += 1
                return calls["n"] > 1

            with patch.object(mfm, "access_path", return_value=(None, "OFFLINE")), \
                 patch.object(mfm, "_rclone_remote", return_value=":smb,host=x:MUSIC003"), \
                 patch.object(mfm, "_is_mountpoint", side_effect=mountpoint), \
                 patch.object(mfm, "subprocess") as proc, \
                 patch.object(mfm, "_listing_responds", return_value=False), \
                 patch.object(mfm, "disconnect_folder", return_value=(True, "Unmounted")) as release:
                proc.Popen.return_value = None
                ok, msg = mfm.connect_folder(folder, config, interactive=False)
            self.assertFalse(ok)
            self.assertIn("offline", msg.lower())
            release.assert_called_once()

    def test_one_broken_folder_does_not_stop_the_others(self):
        good, bad = _folder("DATAMAC1", "DATAMAC1"), _folder("MUSIC003", "EXT_004_P1")

        def connect(f, config, interactive=True):
            if f["id"] == "MUSIC003":
                raise OSError(107, "Transport endpoint is not connected")
            return True, "Mounted"

        with patch.object(mfm, "connect_folder", side_effect=connect):
            results = mfm.ensure_folders_connected([good, bad], _config("/tmp"), interactive=False)
        self.assertEqual([f["id"] for f, ok, _ in results if ok], ["DATAMAC1"])
        self.assertIn("Transport endpoint is not connected", results[1][2])


if __name__ == "__main__":
    unittest.main()
