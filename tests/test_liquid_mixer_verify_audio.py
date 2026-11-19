#!/usr/bin/env python3
"""Verify Audio Files: find rows that cannot play, then offer to drop them.

Reported need: a library can look complete and still hold rows that do not
play - an offline share, a 0-byte file from a failed copy, or a container the
decoders reject. Selecting one mid-set simply did nothing. The mixer now has a
VERIFY AUDIO FILES button that checks every row, loops the exit movie while it
works, and asks before removing the failures.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
for path in (str(MIXER_DIR), str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import mixer_core  # noqa: E402


def _wav(path, seconds=0.05, rate=8000):
    """Write a tiny but genuinely decodable WAV file."""
    import wave
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * 2 * int(rate * seconds))
    return str(path)


class TestVerifyOneFile(unittest.TestCase):
    def test_a_real_audio_file_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _wav(Path(tmp) / "good.wav")
            verdict = mixer_core.verify_audio_path(path)
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.reason, "")

    def test_a_missing_file_is_reported_missing(self):
        verdict = mixer_core.verify_audio_path("/nonexistent/nope.flac")
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "missing")

    def test_a_zero_byte_file_is_reported_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3") as handle:
            verdict = mixer_core.verify_audio_path(handle.name)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "empty")

    def test_a_corrupt_file_is_reported_unreadable(self):
        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as handle:
            handle.write(b"not audio at all" * 100)
            path = handle.name
        try:
            verdict = mixer_core.verify_audio_path(path)
            self.assertFalse(verdict.ok)
            self.assertEqual(verdict.reason, "unreadable")
        finally:
            os.unlink(path)

    def test_an_empty_path_is_missing(self):
        self.assertEqual(mixer_core.verify_audio_path("").reason, "missing")
        self.assertEqual(mixer_core.verify_audio_path(None).reason, "missing")

    def test_every_reason_has_human_text(self):
        for key in ("missing", "empty", "unreadable"):
            self.assertIn(key, mixer_core.VERIFY_REASONS)
            self.assertTrue(mixer_core.VERIFY_REASONS[key])


class TestVerifyLibrary(unittest.TestCase):
    def _rows(self, tmp, specs):
        rows = []
        for index, kind in enumerate(specs):
            path = Path(tmp) / f"track{index}.wav"
            if kind == "good":
                _wav(path)
            elif kind == "empty":
                path.write_bytes(b"")
            rows.append({"name": path.name, "path": str(path)})
        return rows

    def test_it_separates_good_from_bad_and_keeps_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._rows(tmp, ["good", "empty", "good", "missing"])
            verdicts = mixer_core.verify_audio_library(rows, workers=4)
        self.assertEqual(len(verdicts), 4)
        self.assertEqual([v.ok for v in verdicts], [True, False, True, False])
        self.assertEqual([v.path for v in verdicts], [r["path"] for r in rows])

    def test_progress_is_reported_once_per_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._rows(tmp, ["good"] * 6)
            seen = []
            lock = threading.Lock()

            def progress(done, total, verdict):
                with lock:
                    seen.append((done, total, verdict.path))

            mixer_core.verify_audio_library(rows, workers=3, progress=progress)
        self.assertEqual(len(seen), 6)
        self.assertEqual(sorted(d for d, _t, _p in seen), [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(total == 6 for _d, total, _p in seen))

    def test_cancel_stops_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._rows(tmp, ["good"] * 400)
            verdicts = mixer_core.verify_audio_library(
                rows, workers=2, cancel=lambda: True)
        # The callback is consulted before every file, so a pre-set flag runs
        # nothing at all rather than racing to finish the list.
        self.assertEqual(verdicts, [])

    def test_empty_library_is_not_an_error(self):
        self.assertEqual(mixer_core.verify_audio_library([]), [])

    def test_summary_counts_by_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [
                _wav(Path(tmp) / "ok.wav"),
                "",
                str(Path(tmp) / "gone.flac"),
            ]
            empty = Path(tmp) / "empty.mp3"
            empty.write_bytes(b"")
            paths.append(str(empty))
            verdicts = [mixer_core.verify_audio_path(p) for p in paths]
        summary = mixer_core.verify_summary(verdicts)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["bad"], 3)
        self.assertEqual(summary["by_reason"], {"missing": 2, "empty": 1})


class TestPlaylistPruning(unittest.TestCase):
    def _write(self, tmp, paths):
        target = Path(tmp) / "playlist.json"
        target.write_text(json.dumps(
            [{"name": os.path.basename(p), "path": p} for p in paths]),
            encoding="utf-8")
        return target

    def test_an_explicit_path_never_touches_the_live_library(self):
        """The live playlist must only change through an explicit user action.

        A first version ignored the path argument and rewrote the real
        playlist from a unit test, wiping a 50,378-row library. An explicit
        path now writes only that file (and its m3u8 sibling).
        """
        live = mixer_core.music_playlist_json_path()
        before = live.stat().st_mtime if (live and live.is_file()) else None
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, ["/a/one.flac", "/a/two.flac"])
            removed = mixer_core.remove_rows_from_playlist(["/a/one.flac"], target)
            self.assertEqual(removed, 1)
            # The m3u8 sibling is rewritten too, so the two views agree.
            sibling = target.with_suffix(".m3u8")
            self.assertTrue(sibling.is_file())
            self.assertIn("/a/two.flac", sibling.read_text(encoding="utf-8"))
            self.assertNotIn("/a/one.flac", sibling.read_text(encoding="utf-8"))
        after = live.stat().st_mtime if (live and live.is_file()) else None
        self.assertEqual(before, after, "the live playlist was modified by a temp-file test")


        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, ["/a/one.flac", "/a/two.flac", "/a/three.flac"])
            removed = mixer_core.remove_rows_from_playlist(["/a/two.flac"], target)
            kept = mixer_core.read_playlist_rows(target)
        self.assertEqual(removed, 1)
        self.assertEqual([r["path"] for r in kept], ["/a/one.flac", "/a/three.flac"])

    def test_removing_an_unknown_path_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, ["/a/one.flac"])
            removed = mixer_core.remove_rows_from_playlist(["/a/other.flac"], target)
            kept = mixer_core.read_playlist_rows(target)
        self.assertEqual(removed, 0)
        self.assertEqual(len(kept), 1)

    def test_an_empty_drop_list_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, ["/a/one.flac"])
            self.assertEqual(mixer_core.remove_rows_from_playlist([], target), 0)
            self.assertEqual(len(mixer_core.read_playlist_rows(target)), 1)

    def test_a_missing_playlist_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "absent.json"
            self.assertEqual(mixer_core.remove_rows_from_playlist(["/a/x"], target), 0)
            self.assertFalse(target.exists())

    def test_read_accepts_a_wrapped_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "wrapped.json"
            target.write_text(json.dumps({"tracks": [{"name": "a", "path": "/a/a.flac"}]}))
            self.assertEqual(len(mixer_core.read_playlist_rows(target)), 1)

    def test_a_corrupt_playlist_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "broken.json"
            target.write_text("{not json")
            self.assertEqual(mixer_core.read_playlist_rows(target), [])


class TestVerifyButtonLayout(unittest.TestCase):
    def test_the_button_has_its_own_box_inside_the_mixer(self):
        boxes = mixer_core.layout()
        self.assertIn("verify_button", boxes)
        box, mixer = boxes["verify_button"], boxes["mixer"]
        self.assertGreaterEqual(box[0], mixer[0])
        self.assertLessEqual(box[0] + box[2], mixer[0] + mixer[2])
        self.assertGreaterEqual(box[1], mixer[1])
        self.assertLessEqual(box[1] + box[3], mixer[1] + mixer[3])

    def test_it_does_not_overlap_the_faders_values_or_crossfader(self):
        boxes = mixer_core.layout()
        verify = boxes["verify_button"]
        top, bottom = verify[1], verify[1] + verify[3]
        for key in ("strip_A_value", "strip_B_value", "strip_C_value", "strip_D_value"):
            box = boxes[key]
            self.assertGreaterEqual(top, box[1] + box[3], key)
        xfader = boxes["xfader"]
        self.assertLessEqual(bottom, xfader[1])

    def test_it_does_not_overlap_the_knobs(self):
        boxes = mixer_core.layout()
        verify = boxes["verify_button"]
        for channel in mixer_core.CHANNELS:
            box = boxes[f"knob_low_{channel}_value"]
            self.assertGreaterEqual(verify[1], box[1] + box[3], channel)


class _MixerHarness:
    """A LiquidMixer with the GTK bits replaced, for wiring tests."""

    @staticmethod
    def build():
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        mixer.decks = {}
        return mixer


class TestVerifyButtonWiring(unittest.TestCase):
    def _mixer(self):
        return _MixerHarness.build()

    def test_the_button_starts_a_pass(self):
        mixer = self._mixer()
        # Never let a test read or write the live library.
        with patch("mixer_core.read_playlist_rows", return_value=[]), \
             patch.object(mixer, "_start_verify_movie") as movie, \
             patch.object(mixer, "_start_verify_audio", wraps=mixer._start_verify_audio):
            mixer._start_verify_audio()
        # With no rows there is nothing to check, so it reports that and does
        # not start a movie or leave the button stuck on "verifying".
        movie.assert_not_called()
        self.assertFalse(mixer._verify_running)
        self.assertIn("NO PLAYLIST", mixer._verify_button_label())

    def test_the_idle_label_names_the_action(self):
        mixer = self._mixer()
        mixer._verify_message = ""
        self.assertEqual(mixer._verify_button_label(), "VERIFY AUDIO FILES")

    def test_a_running_pass_shows_counts_and_percent(self):
        mixer = self._mixer()
        mixer._verify_running = True
        mixer._verify_done, mixer._verify_total = 500, 1000
        label = mixer._verify_button_label()
        self.assertIn("500", label)
        self.assertIn("1,000", label)
        self.assertIn("50%", label)

    def test_a_clean_pass_says_all_playable(self):
        mixer = self._mixer()
        mixer._verify_running = True
        mixer._verify_started_at = time.monotonic() - 3
        with patch.object(mixer, "_stop_verify_movie"):
            mixer._finish_verify_audio({"total": 42, "ok": 42, "bad": 0,
                                        "by_reason": {}}, [])
        self.assertFalse(mixer._verify_running)
        self.assertFalse(mixer._verify_prompt)
        self.assertIn("42", mixer._verify_message)
        self.assertIn("PLAYABLE", mixer._verify_message)

    def test_a_failed_pass_asks_before_removing(self):
        mixer = self._mixer()
        mixer._verify_running = True
        mixer._verify_started_at = time.monotonic() - 2
        bad = [{"path": "/a/x.flac", "title": "x.flac", "ok": False, "reason": "missing"}]
        with patch.object(mixer, "_stop_verify_movie"):
            mixer._finish_verify_audio({"total": 10, "ok": 9, "bad": 1,
                                        "by_reason": {"missing": 1}}, bad)
        # Nothing is removed until the user answers.
        self.assertTrue(mixer._verify_prompt)
        self.assertEqual(len(mixer._verify_bad), 1)

    def test_answering_yes_removes_and_reloads_the_decks(self):
        mixer = self._mixer()
        mixer._verify_bad = [{"path": "/a/bad.flac", "reason": "empty"}]
        mixer._verify_prompt = True
        mixer.decks = {"A": {"namespace": "mixerdeck_a"}}
        sent = []
        with patch("mixer_core.remove_rows_from_playlist", return_value=1) as remove, \
             patch.object(mixer, "_stop_verify_movie"), \
             patch("mixer_core.send_ipc",
                   side_effect=lambda ns, cmd, **kw: sent.append((ns, cmd)) or {"status": "ok"}):
            mixer._verify_remove_bad()
        remove.assert_called_once_with(["/a/bad.flac"])
        self.assertFalse(mixer._verify_prompt)
        self.assertIn("REMOVED", mixer._verify_message)
        self.assertIn(("mixerdeck_a", "load_playlist"), sent)

    def test_answering_no_keeps_the_rows(self):
        mixer = self._mixer()
        mixer._verify_bad = [{"path": "/a/bad.flac"}]
        mixer._verify_prompt = True
        with patch("mixer_core.remove_rows_from_playlist") as remove:
            mixer._verify_keep_bad()
        remove.assert_not_called()
        self.assertFalse(mixer._verify_prompt)
        self.assertIn("KEPT", mixer._verify_message)

    def test_esc_cancels_a_running_pass(self):
        mixer = self._mixer()
        mixer._verify_running = True
        mixer._verify_total = 100
        with patch.object(mixer, "_stop_verify_movie") as stop:
            mixer._cancel_verify_audio()
        stop.assert_called_once()
        self.assertFalse(mixer._verify_running)
        self.assertTrue(mixer._verify_cancel.is_set())

    def test_shutdown_stops_a_live_movie(self):
        mixer = self._mixer()
        mixer._verify_running = True
        with patch.object(mixer, "_stop_verify_movie") as stop:
            mixer._shutdown()
        stop.assert_called_once()
        self.assertTrue(mixer._verify_cancel.is_set())


class TestVerifyDialog(unittest.TestCase):
    def _mixer(self):
        return _MixerHarness.build()

    def test_the_dialog_only_exists_while_a_prompt_is_open(self):
        mixer = self._mixer()
        mixer._verify_prompt = False
        self.assertFalse(mixer._inside_verify_dialog(10, 10))

    def test_a_click_anywhere_is_consumed_while_prompting(self):
        mixer = self._mixer()
        mixer._verify_prompt = True
        mixer._verify_bad = [{"path": "/a/x"}]
        with patch.object(mixer, "_verify_keep_bad") as keep, \
             patch.object(mixer, "_verify_remove_bad") as remove:
            # A click well away from both buttons (top-left of the canvas)
            # still belongs to the modal dialog and dismisses it as "keep".
            consumed = mixer._inside_verify_dialog(5, 5)
        self.assertTrue(consumed)
        keep.assert_called_once()
        remove.assert_not_called()

    def test_clicking_the_remove_button_removes(self):
        mixer = self._mixer()
        mixer._verify_prompt = True
        mixer._verify_bad = [{"path": "/a/x"}]
        with patch.object(mixer, "_verify_remove_bad") as remove:
            _panel, box, _keep = mixer._verify_dialog_boxes()
            consumed = mixer._inside_verify_dialog(box[0] + box[2] / 2,
                                                   box[1] + box[3] / 2)
        self.assertTrue(consumed)
        remove.assert_called_once()

    def test_clicking_the_keep_button_keeps(self):
        mixer = self._mixer()
        mixer._verify_prompt = True
        mixer._verify_bad = [{"path": "/a/x"}]
        with patch.object(mixer, "_verify_keep_bad") as keep:
            _panel, _remove, box = mixer._verify_dialog_boxes()
            mixer._inside_verify_dialog(box[0] + box[2] / 2, box[1] + box[3] / 2)
        keep.assert_called_once()

    def test_the_dialog_fits_on_the_canvas(self):
        mixer = self._mixer()
        (x, y, w, h), _remove, _keep = mixer._verify_dialog_boxes()
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, mixer._ui_scale * mixer_core.DESIGN_W + 0.5)
        self.assertLessEqual(y + h, mixer._ui_scale * mixer_core.DESIGN_H + 0.5)


class TestVerifyMovieLoop(unittest.TestCase):
    def test_the_loop_uses_the_shared_loader_script(self):
        script = PROJECT_ROOT / "scripts" / "display_loader_video.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("--loop-start", text)
        self.assertIn("--loop-stop", text)
        # The loop is the same supervisor the uninstaller uses, so there is
        # one implementation of "keep the movie up until the work is done".
        self.assertIn("start_uninstall_exit_video", text)
        self.assertIn("stop_uninstall_exit_video", text)

    def test_the_mixer_supervises_the_picture_while_it_runs(self):
        import inspect
        import MP_Liquid_Mixer as app
        source = inspect.getsource(app.LiquidMixer)
        self.assertIn("_supervise_verify_movie", source)
        self.assertIn("_start_verify_movie", source)
        self.assertIn("_stop_verify_movie", source)


if __name__ == "__main__":
    unittest.main()
