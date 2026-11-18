# MP Liquid Mixer 0.0.1

A full-screen, liquid-glass DJ mixer built from the **MP Liquid Player**. Two real
player engines sit left and right with a wide gap between them; the gap holds a
four-channel mixer with a crossfader, and the MP Liquid Visualizer can run
behind the whole surface.

> **Status: 0.0.1.** One 1920x1080 (or 4K) full-screen window. Channels **A and
> B** are live and control the two players. Channels **C and D** are drawn and
> remember a position but are intentionally not wired to anything yet.

---

## What is in the box

| Component | What it does |
| :--- | :--- |
| **One full-screen window** | 1920x1080 design grid, scaled and letterboxed to the monitor. Inherits the Liquid Glass host, so it uses the same theme table, ink resolver, fonts and visualizer plumbing as MP Liquid Player. |
| **Two MP Liquid Players** | Real `MP_Audio_Player.py` engines in VTE terminals, one per side. Each has its own IPC namespace, PID, lock and state, so neither retires or silences the other. They share the library, favorites and theme, and both open the **Music Folders** library in Music mode - never the mix archive, because these decks are for mixing tracks. The terminal is fitted to the player's own 74-column layout. |
| **4 channels + crossfader** | Faders A and B apply per-deck gain; the crossfader is equal-power (both decks at -3 dB in the centre). C and D are inactive placeholders. |
| **Internal / External mixing** | Internal applies the software faders and crossfader. External hands both decks to the house mixer at unity and bypasses the software levels. |
| **MP Visualizer behind** | A locked companion MP Liquid Visualizer runs behind the mixer at the mixer's exact rectangle in both full screen and windowed mode. It follows every move/resize, cannot be detached (Z/double-click are ignored on a locked companion), and is relaunched if it is closed or killed. The **VISUALIZER** button (or `V`) only hides/shows it. |
| **Transparency bar** | The player's own 20%-80% slider range at the bottom of the screen, applied to the whole glass window so the visualizer shows through. |
| **Tempo & sync** | Vertical pitch sliders for A/B, master-tempo ownership, and Sync. BPM tags give a true beat match; without tags the target copies the master's pitch. The master follows the most recently started deck and hands over when its track ends. |
| **Themes** | `C` cycles the same theme list as every other MP window. The players follow the shared `theme` file within a second, so both decks and the chrome change together. The mixer also repaints each deck terminal's background and ink from the theme, including when `C` is pressed inside a deck or in MP Gui. |

---

## Run it

```bash
./MP_Liquid_Mixer/run.sh
```

Full screen is the default. Useful flags:

```bash
./MP_Liquid_Mixer/run.sh --windowed        # 1600x900 for development
./MP_Liquid_Mixer/run.sh --no-visualizer   # skip the background visualizer
./MP_Liquid_Mixer/run.sh --theme sensorium # start on one theme
```

Requirements are the ones MP Liquid Player already needs: Linux with XWayland
(the host forces `GDK_BACKEND=x11` for the X11 window id the visualizer bonds
to), GTK 3, VTE, PyGObject, and the project's `scripts/` on disk. The launcher
finds Python itself; no install step.

---

## Controls

| Input | Action |
| :--- | :--- |
| **Channel A / B fader** (drag) | Deck volume, 0-100% |
| **Crossfader** (drag) | Equal-power blend between deck A and deck B |
| **Channel C / D** | Inactive in 0.0.1 ("SOON") |
| **MIXER mode button** or `X` | Internal (software faders) / External (unity, house mixer) |
| **VISUALIZER** button or `V` | Show/hide the visualizer behind the mixer (it is never detached) |
| **Transparency bar** (drag) | Window opacity, 20%-80%, persisted |
| `C` | Next theme. In a deck the player handles `C` itself (so search can still type "c"); here it writes the same shared theme file, and the mixer follows it. |
| `F11` or **double-click the top bar** | Toggle full screen and a 1920x1080 centred window |
| **Drag the top bar** | Move the window while it is windowed |
| **Drag the bottom-right grip** | Resize the window (minimum 960x540) |
| `Esc` | Quit (stops both deck engines) |

Inside a deck the player's own keys keep working: `Space` play/pause, arrows to
seek, `1`/`2`/`3` libraries, `/` search, and the rest of the MP Audio Player
key map. The mixer only takes `C`, `V`, `X`, `F11` and `Esc`, and only when the
deck is not the focused widget.

The decks deliberately **do not autoplay**. Each starts in **Music mode** with the
Music Folders playlist loaded and its first track paused; `Space` or the player's
Play button starts it.

Only one mixer can run at a time: it holds `~/.config/mix-manager/liquid_mixer.lock`
for its lifetime. A second launch prints that it is already open and exits,
because both decks live in fixed IPC namespaces and would otherwise retire each
other's engines.

The deck terminals render on their own dark pane inside the pale glass card.
The window's transparency still applies to the whole surface, so the visualizer
shows through the chrome and around the player screens.

---

## How it is built

```
MP_Liquid_Mixer/
  MP_Liquid_Mixer.py   # the window, drawing, input, deck spawning, visualizer
  mixer_core.py        # model, equal-power curve, IPC, themes, layout
  run.sh               # launcher
  README.md            # this file
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

## Traktor Kontrol S4

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
is idle, and `S4: not found` when the USB cable is out. Reading evdev is shared, so the standalone Traktor MIDI
service (and Traktor itself) can keep the device open at the same time; the
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
python3 -m unittest tests.test_liquid_mixer -v
```

The suite covers the equal-power curve, channel gating (C/D inert), External
unity, persistence, the 1920x1080 composition and 4K scaling, the opacity and
fader coordinate maps, theme cycling, deck path namespacing and the player's
`MP_PLAYER_NAMESPACE` / `--no-autoplay` wiring.

---

## Known limits (0.0.1)

- C and D are drawn but not routed. The model already stores their gain, so
  wiring a third and fourth engine is additive.
- External mode sets both decks to unity; it does not route them to separate
  physical outputs. Use it when an external mixer is doing the summing.
- Both decks play through the system default output, as the player always does.
  If the desktop default changes between the two engine starts, the deck status
  lines can name different devices; set the default output before launching the
  mixer and both engines follow it.
- One mixer window. Closing it stops both deck engines, because they are
  detached daemons rather than children of the window.
- The companion visualizer is shared with the Liquid Player state file. The
  mixer only stops a visualizer it started; a player-owned one is left alone.
- The glass is Cairo-painted rather than raymarched; it keeps the player's
  palette, transparency range and typography without a second GPU context.
