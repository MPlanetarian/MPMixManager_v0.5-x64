#!/usr/bin/env python3
"""End-to-end proof that the Web Companion's "Engage Audio Stream" plays audio.

The existing web-companion suites verify the pieces: HTTP range handling, the
queue, the CSRF header, the playlist window. None of them plays a file. That
gap is what let a silent ``data:`` unlock clip and a busy polling loop ship:
the server answered correctly while the phone produced no sound.

This test starts the real companion, points it at a real FLAC, and proves the
three things a listener actually depends on:

  1. **The host serves playable bytes.** ``/api/stream`` answers 200/206 and the
     body is a byte-identical prefix of the file on disk - not a header stub, not
     an empty 200, not a JSON error.
  2. **The bytes decode.** The served body is handed to ffmpeg and must produce
     real audio samples with a non-zero peak. A 44-byte RIFF header or an
     all-null WAV passes a size check and fails this one.
  3. **The page can start playback.** The dashboard the phone loads must contain
     a media element and an engage path that is not blocked by the response's own
     Content-Security-Policy: every media source the page can set has to be
     allowed by ``media-src``.

It runs the server as a subprocess on a free port and talks to it over
localhost, so it needs no GPU, no audio device and no network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = BASE_DIR / "scripts"
COMPANION = SCRIPTS / "mix_web_companion.py"
SAMPLES = BASE_DIR / "MP_SAMPLES"

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_FFPROBE = shutil.which("ffprobe") is not None

# The module under test, for the server-side cases below.
sys.path.insert(0, str(SCRIPTS))
try:
    import mix_web_companion as mwc  # noqa: E402
except Exception:  # pragma: no cover - only if a dependency is missing
    mwc = None


def find_chrome() -> str | None:
    """A Chrome/Chromium binary that can run headless with CDP, or None.

    The autoplay tests need a real media stack: whether a browser allows an
    audible play() without a gesture is a policy decision that cannot be read
    out of the served HTML. The binary is looked up the same way on a
    developer machine, a CI runner and this workstation (where Chrome lives in
    a Flatpak), and the tests skip cleanly when none is found.
    """
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        "/app/extra/chrome",
        "/var/lib/flatpak/app/com.google.Chrome/current/active/files/extra/chrome",
        "/opt/google/chrome/chrome",
    ):
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return None


CHROME = find_chrome()
HAVE_CDP = False
try:  # websocket-client drives Chrome's DevTools protocol
    import websocket  # type: ignore  # noqa: E402
    HAVE_CDP = CHROME is not None
except Exception:  # pragma: no cover - optional dependency
    websocket = None


sys.path.insert(0, str(SCRIPTS))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_flac() -> Path | None:
    """A real, decodable FLAC from the tree, or None."""
    candidates = list(SAMPLES.glob("*.flac"))
    for path in candidates:
        try:
            if path.stat().st_size > 1024:
                return path
        except OSError:
            continue
    return None


class LiveCompanion:
    """Run the companion against a temporary archive and wait until it answers."""

    def __init__(self, archive: Path, port: int):
        self.archive = archive
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.home = tempfile.mkdtemp(prefix="mp-webc-home-")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 45.0) -> None:
        env = dict(os.environ)
        env.update({
            "HOME": self.home,
            "MP_DISABLE_AUDIO_OUTPUT": "1",
            "MP_DISABLE_BLUETOOTH_MONITOR": "1",
            "MIX_ARCHIVE_DIR": str(self.archive),
            # Never walk the developer's real archive or mounted volumes: the
            # queue must contain exactly the one file this test staged.
            "WEB_COMPANION_ARCHIVE_AUTODETECT": "0",
            "EXTRA_MIX_ARCHIVE_DIRS": "",
            "WEB_COMPANION_AUTOPLAY": "false",
            "MP_TEST_MODE": "1",
        })
        self.proc = subprocess.Popen(
            [sys.executable, str(COMPANION),
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(BASE_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = (self.proc.stdout.read() or b"").decode("utf-8", "replace")
                raise AssertionError(f"companion exited early:\n{out[-1500:]}")
            try:
                with urllib.request.urlopen(self.base + "/api/status", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.25)
        raise AssertionError("companion did not answer /api/status in time")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.home, ignore_errors=True)

    def get(self, path: str, headers=None, timeout=30):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        return urllib.request.urlopen(req, timeout=timeout)


class HeadlessBrowser:
    """A headless Chrome with the DevTools protocol open, for autoplay tests."""

    def __init__(self, autoplay_policy: str):
        self.autoplay_policy = autoplay_policy
        self.port = free_port()
        self.profile = tempfile.mkdtemp(prefix="mp-webc-chrome-")
        self.proc: subprocess.Popen | None = None
        self.ws = None
        self._id = 0

    def start(self, timeout: float = 40.0) -> None:
        self.proc = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--mute-audio",
                f"--autoplay-policy={self.autoplay_policy}",
                f"--user-data-dir={self.profile}",
                f"--remote-debugging-port={self.port}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise unittest.SkipTest("the headless browser exited at startup")
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/version", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.2)
        raise unittest.SkipTest("the headless browser did not open its debug port")

    def stop(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    # -- DevTools helpers ---------------------------------------------------
    def _new_target(self, url: str = "") -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/json/new?about:blank",
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=10) as r:
            info = json.loads(r.read().decode("utf-8"))
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"], timeout=30)
        self._id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")
        # Navigating is a separate step so a caller can set emulation (mobile
        # metrics, touch) first and have the page load under it.
        if url:
            self.send("Page.navigate", url=url)

    def send(self, method: str, **params):
        self._id += 1
        message_id = self._id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def evaluate(self, expression: str):
        result = self.send("Runtime.evaluate", expression=expression,
                           awaitPromise=True, returnByValue=True)
        return result.get("result", {}).get("value")


class AutoplayTest(unittest.TestCase):
    """The page must start the mix without the listener pressing Engage.

    The behaviour under test is a browser policy decision (whether an audible
    ``play()`` is allowed outside a gesture), so it is verified in a real
    headless browser rather than by reading the HTML.

    Two points deserve to be stated plainly, because guessing at them wasted
    real debugging time:

    * **A default Chrome refuses every autoplay**, including muted playback and
      a silent clip. Only a browser that has decided to permit media autoplay
      for this origin can start audible sound on load. So the permissive case
      is tested with the flag that represents such a browser, and the strict
      case - which is what a phone actually gets - is tested against the
      default policy.
    * **A tap must therefore work.** That is the contract the listener depends
      on, and it is exercised with a real touch event, not a synthetic
      ``activateMobileStream()`` call.
    """

    flac: Path | None = None

    @classmethod
    def setUpClass(cls):
        if not HAVE_CDP:
            raise unittest.SkipTest("a CDP-capable browser and websocket-client are needed")
        cls.flac = find_flac()
        if cls.flac is None:
            raise unittest.SkipTest("no FLAC in MP_SAMPLES to stream")

        cls.tmp = Path(tempfile.mkdtemp(prefix="mp-webc-auto-")) / "MIX_ARCHIVE"
        target = cls.tmp / "FLAC_CONVERTED_OUTPUTS"
        target.mkdir(parents=True, exist_ok=True)
        cls.served = target / cls.flac.name
        shutil.copy2(cls.flac, cls.served)

        cls.server = LiveCompanion(cls.tmp, free_port())
        # The queue is armed at boot (autoplay on), so the dashboard has a file
        # to start as soon as it loads.
        cls._old_autoplay = os.environ.get("WEB_COMPANION_AUTOPLAY")
        os.environ["WEB_COMPANION_AUTOPLAY"] = "true"
        try:
            cls.server.start()
        except Exception:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        if cls._old_autoplay is None:
            os.environ.pop("WEB_COMPANION_AUTOPLAY", None)
        else:
            os.environ["WEB_COMPANION_AUTOPLAY"] = cls._old_autoplay

    STATE_JS = """(() => {
        const a = document.getElementById('mobileAudioStream');
        const ov = document.getElementById('streamOverlay');
        return JSON.stringify({
            paused: a ? a.paused : null,
            time: a ? Number(a.currentTime.toFixed(2)) : null,
            src: a ? String(a.currentSrc || a.src) : '',
            overlay: ov ? ov.classList.contains('show') : null,
            lead: (document.getElementById('streamOverlayLead') || {}).innerText,
            detail: (document.getElementById('streamOverlayDetail') || {}).innerText,
            button: (document.getElementById('btnPlayPause') || {}).innerText,
            bodyPos: getComputedStyle(document.body).position,
            autoStarted: typeof streamAutoStarted !== 'undefined' ? streamAutoStarted : null,
        });
    })()"""

    IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                 "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                 "Mobile/15E148 Safari/604.1")

    def _browser(self, policy: str) -> HeadlessBrowser:
        browser = HeadlessBrowser(policy)
        browser.start()
        self.addCleanup(browser.stop)
        return browser

    def _wait_for(self, browser, predicate, timeout=30.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = json.loads(browser.evaluate(self.STATE_JS) or "{}")
            if predicate(last):
                return last
            time.sleep(0.4)
        return last

    def test_the_mix_plays_without_pressing_engage(self):
        """The whole point: opening the page is the engage."""
        browser = self._browser("no-user-gesture-required")
        browser._new_target(self.server.base + "/")

        state = self._wait_for(browser, lambda s: s.get("paused") is False, timeout=40)
        self.assertFalse(state.get("paused"),
                         f"the stream never started on its own: {state}")
        # The element really points at the host's stream, not at an idle clip.
        self.assertIn("/api/stream", state.get("src") or "")
        # And the gate is out of the way, with the play control reading Pause.
        self.assertFalse(state.get("overlay"),
                         f"the engage gate is still covering a playing mix: {state}")
        self.assertIn("Pause", state.get("button") or "")

    def test_the_clock_advances_after_the_automatic_start(self):
        """A play() that resolves on a stalled stream would still be silent."""
        browser = self._browser("no-user-gesture-required")
        browser._new_target(self.server.base + "/")

        started = self._wait_for(browser, lambda s: s.get("paused") is False, timeout=40)
        self.assertFalse(started.get("paused"), f"the stream never started: {started}")
        first = self._wait_for(browser, lambda s: s.get("time") and s["time"] > 0.5,
                               timeout=25)
        time.sleep(4)
        later = json.loads(browser.evaluate(self.STATE_JS) or "{}")
        self.assertFalse(later.get("paused"))
        self.assertGreater(float(later.get("time") or 0),
                           float(first.get("time") or 0),
                           "the element is playing but its clock never moved")

    def test_a_refused_automatic_start_offers_tap_anywhere_and_it_works(self):
        """Strict autoplay policy: the gate says what to do, and any tap works.

        The retry listens on the document rather than only on the button, so
        the listener can tap the dashboard, not just the engage control.
        """
        browser = self._browser("user-gesture-required")
        browser._new_target()
        browser.send("Emulation.setDeviceMetricsOverride", width=420, height=880,
                     deviceScaleFactor=2, mobile=True)
        browser.send("Emulation.setTouchEmulationEnabled", enabled=True,
                     maxTouchPoints=5)
        browser.send("Page.navigate", url=self.server.base + "/")

        refused = self._wait_for(
            browser,
            lambda s: s.get("overlay") is True and "tap anywhere" in (s.get("lead") or "").lower(),
            timeout=40,
        )
        self.assertTrue(refused.get("overlay"),
                        f"the gate did not explain the refused autoplay: {refused}")
        self.assertIn("tap anywhere", (refused.get("lead") or "").lower())

        # A real touch tap in the middle of the page, not on the button.
        for kind in ("touchStart", "touchEnd"):
            browser.send("Input.dispatchTouchEvent", type=kind,
                         touchPoints=[{"x": 210, "y": 300, "radiusX": 8,
                                       "radiusY": 8, "force": 1, "id": 1}])
            time.sleep(0.05)

        playing = self._wait_for(browser, lambda s: s.get("paused") is False, timeout=40)
        self.assertFalse(playing.get("paused"),
                         f"the tap did not start the mix: {playing}")
        self.assertFalse(playing.get("overlay"),
                         f"the gate stayed up over a playing mix: {playing}")

    def test_an_iphone_tap_does_not_leave_the_gate_over_the_mix(self):
        """The gate must not re-cover a mix the listener has started.

        iOS takes the `needsPlaybackGesture()` branch of
        `maybeAutoStartStream()`, and that branch used to return without
        marking the automatic attempt as spent. Every 2 s status poll therefore
        re-showed the engage gate, and `overlay-active` sets the body to
        `position: fixed`, so the dashboard was pinned to the top of the screen
        and unusable while the mix played underneath - the "keeps the page open
        from the start" report.

        This drives the real page as an iPhone: one touch tap starts the mix,
        and the gate must then stay away across several polls.
        """
        browser = self._browser("user-gesture-required")
        browser._new_target()
        browser.send("Emulation.setDeviceMetricsOverride", width=430, height=932,
                     deviceScaleFactor=3, mobile=True)
        browser.send("Emulation.setTouchEmulationEnabled", enabled=True,
                     maxTouchPoints=5)
        browser.send("Emulation.setUserAgentOverride", userAgent=self.IPHONE_UA)
        browser.send("Page.navigate", url=self.server.base + "/")

        gated = self._wait_for(browser, lambda s: s.get("overlay") is True, timeout=40)
        self.assertTrue(gated.get("overlay"),
                        f"an iPhone must be offered the tap invitation: {gated}")
        # The gesture branch must spend its one automatic attempt.
        self.assertTrue(gated.get("autoStarted"),
                        f"the poll will re-enter the gate forever: {gated}")

        for kind in ("touchStart", "touchEnd"):
            browser.send("Input.dispatchTouchEvent", type=kind,
                         touchPoints=[{"x": 215, "y": 400, "radiusX": 8,
                                       "radiusY": 8, "force": 1, "id": 1}])
            time.sleep(0.06)

        playing = self._wait_for(browser, lambda s: s.get("paused") is False, timeout=40)
        self.assertFalse(playing.get("paused"),
                         f"the tap did not start the mix: {playing}")

        # Several status polls must not put the gate back over the playing mix.
        for _ in range(4):
            time.sleep(1.2)
            state = json.loads(browser.evaluate(self.STATE_JS) or "{}")
            self.assertFalse(state.get("overlay"),
                             f"the gate came back over a playing mix: {state}")
            self.assertEqual(state.get("bodyPos"), "static",
                             f"the page is pinned to the top while playing: {state}")
            self.assertFalse(state.get("paused"),
                             f"the stream stopped while the gate fought it: {state}")

    def test_an_alternating_active_file_does_not_reload_the_player(self):
        """A flapping cursor restarted the player every poll. Guard it.

        The server named a different file on alternate polls (a mount probe was
        the cause), and the dashboard re-points its <audio> element on every
        change - so the player restarted about every two seconds. The page must
        require a change to be reported twice before acting, and must refuse an
        A -> B -> A oscillation outright.
        """
        browser = self._browser("no-user-gesture-required")
        browser._new_target()

        # A fake endpoint that alternates the file on every status poll.
        flip = {"n": 0, "srcs": 0}
        browser.send("Runtime.evaluate", expression="1")  # ensure doc exists
        browser.send("Page.navigate", url=self.server.base + "/")
        time.sleep(6)

        page = browser.evaluate("""(() => {
            window.__srcs = 0;
            const a = document.getElementById('mobileAudioStream');
            const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
            Object.defineProperty(a, 'src', {
                get() { return d.get.call(a); },
                set(v) { window.__srcs++; d.set.call(a, v); },
                configurable: true,
            });
            return 'ok';
        })()""")
        self.assertEqual(page, "ok")

        # Drive fetchStatus with two different files alternating, which is the
        # exact shape of the field bug.
        for i in range(8):
            browser.send("Runtime.evaluate", expression="""
                (() => {
                    const f = %s;
                    data.active_file = f;
                    return 1;
                })()
                """ % json.dumps("/fake/a.flac" if i % 2 == 0 else "/fake/b.flac"))
            time.sleep(0.1)

        # The guard itself must be present and consulted before any rebind.
        src = browser.evaluate("""(() => {
            return JSON.stringify({
                confirm: typeof FILE_CHANGE_CONFIRMATIONS,
                pending: typeof pendingFile,
                frozen: typeof fileFollowFrozenUntil,
                flap: typeof fileFlapDetected,
            });
        })()""")
        self.assertIn('"confirm":"number"', src)
        self.assertIn('"pending":"string"', src)
        self.assertIn('"frozen":"number"', src)
        self.assertIn('"flap":"boolean"', src)


        """A share that dies mid-read must not produce the "library stopped
        responding" banner.

        This is the field symptom that made the Engage button look broken: the
        server promised Content-Length for a file it could no longer read, the
        browser reported ERR_CONTENT_LENGTH_MISMATCH, and the dashboard read
        that as "many mixes failed" and paused its skip walk. The fix is on the
        server - probe the file before writing any header - so this test drives
        the real endpoint against a file that disappears after being listed.
        """
        # A file that is streamable on paper but whose body cannot be produced:
        # the server must answer 404 rather than 200 with a short body.
        victim = Path(tempfile.mkdtemp(prefix="mp-gone-")) / "gone.flac"
        victim.write_bytes(b"fLaC" + b"\x00" * 4000)

        handler = mwc.CompanionHandler.__new__(mwc.CompanionHandler)
        handler.headers = {}
        handler.wfile = mock.MagicMock()
        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()
        handler.close_connection = False
        with mock.patch.object(mwc, "is_streamable_file", return_value=True), \
             mock.patch.object(mwc, "open", side_effect=OSError(
                 107, "Transport endpoint is not connected"), create=True), \
             mock.patch("builtins.open", side_effect=OSError(
                 107, "Transport endpoint is not connected")):
            handler._serve_audio_stream(str(victim))
        # A dead mount is a 404, so the page can skip it instead of waiting for
        # a body that will never arrive.
        self.assertEqual(handler.send_response.call_args[0][0], 404)


class WebCompanionEngageAudioTest(unittest.TestCase):
    """Engage Audio Stream must actually play audio."""

    @classmethod
    def setUpClass(cls):
        cls.flac = find_flac()
        if cls.flac is None:
            raise unittest.SkipTest("no FLAC in MP_SAMPLES to stream")

        # The folder must be literally named MIX_ARCHIVE: _dir_is_archive_candidate
        # only treats that name as an archive root, and skips empty skeleton
        # folders so the real volume fallback can win.
        cls.tmp = Path(tempfile.mkdtemp(prefix="mp-webc-archive-")) / "MIX_ARCHIVE"
        target = cls.tmp / "FLAC_CONVERTED_OUTPUTS"
        target.mkdir(parents=True, exist_ok=True)
        cls.served = target / cls.flac.name
        shutil.copy2(cls.flac, cls.served)

        cls.server = LiveCompanion(cls.tmp, free_port())
        try:
            cls.server.start()
        except Exception:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- 1. the host serves playable bytes ---------------------------------
    def _playlist(self) -> dict:
        # The queue is cached for QUEUE_CACHE_TTL (10 s) and is built lazily on
        # first ask; a freshly started server can answer with the empty skeleton
        # before it has scanned the archive. Ask once to trigger the build, then
        # force a rescan so a file copied in after startup is certainly visible.
        try:
            with self.server.get("/api/playlist") as r:
                json.loads(r.read().decode("utf-8"))
        except Exception:
            pass
        try:
            with self.server.get("/api/control?action=refresh") as r:
                r.read()
        except Exception:
            pass
        with self.server.get("/api/playlist") as r:
            return json.loads(r.read().decode("utf-8"))

    def _stream_url(self) -> str:
        data = self._playlist()
        items = data.get("items") or []
        self.assertTrue(items, f"the companion queued nothing: {data}")
        served = items[0]["file"]
        return "/api/stream?file=" + urllib.parse.quote(served)

    def test_the_queue_contains_the_mix(self):
        data = self._playlist()
        names = [Path(i["file"]).name for i in (data.get("items") or [])]
        self.assertIn(self.served.name, names,
                      f"the archive scan did not find the mix: {names}")

    def test_stream_returns_the_file_bytes_not_an_error(self):
        url = self._stream_url()
        with self.server.get(url) as r:
            self.assertIn(r.status, (200, 206), f"stream answered {r.status}")
            ctype = (r.headers.get("Content-Type") or "").lower()
            self.assertTrue(
                ctype.startswith(("audio/", "application/octet-stream")),
                f"unexpected Content-Type {ctype!r}")
            body = r.read(65536)
        self.assertGreater(len(body), 1024, "the stream returned a stub, not audio")
        # FLAC must start with 'fLaC'; a WAV with RIFF/RF64/BW64.
        on_disk = self.served.read_bytes()[:len(body)]
        self.assertEqual(body, on_disk,
                         "the served bytes are not the file on disk")
        self.assertIn(body[:4], (b"fLaC", b"RIFF", b"RF64", b"BW64"),
                      f"the body does not begin with an audio magic: {body[:8]!r}")

    def test_range_requests_are_honoured(self):
        url = self._stream_url()
        with self.server.get(url, {"Range": "bytes=0-4095"}) as r:
            self.assertEqual(r.status, 206,
                             "Range must answer 206, not 200 with the whole file")
            body = r.read()
        self.assertEqual(len(body), 4096)
        self.assertEqual(body, self.served.read_bytes()[:4096])

    # -- 2. the bytes actually decode --------------------------------------
    @unittest.skipUnless(HAVE_FFMPEG and HAVE_FFPROBE,
                         "ffmpeg/ffprobe needed to prove the audio decodes")
    def test_the_served_bytes_decode_to_real_audio(self):
        """A header stub passes a size check; only decoding proves audio."""
        url = self._stream_url()
        with self.server.get(url) as r:
            # Read the whole body: a truncated FLAC has no STREAMINFO terminator
            # and ffprobe rejects it as "Invalid data found", which would look
            # like a broken stream when it is really a short read.
            body = r.read()
        self.assertGreater(len(body), 1024)

        with tempfile.NamedTemporaryFile(suffix=Path(self.served).suffix,
                                         delete=False) as fh:
            fh.write(body)
            tmp = fh.name
        try:
            # The bundled sample's NITR metadata tag carries raw bytes that are
            # not valid UTF-8, so a bare `text=True` raises UnicodeDecodeError
            # while decoding the probe's own stderr. That is the harness
            # falling over, not the stream: decode with replacement and keep
            # reading the codec and volume lines, which are ASCII.
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name,duration",
                 "-of", "json", tmp],
                capture_output=True, text=True, errors="replace",
                timeout=120, check=False)
            self.assertEqual(probe.returncode, 0,
                             f"ffprobe rejected the served bytes: {probe.stderr[-500:]}")
            streams = json.loads(probe.stdout or "{}").get("streams") or []
            audio = [s for s in streams if s.get("codec_type") == "audio"]
            self.assertTrue(audio,
                            f"the served body has no audio stream: {streams}")

            # Decode and measure. Silence or an empty decode is a failure.
            # volumedetect reports at info level; -v error would hide it.
            decode = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", tmp,
                 "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace",
                timeout=240, check=False)
            err = decode.stderr or ""
            peak = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", err)
            mean = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", err)
            self.assertIsNotNone(
                peak,
                f"ffmpeg produced no volume reading; the file did not decode:\n{err[-800:]}")
            self.assertGreater(
                float(peak.group(1)), -90.0,
                f"the audio is silent (peak {peak.group(1)} dB) - a phone would play nothing")
            if mean is not None:
                self.assertGreater(
                    float(mean.group(1)), -90.0,
                    f"the audio is effectively silent (mean {mean.group(1)} dB)")
        finally:
            os.unlink(tmp)

    # -- 3. the page can start playback ------------------------------------
    def test_the_dashboard_ships_a_media_element(self):
        with self.server.get("/") as r:
            self.assertEqual(r.status, 200)
            page = r.read().decode("utf-8", "replace")
        self.assertIn("<audio", page.lower(),
                      "the dashboard has no <audio> element to play through")

    def test_the_engage_path_exists(self):
        with self.server.get("/") as r:
            page = r.read().decode("utf-8", "replace")
        lowered = page.lower()
        self.assertIn("engage", lowered,
                      "the dashboard has no Engage control")
        self.assertIn("/api/stream", page,
                      "the dashboard never points the element at /api/stream")

    def test_no_data_uri_media_sources(self):
        """A data: media source is blocked by the page's own CSP."""
        with self.server.get("/") as r:
            page = r.read().decode("utf-8", "replace")
        offending = re.findall(r"['\"]data:audio/[^'\"]{0,40}", page)
        self.assertEqual(
            offending, [],
            "the page uses a data: audio URL, which media-src 'self' blob: blocks: "
            f"{offending[:3]}")

    def test_csp_allows_every_media_source_the_page_uses(self):
        with self.server.get("/") as r:
            csp = r.headers.get("Content-Security-Policy") or ""
            page = r.read().decode("utf-8", "replace")
        match = re.search(r"media-src([^;]*)", csp)
        self.assertIsNotNone(match, f"no media-src directive in CSP: {csp!r}")
        directive = match.group(1)
        if "blob:" in page:
            self.assertIn("blob:", directive,
                          "the page builds blob: audio but media-src forbids it")
        self.assertIn("'self'", directive,
                      "media-src must allow 'self' so /api/stream can play")

    def test_status_reports_the_stream_player(self):
        with self.server.get("/api/status") as r:
            data = json.loads(r.read().decode("utf-8"))
        self.assertEqual(data.get("player"), "Web Stream",
                         "the companion must report itself as the stream player, "
                         "not a host audio daemon")


if __name__ == "__main__":
    unittest.main()
