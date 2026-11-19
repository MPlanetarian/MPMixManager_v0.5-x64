#!/usr/bin/env python3
"""The MP Visualizer is refused on Raspberry Pi, and its FPS cap is a setting.

A Pi's VideoCore GPU cannot raymarch the twelve-scene SDF at a usable rate, so
rather than ship a control that opens a window the board cannot drive, every
launcher refuses it. This pins:

  * the platform guard itself, including the escape hatch,
  * that the renderer's own entry point refuses before opening a window,
  * that each launcher surfaces a message instead of failing silently,
  * the FPS cap accepting 30 / 60 / 120 / 144 and 0 (follow the display),
  * the Settings menu offering exactly those choices.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mp_raspberry_pi as pi  # noqa: E402

MGR = ROOT / "Mix_Archive_Manager.sh"
VISUALIZER = ROOT / "scripts" / "mp_liquid_visualizer_vulkan.py"


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          cwd=str(ROOT))


class PlatformGuardTest(unittest.TestCase):
    def test_a_raspberry_pi_is_refused(self):
        with patch.object(pi, "is_raspberry_pi", return_value=True), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MP_FORCE_VISUALIZER_ON_PI", None)
            self.assertFalse(pi.visualizer_supported())

    def test_an_ordinary_machine_is_allowed(self):
        with patch.object(pi, "is_raspberry_pi", return_value=False):
            os.environ.pop("MP_FORCE_VISUALIZER_ON_PI", None)
            self.assertTrue(pi.visualizer_supported())

    def test_the_escape_hatch_re_enables_it_for_a_deliberate_test(self):
        with patch.object(pi, "is_raspberry_pi", return_value=True), \
             patch.dict(os.environ, {"MP_FORCE_VISUALIZER_ON_PI": "1"}):
            self.assertTrue(pi.visualizer_supported())

    def test_the_reason_names_the_platform_and_the_gpu(self):
        text = pi.visualizer_unsupported_reason()
        self.assertIn("Raspberry Pi", text)
        self.assertIn("VideoCore", text)
        # It must not imply the whole suite is broken.
        self.assertIn("every other mp app", text.lower())


class RendererRefusesBeforeOpeningTest(unittest.TestCase):
    def test_run_visualizer_returns_nonzero_on_a_pi(self):
        """A refused launch must not create a window: it exits before the renderer."""
        script = (
            "import sys; sys.path.insert(0, 'scripts'); sys.path.insert(0, '.')\n"
            "import mp_raspberry_pi as pi\n"
            "pi.is_raspberry_pi = lambda: True\n"
            "import mp_liquid_visualizer_vulkan as v\n"
            "sys.exit(v.run_visualizer('Cosmic Dreamworlds (Liquid)'))\n"
        )
        res = subprocess.run([sys.executable, "-c", script], capture_output=True,
                             text=True, cwd=str(ROOT), timeout=120,
                             env={**os.environ, "MP_FORCE_VISUALIZER_ON_PI": ""})
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("Raspberry Pi", res.stderr + res.stdout)


class LaunchersSayWhyTest(unittest.TestCase):
    def test_the_console_menu_names_the_platform(self):
        text = MGR.read_text(encoding="utf-8")
        start = text.index("launch_mp_liquid_visualizer() {")
        fn = text[start:text.index("\n}\n", start)]
        self.assertIn("mp_is_raspberry_pi", fn)
        self.assertIn("not available on Raspberry Pi", fn)

    def test_the_shell_detector_exists(self):
        lib = (ROOT / "scripts" / "lib" / "mp_pi_detect.sh").read_text(encoding="utf-8")
        self.assertIn("mp_is_raspberry_pi()", lib)


class FpsCapSettingTest(unittest.TestCase):
    def test_each_offered_rate_is_accepted(self):
        for value in ("30", "60", "120", "144"):
            with patch.dict(os.environ, {"MP_VISUALIZER_FPS_CAP": value}):
                self.assertEqual(pi.visualizer_fps_cap(), int(value), value)

    def test_zero_caps_nothing(self):
        with patch.dict(os.environ, {"MP_VISUALIZER_FPS_CAP": "0"}):
            self.assertEqual(pi.visualizer_fps_cap(), 0)

    def test_a_typo_is_clamped_rather_than_freezing_the_window(self):
        with patch.dict(os.environ, {"MP_VISUALIZER_FPS_CAP": "1"}):
            self.assertGreaterEqual(pi.visualizer_fps_cap(), 10)
        with patch.dict(os.environ, {"MP_VISUALIZER_FPS_CAP": "99999"}):
            self.assertLessEqual(pi.visualizer_fps_cap(), 240)

    def test_the_settings_menu_offers_the_four_rates(self):
        text = MGR.read_text(encoding="utf-8")
        self.assertIn("configure_visualizer_fps_cap", text)
        start = text.index("configure_visualizer_fps_cap() {")
        fn = text[start:text.index("\n}\n", start)]
        for rate in ("30 FPS", "60 FPS", "120 FPS", "144 FPS", "Uncapped"):
            self.assertIn(rate, fn, rate)
        # It must be reachable from Manager Settings (option 35).
        self.assertIn('[8]="configure_visualizer_fps_cap"', text)
        self.assertIn('"8|MP Visualizer Frame-Rate Cap|', text)

    def test_the_choice_is_persisted(self):
        text = MGR.read_text(encoding="utf-8")
        start = text.index("configure_visualizer_fps_cap() {")
        fn = text[start:text.index("\n}\n", start)]
        self.assertIn('save_config_setting "MP_VISUALIZER_FPS_CAP"', fn)
        self.assertIn('export MP_VISUALIZER_FPS_CAP', fn)


if __name__ == "__main__":
    unittest.main()
