"""The conversion suite: format registry, capability matrix, stats and metadata.

The output format list has to match what this FFmpeg build can actually write,
the statistics table has to add up, and a conversion has to carry its tags,
disc numbers and cover art into the file. These tests pin all three.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audio_conversion_suite as suite

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _ffmpeg_ok(args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True).returncode == 0


class OutputFormatRegistryTests(unittest.TestCase):
    def test_every_output_has_a_unique_key(self):
        keys = [f.key for f in suite.OUTPUT_FORMATS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_output_extension_is_water_under_the_player(self):
        # A converter output the player cannot list would be invisible.
        from audio_formats import AUDIO_EXTENSION_SET
        for fmt in suite.OUTPUT_FORMATS:
            self.assertIn(f".{fmt.extension}", AUDIO_EXTENSION_SET,
                          f"{fmt.key} writes .{fmt.extension}, which the player does not list")

    def test_formats_without_an_encoder_are_not_offered(self):
        keys = {f.key for f in suite.OUTPUT_FORMATS}
        for unavailable in ("ape", "tak", "mpc", "mpp", "dsf", "dff", "ofr", "ofs", "mp1"):
            self.assertNotIn(unavailable, keys)

    def test_unwritable_reasons_explain_every_missing_target(self):
        for ext in (".ape", ".tak", ".dsf", ".dff"):
            self.assertTrue(suite.unwritable_reason(ext), ext)

    def test_count_matches_the_registry(self):
        self.assertEqual(suite.output_format_count(), len(suite.OUTPUT_FORMATS))
        self.assertGreaterEqual(suite.output_format_count(), 20)

    def test_lossless_targets_are_marked(self):
        lossless = {f.key for f in suite.OUTPUT_FORMATS if f.lossless}
        for key in ("flac", "wav", "aiff", "alac", "wv", "tta", "mka"):
            self.assertIn(key, lossless)
        for key in ("mp3", "ogg", "opus", "m4a", "ac3"):
            self.assertNotIn(key, lossless)

    def test_cover_support_matrix_is_explicit(self):
        with_cover = {f.key for f in suite.OUTPUT_FORMATS if f.supports_cover}
        self.assertEqual(with_cover, {"flac", "aiff", "mp3", "m4a", "wma", "mka"})

    def test_choices_rows_are_three_columns(self):
        for key, label, note in suite.format_choices():
            self.assertTrue(key and label)
            self.assertIsInstance(note, str)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
    def test_high_preset_writes_every_advertised_format(self):
        """The registry is a promise: each format must really be produced."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.wav"
            self.assertTrue(_ffmpeg_ok(["-f", "lavfi", "-i",
                                        "sine=frequency=440:duration=1:sample_rate=48000",
                                        "-ac", "2", "-c:a", "pcm_s24le", str(src)]))
            problems = []
            for fmt in suite.OUTPUT_FORMATS:
                dest = Path(tmp) / f"out_{fmt.key}.{fmt.extension}"
                ok, detail = suite.run_conversion(
                    suite.ConversionRequest(str(src), fmt.key, str(dest)), quiet=True)
                if not ok or not dest.is_file() or dest.stat().st_size == 0:
                    problems.append(f"{fmt.key}: {detail or 'no output'}")
            self.assertEqual(problems, [])


class CapabilityMatrixTests(unittest.TestCase):
    def test_lossy_source_to_lossless_target_warns_but_is_allowed(self):
        plan = suite.plan_conversion("song.mp3", "flac")
        self.assertTrue(plan.allowed)
        self.assertIn("cannot restore", plan.quality_note)

    def test_lossless_source_to_lossy_target_is_allowed(self):
        plan = suite.plan_conversion("mix.flac", "mp3")
        self.assertTrue(plan.allowed)

    def test_unknown_target_is_refused(self):
        plan = suite.plan_conversion("mix.flac", "not-a-format")
        self.assertFalse(plan.allowed)
        self.assertIn("not a writable output format", plan.reason)

    def test_alac_and_aac_share_an_extension_without_conflict(self):
        alac = suite.plan_conversion("song.mp3", "alac")
        aac = suite.plan_conversion("song.mp3", "m4a")
        self.assertTrue(alac.allowed and aac.allowed)
        self.assertEqual(suite.FORMAT_BY_KEY["alac"].extension, suite.FORMAT_BY_KEY["m4a"].extension)

    def test_batch_plan_covers_every_source_target_pair(self):
        sources = ["a.wav", "b.flac", "c.mp3"]
        plans, refusals = suite.batch_plan(sources, ["flac", "mp3", "bogus"])
        self.assertEqual(len(plans), 6)
        self.assertEqual(len(refusals), 3)


class HookTests(unittest.TestCase):
    def test_disc_set_fields_become_one_tag(self):
        args = suite.build_tag_args({"disc": "1", "disctotal": "2"})
        self.assertEqual(args.count("-metadata"), 1)
        self.assertIn("disc=1/2", args)

    def test_slash_form_is_understood(self):
        self.assertEqual(suite.normalize_disc("2/3", ""), {"disc": "2", "disctotal": "3"})

    def test_plain_disc_number_stays_plain(self):
        self.assertEqual(suite.normalize_disc("3", ""), {"disc": "3"})

    def test_empty_tag_values_are_skipped(self):
        args = suite.build_tag_args({"artist": "", "album": "X", "genre": None})
        self.assertNotIn("artist=", " ".join(args))
        self.assertIn("album=X", args)

    def test_custom_fields_survive_a_tag_round_trip(self):
        args = suite.build_tag_args({"ARTIST": "A", "MOOD": "uplifting"})
        self.assertIn("MOOD=uplifting", args)

    def test_cover_only_added_for_supporting_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            cover = Path(tmp) / "cover.png"
            cover.write_bytes(b"\x89PNG\r\n\x1a\n")
            src = Path(tmp) / "s.wav"
            src.write_bytes(b"RIFF")
            args = suite.build_command_for_test(src, "wav", cover) if hasattr(suite, "build_command_for_test") else \
                suite.build_ffmpeg_command(suite.ConversionRequest(
                    str(src), "wav", str(Path(tmp) / "o.wav"), front_cover=str(cover)))
            self.assertNotIn("-c:v", args)
            args = suite.build_ffmpeg_command(suite.ConversionRequest(
                str(src), "flac", str(Path(tmp) / "o.flac"), front_cover=str(cover)))
            self.assertIn("attached_pic", args)


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "conv.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows):
        self.log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_counts_group_by_format_with_a_master_total(self):
        self._write([
            {"event": "CONVERSION_COMPLETED", "format": "FLAC", "size_bytes": 100, "timestamp": "2026-01-01T00:00:00"},
            {"event": "CONVERSION_COMPLETED", "format": "FLAC", "size_bytes": 200, "timestamp": "2026-01-02T00:00:00"},
            {"event": "CONVERSION_COMPLETED", "format": "MP3", "size_bytes": 50, "timestamp": "2026-01-03T00:00:00"},
        ])
        stats = suite.read_conversion_stats(str(self.log))
        self.assertEqual(stats.counts, {"FLAC": 2, "MP3": 1})
        self.assertEqual(stats.bytes, {"FLAC": 300, "MP3": 50})
        self.assertEqual(stats.total_files, 3)
        self.assertEqual(stats.total_bytes, 350)
        self.assertEqual(stats.last_timestamp, "2026-01-03T00:00:00")

    def test_other_events_are_ignored(self):
        self._write([
            {"event": "CONVERSION_STARTED", "format": "FLAC"},
            {"event": "CONVERSION_COMPLETED", "format": "WAV", "size_bytes": 10},
        ])
        stats = suite.read_conversion_stats(str(self.log))
        self.assertEqual(stats.counts, {"WAV": 1})

    def test_malformed_lines_do_not_break_the_table(self):
        self.log.write_text('{"event":"CONVERSION_COMPLETED","format":"FLAC"}\nnot json\n', encoding="utf-8")
        stats = suite.read_conversion_stats(str(self.log))
        self.assertEqual(stats.counts, {"FLAC": 1})

    def test_empty_log_reports_a_friendly_placeholder(self):
        rendered = suite.render_stats_table(suite.ConversionStats(), {})
        self.assertIn("no completed conversions", rendered)

    def test_table_shows_master_total_row(self):
        stats = suite.ConversionStats(counts={"FLAC": 5, "WAV": 2}, bytes={"FLAC": 1024, "WAV": 2048})
        rendered = suite.render_stats_table(stats, {})
        self.assertIn("MASTER TOTAL", rendered)
        self.assertIn("7", rendered)

    def test_human_bytes_units(self):
        self.assertEqual(suite.human_bytes(512), "512 B")
        self.assertEqual(suite.human_bytes(2048), "2.0 KB")
        self.assertIn("MB", suite.human_bytes(5 * 1024 * 1024))


class FolderDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_converted_folders_and_skips_other_directories(self):
        flac = self.root / "FLAC_CONVERTED_OUTPUTS"
        flac.mkdir()
        (flac / "a.flac").write_bytes(b"x" * 10)
        (flac / "b.flac").write_bytes(b"y" * 20)
        empty = self.root / "MP3_CONVERTED_OUTPUTS"
        empty.mkdir()
        (self.root / "MIX_ARCHIVE").mkdir()

        folders = suite.find_converted_folders([self.root])
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0].count, 2)
        self.assertEqual(folders[0].total_bytes, 30)
        self.assertIn("FLAC", folders[0].label)

    def test_renders_a_row_per_folder(self):
        flac = self.root / "WAV_CONVERTED_OUTPUTS"
        flac.mkdir()
        (flac / "a.wav").write_bytes(b"x" * 10)
        rows = suite.render_folders_table(suite.find_converted_folders([self.root]), {})
        self.assertIn("[ 1]", rows)
        self.assertIn(str(flac), rows)

    def test_no_folders_reports_cleanly(self):
        self.assertIn("No converted audio folders",
                      suite.render_folders_table([], {}))

    def test_discovery_never_descends_into_converted_outputs(self):
        out = self.root / "FLAC_CONVERTED_OUTPUTS"
        nested = out / "nested"
        nested.mkdir(parents=True)
        (out / "top.flac").write_bytes(b"1")
        (nested / "deep.flac").write_bytes(b"1")
        found = suite.discover_sources([self.root])
        self.assertEqual(found, [])


class BatchDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_groups_sources_by_format(self):
        for name in ("a.wav", "b.wav", "c.flac", "d.mp3"):
            (self.root / name).write_bytes(b"x")
        (self.root / "notes.txt").write_text("nope")
        found = suite.discover_sources([self.root])
        groups = suite.group_by_format(found)
        self.assertEqual(groups["wav"], [str(self.root / "a.wav"), str(self.root / "b.wav")])
        self.assertEqual(len(groups["flac"]), 1)
        self.assertNotIn("txt", groups)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
class EndToEndConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "source.wav"
        _ffmpeg_ok(["-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
                    "-ac", "2", "-c:a", "pcm_s24le", str(self.src)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_metadata_disc_and_cover_land_in_a_flac(self):
        cover = self.dir / "front.png"
        _ffmpeg_ok(["-f", "lavfi", "-i", "color=c=red:s=300x300:d=1", "-frames:v", "1", str(cover)])
        dest = self.dir / "tagged.flac"
        meta = {"artist": "MPlanetarian", "album": "Stream of Frequency",
                "title": "Episode 081", "genre": "Trance", "disc": "1", "disctotal": "2"}
        ok, detail = suite.run_conversion(
            suite.ConversionRequest(str(self.src), "flac", str(dest),
                                    metadata=meta, front_cover=str(cover)), quiet=True)
        self.assertTrue(ok, detail)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format_tags=artist,album,title,disc,genre:stream=codec_type",
             "-of", "json", str(dest)], capture_output=True, text=True).stdout
        data = json.loads(probe)
        tags = data["format"]["tags"]
        self.assertEqual(tags["artist"], "MPlanetarian")
        self.assertEqual(tags["disc"], "1/2")
        self.assertIn("video", [s["codec_type"] for s in data["streams"]])

    def test_back_cover_and_disc_image_are_embedded(self):
        covers = []
        for name in ("front.png", "back.png", "disc.png"):
            path = self.dir / name
            _ffmpeg_ok(["-f", "lavfi", "-i", "color=c=blue:s=200x200:d=1",
                        "-frames:v", "1", str(path)])
            covers.append(str(path))
        dest = self.dir / "multi.mka"
        ok, detail = suite.run_conversion(suite.ConversionRequest(
            str(self.src), "mka", str(dest),
            front_cover=covers[0], back_cover=covers[1], disc_image=covers[2]), quiet=True)
        self.assertTrue(ok, detail)
        streams = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", str(dest)], capture_output=True, text=True).stdout
        self.assertEqual(streams.count("video"), 3)

    def test_lossless_round_trip_preserves_the_audio(self):
        flac = self.dir / "round.flac"
        back = self.dir / "back.wav"
        self.assertTrue(suite.run_conversion(
            suite.ConversionRequest(str(self.src), "flac", str(flac)), quiet=True)[0])
        self.assertTrue(suite.run_conversion(
            suite.ConversionRequest(str(flac), "wav", str(back)), quiet=True)[0])
        self.assertAlmostEqual(suite.probe_duration(str(self.src)),
                               suite.probe_duration(str(back)), places=1)

    def test_batch_estimate_uses_finished_work(self):
        eta, rate = suite.estimate_batch(total_files=10, done=2, elapsed=20.0,
                                         remaining_duration=0.0)
        self.assertAlmostEqual(rate, 0.1)
        self.assertAlmostEqual(eta, 80.0)

    def test_batch_estimate_needs_a_sample(self):
        eta, rate = suite.estimate_batch(total_files=10, done=0, elapsed=0.0,
                                         remaining_duration=0.0)
        self.assertIsNone(eta)
        self.assertEqual(rate, 0.0)


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = suite.PRESETS_FILE
        suite.PRESETS_FILE = Path(self.tmp.name) / "presets.json"

    def tearDown(self):
        suite.PRESETS_FILE = self.original
        self.tmp.cleanup()

    def test_preset_round_trip(self):
        suite.save_preset("Podcast", {"artist": "MPlanetarian", "album": "SOF"})
        presets = dict(suite.list_presets())
        self.assertEqual(presets["Podcast"]["album"], "SOF")
        self.assertTrue(suite.delete_preset("Podcast"))
        self.assertEqual(suite.list_presets(), [])

    def test_custom_fields_are_deduplicated(self):
        suite.add_custom_field("MOOD")
        suite.add_custom_field("MOOD")
        self.assertEqual(suite.custom_fields(), ["MOOD"])

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(suite.list_presets(), [])
        self.assertEqual(suite.custom_fields(), [])

    def test_corrupt_file_does_not_raise(self):
        suite.PRESETS_FILE.write_text("{not json", encoding="utf-8")
        self.assertEqual(suite.list_presets(), [])


if __name__ == "__main__":
    unittest.main()
