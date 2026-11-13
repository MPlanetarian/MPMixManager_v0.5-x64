#!/usr/bin/env python3
"""The CHROMATIC STORM preset must not evaluate the scenes it has faded out.

Preset 11 blends twelve full SDF sequences and holds one at weight 1.0 while
fading the next in, so ten of the twelve weights are always exactly 0.0. The
shader used to build all twelve at every march step, which cost more than the
70 missing frames per second the preset was reported to be short of: it ran at
about 5 fps while every other preset held 60 fps.

These tests pin the guard in place in the GLSL source (which both the Vulkan
and OpenGL backends compile from) and in the shipped SPIR-V manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

SHADER = ROOT / "shaders" / "mp_liquid_visualizer.frag"
PRESET_SPIRV = ROOT / "shaders" / "presets"

SEQUENCE_NAMES = (
    "csSeqFurnaceHull", "csSeqFinCascade", "csSeqSplitKeel", "csSeqBeadSurge",
    "csSeqEmberWake", "csSeqPlasmaCocoon", "csSeqSpineRing", "csSeqShardBloom",
    "csSeqHullStrata", "csSeqTwinVessel", "csSeqNozzleFlare", "csSeqStormCollapse",
)


def _shader_source() -> str:
    return SHADER.read_text(encoding="utf-8")


def _dispatch_block(source: str) -> str:
    """The csMapSequences body from the seq[] array to its return."""
    start = source.index("vec2 seq[12];")
    end = source.index("return vec2(dMix, mat);", start)
    return source[start:end]


class TestChromaticStormSkipsFadedScenes(unittest.TestCase):
    def test_every_sequence_is_guarded_by_its_own_weight(self):
        """Each scene is only evaluated while its crossfade weight is nonzero."""
        block = _dispatch_block(_shader_source())
        for index, name in enumerate(SEQUENCE_NAMES):
            guarded = re.search(
                r"if \(w\[" + str(index) + r"\]\s*>\s*0\.0\)\s*seq\[" + str(index) + r"\]\s*=\s*"
                + name + r"\(p\);",
                block,
            )
            self.assertIsNotNone(guarded, f"{name} is not guarded by w[{index}]")

    def test_no_sequence_is_called_unconditionally(self):
        """An unguarded call would restore the full twelve-scene cost."""
        block = _dispatch_block(_shader_source())
        real_lines = [ln.strip() for ln in block.splitlines() if "csSeq" in ln]
        self.assertTrue(real_lines, "the dispatch block lost its sequence calls")
        for line in real_lines:
            self.assertRegex(
                line,
                r"^if \(w\[\d+\]\s*>\s*0\.0\) seq\[\d+\]\s*=\s*csSeq\w+\(p\);$",
                f"unguarded sequence evaluation: {line}",
            )

    def test_a_skipped_slot_can_never_resolve_as_a_surface(self):
        """Skipped slots must stay out of both the distance blend and the surface.

        A skipped scene keeps its slot in ``seq[]``, so its placeholder value is
        multiplied by its (exactly zero) weight and contributes nothing to
        ``dMix``. The material scan is what could still pick it up, so that scan
        has to ignore any slot whose weight is zero.
        """
        block = _dispatch_block(_shader_source())
        scan = re.search(
            r"if \(w\[i\] > 0\.02 && seq\[i\]\.x < best\)", block
        )
        self.assertIsNotNone(
            scan, "the material scan must ignore slots whose weight is zero"
        )

    def test_gl_and_vulkan_sources_both_carry_the_guard(self):
        """The OpenGL port is derived from the same GLSL, so it inherits it."""
        from liquid_player_visualizer import opengl_visualizer_source

        gl = opengl_visualizer_source()
        guarded = re.findall(r"if \(w\[\d+\]\s*>\s*0\.0\) seq\[\d+\]\s*=\s*csSeq\w+\(p\);", gl)
        self.assertEqual(len(guarded), 12)
        for index in range(12):
            self.assertNotRegex(
                gl, r"^    seq\[" + str(index) + r"\] = csSeq\w+\(p\);", re.MULTILINE
            )


class TestShippedPresetModulesMatchTheShader(unittest.TestCase):
    def test_manifest_digest_matches_the_shader(self):
        """A stale digest silently loads the previous (slow) preset shader."""
        source = _shader_source().encode("utf-8")
        digest = hashlib.sha256(source).hexdigest()[:16]
        manifest = json.loads((PRESET_SPIRV / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"], digest,
                         "shaders/presets/manifest.json does not describe mp_liquid_visualizer.frag")

    def test_manifest_covers_every_preset_with_a_module(self):
        manifest = json.loads((PRESET_SPIRV / "manifest.json").read_text(encoding="utf-8"))
        for preset_id in manifest["presets"]:
            module = PRESET_SPIRV / f"p{preset_id}.frag.spv"
            self.assertTrue(module.is_file(), f"missing shipped module {module.name}")
            self.assertGreater(module.stat().st_size, 16)
            with module.open("rb") as handle:
                self.assertEqual(handle.read(4), b"\x03\x02\x23\x07",
                                 f"{module.name} is not SPIR-V")

    def test_preset_ten_module_is_present(self):
        """The preset this fix is about must ship a folded module."""
        self.assertTrue((PRESET_SPIRV / "p10.frag.spv").is_file())


class TestResolutionForChromaticStorm(unittest.TestCase):
    def test_shipped_module_is_used_when_the_digest_matches(self):
        source = _shader_source()
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        manifest = json.loads((PRESET_SPIRV / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"], digest)
        self.assertEqual(int(manifest["steps"]), 64)

    def test_a_stale_digest_is_reported_instead_of_passing_silently(self):
        """A stale manifest used to fall back to the old module without a word.

        That is how the slow CHROMATIC STORM shader survived a source fix: the
        manifest still described the previous revision, so the shipped (old)
        module kept loading and the picture never changed.
        """
        import contextlib
        import io
        import liquid_player_visualizer as lpv

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shaders = tmp_path / "shaders"
            shaders.mkdir()
            shutil.copy(SHADER, shaders / "mp_liquid_visualizer.frag")
            presets = shaders / "presets"
            presets.mkdir()
            shutil.copy(PRESET_SPIRV / "p10.frag.spv", presets / "p10.frag.spv")
            manifest = json.loads((PRESET_SPIRV / "manifest.json").read_text(encoding="utf-8"))
            manifest["source"] = "0000000000000000"
            (presets / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            backup = (lpv.ROOT, lpv._PRESET_SPIRV, lpv.CONFIG_DIR, lpv._tool_path)
            stderr = io.StringIO()
            try:
                lpv.ROOT = tmp_path
                lpv._PRESET_SPIRV = presets
                lpv.CONFIG_DIR = tmp_path / "cfg"
                # No compiler: force the fallback branch that warns.
                lpv._tool_path = lambda name: None
                with contextlib.redirect_stderr(stderr):
                    resolved = lpv.vulkan_preset_spirv(10)
            finally:
                (lpv.ROOT, lpv._PRESET_SPIRV, lpv.CONFIG_DIR, lpv._tool_path) = backup

        self.assertIsNotNone(resolved, "the shipped module is still a usable fallback")
        self.assertIn("Warning", stderr.getvalue())
        self.assertIn("p10.frag.spv", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
