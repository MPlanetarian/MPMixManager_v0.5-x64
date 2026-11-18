#!/usr/bin/env python3
"""Play the MP exit movie after the last MP Gui / MP Liquid Player window closes.

The movie is `Exit_MP_Video.mp4` in the install tree. It is played by the same
script that plays the startup logo (`scripts/display_loader_video.sh --exit`),
so the player choice, the borderless always-on-top window and the skip keys are
identical on every platform and there is only one implementation to maintain.

The host destroys its own window first, then plays the movie. Closing the
player therefore disappears the instant the user closes it, and the movie runs
on its own with nothing behind it to cover.

Two rules decide whether it plays at all:

  * Only the last window. MP Gui and MP Liquid Player are counted together, so
    closing one of two open windows stays silent and the movie waits for the
    very last window of either kind.
  * Never twice. Each closing host marks itself before counting, so two windows
    closed at the same instant elect exactly one of them.

The four Liquid Glass hosts use `ExitVideo`: `start()` elects and launches the
movie, `wait()` blocks until it ends, then `finish()` clears the marker so a
later launch is elected normally.

The same movie is the intro for the **first MP Liquid Player window** and the
**first MP Gui window**. `PlayerIntro` plays it *before* the window exists, and
only for the first of the four allowed windows of that kind: instances two,
three and four open straight away and never show it. The election is that
role's own count with this process excluded, so a second shortcut pressed
while the intro is on screen is already counted and stays silent. Closing
every window of that kind and opening one again plays it once more.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

EXIT_VIDEO_NAME = "Exit_MP_Video.mp4"
EXIT_VIDEO_TIMEOUT_SECONDS = 90

# The first MP Liquid Player window and the first MP Gui window play the same
# movie as an intro. Later windows of that kind (two to four) open straight
# away and never show it.
INTRO_SCRIPT_ARG = "--intro"
INTRO_VIDEO_TIMEOUT_SECONDS = 90
PLAYER_MODES = ("NORMAL", "VERTICAL", "MINI", "MICRO")

# The loader script is the single player implementation. Hosts pass --exit so
# it plays Exit_MP_Video.mp4 instead of the startup logo.
EXIT_SCRIPT_NAME = "display_loader_video.sh"


def _config_candidates(root: Path) -> list[Path]:
    return [
        Path(os.environ.get("MP_CONFIG", "")) if os.environ.get("MP_CONFIG") else Path("/nonexistent"),
        Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "mix-manager" / "config.env",
        root / "config.env",
    ]


def config_value(key: str, default: str = "", root: Path | None = None) -> str:
    """Read one KEY from config.env without executing the file.

    The hosts never source config.env (it is user-editable, so sourcing it
    would run whatever it contains), so the exit-video switch is read here.
    An environment variable always wins over the file.
    """
    from_env = os.environ.get(key, "")
    if from_env:
        return from_env
    root = Path(root) if root else script_root()
    for cfg in _config_candidates(root):
        try:
            if not cfg.is_file():
                continue
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped.startswith(key) or "=" not in stripped:
                    continue
                name, value = stripped.split("=", 1)
                if name.strip() != key:
                    continue
                value = value.strip().strip('"').strip("'")
                return value or default
        except OSError:
            continue
    return default


def exit_video_enabled(root: Path | None = None) -> bool:
    return config_value("SHOW_EXIT_VIDEO", "true", root).strip().lower() not in (
        "false", "0", "no", "off",
    )


def exit_video_timeout(root: Path | None = None) -> int:
    raw = config_value("EXIT_VIDEO_MAX_SECONDS", str(EXIT_VIDEO_TIMEOUT_SECONDS), root)
    try:
        return max(5, int(float(raw)))
    except (TypeError, ValueError):
        return EXIT_VIDEO_TIMEOUT_SECONDS


def intro_video_enabled(root: Path | None = None) -> bool:
    """`SHOW_PLAYER_INTRO_VIDEO` decides whether the first window gets an intro."""
    return config_value("SHOW_PLAYER_INTRO_VIDEO", "true", root).strip().lower() not in (
        "false", "0", "no", "off",
    )


def intro_video_timeout(root: Path | None = None) -> int:
    raw = config_value("PLAYER_INTRO_VIDEO_MAX_SECONDS", str(INTRO_VIDEO_TIMEOUT_SECONDS), root)
    try:
        return max(5, int(float(raw)))
    except (TypeError, ValueError):
        return INTRO_VIDEO_TIMEOUT_SECONDS


def script_root() -> Path:
    """The install tree this module belongs to."""
    return Path(__file__).resolve().parent.parent


def display_script(root: Path | None = None) -> Path | None:
    root = Path(root) if root else script_root()
    script = root / "scripts" / EXIT_SCRIPT_NAME
    if script.is_file():
        return script
    return None


def find_exit_video(root: Path | None = None) -> Path | None:
    """Locate Exit_MP_Video.mp4 without starting a subprocess."""
    root = Path(root) if root else script_root()
    for candidate in (
        root / EXIT_VIDEO_NAME,
        root / "assets" / EXIT_VIDEO_NAME,
        Path.home() / "Videos" / EXIT_VIDEO_NAME,
    ):
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def role_for_mode(mode: str | None) -> str:
    """The capped role a Liquid Glass host draws: MP Gui or MP Liquid Player."""
    return "gui" if (mode or "").upper() == "MANAGER" else "player"


def _limiter():
    """The shared window counter, or None when the tree was copied alone."""
    try:
        import mp_instance_limit  # type: ignore
    except ImportError:
        return None
    return mp_instance_limit


def is_last_window_open(role: str = "") -> bool:
    """True when no other MP Gui or MP Liquid Player window is still open.

    Read-only: unlike `should_play_exit_video` it marks nothing, so it can be
    asked before the movie is elected. A host uses this to decide whether the
    audio it owns has anything left to play to, which must not depend on
    whether the closing movie is enabled.
    """
    limiter = _limiter()
    if limiter is None:
        # Without the counter the safest reading is "this was the last one":
        # leaving audio running with no window on screen is the worse bug.
        return True
    try:
        return not limiter.remaining_windows(os.getpid())
    except Exception:
        return True


def should_play_exit_video(role: str = "") -> bool:
    """Mark this host as closing and report whether it is the last window.

    Returns False as soon as another MP Gui or MP Liquid Player window is
    still open. The marker written here keeps this host out of the next
    window's count while the movie plays.
    """
    limiter = _limiter()
    if limiter is None:
        # Without the counter there is no way to know about sibling windows.
        # Playing once is better than never showing the movie at all.
        return True
    try:
        return bool(limiter.is_last_window(role))
    except Exception:
        return False


def finish_exit_video(role: str = "") -> None:
    """Clear this host's closing marker once the movie is over."""
    limiter = _limiter()
    if limiter is None:
        return
    try:
        limiter.end_closing(role)
    except Exception:
        pass


def intro_role(mode: str | None) -> str:
    """Which window kind this launch is, or "" when it gets no intro."""
    upper = (mode or "").upper()
    if upper == "MANAGER":
        return "gui"
    if upper in PLAYER_MODES:
        return "player"
    return ""


def _intro_blocked(done_var: str) -> bool:
    """Shared reasons an opening movie must not start."""
    if os.environ.get("MP_NO_PLAYER_INTRO") == "1" or os.environ.get("MP_NO_GUI_INTRO") == "1":
        return True
    # A launcher that already played the movie passes this through exec, so
    # the window it starts does not play the same movie twice.
    if os.environ.get(done_var) == "1":
        return True
    if os.environ.get("MP_BENCHMARK_MODE") == "1" or os.environ.get("MP_VR_MODE") == "1":
        return True
    return False


def player_intro_wanted(mode: str | None = None, root: Path | None = None) -> bool:
    """True when this launch is a plain MP Liquid Player window.

    The tracklist viewer and the embed and self-test helpers never show the
    intro. MP Gui has its own election (`gui_intro_wanted`). Benchmark, soak
    and VR runs skip it too: the first two measure window timing, and VR
    places the player on a canvas where a full-screen movie would cover the
    headset view.
    """
    del root
    if _intro_blocked("MP_PLAYER_INTRO_DONE"):
        return False
    return (mode or "").upper() in PLAYER_MODES


def gui_intro_wanted(mode: str | None = None, root: Path | None = None) -> bool:
    """True when this launch is an MP Gui window that may show the intro.

    Only the first MP Gui of the session plays it. The tracklist viewer and
    the helper flags are not MP Gui. A second, third, or fourth MP Gui never
    shows the movie.
    """
    del root
    if _intro_blocked("MP_GUI_INTRO_DONE"):
        return False
    return (mode or "").upper() == "MANAGER"


def is_first_window(role: str, pid: int | None = None) -> bool:
    """True when no other window of this role is open.

    The caller's own claim is excluded: the launcher reserved it before exec
    (or the host reserved it for itself on Windows), and it must not make the
    first window look like the second.
    """
    if role not in ("gui", "player"):
        return False
    limiter = _limiter()
    if limiter is None:
        # Without the counter there is no way to tell a first launch from a
        # fourth one. Showing the intro once more is better than never.
        return True
    try:
        return limiter.instance_count(role, exclude_pid=int(pid or os.getpid())) == 0
    except Exception:
        return False


def is_first_player_window(pid: int | None = None) -> bool:
    """True when no other MP Liquid Player window is open."""
    return is_first_window("player", pid)


def is_first_gui_window(pid: int | None = None) -> bool:
    """True when no other MP Gui window is open."""
    return is_first_window("gui", pid)


class PlayerIntro:
    """The intro movie played by the first MP Gui or MP Liquid Player window.

    `play()` blocks until the movie ends, is skipped, or reaches its timeout,
    and runs before the window is created, so the movie is the first thing on
    screen and is gone before the glass appears. A second, third, or fourth
    window of the same kind never plays it.
    """

    def __init__(self, mode: str | None = None, root: Path | None = None, timeout: int | None = None):
        self.mode = mode
        self.root = Path(root) if root else script_root()
        self.timeout = max(5, int(timeout if timeout is not None else intro_video_timeout(self.root)))
        self.process: subprocess.Popen | None = None
        self.played = False

    def play(self) -> bool:
        """Play the intro once. False means this window skipped it."""
        if self.played:
            return True
        role = intro_role(self.mode)
        if role == "gui":
            if not gui_intro_wanted(self.mode, self.root):
                return False
            first = is_first_gui_window()
        elif role == "player":
            if not player_intro_wanted(self.mode, self.root):
                return False
            first = is_first_player_window()
        else:
            return False
        if not intro_video_enabled(self.root):
            return False
        # Instance two and above never show the movie, even if this process
        # is the one that happens to be closing later.
        if not first:
            return False
        script = display_script(self.root)
        video = find_exit_video(self.root)
        if script is None or video is None:
            return False

        env = dict(os.environ)
        env["PLAYER_INTRO_VIDEO_MAX_SECONDS"] = str(self.timeout)
        # Skip keys are read from the terminal that launched the player. A
        # detached child has no controlling terminal, so it is attached to a
        # fresh session instead and left to finish, time out, or be closed by
        # clicking its own window.
        attached = _has_tty()
        kwargs = {}
        if not attached:
            kwargs["start_new_session"] = True
        try:
            self.process = subprocess.Popen(
                ["bash", str(script), INTRO_SCRIPT_ARG],
                cwd=str(self.root),
                env=env,
                stdin=None if attached else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except (OSError, subprocess.SubprocessError):
            self.process = None
            return False
        self.played = True
        try:
            self.process.wait(timeout=self.timeout + 5)
        except subprocess.TimeoutExpired:
            self.stop()
        finally:
            # The movie window must be gone before the player is created, or
            # it would sit on top of the brand-new glass.
            self.process = None
        return True

    def stop(self) -> None:
        proc, self.process = self.process, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass


def _has_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def play_player_intro(
    mode: str | None = None,
    root: Path | None = None,
    timeout: int | None = None,
) -> bool:
    """Play the first-window intro movie. True when it actually played.

    `mode="MANAGER"` is the first MP Gui. A plain player mode is the first
    MP Liquid Player. Anything else, and every window after the first of
    that kind, returns False without starting a player.
    """
    return PlayerIntro(mode, root, timeout).play()


class ExitVideo:
    """One run of the exit movie, owned by the window that is closing.

    `start()` is the whole decision: it elects this process, writes the
    closing marker, and launches the player. Until `finish()` runs the marker
    keeps sibling windows from showing the movie a second time.
    """

    def __init__(
        self,
        mode: str | None = None,
        root: Path | None = None,
        timeout: int | None = None,
        elect: bool = True,
    ):
        self.mode = mode
        self.role = role_for_mode(mode)
        self.root = Path(root) if root else script_root()
        self.timeout = max(5, int(timeout if timeout is not None else exit_video_timeout(self.root)))
        self.process: subprocess.Popen | None = None
        self.elected = False
        # `elect=False` is for an explicit close of the whole suite (the tray's
        # Close MP Mix Manager item). It must play regardless of how many
        # windows are open, because they are all about to be stopped.
        self.elect = bool(elect)
        self._deadline = 0.0

    # -- election ---------------------------------------------------------
    def start(self) -> bool:
        """Elect this window and start the movie. False means stay silent."""
        if self.process is not None:
            return True
        if not exit_video_enabled(self.root):
            return False
        if self.elect and not should_play_exit_video(self.role):
            return False
        self.elected = True

        script = display_script(self.root)
        if script is None or find_exit_video(self.root) is None:
            # No movie shipped, or the tree was copied without the loader.
            # Nothing to wait for; release the marker immediately.
            finish_exit_video(self.role)
            self.elected = False
            return False

        env = dict(os.environ)
        env["EXIT_VIDEO_MAX_SECONDS"] = str(self.timeout)
        # A closing host has no terminal to read a skip key from.
        env["MP_EXIT_VIDEO_NO_TTY"] = "1"
        try:
            self.process = subprocess.Popen(
                ["bash", str(script), "--exit"],
                cwd=str(self.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError):
            self.process = None
            finish_exit_video(self.role)
            self.elected = False
            return False
        self._deadline = time.monotonic() + self.timeout
        return True

    # -- waiting ----------------------------------------------------------
    def running(self) -> bool:
        if self.process is None:
            return False
        if self.process.poll() is not None:
            return False
        if time.monotonic() >= self._deadline:
            self.stop()
            return False
        return True

    def wait(self, pump=None, interval: float = 0.03) -> None:
        """Block until the movie ends.

        The host closes its own window before calling this, so there is nothing
        left to draw. `pump` remains for a caller that still owns a live window
        and wants it to stay responsive while the movie runs.
        """
        while self.running():
            if pump is not None:
                try:
                    pump()
                except Exception:
                    pass
            time.sleep(interval)

    # -- teardown ---------------------------------------------------------
    def stop(self) -> None:
        proc, self.process = self.process, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass

    def finish(self) -> None:
        """Stop the player if needed and clear the closing marker."""
        self.stop()
        if self.elected:
            finish_exit_video(self.role)
            self.elected = False

    def __enter__(self) -> "ExitVideo":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.finish()


def play_exit_video(
    mode: str | None = None,
    root: Path | None = None,
    timeout: int | None = None,
    pump=None,
    elect: bool = True,
) -> bool:
    """Start, wait for, and finish the exit movie in one call.

    Returns True when the movie played on this window. Call it after the
    window is destroyed; pass `pump` only if the caller still owns a window
    that must stay responsive.
    """
    video = ExitVideo(mode, root, timeout, elect=elect)
    if not video.start():
        return False
    try:
        video.wait(pump)
    finally:
        video.finish()
    return True


def main(argv: list[str] | None = None) -> int:
    """Command line entry point, used by the hosts' wrappers and by tests."""
    args = list(sys.argv[1:] if argv is None else argv)
    mode = ""
    root = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--manager":
            mode = "MANAGER"
        elif arg == "--mode" and index + 1 < len(args):
            mode = args[index + 1]
            index += 1
        elif arg == "--root" and index + 1 < len(args):
            root = Path(args[index + 1])
            index += 1
        index += 1
    return 0 if play_exit_video(mode, root) else 1


if __name__ == "__main__":
    raise SystemExit(main())
