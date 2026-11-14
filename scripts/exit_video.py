#!/usr/bin/env python3
"""Play the MP exit movie before the last MP Gui / MP Liquid Player window closes.

The movie is `Exit_MP_Video.mp4` in the install tree. It is played by the same
script that plays the startup logo (`scripts/display_loader_video.sh --exit`),
so the player choice, the borderless always-on-top window and the skip keys are
identical on every platform and there is only one implementation to maintain.

Two rules decide whether it plays at all:

  * Only the last window. MP Gui and MP Liquid Player are counted together, so
    closing one of two open windows stays silent and the movie waits for the
    very last window of either kind.
  * Never twice. Each closing host marks itself before counting, so two windows
    closed at the same instant elect exactly one of them.

The four Liquid Glass hosts use `ExitVideo`: `start()` elects and launches the
movie, the host keeps pumping its own window while `wait()` runs, then
`finish()` clears the marker so a later launch is elected normally.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

EXIT_VIDEO_NAME = "Exit_MP_Video.mp4"
EXIT_VIDEO_TIMEOUT_SECONDS = 90

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
    ):
        self.mode = mode
        self.role = role_for_mode(mode)
        self.root = Path(root) if root else script_root()
        self.timeout = max(5, int(timeout if timeout is not None else exit_video_timeout(self.root)))
        self.process: subprocess.Popen | None = None
        self.elected = False
        self._deadline = 0.0

    # -- election ---------------------------------------------------------
    def start(self) -> bool:
        """Elect this window and start the movie. False means stay silent."""
        if self.process is not None:
            return True
        if not exit_video_enabled(self.root):
            return False
        if not should_play_exit_video(self.role):
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
        """Block until the movie ends, calling `pump` so the host stays live.

        `pump` is the host's own event-pump (drawing a frame, polling input).
        Without it the closing window would freeze for the whole movie.
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
) -> bool:
    """Start, wait for, and finish the exit movie in one call.

    Returns True when the movie played on this window. Call it before the
    window is destroyed and pass `pump` to keep the window responsive.
    """
    video = ExitVideo(mode, root, timeout)
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
