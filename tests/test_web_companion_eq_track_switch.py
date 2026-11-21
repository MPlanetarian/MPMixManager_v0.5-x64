#!/usr/bin/env python3
"""The dashboard equaliser must keep moving after Next Track.

Chrome's ``captureStream()`` track stops producing samples when the media
element loads a new source. The analyser then reads zeros forever, so the bars
went flat and the spectrogram stopped drawing even though the next mix was
playing. The graph now prefers a ``MediaElementSource`` (which survives a src
change) and keeps captureStream only as a fallback.

This drives the real page in headless Chrome and switches tracks through the
companion, the same way the screenshot that reported the bug was produced.
"""

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT / "scripts"))

import test_web_companion_engage_audio as harness  # noqa: E402


@unittest.skipUnless(harness.HAVE_CDP, "a CDP-capable Chrome and websocket-client are needed")
class EqualizerAfterNextTrackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flac = harness.find_flac()
        if flac is None:
            raise unittest.SkipTest("no FLAC in MP_SAMPLES to stream")

        cls.tmp = Path(tempfile.mkdtemp(prefix="mp-webc-eq-")) / "MIX_ARCHIVE"
        target = cls.tmp / "FLAC_CONVERTED_OUTPUTS"
        target.mkdir(parents=True)
        # Two distinct names guarantee Next really changes activeAudioFile.
        for name in ("Track One.flac", "Track Two.flac"):
            shutil.copy2(flac, target / name)

        cls.server = harness.LiveCompanion(cls.tmp, harness.free_port())
        cls.server.start()
        cls.browser = harness.HeadlessBrowser("no-user-gesture-required")
        cls.browser.start()
        cls.browser._new_target(cls.server.base + "/")

    @classmethod
    def tearDownClass(cls):
        cls.browser.stop()
        cls.server.stop()
        shutil.rmtree(cls.tmp.parent, ignore_errors=True)

    def _eval(self, expression):
        return self.browser.evaluate(expression)

    def _wait_for(self, predicate, timeout=25.0, interval=0.25):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(interval)
        return last

    def _state(self):
        raw = self._eval("""(() => {
            const a = document.getElementById('mobileAudioStream');
            return JSON.stringify({
                file: activeAudioFile,
                paused: a ? a.paused : null,
                mode: (audioGraph && audioGraph.mode) || null,
                failed: Boolean(audioGraph && audioGraph.failed)
            });
        })()""")
        return json.loads(raw) if raw else {}

    def _spectrum_peak(self):
        raw = self._eval("""(() => {
            try { sampleLiveSpectrum(); } catch (e) { return JSON.stringify({error: String(e)}); }
            let peak = 0;
            if (freqBins) {
                for (let i = 0; i < freqBins.length; i += 4) peak = Math.max(peak, freqBins[i]);
            }
            return JSON.stringify({peak: peak, targetMax: Math.max(...targetLevels)});
        })()""")
        return json.loads(raw) if raw else {}

    def test_levels_keep_flowing_after_next_track(self):
        # Wait for the page's first status poll to arm the queue, then engage.
        self.assertTrue(self._wait_for(lambda: self._state().get("file")),
                        "the dashboard never picked a mix to stream")
        self._eval("activateMobileStream()")
        self.assertTrue(self._wait_for(lambda: self._state().get("paused") is False),
                        "the stream never started")

        first = self._state()
        self.assertEqual(first.get("mode"), "element",
                         "the analyser must follow the element across src changes")

        before = self._wait_for(
            lambda: (lambda s: s if s.get("peak", 0) > 0
                     and s.get("targetMax", 0) > 0.05 else None)(self._spectrum_peak()),
            timeout=15.0,
        )
        self.assertTrue(before and before.get("peak", 0) > 0,
                        "the equaliser saw no audio before Next")

        self._eval("sendAction('next')")
        switched = self._wait_for(
            lambda: (lambda s: s if s.get("file") and s.get("file") != first.get("file")
                     and s.get("paused") is False else None)(self._state())
        )
        self.assertTrue(switched, "Next never switched to the second track")

        # Give the analyser a moment on the new source, then sample again.
        time.sleep(0.8)
        after = self._wait_for(
            lambda: (lambda s: s if s.get("peak", 0) > 0
                     and s.get("targetMax", 0) > 0.05 else None)(self._spectrum_peak()),
            timeout=15.0,
        )
        self.assertTrue(after and after.get("peak", 0) > 0,
                        "the equaliser went flat after Next Track")
        self.assertGreater(after.get("targetMax", 0), 0.05,
                           "the bars and spectrogram received no new energy")


if __name__ == "__main__":
    unittest.main()
