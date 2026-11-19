#!/usr/bin/env python3
"""Tests for the MIDI DJ Mixer Profiles (Kontrol S4 and Kontrol S3)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIXER_DIR = PROJECT_ROOT / "MP_Liquid_Mixer"
sys.path.insert(0, str(MIXER_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

import mixer_midi  # noqa: E402


class TestDetection(unittest.TestCase):
    def _proc(self, tmp, cards):
        root = Path(tmp) / "asound"
        dev = Path(tmp) / "snd"
        dev.mkdir(parents=True)
        for card, (name, sub) in cards.items():
            directory = root / f"card{card}"
            directory.mkdir(parents=True)
            (directory / f"midi{sub}").write_text(f"{name}\n\nType: Legacy\n", encoding="utf-8")
            (dev / f"midiC{card}D{sub}").write_text("", encoding="utf-8")
        return root, dev

    def test_only_a_dj_port_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, dev = self._proc(tmp, {
                1: ("Kontrol S4", 0),
                2: ("MPKmini2", 0),
            })
            found = mixer_midi.find_dj_devices(root, dev)
            self.assertEqual([item["name"] for item in found], ["Kontrol S4"])
            self.assertTrue(found[0]["path"].endswith("midiC1D0"))

    def test_a_non_dj_device_maps_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, dev = self._proc(tmp, {2: ("MPKmini2", 0)})
            self.assertEqual(mixer_midi.find_dj_devices(root, dev), [])

    def test_a_dj_name_without_a_rawmidi_node_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, dev = self._proc(tmp, {1: ("Kontrol S4", 0)})
            (dev / "midiC1D0").unlink()
            self.assertEqual(mixer_midi.find_dj_devices(root, dev), [])


class TestSequencer(unittest.TestCase):
    def test_the_sequencer_list_ignores_non_dj_ports(self):
        from types import SimpleNamespace
        output = (
            " Port    Client name                      Port name\n"
            " 20:0    Kontrol S4               Kontrol S4\n"
            " 24:0    MPKmini2                         MPKmini2 MIDI 1\n"
        )
        with patch("mixer_midi.shutil.which", return_value="/usr/bin/aseqdump"), \
             patch("mixer_midi.subprocess.run", return_value=SimpleNamespace(stdout=output)):
            ports = mixer_midi.find_dj_sequencer_ports()
        self.assertEqual(ports, [{"name": "Kontrol S4", "path": "20:0", "backend": "sequencer"}])

    def test_sequencer_events_are_parsed(self):
        self.assertEqual(
            mixer_midi.parse_sequencer_line(" 20:0   Note on                 0, note 0, velocity 127"),
            (0x90, 0, 127),
        )
        self.assertEqual(
            mixer_midi.parse_sequencer_line(" 20:0   Note off                0, note 48, velocity 0"),
            (0x80, 48, 0),
        )
        self.assertEqual(
            mixer_midi.parse_sequencer_line(" 20:0   Control change          0, controller 8, value 127"),
            (0xB0, 8, 127),
        )
        self.assertEqual(
            mixer_midi.parse_sequencer_line(" 20:0   Pitch bend              0, value 8192"),
            (0xE0, 0, 64),
        )
        self.assertIsNone(mixer_midi.parse_sequencer_line("Waiting for data."))


class TestMapping(unittest.TestCase):
    def test_the_default_map_contains_the_mixer_buttons(self):
        notes, cc = mixer_midi.load_mapping(Path("/does/not/exist.json"))
        self.assertEqual(notes[0], "play:A")
        self.assertEqual(notes[1], "play:B")
        self.assertEqual(notes[6], "sync:A")
        self.assertEqual(notes[7], "sync:B")
        self.assertEqual(notes[48], "shift:A")
        self.assertEqual(notes[32], "record")
        self.assertEqual(cc[8], "channel:A")
        self.assertEqual(cc[9], "channel:B")

    def test_a_user_override_file_changes_only_what_it_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            path.write_text(json.dumps({"notes": {"0": "sync:B"}, "cc": {"8": "channel:B"}}),
                            encoding="utf-8")
            notes, cc = mixer_midi.load_mapping(path)
            self.assertEqual(notes[0], "sync:B")
            self.assertEqual(notes[6], "sync:A")
            self.assertEqual(cc[8], "channel:B")
            self.assertEqual(cc[9], "channel:B")


class TestMessages(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.listener = mixer_midi.DJMidiListener(
            on_change=lambda action, value: self.events.append((action, value)),
            mapping_path=Path("/does/not/exist.json"),
        )

    def send(self, *data):
        for status, data1, data2 in self.listener._parse(bytes(data)):
            self.listener._dispatch(status, data1, data2)

    def test_play_and_sync_buttons_are_deck_specific(self):
        self.send(0x90, 0, 127)
        self.send(0x90, 1, 127)
        self.send(0x90, 6, 127)
        self.send(0x90, 7, 127)
        self.assertEqual(self.events, [
            ("play:A", 1.0), ("play:B", 1.0), ("sync:A", 1.0), ("sync:B", 1.0),
        ])

    def test_shift_release_is_sent(self):
        self.send(0x90, 48, 127)
        self.send(0x80, 48, 0)
        self.assertEqual(self.events, [("shift:A", 1.0), ("shift:A", 0.0)])

    def test_browse_and_record_buttons(self):
        self.send(0x90, 16, 127)
        self.send(0x90, 17, 127)
        self.send(0x90, 32, 127)
        self.assertEqual(self.events, [("browse", -1.0), ("browse", 1.0), ("record", 1.0)])

    def test_volume_and_pitch_cc_are_scaled(self):
        self.send(0xB0, 8, 127)
        self.send(0xB0, 14, 64)
        self.send(0xB0, 15, 0)
        self.assertEqual(self.events[0], ("channel:A", 1.0))
        self.assertEqual(self.events[1], ("pitch:A", 0.0))
        self.assertEqual(self.events[2], ("pitch:B", -20.0))

    def test_pitch_bend_is_deck_specific(self):
        self.send(0xE0, 0, 0)
        self.send(0xE1, 127, 127)
        self.assertEqual(self.events[0], ("pitch:A", -20.0))
        self.assertEqual(self.events[1], ("pitch:B", 20.0))

    def test_a_generic_midi_controller_has_no_map(self):
        # The listener itself only receives bytes from a DJ port. A note
        # outside the DJ map is simply ignored, never assigned by guess.
        self.send(0x90, 99, 127)
        self.assertEqual(self.events, [])


class TestMixerWiring(unittest.TestCase):
    def test_start_midi_constructs_the_profile_listener(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        with patch.object(app.mixer_midi, "MixerMidiListener") as listener:
            mixer._start_midi()
        listener.assert_called_once()
        listener.return_value.start.assert_called_once()

    def test_record_button_from_midi_toggles_the_recorder(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        with patch.object(mixer, "_toggle_recording") as toggle:
            mixer._apply_s4("record", 1.0)
        toggle.assert_called_once()

    def test_duplicate_button_events_are_coalesced(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        mixer.area = MagicMock()
        with patch.object(mixer, "_set_browse_target"), \
             patch.object(mixer, "_toggle_deck") as toggle:
            mixer._apply_s4("play:A", 1.0)
            mixer._apply_s4("play:A", 1.0)
        toggle.assert_called_once()

    def test_status_reports_the_mapped_controller(self):
        import MP_Liquid_Mixer as app
        mixer = app.LiquidMixer()
        listener = MagicMock()
        listener.connected = True
        listener.device_name = "Kontrol S3"
        listener.last_event = 0.0
        listener.last_message = "mapped"
        mixer.midi_listener = listener
        mixer.profile = "B"
        text, _color = mixer._s4_status()
        self.assertIn("DJ MIXER", text)
        self.assertIn("Kontrol S3", text)


class TestProfiles(unittest.TestCase):
    def test_the_two_profiles_bind_their_own_controller(self):
        self.assertTrue(mixer_midi.name_matches_profile("Kontrol S4 MIDI", "A"))
        self.assertFalse(mixer_midi.name_matches_profile("Kontrol S3 MIDI", "A"))
        self.assertTrue(mixer_midi.name_matches_profile("Kontrol S3 MIDI", "B"))
        self.assertFalse(mixer_midi.name_matches_profile("MPKmini2", "B"))

    def test_the_s3_map_reaches_all_four_channels(self):
        self.assertEqual(mixer_midi.S3_CC[8], "channel:A")
        self.assertEqual(mixer_midi.S3_CC[11], "channel:D")
        self.assertEqual(mixer_midi.S3_CC[16], "pitch:C")
        self.assertEqual(mixer_midi.S3_CC[23], "filter:D")
        self.assertEqual(mixer_midi.S3_CC[12], "crossfader")

    def test_the_s4_profile_keeps_its_original_map(self):
        self.assertEqual(mixer_midi.PROFILE_MAPS["A"][0], mixer_midi.S4_NOTES)
        self.assertEqual(mixer_midi.PROFILE_MAPS["A"][1], mixer_midi.S4_CC)

    def test_a_switched_listener_loads_the_other_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            listener = mixer_midi.MixerMidiListener(mapping_path=path, profile="A")
            self.assertEqual(listener.cc.get(8), "channel:A")
            listener.set_profile("B")
            self.assertEqual(listener.profile, "B")
            self.assertEqual(listener.cc.get(11), "channel:D")
            self.assertNotIn(19, listener.cc)

    def test_profile_overrides_are_namespaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            path.write_text(json.dumps({
                "profile": "B",
                "overrides": {"B": {"cc": {"70": "master_volume"}}},
            }), encoding="utf-8")
            notes_b, cc_b = mixer_midi.load_mapping(path, "B")
            notes_a, cc_a = mixer_midi.load_mapping(path, "A")
        self.assertEqual(cc_b.get(70), "master_volume")
        self.assertNotIn(70, cc_a)
        self.assertEqual(notes_a, mixer_midi.S4_NOTES)

    def test_bytes_split_into_channel_messages(self):
        events = mixer_midi.split_midi_bytes(b"\x90\x00\x7f\xb0\x08\x40")
        self.assertEqual(events, [(0x90, 0, 127), (0xB0, 8, 64)])


if __name__ == "__main__":
    unittest.main()
