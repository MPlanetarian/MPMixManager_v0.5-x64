# Traktor Kontrol S4 Hardware MIDI Control — User Guide
**MP Mix Archive Manager & MP Audio Player — Version 0.5.4 Stable (Sensorium)**

---

## 1. Overview & Supported Hardware

In Version 0.4.3, MP Mix Archive Manager introduces direct hardware MIDI integration exclusively for the **Native Instruments Traktor Kontrol S4** via the ALSA hardware MIDI port. The port is **auto-detected**: the S4 is usually card 0 (`hw:0,0` / `/dev/snd/midiC0D0`), but when HDMI audio or another USB interface occupies card 0 the S4 lands on a later card (for example `hw:1,0` / `/dev/snd/midiC1D0`). MP Mix Manager finds the card itself, so no port ever has to be typed.

With this feature enabled, you can control the built-in **MP Audio Player** directly from your physical DJ controller without touching the keyboard or mouse.

### Key Capabilities
* **Dual-Deck Pitch Controls**: Use the physical pitch faders on either side of the unit (Deck A and Deck B) for ultra-smooth real-time pitch and tempo control (-20% to +20%) with zero-latency response and an automatic deadzone center snap (0%).
* **Transport Controls**: Dedicated hardware **PLAY**, **CUE**, and **CUP** buttons for immediate playback response.
* **Track Browsing & Selection**: Use the large central **BROWSE** encoder knob to skip between tracks/mixes and push to play.
* **Quick Seeking & Hotcues**: Loop IN/OUT buttons offer instant 10-second jumps, Loop Size encoder offers 30-second scrubbing, and Hotcues 1–4 jump across the mix (0%, 25%, 50%, 75%).
* **Mixer Volume & Recording**: Channel volume sliders and Master level adjust player volume; Headphone Cue buttons toggle mute; and Loop Recorder REC toggles live session recording.

---

## 2. Putting the Traktor Kontrol S4 into MIDI Mode

By default, the Traktor Kontrol S4 boots in Native Instruments proprietary NHL/HID mode. To transmit standard MIDI signals over the hardware ALSA port to MP Mix Manager, switch the controller into **MIDI Mode**:

### Hardware Shortcut
* **Traktor Kontrol S4 MK1**: Hold down the **`SHIFT`** button and press the **`BROWSE`** button on the mixer.
* **Traktor Kontrol S4 MK2**: Hold down the **`SHIFT`** button and press the **`PREVIEW`** button.
* **Display Confirmation**: Both 2-digit 7-segment **Loop Size Displays** on the left and right decks will light up and display **`ON`**.

> **Note**: To return to standard Traktor mode at any time, press the same shortcut again.

---

## 3. Hardware MIDI Port Verification (auto-detected)

Under Linux, the Traktor Kontrol S4's USB audio/MIDI interface is handled by the kernel ALSA driver (`snd-usb-caiaq`).

You can verify the connection anytime in the terminal:
```bash
amidi -l
```
You should see the S4 on whichever card ALSA gave it:
```text
Dir Device    Name
IO  hw:1,0    Traktor Kontrol S4
```
The raw ALSA MIDI device node is the matching `/dev/snd/midiC<card>D<dev>` (for the line above, `/dev/snd/midiC1D0`).

To see exactly what the manager resolved:
```bash
python3 scripts/traktor_s4_midi.py --status
```

> **Note:** the port is never hardcoded. `--port` is an optional override; leaving it out (including from the menu) lets the manager locate the S4's current card. If you pass a port that does not exist, the daemon adopts the detected one instead.

### On FreeBSD there is no ALSA

FreeBSD does not ship ALSA, `amidi` or `/dev/snd/*`. MIDI controllers arrive
through the kernel `umidi(4)` driver and appear as `/dev/umidi*` (usually
`/dev/umidi0.0`) or `/dev/midi*`. Verify the connection with:

```bash
ls /dev/umidi* /dev/midi*
kldstat | grep umidi        # load it with: kldload umidi
```

The MIDI service still starts and stays in **standby** until a device appears,
so the manager never fails to launch on FreeBSD when no controller is plugged
in. Once a `/dev/umidi*` node exists, the daemon reads raw MIDI from it
directly. The usual group for these nodes is `wheel`/`operator`-accessible via
`/etc/devfs.rules`; add a rule if your user cannot open the device.

---

## 4. Enabling MIDI Control in MP Mix Archive Manager

### First-Time Device Detection Notification
When the MP Mix Archive Manager launches and detects a connected Traktor Kontrol S4 for the first time, a prominent banner notification appears at the top of the **Manager Main Page**:
```text
  🎛️  Traktor Kontrol S4 Hardware MIDI Device Detected (auto-detected ALSA port)!
  Please navigate to Option 19 (Studio Hardware & MIDI) to enable and configure Traktor Kontrol S4 MIDI Device Mapping Controls.
```

### Accessing the Menu
1. From the Manager Main Page, select **Option 19**:
   `18 | Studio Hardware, Audio Interfaces & Master Volume Control`
2. Select **Option 3**:
   `3 | Traktor Kontrol S4 Hardware MIDI Control & Mapping`
   *(Shortcut: You can also type `s4` or `midi` from the main console menu).*
3. Select **Option 1** (`Enable Traktor Kontrol S4 MIDI Mapping Support`).
4. The background listener daemon starts automatically, detecting the S4's card, opening its rawmidi node, and bridging events to the MP Audio Player. The submenu header shows the resolved port and node; only one daemon can own the device, so a second start is refused instead of fighting over it.

---

## 5. Complete Hardware Control Mapping Schema

The mapping schema is intelligently organized across the Traktor Kontrol S4 surface:

| Hardware Control | S4 Element | MP Mix Manager / Player Action | Description |
| :--- | :--- | :--- | :--- |
| **Left Deck Pitch Fader** | Deck A Fader | **DJ Pitch Control** | Adjusts playback pitch smoothly between **-20% and +20%**. Center detent snaps to 0%. |
| **Right Deck Pitch Fader** | Deck B Fader | **DJ Pitch Control** | Independent pitch adjustment on right side (-20% to +20%). |
| **Pitch Bend [+] Button** | Left / Right Deck | **Pitch Nudge Up** | Increments pitch by **+1%**. |
| **Pitch Bend [-] Button** | Left / Right Deck | **Pitch Nudge Down** | Decrements pitch by **-1%**. |
| **SYNC Button (Left)** | Deck A Sync | **Reset Pitch (0%)** | Instantly resets pitch back to 0.0% standard speed. |
| **SYNC Button (Right)** | Deck B Sync | **Shuffle Mode** | Toggles playlist shuffle mode on/off. |
| **PLAY ▶ Button** | Left / Right Deck | **Play / Pause** | Toggles playback. Starts player daemon if offline. |
| **CUE ⏹ Button** | Left / Right Deck | **Cue / Stop** | Stops playback and returns to start position (`0:00`). |
| **CUP ⏯ Button** | Left / Right Deck | **Cue-Play** | Seeks to `0:00` and immediately starts playback. |
| **BROWSE Encoder (Turn)** | Center Mixer | **Track Navigation** | Rotate clockwise for **Next Track/Mix**; rotate counter-clockwise for **Previous**. |
| **BROWSE Encoder (Push)** | Center Mixer | **Play / Select** | Plays selected track or toggles playback. |
| **LOOP IN Button** | Deck Loop Area | **Quick Seek -10s** | Skips backward 10 seconds. |
| **LOOP OUT Button** | Deck Loop Area | **Quick Seek +10s** | Skips forward 10 seconds. |
| **LOOP SIZE Encoder** | Deck Loop Area | **Scrub Seek ±30s** | Rotate clockwise for +30s seek; counter-clockwise for -30s. |
| **HOTCUE 1** | Deck Hotcues | **Jump to 0%** | Jumps to beginning of the mix. |
| **HOTCUE 2** | Deck Hotcues | **Jump to 25%** | Jumps to the 25% point of the mix. |
| **HOTCUE 3** | Deck Hotcues | **Jump to 50%** | Jumps to the halfway mark (50%). |
| **HOTCUE 4** | Deck Hotcues | **Jump to 75%** | Jumps to the 75% mark of the mix. |
| **Channel Faders (A, B, C, D)** | Mixer Strips | **Master Volume** | Continuous fader volume adjustment (**0% to 100%**). |
| **Headphone CUE Buttons** | Channel Cues | **Mute / Unmute** | Instantly toggles audio output mute. |
| **Loop Recorder REC** | Center Mixer | **Live Recording** | Toggles audio recording to `LIVE_RECORDINGS/`. |
| **Loop Recorder PLAY** | Center Mixer | **Play / Pause** | Toggles playback. |

---

## 6. Live MIDI Monitor & Test Mode

To verify that your physical buttons, encoders, and pitch sliders are sending signals properly:
1. Open Option 19 ➔ Option 3 ➔ **Option 4** (`Interactive Live MIDI Monitor & Packet Test`).
2. Move the pitch sliders or press any button on the S4.
3. Every packet will be printed to your terminal in real-time with hex values and the exact action executed:
```text
  [MIDI] CC 14 Ch 1: Val=72                       ➔ DJ Pitch: +3%
  [MIDI] Note ON 0 Ch 1 Vel=127                   ➔ Play / Pause (Toggle) ▶⏸
  [MIDI] Note ON 16 Ch 1 Vel=127                  ➔ Loop In / Quick Seek -10s ⏪
  [MIDI] Note ON 6 Ch 1 Vel=127                   ➔ Sync (Pitch Reset to 0%) 🎚
```

You can also run the monitor directly from the command line:
```bash
./traktor_s4_midi.sh --monitor
```

---

## 7. Official Manuals Reference

The official Native Instruments documentation has been downloaded and archived in the `docs/` folder:
* **Controller Editor Reference Manual**: `docs/Controller_Editor_Manual_English.pdf` (Covers MIDI templates, CC assignments, and S4 mapping architecture).
* **Traktor Kontrol S4 MK2 Manual**: `docs/traktor_kontrol_s4_mk2_manual_english.pdf` (Hardware reference, MIDI mode toggling, audio interface configuration).

You can open these directly via Option 19 ➔ Option 3 in the Mix Archive Manager.

---

## 8. Troubleshooting

* **Issue: The S4 is plugged in, but buttons don't trigger anything.**
  * *Check*: Make sure the S4 is in **MIDI Mode** (`SHIFT + BROWSE` on MK1 or `SHIFT + PREVIEW` on MK2). The Loop displays must say **`ON`**.
* **Issue: `amidi -l` does not list the S4.**
  * *Check*: Check the USB connection and power supply. The S4 requires external AC power for full LED and audio operation.
* **Issue: Permission denied reading the rawmidi node.**
  * *Fix*: Ensure your user is in the `audio` group (`sudo usermod -aG audio $USER`), or test the node shown by `--status`, e.g. `amidi -p hw:1,0 --dump`.
* **Issue: `Device or resource busy` when opening the rawmidi node.**
  * *Check*: The MIDI service is already running and owns the node (rawmidi input is exclusive). Use **Option 4** in the submenu, which pauses and resumes the service automatically, or run `python3 scripts/traktor_s4_midi.py --stop` first.
* **Issue: The MP Audio Player pitch does not move.**
  * *Fix*: Ensure the MP Audio Player is running (`Option 1` or `Option 2`). The MIDI daemon automatically routes commands via IPC to any active player window.
