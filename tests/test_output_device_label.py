#!/usr/bin/env python3
"""The CLI Output line names the system sink, not PortAudio's "default" alias."""

import unittest

from MP_Audio_Player import output_device_label, portaudio_name_is_generic


class OutputDeviceLabelTest(unittest.TestCase):
    def test_default_alias_uses_the_system_sink(self):
        sink = "GA102 High Definition Audio Controller Digital Stereo (HDMI - HISENSE) [HISENSE]"
        self.assertTrue(portaudio_name_is_generic("default"))
        self.assertTrue(portaudio_name_is_generic("Default"))
        self.assertTrue(portaudio_name_is_generic("default:CARD=NVidia"))
        self.assertEqual(output_device_label("default", sink), sink)

    def test_a_named_device_is_left_alone(self):
        self.assertFalse(portaudio_name_is_generic("Starship/Matisse HD Audio"))
        self.assertEqual(
            output_device_label("Starship/Matisse HD Audio", "Something Else"),
            "Starship/Matisse HD Audio",
        )

    def test_missing_system_name_keeps_the_alias(self):
        self.assertEqual(output_device_label("default", ""), "default")
        self.assertEqual(output_device_label("default", "Default Audio Output"), "default")


if __name__ == "__main__":
    unittest.main()
