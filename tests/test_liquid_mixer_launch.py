#!/usr/bin/env python3
"""X opens MP Liquid Mixer, and never opens a second one.

The mixer holds ``liquid_mixer.lock`` for its whole life because both of its
decks live in fixed IPC namespaces, so a second window would retire the first
pair of engines. Every surface that binds X reads that lock through
``scripts/liquid_mixer_launch.py``: while a mixer lives, X can only raise it.

These tests cover the lock probe, the launch claim that closes the race between
two X presses, and the key wiring in MP Liquid Player and the console manager.
"""

import errno
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import liquid_mixer_launch as launcher  # noqa: E402


class LauncherCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Path(self._tmp.name)
        for name, value in (
            ("CONFIG_DIR", self.config),
            ("LOCK_PATH", self.config / "liquid_mixer.lock"),
            ("CLAIM_PATH", self.config / "liquid_mixer_launch.json"),
            ("CLAIM_GUARD_PATH", self.config / "liquid_mixer_launch.lock"),
            ("LOG_PATH", self.config / "liquid_mixer.log"),
        ):
            patcher = patch.object(launcher, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def hold_lock(self):
        """Take the mixer's own lock, the way a running window does."""
        fd = os.open(str(launcher.LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl = launcher.fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())

        def _close():
            try:
                os.close(fd)
            except OSError:
                pass

        self.addCleanup(_close)
        return fd


class TestLockProbe(LauncherCase):
    def test_no_mixer_when_the_lock_is_free(self):
        self.assertFalse(launcher.mixer_lock_held())
        self.assertEqual(launcher.status(), "free")

    def test_a_held_lock_means_a_live_mixer(self):
        self.hold_lock()
        self.assertTrue(launcher.mixer_lock_held())
        self.assertEqual(launcher.status(), "running")

    def test_the_probe_does_not_clobber_the_owners_pid(self):
        self.hold_lock()
        before = launcher.LOCK_PATH.read_bytes()
        launcher.mixer_lock_held()
        self.assertEqual(launcher.LOCK_PATH.read_bytes(), before)
        self.assertEqual(launcher.LOCK_PATH.read_text(encoding="utf-8").strip(), str(os.getpid()))


class TestLaunchClaim(LauncherCase):
    def test_a_claim_from_a_live_launch_blocks_a_second_window(self):
        launcher.write_launch_claim(os.getpid())
        self.assertTrue(launcher.launch_claim_active())
        self.assertEqual(launcher.status(), "starting")

    def test_a_dead_launch_claim_is_ignored(self):
        launcher.write_launch_claim(999_999)
        self.assertFalse(launcher.launch_claim_active())

    def test_a_stale_claim_is_ignored(self):
        launcher.write_launch_claim(os.getpid())
        payload = launcher.read_launch_claim()
        payload["started"] = 1.0
        launcher.CLAIM_PATH.write_text(__import__("json").dumps(payload), encoding="utf-8")
        self.assertFalse(launcher.launch_claim_active())

    def test_the_mixer_clears_only_its_own_claim(self):
        launcher.write_launch_claim(os.getpid() + 1)
        launcher.release_launch_claim(os.getpid())
        self.assertIsNotNone(launcher.read_launch_claim())

    def test_release_clears_the_matching_claim(self):
        launcher.write_launch_claim(os.getpid())
        launcher.release_launch_claim(os.getpid())
        self.assertIsNone(launcher.read_launch_claim())


class TestLaunch(LauncherCase):
    def test_a_running_mixer_is_raised_and_never_spawned(self):
        self.hold_lock()
        with patch.object(launcher, "mixer_supported", return_value=True), \
             patch.object(launcher, "mixer_command", return_value=["bash", "run.sh"]), \
             patch.object(launcher, "focus_mixer", return_value=True) as focus, \
             patch.object(launcher, "_popen") as popen:
            self.assertEqual(launcher.launch_mixer(), "already-open")
        focus.assert_called_once()
        popen.assert_not_called()

    def test_a_live_claim_does_not_spawn_a_second_window(self):
        launcher.write_launch_claim(os.getpid())
        with patch.object(launcher, "mixer_supported", return_value=True), \
             patch.object(launcher, "mixer_command", return_value=["bash", "run.sh"]), \
             patch.object(launcher, "focus_mixer", return_value=True) as focus, \
             patch.object(launcher, "_popen") as popen:
            self.assertEqual(launcher.launch_mixer(), "starting")
        focus.assert_called_once()
        popen.assert_not_called()

    def test_a_free_slot_spawns_exactly_one_window_and_claims_it(self):
        proc = MagicMock(pid=os.getpid())
        with patch.object(launcher, "mixer_supported", return_value=True), \
             patch.object(launcher, "mixer_command", return_value=["bash", "run.sh"]), \
             patch.object(launcher, "_popen", return_value=proc) as popen:
            self.assertEqual(launcher.launch_mixer(), "opened")
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0], ["bash", "run.sh"])
        self.assertTrue(launcher.launch_claim_active())

    def test_a_second_press_after_a_close_is_allowed_again(self):
        """Closing the window releases the lock, so X opens a mixer again."""
        held = self.hold_lock()
        with patch.object(launcher, "mixer_supported", return_value=True), \
             patch.object(launcher, "mixer_command", return_value=["bash", "run.sh"]), \
             patch.object(launcher, "focus_mixer", return_value=True), \
             patch.object(launcher, "_popen") as popen:
            self.assertEqual(launcher.launch_mixer(), "already-open")
            popen.assert_not_called()
        # A closed window drops its flock; the next press is a fresh launch.
        os.close(held)
        launcher.release_launch_claim()
        proc = MagicMock(pid=os.getpid())
        with patch.object(launcher, "mixer_supported", return_value=True), \
             patch.object(launcher, "mixer_command", return_value=["bash", "run.sh"]), \
             patch.object(launcher, "_popen", return_value=proc) as popen:
            self.assertEqual(launcher.launch_mixer(), "opened")
        popen.assert_called_once()

    def test_a_text_console_has_nowhere_to_draw_the_mixer(self):
        with patch.dict(os.environ, {"DISPLAY": ""}), \
             patch.object(launcher, "_popen") as popen:
            self.assertEqual(launcher.launch_mixer(), "unsupported")
        popen.assert_not_called()

    def test_a_missing_tree_is_reported(self):
        with patch.dict(os.environ, {"DISPLAY": ":0"}), \
             patch.object(launcher, "mixer_command", return_value=None), \
             patch.object(launcher, "_popen") as popen:
            self.assertEqual(launcher.launch_mixer(), "missing")
        popen.assert_not_called()


class TestPlayerKey(unittest.TestCase):
    """MP Liquid Player binds X to the mixer and keeps Esc/q as exits."""

    def _ui(self):
        import MP_Audio_Player as player
        scanner = MagicMock()
        scanner.get_mixes.return_value = []
        scanner.base_dir = ROOT
        ui = player.TerminalPlayerUI(engine=None, scanner=scanner, is_remote=True)
        ui.running = True
        return player, ui

    def test_x_opens_the_mixer_and_keeps_the_player_open(self):
        player, ui = self._ui()
        with patch.object(player, "launch_liquid_mixer", return_value="opened") as launch:
            ui._process_key(b"x")
        launch.assert_called_once()
        self.assertTrue(ui.running)
        self.assertIn("MP Liquid Mixer", ui.notification_msg)

    def test_shift_x_is_the_same_launcher(self):
        player, ui = self._ui()
        with patch.object(player, "launch_liquid_mixer", return_value="already-open") as launch:
            ui._process_key(b"X")
        launch.assert_called_once()
        self.assertTrue(ui.running)
        self.assertIn("already open", ui.notification_msg)

    def test_an_open_mixer_is_reported_not_duplicated(self):
        player, ui = self._ui()
        with patch.object(player, "launch_liquid_mixer", return_value="already-open"):
            ui._process_key(b"x")
        self.assertTrue(ui.running)

    def test_escape_and_q_still_exit(self):
        player, ui = self._ui()
        ui._process_key(b"\x1b")
        self.assertFalse(ui.running)
        player, ui = self._ui()
        ui._process_key(b"\x03")
        self.assertFalse(ui.running)

    def test_a_text_console_keeps_x_as_stop_and_exit(self):
        player, ui = self._ui()
        with patch.object(player, "launch_liquid_mixer", return_value="unsupported"), \
             patch.object(ui, "_send_cmd") as send:
            ui._process_key(b"x")
        send.assert_called_once_with("stop")
        self.assertFalse(ui.running)

    def test_a_mixer_deck_never_asks_for_a_second_mixer(self):
        player, ui = self._ui()
        with patch.dict(os.environ, {"MP_LIQUID_GLASS_MIXER": "1"}), \
             patch.object(player, "launch_liquid_mixer") as launch:
            ui._process_key(b"x")
        launch.assert_not_called()
        self.assertTrue(ui.running)
        self.assertIn("already open", ui.notification_msg)

    def test_search_can_still_type_x(self):
        player, ui = self._ui()
        ui.search_mode = True
        ui.search_query = ""
        with patch.object(player, "launch_liquid_mixer") as launch:
            ui._process_key(b"x")
        launch.assert_not_called()
        self.assertEqual(ui.search_query, "x")


class TestManagerKey(unittest.TestCase):
    def test_the_menu_binds_x_to_the_mixer(self):
        text = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        # X is handled in the CHAR:* branch beside b/p/f, so a typed word such
        # as "exit" still reaches the text selection instead of launching.
        self.assertIn('[ "$ch" = "x" ] || [ "$ch" = "X" ]', text)
        self.assertIn("launch_mp_liquid_mixer --quiet", text)
        self.assertIn("[xX]|mixer|liquid-mixer|mp-liquid-mixer|mp_liquid_mixer) launch_mp_liquid_mixer", text)

    def test_a_typed_x_is_not_a_launch(self):
        text = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        self.assertNotIn("CHAR:x|CHAR:X)", text)
        start = text.index('CHAR:*) local ch="${key_action#CHAR:}"')
        body = text[start:start + 9000]
        x_at = body.index('[ "$ch" = "x" ]')
        typed_at = body.index('typed_buffer+="$ch"')
        self.assertLess(x_at, typed_at, "x must be decided before the text buffer grows")
        self.assertIn('[ -z "$typed_buffer" ]', body[x_at - 40:x_at])

    def test_the_menu_lists_the_mixer_key(self):
        text = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        self.assertIn("[G] MP Gui [X] Mixer", text)

    def test_the_launcher_routes_through_the_shared_helper(self):
        text = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        start = text.index("launch_mp_liquid_mixer() {")
        body = text[start:start + 2600]
        self.assertIn("liquid_mixer_launch.py", body)
        self.assertIn('"$py_bin" "$helper" launch', body)

    def test_the_quiet_menu_path_never_waits_for_a_key(self):
        text = (ROOT / "Mix_Archive_Manager.sh").read_text(encoding="utf-8")
        start = text.index("launch_mp_liquid_mixer() {")
        body = text[start:start + 5000]
        quiet = body.index('if [ "$quiet" -eq 1 ]; then')
        self.assertIn("return 0", body[quiet:quiet + 200])

    def test_the_shell_still_parses(self):
        import subprocess
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "Mix_Archive_Manager.sh")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestMixerSide(unittest.TestCase):
    def test_the_mixer_clears_its_launch_claim_once_the_window_is_up(self):
        source = (ROOT / "MP_Liquid_Mixer" / "MP_Liquid_Mixer.py").read_text(encoding="utf-8")
        self.assertIn("release_launch_claim", source)
        claim_at = source.index("self._release_launch_claim()")
        main_at = source.index("Gtk.main()", claim_at)
        self.assertLess(claim_at, main_at, "the window must be up before the claim is dropped")

    def test_the_mixer_still_refuses_a_second_window_by_itself(self):
        source = (ROOT / "MP_Liquid_Mixer" / "MP_Liquid_Mixer.py").read_text(encoding="utf-8")
        self.assertIn("already open", source)
        core = (ROOT / "MP_Liquid_Mixer" / "mixer_core.py").read_text(encoding="utf-8")
        self.assertIn("LOCK_EX | fcntl.LOCK_NB", core)


if __name__ == "__main__":
    unittest.main()
