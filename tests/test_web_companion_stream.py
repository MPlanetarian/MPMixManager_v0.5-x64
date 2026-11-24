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
import math
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:  # pragma: no cover - the server falls back to zeros
    HAS_NUMPY = False

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

    def test_repeat_one_restarts_the_element(self):
        self.assertIn("currentRepeatMode === 'one'", self.html)
        self.assertIn("audioEl.currentTime = 0", self.html)

    def test_the_scrubber_follows_a_finger(self):
        self.assertIn("function bindScrubber()", self.html)
        self.assertIn("pointerdown", self.html)
        self.assertIn("function paintLocalProgress(", self.html)
        self.assertIn('id="progressBg"', self.html)
        self.assertNotIn('id="progressBg" onclick', self.html)

    def test_sleep_favourites_and_home_screen_are_on_the_page(self):
        self.assertIn("function fireSleep()", self.html)
        self.assertIn('id="btnSleep"', self.html)
        self.assertIn('id="btnFavorite"', self.html)
        self.assertIn('rel="manifest"', self.html)
        self.assertIn("apple-mobile-web-app-capable", self.html)
        self.assertIn("trackMeta.artwork", self.html)


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
        self.assertIn("function skipBy(delta, opts)", self.html)
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

    def test_every_skip_reaches_the_host_player(self):
        """A mirrored phone moved only its silent element, never the sound.

        With a desktop player on air the phone's <audio> element is paused on
        purpose, so seeking it produced no audible change and the buttons read
        as dead. The seek must always be forwarded to the server, which owns
        the resume point and relays the command to the player.
        """
        fn = self.html[self.html.index("function skipBy(delta, opts)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("sendAction(delta > 0 ? 'seek_forward' : 'seek_backward')", fn)
        # The forward must not sit behind an early return for the loaded case.
        self.assertNotIn("return;\n            }\n            target =", fn)

    def test_an_engaged_page_seeks_itself_to_an_absolute_position(self):
        """The 30s buttons must move the mix this page is playing.

        A relative seek against a session clock parked at zero jumped the
        browser back to the start, or moved only the desk player, so the
        arrows looked dead.
        """
        fn = self.html[self.html.index("function skipBy(delta, opts)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("sendAction('seek_to'", fn)
        self.assertIn("phoneOwnsPlayback()", fn)

    def test_skip_uses_the_seekable_range_when_duration_is_unknown(self):
        # A live stream reports duration Infinity, which used to park every
        # press in the pending-seek branch and move nothing.
        self.assertIn("function audioTimelineEnd(audioEl)", self.html)
        self.assertIn("audioEl.seekable.end", self.html)

    def test_skip_does_not_strand_a_pending_seek(self):
        # Parking an offset without applying it meant the next press only
        # updated the same parked number and the mix never moved.
        fn = self.html[self.html.index("function skipBy(delta, opts)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("applyPendingSeek()", fn)

    def test_running_off_the_end_of_a_mix_keeps_playing(self):
        """+30 from the tail advances the queue, so the next mix must not stall.

        The element has already flipped to `ended` when the server names the
        following file, so a plain audioElIsPlaying() check reported "not
        playing" and the new mix loaded paused - the button looked like it had
        stopped the music.
        """
        fn = self.html[self.html.index("const prevEl ="):]
        fn = fn[: fn.index("activeAudioFile = data.active_file")]
        self.assertIn("prevEl.ended", fn)


class KeyboardSeekTest(unittest.TestCase):
    """Left/Right seek the mix, the same contract the desktop player has.

    The dashboard's transport row was pointer-only: no keydown handler existed
    for playback at all, so on a laptop, a keyboard case or a TV browser the
    arrow keys did nothing to the mix. The keys must go through skipBy so they
    inherit its clamped targets, its parked offset before metadata and its
    forward to a desk player that owns the sound.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def _fn(self, name):
        fn = self.html[self.html.index("function " + name):]
        return fn

    def test_the_page_binds_a_keyboard_seek_handler(self):
        self.assertIn("function handleSeekKeydown(ev)", self.html)
        self.assertIn(
            "document.addEventListener('keydown', handleSeekKeydown)",
            self.html,
        )

    def test_both_arrows_seek_through_the_shared_skip_helper(self):
        fn = self.html[self.html.index("function handleSeekKeydown(ev)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("key !== 'ArrowLeft' && key !== 'ArrowRight'", fn)
        self.assertIn("skipBy(key === 'ArrowRight' ? step : -step, { relativeSeconds: true })", fn)

    def test_the_step_matches_the_desktop_player(self):
        # The player's own arrows are +/-10 s, so the browser must not invent a
        # different distance for the same gesture.
        self.assertIn("const KEY_SEEK_STEP_SECONDS = 10;", self.html)
        self.assertIn("const KEY_SEEK_STEP_SHIFT_SECONDS = 30;", self.html)

    def test_arrows_do_not_hijack_text_fields(self):
        """The playlist search and the assistant prompt are real inputs."""
        self.assertIn("function isSeekBlockedTarget(target)", self.html)
        fn = self.html[self.html.index("function isSeekBlockedTarget(target)"):]
        fn = fn[: fn.index("function handleSeekKeydown")]
        self.assertIn("isContentEditable", fn)
        for tag in ("'input'", "'textarea'", "'select'"):
            self.assertIn(tag, fn)

    def test_modified_arrows_stay_with_the_browser(self):
        fn = self.html[self.html.index("function handleSeekKeydown(ev)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("ev.altKey || ev.ctrlKey || ev.metaKey", fn)
        self.assertIn("ev.preventDefault()", fn)

    def test_arrows_do_nothing_on_the_assistant_tab_or_the_gate(self):
        fn = self.html[self.html.index("function handleSeekKeydown(ev)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("if (activeTab !== 'player') return;", fn)
        self.assertIn("if (streamOverlayWanted) return;", fn)
        self.assertIn("if (!activeAudioFile) return;", fn)

    def test_a_held_key_scrubs_at_a_servable_rate(self):
        self.assertIn("const KEY_SEEK_REPEAT_MS = 150;", self.html)
        fn = self.html[self.html.index("function handleSeekKeydown(ev)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("ev.repeat", fn)
        self.assertIn("KEY_SEEK_REPEAT_MS", fn)

    def test_the_keys_move_a_desk_player_by_their_own_step(self):
        """A 10 s arrow must not become the buttons' fixed 30 s on the desk.

        `seek_forward`/`seek_backward` are hard-coded to 30 s inside the
        server, so a 10 s arrow forwarded as `seek_forward` moved the room 30 s
        while the browser's silent mirror moved 10. `seek` carries the real
        distance, which is what the desktop player's own arrows use.
        """
        fn = self.html[self.html.index("function skipBy(delta, opts)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("options.relativeSeconds", fn)
        self.assertIn("sendAction('seek', { seconds: delta })", fn)
        keys = self.html[self.html.index("function handleSeekKeydown(ev)"):]
        keys = keys[: keys.index("function seekAudioToWithResume")]
        self.assertIn("{ relativeSeconds: true }", keys)

    def test_the_buttons_keep_their_own_30_second_actions(self):
        # The fix is scoped to the keyboard: the two buttons must still send
        # the named actions they always have.
        fn = self.html[self.html.index("function skipBy(delta, opts)"):]
        fn = fn[: fn.index("function seekAudioToWithResume")]
        self.assertIn("sendAction(delta > 0 ? 'seek_forward' : 'seek_backward')", fn)
        self.assertIn('onclick="skipBy(-30)"', self.html)
        self.assertIn('onclick="skipBy(30)"', self.html)

    def test_the_buttons_advertise_the_keys(self):
        self.assertIn("Left arrow rewinds 10s", self.html)
        self.assertIn("Right arrow forwards 10s", self.html)


class PlayPauseIconTest(unittest.TestCase):
    """The transport glyph names the next action and follows real playback.

    Playing shows Pause, because the tap will pause. Paused or stopped shows
    Play, because the tap will play. The label used to be painted from the
    daemon's `state`, so a pause on the phone (screen lock, a call, unplugged
    headphones) left it on Pause while the room was silent.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_the_button_starts_on_the_play_glyph(self):
        self.assertIn('id="btnPlayPause"', self.html)
        start = self.html.index('id="btnPlayPause"')
        marker = self.html[start: self.html.index("</button>", start)]
        self.assertIn("▶ Play", marker)
        self.assertNotIn("⏸ Pause", marker)

    def test_the_label_is_derived_from_media_events(self):
        self.assertIn("function paintPlayPauseButton(playing)", self.html)
        for event in ("'play'", "'pause'", "'playing'", "'ended'"):
            self.assertIn(
                f"audioEl.addEventListener({event}, () => syncPlayPauseButton())",
                self.html,
            )

    def test_playing_shows_pause_and_paused_shows_play(self):
        fn = self.html[self.html.index("function paintPlayPauseButton"):]
        fn = fn[: fn.index("function phoneOwnsPlayback")]
        playing_branch, paused_branch = fn.split("} else {", 1)
        self.assertIn("⏸ Pause", playing_branch)
        self.assertNotIn("▶ Play", playing_branch)
        self.assertIn("▶ Play", paused_branch)
        self.assertNotIn("⏸ Pause", paused_branch)

    def test_a_desktop_player_still_drives_the_icon(self):
        # In host-mirror mode the phone element is deliberately idle, so the
        # daemon state is the only truth available.
        self.assertIn("function syncPlayPauseButton(serverState)", self.html)
        self.assertIn("lastServerState", self.html)

    def test_the_tap_is_forwarded_while_the_desktop_owns_audio(self):
        fn = self.html[self.html.index("function handlePlayPauseClick()"):]
        head = fn.split("if (audioEl) {", 1)[0]
        self.assertIn("phoneOwnsPlayback()", head)
        self.assertIn("sendAction('play-pause')", head)

    def test_an_engaged_page_sends_play_and_pause_not_a_toggle(self):
        fn = self.html[self.html.index("function handlePlayPauseClick()"):]
        fn = fn[: fn.index("function animLoop")]
        self.assertIn("listenerPaused = true", fn)
        self.assertIn("sendAction('pause')", fn)
        self.assertIn("sendAction('play')", fn)

    def test_engaged_transport_targets_the_web_stream(self):
        self.assertIn("target: 'stream'", self.html)
        self.assertIn("follow=stream", self.html)
        self.assertIn("action=adopt_host", self.html)
        self.assertIn("function resumeAfterNetworkDrop()", self.html)
        self.assertIn("audioStreamEngaged && activeAudioFile", self.html)


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


class PhoneEqualiserTest(unittest.TestCase):
    """The iPhone equaliser reads the element, and a press opens the spectrogram.

    iOS keeps the element on the native media pipeline for background play, so
    it has no AnalyserNode. /api/eq now decodes the file the phone is hearing
    and returns real bands for that fallback instead of 24 zeros. Percentage
    heights inside the flex row also do not resolve on iOS, so a bar that did
    change level still did not change size.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def test_a_press_switches_to_the_dynamic_spectrogram(self):
        self.assertIn('id="visContainer"', self.html)
        self.assertIn('onclick="toggleVisualizerMode(event)"', self.html)
        self.assertIn('id="visSpectrogram"', self.html)
        self.assertIn("Stream of Frequency (Dynamic Spectrogram)", self.html)
        self.assertIn("function toggleVisualizerMode", self.html)
        self.assertIn("function applyVisualizerMode", self.html)
        self.assertIn("vizMode === 'spectrogram' ? 'bars' : 'spectrogram'", self.html)
        self.assertIn("mode-spectrogram", self.html)
        self.assertIn("touch-action: manipulation", self.html)
        self.assertIn("ontouchend=\"toggleVisualizerMode(event)\"", self.html)
        canvas = self.html[self.html.index("#visSpectrogram"):]
        canvas = canvas[:canvas.index(".v-col")]
        self.assertIn("pointer-events: none", canvas)

    def test_only_two_effects_can_be_selected(self):
        self.assertIn('id="vizEffectBars"', self.html)
        self.assertIn('id="vizEffectSpectro"', self.html)
        self.assertEqual(self.html.count("selectVisualizerMode(event, 'bars')"), 2)
        self.assertEqual(self.html.count("selectVisualizerMode(event, 'spectrogram')"), 2)
        self.assertIn("function selectVisualizerMode", self.html)
        self.assertIn("if (mode !== 'bars' && mode !== 'spectrogram') return;", self.html)
        self.assertNotIn("selectVisualizerMode(event, 'liquid')", self.html)

    def test_bars_follow_the_phone_element_in_pixels(self):
        self.assertIn("function ensureAudioGraph", self.html)
        self.assertIn("createMediaElementSource", self.html)
        self.assertIn("getByteFrequencyData", self.html)
        self.assertIn("createScriptProcessor", self.html)
        self.assertIn("bar.style.height = barPx + 'px'", self.html)
        self.assertNotIn("bar.style.height = barPct + '%'", self.html)
        fn = self.html[self.html.index("async function fetchEq"):]
        fn = fn[:fn.index("function streamUnavailableDetail")]
        self.assertIn("if (!phoneIsAudible())", fn)
        self.assertIn("phoneIsAudible", fn)

    def test_the_spectrum_puller_only_exists_for_capture_streams(self):
        """An always-on ScriptProcessorNode is what iOS tears down.

        The muted pull node exists because WebKit drops AnalyserNode data when
        nothing pulls the graph. An element source is already pulled by its
        audible connection, so building the node for that path added a
        2048-frame callback running ~21 times a second forever - the node most
        often behind an iOS audio-session teardown, and one more reason the
        page reloaded.
        """
        self.assertIn("if (mode === 'capture') attachSpectrumPull(ctx, analyser);", self.html)
        # The unconditional call must be gone.
        ensure = self.html[self.html.index("function ensureAudioGraph"):]
        ensure = ensure[:ensure.index("function ")]
        self.assertNotIn("            attachSpectrumPull(ctx, analyser);\n", ensure)
        # attachSpectrumPull must not silently create a second graph reference.
        pull_fn = self.html[self.html.index("function attachSpectrumPull"):]
        pull_fn = pull_fn[:pull_fn.index("function ensureAudioGraph")]
        self.assertIn("if (!ctx || !analyser) return;", pull_fn)
        self.assertNotIn("audioGraph = audioGraph || {{}};", pull_fn)

    def test_engage_seeks_to_the_live_desk_position(self):
        """A desk player already in the mix must not start the phone at 0.

        The element is often already pointed at the file by the time Engage
        runs. Seeking only when src was assigned left play() at the old
        offset, so the web stream and MP Liquid / the CLI disagreed.
        """
        fn = self.html[self.html.index("function engageStreamFromServer"):]
        fn = fn[:fn.index("function handleUnplayableMix")]
        self.assertIn("liveHostPosition()", fn)
        self.assertIn("alignPlaybackTo(startAt, hostPlayerActive)", fn)
        self.assertNotIn("if (mustBind && isFinite(startAt)", fn)
        self.assertIn("function alignPlaybackTo(pos, fromHost)", self.html)
        self.assertIn("hostAlignTarget", self.html)

    def test_the_audio_graph_is_opened_inside_the_engage_tap(self):
        fn = self.html[self.html.index("function activateMobileStream"):]
        fn = fn[:fn.index("function unlockStreamElement")]
        self.assertIn("unlockAudioContext()", fn)
        engage = self.html[self.html.index("function engageStreamFromServer"):]
        engage = engage[:engage.index("function handleUnplayableMix")]
        self.assertIn("ensureAudioGraph()", engage)


class ServerSpectrumTest(unittest.TestCase):
    """The iOS fallback gets real bands from the server, not 24 zeros."""

    @unittest.skipUnless(HAS_NUMPY, "numpy is required for the server spectrum")
    def test_a_tone_lands_in_the_expected_band(self):
        rate = 22050
        samples = [
            12000.0 * math.sin(2.0 * math.pi * 1000.0 * i / rate)
            for i in range(2048)
        ]
        levels = mwc._spectrum_bands(samples, rate)
        self.assertEqual(len(levels), 24)
        self.assertGreater(max(levels), 0.15)
        peak = max(range(len(levels)), key=lambda index: levels[index])
        # 1 kHz sits high in a 30 Hz-18 kHz log split, but not at the top.
        self.assertGreater(peak, 8)
        self.assertLess(peak, 19)

    @unittest.skipUnless(
        HAS_NUMPY and shutil.which("ffmpeg"),
        "ffmpeg and numpy are required to decode the stream window",
    )
    def test_the_stream_window_is_decoded_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.wav"
            rate = 22050
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(b"".join(
                    struct.pack("<h", int(12000 * math.sin(2 * math.pi * 1000 * i / rate)))
                    for i in range(rate * 2)
                ))
            first = mwc.stream_spectrum_levels(str(path), 0.5)
            self.assertGreater(max(first), 0.15)
            # The same position is served from the window cache, not a second
            # ffmpeg run.
            with mock.patch.object(mwc, "_decode_stream_window") as decode:
                cached = mwc.stream_spectrum_levels(str(path), 0.5)
            decode.assert_not_called()
            self.assertEqual(cached, first)

    def test_phone_eq_reports_the_server_levels(self):
        levels = [0.42] * 24
        state = {
            "playing": True,
            "position_sec": 30.0,
            "duration_sec": 120.0,
            "volume": 80,
        }
        with mock.patch.object(mwc, "host_player_payload", return_value=None), \
             mock.patch.object(mwc, "current_stream_file", return_value=("/mix.flac", state)), \
             mock.patch.object(mwc, "stream_position", return_value=30.0), \
             mock.patch.object(mwc, "stream_spectrum_levels", return_value=levels):
            eq = mwc.get_eq_status("stream")
        self.assertEqual(eq["levels"], levels)
        self.assertEqual(eq["peaks"], levels)


if __name__ == "__main__":
    unittest.main()
