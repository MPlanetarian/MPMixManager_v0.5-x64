"""Regression tests for the Web Companion mobile audio stream.

Two defects used to make a phone repeat the same few seconds of a mix forever:

1. `/api/stream` only matched `bytes=start-end`. A plain `bytes=start-`
   (the suffix-open form every mobile browser sends when it scrubs) fell
   through to the full-file 200 path, so the element was handed a body that
   did not start where it asked for and restarted from the same offset.
2. The 100 ms `/api/eq` poll force-seeked the element back to the daemon's
   `position_sec` even while the daemon was paused. With a frozen position the
   phone played ~2 s, was yanked back, and looped that fragment indefinitely.

These tests pin the range maths and the "only follow a live clock" rule.
"""

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mix_web_companion as mwc  # noqa: E402


SIZE = 10_000


def _parse(range_header):
    """Run CompanionHandler._parse_range_header without a live socket."""
    handler = mwc.CompanionHandler.__new__(mwc.CompanionHandler)
    handler.headers = {"Range": range_header} if range_header is not None else {}
    return handler, handler._parse_range_header(SIZE)


class StreamRangeTest(unittest.TestCase):
    def test_no_range_header_means_whole_file(self):
        _, (start, end) = _parse(None)
        self.assertEqual((start, end), (0, SIZE - 1))

    def test_open_ended_range_serves_the_whole_tail(self):
        """bytes=5000- must reach the final byte, not stop early."""
        _, (start, end) = _parse("bytes=5000-")
        self.assertEqual((start, end), (5000, SIZE - 1))

    def test_closed_range_is_honoured_exactly(self):
        _, (start, end) = _parse("bytes=100-199")
        self.assertEqual((start, end), (100, 199))

    def test_suffix_range_returns_final_bytes(self):
        _, (start, end) = _parse("bytes=-1024")
        self.assertEqual((start, end), (SIZE - 1024, SIZE - 1))

    def test_range_past_eof_is_unsatisfiable(self):
        _, result = _parse(f"bytes={SIZE}-")
        self.assertEqual(result, (None, 0))

    def test_end_beyond_eof_is_clamped(self):
        _, (start, end) = _parse(f"bytes=0-{SIZE * 5}")
        self.assertEqual((start, end), (0, SIZE - 1))

    def test_garbage_range_falls_back_to_full_body(self):
        _, (start, end) = _parse("bytes=not-a-range")
        self.assertEqual((start, end), (0, SIZE - 1))


class StreamDeliveryTest(unittest.TestCase):
    def _serve(self, range_header, payload):
        handler = mwc.CompanionHandler.__new__(mwc.CompanionHandler)
        handler.headers = {"Range": range_header} if range_header else {}
        handler.wfile = mock.MagicMock()
        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()
        handler.close_connection = False
        handler.send_error = mock.MagicMock()
        return handler

    def test_broken_pipe_is_swallowed(self):
        """A phone closing the tab mid-transfer must not log a server fault."""
        handler = self._serve("bytes=0-", None)
        handler.wfile.write = mock.MagicMock(side_effect=BrokenPipeError)
        with mock.patch.object(mwc.os.path, "isfile", return_value=True), \
             mock.patch.object(mwc.os.path, "getsize", return_value=SIZE), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"x" * SIZE)):
            handler._serve_audio_stream("/tmp/opencode/fake.wav")
        self.assertTrue(handler.close_connection)
        handler.send_error.assert_not_called()

    def test_open_ended_range_emits_206_with_full_tail_length(self):
        handler = self._serve("bytes=9000-", None)
        with mock.patch.object(mwc, "is_streamable_file", return_value=True), \
             mock.patch.object(mwc.os.path, "getsize", return_value=SIZE), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"x" * SIZE)):
            handler._serve_audio_stream("/tmp/opencode/fake.wav")
        handler.send_response.assert_called_once_with(206)
        headers = {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}
        self.assertEqual(headers["Content-Range"], f"bytes 9000-{SIZE - 1}/{SIZE}")
        self.assertEqual(headers["Content-Length"], str(SIZE - 9000))

    def test_whole_file_request_emits_200(self):
        handler = self._serve(None, None)
        with mock.patch.object(mwc, "is_streamable_file", return_value=True), \
             mock.patch.object(mwc.os.path, "getsize", return_value=SIZE), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"x" * SIZE)):
            handler._serve_audio_stream("/tmp/opencode/fake.wav")
        handler.send_response.assert_called_once_with(200)


class ClientSyncTest(unittest.TestCase):
    """The page must never re-seek the phone from a server-side clock."""

    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_poll_never_seeks_the_audio_element(self):
        # The 100 ms poll only records the server position; seeking from it is
        # what trapped playback in a repeating fragment.
        self.assertIn("never seek from this poll", self.html)
        self.assertNotIn("audioEl.currentTime = data.position_sec;", self.html)
        self.assertNotIn("serverClockAdvancing", self.html)

    def test_seeks_go_through_the_single_guarded_helper(self):
        self.assertIn("function seekAudioTo(pos)", self.html)
        self.assertIn("audioSeekInFlight", self.html)

    def test_resume_seek_waits_for_metadata(self):
        self.assertIn("loadedmetadata", self.html)

    def test_progress_uses_the_browser_clock_while_streaming(self):
        self.assertIn("hasLocalClock", self.html)
        self.assertIn("formatClock(shownPos)", self.html)

    def test_browser_reports_end_of_mix_to_the_server(self):
        self.assertIn("sendAction('track_ended')", self.html)


class Skip30SecondsTest(unittest.TestCase):
    """The 30 s arrows must move the element that is actually playing.

    They used to send `seek_forward` / `seek_backward` only, which updated the
    daemon's bookkeeping clock while the phone's <audio> element kept playing
    untouched - so the buttons did nothing audible.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_buttons_use_the_client_skip_helper(self):
        self.assertIn('onclick="skipBy(-30)"', self.html)
        self.assertIn('onclick="skipBy(30)"', self.html)
        self.assertNotIn("sendAction('seek_backward')", self.html)
        self.assertNotIn("sendAction('seek_forward')", self.html)

    def test_skip_helper_seeks_the_audio_element(self):
        self.assertIn("function skipBy(delta)", self.html)
        self.assertIn("seekAudioTo(target);", self.html)

    def test_skip_clamps_within_the_mix(self):
        # Rewinding past zero and skipping past the end must not produce a
        # negative or out-of-range currentTime assignment.
        self.assertIn("if (target < 0) target = 0;", self.html)
        self.assertIn("if (dur > 0 && target > dur)", self.html)

    def test_skip_waits_for_a_timeline_when_the_file_is_still_loading(self):
        self.assertIn("pendingSeek", self.html)
        self.assertIn("function applyPendingSeek()", self.html)
        self.assertIn("addEventListener('loadedmetadata', applyPendingSeek)", self.html)

    def test_skip_during_load_stacks_on_the_parked_offset(self):
        # Pressing twice before metadata must add up rather than each press
        # starting again from a stale currentTime of 0.
        self.assertIn("else if (pendingSeek !== null)", self.html)

    def test_unloaded_stream_falls_back_to_the_server_clock(self):
        self.assertIn("if (!configured)", self.html)
        self.assertIn("'seek_forward'", self.html)
        self.assertIn("'seek_backward'", self.html)


class ThemeAndPlaylistUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_theme_selector_is_rendered(self):
        # 0.5 replaced the header <select> with a themed dropdown menu, because
        # a native <select> cannot be styled to match the active palette. The
        # control is still there and still switches themes; assert that rather
        # than the old element id.
        self.assertIn('id="themeMenu"', self.html)
        self.assertIn('id="themeBtn"', self.html)
        self.assertIn("changeTheme(", self.html)

    def test_theme_is_applied_live_from_the_selector(self):
        self.assertIn("function applyPalette(th)", self.html)
        self.assertIn("/api/set_theme?theme=", self.html)

    def test_theme_palette_uses_css_variables(self):
        # The selector reuses the palette variables, so the chosen theme's
        # colours drive the controls rather than hard-coded literals.
        self.assertIn("--accent-secondary", self.html)
        self.assertIn("--card-bg", self.html)

    def test_mini_playlist_renders_exactly_five_tracks(self):
        # 0.5 changed the window from three tracks to five (MINI_PLAYLIST_SIZE)
        # with a twenty-entry scrollback, which is what the README documents.
        self.assertIn('id="miniPlItems"', self.html)
        self.assertIn("renderMiniPlaylist", self.html)
        self.assertEqual(mwc.MINI_PLAYLIST_SIZE, 5)

    def test_mini_playlist_entries_are_selectable(self):
        self.assertIn("select_index", self.html)
        self.assertIn("sendAction('select_index'", self.html)


if __name__ == "__main__":
    unittest.main()
