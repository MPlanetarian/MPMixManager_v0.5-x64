#!/usr/bin/env python3
"""Tests for exporting MP Liquid Mixer recordings into MIX_ARCHIVE."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import mixer_export  # noqa: E402
import mixer_recording  # noqa: E402


class TestArchiveResolution(unittest.TestCase):
    def test_no_setting_is_reported_as_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "app"
            config = Path(tmp) / "cfg"
            base.mkdir()
            config.mkdir()
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": ""}, clear=False):
                result = mixer_export.resolve_primary_archive(base, config)
            self.assertIsNone(result["path"])
            self.assertEqual(result["problem"], "unset")

    def test_env_archive_wins_and_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "MIX_ARCHIVE"
            archive.mkdir()
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(archive)}, clear=False):
                result = mixer_export.resolve_primary_archive(Path(tmp) / "app", Path(tmp) / "cfg")
            self.assertEqual(result["problem"], "")
            self.assertEqual(Path(result["path"]), archive.resolve())

    def test_config_env_is_used_when_the_environment_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "app"
            base.mkdir()
            archive = Path(tmp) / "ARCHIVE"
            archive.mkdir()
            (base / "config.env").write_text(f'MIX_ARCHIVE_DIR="{archive}"\n', encoding="utf-8")
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": ""}, clear=False):
                result = mixer_export.resolve_primary_archive(base, Path(tmp) / "cfg")
            self.assertEqual(Path(result["path"]), archive.resolve())

    def test_a_configured_but_missing_path_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not-mounted"
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(missing)}, clear=False):
                result = mixer_export.resolve_primary_archive(Path(tmp) / "app", Path(tmp) / "cfg")
            self.assertIsNone(result["path"])
            self.assertEqual(result["problem"], "missing")
            self.assertIn("not-mounted", str(result["configured"]))


class TestExport(unittest.TestCase):
    def _layout(self, tmp):
        archive = Path(tmp) / "MIX_ARCHIVE"
        archive.mkdir()
        base = Path(tmp) / "app"
        records = base / "MP_AUDIOWAV_REC_OUTPUT"
        records.mkdir(parents=True)
        config = Path(tmp) / "cfg"
        config.mkdir()
        return archive, base, records, config

    def test_wav_goes_to_the_root_and_flac_to_the_converted_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, base, records, config = self._layout(tmp)
            wav = records / "MP_LIQUID_MIXER_REC_20261119_010000.wav"
            wav_part = records / "MP_LIQUID_MIXER_REC_20261119_010000_PART_2.wav"
            flac = records / "MP_LIQUID_MIXER_REC_20261119_010001.flac"
            log = records / "liquid_mixer_recording.log"
            for path in (wav, wav_part, flac):
                path.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(archive)}, clear=False):
                result = mixer_export.export_recordings(base=base, config_dir=config)
            self.assertEqual(result["action"], "moved")
            self.assertEqual(result["wav"], 2)
            self.assertEqual(result["flac"], 1)
            self.assertTrue((archive / wav.name).is_file())
            self.assertTrue((archive / wav_part.name).is_file())
            self.assertTrue((archive / "FLAC_CONVERTED_OUTPUTS" / flac.name).is_file())
            self.assertTrue(log.is_file(), "the recording log must stay put")
            self.assertFalse(wav.exists())
            self.assertFalse(flac.exists())

    def test_recordings_left_in_the_install_tree_are_exported_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, base, records, config = self._layout(tmp)
            old = records / "old.wav"
            old.write_bytes(b"audio")
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(archive)}, clear=False):
                result = mixer_export.export_recordings(base=base, config_dir=config)
            self.assertEqual(result["action"], "moved")
            self.assertTrue((archive / "old.wav").is_file())

    def test_a_name_clash_gets_a_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, base, records, config = self._layout(tmp)
            (archive / "same.wav").write_bytes(b"existing")
            (records / "same.wav").write_bytes(b"new")
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(archive)}, clear=False):
                mixer_export.export_recordings(base=base, config_dir=config)
            self.assertEqual((archive / "same.wav").read_bytes(), b"existing")
            self.assertEqual((archive / "same_2.wav").read_bytes(), b"new")

    def test_no_archive_moves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, base, records, config = self._layout(tmp)
            wav = records / "keep.wav"
            wav.write_bytes(b"audio")
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": ""}, clear=False):
                result = mixer_export.export_recordings(base=base, config_dir=config)
            self.assertEqual(result["action"], "no_archive")
            self.assertTrue(wav.is_file())
            self.assertFalse(archive.joinpath("keep.wav").exists())

    def test_a_missing_archive_moves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, base, records, config = self._layout(tmp)
            wav = records / "keep.wav"
            wav.write_bytes(b"audio")
            missing = Path(tmp) / "unmounted"
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(missing)}, clear=False):
                result = mixer_export.export_recordings(base=base, config_dir=config)
            self.assertEqual(result["action"], "missing_archive")
            self.assertTrue(wav.is_file())

    def test_empty_export_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, base, records, config = self._layout(tmp)
            with patch.dict(os.environ, {"MIX_ARCHIVE_DIR": str(archive)}, clear=False):
                result = mixer_export.export_recordings(base=base, config_dir=config)
            self.assertEqual(result["action"], "empty")


class TestExportNotifications(unittest.TestCase):
    def test_no_archive_message_asks_for_one(self):
        with patch.object(mixer_export, "notify", return_value=True) as notify:
            mixer_export.notify_export({"action": "no_archive"})
        message = notify.call_args.args[1]
        self.assertIn("Set up a primary MIX_ARCHIVE folder", message)

    def test_success_message_names_the_archive_path(self):
        archive = Path("/tmp/MIX_ARCHIVE")
        with patch.object(mixer_export, "notify", return_value=True) as notify:
            mixer_export.notify_export({
                "action": "moved", "archive": archive, "wav": 2, "flac": 1, "failed": [],
            })
        message = notify.call_args.args[1]
        self.assertIn("All recorded mixes have been moved", message)
        self.assertIn(str(archive), message)
        self.assertIn("2 WAV", message)
        self.assertIn("1 FLAC", message)

    def test_the_tray_notify_helper_is_used(self):
        with patch("mp_system_tray.notify", return_value=True) as tray_notify:
            self.assertTrue(mixer_export.notify("title", "message"))
        tray_notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
