#!/usr/bin/env python3
"""
Unit and Integration Tests for Kontrol S4 Hardware MIDI Support
MP Mix Archive Manager v0.5.3
"""

import os, sys, time, json, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure scripts directory is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT))

import dj_s4_midi
from dj_s4_midi import (
    DJS4MidiController,
    detect_dj_s4_port,
    freebsd_midi_devices,
    is_daemon_running,
    CONFIG_DIR,
    PID_FILE,
    STATE_FILE,
)

# Tests must not touch the live service files in ~/.config/mix-manager:
# run_amidi_stream() and the port-repair path write log and state entries.
_TEST_TMP = Path(tempfile.mkdtemp(prefix="s4midi-test-"))
dj_s4_midi.LOG_FILE = _TEST_TMP / "dj_s4_midi.log"
dj_s4_midi.STATE_FILE = _TEST_TMP / "dj_s4_midi_state.json"
dj_s4_midi.PID_FILE = _TEST_TMP / "dj_s4_midi.pid"
dj_s4_midi.LOCK_FILE = _TEST_TMP / "dj_s4_midi.lock"


class TestDJS4MidiController(unittest.TestCase):
    def setUp(self):
        self.controller = DJS4MidiController(port="hw:0,0")

    def test_port_detection(self):
        detected, port, details = detect_dj_s4_port()
        self.assertIsInstance(detected, bool)
        self.assertIsInstance(port, str)
        if dj_s4_midi.IS_FREEBSD:
            # FreeBSD exposes MIDI through umidi(4) as a path, never ALSA "hw:".
            self.assertTrue(port.startswith("/dev/") or port.startswith("hw:"))
        else:
            self.assertIn("hw:", port)

    def test_pitch_cc_scaling_center_deadband(self):
        # Center values must firmly snap to 0%
        self.assertEqual(self.controller.scale_pitch_cc_to_percent(64), 0)
        self.assertEqual(self.controller.scale_pitch_cc_to_percent(63), 0)
        self.assertEqual(self.controller.scale_pitch_cc_to_percent(65), 0)
        self.assertEqual(self.controller.scale_pitch_cc_to_percent(62), 0)
        self.assertEqual(self.controller.scale_pitch_cc_to_percent(66), 0)

    def test_pitch_cc_scaling_extremes(self):
        # 0 -> -20%, 127 -> +20%
        self.assertEqual(self.controller.scale_pitch_cc_to_percent(0), -20)
        self.assertEqual(self.controller.scale_pitch_cc_to_percent(127), 20)

    def test_pitch_bend_scaling(self):
        # Center 8192 -> 0%
        self.assertEqual(self.controller.scale_pitch_bend_to_percent(0, 64), 0)
        # Min 0 -> -20%
        self.assertEqual(self.controller.scale_pitch_bend_to_percent(0, 0), -20)
        # Max 16383 -> +20%
        self.assertEqual(self.controller.scale_pitch_bend_to_percent(127, 127), 20)

    @patch("dj_s4_midi.send_player_ipc")
    def test_deck_a_pitch_bend_event(self, mock_ipc):
        # 0xE0 = Pitch bend on channel 1 (Left Deck)
        desc, action = self.controller.process_midi_event(0xE0, 0, 96) # Above center
        self.assertIn("Pitch Bend Ch 1", desc)
        self.assertIn("DJ Pitch", action)
        mock_ipc.assert_called_with("set_pitch", percent=unittest.mock.ANY)

    @patch("dj_s4_midi.send_player_ipc")
    def test_deck_b_pitch_fader_cc(self, mock_ipc):
        # CC 15 on Channel 2 = Right Deck pitch fader
        desc, action = self.controller.process_midi_event(0xB1, 15, 100)
        self.assertIn("CC 15", desc)
        self.assertIn("DJ Pitch", action)
        mock_ipc.assert_called_with("set_pitch", percent=unittest.mock.ANY)

    @patch("dj_s4_midi.send_player_ipc")
    def test_play_pause_button(self, mock_ipc):
        # Note On 0, Vel 127
        desc, action = self.controller.process_midi_event(0x90, 0, 127)
        self.assertIn("Play / Pause", action)
        mock_ipc.assert_called_with("toggle")

    @patch("dj_s4_midi.send_player_ipc")
    def test_cue_button(self, mock_ipc):
        # Note On 2, Vel 127
        desc, action = self.controller.process_midi_event(0x90, 2, 127)
        self.assertIn("CUE", action)
        mock_ipc.assert_called_with("seek_to", position=0.0)

    @patch("dj_s4_midi.send_player_ipc")
    def test_sync_pitch_reset(self, mock_ipc):
        # Note On 6 (Left Deck Sync)
        desc, action = self.controller.process_midi_event(0x90, 6, 127)
        self.assertIn("Sync", action)
        mock_ipc.assert_called_with("reset_pitch")

    @patch("dj_s4_midi.send_player_ipc")
    def test_pitch_nudge_buttons(self, mock_ipc):
        # Note On 8 (Pitch +)
        desc, action = self.controller.process_midi_event(0x90, 8, 127)
        self.assertIn("Pitch Nudge Up", action)
        mock_ipc.assert_called_with("pitch_up", step=1)

        # Note On 9 (Pitch -)
        desc, action = self.controller.process_midi_event(0x90, 9, 127)
        self.assertIn("Pitch Nudge Down", action)
        mock_ipc.assert_called_with("pitch_down", step=1)

    @patch("dj_s4_midi.send_player_ipc")
    def test_browse_encoder(self, mock_ipc):
        # CC 42 clockwise (val > 64)
        self.controller.last_turn_time = 0.0
        desc, action = self.controller.process_midi_event(0xB0, 42, 65)
        self.assertIn("Next Track", action)
        mock_ipc.assert_called_with("next")

        # CC 42 counter-clockwise (val < 64)
        self.controller.last_turn_time = 0.0
        desc, action = self.controller.process_midi_event(0xB0, 42, 63)
        self.assertIn("Previous Track", action)
        mock_ipc.assert_called_with("prev")

    @patch("dj_s4_midi.send_player_ipc")
    def test_channel_volume_fader(self, mock_ipc):
        # CC 8 (Channel A Fader) at 64 (~50%)
        desc, action = self.controller.process_midi_event(0xB0, 8, 64)
        self.assertIn("Volume: 50%", action)
        mock_ipc.assert_called_with("set_volume", volume=50)

    @patch("dj_s4_midi.send_player_ipc")
    def test_loop_seek_buttons(self, mock_ipc):
        # Note 16 (Loop In) -> Seek -10s
        desc, action = self.controller.process_midi_event(0x90, 16, 127)
        self.assertIn("Quick Seek -10s", action)
        mock_ipc.assert_called_with("seek", seconds=-10.0)

        # Note 17 (Loop Out) -> Seek +10s
        desc, action = self.controller.process_midi_event(0x90, 17, 127)
        self.assertIn("Quick Seek +10s", action)
        mock_ipc.assert_called_with("seek", seconds=10.0)

    @patch("dj_s4_midi.send_player_ipc")
    def test_recording_toggle(self, mock_ipc):
        # Note 32 (Loop Recorder REC)
        desc, action = self.controller.process_midi_event(0x90, 32, 127)
        self.assertIn("Loop Recorder REC", action)
        mock_ipc.assert_called_with("toggle_record")


class TestPortAutoDetection(unittest.TestCase):
    """The S4 is not always card 0.

    HDMI audio commonly takes card 0, which pushes the S4 to hw:1,0. The old
    hardcoded hw:0,0 / /dev/snd/midiC0D0 therefore never opened the device.
    """

    CARDS = """ 0 [NVidia         ]: HDA-Intel - HDA NVidia
                      HDA NVidia at 0xb5080000 irq 172
 1 [DJKontrolS]: snd-usb-caiaq - Kontrol S4
                      Native Instruments Kontrol S4 (usb-0000:00:14.0-11.3)
 2 [MPKmini2       ]: USB-Audio - MPKmini2
                      AKAI MPKmini2 at usb-0000:00:14.0-11.2.4, full speed
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proc = self.root / "proc"
        self.dev = self.root / "dev"
        (self.proc / "asound").mkdir(parents=True)
        (self.proc / "asound" / "cards").write_text(self.CARDS)
        (self.dev / "snd").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _add_card_midi(self, card, dev=0, text="Kontrol S4"):
        d = self.proc / "asound" / f"card{card}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"midi{dev}").write_text(text + "\n")

    def _add_node(self, card, dev=0):
        (self.dev / "snd" / f"midiC{card}D{dev}").write_text("")

    def test_finds_s4_on_card_one(self):
        self._add_card_midi(1)
        self._add_node(1)
        detected, port, details = detect_dj_s4_port(str(self.proc), str(self.dev))
        self.assertTrue(detected)
        self.assertEqual(port, "hw:1,0")
        self.assertIn("Kontrol", details)

    def test_ignores_other_cards(self):
        self._add_card_midi(0, text="HDA NVidia")
        self._add_card_midi(2, text="MPKmini2")
        self._add_card_midi(1)
        detected, port, _ = detect_dj_s4_port(str(self.proc), str(self.dev))
        self.assertTrue(detected)
        self.assertEqual(port, "hw:1,0")

    def test_no_device_falls_back_to_default(self):
        (self.proc / "asound" / "cards").write_text(
            " 0 [NVidia         ]: HDA-Intel - HDA NVidia\n"
        )
        # Keep the host's real `amidi -l` out of this hermetic check.
        empty = MagicMock(stdout="", stderr="", returncode=0)
        with patch("dj_s4_midi.subprocess.run", return_value=empty):
            detected, port, _ = detect_dj_s4_port(str(self.proc), str(self.dev))
        self.assertFalse(detected)
        self.assertEqual(port, dj_s4_midi.DEFAULT_PORT)

    def test_resolve_port_replaces_stale_default(self):
        self._add_card_midi(1)
        self._add_node(1)
        port = dj_s4_midi.resolve_port("hw:0,0", str(self.proc), str(self.dev))
        self.assertEqual(port, "hw:1,0")

    def test_resolve_port_keeps_explicit_working_port(self):
        self._add_card_midi(1)
        self._add_node(1)
        port = dj_s4_midi.resolve_port("hw:1,0", str(self.proc), str(self.dev))
        self.assertEqual(port, "hw:1,0")

    def test_rawmidi_path_mapping(self):
        self._add_node(1)
        self.assertEqual(
            dj_s4_midi.rawmidi_path_for_port("hw:1,0", str(self.dev)),
            str(self.dev / "snd" / "midiC1D0"),
        )
        self.assertIsNone(dj_s4_midi.rawmidi_path_for_port("hw:0,0", str(self.dev)))

    def test_port_from_rawmidi_path(self):
        self.assertEqual(dj_s4_midi.port_from_rawmidi_path("/dev/snd/midiC3D1"), "hw:3,1")
        self.assertIsNone(dj_s4_midi.port_from_rawmidi_path("/dev/umidi0.0"))

    def test_controller_repairs_stale_hardcoded_port(self):
        with patch("dj_s4_midi.detect_dj_s4_port",
                   return_value=(True, "hw:1,0", "Kontrol S4")), \
             patch("dj_s4_midi.rawmidi_path_for_port",
                   side_effect=lambda p, dev_root="/dev": "/dev/snd/midiC1D0" if p == "hw:1,0" else None):
            ctrl = DJS4MidiController(port="hw:0,0")
        self.assertEqual(ctrl.port, "hw:1,0")
        self.assertEqual(ctrl.rawmidi_dev, "/dev/snd/midiC1D0")


class TestRawmidiReadLoop(unittest.TestCase):
    """Bytes arriving on the rawmidi node must reach the event mapper."""

    def test_stream_processes_messages_from_device_node(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("platform has no FIFOs")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "midi"
            os.mkfifo(fifo)
            with patch("dj_s4_midi.detect_dj_s4_port",
                       return_value=(False, "hw:99,0", "")):
                ctrl = DJS4MidiController(port="hw:99,0")
            ctrl.rawmidi_dev = str(fifo)
            ctrl.running = True
            seen = []

            def fake_process(status, data1, data2):
                seen.append((status, data1, data2))
                return "desc", "Ignored"

            ctrl.process_midi_event = fake_process
            th = threading.Thread(target=ctrl.run_rawmidi_stream, daemon=True)
            th.start()
            with open(fifo, "wb", buffering=0) as writer:
                time.sleep(0.2)
                writer.write(bytes([0xB0, 0x63, 0x01]))  # CC 99, value 1
            deadline = time.time() + 2
            while time.time() < deadline and not seen:
                time.sleep(0.02)
            ctrl.running = False
            th.join(timeout=3)
        self.assertEqual(seen, [(0xB0, 0x63, 0x01)])
        self.assertFalse(th.is_alive(), "reader should stop when running is cleared")


class TestSingleInstanceLock(unittest.TestCase):
    """Two daemons used to race for the exclusive rawmidi node."""

    def test_second_lock_is_refused(self):
        fd = dj_s4_midi.acquire_single_instance_lock()
        if fd == "unsupported":
            self.skipTest("platform has no fcntl")
        self.assertIsNotNone(fd)
        try:
            self.assertIsNone(dj_s4_midi.acquire_single_instance_lock())
        finally:
            os.close(fd)


class TestFreeBSDMidiFallback(unittest.TestCase):
    """FreeBSD has no ALSA, no amidi and no /dev/snd tree.

    The daemon used to die with FileNotFoundError: 'amidi' as soon as it
    started on FreeBSD, because run_amidi_stream() always spawned amidi.
    """

    def test_freebsd_midi_devices_is_empty_off_freebsd(self):
        if not dj_s4_midi.IS_FREEBSD:
            self.assertEqual(freebsd_midi_devices(), [])

    def test_rawmidi_path_uses_umidi_on_freebsd(self):
        if not dj_s4_midi.IS_FREEBSD:
            self.skipTest("FreeBSD-only path")
        ctrl = DJS4MidiController(port="hw:0,0")
        if ctrl.rawmidi_dev is not None:
            self.assertTrue(ctrl.rawmidi_dev.startswith("/dev/"))
            self.assertNotIn("/dev/snd/", ctrl.rawmidi_dev)

    def test_amidi_stream_stands_by_without_amidi(self):
        """No traceback, no subprocess, just a clean standby loop."""
        ctrl = DJS4MidiController(port="hw:0,0")
        ctrl.running = True
        with patch("dj_s4_midi.subprocess.Popen") as mock_popen, \
             patch("dj_s4_midi.shutil.which", return_value=None), \
             patch("dj_s4_midi.os.path.exists", return_value=False):
            import threading
            th = threading.Thread(target=ctrl.run_amidi_stream, daemon=True)
            th.start()
            time.sleep(0.3)
            ctrl.running = False
            th.join(timeout=3.0)
        self.assertFalse(th.is_alive(), "run_amidi_stream should exit when stopped")
        mock_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()