"""Auto-skip when a device cannot decode a mix.

Observed on a real archive: a multi-gigabyte WAV export that never finished
writing. Its size cleared the old "more than a header" floor, and it began with
1.9 GB of nulls - no RIFF magic, no frames - so the phone pulled the body,
raised a media error, and the dashboard answered with one dead-end card:

    "This mix cannot be played on this device ... pick another mix or
     re-export it."

The mix next to it played fine. The listener had to tap through by hand, and
if every mix in a playlist was damaged there was no answer at all.

These tests pin the three parts of the fix:

1. the queue gate recognises a WAV whose head is all nulls (the real file),
2. a device's media-error verdict is remembered and skipped by the host, both
   for the next poll and for every later device,
3. the queue walks past failures and stops with one clear message once nothing
   is left to try, instead of looping on the same dead URL.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mix_web_companion as mwc  # noqa: E402

mwc.ARCHIVE_MOUNT_GLOBS = ()


def _wav_header(data_bytes: int) -> bytes:
    return (
        b"RIFF" + (36 + data_bytes).to_bytes(4, "little") + b"WAVE"
        + b"fmt " + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little") + (2).to_bytes(2, "little")
        + (48000).to_bytes(4, "little") + (192000).to_bytes(4, "little")
        + (4).to_bytes(2, "little") + (16).to_bytes(2, "little")
        + b"data" + data_bytes.to_bytes(4, "little")
    )


class ZeroFilledWavTest(unittest.TestCase):
    """The file that started this: a big WAV export of pure nulls."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_zero_wav_"))
        mwc._frame_probe_cache.clear()
        mwc._frame_probe_dirty = False

    def tearDown(self):
        mwc._frame_probe_cache.clear()
        mwc._frame_probe_dirty = False

    def test_a_wav_of_nothing_but_nulls_is_not_streamable(self):
        dead = self.tmp / "interrupted.wav"
        # 256 KiB is comfortably over MIN_STREAMABLE_BYTES, which is why the
        # size floor let the real 1.9 GB file through.
        dead.write_bytes(b"\x00" * (256 * 1024))
        self.assertGreater(dead.stat().st_size, mwc.MIN_STREAMABLE_BYTES)
        self.assertFalse(mwc.is_streamable_file(str(dead)))

    def test_a_real_wav_next_to_it_still_plays(self):
        good = self.tmp / "real.wav"
        good.write_bytes(_wav_header(4096) + b"\x01\x02" * 2048)
        self.assertTrue(mwc.is_streamable_file(str(good)))

    def test_an_empty_wav_stub_is_still_rejected(self):
        # The 44-byte header stub the earlier fix covers; the new zero-head
        # check must not accidentally accept or misjudge it.
        stub = self.tmp / "stub.wav"
        stub.write_bytes(b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
                         + b"fmt " + (16).to_bytes(4, "little") + b"\x00" * 16
                         + b"data" + (0).to_bytes(4, "little"))
        self.assertFalse(mwc.is_streamable_file(str(stub)))

    def test_a_non_wav_container_is_untouched_by_the_zero_head_rule(self):
        """Only a .wav suffix reaches the header probe."""
        mp3 = self.tmp / "silence.mp3"
        mp3.write_bytes(b"\x00" * 4096)
        self.assertTrue(mwc.is_streamable_file(str(mp3)))


class FrameProbeCacheVersionTest(unittest.TestCase):
    """A stale "has audio" verdict must not survive a stricter probe."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_probe_ver_"))
        self.cache = self.tmp / "frames.json"
        mwc._frame_probe_cache.clear()
        mwc._frame_probe_dirty = False

    def tearDown(self):
        mwc._frame_probe_cache.clear()
        mwc._frame_probe_dirty = False

    def test_an_unversioned_cache_is_ignored(self):
        # Exactly the shape written by the previous release: a bare mapping of
        # path -> [mtime, size, verdict], including a wrong "True" for the dead
        # WAV. Loading it would keep the bad file in the queue forever.
        self.cache.write_text(json.dumps({
            "wav:/archive/dead.wav": [123.0, 1914798348, True],
        }))
        with mock.patch.object(mwc, "FRAME_PROBE_CACHE_FILE", self.cache):
            mwc._load_frame_probe_cache()
        self.assertEqual(mwc._frame_probe_cache, {})

    def test_a_current_version_cache_is_restored(self):
        self.cache.write_text(json.dumps({
            "version": mwc.FRAME_PROBE_CACHE_VERSION,
            "entries": {"wav:/archive/dead.wav": [123.0, 1914798348, False]},
        }))
        with mock.patch.object(mwc, "FRAME_PROBE_CACHE_FILE", self.cache):
            mwc._load_frame_probe_cache()
        self.assertEqual(
            mwc._frame_probe_cache["wav:/archive/dead.wav"], (123.0, 1914798348, False)
        )

    def test_a_new_verdict_is_written_with_the_version(self):
        with mock.patch.object(mwc, "FRAME_PROBE_CACHE_FILE", self.cache):
            mwc._frame_probe_cache["wav:/a.wav"] = (1.0, 10, True)
            mwc._save_frame_probe_cache()
        raw = json.loads(self.cache.read_text())
        self.assertEqual(raw["version"], mwc.FRAME_PROBE_CACHE_VERSION)
        self.assertIn("entries", raw)


class UnplayableLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_ledger_"))
        self.store = self.tmp / "unplayable.json"
        self._patch = mock.patch.object(mwc, "UNPLAYABLE_FILE", self.store)
        self._patch.start()
        mwc.clear_unplayable()

    def tearDown(self):
        mwc.clear_unplayable()
        self._patch.stop()

    def test_a_marked_file_is_skipped(self):
        dead = self.tmp / "dead.wav"
        dead.write_bytes(b"\x00" * 64)
        self.assertFalse(mwc.is_unplayable(str(dead)))
        self.assertTrue(mwc.mark_unplayable(str(dead)))
        self.assertTrue(mwc.is_unplayable(str(dead)))

    def test_re_exporting_the_file_gives_it_a_new_chance(self):
        """The ledger is not a permanent blacklist."""
        dead = self.tmp / "repair.wav"
        dead.write_bytes(b"\x00" * 64)
        mwc.mark_unplayable(str(dead))
        self.assertTrue(mwc.is_unplayable(str(dead)))
        # A re-export changes the size (and usually the mtime). Either way the
        # recorded signature no longer describes the file on disk.
        dead.write_bytes(_wav_header(1024) + b"\x03\x04" * 512)
        self.assertFalse(mwc.is_unplayable(str(dead)))

    def test_a_deleted_file_is_forgotten_not_kept(self):
        gone = self.tmp / "gone.wav"
        mwc.mark_unplayable(str(gone))
        self.assertFalse(mwc.is_unplayable(str(gone)))

    def test_a_rescan_clears_the_ledger(self):
        dead = self.tmp / "dead.wav"
        dead.write_bytes(b"\x00" * 64)
        mwc.mark_unplayable(str(dead))
        mwc.clear_unplayable()
        self.assertFalse(mwc.is_unplayable(str(dead)))

    def test_the_ledger_survives_a_restart(self):
        """The dead file stays on the archive across a service restart.

        Forgetting the verdict on restart is what would hand the same dead URL
        to the next listener after a reboot, so the ledger is persisted.
        """
        dead = self.tmp / "dead.wav"
        dead.write_bytes(b"\x00" * 64)
        mwc.mark_unplayable(str(dead))
        # Simulate the process boundary: drop memory, reload from disk.
        mwc._unplayable_files.clear()
        mwc._load_unplayable()
        self.assertTrue(mwc.is_unplayable(str(dead)))

    def test_a_repaired_file_is_not_skipped_after_a_restart(self):
        """The signature is what stops a persisted verdict outliving a fix."""
        dead = self.tmp / "repair.wav"
        dead.write_bytes(b"\x00" * 64)
        mwc.mark_unplayable(str(dead))
        dead.write_bytes(_wav_header(2048) + b"\x05\x06" * 1024)
        mwc._unplayable_files.clear()
        mwc._load_unplayable()
        self.assertFalse(mwc.is_unplayable(str(dead)))

    def test_next_playable_index_walks_past_failures(self):
        files = [str(self.tmp / f"{i}.wav") for i in range(4)]
        for path in files:
            Path(path).write_bytes(b"\x00" * 64)
        mwc.mark_unplayable(files[1])
        mwc.mark_unplayable(files[2])
        self.assertEqual(mwc.next_playable_index(files, 1), 3)
        # A walk that starts on the last entry wraps to the first one.
        mwc.mark_unplayable(files[3])
        self.assertEqual(mwc.next_playable_index(files, 3), 0)

    def test_next_playable_index_returns_none_when_all_failed(self):
        files = [str(self.tmp / f"{i}.wav") for i in range(3)]
        for path in files:
            Path(path).write_bytes(b"\x00" * 64)
            mwc.mark_unplayable(path)
        self.assertIsNone(mwc.next_playable_index(files, 0))


class QueueSkipsUnplayableTest(unittest.TestCase):
    """The host's own cursor must never hand a dead URL back to a phone."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp_skip_queue_"))
        self.queue = [str(self.tmp / f"{i}.flac") for i in range(5)]
        for path in self.queue:
            Path(path).write_bytes(b"\x00" * 64)
        self.state = self.tmp / "stream.json"
        mwc.clear_unplayable()
        self.store_patch = mock.patch.object(mwc, "UNPLAYABLE_FILE",
                                             self.tmp / "unplayable.json")
        self.store_patch.start()
        self.patches = [
            mock.patch.object(mwc, "CONFIG_DIR", self.tmp),
            mock.patch.object(mwc, "VOLUME_FILE", self.state),
            mock.patch.object(mwc, "build_stream_queue", side_effect=lambda *a, **k: list(self.queue)),
            mock.patch.object(mwc, "build_stream_queue_cached", side_effect=lambda *a, **k: list(self.queue)),
            mock.patch.object(mwc, "probe_duration", return_value=600.0),
            mock.patch.object(mwc, "host_player_status", return_value=(None, "none")),
            mock.patch.object(mwc, "host_player_payload", return_value=None),
        ]
        for p in self.patches:
            p.start()
        mwc.invalidate_queue_cache()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.store_patch.stop()
        mwc.clear_unplayable()
        mwc.invalidate_queue_cache()

    def test_next_skips_a_failed_mix(self):
        mwc.save_stream_session({"index": 0, "path": self.queue[0], "playing": True})
        mwc.mark_unplayable(self.queue[1])
        mwc.control_player("next")
        path, _ = mwc.current_stream_file()
        self.assertEqual(path, self.queue[2])

    def test_the_status_endpoint_never_names_a_failed_file(self):
        mwc.save_stream_session({"index": 1, "path": self.queue[1], "playing": True})
        mwc.mark_unplayable(self.queue[1])
        status = mwc.get_playback_status(None)
        self.assertEqual(status["active_file"], self.queue[2])
        self.assertEqual(status["unplayable_count"], 1)

    def test_a_track_failed_report_advances_to_the_next_mix(self):
        mwc.save_stream_session({"index": 0, "path": self.queue[0], "playing": True})
        ok = mwc.control_player("track_failed", {"path": [self.queue[0]]})
        self.assertTrue(ok)
        path, state = mwc.current_stream_file()
        self.assertEqual(path, self.queue[1])
        self.assertFalse(state.get("playback_exhausted"))

    def test_an_explicit_failed_path_wins_over_the_cursor(self):
        """The phone's poll can be one step behind the file that errored."""
        mwc.save_stream_session({"index": 0, "path": self.queue[0], "playing": True})
        mwc.control_player("track_failed", {"path": [self.queue[2]]})
        self.assertTrue(mwc.is_unplayable(self.queue[2]))
        path, _ = mwc.current_stream_file()
        self.assertEqual(path, self.queue[3])

    def test_every_candidate_failing_marks_the_playlist_exhausted(self):
        files = list(self.queue)
        for path in files[:-1]:
            mwc.mark_unplayable(path)
        mwc.save_stream_session({"index": 0, "path": files[0], "playing": True})
        mwc.control_player("track_failed", {"path": [files[-1]]})
        status = mwc.get_playback_status(None)
        self.assertTrue(status["playback_exhausted"])
        self.assertEqual(status["unplayable_count"], len(files))

    def test_playlist_rows_flag_skipped_mixes(self):
        mwc.save_stream_session({"index": 0, "path": self.queue[0], "playing": True})
        mwc.mark_unplayable(self.queue[1])
        data = mwc.get_mini_playlist()
        flags = {item["file"]: item["unplayable"] for item in data["items"]}
        self.assertTrue(flags[self.queue[1]])
        self.assertFalse(flags[self.queue[0]])
        self.assertEqual(data["unplayable_count"], 1)

    def test_rescan_forgives_written_off_files(self):
        mwc.mark_unplayable(self.queue[0])
        self.assertTrue(mwc.is_unplayable(self.queue[0]))
        mwc.control_player("rescan")
        self.assertFalse(mwc.is_unplayable(self.queue[0]))

    def test_rescan_also_clears_the_exhausted_flag(self):
        """A retry must not be refused by the previous run's verdict.

        The ledger and the exhausted flag are two halves of the same answer.
        Clearing only the ledger left `playback_exhausted` set, so the dashboard
        kept the "nothing in this playlist plays" card up right after the
        operator tapped retry - the rescan looked like it had done nothing.
        """
        for path in self.queue:
            mwc.mark_unplayable(path)
        state = mwc.load_stream_session()
        state.update(index=0, path=self.queue[0], playing=False,
                     playback_exhausted=True)
        mwc.save_stream_session(state)
        self.assertTrue(mwc.load_stream_session()["playback_exhausted"])

        mwc.control_player("rescan")
        self.assertFalse(mwc.load_stream_session()["playback_exhausted"])
        self.assertEqual(mwc.get_playback_status(None)["unplayable_count"], 0)

    def test_shuffle_never_lands_on_a_failed_mix(self):
        mwc.save_stream_session({"index": 0, "path": self.queue[0], "playing": True,
                                 "shuffle": True})
        for path in self.queue[1:]:
            mwc.mark_unplayable(path)
        for _ in range(10):
            mwc.control_player("next")
            path, _ = mwc.current_stream_file()
            self.assertFalse(mwc.is_unplayable(path))


class ClientAutoSkipTest(unittest.TestCase):
    """The dashboard must skip rather than stop on a media error."""

    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_a_decode_error_reports_the_file_and_moves_on(self):
        fn = self.html[self.html.index("audioEl.addEventListener('error'"):]
        fn = fn[: fn.index("loadThemeList();")]
        self.assertIn("handleUnplayableMix(activeAudioFile)", fn)
        self.assertNotIn("pick another mix", fn)

    def test_the_failed_file_is_reported_to_the_host(self):
        self.assertIn("sendAction('track_failed', { path: file })", self.html)

    def test_the_skip_walks_the_whole_queue_and_then_stops(self):
        fn = self.html[self.html.index("function handleUnplayableMix"):]
        fn = fn[: fn.index("function showPlaybackExhaustedNotice")]
        # Bounded by the queue length: one pass, then the exhausted message.
        self.assertIn("consecutiveSkips >= total", fn)
        self.assertIn("showPlaybackExhaustedNotice()", fn)

    def test_exhaustion_is_one_clear_message_not_a_loop(self):
        fn = self.html[self.html.index("function showPlaybackExhaustedNotice"):]
        fn = fn[: fn.index("// A resume point on the final frame")]
        self.assertIn("None of the mixes in this playlist can be played", fn)
        self.assertIn("exhaustedNoticeShown = true", fn)

    def test_the_next_mix_keeps_playing_after_a_skip(self):
        self.assertIn("skipResumePending", self.html)
        self.assertIn("wasPlaying || resumeAfterSkip", self.html)

    def test_a_file_written_off_is_not_reloaded_from_a_stale_poll(self):
        self.assertIn("unplayableFiles.has(activeAudioFile)", self.html)

    def test_retrying_after_exhaustion_clears_the_verdicts(self):
        fn = self.html[self.html.index("function rescanForEngage"):]
        fn = fn[: fn.index("function handlePlayPauseClick")]
        self.assertIn("unplayableFiles.clear()", fn)

    def test_the_old_dead_end_message_is_gone(self):
        self.assertNotIn("It is empty or damaged on the computer — pick another mix", self.html)

    def test_a_file_that_errors_twice_costs_one_slot_in_the_run(self):
        """Two `error` events for the same file are one dead mix, not two.

        Without this guard a single file that fires the handler twice would use
        two entries of a run budget sized to the queue, so the page would give
        up one mix too early and never try the last one.
        """
        fn = self.html[self.html.index("function handleUnplayableMix"):]
        fn = fn[: fn.index("function showPlaybackExhaustedNotice")]
        self.assertIn("const alreadyKnown = unplayableFiles.has(file);", fn)
        guard = fn[fn.index("if (alreadyKnown)"):]
        # The early return happens before the counter moves.
        self.assertIn("return;", guard)
        # The counter only moves in the branch that just recorded a new file.
        self.assertLess(fn.index("unplayableFiles.add(file)"),
                        fn.index("consecutiveSkips += 1"))

    def test_a_freshly_loaded_page_counts_the_hosts_failures(self):
        self.assertIn("serverUnplayableCount", self.html)
        self.assertIn("Math.max(unplayableFiles.size, Number(serverUnplayableCount) || 0)",
                      self.html)


class SkipBannerFlickerTest(unittest.TestCase):
    """The top banner must not strobe while the queue skips dead mixes.

    The 2 s status poll used to clear the "cannot be played - skipping" notice
    unconditionally, right after the error handler had shown it. The next file
    failed, the banner came back, the poll cleared it again: on Chrome the top
    of the page flashed roughly once a second for the whole queue.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_a_skip_notice_survives_the_status_poll(self):
        fn = self.html[self.html.index("async function fetchStatus"):]
        fn = fn[:fn.index("document.getElementById('trackTitle')")]
        self.assertIn("restartNoticeKind !== 'skip'", fn)

    def test_the_skip_notice_is_not_redrawn_for_every_dead_file(self):
        fn = self.html[self.html.index("function showRestartNotice"):]
        fn = fn[:fn.index("function hideRestartNotice")]
        self.assertIn("nextKind === 'skip'", fn)
        self.assertIn("return;", fn)
        # The skip caller names its kind, or the notice is treated as a restart.
        self.assertIn("'skip');", self.html)

    def test_a_burst_of_failures_pauses_the_walk(self):
        fn = self.html[self.html.index("function handleUnplayableMix"):]
        fn = fn[:fn.index("function showPlaybackExhaustedNotice")]
        self.assertIn("SKIP_BURST_LIMIT", fn)
        self.assertIn("skipRunStopped = true", fn)
        self.assertIn("if (skipRunStopped) return;", fn)

    def test_the_stop_is_not_cleared_by_the_status_poll(self):
        fn = self.html[self.html.index("async function fetchStatus"):]
        fn = fn[:fn.index("updateAutoplayNotice(data.autoplay)")]
        self.assertNotIn("skipRunStopped", fn)
        # A successful play and a deliberate re-engage are the only resets.
        self.assertGreaterEqual(self.html.count("skipRunStopped = false;"), 2)

    def test_the_paused_message_names_the_share(self):
        self.assertIn("The music library stopped responding", self.html)
        self.assertIn("archive drive or share", self.html)


if __name__ == "__main__":
    unittest.main()
