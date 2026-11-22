# MP Liquid Mixer on Windows 10 & 11 (Beta 0.0.1)

A native Windows port of the MP Liquid Mixer. It keeps the Linux mixer's
four-channel model, theme table, MIDI profiles and deck IPC, and replaces the
GTK/VTE host with a single Tkinter glass window.

> **Status: 0.0.1 beta.** The port loads, draws, cycles themes with `C`, starts
> four MP Audio Player engines, records the Windows output through WASAPI
> loopback, and places the MP Liquid Visualizer directly behind the mixer.
> Windows 11 is expected to work because it uses only Tk, winmm, and WASAPI.

## Start it

```bat
launch_liquid_mixer.bat
```

or:

```bat
MP_Liquid_Mixer\run.bat
```

Useful switches:

```bat
launch_liquid_mixer.bat --windowed
launch_liquid_mixer.bat --no-visualizer
launch_liquid_mixer.bat --music-mode
launch_liquid_mixer.bat --mix-archive-mode
launch_liquid_mixer.bat --midi-profile A
launch_liquid_mixer.bat --midi-device "Traktor Kontrol S4"
```

The launcher uses `pythonw.exe` so the mixer does not open a console beside its
window. `--self-test` runs the headless checks and writes no window.

## Requirements

- **Windows 10 or 11**, Python 3.10+.
- `tkinter` (included with the python.org and Microsoft Store builds).
- `numpy`, `sounddevice` for the four MP Audio Player decks.
- **PyAudioWPatch** for WASAPI loopback recording (`pip install PyAudioWPatch`).
  Recording is disabled with a clear status message when it is absent.
- `glfw` / `PyOpenGL` and a working GPU driver for the companion visualizer.
- `ffmpeg` for FLAC recordings and for the visualizer's video helpers.

The project installer adds these through `requirements.txt` /
`scripts/mp_dependencies.py`. The mixer's decks use the same engine and IPC as
MP Audio Player; no separate audio install is needed.

## Controls

| Input | Action |
| :--- | :--- |
| Channel fader | Deck volume, 0-100% (also mouse wheel) |
| Crossfader | Equal-power blend between decks A and B |
| TRIM / HI / MID / LOW knobs | Per-channel gain and three-band EQ (drag vertically or wheel) |
| FILTER knob | Insert depth, or the DJ filter sweep when FILT is selected |
| FLA / REV / DEL / GAT / FILT | Pick the insert for that channel |
| FX | Arm or bypass that channel's insert |
| CUE | Route that deck to the monitor/cue bus |
| PLAY / CUE / LIBRARY | Deck transport and track picker |
| SWITCH A/C or B/D | Flip that side between its two decks |
| MIXER | Internal software levels / External house-mixer unity |
| VISUAL or `V` | Show/hide the companion visualizer |
| `C` | Next theme (writes the shared theme file) |
| `S` | Settings overlay |
| `R` | Start/stop WASAPI loopback recording |
| `1`-`9` | Send that preset key to the visualizer window |
| `L` / `G` / `M` | Launch MP Liquid Player / MP Gui / MP Mix Manager |
| `F11` or double-click | Full screen / 1440x810 windowed |
| `Esc` | Leave full screen, then close |

## Kontrol S4 / S3 MIDI

The mixer binds only a controller whose Windows MIDI name contains `s4`,
`traktor`, `kontrol` (profile A) or `s3` (profile B). A generic MIDI keyboard is
never opened or mapped.

The S4 must be in **MIDI mode** for Windows to expose it as a MIDI input. Check
the top bar's status line:

- `Traktor Kontrol S4 (MIDI A)` - mapped and reading.
- `no Kontrol A MIDI port (found: MPKmini2)` - Windows sees other MIDI devices
  but not the S4. Put the S4 in MIDI mode (or install NI's driver) and reopen
  the mixer; `S` > MIDI profile can also switch to B.

For testing without an S4, select a visible input by index or name:

```bat
launch_liquid_mixer.bat --midi-device 0
```

The S4 MIDI map covers Play/Cue, Sync/Shift, Browse/Load, Record, the four
channel volumes, crossfader, pitch and the EQ/filter CCs. The native S4 HID
(evdev) path from Linux is not implemented on Windows yet.

## Visualizer behind the mixer

The mixer starts one companion MP Liquid Visualizer with
`MP_VISUALIZER_COMPANION=1` and `MP_VISUALIZER_MATCH_GUI=1`, publishes its
window rectangle and HWND to `liquid_gui_window.json`, and the visualizer's
Windows bond places itself directly behind the mixer. It follows moves and
resizes. `V` hides/shows it; closing the mixer closes it.

The mixer window uses the alpha in
`~/.config/mix-manager/liquid_mixer_windows.json` (default `0.94`). Lower it to
`0.88` for more of the scene, or raise it to `1.0` for a fully solid window.
Without a visualizer the window is forced solid so the desktop cannot bleed in.

## Recording

`R` records the default Windows output with PyAudioWPatch's WASAPI loopback to
`MP_AUDIOWAV_REC_OUTPUT` (or the configured recording directory). WAV is the
default; choose FLAC in Settings and `ffmpeg` converts the finished file.
Recording the master output is independent of the four deck engines, so the
faders, crossfader, EQ, filters and inserts are all captured.

## Themes

`C` cycles the shared MP theme list, writes `~/.config/mix-manager/theme`, and
repaints the mixer chrome, buttons, knobs, meters and Settings overlay. The four
deck engines read the same file within a second. The mixer opens in **Into
Darkness** like the Linux build; any saved theme is replaced on launch.

## Known beta limitations

- The two Linux playlist/VTE panes are replaced by the library overlay and the
  four MP Audio Player engines; there is no embedded terminal in the mixer.
- Monitor/cue routing uses the model's cue state; per-app routing to a second
  Windows audio device is not implemented yet.
- BPM-tag sync uses the master deck's pitch; the full `ffprobe` BPM match is
  Linux-only in this beta.
- The native S4 HID path is Linux-only; MIDI mode is required on Windows.
- The Windows 11 VM is not available for this build; the code path uses only
  Tk, winmm and WASAPI, which are stable across Windows 10/11.

## Tests

```bat
python -m unittest tests.test_liquid_mixer_windows -v
python MP_Liquid_Mixer_Windows_10.py --self-test
```

The suite covers theme cycling, S4 CC/note parsing, engine command mapping,
layout, the visualizer companion flags, Windows shortcut/installer inclusion,
and an optional headless Tk smoke test. Run it on Windows 10 and 11.
