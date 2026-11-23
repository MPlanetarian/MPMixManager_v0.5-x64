#!/usr/bin/env python3
"""Vulkan glass falls back to OpenGL on Wayland so its corners stay round."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import MP_Audio_Player_Liquid_Glass as glass  # noqa: E402


class WaylandGlassFallbackTests(unittest.TestCase):
    def test_wayland_re_execs_the_opengl_host(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False), \
             patch.object(glass.sys, "platform", "linux"), \
             patch.object(glass.os, "execv") as execv:
            self.assertTrue(glass._prefer_opengl_glass_on_wayland([]))
        argv = execv.call_args[0][1]
        self.assertTrue(str(argv[1]).endswith("MP_Audio_Player_Liquid_Glass_OpenGL.py"))

    def test_self_checks_stay_in_this_process(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False), \
             patch.object(glass.sys, "platform", "linux"), \
             patch.object(glass.os, "execv") as execv:
            self.assertFalse(glass._prefer_opengl_glass_on_wayland(["--self-test"]))
        execv.assert_not_called()

    def test_explicit_vulkan_opt_out_is_honoured(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0",
                                     "MP_LIQUID_GLASS_ALLOW_VULKAN": "1"}, clear=False), \
             patch.object(glass.sys, "platform", "linux"), \
             patch.object(glass.os, "execv") as execv:
            self.assertFalse(glass._prefer_opengl_glass_on_wayland([]))
        execv.assert_not_called()

    def test_x11_is_left_alone(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(glass.sys, "platform", "linux"), \
             patch.object(glass.os, "execv") as execv:
            self.assertFalse(glass._prefer_opengl_glass_on_wayland([]))
        execv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
