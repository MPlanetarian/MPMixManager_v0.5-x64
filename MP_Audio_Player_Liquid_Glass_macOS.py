#!/usr/bin/env python3
"""Apple Silicon Metal liquid-glass window hosting the MP Audio Player for macOS.

Runs natively on Apple Silicon (M1/M2/M3/M4 or newer) using Apple's Metal graphics API.
Legacy Intel-based Macs are explicitly not supported.
"""

from __future__ import annotations

import os
import platform
import pty
import select
import shutil
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from liquid_visualizer_session import (  # noqa: E402
    stop_companion_for_mode,
    stop_companion_playback,
    stop_companion_visualizer,
)
try:  # The four-window cap ships beside the host; a lone copy still runs.
    from mp_instance_limit import host_allowed, notify_limit  # noqa: E402
except ImportError:
    def host_allowed(role):  # type: ignore[misc]
        return True

    def notify_limit(role, message=None):  # type: ignore[misc]
        print(message or f"{role} is already open 4 times.", file=sys.stderr)
try:  # The exit movie ships beside the host too.
    from exit_video import ExitVideo, play_player_intro  # noqa: E402
except ImportError:
    class ExitVideo:  # type: ignore[no-redef]
        """Stub used when the tree was copied without scripts/exit_video.py."""

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return False

        def wait(self, *_args, **_kwargs):
            return None

        def finish(self):
            pass

    def play_player_intro(*_args, **_kwargs):  # type: ignore[misc]
        """Stub: no intro movie when scripts/exit_video.py is missing."""
        return False
from liquid_glass_metal import (  # noqa: E402
    LiquidGlassRendererMetal,
    check_apple_silicon_support,
    is_apple_silicon,
    is_intel_mac,
    is_macos,
    read_player_theme,
)

COLS = 120
ROWS = 40
# The portrait deck M reaches first from the full window.
VERTICAL_COLS, VERTICAL_ROWS = 40, 52


def _mode_grid(mode):
    """Terminal grid the host gives one mode."""
    mode = str(mode or "").strip().upper()
    if mode == "TRACKLIST":
        return 100, 35
    if mode == "VERTICAL":
        return VERTICAL_COLS, VERTICAL_ROWS
    if mode == "MINI":
        return 74, 14
    if mode == "MICRO":
        return 74, 7
    return COLS, ROWS


def ui_size_step(current: str) -> str:
    """The next size on the M ring: Normal, Vertical, Mini, Micro."""
    order = ("NORMAL", "VERTICAL", "MINI", "MICRO")
    cur = str(current or "NORMAL").strip().upper()
    if cur not in order:
        cur = "NORMAL"
    return order[(order.index(cur) + 1) % len(order)]


def player_panel_rects(cols=130, rows=42, char_w=8, char_h=16, origin_x=0, origin_y=0, mode="NORMAL"):
    """Pixel boxes for the player panels in ``_draw_frame`` across Normal, Mini, and Micro modes."""
    def box(col, row, box_cols, box_rows):
        return (
            origin_x + col * char_w,
            origin_y + row * char_h,
            box_cols * char_w,
            box_rows * char_h,
        )

    m = str(mode).upper()
    if m in ("MICRO", "MANAGER"):
        return (
            box(2, 0, max(20, cols - 4), rows),
            (0.0, 0.0, 0.0, 0.0),
        )
    elif m == "VERTICAL":
        # The deck stacks the main window, the equaliser and the playlist.
        main_rows, eq_rows = 23, 11
        list_rows = max(5, rows - main_rows - eq_rows)
        return (
            box(2, 0, max(20, cols - 4), main_rows),
            box(2, main_rows, max(20, cols - 4), eq_rows + list_rows),
        )
    elif m == "MINI":
        return (
            box(2, 0, max(20, cols - 4), 3),
            box(2, 3, max(20, cols - 4), max(1, rows - 3)),
        )
    elif m == "TRACKLIST":
        iw = max(20, cols - 4)
        if rows <= 7:
            return (box(2, 0, iw, rows), (0.0, 0.0, 0.0, 0.0))
        return (
            box(2, 0, iw, 7),
            box(2, 7, iw, max(1, rows - 7)),
        )
    else:
        iw = max(20, cols - 6)
        viz_rows = 7 if rows >= 34 else (5 if rows >= 28 else 3)
        before = 4 + 1 + 1 + 3 + (viz_rows + 3) + 2
        body_rows = max(1, rows - (before + 1) - 4)
        return (
            box(2, 0, iw + 2, 4),
            box(2, before, iw + 2, body_rows + 2),
        )


def manager_argv():
    bash = shutil.which("bash") or "bash"
    return [bash, str(ROOT / "Mix_Archive_Manager.sh")]


def player_argv(mode="NORMAL", extra_args=None):
    cmd = [sys.executable, str(ROOT / "MP_Audio_Player.py"), "--interactive"]
    m = str(mode).upper()
    if m == "MINI":
        cmd.append("--mini")
    elif m == "MICRO":
        cmd.append("--micro")
    elif m == "VERTICAL":
        cmd.append("--vertical")
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def embed_check_argv():
    code = "import time; print('LIQUID_GLASS_CHILD', flush=True); time.sleep(30)"
    return [sys.executable, "-c", code]


def tracklist_argv(tracklist_path, audio_path=None, theme=None):
    base = ROOT
    v_sh = base / "scripts" / "view_tracklist_console.sh"
    if not v_sh.is_file():
        v_sh = base / "view_tracklist_console.sh"
    bash_bin = shutil.which("bash") or "bash"
    cmd = [bash_bin, str(v_sh)]
    if theme:
        cmd.extend(["--theme", str(theme)])
    cmd.append(str(tracklist_path))
    if audio_path:
        cmd.append(str(audio_path))
    return cmd


def _child_env():
    env = {key: value for key, value in os.environ.items()}
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["MP_LIQUID_GLASS"] = "1"
    env["MP_LIQUID_GLASS_BACKEND"] = "metal"
    env["MP_LIQUID_GLASS_PLATFORM"] = "macos-arm64"
    return env


class MetalGlassHost:
    """Apple Silicon Metal liquid-glass host for MP Audio Player."""

    def __init__(self, argv, embed_check=False, mode="NORMAL", cli_theme=None):
        self.argv = list(argv)
        self.embed_check = embed_check
        self.mode = mode.upper()
        self.cli_theme = cli_theme
        self.renderer = None
        self.child_pid = 0
        self.child_done = False
        self.is_maximized = False
        self._saved_rect = None
        self._last_top_click = 0.0
        self._last_top_click_x = 0
        self._last_top_click_y = 0
        if self.mode == "TRACKLIST":
            self._target_cols, self._target_rows = 100, 35
        elif self.mode == "VERTICAL":
            self._target_cols, self._target_rows = VERTICAL_COLS, VERTICAL_ROWS
        elif self.mode in ("MINI", "MICRO"):
            self._target_cols, self._target_rows = 74, (14 if self.mode == "MINI" else 7)
        else:
            self._target_cols, self._target_rows = COLS, ROWS

    def _on_bezel(self, x, y, width, height):
        margin = self.renderer.bezel_px
        return x < margin or y < margin or x >= width - margin or y >= height - margin

    def _on_top_bezel(self, x, y, width, height):
        margin = self.renderer.bezel_px
        return y < margin + 40 and not self._on_close(x, y, width, height)

    def _on_close(self, x, y, width, height):
        margin = max(float(self.renderer.bezel_px), 8.0)
        cx = width - margin * 0.72
        cy = margin * 0.62
        radius = max(margin * 0.30, 4.0)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius

    def _sync_panels(self):
        if self.renderer is None:
            return
        char_w, char_h = 9, 18
        margin = self.renderer.bezel_px
        cols = getattr(self, "_target_cols", COLS)
        rows = getattr(self, "_target_rows", ROWS)
        self.renderer.set_panels(
            player_panel_rects(cols, rows, char_w, char_h, margin, margin, mode=self.mode)
        )

    def _follow_child_ui_size(self):
        """Grow or shrink with the size the player published.

        M belongs to the player, because during search that letter is text.
        Without this the window kept the full grid while the child drew the
        deck, so the tall view was cropped to the middle of a wide frame.
        """
        if self.mode in ("MANAGER", "TRACKLIST") or not self.child_pid:
            return
        try:
            from liquid_ui_size import read_ui_size
        except ImportError:
            return
        size = read_ui_size(Path.home() / ".config" / "mix-manager", int(self.child_pid))
        if not size or size == self.mode:
            return
        self.mode = size
        self._target_cols, self._target_rows = _mode_grid(size)
        if self.renderer:
            margin = self.renderer.bezel_px
            self.renderer.set_window_size(
                self._target_cols * 9 + 2 * margin,
                self._target_rows * 18 + 2 * margin,
            )
            self._sync_panels()

    def toggle_maximize(self):
        """Toggle fullscreen maximize across active display workarea."""
        if self.renderer is None:
            return
        char_w, char_h = 9, 18
        margin = self.renderer.bezel_px

        if not self.is_maximized:
            self._saved_rect = (self.renderer.window_pos(), self.renderer.window_size())
            wx, wy, ww, wh = self.renderer.get_workarea()
            max_cols = max(40, int((ww - 2 * margin) // char_w))
            max_rows = max(10, int((wh - 2 * margin) // char_h))

            self._target_cols = max_cols
            self._target_rows = max_rows
            self.renderer.set_window_pos(wx, wy)
            self.renderer.set_window_size(ww, wh)
            self._sync_panels()
            self.is_maximized = True
        else:
            pos, size = getattr(self, "_saved_rect", ((72, 64), (1280, 820)))
            cols, rows = _mode_grid(self.mode)
            self._target_cols = cols
            self._target_rows = rows
            win_w = cols * char_w + 2 * margin
            win_h = rows * char_h + 2 * margin

            self.renderer.set_window_pos(pos[0], pos[1])
            self.renderer.set_window_size(win_w, win_h)
            self._sync_panels()
            self.is_maximized = False

    def cycle_mode(self):
        """Cycle Normal, Vertical, Mini and Micro in-place, in that order."""
        char_w, char_h = 9, 18
        margin = self.renderer.bezel_px

        self.mode = ui_size_step(self.mode)
        self._target_cols, self._target_rows = _mode_grid(self.mode)

        ww = self._target_cols * char_w + 2 * margin
        wh = self._target_rows * char_h + 2 * margin

        if self.renderer:
            self.renderer.set_window_size(ww, wh)
            self._sync_panels()

    def run(self):
        """Execute the Metal Liquid Glass player session."""
        check_apple_silicon_support()

        if self.embed_check:
            char_w, char_h = 9, 18
            margin = LiquidGlassRendererMetal.bezel_px
            init_w = self._target_cols * char_w + 2 * margin
            init_h = self._target_rows * char_h + 2 * margin
            title = "MP Audio Player (Liquid Glass - Metal / Apple Silicon)"
            self.renderer = LiquidGlassRendererMetal(width=init_w, height=init_h, title=title)
            self.renderer.open()
            self._sync_panels()
            theme = read_player_theme()
            self.renderer.draw(theme)
            return True

        # 1. Set up PTY and fork BEFORE touching Metal/AppKit (single-threaded)
        master_fd, slave_fd = pty.openpty()
        
        pid = os.fork()
        if pid == 0:
            # Child process: attach to slave PTY and run player
            os.close(master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            env = _child_env()
            os.execvpe(self.argv[0], self.argv, env)
            sys.exit(1)

        os.close(slave_fd)
        self.child_pid = pid

        # 2. Initialize the Metal GUI renderer in the parent process
        char_w, char_h = 9, 18
        margin = LiquidGlassRendererMetal.bezel_px
        init_w = self._target_cols * char_w + 2 * margin
        init_h = self._target_rows * char_h + 2 * margin

        if self.mode == "TRACKLIST":
            title = "Mix Tracklist Viewer (Liquid Glass - Metal / Apple Silicon)"
        elif self.mode == "MANAGER":
            title = "MP Mix Manager (Liquid Glass - Metal / Apple Silicon)"
        else:
            title = "MP Audio Player (Liquid Glass - Metal / Apple Silicon)"
        
        self.renderer = LiquidGlassRendererMetal(
            width=init_w,
            height=init_h,
            title=title,
        )
        self.renderer.open()
        self._sync_panels()

        theme = read_player_theme()
        theme_at = 0.0

        # Give the Metal window server a brief moment to settle
        time.sleep(0.05)

        try:
            while not self.renderer.should_close:
                now = time.monotonic()
                if now - theme_at > 0.4:
                    theme = read_player_theme()
                    theme_at = now

                # Poll window events to keep macOS WindowServer responsive
                self.renderer.poll()
                self._follow_child_ui_size()

                self._sync_panels()
                self.renderer.draw(theme)

                # Check if child process has exited
                pid_res, _ = os.waitpid(self.child_pid, os.WNOHANG)
                if pid_res == self.child_pid:
                    break

                # Match the CLI's 20 ms redraw cadence. Read a full terminal
                # frame at once so output cannot backlog in 4 KB slices.
                r, _, _ = select.select([master_fd], [], [], 0.020)
                if master_fd in r:
                    try:
                        frame = bytearray()
                        while True:
                            data = os.read(master_fd, 65536)
                            if not data:
                                break
                            frame.extend(data)
                            if not select.select([master_fd], [], [], 0)[0]:
                                break
                        if not frame:
                            break
                        # Write the child's terminal frame directly to stdout.
                        sys.stdout.buffer.write(frame)
                        sys.stdout.buffer.flush()
                    except OSError:
                        break
        finally:
            self._close_then_exit_video(master_fd)

        return True

    def _close_then_exit_video(self, master_fd=None):
        """Tear the host down, then play the exit movie on the last MP window.

        The window is destroyed first, so closing the player disappears at
        once instead of sitting on screen for the whole movie. The companion
        visualizer is retired at the same moment: it is the background of this
        window, and leaving it up while the movie plays put a full-size
        raymarch window on the desktop with nothing in front of it. The movie
        then plays on its own, once, and closes itself when it ends, is
        skipped, or reaches its timeout. Only the last MP Gui or MP Liquid
        Player window shows it.
        """
        self.close(master_fd)
        # An embed check is a probe, not a close: it must never take a
        # companion the user is watching down with it.
        if not self.embed_check:
            stop_companion_for_mode(self.mode, player_gui=self.mode != "TRACKLIST")
        if not self.embed_check and self.mode in ("NORMAL", "MANAGER", "VERTICAL", "MINI", "MICRO"):
            video = ExitVideo(self.mode)
            try:
                if video.start():
                    video.wait()
            except Exception:
                pass
            finally:
                video.finish()

    def close(self, master_fd=None):
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        pid = self.child_pid
        self.child_pid = 0
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.05)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if self.renderer:
            self.renderer.close()
            self.renderer = None


def _companion_already_running() -> bool:
    """True when the companion this host owns is already on screen.

    Scoped to the companion rather than "any visualizer": a Liquid Player and
    MP Gui each own a separate window, and a free window opened with Z is not a
    companion at all.
    """
    try:
        from liquid_player_visualizer import companion_visualizer_running
    except ImportError:
        from scripts.liquid_player_visualizer import companion_visualizer_running
    try:
        return bool(companion_visualizer_running(
            os.environ.get("MP_GUI_SESSION") == "1"))
    except Exception:
        return False


def ensure_random_visualizer_started() -> bool:
    """Automatically start MP Liquid Visualizer with a random preset if not already running."""
    try:
        import shutil, subprocess
        if not _companion_already_running() and shutil.which("pgrep") and sys.platform != "linux":
            # macOS and Windows have no /proc, so fall back to the process list.
            res = subprocess.run(["pgrep", "-f", "MP_Liquid_Visualizer|mp_liquid_visualizer_vulkan"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return False
        elif _companion_already_running():
            return False

        root = Path(__file__).resolve().parent
        vis_candidates = [
            root / "MP_Liquid_Visualizer.py",
            root / "scripts" / "mp_liquid_visualizer_vulkan.py",
        ]
        vis_script = None
        for cand in vis_candidates:
            if cand.is_file():
                vis_script = cand
                break
        if not vis_script:
            return False

        log_dir = Path.home() / ".config" / "mix-manager"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "liquid_visualizer.log"

        with open(log_file, "a") as f:
            subprocess.Popen(
                [sys.executable, str(vis_script), "--preset", "random", "--backend", "auto"],
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=f,
                stderr=f,
                start_new_session=True,
            )
        return True
    except Exception:
        return False


def spawn_new_visualizer() -> bool:
    """Open a new MP Liquid Visualizer window, even when one is already running.

    This backs the Z hotkey, so it must not consult the running processes the
    way ensure_random_visualizer_started does.
    """
    try:
        import subprocess
        root = Path(__file__).resolve().parent
        vis_script = next((p for p in (root / "MP_Liquid_Visualizer.py", root / "scripts" / "mp_liquid_visualizer_vulkan.py") if p.is_file()), None)
        if not vis_script:
            return False
        log_dir = Path.home() / ".config" / "mix-manager"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "liquid_visualizer.log", "a") as f:
            subprocess.Popen(
                [sys.executable, str(vis_script), "--preset", "random", "--backend", "auto"],
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=f,
                stderr=f,
                start_new_session=True,
            )
        return True
    except Exception:
        return False


def main(argv):
    if "--help" not in argv and "-h" not in argv:
        if not is_macos():
            print(
                "Error: MP Audio Player Liquid Glass (macOS Metal) requires Apple Silicon macOS (M1/M2/M3/M4+).\n"
                f"Current operating system is '{sys.platform}'.",
                file=sys.stderr,
            )
            return 1

        if is_intel_mac():
            print(
                "======================================================================\n"
                "  ❌ UNSUPPORTED ARCHITECTURE: INTEL-BASED MAC DETECTED\n"
                "======================================================================\n"
                "  MP Audio Player Liquid Glass (Metal) exclusively supports Apple\n"
                "  Silicon Macs (Apple M1, M2, M3, M4 or newer) running macOS.\n\n"
                "  Legacy Intel x86_64 Macs do not have the required unified memory\n"
                "  Metal pipeline architecture and are not supported.\n"
                "======================================================================",
                file=sys.stderr,
            )
            return 1

        if not is_apple_silicon():
            print(
                f"Error: Unsupported machine architecture '{platform.machine()}'.\n"
                "Apple Silicon (M1/M2/M3/M4+) is required.",
                file=sys.stderr,
            )
            return 1

    if "--self-test" in argv:
        from liquid_glass_metal import run_self_test

        run_self_test()
        return 0

    embed = "--embed-check" in argv
    manager = "--manager" in argv
    tracklist_path = None
    audio_path = None
    theme_arg = None
    cleaned_argv = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--no-visualizer", "--manager"):
            i += 1
            continue
        elif arg == "--tracklist" and i + 1 < len(argv):
            tracklist_path = argv[i + 1]
            i += 2
            continue
        elif arg.startswith("--tracklist="):
            tracklist_path = arg.split("=", 1)[1]
            i += 1
            continue
        elif arg == "--audio" and i + 1 < len(argv):
            audio_path = argv[i + 1]
            i += 2
            continue
        elif arg.startswith("--audio="):
            audio_path = arg.split("=", 1)[1]
            i += 1
            continue
        elif arg in ("--theme", "-t") and i + 1 < len(argv):
            theme_arg = argv[i + 1]
            i += 2
            continue
        elif arg.startswith("--theme="):
            theme_arg = arg.split("=", 1)[1]
            i += 1
            continue
        cleaned_argv.append(arg)
        i += 1

    player_gui = not embed and not tracklist_path and "--self-test" not in argv
    # MP Gui and MP Liquid Player stop at four windows. The tracklist viewer
    # and the embed check are not counted.
    if player_gui:
        counted_role = "gui" if manager else "player"
        if not host_allowed(counted_role):
            # A shortcut launch discards stderr, so the refusal used to look
            # like nothing happened at all. Say it on the desktop as well.
            notify_limit(counted_role)
            return 1
    mode = "MINI" if "--mini" in argv else ("MICRO" if "--micro" in argv else ("VERTICAL" if "--vertical" in argv else "NORMAL"))
    # Mark the MP Gui session before the companion is spawned so that child
    # bonds behind MP Gui. The intro runs before the visualizer too.
    if manager and not embed and not tracklist_path:
        mode = "MANAGER"
        os.environ["MP_GUI_SESSION"] = "1"
        os.environ["MP_LIQUID_GLASS"] = "1"
    # The first MP Gui and the first MP Liquid Player each play
    # Exit_MP_Video.mp4 once, before the glass and its companion visualizer.
    # The movie window closes when the clip ends. A second window of that
    # same kind never shows it.
    if player_gui:
        play_player_intro(mode)
    if player_gui and "--no-visualizer" not in argv:
        ensure_random_visualizer_started()

    if manager and not embed and not tracklist_path:
        command = manager_argv()
        if not Path(command[1]).is_file():
            raise RuntimeError(f"MP Mix Manager was not found at {command[1]}")
    elif tracklist_path and not embed:
        mode = "TRACKLIST"
        command = tracklist_argv(tracklist_path, audio_path=audio_path, theme=theme_arg)
        if not Path(command[1]).is_file():
            raise RuntimeError(f"Tracklist viewer script was not found at {command[1]}")
    elif embed:
        command = embed_check_argv()
    else:
        extra = [a for a in cleaned_argv if a not in ("--self-test", "--embed-check", "--mini", "--micro")]
        if theme_arg and not any(a == "--theme" or a.startswith("--theme=") for a in extra):
            extra.extend(["--theme", theme_arg])
        command = player_argv(mode=mode, extra_args=extra)
        if not Path(command[1]).is_file():
            raise RuntimeError(f"MP Audio Player was not found at {command[1]}")

    host = MetalGlassHost(command, embed_check=embed, mode=mode, cli_theme=theme_arg)
    try:
        ok = host.run()
        if embed and ok:
            print("LIQUID_GLASS_METAL_EMBED_OK", flush=True)
        return 0
    finally:
        # The companion is already retired by _close_then_exit_video(); this
        # catches a raise before it gets there. An embed check must not touch
        # a companion at all, and a close that already stopped it is a no-op.
        if not embed:
            stop_companion_for_mode(mode, player_gui=player_gui)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        print(f"Liquid Glass (Metal): {exc}", file=sys.stderr)
        sys.exit(1)
