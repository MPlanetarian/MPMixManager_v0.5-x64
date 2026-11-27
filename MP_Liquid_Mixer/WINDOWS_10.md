# MP Liquid Mixer on Windows 10 & 11 (Beta 0.0.1)

A native Windows port of the MP Liquid Mixer. It is **the same mixer** as the
Linux build: both ports draw the one shared 1920x1080 design grid from
`mixer_core.layout()`, use the same theme table, the same MIDI maps and the same
deck IPC. Only the window host differs — GTK/VTE on Linux, Tkinter on Windows.

> **Status: 0.0.1 beta.** The window loads, draws the full Linux layout, cycles
> themes with `C`, shows two MP Liquid Players, records the Windows output
> through WASAPI loopback, and places the MP Liquid Visualizer directly behind
> the mixer. Windows 11 uses the same Tk, winmm and WASAPI paths.

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

## The window

The layout is identical to the Linux mixer, at one scale:

| Area | What is there |
| :--- | :--- |
| **Top ribbon** | `MP LIQUID MIXER`, the MASTER and MONITOR trims beside the title, the Trance Connection lamp, the DJ MIDI DJ Mixer Profile switch, the controller status, SETTINGS, VISUALIZER ON/OFF and the close button. |
| **Left and right player panes** | Two MP Liquid Players, one per side. Each pane shows the visualizer name, the library, the loaded track, format/rate/channels, the position and progress bar, DJ PITCH, VOLUME, the two 16-band NEON PRISM analysers, the FAVORITES / VISUALIZATION / BASS BOOST row, and **the playlist that deck is mixing from**. |
| **Mixer column** | Four knob rows (GAIN, TREBLE, MID, LOW), the larger FILTER knob, the four fixed-colour effect squares plus the orange Filter square, the four narrow channel strips with their OFF/ON effect switch, headphone cue, fader and meter, then VERIFY AUDIO FILES, the crossfader, BROWSE, INTERNAL/EXTERNAL, WAV and 24-Bit, REC, the credit, the live clock and EXPORT MIXES. |
| **Bottom ribbon** | The master meter and volume, the TRANSPARENCY track with its percentage, and the keyboard legend. |

The Windows host adds exactly three boxes to the shared grid — the close button,
and the MASTER/MONITOR trims with their percentage line — and moves VISUALIZER
left so the three never overlap. Every other rectangle comes from
`mixer_core.layout()` unchanged.

## The two players and their playlists

Each side runs a real `MP_Audio_Player.py --daemon` engine in its own IPC
namespace (`mixerdeck_a` … `mixerdeck_d`), exactly as Linux does. The left pane
shows deck **A** or **C**; the right pane shows **B** or **D**. Pressing the
deck-switch button in a pane flips that side to its other deck.

The pane paints the *engine's own* playlist, requested over IPC
(`get_playlist`), so it is the list that deck will actually play. Clicking a row
loads and starts that track on that deck. When a deck is playing, the list
scrolls to keep the current row visible. The mouse wheel scrolls a pane, and
`↑`/`↓` scroll the left pane.

The Linux build embeds a VTE terminal for the same panel; Windows has no VTE, so
the pane is painted from the engine status and playlist instead. The content —
track line, format, pitch, volume, analysers, library list — is the same.

## Themes

The mixer **always opens in Into Darkness (Void Out)**, on both platforms. The
shared theme file is rewritten on every launch, so the four deck engines start
in the same theme. `C` cycles the shared theme list from there and repaints the
whole surface, the decks and the Settings overlay.

Into Darkness has a black field and black accents. Fills and borders stay black;
text accents are brightened just enough to read, using the same `_ink_accent`
rule as the Linux window.

## Transparency

The TRANSPARENCY track at the bottom of the window controls the Tk window's
`-alpha`, which is what lets the bonded visualizer show through the glass. It
uses the shared model value (`liquid_mixer.json` → `opacity`), 10%–100%, the
same range and the same file as Linux.

- With the visualizer enabled, dragging the track changes the window alpha
  immediately, and the percentage follows.
- Without the visualizer there is nothing behind the glass, so the window is
  forced solid (100%) and the desktop cannot bleed into the panels.
- The value is saved with the model, so the next launch keeps it.

`_alpha_state` records the target, the last applied alpha and any platform
error; the Settings overlay prints the applied value next to the requested one.

## Kontrol S4 / S3 MIDI

The mixer binds only a controller whose Windows MIDI name contains `s4`,
`traktor`, `kontrol` (profile A) or `s3` (profile B). A generic MIDI keyboard is
never opened or mapped.

The S4 must be in **MIDI mode** for Windows to expose it as a MIDI input. Check
the top bar's status line:

- `DJ MIXER Kontrol S4  <name> mapped` - mapped and reading.
- `Kontrol A not found - check the USB cable` - Windows sees no Kontrol port.
  Put the S4 in MIDI mode (or install NI's driver) and reopen the mixer; the
  profile button switches to B for an S3.

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

## Recording and export

`R` records the default Windows output with PyAudioWPatch's WASAPI loopback to
`MP_AUDIOWAV_REC_OUTPUT` (or the configured recording directory). WAV is the
default; choose FLAC in Settings and `ffmpeg` converts the finished file.
Recording the master output is independent of the four deck engines, so the
faders, crossfader, EQ, filters and inserts are all captured. EXPORT MIXES moves
finished recordings into the primary MIX_ARCHIVE.

## VERIFY AUDIO FILES

The button checks every row of the browsed playlist on background workers. If
any file cannot be played it shows a modal prompt offering **REMOVE FROM
PLAYLIST** or **KEEP THEM**; removal rewrites the shared playlist the same way
the Linux window does.

## Controls

| Input | Action |
| :--- | :--- |
| Channel fader | Deck volume, 0-100% (also mouse wheel) |
| Crossfader | Equal-power blend between decks A and B |
| GAIN / TREBLE / MID / LOW knobs | Per-channel trim and three-band EQ (drag vertically or wheel) |
| FILTER knob | Insert depth, or the DJ filter sweep when the orange square is chosen |
| Effect squares / orange square | Pick the insert for that channel |
| OFF / ON above a fader | Bypass that channel's insert |
| Headphone button | Route that deck to the monitor/cue bus |
| Deck-switch button | Flip that pane between A↔C or B↔D |
| Playlist row | Load and start that track on that deck |
| SYNC / MASTER | Copy the master deck's pitch to this deck |
| BROWSE | Cycle the browse target deck |
| INTERNAL / EXTERNAL | Software faders / unity into an external mixer |
| WAV / 24-Bit | Recording container and the fixed bit-depth badge |
| VISUAL or `V` | Show/hide the companion visualizer |
| `C` | Next theme (writes the shared theme file) |
| `S` | Settings overlay |
| `R` | Start/stop WASAPI loopback recording |
| `X` | Toggle INTERNAL/EXTERNAL |
| `1`-`9` | Send that preset key to the visualizer window |
| `L` / `G` / `M` / `Z` | Launch MP Liquid Player / MP Gui / MP Mix Manager / the standalone visualizer |
| `F11` or double-click | Full screen / windowed |
| `Esc` | Leave full screen, then close |

## Requirements

- **Windows 10 or 11**, Python 3.10+.
- `tkinter` (included with the python.org and Microsoft Store builds).
- `numpy`, `sounddevice` for the four MP Audio Player decks.
- **PyAudioWPatch** for WASAPI loopback recording (`pip install PyAudioWPatch`).
  Recording is disabled with a clear status message when it is absent.
- `glfw` / `PyOpenGL` and a working GPU driver for the companion visualizer.
- `ffmpeg` for FLAC recordings and for the visualizer's video helpers.

The project installer adds these through `requirements.txt` /
`scripts/mp_dependencies.py`.

## Known beta limitations

- The player panes are painted from the engine's status and playlist. There is
  no embedded terminal in the mixer, so the player's own typed search and
  command line are not available inside the pane; the library overlay and the
  row clicks cover track selection.
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

The suite covers the forced Into Darkness default and its readable ink, the
shared 1920x1080 layout grid, the narrow four-channel strips, the two player
panes and their live playlists, the transparency model and its alpha range, S4
CC/note parsing, engine command mapping, the visualizer companion flags,
Windows shortcut/installer inclusion, and an optional headless Tk render.
