# Testing the Mac mini via NoMachine

**Date:** 15 November 2026
**Host:** the Mac mini on your LAN — macOS 27.2 (build 26B123)
**Client:** NoMachine 9.7.3 on the Linux workstation (`/usr/NX/bin/nxplayer`)

---

## Why NoMachine was worth doing

Plain SSH into macOS runs **outside the GUI session**. That is not a detail: a
whole class of behaviour cannot be exercised or observed from an SSH shell, and
several test failures I had been treating as "environment noise" are exactly
that. NoMachine attaches to the **physical desktop session**, so it runs as the
console user with a real window server.

### The decisive measurement

The same test, same machine, same code, run two ways:

| Context | Command | Result |
|---|---|---|
| SSH (no GUI context) | `python3 -m unittest tests.test_web_companion_install_identity.TestTrayStartupDoesNotAbort.test_exits_zero_without_a_display` | **FAILED** in 7.8 s |
| NoMachine GUI session | same test, `SSH_CONNECTION=none` | **PASSES** (`TRAY_TEST_RC=0`) in 1.8 s |

**Root cause of the SSH failure (proven, not guessed):**

```
$ launchctl asuser 501 /usr/bin/open -g -a Calculator
Could not switch to audit session 0x186b7b: 1: Operation not permitted
```

macOS refuses `launchctl asuser` from an SSH session. `run_macos_tray()` uses
exactly that call to hand the menu-bar agent to LaunchServices, so it can never
succeed over SSH — no matter how correct the code is. The test was reporting a
platform security boundary as a product failure.

### Same story for the PTY tests

`tests/test_option2_submenus.py` failed 13 of 15 over SSH:

```
OSError: [Errno 5] Input/output error
  at os.write(master, tok)
```

Those tests drive interactive menus through a **pseudo-terminal**. An SSH
session without a TTY cannot back one properly, so `os.write` to the master fd
fails. Again an environment capability, not a defect.

---

## How to connect

The working recipe (a saved session file is what makes it repeatable):

```
Server host   : <mac-mini-lan-address>
Server port   : 22            # NoMachine tunnels over SSH here
Daemon port   : 4000
VPN mode      : tunnel
Username      : <your-mac-username>
Authentication: password
```

Then:

```bash
/usr/NX/bin/nxplayer --session /path/to/macmini138.nxs
```

The server is already running on the Mac (`/etc/NX/nxserver --daemon`, plus
`nxnode --localsession` for the physical desktop). Port **4000** answers a TCP
connect; it just waits for the client hello, which is why a bare `nc` shows
nothing.

### Gotchas found along the way

- **The workstation has three monitors** (DP-2 `0,0`, HDMI `696,1740`, DP-3
  `2784,0`). `nxplayer` opens its window on **DP-3**, so a screenshot of the
  primary monitor or any window geometry read from `xdotool` looks wrong.
  Capture the DP-3 region (`+2784+35`) or move the window with a KWin script.
- **`wmctrl`/`xdotool` cannot enumerate the local Konsole** — it is a native
  Wayland surface, so it does not appear in `wmctrl -l` but still composites
  above X windows in a full-screen capture. That is what made several crops
  show the wrong content.
- **`xdotool windowactivate` also activates other WM windows.** Sending keys
  with `--window <id> --clearmodifiers` is what actually reached the Mac; a
  bare `xdotool type` went to whatever had focus locally.
- Moving the NoMachine window to another **virtual desktop closed the client**.

---

## What this means for the test suite

1. **The Mac must be tested inside the GUI session for these suites.** Running
   `tests/run_tests.py` over SSH produced *73 failed, 42 errored* on macOS,
   dominated by:
   - PTY/menu tests (`OSError` on the pty master)
   - the macOS tray/menu-bar test (`launchctl asuser` denied)
   - anything that needs to launch or observe a real window

   The same suites pass for the modules that do not need a GUI.

2. **The MP Auto Tester should drive the Mac over NoMachine, not SSH, for the
   screenshot and tray steps.** This is the change worth making next: an
   `nxplayer`-backed screenshot path would let `sweep_over_vnc "mac"` capture the
   real framebuffer instead of relying on VNC (which macOS now restricts).

3. **Confirmed working on the Mac via NoMachine:**
   - MP Liquid Visualizer launches and renders on the physical desktop
   - the MP Mix Manager desktop shortcuts exist and open
   - the Web Companion answers **HTTP 200** on `:8888`
   - `mp_daily_email_reporter` resolves `BASE_DIR` to the app tree (no
     `~/MP_REPORTER` on this Mac) and imports cleanly

---

## Reproduce

```bash
# 1. build a session file from a cached one, set host/port/user/tunnel
#    (see the recipe above)

# 2. connect
DISPLAY=:0 /usr/NX/bin/nxplayer --session /tmp/opencode/nxsess/macmini138_v2.nxs &

# 3. the client lands on DP-3; capture that region
spectacle -b -n -f -o /tmp/shot.png
magick /tmp/shot.png -crop 2784x1647+2784+35 +repage /tmp/mac_view.png
```
