#!/usr/bin/env python3
"""Hidden Mode: H hides every MP window and H again brings them back.

The restore half has to live in the compositor. On Plasma Wayland the four
GTK hosts are native Wayland clients, so `wmctrl`/`xprop` never see them, and
KWin moves focus away from a window as soon as it is minimized or made
transparent. A focused app therefore cannot see the H that should undo the
hide, which is why the un-hide shortcut is registered by the KWin script and
not by the application.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mp_hidden_mode as hidden  # noqa: E402


class TestWindowTitles(unittest.TestCase):
    def test_every_mp_shortcut_window_is_covered(self):
        # The four shortcuts, plus the tracklist viewer the player spawns.
        for title in (
            "MP Liquid Player",
            "MP Mix Manager",
            "MP Gui",
            "MP Liquid Visualizer",
        ):
            self.assertTrue(
                hidden.HIDDEN_WINDOW_TITLES,
                msg="Hidden Mode must know the MP window titles",
            )
        self.assertIn("MP Audio Player", hidden.HIDDEN_WINDOW_TITLES)
        self.assertIn("MP Mix Archive Manager", hidden.HIDDEN_WINDOW_TITLES)
        self.assertIn("Mix Tracklist Viewer", hidden.HIDDEN_WINDOW_TITLES)

    def test_host_titles_match_by_prefix(self):
        # Each host appends its backend to the title.
        script = hidden._hide_script()
        self.assertIn("indexOf(MP_HIDDEN_TITLES[i]) === 0", script)

    def test_other_peoples_windows_are_not_hidden(self):
        # The match is anchored at position 0, so a Konsole window that merely
        # mentions MP in its title is left alone.
        script = hidden._hide_script()
        self.assertNotIn("MP Audio Player') !== -1", script)


class TestHideScript(unittest.TestCase):
    def test_hiding_also_arms_the_unhide_key(self):
        script = hidden._hide_script()
        self.assertIn("registerShortcut(", script)
        self.assertIn('"H"', script)
        self.assertIn("minimized = true", script)

    def test_the_unhide_key_unloads_the_script(self):
        # H must return to being a normal character once the windows are back.
        script = hidden._hide_script()
        self.assertIn("unloadScript", script)
        self.assertIn("mp_hidden_mode", script)

    def test_unhide_leaves_the_windows_visible(self):
        script = hidden._show_script()
        self.assertIn("minimized = false", script)
        self.assertNotIn("registerShortcut(", script)


class TestState(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            hidden.write_state({"hidden": True}, base)
            with patch.object(hidden, "kwin_available", return_value=False):
                self.assertTrue(hidden.is_hidden(base))
            hidden.write_state({"hidden": False}, base)
            with patch.object(hidden, "kwin_available", return_value=False):
                self.assertFalse(hidden.is_hidden(base))

    def test_missing_state_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(hidden.is_hidden(Path(tmp)))

    def test_is_hidden_follows_the_armed_compositor_script(self):
        """H is handled inside KWin, so the app can never clear its own flag."""
        with tempfile.TemporaryDirectory() as tmp:
            hidden.write_state({"hidden": True}, Path(tmp))
            with patch.object(hidden, "kwin_available", return_value=True), \
                    patch.object(hidden, "_script_loaded", return_value=False):
                self.assertFalse(
                    hidden.is_hidden(Path(tmp)),
                    msg="the armed KWin script decides, not a stale flag",
                )
            with patch.object(hidden, "kwin_available", return_value=True), \
                    patch.object(hidden, "_script_loaded", return_value=True):
                self.assertTrue(hidden.is_hidden(Path(tmp)))

    def test_state_falls_back_when_kwin_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            hidden.write_state({"hidden": True}, Path(tmp))
            with patch.object(hidden, "kwin_available", return_value=False):
                self.assertTrue(hidden.is_hidden(Path(tmp)))


class TestNotice(unittest.TestCase):
    def test_hidden_notice_names_the_key(self):
        self.assertIn("Hidden Mode", hidden.NOTIFICATION_BODY)
        self.assertIn("Press H again", hidden.NOTIFICATION_BODY)

    def test_notice_is_queued_for_the_tray(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(hidden, "config_root", return_value=Path(tmp)), \
                    patch.object(hidden, "_notify_send", return_value=True):
                hidden.announce()
            notice = hidden.read_state(Path(tmp)).get("notice") or {}
            self.assertTrue(notice.get("pending"))
            self.assertIn("Hidden Mode", notice.get("title", ""))

    def test_toggle_writes_the_notice_when_hiding(self):
        with tempfile.TemporaryDirectory() as tmp:
            doomed = Path(tmp)
            with patch.object(hidden, "config_root", return_value=doomed), \
                    patch.object(hidden, "hide_windows",
                                 return_value={"hidden": True, "applied": True}), \
                    patch.object(hidden, "_notify_send", return_value=True):
                hidden.toggle(doomed)
            notice = hidden.read_state(doomed).get("notice") or {}
            self.assertTrue(notice.get("pending"))


class TestToggle(unittest.TestCase):
    def test_toggle_hides_when_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(hidden, "is_hidden", return_value=False), \
                    patch.object(hidden, "hide_windows",
                                 return_value={"hidden": True, "applied": True}) as hide, \
                    patch.object(hidden, "show_windows") as show, \
                    patch.object(hidden, "_notify_send", return_value=True):
                hidden.toggle(Path(tmp))
            hide.assert_called_once()
            show.assert_not_called()

    def test_toggle_shows_when_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(hidden, "is_hidden", return_value=True), \
                    patch.object(hidden, "hide_windows") as hide, \
                    patch.object(hidden, "show_windows",
                                 return_value={"hidden": False, "applied": True}) as show, \
                    patch.object(hidden, "_notify_send", return_value=True):
                hidden.toggle(Path(tmp))
            show.assert_called_once()
            hide.assert_not_called()

    def test_toggle_reports_a_desktop_that_cannot_hide(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(hidden, "is_hidden", return_value=False), \
                    patch.object(hidden, "hide_windows",
                                 return_value={"hidden": True, "applied": False}), \
                    patch.object(hidden, "_notify_send", return_value=False):
                result = hidden.toggle(Path(tmp))
            self.assertTrue(result.get("hidden"))
            self.assertFalse(result.get("applied"))


class TestHostWiring(unittest.TestCase):
    def test_every_shortcut_has_a_key_handler(self):
        hosts = {
            # The terminal player binds Hidden Mode to Ctrl+H: plain H is
            # Favorites there, which is the documented binding since 0.4.2.
            "MP_Audio_Player.py": "_toggle_hidden_mode",
            "MP_Audio_Player_Liquid_Glass.py": "Gdk.KEY_h",
            "MP_Audio_Player_Liquid_Glass_OpenGL.py": "Gdk.KEY_h",
            "scripts/mp_liquid_visualizer_vulkan.py": "GLFW_KEY_H",
            "scripts/liquid_player_visualizer.py": "key == 72",
        }
        for rel, marker in hosts.items():
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(marker, source, msg=f"{rel} does not handle H")

    def test_favorites_and_file_manager_keep_their_keys(self):
        # H stays on Favorites (the README binding); Shift+F is an alias and
        # lowercase f still opens the file manager.
        source = (ROOT / "MP_Audio_Player.py").read_text(encoding="utf-8")
        self.assertIn('elif data in (b"h", b"H"): self._toggle_favorite()', source)
        self.assertIn('elif data == b"F": self._toggle_favorite()', source)
        self.assertIn('elif data == b"f": self._open_selected_in_file_manager()', source)

    def test_manager_sh_has_the_menu_key(self):
        source = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        self.assertIn("CHAR:h|CHAR:H)", source)
        self.assertIn("toggle_hidden_mode()", source)


if __name__ == "__main__":
    unittest.main()
