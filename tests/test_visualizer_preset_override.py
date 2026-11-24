#!/usr/bin/env python3
"""An explicit preset request must survive the player-bond sync.

The bug this locks down: pressing a preset (or running
``MP_Liquid_Visualizer.py --preset N``) opened a window that showed a
completely different scene. Every preset looked like the same flat, banded
river, and presets 11 and 12 were reported as "not 3D at all".

The cause was the player bond. The Liquid Player publishes its own
``preset_id`` (~/.config/mix-manager/liquid_player_visualizer.json) and
``_sync_bonded_preset`` copied it onto the visualizer on the first poll. That
is right for the companion window the player spawns, but it also overrode a
preset the user had just chosen, so every window snapped back to whatever the
player had saved. On the reporting machine that was Dynamic Waves P2 (id 12),
which is why presets 0-11 all looked like the same 2D river.

Two smaller bugs in the same path:

* ``--self-test`` dropped its ``preset_name``, so ``--preset N --self-test``
  saved the choice and then rendered the previous one.
* The UBO staging buffer was ``F32 * 36`` while ``VisualizerUBO`` is 40 floats,
  so the entire ``stream`` vec4 (PID phase and heat) was lost whenever the
  mapped buffer was unavailable.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mp_liquid_visualizer_vulkan as vulkan_vis  # noqa: E402
import visualizer_player_bond as bond_mod  # noqa: E402


def _stub_renderer(preset_id: int) -> MagicMock:
    """A renderer double with just what _sync_bonded_preset touches."""
    r = MagicMock()
    r.preset = {"id": preset_id, "key": f"preset_{preset_id}", "name": f"Preset {preset_id}"}
    r._preset_pinned = False
    r.switch_preset = MagicMock()
    return r


PRESETS = [{"id": i, "key": f"preset_{i}", "name": f"Preset {i}"} for i in range(13)]


class TestExplicitPresetBeatsTheBond(unittest.TestCase):
    def test_pinned_window_keeps_its_preset(self):
        """A --preset window must not be dragged to the player's preset."""
        r = _stub_renderer(3)
        r._preset_pinned = True
        bond_mod.PlayerBondMixin._sync_bonded_preset(r, {"preset_id": 12}, lambda: PRESETS)
        r.switch_preset.assert_not_called()
        self.assertEqual(r.preset["id"], 3)

    def test_unpinned_window_still_follows_the_player(self):
        """The companion window the player spawns must still sync."""
        r = _stub_renderer(3)
        r._preset_pinned = False
        bond_mod.PlayerBondMixin._sync_bonded_preset(r, {"preset_id": 12}, lambda: PRESETS)
        r.switch_preset.assert_called_once()
        self.assertEqual(r.switch_preset.call_args[0][0]["id"], 12)

    def test_no_sync_when_already_on_the_players_preset(self):
        r = _stub_renderer(12)
        bond_mod.PlayerBondMixin._sync_bonded_preset(r, {"preset_id": 12}, lambda: PRESETS)
        r.switch_preset.assert_not_called()


class TestTheConstructorPinsANamedPreset(unittest.TestCase):
    def test_named_preset_pins(self):
        r = vulkan_vis.LiquidVisualizerVulkanRenderer("orbital_void_reflux")
        self.assertTrue(r._preset_pinned)
        self.assertEqual(r.preset["id"], 1)

    def test_unnamed_constructor_does_not_pin(self):
        """With no argument the window is a companion and may follow the player."""
        r = vulkan_vis.LiquidVisualizerVulkanRenderer(None)
        self.assertFalse(r._preset_pinned)

    def test_random_preset_does_not_pin(self):
        """Three launchers pass --preset random; pinning it would freeze the shuffle."""
        for arg in ("random", "rand", "rnd", "shuffle", "any", "-1"):
            r = vulkan_vis.LiquidVisualizerVulkanRenderer(arg)
            self.assertFalse(r._preset_pinned, f"{arg} must keep following the bond")


class TestSelfTestForwardsThePreset(unittest.TestCase):
    def test_self_test_call_passes_preset_name(self):
        source = (SCRIPTS / "mp_liquid_visualizer_vulkan.py").read_text(encoding="utf-8")
        self.assertIn("run_self_test(args.png, preset_name=preset_name, backend=args.backend)", source)
        # and it must not have reverted to dropping the argument
        self.assertNotIn("run_self_test(args.png, backend=args.backend)", source)

    def test_run_self_test_accepts_the_argument(self):
        import inspect
        sig = inspect.signature(vulkan_vis.run_self_test)
        self.assertIn("preset_name", sig.parameters)


class TestUniformBufferCoversTheWholeStruct(unittest.TestCase):
    def test_visualizer_ubo_is_forty_floats(self):
        self.assertEqual(vulkan_vis.sizeof(vulkan_vis.VisualizerUBO), 160)
        self.assertEqual(vulkan_vis.sizeof(vulkan_vis.VisualizerUBO) // 4, 40)

    def test_staging_buffer_is_not_short(self):
        """A 36-float staging buffer silently dropped the whole stream vec4."""
        source = (SCRIPTS / "mp_liquid_visualizer_vulkan.py").read_text(encoding="utf-8")
        self.assertNotIn("(F32 * 36)()", source)
        self.assertIn("(F32 * (sizeof(VisualizerUBO) // 4))()", source)


class TestTheOpenGLRendererHonoursTheSamePin(unittest.TestCase):
    """The OpenGL window had no pin, so the bond replaced a requested preset.

    ``--preset 12`` on the OpenGL backend opened the player's saved preset
    instead, because only the Vulkan renderer set ``_preset_pinned``. That made
    the Dynamic Waves pair unreachable from a bonded window, which is the
    backend most Linux desktops run.
    """

    def setUp(self):
        import liquid_player_visualizer as lpv
        self.lpv = lpv

    def test_named_preset_pins(self):
        r = self.lpv.LiquidVisualizerOpenGLRenderer("stream_of_frequency_dynamic_waves")
        self.assertTrue(r._preset_pinned)
        self.assertEqual(r.preset["id"], vulkan_vis.STREAM_WAVES_HORIZONTAL_ID)

    def test_vertical_preset_pins(self):
        r = self.lpv.LiquidVisualizerOpenGLRenderer("stream_of_frequency_dynamic_waves_p2")
        self.assertTrue(r._preset_pinned)
        self.assertEqual(r.preset["id"], vulkan_vis.STREAM_WAVES_VERTICAL_ID)

    def test_unnamed_constructor_does_not_pin(self):
        r = self.lpv.LiquidVisualizerOpenGLRenderer(None)
        self.assertFalse(r._preset_pinned)

    def test_random_stays_unpinned(self):
        for arg in ("random", "rand", "rnd", "shuffle", "any", "-1"):
            r = self.lpv.LiquidVisualizerOpenGLRenderer(arg)
            self.assertFalse(r._preset_pinned, f"{arg} must keep following the bond")

    def test_a_pinned_opengl_window_survives_the_bond_sync(self):
        from unittest.mock import MagicMock
        r = self.lpv.LiquidVisualizerOpenGLRenderer("stream_of_frequency_dynamic_waves")
        r.switch_preset = MagicMock()
        bond_mod.PlayerBondMixin._sync_bonded_preset(r, {"preset_id": 4}, lambda: PRESETS)
        r.switch_preset.assert_not_called()
        self.assertEqual(r.preset["id"], vulkan_vis.STREAM_WAVES_HORIZONTAL_ID)


class TestTheShippedDynamicWavesReplacement(unittest.TestCase):
    """Preset 11 now ships as the generated ribbon vault in the repo.

    The replacement lives in ``shaders/presets/builtin-11`` and uses the same
    folder layout as a user override, so Vulkan, OpenGL and Metal all resolve
    it without a second code path. A user override for the same id still wins.
    """

    def setUp(self):
        import mp_visualizer_user_presets as presets

        self.presets = presets
        self.temp = tempfile.TemporaryDirectory(prefix="mpvis-bundled-")
        root = Path(self.temp.name)
        self._saved = (
            presets.USER_PRESET_ROOT,
            presets.REGISTRY_FILE,
            presets.OVERRIDE_FILE,
        )
        presets.USER_PRESET_ROOT = root / "visualizer-presets"
        presets.REGISTRY_FILE = presets.USER_PRESET_ROOT / "registry.json"
        presets.OVERRIDE_FILE = presets.USER_PRESET_ROOT / "overrides.json"

    def tearDown(self):
        (
            self.presets.USER_PRESET_ROOT,
            self.presets.REGISTRY_FILE,
            self.presets.OVERRIDE_FILE,
        ) = self._saved
        self.temp.cleanup()

    def test_preset_eleven_serves_every_backend_from_the_repo(self):
        preset = self.presets.find_override(11)
        self.assertIsNotNone(preset)
        self.assertEqual(preset["key"], "builtin-11")
        self.assertEqual(preset["name"], "Stream of Frequency — Dynamic Waves")
        self.assertEqual(preset["album"], "MPlanetarian - Dreamworlds Productions V3")
        expected_dir = BASE / "shaders" / "presets" / "builtin-11"
        for key, name in (
            ("vulkan_spv", "preset.frag.spv"),
            ("vulkan_source", "preset.frag"),
            ("opengl_source", "preset.opengl.frag"),
            ("metal_source", "preset.metal"),
        ):
            self.assertEqual(Path(preset[key]), expected_dir / name, key)
            self.assertTrue(Path(preset[key]).is_file(), key)
        self.assertTrue(preset["cover_paths"])
        self.assertEqual(Path(preset["cover_paths"][0]), expected_dir / "cover.png")

    def test_the_backend_lookups_return_the_bundled_files(self):
        self.assertIsNotNone(self.presets.vulkan_spirv_for(11))
        self.assertIsNotNone(self.presets.opengl_source_for(11))
        self.assertIsNotNone(self.presets.metal_source_for(11))

    def test_a_user_override_still_beats_the_bundled_preset(self):
        user_dir = self.presets.override_dir(11)
        user_dir.mkdir(parents=True)
        (user_dir / "preset.json").write_text(
            json.dumps({"id": 11, "name": "Local Waves", "cover": "cover.png"}),
            encoding="utf-8",
        )
        for name in ("preset.frag", "preset.opengl.frag", "preset.metal", "cover.png"):
            (user_dir / name).write_text("// local\n", encoding="utf-8")
        (user_dir / "preset.frag.spv").write_bytes(b"\x03\x02\x23\x07" + b"\0" * 32)
        self.presets.set_override(user_dir, 11, {"name": "Local Waves"})
        preset = self.presets.find_override(11)
        self.assertIsNotNone(preset)
        self.assertEqual(preset["name"], "Local Waves")
        self.assertEqual(Path(preset["preset_dir"]), user_dir)


if __name__ == "__main__":
    unittest.main()
