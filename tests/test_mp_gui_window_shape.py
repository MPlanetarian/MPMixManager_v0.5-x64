#!/usr/bin/env python3
"""M in MP Gui toggles a portrait window. Header values after a colon are white."""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mp_gui_window_shape import (  # noqa: E402
    font_point_size,
    portrait_window,
    scaled_cell,
    terminal_grid,
)
from tests.mp_test_bash import BASH  # noqa: E402

import MP_Audio_Player_Liquid_Glass as lg_vulkan  # noqa: E402
import MP_Audio_Player_Liquid_Glass_OpenGL as lg_opengl  # noqa: E402

MANAGER = ROOT / "Mix_Archive_Manager.sh"
WHITE = (255, 255, 255)
DEFAULT_INK = (24, 26, 34)
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _fg_at(text: str, index: int, default=DEFAULT_INK):
    fg = default
    for match in _SGR.finditer(text, 0, index):
        codes = [c for c in match.group(1).split(";") if c]
        if match.group(1) == "" or "0" in codes:
            fg = default
        for i, code in enumerate(codes):
            if code == "38" and codes[i + 1:i + 2] == ["2"]:
                fg = tuple(int(v) for v in codes[i + 2:i + 5])
    return fg


def _whiten(raw: str) -> str:
    text = MANAGER.read_text(encoding="utf-8")
    start = text.index("_whiten_header_data_fields() {")
    end = text.index("\nrender_main_header() {", start)
    script = text[start:end] + """
IFS= read -r -d '' src || true
_whiten_header_data_fields src
printf '%s' "$src"
"""
    proc = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", script],
        input=raw.encode() + b"\0",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.decode())
    return proc.stdout.decode()


class WindowShapeMathTest(unittest.TestCase):
    def test_vertical_font_is_twenty_percent_smaller(self):
        self.assertEqual(font_point_size("Monospace 11", 0.8), 8.8)
        self.assertEqual(font_point_size("Monospace 11", 1), 11.0)
        self.assertEqual(scaled_cell((9, 18), 0.8), (7, 14))

    def test_portrait_window_is_taller_than_it_is_wide(self):
        width, height = portrait_window(1096, 780)
        self.assertLess(width, height)
        self.assertEqual((width, height), (780, 1096))
        square_w, square_h = portrait_window(400, 400)
        self.assertLess(square_w, square_h)


class HeaderValueColorTest(unittest.TestCase):
    def test_values_after_colons_are_white_and_labels_stay(self):
        raw = (
            "  \033[1m\033[38;2;0;193;228m🎧 Audio Interface:\033[0m GA102 HDMI\033[0m\n"
            "  \033[1m\033[38;2;0;193;228m⚡ Latency:\033[0m "
            "\033[38;2;0;229;255m21.3ms\033[0m  "
            "\033[1m\033[38;2;102;102;255m│\033[0m  "
            "\033[1m\033[38;2;0;193;228m🎛️ Engine:\033[0m PipeWire\n"
            "  \033[1m\033[38;2;0;193;228m🌐 Network:\033[0m \033[32mOnline\033[0m  "
            "\033[1m\033[34m|\033[0m  "
            "\033[1m\033[38;2;0;193;228m🐚 Shell:\033[0m Bash 5.2.15(1)-release\n"
            "  \033[1m\033[38;2;0;193;228m☁️  Cloud Backup:\033[0m "
            "\033[1m\033[33mUp-to-date\033[0m \033[2m(Last: 2026-11-15 11:00)\033[0m\n"
            "  \033[1m\033[35mMP Mix Archive Manager (v0.5)\033[0m\n"
        )
        out = _whiten(raw)
        self.assertEqual(_ANSI.sub("", out), _ANSI.sub("", raw))
        for label in ("Audio Interface:", "Latency:", "Engine:", "Network:", "Shell:", "Cloud Backup:"):
            self.assertEqual(out[out.find(label):out.find(label) + len(label)], label)
            self.assertNotEqual(_fg_at(out, out.find(label)), WHITE)
        for value in ("GA102", "21.3ms", "PipeWire", "Online", "Bash", "Up-to-date", "11:00"):
            self.assertEqual(_fg_at(out, out.find(value)), WHITE, value)
        self.assertEqual(_fg_at(out, out.find("│")), (102, 102, 255))
        title = "MP Mix Archive Manager (v0.5)"
        self.assertIn(title, out)
        self.assertNotEqual(_fg_at(out, out.find(title)), WHITE)
        # The title line has no data field, so its bytes stay as written.
        title_line = raw.splitlines()[-1]
        self.assertIn(title_line, out)
        literal = (
            "  \\033[1m\\033[38;2;91;134;187mAudio Interface:\\033[0m GA102"
            "  \\033[1m\\033[38;2;21;83;158m|\\033[0m  "
            "\\033[1m\\033[38;2;91;134;187mEngine:\\033[0m PipeWire\n"
        )
        expanded = _whiten(literal)
        self.assertEqual(_fg_at(expanded, expanded.find("GA102")), WHITE)
        self.assertEqual(_fg_at(expanded, expanded.find("PipeWire")), WHITE)
        self.assertNotEqual(_fg_at(expanded, expanded.find("Engine:")), WHITE)

    def test_rendered_mp_gui_header_whitens_live_fields(self):
        script = r"""
        export MP_GUI_SESSION=1
        export WEATHER_ENABLED=false
        export PLANETS_ENABLED=false
        source ./Mix_Archive_Manager.sh >/dev/null 2>&1 || true
        set_theme_colors retro_linux_memories
        _TERM_COLS=140
        _TERM_LINES=40
        MIX_ARCHIVE_CONFIGURED=true
        is_mix_archive_configured() { return 0; }
        _s4_hardware_connected() { return 1; }
        get_manager_uptime() {
          _MANAGER_UPTIME_LINE="  ${BOLD}${CYAN}⏱ MP Manager Uptime:${NC} 3m  ${BOLD}${BLUE}│${NC}  ${BOLD}${CYAN}🖥 System Uptime:${NC} 1h 2m  ${BOLD}${BLUE}│${NC}  ${BOLD}${CYAN}🔊 Audio:${NC} 40%"
        }
        _CACHED_OS_BADGE="${BOLD}${CYAN}🐧 OS:${NC} Bazzite"
        _CACHED_OS_UPDATE_STATUS="${GREEN}Up to date${NC}"
        _CACHED_INTERNET_STATUS=online
        _CACHED_PERF_DISPLAY="  ${BOLD}${CYAN}⚡ CPU Load:${NC} 0.50  ${BOLD}${BLUE}│${NC}  ${BOLD}${CYAN}🧠 RAM:${NC} 1G/2G  ${BOLD}${BLUE}│${NC}  ${BOLD}${CYAN}💾 Mix Drive Free:${NC} 10G free"
        _CACHED_AUDIO_IFACE_DISPLAY=$'  '"${BOLD}${CYAN}🎧 Audio Interface:${NC} GA102 HDMI${NC}"$'\n'"  ${BOLD}${CYAN}⚡ Latency:${NC} ${GREEN}21.3ms${NC}  ${BOLD}${BLUE}│${NC}  ${BOLD}${CYAN}🎛️ Engine:${NC} PipeWire"
        render_main_header 130 >/dev/null
        printf '%s' "$_RENDERED_MAIN_HEADER"
        """
        proc = subprocess.run(
            [BASH, "--noprofile", "--norc", "-c", script],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[-2000:])
        out = proc.stdout.decode()
        self.assertIn("Audio Interface:", out)
        self.assertEqual(_fg_at(out, out.find("GA102")), WHITE)
        self.assertNotEqual(_fg_at(out, out.find("Audio Interface:")), WHITE)
        self.assertEqual(_fg_at(out, out.find("21.3ms")), WHITE)
        self.assertEqual(_fg_at(out, out.find("PipeWire")), WHITE)
        self.assertNotEqual(_fg_at(out, out.find("Engine:")), WHITE)
        self.assertEqual(_fg_at(out, out.find("0.50")), WHITE)
        self.assertEqual(_fg_at(out, out.find("Up to date")), WHITE)
        self.assertEqual(_fg_at(out, out.find("Bazzite")), WHITE)
        self.assertNotEqual(_fg_at(out, out.find("MP Mix Archive Manager")), WHITE)


class GuiMKeyTest(unittest.TestCase):
    def test_search_keeps_m_and_gui_toggles_shape(self):
        text = MANAGER.read_text(encoding="utf-8")
        chunk = text[text.find("CHAR:m|CHAR:M)"):text.find("CHAR:m|CHAR:M)") + 360]
        self.assertIn('search_mode" -eq 1', chunk)
        self.assertLess(chunk.find("search_query"), chunk.find("_mp_key_m"))
        self.assertNotIn("launch_mp_audio_player_window", chunk)

        start = text.index("_toggle_mp_gui_window_shape() {")
        end = text.index("\n# M on the main screen", start)
        with tempfile.TemporaryDirectory() as tmp:
            script = f'''
            export HOME="{tmp}"
            export MP_GUI_SESSION=1
            {text[start:end]}
            _mp_key_m
            IFS= read -r shape < "$HOME/.config/mix-manager/gui_window_shape.$$"
            printf 'first:%s\\n' "$shape"
            _mp_key_m
            IFS= read -r shape < "$HOME/.config/mix-manager/gui_window_shape.$$"
            printf 'second:%s\\n' "$shape"
            unset MP_GUI_SESSION
            launched=0
            launch_mp_audio_player_window() {{ launched=1; }}
            _mp_key_m
            printf 'launched:%s\\n' "$launched"
            IFS= read -r shape < "$HOME/.config/mix-manager/gui_window_shape.$$"
            printf 'still:%s\\n' "$shape"
            '''
            proc = subprocess.run(
                [BASH, "--noprofile", "--norc", "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        lines = proc.stdout.decode().splitlines()
        self.assertEqual(lines, ["first:VERTICAL", "second:HORIZONTAL", "launched:1", "still:HORIZONTAL"])

    def test_startup_drops_a_stale_shape_file(self):
        text = MANAGER.read_text(encoding="utf-8")
        line = next(
            item for item in text.splitlines()
            if item.startswith("if [ \"${MP_GUI_SESSION:-0}\" = \"1\" ]; then rm -f")
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = f'''
            export HOME="{tmp}"
            mkdir -p "$HOME/.config/mix-manager"
            printf 'VERTICAL\\n' > "$HOME/.config/mix-manager/gui_window_shape.$$"
            MP_GUI_SESSION=0
            {line}
            if [ -f "$HOME/.config/mix-manager/gui_window_shape.$$" ]; then printf 'kept\\n'; else printf 'gone\\n'; fi
            export MP_GUI_SESSION=1
            {line}
            if [ -f "$HOME/.config/mix-manager/gui_window_shape.$$" ]; then printf 'stuck\\n'; else printf 'cleared\\n'; fi
            '''
            proc = subprocess.run(
                [BASH, "--noprofile", "--norc", "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(proc.stdout.decode().splitlines(), ["kept", "cleared"])


class GuiHostFollowTest(unittest.TestCase):
    def test_manager_window_swaps_to_portrait_and_back(self):
        for mod, apply_name in (
            (lg_opengl, "_apply_grid"),
            (lg_vulkan, "_apply_gui_grid"),
        ):
            pid = 880000 + (1 if mod is lg_opengl else 2)
            flag = mod.CONFIG_DIR / f"gui_window_shape.{pid}"
            flag.unlink(missing_ok=True)
            try:
                host = mod.GlassHost.__new__(mod.GlassHost)
                host.mode = "MANAGER"
                host.child_pid = pid
                host.font_name = "Monospace 11"
                host.glib = None
                host.renderer = MagicMock()
                host.renderer.bezel_px = 8
                host.renderer.window_size.return_value = (100, 100)
                host.terminal = MagicMock()
                host.terminal.get_char_width.return_value = 9
                host.terminal.get_char_height.return_value = 18
                host.set_mode = MagicMock()
                host._recenter_window = lambda width, height: None
                calls = []

                def record(cols, rows, size=None, _calls=calls):
                    _calls.append((cols, rows, size))

                setattr(host, apply_name, record)
                host._follow_child_ui_size()
                self.assertEqual(calls, [])
                host.set_mode.assert_not_called()

                flag.write_text("VERTICAL\n", encoding="utf-8")
                host._follow_child_ui_size()
                host.set_mode.assert_not_called()
                self.assertEqual(host._font_scale, 0.8)
                self.assertFalse(host.is_maximized)
                cols, rows, size = calls[-1]
                self.assertIsNotNone(size)
                self.assertLess(size[0], size[1])
                extra = host._gui_height_extra()
                normal = (
                    mod.COLS * 9 + 16,
                    mod.ROWS * 18 + 16 + extra,
                )
                expect_size = portrait_window(*normal)
                self.assertEqual(size, expect_size)
                expect_grid = terminal_grid(*expect_size, *scaled_cell((9, 18), 0.8), 8, extra)
                self.assertEqual((cols, rows), expect_grid)

                flag.write_text("HORIZONTAL\n", encoding="utf-8")
                host._follow_child_ui_size()
                self.assertEqual(host._font_scale, 1.0)
                cols, rows, size = calls[-1]
                self.assertEqual((cols, rows, size), (mod.COLS, mod.ROWS, None))
            finally:
                flag.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
