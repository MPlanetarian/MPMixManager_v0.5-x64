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

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = BASE_DIR / "scripts"
COMPANION = SCRIPTS / "mix_web_companion.py"
SAMPLES = BASE_DIR / "MP_SAMPLES"

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_FFPROBE = shutil.which("ffprobe") is not None

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
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name,duration",
                 "-of", "json", tmp],
                capture_output=True, text=True, timeout=120, check=False)
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
                capture_output=True, text=True, timeout=240, check=False)
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
