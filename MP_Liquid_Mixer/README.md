# MP Liquid Mixer 0.0.2

A full-screen, liquid-glass DJ mixer built from the **MP Liquid Player**. Four
real player engines feed a four-channel mixer with a crossfader, and the MP
Liquid Visualizer runs behind the whole surface.

> **Status: 0.0.2.** One 1920x1080 (or 4K) full-screen window. All four
> channels - **A, B, C and D** - have their own engine, fader, EQ, filter knob,
> effect insert and level meter. The two playlist windows each switch between a
> pair of decks (left: A/C, right: B/D), so two windows reach all four channels.
> The mixer always opens in **Into Darkness (Void Out)**.

> **Windows 10/11 beta:** a native Tkinter/winmm port lives in
> [WINDOWS_10.md](WINDOWS_10.md). It keeps the four-channel model, themes,
> MIDI profiles, IPC decks, WASAPI recording and visualizer companion; the
> GTK/VTE playlist panes are replaced by the library overlay.

---

## What is in the box

| Component | What it does |
| :--- | :--- |
| **One full-screen window** | 1920x1080 design grid, scaled and letterboxed to the monitor. Inherits the Liquid Glass host, so it uses the same theme table, ink resolver, fonts and visualizer plumbing as MP Liquid Player. |
| **Four MP Liquid Players** | Real `MP_Audio_Player.py` engines in VTE terminals, four in all. Each has its own IPC namespace, PID, lock and state, so none retires or silences another. They share the library, favorites and theme, and all open the **Music Folders** library in Music mode - never the mix archive, because these decks are for mixing tracks. The terminal is fitted to the player's own 74-column layout. |
| **Two playlist windows, four decks** | The left player shows deck **A** or **C**, the right player **B** or **D**. Each player's header carries a neon **DECK A / SWITCH TO C** button: one click flips that window to its other deck, another flips it back. Switching re-points that side's tempo slider at the new deck and moves the playlist highlight to the track the deck has loaded, flashing the theme colours so the change is obvious. |
| **4 channels + crossfader** | Every fader is live. A and B are blended by the equal-power crossfader (both at -3 dB in the centre, with a centre detent marker on the track); C and D play at their own fader level. Each strip has an **FX** button, a headphone-cue button and a peak meter. |
| **Internal / External mixing** | Internal applies the software faders and crossfader. External hands both decks to the house mixer at unity and bypasses the software levels. |
| **MP Visualizer behind** | A locked companion MP Liquid Visualizer runs behind the mixer at the mixer's exact rectangle in both full screen and windowed mode. It follows every move/resize, cannot be detached (Z/double-click are ignored on a locked companion), and is relaunched if it is closed or killed. The **VISUALIZER** button (or `V`) only hides/shows it. |
| **DJ MIDI auto-map** | A detected **DJ Kontrol** mixer in MIDI mode is mapped automatically (Play A/B, Sync A/B, Shift, Browse, Load, Record, channel volume and deck pitch). A generic MIDI controller is never opened or mapped; only a device whose ALSA name contains `DJ` is used. The native S4 evdev path remains available for hardware that is not in MIDI mode. |
| **Exit_MP_Video.mp4 brackets the window** | On every launch the shared loader movie plays **before** the mixer window exists, and after the last window closes the same movie plays **after** the window is gone. Both use `scripts/display_loader_video.sh` (`--intro-force` / `--exit-force`), so the movie, player choice, borderless window and timeout are the ones every other MP window uses. Each launch path (desktop shortcut, `X` in another MP window, or `run.sh`) shares this one implementation, so the movie never plays twice. |
| **Desktop shortcut** | `MP Liquid Mixer.desktop` on the Desktop and in the application menu on Linux, written by `scripts/install_desktop_shortcuts.py` in the same style as the other four MP entries. It points at `scripts/launch_liquid_mixer.sh` through `mp-desktop-exec`, so moving or renaming the install keeps it working. Windows gets **MP Liquid Mixer.lnk** from the same shortcut writer, pointing at `launch_liquid_mixer.bat`. The mixer is a **selectable fifth app** on Linux and Windows: `./install.sh --apps mixer` installs only it. macOS still refuses `--apps mixer` because there is no port yet. |
| **WAV / FLAC with a selectable part size** | The master output records as WAV (default) or FLAC. The part ceiling is 4 GiB by default and can be lowered to 2 GiB or 1 GiB in Settings; WAV sizes are unsigned 32-bit, so a 4 GiB part passes the old 2 GiB wall. At the ceiling the next file is `..._PART_2`, `..._PART_3`, and so on. |
| **Themed chrome** | A **Trance Connection Enabled** status lamp, a live system clock, the **WAV/FLAC** toggle, a **24-Bit** badge and the **DREAMWORLDS PRODUCTIONS / MPLANETARIAN** credit all use the active theme's accent and secondary colours, so `C` repaints them with the rest of the window. |
| **Transparency bar** | The player's own 20%-80% slider range at the bottom of the screen, applied to the whole glass window so the visualizer shows through. |
| **Settings window** | The **SETTINGS** button in the top bar (or `S`) opens one modal window for every configurable preference: recording folder, format and part size; the **master output** and a separate **monitor output** (for two-interface setups); the default theme; the startup and exit videos; the background visualizer; a default equaliser preset; the MIDI DJ Mixer Profile; start full screen and deck autoplay. The dialog is painted in the mixer's active theme, follows `C` live, and is saved to `~/.config/mix-manager/liquid_mixer_settings.json`. |
| **Master and monitor** | **MASTER** and **MON** trims sit on the right side of the top ribbon, and each channel's headphone button routes that one deck to the monitor interface while the rest stay on the master output. A **MASTER** meter and volume slider at the bottom left show and set the summed output. |
| **Per-channel filter and effects** | Under each channel's LOW EQ knob there is a **FILTER** knob at 1.5x the EQ size. Above it are four fixed-colour effect squares - **red Flanger, blue Reverb, yellow Delay, green Gater** - and an **orange Filter** square. The knob is the effect depth; the orange square turns it into a centre-detent DJ filter sweep (low-pass below centre, high-pass above). The **FX** button above each fader arms or bypasses that insert. |
| **Channel meters** | A segmented ladder beside each fader shows that deck's peak. The zones run yellow at the bottom, green through the middle, yellow at 75% and red at the top. |
| **Tempo & sync** | Two vertical pitch sliders, each beside a playlist window and driving that window's current deck. Each has master-tempo ownership and Sync, and the mouse wheel nudges either one. BPM tags give a true beat match; without tags the target copies the master's pitch. The master follows the most recently started deck and hands over when its track ends. |
| **Themes** | The mixer always opens in **Into Darkness (Void Out)**, whatever was used last. `C` cycles the same theme list as every other MP window. The players follow the shared `theme` file within a second, so every deck and the chrome change together, including the Settings window. |

---

## Run it

```bash
./MP_Liquid_Mixer/run.sh
```

Or open **MP Liquid Mixer** from the Desktop/application menu. The shortcut
starts the same `run.sh`, so either way `Exit_MP_Video.mp4` plays before the
window appears and again after it closes.

Full screen is the default. Useful flags:

```bash
./MP_Liquid_Mixer/run.sh --windowed        # 1600x900 for development
./MP_Liquid_Mixer/run.sh --no-visualizer   # skip the background visualizer
./MP_Liquid_Mixer/run.sh --theme sensorium # start on one theme
```

The GTK/VTE/ALSA host described below is **Linux/FreeBSD only**. **Windows 10
and 11** use the native Tkinter/winmm port in
[WINDOWS_10.md](WINDOWS_10.md), which the installer can select as `--apps mixer`.
It is not available on macOS, WSL or Raspberry Pi.

**Windows 10/11** requirements are Python 3.10+, `tkinter`, `numpy`,
`sounddevice`, `PyAudioWPatch` for WASAPI loopback recording, and
`glfw`/`PyOpenGL` for the visualizer; see [WINDOWS_10.md](WINDOWS_10.md).

The Linux/FreeBSD requirements are the ones MP Liquid Player already needs,
with the X11 and ALSA pieces it uses for the window id, the decks and DJ
hardware:

- **Linux with X11**, or **XWayland** on a Wayland desktop. The host forces
  `GDK_BACKEND=x11` for the X11 window id the visualizer bonds to, so a bare
  Wayland session without XWayland cannot run it.
- **Python 3.8+**, **PyGObject** and **GTK 3** typelibs (`Gdk 3.0`,
  `Gtk 3.0`, `GdkX11 3.0`, `Pango`, `PangoCairo`).
- **VTE 2.91** for the four embedded player terminals. The installer bundles a
  missing VTE into `~/.local/lib/mp-liquid-glass`.
- **PulseAudio or PipeWire-Pulse** with `pactl` for master and monitor
  outputs; `wpctl` is the fallback.
- **PortAudio** through `sounddevice`, plus `numpy`, `Pillow`, `PyOpenGL` and
  `glfw` from `requirements.txt`.
- **ALSA** and **alsa-utils** (`amidi`, `aseqdump`) for a DJ controller; the
  native Kontrol S4 evdev path needs the Python `evdev` module and read access
  to `/dev/input/event*`.
- **`ffmpeg`** for FLAC recording; `flac` is optional for `metaflac` tagging.
- An **OpenGL/GLFW** driver for the locked background visualizer, or start with
  `--no-visualizer` (also available in Settings).
- **`rclone`** for the networked Music Folders library.

The suite's installer runs `scripts/mp_dependencies.py`, which installs the
Python packages and names the distribution packages when a system library is
missing. The launcher finds Python itself; no separate install step.

---

## Controls

| Input | Action |
| :--- | :--- |
| **Channel fader A-D** (drag or wheel) | Deck volume, 0-100% |
| **Crossfader** (drag or wheel) | Equal-power blend between decks A and B, with a centre marker |
| **Knobs** (drag or wheel) | GAIN, TREBLE, MID, LOW and FILTER per channel; MASTER and MON trims; the wheel nudges each one |
| **Effect squares** | Pick which effect the FILTER knob drives; the orange square selects the filter itself |
| **FX button** | Arm or bypass that channel's insert |
| **Headphone button** | Route that channel to the monitor output |
| **DECK A / SWITCH TO C button** | Flip a playlist window to its other deck (left A/C, right B/D) |
| **MASTER slider** (bottom left) | Software master volume |
| **MIXER mode button** or `X` | Internal (software faders) / External (unity, house mixer) |
| **VISUALIZER** button or `V` | Show/hide the visualizer behind the mixer (it is never detached) |
| **Trance Connection Enabled** | Themed status lamp in the top bar |
| **WAV / FLAC** button | Toggle the recording container. The label shows the next recording's format; a live recording restarts in the chosen one. |
| **24-Bit** | Themed badge beside the format button; it is informational and does nothing when pressed |
| **DREAMWORLDS PRODUCTIONS / MPLANETARIAN** | Themed credit button in the mixer panel |
| **EXPORT MIXES** | Moves finished recordings into the primary `MIX_ARCHIVE`: WAV to its root, FLAC to `FLAC_CONVERTED_OUTPUTS`. With no archive configured, nothing is moved and the MP Tray posts a setup reminder. |
| **Clock** | Real-time system clock (date and `HH:MM:SS`), read when the mixer opens and refreshed every second |
| **Transparency bar** (drag) | Window opacity, 20%-80%, persisted |
| `1`-`9` | Choose that MP Visualizer preset on the window behind the mixer |
| `C` | Next theme. In a deck the player handles `C` itself (so search can still type "c"); here it writes the same shared theme file, and the mixer and the Settings window follow it. |
| `S` or **SETTINGS** | Open the Settings window (see below) |
| `R` or **REC** | Start/stop master-output recording |
| `M` | Open MP Mix Manager in a new Konsole window (`Mix_Archive_Manager.sh`) |
| `Z` | Open an MP Liquid Visualizer window |
| `W` | Open an MP Black Glass pane at the mixer's size |
| `L` | Open MP Liquid Player |
| `G` | Open MP Gui |
| `F11` or **double-click the top bar** | Toggle full screen and a 1920x1080 centred window |
| **Drag the top bar** | Move the window while it is windowed |
| **Drag the bottom-right grip** | Resize the window (minimum 960x540) |
| `Esc` or `Q` | Quit and silence the decks. Both apply over the mixer's chrome; a focused deck keeps `Esc` (cancel search) and `Q` (queue) for its own player, so click the chrome first to quit from there. Shutdown pauses and stops all four engines, then escalates to a signal for any that ignore it. |

Inside a deck the player's own keys keep working: `Space` play/pause, arrows to
seek, `1`/`2`/`3` libraries, `/` search, and the rest of the MP Audio Player
key map. While a deck has keyboard focus the mixer takes **no** single-letter
shortcuts, so typing `V`, `R`, `S`, `C`, `W`, `X`, `M`, `Z`, `L`, `G` or
`Esc` into the search field is passed straight to the player. Click the
mixer's chrome to give the shortcuts back. `F11` remains mixer-wide in either
case.

### Settings

**SETTINGS** in the top bar, or `S`, opens one window holding every preference
the mixer can change. **Save Settings** applies it to the running window where
that has a live meaning, and to the next launch where it does not.

| Section | Setting | What it does |
| :--- | :--- | :--- |
| **Recording** | Output folder | Where finished recordings land. The default follows `MIX_ARCHIVE/MP_AUDIOWAV_REC_OUTPUT`; **Use default** puts it back. |
| | Audio format | `WAV` (PCM 16-bit, the default) or `FLAC`. A live recording restarts in the chosen container. |
| | Part size | **4 GB** (the WAV maximum, the default), **2 GB** or **1 GB** per part. A longer set continues as `_PART_2`, `_PART_3`. |
| | Record from | The master output (the room mix, the default) or the monitor of any output device. |
| | Start recording when the mixer opens | Presses REC once the decks are up. |
| **Audio Output** | Master output | Both decks play through this device, **any sink including Bluetooth**. **System default** follows the desktop. The desktop's own default is never changed: the deck engines are pinned with `PULSE_SINK` and an existing stream is moved with `pactl move-sink-input`. |
| **Appearance** | Default theme | Any theme in the drop-down. It becomes the shared theme on save and at launch. |
| | Disable the visualizer window behind the mixer | Checked means the visualizer is never started at launch. `V` still opens one on demand. |
| | Start full screen | Off starts in the centred 1920x1080 window. |
| **Startup & Exit** | Play the startup video | Uncheck and `MP_loader.mp4` never plays before the window. |
| | Play the exit video | Uncheck and `Exit_MP_Video.mp4` never plays when the mixer closes. |
| | Load each deck ready to play | Off (the default) keeps both decks paused. |
| **Deck Audio** | Default equaliser | `Flat`, `Ambient`, `Trance`, `Techno`, `House`, `Progressive`, `Vapourwave` or `Sensorium Interface`, applied to both decks at launch. The mixer's own EQ knobs override it for a deck once moved. |

The settings live in `~/.config/mix-manager/liquid_mixer_settings.json`, a plain
JSON file that can be edited by hand. Every value is validated on load: an
unknown format, part size, theme or preset falls back to the documented default
rather than breaking a launch. Deleting the file restores every default.

The Settings window re-reads the device list when it opens, and **Refresh** in
the Audio Output section fetches it again, so a headset connected while the
mixer is running appears as soon as the desktop has it.

### DJ hardware

The mixer looks for a DJ Kontrol port at startup and keeps looking while
it runs, so a device switched to MIDI mode or plugged in later is picked up.

- Only ALSA rawmidi ports whose device name contains **DJ** are opened.
  The MPK mini and any other generic controller stay unopened and unmapped.
- MIDI-mode buttons use the S4 note/CC map already used by
  `scripts/dj_s4_midi.py`: Play A/B, Sync A/B, Shift, Browse push/turn,
  Load, Record, channel volume and deck pitch.
- The listener prefers the ALSA **sequencer** (via `aseqdump`), which several
  processes can share, so it works beside the standalone DJ service. It
  falls back to the rawmidi node when the sequencer is unavailable.
- The map can be adjusted per control in
  `~/.config/mix-manager/liquid_mixer_midi.json`, for example
  `{"notes": {"0": "sync:A"}, "cc": {"8": "channel:B"}}`.
- A native-mode S4 still uses the evdev path. If both paths see the same
  button, duplicate presses within 80 ms are coalesced.

---

## Recording

The master output is recorded as **PCM 16-bit stereo WAV** by default. The
format button switches to **FLAC** and back; Settings > Recording sets the same
two values, and both formats use the same capture.

- **The part ceiling is 4 GiB by default and can be lowered to 2 GiB or 1 GiB**
  in Settings > Recording. A WAV part at the 4 GiB default is capped at
  4,294,967,248 bytes of audio (44-byte header excluded, rounded down to a whole
  16-bit stereo frame). The RIFF `ChunkSize` and `data` size are written as
  **unsigned 32-bit** fields, so a part is not trapped at 2 GiB.
- **Parts continue automatically.** When the current part reaches the ceiling
  it is finalised and recording continues as `..._PART_2.wav` (or `.flac`),
  then `..._PART_3`, and so on. The original file keeps its name and is part 1.
- **FLAC** is encoded with the `flac` command from raw PCM (or `ffmpeg`, which
  the suite already requires, when `flac` is absent). Its part limit is 16 MiB
  below the chosen ceiling so encoder buffering can never push a file over.
- The record log (`liquid_mixer_recording.log`) is written beside the parts.
- The capture source is the master output (the default, or the device chosen in
  Settings) rather than always the desktop default.

### Exporting to MIX_ARCHIVE

The **EXPORT MIXES** button moves finished recordings out of
`MP_AUDIOWAV_REC_OUTPUT` into the primary archive:

| Recording | Destination |
| :--- | :--- |
| `*.wav` (including `_PART_2`, `_PART_3`, ...) | `<MIX_ARCHIVE>/` |
| `*.flac` (including parts) | `<MIX_ARCHIVE>/FLAC_CONVERTED_OUTPUTS/` |

- The primary archive is `MIX_ARCHIVE_DIR`, read from the environment first and
  then from `config.env`. A configured path that is not currently a directory
  (an unplugged drive) is reported instead of guessed.
- If no `MIX_ARCHIVE_DIR` has ever been set, **nothing is scanned or moved**.
  The MP Tray posts "Set up a primary MIX_ARCHIVE folder before you can export
  mixes."
- On success the MP Tray notification names the archive path and how many WAV
  and FLAC files were moved.
- Export scans both the archive's recording folder and the install-tree
  fallback, so recordings made before the archive was configured are included.
- Existing files are never overwritten; a name clash gets a numeric suffix.
  The recording log is left in place.
- Export is refused while a recording is live, so the open part is never moved
  out from under the capture thread.

The decks deliberately **do not autoplay**. Each starts in **Music mode** with the
Music Folders playlist loaded and its first track paused; `Space` or the player's
Play button starts it.

### The first launch loads the theme track

If the mixer is the very first MP app a user opens from the desktop shortcuts,
no Music Folders and no Mix Archive have been configured yet, so both decks
would otherwise show empty libraries and there would be nothing to mix. In that
one case each deck opens the bundled release theme track
(`MP_SAMPLES/MPlanetarian - The Orbits of Planets (Cosmic Dreamworlds Remake).flac`)
loaded, paused at 0:00 and ready — press `Space` and mix.

That preload is deliberately one-way: as soon as a Music Folders configuration
(`~/.config/mix-manager/music_folders.json`, or a legacy `MUSIC_SMB_HOST` /
`MUSIC_SMB_SHARES`) or a Mix Archive configuration (`MIX_ARCHIVE_DIR`,
`EXTRA_MIX_ARCHIVE_DIRS`, or `MIX_ARCHIVE_CONFIGURED="true"`) exists, the decks
open the real library and the theme track is never pushed in front of it. The
deck is handed a generated one-row playlist
(`~/.config/mix-manager/first_run_mix_playlist.m3u8`) rather than an extra audio
file argument, so its UI list and its engine agree on what is loaded.

### The Music Folders library must be mounted

The decks browse the **Music Folders** library rather than the mix archive, and
that library is generated from SMB shares as `~/MUSIC_SMB/<id>/...` paths. Those
paths are `rclone` FUSE mounts, and a plain desktop login does not start them.
When they are down nothing can play: every row fails `os.path.isfile()` inside
the engine, so pressing Enter (or Play, or the S4's Play button) looks like it
does nothing at all.

The mixer therefore mounts the configured shares itself when it opens, on a
worker thread so the window stays responsive, and reloads both decks' Music
playlists once they answer. Selecting a row whose share is down mounts just that
share before playing it, bounded so a wedged mount cannot freeze the deck.

If nothing can be mounted, the deck headers say so, and a refused row names the
folder: *"Cannot play track.flac: music folder MUSIC004 is offline."* The fix is
almost always a stale `~/.config/mix-manager/music_folders.json` entry or an SMB
login the server no longer accepts:

```bash
python3 scripts/music_folders_manager.py --status   # which shares answer
python3 scripts/music_folders_manager.py --mount    # mount them now
python3 scripts/music_folders_manager.py --list     # configured folders
```

Re-point a share that moved and rebuild the library with `--remove <id>`,
`--add <path>` and `--generate ALL`. A share that is mounted but lists zero
files is not a mixer problem: check it from the server (`smbclient "//host/share"
-U user -c ls`), because an empty share is usually the wrong share name rather
than a broken mount.

### Opening it with `X`

`X` opens this window from **MP Gui**, **MP Liquid Player**, and the console
**MP Mix Manager**. All three call `scripts/liquid_mixer_launch.py`, so they
share one launch path with the desktop shortcut:

| State | What `X` does |
| :--- | :--- |
| No mixer open | Opens exactly one window |
| Mixer already open | Raises that window; never opens a second one |
| Mixer still starting | Says it is starting; never opens a second one |
| Text console (TTY Audio Mode, no display) | The console menu says a desktop is needed; in the terminal player `X` keeps its old stop-and-exit meaning |

Only one mixer can exist at a time because both decks live in fixed IPC
namespaces. The window holds `~/.config/mix-manager/liquid_mixer.lock` with
`flock` for its whole life, and the launcher reads that same lock. A short-lived
claim file (`liquid_mixer_launch.json`) covers the moment between the spawn and
the lock being taken, so two presses in a row cannot both start a window. Close
the mixer window and `X` opens a fresh one.

Only one mixer can run at a time: it holds `~/.config/mix-manager/liquid_mixer.lock`
for its lifetime. A second launch raises the window that owns the lock and
exits, because both decks live in fixed IPC namespaces and would otherwise
retire each other's engines. `X` in any MP window reaches that same check.

The deck terminals render on their own dark pane inside the pale glass card.
The window's transparency still applies to the whole surface, so the visualizer
shows through the chrome and around the player screens.

---

## How it is built

```
MP_Liquid_Mixer/
  MP_Liquid_Mixer.py   # the window, drawing, input, deck spawning, visualizer
  mixer_core.py        # model, equal-power curve, IPC, themes, layout
  mixer_effects.py     # flanger / reverb / delay / gater / filter DSP
  mixer_midi.py        # MIDI DJ Mixer Profiles (Kontrol S4 and Kontrol S3)
  mixer_evdev.py       # native Kontrol fader axes on Linux
  mixer_settings.py    # the saved preferences and the audio-device helpers
  mixer_settings_dialog.py  # the Settings window (GTK, themed)
  run.sh               # launcher
  README.md            # this file
scripts/
  liquid_mixer_launch.py   # the X key: launch once, raise after that
  launch_liquid_mixer.sh   # interpreter/install-tree launcher the shortcut uses
```

`LiquidMixer` subclasses `GlassHost` from
`MP_Audio_Player_Liquid_Glass_OpenGL.py` and reuses its `player_argv`,
`_child_env`, `_visualizer_env`, font resolution and theme helpers. The window is
painted with Cairo on one `Gtk.DrawingArea`; the two VTE terminals are placed on
top of it at the deck rectangles. That keeps the layout independent of the GPU
backend while matching the player's glass palette.

### Multi-instance support in the player

Two engines need two of everything except the library. `MP_Audio_Player.py` now
honours `MP_PLAYER_NAMESPACE`:

| File | Default | Deck A |
| :--- | :--- | :--- |
| socket | `mp_audio_player.sock` | `mp_audio_player-mixerdeck_a.sock` |
| port | `mp_audio_player.port` | `mp_audio_player-mixerdeck_a.port` |
| state | `mp_player_state.json` | `mp_player_state-mixerdeck_a.json` |
| pid / lock | `mp_player.pid`, `mp_player.lock` | `mp_player-mixerdeck_a.pid`, `mp_player-mixerdeck_a.lock` |
| resume | `playback_resume.json` | `playback_resume-mixerdeck_a.json` |

Daemon scans and MPRIS registration are namespace-aware too: deck A never
retires deck B, and only the default player claims the desktop media keys.
`--no-autoplay` is the second addition: no resume, no first-run sample, no
"latest mix" - the engine loads the playlist paused.

The mixer writes `liquid_player_window.json` with its own X11 window id, so the
existing `PlayerBondMixin` in `scripts/visualizer_player_bond.py` stacks the
companion directly below it and follows the rectangle. `_glass_host_running()`
also recognises `MP_Liquid_Mixer` as a player host, so the companion does not
close itself after its 15-second host grace period.

---

## Kontrol S4

The S4 **does not need MIDI mode** for the mixer. Its faders are native
absolute axes on the controller's evdev node, and the mixer reads them
directly:

| S4 control | evdev axis | Mixer action |
| :--- | :--- | :--- |
| Channel fader A | 18 | Channel A (live) |
| Channel fader B | 17 | Channel B (live) |
| Channel fader C | 19 | Channel C (stored, not routed yet) |
| Channel fader D | 16 | Channel D (stored, not routed yet) |
| Crossfader | 23 | Equal-power blend between decks A and B |
| Play (left) | key 263 | Toggle Deck A play/pause |
| Play (right) | key 319 | Toggle Deck B play/pause |
| Pitch fader A | axis 21 | Deck A tempo, -20.0% to +20.0% |
| Pitch fader B | axis 22 | Deck B tempo, -20.0% to +20.0% |
| Sync (left) | key 259 | Sync Deck A to the master |
| Sync (right) | key 315 | Sync Deck B to the master |
| Shift left / right | 257 / 313 | Hold with Sync to make that deck the master |
| Browse wheel | axis 54 | Move the target deck's playlist selection |
| Browse push | key 292 | Stop while playing; otherwise load/play the selection or resume |

Axis values run 0-4095, with 0 at the bottom/left; the mixer scales them to
0.0-1.0 and publishes each fader's current position on attach, so the screen
starts in sync with the hardware.

The axis table comes from the S4 MK1 Linux driver (`Slider.cpp`). To use a
different template or a controller with a different report layout, write
`~/.config/mix-manager/liquid_mixer_s4.json`:

```json
{
  "channels": {"A": 18, "B": 17, "C": 19, "D": 16},
  "crossfader": 23,
  "browse": 54,
  "pitch": {"A": 21, "B": 22},
  "invert": {"A": false, "B": false, "C": false, "D": false, "crossfader": false,
             "browse": false, "pitch:A": false, "pitch:B": false},
  "buttons": {"play:A": 263, "play:B": 319, "load": 292,
              "sync:A": 259, "sync:B": 315, "shift:A": 257, "shift:B": 313}
}
```

`invert: true` flips a fader that runs the wrong way.

The two vertical **PITCH** sliders beside the mixer show and set each deck's
tempo (-20% to +20%, centre detent 0). Moving the S4's long-throw tempo faders
drives them (axes 21/22). The **SYNC** pill in each deck header shows
**MASTER** on the deck that owns the master tempo. Click it to sync that deck,
or Shift-click it (or hold the hardware Shift and press Sync) to make it the
master. The master is also the deck you most recently start playing; it hands
over automatically when its track ends or changes while the other deck is
playing.

Sync prefers the tracks' **BPM tags** (read with `ffprobe`) and the master's
current pitch, so tagged tracks beat-match at the correct ratio. When a track
has no BPM tag it falls back to matching the master's pitch percentage.

The Browse wheel is a relative counter (0-15, one detent at a time). Each
step is fed to the **target deck** as an Up/Down key, so the player's playlist
selection moves without starting playback. Push the browse knob to toggle that
deck: **playing -> pause**, **paused after browsing -> load and play the
selected row**, **paused without browsing -> resume the current track**. Pushing
again after a stop resumes/plays, so the button behaves as a play/stop toggle. The target deck is the last one you clicked or
pressed Play on; the mixer shows a **BROWSE A/B** button you can click to
switch it. Rotate the other way with `"invert": {"browse": true}`.

The Play buttons are key events (263 left / 319 right) and toggle the
matching deck through its own IPC socket, so they behave like the player's
Space bar. The top bar reports the state live: green
`S4 /dev/input/eventN  A 62%` while a fader moves, amber when the controller
is idle, and `S4: not found` when the USB cable is out. Reading evdev is shared, so the standalone DJ MIDI
service (and DJ itself) can keep the device open at the same time; the
mixer no longer stops anything.

> **MIDI mode.** There is no host command that forces an S4 MK1/MK2 into MIDI
> mode: the switch is handled by the controller firmware on its button combo,
> and the caiaq driver exposes no mode command (its EP1 set is device info,
> IO/ERP reads, MIDI read/write, audio params, auto-message and LED dimming).
> The native axes above make that switch unnecessary for the faders. If you do
> want MIDI mode for other software, the correct combos are **SHIFT (left or
> right deck) + the magnifying-glass BROWSE button** on MK1 and
> **SHIFT + PREVIEW** on MK2, with the unit on its power supply.

## Tests

```bash
python3 -m unittest tests.test_liquid_mixer tests.test_liquid_mixer_settings -v
```

The suite covers the equal-power curve, all four live channels, External
unity, persistence, the 1920x1080 composition and 4K scaling, the opacity and
fader coordinate maps, theme cycling, deck path namespacing and the player's
`MP_PLAYER_NAMESPACE` / `--no-autoplay` wiring.

`tests.test_liquid_mixer_settings` covers the Settings model: defaults,
validation, persistence, the theme list, the master/monitor device helpers, the
recording part limits and the recorder's capture device. The GTK dialog itself
is driven by those tests through a stubbed `run()`, so a headless run still
exercises it, including the themed CSS and the `C` key cycling the theme.

`tests.test_liquid_mixer_v02` covers the four-deck playlist switches, the filter
knob and its five switches, the effect bypass buttons, the channel and master
meters, the mouse wheel on every fader and knob, the quit keys, and the Settings
window's live theme. `tests.test_liquid_mixer_player_bridge` covers the player
side: the Favorites toggle and the per-channel insert effect.

---

## Known limits (0.0.2)

- The four engines all play through the master output (or the monitor output
  for a cued deck). External mode sets every deck to unity; it does not route
  them to separate physical outputs. Use it when an external mixer is doing the
  summing.
- The effect squares are fixed colours on every theme, by design: they are a
  hardware-style legend for the FILTER knob, not themed chrome.
- With no master output configured the decks follow the system default, as the
  player always does. If the desktop default changes between engine starts, the
  deck status lines can name different devices; set the default output (or pin
  one in Settings) before launching the mixer.
- One mixer window. Closing it (Esc or Q over the chrome) pauses and stops all
  four deck engines, because they are detached daemons rather than children of
  the window.
- The companion visualizer is shared with the Liquid Player state file. The
  mixer only stops a visualizer it started; a player-owned one is left alone.
- The glass is Cairo-painted rather than raymarched; it keeps the player's
  palette, transparency range and typography without a second GPU context.
