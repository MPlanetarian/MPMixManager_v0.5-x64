#!/usr/bin/env python3
"""The Web Companion must keep playing when an iPhone leaves Safari.

iOS treats a plain ``<audio>`` element and a Web Audio graph very differently:

* the element is a media session. iOS keeps it playing when Safari is
  backgrounded or the phone is locked, provided the page started it from a tap;
* an ``AudioContext`` is suspended/interrupted as soon as the page loses focus.
  A mix routed through ``createMediaElementSource`` then plays silence, even
  though the element still reports ``paused === false``.

That difference is the bug this suite pins. On iPhone/iPad the element must be
left on the native pipeline, the audio session must be claimed as ``playback``
(so the silent switch and the lock screen do not end it), and the lock screen
must get metadata and transport handlers. Elsewhere the Web Audio analyser is
still the equaliser's source, so those guards must stay conditional.

These tests only inspect the rendered dashboard; the media stack itself cannot
run under unittest.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

import mix_web_companion as mwc  # noqa: E402


class IOSBackgroundAudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = mwc.render_html_page(mwc.get_theme_palette(None))

    def function_body(self, name: str) -> str:
        marker = "function " + name + "("
        start = self.html.index(marker)
        start = self.html.rfind("\n", 0, start) + 1
        end = self.html.index("\n        }", start)
        return self.html[start:end]

    def test_ios_never_routes_the_element_through_web_audio(self):
        """The guard has to run *before* the context and the source node.

        A context built first and then abandoned still holds the element's
        output. The early return is what keeps the native media pipeline.
        """
        unlock = self.function_body("unlockAudioContext")
        guard = unlock.index("if (isIOSAudioDevice()) return null;")
        self.assertLess(guard, unlock.index("window.AudioContext"))

        graph = self.function_body("ensureAudioGraph")
        guard = graph.index("if (isIOSAudioDevice()) return null;")
        self.assertLess(guard, graph.index("createMediaElementSource"))
        self.assertLess(guard, graph.index("unlockAudioContext()"))

    def test_the_page_claims_a_playback_audio_session(self):
        claim = self.function_body("claimPlaybackAudioSession")
        self.assertIn("audioSession", claim)
        self.assertIn("type = 'playback'", claim)
        # Claimed from the real taps, not only from a timer.
        engage = self.function_body("activateMobileStream")
        self.assertIn("claimPlaybackAudioSession()", engage)
        transport = self.function_body("handlePlayPauseClick")
        self.assertIn("claimPlaybackAudioSession()", transport)
        self.assertIn("claimPlaybackAudioSession()", self.function_body("noteStreamEngaged"))

    def test_lock_screen_metadata_and_transport_are_wired(self):
        metadata = self.function_body("updateMediaSessionMetadata")
        self.assertIn("new MediaMetadata", metadata)
        self.assertIn("title:", metadata)
        self.assertIn("artist:", metadata)
        self.assertIn("navigator.mediaSession.playbackState", self.function_body(
            "updateMediaSessionState"))
        actions = self.function_body("installMediaSessionActions")
        for name in ("play", "pause", "previoustrack", "nexttrack", "seekforward",
                     "seekbackward", "seekto"):
            self.assertIn(name + ":", actions)
        self.assertIn("navigator.mediaSession.setPositionState", self.html)
        # The metadata is refreshed from the status poll, so a track change
        # updates the lock screen and not only the dashboard heading.
        self.assertIn("updateMediaSessionMetadata()", self.function_body("fetchStatus"))

    def test_backgrounding_never_pauses_the_mix(self):
        handler = self.html[self.html.index("document.addEventListener('visibilitychange'"):]
        handler = handler[:handler.index("});", handler.index("resumeStreamAfterInterruption"))]
        self.assertIn("setPollingPaused(document.hidden)", handler)
        self.assertIn("resumeStreamAfterInterruption()", handler)
        self.assertNotIn(".pause()", handler)
        # A mix paused by a call or Siri is resumed on return, unless the
        # listener asked for silence.
        resume = self.function_body("resumeStreamAfterInterruption")
        self.assertIn("audioStreamEngaged", resume)
        self.assertIn("listenerPaused", resume)
        self.assertIn("audioEl.play()", resume)

    def test_the_queue_keeps_advancing_while_hidden(self):
        """One throttled status poll, not the ten-a-second schedule.

        iOS keeps the element playing in the background, so the page still has
        to notice the end of a mix and load the next file. The full schedule
        stays paused; only this single request survives.
        """
        paused = self.function_body("setPollingPaused")
        self.assertIn("stopPolls();", paused)
        self.assertIn("startBackgroundStreamPoll();", paused)
        self.assertIn("stopBackgroundStreamPoll();", paused)
        poll = self.function_body("startBackgroundStreamPoll")
        self.assertIn("isIOSAudioDevice()", poll)
        self.assertIn("fetchStatus()", poll)
        self.assertIn("BACKGROUND_STREAM_POLL_MS", poll)
        self.assertIn("!listenerPaused", poll)

    def test_the_volume_pill_does_not_pretend_on_ios(self):
        volume = self.function_body("applyStreamVolume")
        self.assertIn("'Vol: iOS'", volume)
        self.assertIn("speakersFollowGain()", volume)


if __name__ == "__main__":
    unittest.main()
