import math
import re
import unittest
from pathlib import Path

from MP_Audio_Player import EqualizerVisualizer, _THEME_ALIASES, _THEME_PALETTES
from MP_Audio_Player import TerminalPlayerUI, _rgb_from_ansi
import MP_Audio_Player
from scripts.liquid_glass_opengl import (
    LiquidGlassRendererOpenGL,
    THEME_ALIASES as GL_ALIASES,
    THEME_COLORS as GL_COLORS,
    get_dynamic_theme_state as gl_dynamic_theme,
)
from scripts.liquid_glass_vulkan import (
    LiquidGlassRenderer as LiquidGlassRendererVulkan,
    THEME_ALIASES as VK_ALIASES,
    THEME_COLORS as VK_COLORS,
    get_dynamic_theme_state as vk_dynamic_theme,
)
from scripts.mix_web_companion import (
    THEMES as WEB_THEMES,
    THEME_ALIASES as WEB_ALIASES,
    render_html_page,
)

ROOT = Path(__file__).resolve().parent.parent


class TestDynamicRainbowLiquidGlass(unittest.TestCase):
    def test_default_transparency_reduced_by_10_percent(self):
        """Verify default core transparency is reduced by 10% (clear_opacity increased from 0.60 to 0.70)."""
        self.assertEqual(LiquidGlassRendererOpenGL.clear_opacity, 0.70)
        self.assertEqual(LiquidGlassRendererVulkan.clear_opacity, 0.70)

        # Shader sources check for 0.50 alpha (reduced by 10% from 0.40)
        gl_frag = (ROOT / "shaders" / "liquid_glass_gl.frag").read_text(encoding="utf-8")
        vk_frag = (ROOT / "shaders" / "liquid_glass.frag").read_text(encoding="utf-8")
        self.assertIn("float alpha = 0.50 *", gl_frag)
        self.assertIn("float alpha = 0.50 *", vk_frag)
        self.assertIn("alpha *= u.themeC.w;", gl_frag)
        self.assertIn("alpha *= u.themeC.w;", vk_frag)

    def test_dynamic_rainbow_theme_registration(self):
        """Verify theme registration across MP_Audio_Player, OpenGL, and Vulkan."""
        self.assertIn("dynamic_rainbow_liquid_glass", EqualizerVisualizer.THEMES)
        self.assertIn("dynamic_rainbow_liquid_glass", _THEME_PALETTES)
        self.assertEqual(_THEME_ALIASES.get("dynamic_liquid_glass_rainbow"), "dynamic_rainbow_liquid_glass")
        self.assertEqual(_THEME_ALIASES.get("rainbow"), "dynamic_rainbow_liquid_glass")

        self.assertIn("dynamic_rainbow_liquid_glass", GL_COLORS)
        self.assertIn("dynamic_rainbow_liquid_glass", VK_COLORS)
        self.assertEqual(GL_ALIASES.get("dynamic_liquid_glass_rainbow"), "dynamic_rainbow_liquid_glass")
        self.assertEqual(VK_ALIASES.get("dynamic_liquid_glass_rainbow"), "dynamic_rainbow_liquid_glass")

    def test_dynamic_transparency_oscillation_6_second_cycle(self):
        """Verify transparency changes between -9% and +9% over a 6-second period."""
        for dyn_fn in (gl_dynamic_theme, vk_dynamic_theme):
            # t = 0s: factor should be 1.0 (baseline)
            _, op0, f0 = dyn_fn("dynamic_rainbow_liquid_glass", base_opacity=1.0, current_time=0.0)
            self.assertAlmostEqual(f0, 1.0, places=4)
            self.assertAlmostEqual(op0, 1.0, places=4)

            # t = 1.5s (quarter cycle, peak): +9% increase -> factor 1.09
            _, op1_5, f1_5 = dyn_fn("dynamic_rainbow_liquid_glass", base_opacity=1.0, current_time=1.5)
            self.assertAlmostEqual(f1_5, 1.09, places=4)
            self.assertAlmostEqual(op1_5, 1.09, places=4)

            # t = 3.0s (half cycle): back to 1.0
            _, op3, f3 = dyn_fn("dynamic_rainbow_liquid_glass", base_opacity=1.0, current_time=3.0)
            self.assertAlmostEqual(f3, 1.0, places=4)
            self.assertAlmostEqual(op3, 1.0, places=4)

            # t = 4.5s (three-quarter cycle, trough): -9% reduction -> factor 0.91
            _, op4_5, f4_5 = dyn_fn("dynamic_rainbow_liquid_glass", base_opacity=1.0, current_time=4.5)
            self.assertAlmostEqual(f4_5, 0.91, places=4)
            self.assertAlmostEqual(op4_5, 0.91, places=4)

            # t = 6.0s (full cycle complete): back to 1.0
            _, op6, f6 = dyn_fn("dynamic_rainbow_liquid_glass", base_opacity=1.0, current_time=6.0)
            self.assertAlmostEqual(f6, 1.0, places=4)
            self.assertAlmostEqual(op6, 1.0, places=4)

    def test_dynamic_rainbow_colors_shift_over_time(self):
        """Verify liquid glass colors dynamically cycle through different rainbow colors over time."""
        c0, _, _ = gl_dynamic_theme("dynamic_rainbow_liquid_glass", current_time=0.0)
        c4, _, _ = gl_dynamic_theme("dynamic_rainbow_liquid_glass", current_time=4.5)
        c9, _, _ = gl_dynamic_theme("dynamic_rainbow_liquid_glass", current_time=9.0)

        # Colors should not be static; they must evolve over time
        self.assertNotEqual(c0, c4)
        self.assertNotEqual(c4, c9)
        self.assertNotEqual(c0, c9)

    def test_web_companion_dynamic_rainbow_theme(self):
        """Verify Web Companion supports dynamic liquid glass rainbow theme with animations."""
        self.assertIn("dynamic_rainbow_liquid_glass", WEB_THEMES)
        self.assertEqual(WEB_ALIASES.get("dynamic_liquid_glass_rainbow"), "dynamic_rainbow_liquid_glass")

        theme = WEB_THEMES["dynamic_rainbow_liquid_glass"]
        self.assertTrue(theme.get("is_dynamic_rainbow"))
        self.assertIn("Dynamic Liquid Glass [Rainbow Colours] (New)", theme["name"])

        html_out = render_html_page(theme)
        self.assertIn("dynamic-rainbow-glass-active", html_out)
        self.assertIn("dynamicRainbowHue", html_out)
        self.assertIn("dynamicRainbowBreath", html_out)
        self.assertIn("6s ease-in-out infinite", html_out)
        self.assertIn('class="dynamic-rainbow-glass-active"', html_out)

    def test_the_wash_is_not_a_giant_rotating_layer(self):
        """A transform-animated, oversized wash is what killed iOS WebContent.

        The wash was `inset: -50%` so a 360-degree rotation never exposed a
        corner. On an iPhone 16 Pro Max (430x932 CSS at DPR 3) that is a
        14.4 MP compositor surface (~58 MB of RGBA) being re-composited every
        frame, and the theme's `backdrop-filter` above it then re-blurred that
        moving backdrop. iOS killed the WebContent process and Safari silently
        reloaded the page, which is the reload reported from the phone.

        The hue is stepped in JS instead, so the wash needs neither the
        oversized box nor a transform animation, and the cards drop the blur.
        """
        def strip_comments(css: str) -> str:
            return re.sub(r"/\*.*?\*/", "", css, flags=re.S)

        html_out = render_html_page(WEB_THEMES["dynamic_rainbow_liquid_glass"])
        # Only the active rule matters; the base rule is display:none.
        active = strip_comments(
            html_out.split("body.dynamic-rainbow-glass-active #rainbowWash {", 1)[1].split("}", 1)[0]
        )
        self.assertIn("inset: 0", active, "the wash must not exceed the viewport")
        self.assertNotIn("inset: -50%", active, "an oversized animated layer reloads iOS Safari")
        self.assertNotIn("will-change", active,
                         "a promoted always-animating layer burns the compositor")
        self.assertNotIn("dynamicRainbowHue", active,
                         "rotating the wash forces a full re-composite every frame")
        # No backdrop-filter may sit over the wash.
        cards = strip_comments(
            html_out.split("body.dynamic-rainbow-glass-active .card,", 1)[1].split("}", 1)[0]
        )
        self.assertNotIn("blur(", cards,
                         "backdrop-filter over the animating wash re-blurs every frame")

    def test_the_hue_is_not_stepped_from_javascript(self):
        """The 4 Hz --rainbow-hue timer was the second cause of the iOS reload.

        Every tick rewrote a registered custom property that 27 other
        properties derive from, so each one was a whole-document style
        recalculation. Measured in WebKitGTK at 430x932 the timer grew the
        WebContent process by 0.81 MB/s (about 49 MB a minute) and used 4.8% of
        a core while nothing else moved. iOS ends a WebContent process that
        grows like that while it is also decoding FLAC, and Safari reloads the
        page - the "snaps back to the top and restarts the mix" report.
        The same colour turn is now a compositor-only CSS animation.
        """
        html_out = render_html_page(WEB_THEMES["dynamic_rainbow_liquid_glass"])
        sync = html_out.split("function syncRainbowDrive()", 1)[1]
        self.assertNotIn("setInterval(tickRainbow", sync,
                         "a timer that rewrites --rainbow-hue restyles the whole page")
        self.assertNotIn("setInterval(tickRainbow", html_out,
                         "no hue timer may be started anywhere")

    def test_the_wash_turns_its_hue_on_the_compositor(self):
        """The colour motion must come from CSS, not from JS restyling."""
        def strip_comments(css: str) -> str:
            return re.sub(r"/\*.*?\*/", "", css, flags=re.S)

        html_out = render_html_page(WEB_THEMES["dynamic_rainbow_liquid_glass"])
        active = strip_comments(
            html_out.split("body.dynamic-rainbow-glass-active #rainbowWash {", 1)[1].split("}", 1)[0]
        )
        self.assertIn("dynamicRainbowSpin", active,
                      "the wash must carry the hue rotation")
        self.assertIn("hue-rotate", strip_comments(html_out),
                      "the rotation must be a filter animation")

    def test_a_playing_mix_is_never_covered_by_the_gate(self):
        """The gate must not lock a page whose audio is already running.

        On an iPhone `maybeAutoStartStream()` bailed out of the gesture branch
        without marking the automatic attempt as spent, so every 2 s poll
        re-showed the engage gate. `overlay-active` sets the body to
        position: fixed, so the dashboard was pinned to the top of the screen,
        unusable, while the mix played underneath. The renderer now refuses to
        cover a playing element, and the branch spends its one attempt.
        """
        html_out = render_html_page(WEB_THEMES["dynamic_rainbow_liquid_glass"])
        render = html_out.split("function renderStreamOverlay()", 1)[1].split("\n        }}", 1)[0]
        self.assertIn("audioElIsPlaying()", render,
                      "the gate must check the element before covering the page")
        auto = html_out.split("function maybeAutoStartStream()", 1)[1].split("\n        }}", 1)[0]
        self.assertIn("streamAutoStarted = true", auto,
                      "the iPhone gesture branch must spend its one attempt")
        self.assertIn("hideStreamOverlay()", auto,
                      "a playing mix must dismiss the gate")

    def test_web_companion_rainbow_css_available_without_reload(self):
        """The rainbow animation rules must ship with every render.

        Selecting the theme at runtime only toggles a body class, so the
        keyframes and class-scoped rules have to already be in the document.
        Emitting them only for the initially-active theme forced a reload
        before the colours would start cycling.
        """
        for theme_id, theme in WEB_THEMES.items():
            html_out = render_html_page(theme)
            self.assertIn("@keyframes dynamicRainbowHue", html_out, theme_id)
            self.assertIn("@keyframes dynamicRainbowBreath", html_out, theme_id)
            self.assertIn('id="rainbowWash"', html_out, theme_id)
            self.assertIn("isolation: isolate", html_out, theme_id)
            self.assertIn("function syncRainbowDrive", html_out, theme_id)
            self.assertNotIn("body.dynamic-rainbow-glass-active::before", html_out, theme_id)

    def test_rainbow_colour_is_painted_not_stuck_on_a_hidden_layer(self):
        """iOS Safari never showed the old hue spin.

        The spin was `filter` on a ::before with z-index -1, which paints
        under the body's opaque background, and WebKit does not animate that
        filter. pagehide also froze the animation while the page was still
        visible, and pageshow only unfroze a restored page.
        """
        html_out = render_html_page(WEB_THEMES["dynamic_rainbow_liquid_glass"])
        self.assertIn("hsl(var(--rainbow-hue)", html_out)
        self.assertIn("function tickRainbow", html_out)
        self.assertIn("if (document.hidden) setPollingPaused(true)", html_out)
        self.assertIn("if (!document.hidden) setPollingPaused(false)", html_out)
        self.assertNotIn("e.persisted", html_out)
        plain = render_html_page(next(th for tid, th in WEB_THEMES.items() if tid != "dynamic_rainbow_liquid_glass"))
        self.assertNotIn('class="dynamic-rainbow-glass-active"', plain)

    def test_manager_themes_menu_includes_option_16(self):
        """Verify Mix_Archive_Manager.sh includes option 16 for Dynamic Liquid Glass."""
        mgr_sh = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        self.assertIn("16|Dynamic Liquid Glass [Rainbow Colours] [Default] (New)", mgr_sh)
        self.assertIn("dynamic_rainbow_liquid_glass", mgr_sh)


class TestCliPlayerAnimatesTheRainbowTheme(unittest.TestCase):
    """The CLI player has to animate the theme, not just the glass and the web.

    Only the Web Companion and the shader hosts used to drive the hue over
    time. `MP_Audio_Player.py` read a frozen `_THEME_PALETTES` entry, so
    choosing Dynamic Liquid Glass in the console looked exactly like
    Dreamworlds Ultra with its violet highlight and rose active row.
    """

    def test_palette_turns_over_the_cycle(self):
        viz = EqualizerVisualizer(theme="dynamic_rainbow_liquid_glass")
        palettes = [viz.get_theme_palette(at) for at in (0.0, 4.0, 8.0, 12.0, 16.0, 20.0)]
        borders = [p["border"] for p in palettes]
        self.assertEqual(len(set(borders)), len(borders), "border hue never moved")

    def test_resting_frame_is_the_shipped_palette(self):
        """t=0 must be the static table, or the manager menu disagrees."""
        viz = EqualizerVisualizer(theme="dynamic_rainbow_liquid_glass")
        self.assertEqual(
            viz.get_theme_palette(0.0),
            _THEME_PALETTES["dynamic_rainbow_liquid_glass"],
        )

    def test_cycle_is_the_web_companions_24_seconds(self):
        viz = EqualizerVisualizer(theme="dynamic_rainbow_liquid_glass")
        self.assertEqual(viz.get_theme_palette(0.0), viz.get_theme_palette(24.0))
        self.assertNotEqual(viz.get_theme_palette(2.0), viz.get_theme_palette(12.0))

    def test_every_other_theme_stays_still(self):
        for theme in EqualizerVisualizer.THEMES:
            if theme == "dynamic_rainbow_liquid_glass":
                continue
            viz = EqualizerVisualizer(theme=theme)
            self.assertEqual(
                viz.get_theme_palette(3.0),
                viz.get_theme_palette(19.0),
                f"{theme} was dragged into the animation",
            )

    def test_frame_clock_is_shared_by_the_whole_frame(self):
        """Border, selection bar and pitch fader must agree on one hue."""
        viz = EqualizerVisualizer(theme="dynamic_rainbow_liquid_glass")
        viz.begin_frame(7.5)
        border = _rgb_from_ansi(viz.get_theme_palette()["border"])
        knob = _rgb_from_ansi(viz.pitch_control_colors()["knob"])
        self.assertNotEqual(border, _rgb_from_ansi(_THEME_PALETTES["dynamic_rainbow_liquid_glass"]["border"]))
        self.assertIsNotNone(knob)
        viz.begin_frame(7.5)
        self.assertEqual(_rgb_from_ansi(viz.get_theme_palette()["border"]), border)

    def test_selection_bar_animates_in_the_console(self):
        fills = {_rgb_from_ansi(MP_Audio_Player._selection_bar_ansi("dynamic_rainbow_liquid_glass", t)[0])
                 for t in (0.0, 4.0, 8.0)}
        self.assertEqual(len(fills), 3, "the selected row kept one colour")

    def test_selection_bar_at_rest_matches_the_manager(self):
        """The manager's SEL_BAR is a still frame of the same cycle."""
        bg, _ = MP_Audio_Player._selection_bar_ansi("dynamic_rainbow_liquid_glass")
        self.assertEqual(_rgb_from_ansi(bg), (176, 88, 255))

    def test_playlist_rows_repaint_instead_of_hitting_the_cache(self):
        """A cached selected/playing row would freeze the animated ink."""
        text = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertIn("bar_tick = round(self.visualizer.frame_time(), 1)", text)
        self.assertIn("bar_tick if (in_sel or act) else None", text)

    def test_rendered_frames_differ_over_time(self):
        """End-to-end: two draws a few seconds apart are not identical."""
        from unittest.mock import MagicMock, patch

        scanner = MagicMock()
        scanner.get_mixes.return_value = [
            {"name": f"Mix {i:02d} - Astral Projection", "path": f"/p/{i}.flac",
             "ext": "FLAC", "size_mb": 80 + i}
            for i in range(6)
        ]
        st = {
            "title": "Mix 00 - Astral Projection", "path": "/p/0.flac", "state": "playing",
            "index": 0, "position_fmt": "02:00", "duration_fmt": "60:00",
            "progress_pct": 3.3, "volume": 85, "pitch_percent": 0,
            "levels": [0.4] * 24, "peaks": [0.6] * 24,
            "levels_left": [0.4] * 24, "levels_right": [0.4] * 24,
            "peaks_left": [0.6] * 24, "peaks_right": [0.6] * 24,
            "queue_len": 0, "queue": [],
        }
        ui = TerminalPlayerUI(engine=None, scanner=scanner,
                              ui_size_mode="NORMAL", is_remote=True)
        ui.visualizer.theme = "dynamic_rainbow_liquid_glass"
        ui.selected_row = 0

        def draw_at(moment):
            captured = []
            with patch.dict("os.environ", {"MP_LIQUID_GLASS": "1"}), \
                 patch("MP_Audio_Player._present_frame",
                       side_effect=lambda lines, w, h: captured.extend(lines)), \
                 patch("MP_Audio_Player._win_visible_size", return_value=(120, 40)), \
                 patch("MP_Audio_Player.time.monotonic", return_value=moment):
                ui._get_status = MagicMock(return_value=st)
                ui._draw_frame()
            return captured

        first, later = draw_at(0.0), draw_at(6.0)
        # Compare the panel border ink, not the whole frame: the equaliser has
        # its own clocks, so "the frames differ" would pass for the wrong
        # reason. The header rule is painted from the theme palette.
        def border_ink(lines):
            for line in lines:
                if "╭" in MP_Audio_Player._ANSI_RE.sub("", line):
                    return MP_Audio_Player._rgb_from_ansi(line)
            return None

        self.assertIsNotNone(border_ink(first))
        self.assertNotEqual(border_ink(first), border_ink(later), "the CLI border never changed colour")


if __name__ == "__main__":
    unittest.main()
