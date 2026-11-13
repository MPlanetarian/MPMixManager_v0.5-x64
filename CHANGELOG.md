# Changelog

All notable changes to the **MP Mix Archive Manager** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed
- **The visualizer sits directly behind the player and stays visible over other windows** (`scripts/visualizer_player_bond.py`, `scripts/liquid_glass_slider.py`, `scripts/launch_liquid_glass.sh`, `tests/test_visualizer_player_bond.py`):
  - **`_NET_WM_STATE_BELOW` was the wrong mechanism, and it caused the reported bug.** The bond asked for it with `wmctrl -b add,below`. KWin reads that state as "below **every** normal window", not "below this one window", so the visualizer sank beneath the rest of the desktop. That is exactly what the two screenshots show: over an empty area the player blended with the visualizer, but over a maximised console the player blended with the *console*, because the visualizer was underneath the console.
  - The fix is to order the two windows **against each other**: `XConfigureWindow` with `CWSibling|CWStackMode` and `StackModeBelow`, using the player's own window as the sibling. The visualizer is now the player's immediate lower neighbour, so whatever is below the pair no longer matters. The stale hint is removed on bond, and the order is re-asserted every 0.5 s because the player is raised whenever it gains focus. Verified live: visualizer index 2, player index 3 — immediately adjacent — with `_NET_WM_STATE` free of `BELOW`.
  - `XLowerWindow` (my first attempt) had the same defect as the hint: it sends the window to the very bottom of the stack. It survives only as a fallback for when the player window cannot be identified.
  - **The transparency bar looked black.** The GTK overlay had no RGBA visual and no paintable window, so it painted an opaque theme background. It now asks for `set_app_paintable` plus the screen's RGBA visual, and clears the `window`/`drawingarea` CSS background, so only the bar is drawn. The bar itself is a frosted glass trough — the theme hue lightened toward white at low alpha, with a translucent accent fill and a bright glass knob. It now reads within **1–3 RGB units** of the surrounding pane instead of a black strip.
  - **The companion could be skipped at launch.** The launcher's `pgrep -f "MP_Liquid_Visualizer|mp_liquid_visualizer_vulkan"` matched any shell whose command line merely *mentioned* those names, including the launcher's own wrapper. It then believed a visualizer was already running and started none. The check now matches only an interpreter actually running the script.

- **The MP Liquid Player keeps its themed glass, an animated visualizer behind it, and the transparency bar on the Vulkan backend** (`scripts/mp_liquid_visualizer_vulkan.py`, `scripts/liquid_glass_vulkan.py`, `scripts/liquid_glass_slider.py`, `MP_Audio_Player_Liquid_Glass.py`, `CHANGELOG.md`):
  - **Three separate faults, all on the Vulkan path** (the backend this desktop is configured for), which is why the OpenGL path looked fine:
    - **The window was fully transparent.** `LiquidGlassRenderer.draw()` painted the visualizer *inside* the player window and returned, skipping the themed glass shader entirely. The bonded companion window already supplies that picture, so the player became a see-through frame over the raw scene with no theme colour on it. On Linux the host now always draws its own glass pane, exactly as the 0.4 release did — `v0.4`'s `draw()` has no in-window visualizer branch at all. Platforms without a companion window keep the in-window path.
    - **The visualizer never moved.** The standalone Vulkan renderer never called `march_advance()`, so `accum_time` stayed at `0.000` whenever no audio was playing and the scene was a still image. The OpenGL in-player layer already had this call; the standalone Vulkan renderer now advances the clock too, using a real frame delta. Measured: `accum_time 0.000 → 0.000` before, `0.009 → 0.931` after, with 70% of pixels changing over two seconds.
    - **The transparency bar was missing.** Only the OpenGL host ever drew one, inside its GL frame. The Vulkan host had no slider code at all — `grep slider MP_Audio_Player_Liquid_Glass.py` returned nothing, and the same is true of the 0.4 release, so the bar disappeared the moment Vulkan became the default. Added `scripts/liquid_glass_slider.py`, a borderless GTK overlay pinned to the 44 px strip the terminal well reserves at the bottom. It draws the same track and thumb, follows the active theme colour, and reports drag input, so both backends share one implementation.
  - Verified live with the desktop shortcut on Vulkan: player mean RGB **(150, 175, 201)** with **0% near-black** (themed glass, not a black or transparent window), the visualizer changing **46% of pixels** over two seconds, and the bar drawn as a cyan track with a pink thumb in the bottom strip. On OpenGL the native GL slider was confirmed at rows **20–27 px above the bottom edge**.
  - `_NET_WM_WINDOW_OPACITY` stays at the documented 70% clear-glass value, matching 0.4.

- **The Web Companion dashboard no longer flickers with a permanent "has been restarted" banner on a phone** (`scripts/mix_web_companion.py`, `tests/test_web_companion_autoplay.py`, `tests/test_web_audio_tray.py`):
  - **Root cause: the queue served WAV files that contain no audio.** The archive held four 44-byte `RIFF/WAVE` files — a valid header whose `data` chunk declares zero frames, left behind by interrupted recordings. The only gate was `st_size <= 0`, so they passed as "streamable". The phone pointed its `<audio>` element at one, the browser raised a media error, and the dashboard read that as "the companion restarted", re-attached, and errored again. The result was the reported symptom exactly: a constantly flickering screen with `MP Audio Web Companion has been restarted` stuck in the top bar.
  - `is_streamable_file()` now gates the queue on real playability, not just size: a regular file, more than `MIN_STREAMABLE_BYTES` (32 — safely under the 43-byte smallest measured playable file, so it can never reject real audio), and a precise "valid header but no audio" check for WAV and FLAC. The archive scan, both playlist readers (JSON and m3u) and `/api/stream` all use it. Other containers are never header-probed, so a 51k-track music library pays no extra I/O.
  - The size floor is deliberately a single low constant rather than a per-format table. A per-format table was measured and rejected: the minimums span 43 bytes (AAC) to 8 KB (FLAC), and a table tight enough to catch stubs silently deleted valid short files in testing. Rejecting real music is the worse error, because it is invisible and unrecoverable; a stub that slips through now produces a clear message instead of a loop. The WAV/FLAC header checks are what actually fix the bug, and FLAC shares WAV's failure class (a `fLaC` marker plus STREAMINFO and no frames), so its metadata blocks are walked to confirm audio follows.
  - `/api/stream` returns a JSON **404** for such a file instead of a 200 carrying the bare header. A 404 is unambiguous; the old empty 200 body was what the browser turned into an endless media error.
  - **The client loop was unbounded.** `reattachToRestartedServer()` cleared the one-shot `mediaRecoveryUsed` guard from inside the recovery path, so recovery could repeat forever. It is now capped at `MEDIA_RECOVERY_LIMIT` (2) attempts per file. A good play clears the budget; a different track resets it. When the cap is reached the page stops retrying and says `This mix cannot be played on this device … It is empty or damaged on the computer`, instead of pretending the service restarted.
  - The `ended` handler had the same shape: `restartStreamFromTop()` restarted a zero-length file on every `ended`, so a stub looped there too. It is now bounded to two quick restarts per file, after which the queue advances normally.
  - The `play()` success path only clears the recovery budget when the element reports a duration. A header-only file resolves `play()` and then ends immediately, so clearing unconditionally would have re-armed the loop.
  - `current_stream_file()` resets the elapsed clock when the saved path has dropped out of the queue (renamed, deleted, or filtered as unplayable). Previously it kept the old file's position and resumed the replacement mix mid-way or at its final frame, which looks like a dead player.

- **Every Liquid Glass theme renders again, with readable terminal ink, and the transparency slider is back** (`scripts/liquid_glass_opengl.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `tests/test_liquid_glass_opengl.py`):
  - **Black theme windows.** `scripts/liquid_glass_vulkan.py` imported `preset_by_id` from `mp_liquid_visualizer_vulkan`, where it does not exist. The in-player visualizer layer raised on every frame, the host logged "MP Visualizer background unavailable (Vulkan)", and the pane fell back to a black fill — so every theme looked black while the visualizer was on. `preset_by_id` lives in `liquid_player_visualizer`; the import now comes from there. Measured on the reported window: mean RGB **(27, 33, 33)** with 53% near-black before, **(126, 141, 170)** with 0% after.
  - **The transparency slider was drawn off-screen.** `_draw_transparency_slider` computed its NDC y as `2*(y/height) - 1`, which maps a distance-from-bottom to the *top* of the window, where the bezel clipped it. NDC y grows upward, so it is `1 - 2*(y/height)`. The bar now sits in the reserved strip at the bottom (verified at rows 23–30 from the bottom).
  - **The slider was also covered by the terminal.** `place_terminal()` discarded its `x, y, width, height` arguments on X11 (`del x, y, width, height`), so the 44 px the well reserves at the bottom was never applied. VTE sized itself to its own row grid (1040 px where the well asked for 996) and painted over the bar. The terminal is now moved and resized with `XMoveResizeWindow` to the well rectangle, which restores the reserved strip; the slider also measures the terminal's real bottom edge so it stays clear even if a toolkit overrides the size again.
  - **Terminal ink is readable on both panes.** `glass_foreground_rgb()` returned white for every theme, so text vanished on the pale water pane. It now follows `theme_uses_dark_glass()`: dark ink on the pale pane, light ink on the charcoal pane (Retro Linux Memories, and Into Darkness (Void Out), which is pure black). This is the contract the existing glass-ink tests describe; the test that claimed only Retro Linux is a dark pane now lists both.
  - **MP Gui shows the same theme.** Its visualizer was spawned without the companion flag and its geometry was never published, so nothing sat behind it. It now launches the locked companion and publishes its own rectangle.

- **The companion MP Visualizer is bonded behind the MP Liquid Player again, permanently, on both OpenGL and Vulkan** (`scripts/visualizer_player_bond.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/liquid_player_visualizer.py`, `scripts/launch_liquid_glass.sh`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `tests/test_visualizer_player_bond.py`):
  - `PlayerBondMixin` — the shared bond implementation — existed and had tests, but **neither renderer inherited it**. `LiquidVisualizerVulkanRenderer` still carried an older inline copy while `LiquidVisualizerOpenGLRenderer` had no bond code at all, so the OpenGL path (the Linux default) opened as a free-floating square and only the Vulkan backend ever bonded. Both classes now inherit the mixin and the duplicated inline copy is gone, so one implementation serves both backends.
  - The OpenGL renderer was missing the interface the mixin requires: `_windowed_size`, `_set_windowed_size()`, `_user_selected_window_mode`, and a `poll()` that calls `_sync_player_bond()`. It used a private `_size` instead. Added, and `_size`/`_windowed_size` are kept in step everywhere the window is resized or re-placed.
  - **The companion is now locked.** The window the player launches carries `MP_VISUALIZER_COMPANION=1`, which sets `_bond_locked`. A locked companion ignores the Z cycle and double-click fullscreen, refuses to drag off the player, and keeps the player's size limits pinned, so it cannot be detached or resized away. It also bonds even when the state file has not caught up, and opens directly on the player's rectangle instead of flashing in the corner first.
  - **Later windows stay free.** Z (and the Manager) spawn with `MP_VISUALIZER_DETACHED=1`, so an extra visualizer opened afterwards is explicitly detached and never latches onto the player. Previously the only signal was "is a companion active", which would have bonded every window.
  - `mark_player_visualizer_attached()` was missing although its tests referenced it. Restored: it writes `active: true` *before* the child process starts, so the visualizer's very first poll already sees the bond. Both Python hosts and the shell launcher now call it on the companion path.
  - `_visualizer_env()` also strips `MP_LIQUID_GLASS`/`MP_LIQUID_GLASS_BACKEND`, which marked the child as another player host.
  - Verified live on this desktop (Vulkan config and forced OpenGL): the visualizer rectangle matches the player exactly in Normal (1574x1054), Mini (976x378) and Micro (976x196), and the visualizer sits below the player in the stack.

- **The Web Companion gets its macOS privacy grant on the app, not on `python3`** (`scripts/mix_web_companion.sh`, `tests/test_web_companion_macos_service.py`):
  - Ported the 0.4.3 macOS bundle work into the 0.5 wrapper. The bundle entry point is now a native launcher (compiled by `./scripts/mix_web_companion.sh install`) that **embeds libpython** via `Py_BytesMain` instead of `exec`-ing the interpreter. A `#!/bin/bash … exec python3` shim is replaced by `exec`, and macOS CPython is itself `Python.app`, so either way the archive read was attributed to the interpreter and a grant recorded against **MP Web Companion.app** never applied. The bundle now stays the responsible process for the whole session, so one grant is enough.
  - The launcher `chdir`s to the install tree and reopens `mp-web-companion.log` itself, because LaunchServices starts an app with `/` as its working directory and ignores launchd's `StandardOutPath`.
  - The launchd job runs `/usr/bin/open -g -W <bundle>` rather than executing the binary, so the app has a LaunchServices record and actually appears in **Privacy & Security**. `KeepAlive` is unconditional, since `open -W`'s exit status says nothing about the app.
  - `get_pids()` matches both `mix_web_companion.py` and the bundle executable; with an embedded interpreter the command line shows only the bundle name, so `stop`/`restart` used to leave the old server holding port 8888.
  - `Info.plist` declares `NSRemovableVolumesUsageDescription` and `NSNetworkVolumesUsageDescription` (needed for the prompt on a USB archive such as `/Volumes/DATAMAC3`).
  - `status` now asks the running service (`/api/status` → `library_detail`) whether it can read the archive, instead of calling `os.listdir()`. Terminal usually holds its own Full Disk Access grant, so the local probe could report green while the launchd job was still blocked.
  - `grant` copies the bundle path to the clipboard, reveals it in Finder, and explains the `+` / `Cmd+Shift+G` / `Cmd+V` steps, because macOS never prompts a background job for removable-volume access.
  - **The bundle now lives in `~/Applications/MP Web Companion.app`**, beside `MP Gui.app` and `MP Liquid Player.app`, instead of inside the install tree. The System Settings **+** picker only accepts `.app` bundles — a `.sh` or `.py` row is greyed out no matter what — so a bundle buried in `MPMixManager_v0.5-x64/` was hard to find and easy to mistake for one of the unselectable scripts around it.
  - The bundle is now a fully-formed app: `CFBundleVersion`, `CFBundleInfoDictionaryVersion`, `LSMinimumSystemVersion`, and a real `.icns` built from `assets/mp_liquid_glass_icon.png`. It is re-registered with `lsregister -f` after every `install`, so it appears in the privacy list immediately rather than after the next login. A bundle left behind by 0.4.x/0.5.0 in the install tree is removed (with its LaunchServices record) so macOS only ever resolves one copy per bundle id.
  - `uninstall` removes both the new and legacy bundle locations together with their LaunchServices records.

- **The installer runs on macOS again** (`scripts/install_desktop_shortcuts.py`, `MP_Audio_Player.py`, `send_promo_email.py`):
  - Desktop shortcuts failed with `TypeError: write_text() got an unexpected keyword argument 'newline'`. `Path.write_text(newline=...)` only exists from Python 3.10, and macOS still ships 3.9 as `/usr/bin/python3`. Writing now goes through `open(..., newline=...)`.
  - `MP_Audio_Player.py` and `send_promo_email.py` used a backslash inside an f-string expression, which Python 3.9 rejects outright. Both now build the string with concatenation, so the player and the promo mailer import on 3.9 as well as 3.12+.

- **MP Liquid Player, MP Gui and MP Liquid Visualizer now open centred on the primary screen** (`scripts/desktop_launch_screen.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `scripts/liquid_player_visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `tests/test_desktop_launch_screen.py`):
  - Every MP window was placed at a fixed `(72, 64)` offset from the primary monitor's top-left. A finished Liquid Player is 1574×1098 inside a 1920×1200 monitor, so the window ran past the right and bottom edges and read as "opens in the top-right corner, not centred".
  - Added `main_window_origin()` and `centered_window_origin()`, which centre a window of a known size on the primary desktop. `main_desktop_rect()` returns that area in **X11 pixels**, the unit GLFW and `wmctrl` actually use, and subtracts the bottom panel so a centred window is not hidden behind it.
  - The Liquid Glass hosts call it after the window has been refitted to its real terminal cell size, which is when the final size is known. The standalone visualizer uses it for its opening offset as well.
  - `x11_coordinate_scale()` still reads the Xwayland scale from matched output origins. The centring maths converts through it, so a 1.45-scaled layout lands on the same logical point as a 1.0 layout.
  - `place_on_main_screen()` and `ensure_on_main_screen()` accept the window size and re-centre a window they have to move, instead of dropping it back in the corner.
  - `tests/test_desktop_launch_screen.py` covers the centred point, containment inside the monitor, the offset round-trip, and a scaled 1.45 layout.

- **The MP Liquid Visualizer shortcut opens a window again** (`scripts/liquid_player_visualizer.py`, `tests/test_mp_liquid_visualizer.py`):
  - The standalone OpenGL visualizer passed `GLFW_VISIBLE` and `GLFW_DECORATED` transposed: it created the window with `VISIBLE = 0` and `DECORATED = 1`, so the process started, initialised OpenGL, printed its usual banner and never showed anything. This is the same transposition the CHANGELOG already records for the Windows path.
  - The hints are now named constants (`GLFW_VISIBLE`, `GLFW_DECORATED`, `GLFW_CLIENT_API`, …) declared in the module, so the two cannot be swapped again.
  - The visualizer also pins GLFW to X11 and PyOpenGL to GLX on Linux. Without the platform hint GLFW opened a Wayland surface that has no X11 handle, so `xwininfo`/`wmctrl` could neither see nor position it. Without `PYOPENGL_PLATFORM=glx` PyOpenGL looked for an EGL context that the GLX surface does not have and aborted with "Attempt to retrieve context when no valid context".

- **The MP Liquid Visualizer is no longer greyed out, can be dragged, and switches presets with 1–9** (`scripts/liquid_player_visualizer.py`, `tests/test_liquid_player_visualizer_mode.py`):
  - The standalone window reused the in-player drawing layer with its terminal-text guard permanently on. That guard exists so white terminal characters stay readable *when the scene is drawn behind the player's text* — it desaturates and flattens the whole image. A standalone window has no text over it, so the only effect was a grey wash over every preset. Measured on the reported build: mean luma **60.6** with the guard, **139.1** without.
  - `PlayerVisualizerLayer.draw_with_state()` now takes `text_guard`, threaded through `_draw()` to `_draw_scene()`. It defaults to on, so the in-player overlay is byte-for-byte unchanged, and the standalone renderer passes `text_guard=False`.
  - Left-drag did nothing because the OpenGL renderer never implemented dragging: it had no drag state and ignored cursor motion, so the borderless window could not be moved at all. It now tracks the press origin and moves the window from the root pointer, matching the Vulkan renderer. Double-click still toggles fullscreen, and a fullscreen window correctly refuses to drag.
  - Number keys 1–9 were already wired to `presets_by_slot()`, but the window could not take keyboard focus reliably and drag was equally dead; both came from the same missing input plumbing. Verified every slot 1–9 selects its preset and that the rendered picture differs by ~87–91% between slots.
  - `_x11` setup gains an opt-in `MP_VISUALIZER_DEBUG` trace. It caught a real ordering bug during this work: the block called `ctypes.CDLL` although the module only imported selected names from `ctypes`, so the X11 handle was silently unavailable and `root_pointer()` always returned `(0, 0)`.

- **MP Gui and MP Liquid Player now share one theme and one navigation-bar colour** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_theme_sync_across_windows.py`):
  - Pressing `C` in either window only recoloured the window that received the key. The other kept its previous palette until it was restarted, so MP Gui and the player disagreed after every press.
  - Both windows now poll `~/.config/mix-manager/theme` while they are open: the manager on each menu tick, the player once a second from its frame loop. A press in one window reaches the other within about a second, in both directions.
  - The manager's `C` cycle was two entries shorter than the player's (it was missing `into_darkness_void_out`), so the two keys drifted apart after Sensorium. Both keys now walk `EqualizerVisualizer.THEMES` in the same order, and a test asserts the lists are identical.
  - The selected row in MP Gui was shaded from a per-row variant of the theme accent, while the player filled its selected playlist row with `_THEME_PALETTES[theme]["highlight"]`. The manager's navigation bar now publishes that same RGB (`SEL_BAR_*`) and uses the player's luminance rule for the text, so the bar is the identical colour in both windows on every theme.
  - `into_darkness_void_out` was missing from `set_theme_colors`, so the manager had no palette for it. Added its console colours, menu accent, pane, and navigation-bar fill.
  - The theme watcher checksums the file rather than trusting mtime: `dreamworlds_ultra` and `cosmic_supernova` are both 17 bytes, and two such writes in the same second shared a mtime and were missed.

- **MP Visualizer presets 5–9 no longer render the same scene** (`shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `shaders/presets/p0`–`p10.frag.spv`, `shaders/presets/manifest.json`, `scripts/mp_liquid_visualizer_vulkan.py`):
  - Preset ids 5, 6, 7 and 8 all matched one shared `u.preset.x > 4.5` branch in `getPresetPalette`, `mapScene` and `renderCosmicBackground`, so keyboard slots 6, 7, 8 and 9 drew the same geometry over the same background. Only the camera distance and a few lighting constants varied.
  - Each of those four ids now has its own branch. Slot 6 (`stream_of_frequency`) renders the resonant frequency core with five live FFT ribbons. Slot 7 (`beyond_dimensions`) renders the 4D hypersphere with tesseract portal rings. Slot 8 (`particle_accelerator`) renders the dual counter-rotating collider toroids with helical beams. Slot 9 (`cosmic_universe`) renders the pulsar core with constellation web filaments.
  - Added the `streamOfFrequencyPalette`, `beyondDimensionsPalette`, `particleAcceleratorPalette` and `cosmicUniversePalette` signature palettes, and scoped the legacy chrome/facet overrides to the Stream of Frequency preset so the newer scenes keep their own colours.
  - Shipped per-preset SPIR-V modules and `manifest.json` were rebuilt against the new source digest, so the installed tree uses the distinct scenes instead of falling back to the old shared program.

- **The Manager's "Stream of Frequency" visualizer entry resolves to the right preset again** (`scripts/mp_liquid_visualizer_vulkan.py`):
  - A 0.4.3 rename left preset id 5 registered as `infinite_complexity_stream037` / "Infinite Complexity". The `stream_of_frequency` key had no owner, so Option 6 in the preset menu and `--set-preset "Stream of Frequency"` both fell through to preset 1 (Cosmic Dreamworlds).
  - Preset 5 is registered as `stream_of_frequency` / "Stream of Frequency" again, keeps the `infinite_complexity_stream037`, `bifurcation`, `xi-n` and `stream 037` aliases, and stays on keyboard slot 6.

### Changed
- **Launchers and desktop shortcuts resolve the install tree instead of hard-coding a path** (`scripts/lib/mp_install_dir.sh`, `scripts/lib/mp_install_dir.py`, `bin/mp-desktop-exec`, `Mix_Archive_Manager.sh`, `scripts/launch_liquid_glass.sh`, `scripts/launch-liquid-glass`, `bin/mix-archive-manager`, `bin/launch-manager`, `bin/launch-manager-fullscreen`, `install.sh`, `manage_installation_config.sh`, `scripts/install_desktop_shortcuts.py`, `scripts/liquid_glass_reward.py`):
  - Nothing points at `/var/home/<user>/MP_Mix_Manager_v0.x` or any other version-numbered directory any more.
  - The install tree is found from `$MP_MIX_MANAGER_DIR`, then `~/.config/mix-manager/install_dir` (written by `install.sh` and by the migration menu), then the `~/.local/bin` launcher symlinks, then the newest matching tree under `$HOME` and `/opt`. Every candidate is verified with a marker file, so a stale symlink or a backup copy cannot win.
  - `Mix_Archive_Manager.sh` now follows its own symlink, so `manager` and `~/.local/bin/mix-archive-manager` report the tree they really point into.
  - Linux `.desktop` entries call `mp-desktop-exec` and use the icon name, so a moved or renamed tree keeps its shortcut and icon.
  - The Liquid Player launcher, MP Gui, the audio and WAN2GP output monitors, the alarm clock, and the KDE Connect helpers use the same resolver.

### Added
- **Path resolver library** (`scripts/lib/mp_install_dir.sh`, `scripts/lib/mp_install_dir.py`) with the same lookup order in shell and Python.
- **`mp-desktop-exec` helper** (`bin/mp-desktop-exec`) that launches a named script from the resolved tree, for desktop entries and shell aliases.
- **`tests/test_install_dir_resolution.py`** covering resolver lookup, moved and renamed trees, and the desktop entries.
- **MP Dev Utility builds arm64 and amd64 packages from one source tree** (`MP_Dev_Utility.sh` in `BASH_SCRIPTS`, `tests/test_package_dev_utility.py`):
  - New `--arch host|amd64|arm64|all` option and an interactive architecture prompt. Previously every format silently took its architecture from `uname -m`, so a packaging run on a PC could only ever produce PC packages.
  - `.deb`, `.rpm`, pacman `.pkg.tar.zst` and portable tarballs are now produced once per requested architecture, with the matching `Architecture:`/`arch =` metadata and file names (`mp-mix-manager_<version>_arm64.deb`, `..._aarch64.pkg.tar.zst`, `..._arm64_portable.tar.gz`).
  - No cross-compilation is involved: the payload is interpreted Bash and Python, and the only difference between the two packages is the recorded architecture. A native ELF scanner refuses to put an amd64 binary into an arm64 package unless `--allow-native-binaries` is passed.
  - Cross-architecture RPMs build inside an emulated container of the target architecture (needs binfmt/qemu); the Alpine `.apk` is `noarch` and covers every architecture from one file.
  - Formats that embed a native builder or base runtime are reported rather than faked: the AppImage and Snap are host-only (`--arch` lists them as skipped), and the Flatpak stays on the build host because `flatpak-builder` only builds for its own architecture.
  - Every run writes `BUILD-MANIFEST.txt` with the host, the requested architectures, the formats, and the exact artifacts produced; each package also carries `architecture.txt`/`ARCHITECTURE.txt`.

## [0.5.0 Stable (Sensorium)] - 2026-11-12

### Fixed
- **Desktop shortcuts now open when clicked** (`scripts/install_desktop_shortcuts.py`, `MP_Liquid.desktop`, `Mix_Archive_Manager.desktop`, `desktop/*.desktop`, `tests/test_desktop_shortcuts.py`):
  - The `Exec=` line wrote `\"` and `\$` for its quoted arguments. Neither is a valid escape in a Desktop Entry `Exec` value, so GLib refused to load the entry. `desktop-file-validate` accepted the file, which is why the breakage was not caught, but clicking any of the four icons did nothing.
  - An `Exec` value is unescaped twice, once as a key-file string and once as an argument list. Shell metacharacters now carry two backslashes so `$HOME` still expands at launch.
  - `TryExec=mp-desktop-exec` was a bare name, but a desktop session's `PATH` is only `/usr/local/sbin:/usr/local/bin:/usr/bin` and does not contain `~/.local/bin`. A failed `TryExec` also makes GLib treat the entry as unloadable, so the key now points at the published helper by absolute path. The shipped `.desktop` files omit `TryExec` instead of baking a user name in.
  - `desktop_shortcuts_present` now checks that each shortcut has a loadable `Exec` and a resolvable `TryExec`, so a broken shortcut from an earlier run is repaired on the next launch instead of being counted as present.
  - `tests/test_desktop_shortcuts.py` covers both failures, so a shortcut that validates but cannot load now fails the suite.
- **Web Companion no longer loops the same fragment of a mix** (`scripts/mix_web_companion.py`, `tests/test_web_companion_stream.py`):
  - `/api/stream` now honours the open-ended range form `bytes=start-` (and `bytes=-suffix`). Previously only `bytes=start-end` matched, so a phone scrubbing a track was handed a body from offset 0 and replayed the same opening seconds.
  - The 100 ms `/api/eq` poll no longer seeks the phone's `<audio>` element. It used to force `currentTime` back to the player's `position_sec` even while that position was frozen, which trapped playback in a repeating loop of the same few seconds.
  - Progress, position, and duration now come from the phone's own audio clock while streaming, so the bar tracks real playback.
  - A phone that disconnects mid-transfer no longer produces `BrokenPipeError` tracebacks; the connection is simply closed.
  - Zero-byte files (interrupted conversions) are excluded from the stream queue, since they would stall the client on an empty body.
  - Fixed a duration-cache bug that returned the cached file size instead of the cached duration.

### Added
- **MP Assistant tab in the Web Companion** (`scripts/mix_web_companion.py`, `tests/test_web_companion_assistant.py`):
  - The page header now has two tabs: **MP Liquid Player** and **MP Assistant**. Switching tabs only hides a panel, so a mix keeps streaming while the assistant is open.
  - The assistant tab is a live prompt for the **MP Harmony Agent**, streaming replies token by token into the transcript.
  - Replies are spoken through the agent's TTS proxy (`GET /api/assistant/speech`), defaulting to **Stephen Hawking** (`stephen_hawking_matrix`), with a voice selector for the other Matrix voices.
  - The mix is paused while a spoken reply plays and resumes when the clip ends. Unticking **🔊 Speak replies** keeps it text-only.
  - **Fixed: replies arrived as text only on phones.** Mobile browsers only allow audio inside a user gesture, and the model takes ~10s to answer, so the tap that sent the prompt had expired before `play()` ran and the browser rejected it with `NotAllowedError`. The reply element is now primed with a silent clip on every tap in the assistant tab (and on send), which authorises the real reply to start later. If a browser still refuses, a **▶ Speak reply** button is attached to that bubble and replays the answer inside the tap. Verified against Chrome's strict `user-gesture-required` autoplay policy.
  - The reply `<audio>` element is no longer `display: none`, which iOS Safari can treat as unplayable; it is laid out off-screen instead.
  - A **🔊 Speaking…** badge shows while a reply is playing, and stale speech is cancelled when the voice is muted or a new chat is started.
  - Because the agent binds `127.0.0.1:11435`, the companion proxies its OpenAI-compatible API (`GET /api/assistant`, `POST /api/assistant/chat`) so a phone only talks to port 8888.
  - Each browser gets an isolated agent session that survives reloads; **↺ New chat** clears it.
  - The selected tab is remembered in `localStorage` and is deep-linkable (`#assistant`).
  - New settings: `MP_ASSISTANT_ENABLED`, `MP_ASSISTANT_URL`, `MP_ASSISTANT_TIMEOUT`.
- **Web Companion theme switching** (`scripts/mix_web_companion.py`, `tests/test_web_companion_autoplay.py`):
  - The page header has a theme dropdown listing every palette the manager offers.
  - Choosing a theme applies it immediately and persists it to `~/.config/mix-manager/web_companion_theme`, which takes precedence over the manager theme for the companion only.
  - New endpoints: `GET /api/themes` (available palettes and the active one) and `GET /api/set_theme?theme=<id|name|alias>` (validates and applies).
  - Deleting `web_companion_theme` makes the companion follow the manager theme again.
- **Web Companion mini playlist** (`scripts/mix_web_companion.py`, `tests/test_web_companion_autoplay.py`):
  - Shows exactly three mixes: the current one plus the next two, in the active theme.
  - Tapping an entry plays that mix (`POST /api/control?action=select_index&index=N`).
  - New endpoint `GET /api/playlist` returns the three-track window plus the total queue size.

### Changed
- **Version bump to `0.5.0 Stable (Sensorium)`.** `VERSION` now reads `0.5.0 Stable (Sensorium)`. Current-release fallbacks, headers, badges, installers, launcher resources, and desktop entries that named `0.4.0`, `0.4.1`, or `0.4.2 Stable (Liquid)` now name `0.5.0 Stable (Sensorium)`. The macOS `CFBundleShortVersionString`/`CFBundleVersion` values are `0.5.0`, and the Windows launcher resource is `0,5,0,0`.
- **First-run welcome** (`scripts/liquid_glass_reward.py`) greets `0.5.0 (Sensorium) - [Stable Release]` and lists the features added since `0.4.2`.
- **Repository paths** in `README.md`, `INSTALL_BAZZITE.md`, and `scripts/launch-liquid-glass` point at `MPMixManager_v0.5-x64`. The 0.4 path stays as a fallback in the launcher.
- **Web Companion is now a pure HTTP streamer** (`scripts/mix_web_companion.py`):  - Removed all local-player code. The service no longer spawns `MP_Audio_Player.py --daemon`, sends IPC commands to it, or opens a sound device. This was the cause of audio starting on the host machine when the companion restarted.
  - The companion keeps its own stream queue and position state in `~/.config/mix-manager/web_companion_stream.json`, and the browser reports track changes via `action=track_ended`.
  - Removed settings `WEB_COMPANION_AUTOPLAY`, `WEB_COMPANION_MODE`, `WEB_COMPANION_PLAYLIST` and flags `--no-autoplay`, `--autoplay`, `--autoplay-status` in favour of `--stream-status`.
  - `/api/status` reports `player: "Web Stream"` and includes `queue_len`/`queue_position`.

- **Web Companion resource and connection hardening** (`scripts/mix_web_companion.py`, `tests/test_web_companion_autoplay.py`):
  - The stream queue is cached for 30 seconds instead of being re-walked on every request. The dashboard polls several endpoints per second, and uncached scans pushed the service to a 1.4 GB peak; it now idles around 30 MB.
  - The listen backlog is raised from the stdlib default of 5 to 128, so a burst of dashboard polls and parallel audio range requests is no longer dropped.
  - Client disconnects are no longer written to the journal as server faults. Phones close tabs, scrub, and time out mid-request constantly, and each one previously produced a `BrokenPipeError` traceback.
### Added
- **Hidden Mode — press H to hide every MP window, press H again to bring them back** (`scripts/mp_hidden_mode.py`, `MP_Audio_Player.py`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/liquid_player_visualizer.py`, `scripts/mp_system_tray.py`, `Mix_Archive_Manager.sh`, `tests/test_hidden_mode.py`):
  - With any of the four desktop shortcuts focused, **`H`** hides **every** MP window — MP Liquid Player, MP Mix Manager, MP Gui, MP Liquid Visualizer and the tracklist viewer — and the shared tray icon posts **"MP Mix Manager is now operating in Hidden Mode. Press H again to unhide every MP window."** Pressing **`H`** again restores all of them, in place, and re-focuses the window that was in front. Other applications are never touched.
  - **The unhide key is owned by the compositor, not the application.** On Plasma Wayland the four GTK hosts are native Wayland clients, so `wmctrl` and `xprop` never see them, and KWin moves focus away from a window as soon as it is minimized or made transparent. A focused app therefore cannot receive the `H` that is supposed to undo the hide. `scripts/mp_hidden_mode.py` loads a short-lived KWin script that minimizes the MP windows *and* registers a global `H` to un-minimize them; the script unloads itself after restoring, so `H` returns to being an ordinary character key.
  - Verified on KWin 6.7.5 (Plasma 6, Wayland): eight live MP windows (manager, player, four visualizers) hide together and return together.
  - Hidden Mode is available from all four shortcuts: the console manager's menu key, the player's key handler, both Liquid Glass hosts (which also cover MP Gui and Manager mode), and both visualizer backends (OpenGL and Vulkan).
  - A **Favorites** note: `H` now belongs to Hidden Mode, so marking a favourite moved to **`Shift+F`**. Lowercase **`f`** still opens the selected file in the file manager, and the equalizer band cursor uses the arrow keys so it no longer competes for `h`.
- **The Whitest White — an all-white manager theme** (`Mix_Archive_Manager.sh`, `scripts/view_tracklist_console.sh`, `themes/The_Whitest_White_MP_Mix_Manager.theme`, `tests/test_whitest_white_theme.py`):
  - A pure-white pane with pure-black text for the **Mix Archive Manager**: the console background is pinned to `#ffffff` with OSC 11 (BEL and ST forms, so Windows Terminal, VTE/Konsole/xterm, iTerm and Terminal.app all take it), the console foreground is pinned to `#000000` with OSC 10, and the classic Windows console pins palette slot 0 plus the default text attribute. Restoring hands both colours back to the user's profile.
  - Every palette hue the manager uses — headings, section rules, menu numbers, descriptions, search/status lines, prompt feedback and the interactive Bash shell prompt — is set to `[38;2;0;0;0m`, and `DIM` is dropped so nothing is rendered as grey.
  - The selected row is the one thing a white-on-white theme cannot colour, so it stays a **pure white fill** with bold black text and a black `▶` pointer instead of the themed bar.
  - Btop gets a matching palette (`themes/The_Whitest_White_MP_Mix_Manager.theme`) — white field, black outlines, meters, graphs and text — synced by `_sync_btop_theme`.
  - Available as **Theme option 23** in *Themes & Color Palette Switcher*, and in the `C`-key cycle right after Retro Linux Memories. Aliases: `the_whitest_white`, `whitest_white`, `whitest`, `white`, "The Whitest White".
  - The tracklist viewer knows the theme as well. The MP Liquid Visualizer presets are deliberately untouched.
- **MP Assistant tab in the Web Companion** (`scripts/mix_web_companion.py`, `tests/test_web_companion_assistant.py`):
  - A **tab bar at the top of the dashboard** switches between **MP Liquid Player** and **MP Assistant**. Tapping **MP Assistant** drops you into a Harmony prompt; tapping **MP Liquid Player** takes you back at any time.
  - Switching tabs only hides a panel, so an engaged mix **keeps playing** underneath and returning to the player never costs another autoplay tap. The chosen tab is remembered across reloads.
  - The companion proxies the agent's OpenAI-compatible stream through `/api/assistant/chat`, so a phone on the LAN can chat with the loopback-only agent. Tokens render as they arrive, and **Stop** sends the agent's own barge-in abort.
  - Replies are **spoken on the phone**, not the host: `/api/assistant/speech` synthesizes the WAV through the agent's `/v1/audio/speech` and falls back to a local `espeak-ng` Hawking profile if the agent is down. One shared, `playsinline` `<audio>` element unlocked inside the Send tap keeps iOS playback working.
  - The default voice is **Stephen Hawking** (`stephen_hawking_matrix`) unless changed with **`/voice`**. `/voice` lists all eleven Matrix/Hawking presets, `/voice hawking` restores the default, `/voice speed 1.15` adjusts the rate, and aliases such as `smith`, `neo` and `trinity` resolve to real ids. The choice persists per browser and is mirrored into `~/.harmony_voice` so the CLI agrees.
  - The stream gate overlay now belongs to the player tab only, so the new tab bar stays reachable before the first audio gesture, and a **Start Agent** button launches `manage_harmony_agent.sh start` when the agent is offline.
- **Import Traktor playlists into the MP Audio Player playlist** (`scripts/traktor_playlist_importer.py`, `scripts/traktor_playlist_importer.sh`, `scripts/music_folders_manager.py`, `Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_traktor_playlist_import.py`, `tests/test_traktor_player_shortcut.py`):
  - Reads **every playlist in `collection.nml`**, including playlists nested inside Traktor folders, and pre-generates them all as `TRAKTOR_<name>.json` + `.m3u8` in `PLAYLISTS_GENERATED`. A playlist must be generated before the player can open it; `TRAKTOR_PLAYLISTS.json` is the catalog the player reads.
  - **Music Folders are a hard gate.** The importer refuses to run when no local drive or SMB share is mapped, and offers to run the existing Music Folders scan first so there is an index to match against.
  - **Traktor paths are resolved, not trusted.** A LOCATION record is matched to the scanned library by folder suffix then file name, so a library that moved to another drive or mount point still resolves after that folder is added as a Music Folder. The menu explains this and warns when Traktor's volume/path must be recreated.
  - **Every path is validated before it is written or played.** Missing files are counted and skipped, a playlist with no valid files is not generated, and loading a playlist (menu or player) re-checks each file, offering to open only the available ones.
  - Entry points: Manager > **Custom Playlists & Traktor History Suite > 7**, Music Folders menu > **9**, `--import-traktor`, or `python3 scripts/traktor_playlist_importer.py` (`--generate-all`, `--generate NAME`, `--list`, `--list-traktor`).
  - **MP Audio Player CLI shortcut: `Shift+T`.** It opens a Traktor playlist menu over the player (generated lists only, arrows + Enter, `G` pre-generates all, `I` opens the full import menu). Audio keeps playing while an external menu is open, and lowercase `t` still opens the selected tracklist. Liquid Glass reports that the feature is CLI-only.
  - **All playback paths are validated.** `AudioEngine.load_track` now refuses a missing or empty file, and SMB paths get one mount attempt, so a broken playlist entry can no longer be loaded as a silent track.
- **21 audio output formats in the Mix Conversion suite** (`scripts/audio_conversion_suite.py`, `Make_SOF_FLAC_CONVERSION.sh`, `Mix_Archive_Manager.sh`, `tests/test_audio_conversion_suite.py`, `tests/test_conversion_menu_and_output_rule.py`):
  - The converter now writes **FLAC, WAV, Wave64, AIFF, CAF, AU, MP3, OGG, Opus, M4A/AAC, ALAC, WMA, WavPack, TTA, Matroska-FLAC, AC-3, E-AC-3, DTS, MP2, VOC and AMR**, every one at its high-quality preset (FLAC `-compression_level 12`, MP3 320k CBR, Vorbis Q10, Opus 256k, AAC 320k, WavPack/TTA level 8, AC-3/E-AC-3 640k, DTS 1536k).
  - A single format registry drives the picker, the manager menu, the batch mode and the statistics, so the menu can never offer a format the encoder cannot write. Formats with a decoder but no encoder (APE, TAK, Musepack, DSD) are refused with the reason instead of failing mid-batch.
  - New **Convert EVERY Format at Once** batch mode reads every source type and writes one `*_CONVERTED_OUTPUTS` folder per format. Same-named `.wav`/`.flac`/`.mp3` are treated as alternative sources, not split parts, and converted output folders are never re-ingested.
  - Lossy sources keep their existing quality; converting `.mp3` to FLAC reports that it cannot restore anything rather than silently implying an upgrade.
- **Output folders are created only when a run completes** (`Make_SOF_FLAC_CONVERSION.sh`):
  - Conversions are staged in a temp area and published at the end. A cancelled, interrupted or failed batch leaves **no** `AUDIO_FORMAT_OUTPUT` folder behind, so the archive never looks converted when it is not. A partial failure prints where the staged files are instead of publishing them.
- **Previous-conversion statistics table** (`audio_conversion_suite.read_conversion_stats`, `render_stats_table`):
  - Before converting, the wizard prints a themed table of every format converted before, with per-format file counts, total size, share bars and a **MASTER TOTAL** row. Reading the JSONL log is tolerant of partial lines.
- **Converted-folder browser** (`manage_converted_audio_folders` in `Mix_Archive_Manager.sh`):
  - New **Open Converted Audio Folders** page lists every output folder that already holds audio, with its format, file count and size, and opens the chosen one in the file manager.
- **Metadata: presets, custom fields, covers and disc sets** (`Make_SOF_FLAC_CONVERSION.sh`, `scripts/audio_conversion_suite.py`):
  - Ask for title/track per file, or press **s** at any prompt to use the current values for the rest of the batch.
  - Save and load **named metadata presets**, and register **custom metadata fields** (e.g. `MOOD`, `LABEL`, `CATALOG`) that are written alongside the standard tags.
  - **Front cover, back cover and disc image** paths are embedded as separate attached pictures where the container supports them, and the chosen format warns when it cannot carry artwork instead of dropping it silently.
  - **Disc-set metadata**: `1/2`, `2/2` and similar are written as the `disc` tag; a plain number sets `disc` alone.
- **Advanced conversion progress**: a live bar with position, ETA, speed and predicted size, plus batch ETA derived from the work already finished.
- The manager header now shows **Audio Output Formats Available**, and option **4** lists every format, the batch mode, the folder browser, the statistics and the format table.

### Fixed
- **Traktor S4 MIDI daemon crashed on FreeBSD at every start** (`scripts/traktor_s4_midi.py`, `tests/test_traktor_s4_midi.py`):
  - FreeBSD has no ALSA: there is no `amidi` binary and no `/dev/snd/midi*` tree, only `umidi(4)` character devices (`/dev/umidi*`, `/dev/midi*`). The daemon always fell through to `run_amidi_stream()`, which unconditionally spawned `amidi -p hw:0,0 --dump`, so every manager launch wrote a `FileNotFoundError: [Errno 2] ... 'amidi'` traceback into `~/.config/mix-manager/traktor_s4_midi.log` and the MIDI service died instantly.
  - `run_amidi_stream()` now detects a missing `amidi` and holds in a clean **standby** loop instead of raising, recording `status: "standby"` plus the reason in the state file. Hardware detection skips `amidi -l` when the binary is absent and scans the FreeBSD `umidi(4)` devices instead; `--status` prints a **MIDI Port** row pointing at `/dev/umidi*` rather than claiming an ALSA port.
  - Verified on FreeBSD 15.1-RELEASE-p6: `--start` reports "Active & Running" in standby with no traceback, and `tests/test_traktor_s4_midi.py` passes 17/17 on both FreeBSD and Linux.
- **MP Visualizer was very dark when launched from its desktop shortcut** (`scripts/liquid_player_visualizer.py`, `tests/test_liquid_player_visualizer_mode.py`):
  - The standalone OpenGL window drew through the same `PlayerVisualizerLayer` as the **in-player** background, and that layer always switched on the text guard (`uTextGuard = 1.0`). The guard exists to keep the player's terminal text readable: it lifts dark glyphs and washes the whole frame toward a near-black pane (`col *= mix(0.92, 0.42, ...)` plus a 20 % mix toward `vec3(0.015, 0.020, 0.034)`). With no text on top, all it did was crush the picture — on the default preset the mean brightness fell from **126/255 to 57/255**, which is what made the window look almost black.
  - The layer now reads `state["text_guard"]`, defaulting to **on** so the in-player background and any older state file behave exactly as before. The standalone renderer passes `False`, so the shortcut window draws the preset at full brightness again and now matches the Vulkan backend (126.3 vs 126.4 mean luma on the same preset).
  - The **Stream of Frequency — Dynamic Waves** presets hid the bug because they return early from the shader, before the guard runs. Every raymarched preset (including the saved default, Orbital Void Reflux) was affected.
  - Test: `test_standalone_window_draws_ahead_of_the_text_guard`.
- **"Engage Audio Stream" did nothing after the companion sat idle** (`scripts/mix_web_companion.py`, `tests/test_web_companion_playlist.py`):
  - The page loaded and the button looked alive, but tapping it produced no sound. The host arms the stream when the service boots and nothing plays on the computer, yet its elapsed clock kept counting with no listener attached. After a few idle hours `/api/status` reported the **end** of the mix as the current position (reproduced on the affected host: a 45 s track reported `45.0 / 45.0`), so the phone seeked to the final frame on engage. `play()` resolved immediately, `ended` fired, and the tap appeared to do nothing (or skipped a track, because `ended` posted `track_ended`).
  - **The resume point is now separate from the progress clock.** `/api/status` sends a new `resume_sec` alongside `position_sec`. `resume_sec` is `0` whenever the recorded position is not safely inside the file (an overrun, or inside the last 2 s / 2 %), so engaging audio always starts audible playback. The dashboard uses `resume_sec` for the first engage and for a server-side track change; the progress bar still shows the real clock.
  - **The phone now corrects the host clock.** Once audio is actually engaged it posts `report_position` every ~5 s, so the host resumes where the listener really stopped instead of from its own unattended drift. Invalid values (`nan`, `inf`) are rejected and clamped.
  - **A resume that lands on the tail restarts the mix.** `seekAudioTo()` guards resume seeks (not deliberate progress-bar seeks), and an `ended` event within 2.5 s of engaging restarts the track from the top instead of firing `track_ended`.
  - Verified in a real mobile-emulated Chrome session: a session drifted 83 minutes past a 45 s file now reports `resume_sec: 0.0`, and the tap starts playback at 0 s and advances normally.
- **A `/dev/tty` write aborted non-interactive conversions** (`Make_SOF_FLAC_CONVERSION.sh`): the split prompt and tracklist review wrote to the terminal unconditionally, so a batch with no terminal died under `set -e` before its output folders were published. New `say()` helper falls back to stdout.
- **`SOF_OUTPUT_FORMAT` was ignored without a terminal**: the non-interactive branch forced FLAC, so scripted conversions could not target another format.

---

## [0.4.3 Stable (Liquid)] - 2026-11-12

Released November 12, 2026. This release finishes the visualizer work started in 0.4.2 (preset 11, the desktop screen saver, multi-monitor spans, OpenGL/Vulkan parity), adds BigScreen VR mode, the PID-driven Stream of Frequency Dynamic Waves screen saver, the Favorites DJ Set exporter, MP Harmony tracklist narration, the Web Companion launch pad, a reinstall/factory-reset flow, the Web Companion system service, uniform menus with mouse support, three rainbow EQ effects, the arm64/amd64 package builder, tracklist HTML/TEXT export, the instance cap, and the Web Companion Favorites library. It also fixes the player banner layout, the Theme navbars, the tracklist viewer, Traktor history sync, and the visualizer detaching from the player in Mini and Micro mode.

### Added
- **"Play Anything" in MP Harmony: a random track straight to MP Audio Player** (`scripts/mix_web_companion.py`, `MP_Audio_Player.sh`, `scripts/resolve_mp_python.sh`, `MP_Harmony_Agent.py`, `tests/test_play_anything.py`, `tests/test_web_companion_playlist.py`, `tests/test_resolve_mp_python.py`):  - Say **"Play Anything"** (or "play something", "play a random track") to the MP Harmony Agent and it plays one randomly chosen entry from the playlist the listener is actually streaming, immediately, in **MP Audio Player**.
  - **The MP Web Companion is the source of truth and a hard gate.** It owns the Mix / Music / Favorites queue, so the random pick always comes from the library the listener selected. If it is not running the agent starts it; if it is running without autostart it enables it. Only when it is both **running and enabled** does the action proceed. A companion that cannot be started is reported, never silently ignored.
  - **An empty playlist is refused before anything is touched.** The companion reports `empty_playlist` with the reason (`No tracks available in the Music playlist`, or the Favorites hint) and the agent returns it as-is. The player is not woken and no fake track is announced.
  - **Random selection skips the track already playing** whenever the playlist has more than one entry, so repeated requests do not replay the same file. A single-track playlist still plays that track.
  - **Playback is confirmed, not assumed.** After handing the file over, the agent polls the player's status until the daemon reports that exact path. A start that exits 0 but leaves the previous track loaded is returned as a failure with the player's own reason.
  - **Silent-player self-heal.** A player daemon left running by an older launcher on an interpreter without `sounddevice` reports `state: playing` while writing nothing to a device (`audio_error: sounddevice is not installed`). The agent stops that daemon once and relaunches through `MP_Audio_Player.sh`, which now resolves an interpreter that can import **both** `soundfile` and `sounddevice`, so the track is audible rather than merely "playing".
  - The companion gains `GET /api/play-random`, and `/api/control?action=random` works from the dashboard too. The agent gains the `play_anything` tool, the `/play-anything` slash command, and a **Play Anything** quick chip.
  - **"Play my latest mix" plays the newest mix, 163 The Celebration.** A second tool, `play_latest_mix` (`/play-latest`, plus a **Play Latest Mix** chip), asks **MP Audio Player** for the newest mix with its own `--get-latest-mix` scan, so "latest" is identical to what the player itself would start — including its rule that ignores the bundled first-run sample. It then plays that file and reports the readable title (`Stream of Frequency 163 THE CELEBRATION`), stripping the `MPlanetarian_-_` prefix and the timestamp. Latest-mix playback is local, so it does **not** require the Web Companion. With no mix in the archive it answers `no_mix_available` and names the archive setting to check instead of touching the player.
  - Manager discovery is no longer pinned to `MP_Mix_Manager_v0.3`: the agent probes `MP_MIX_MANAGER_DIR` / `MP_APP_DIR`, every known v0.4 and v0.3 layout, then keeps the installs that actually contain `MP_Audio_Player.py`. The same resolver now feeds `play_mix_or_audio` and the mix archive roots.
- **The Ultimate Stream of Frequency screen saver — Presets 12 & 13: "Stream of Frequency — Dynamic Waves" and "Stream of Frequency — Dynamic Waves P2"** (`shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `shaders/presets/p11.frag.spv`, `shaders/presets/p12.frag.spv`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/liquid_player_visualizer.py`, `scripts/liquid_glass_vulkan.py`, `scripts/pid_stream.py`, `scripts/mp_visualizer_idle_saver.py`, `scripts/generate_sof_waves_cover.py`, `Mix_Archive_Manager.sh`, `assets/stream_of_frequency_dynamic_waves_cover.png`, `assets/stream_of_frequency_dynamic_waves_p2_cover.png`, `COVERS/Stream_of_Frequency_Dynamic_Waves_Preset.png`, `COVERS/Stream_of_Frequency_Dynamic_Waves_P2_Preset.png`, `tests/test_sof_dynamic_waves.py`):
  - **Two new MP Visualizer presets, and they are the installed screen saver.** **Dynamic Waves** is the horizontal river: one stream of frequency bands spanning most of the screen, every band travelling **left → right together** on the same clock. **Dynamic Waves P2** is the vertical river: pixel glyph columns falling **top → bottom**, Matrix-rain style.
  - **Pure white background, 82% coverage.** `SOF_COVERAGE = 0.82` fills 82% of the **short** screen axis with the river on a genuine paper-white page — no gradient, no vignette, no texture. The percentage was picked to be readable at a glance without touching the window edges.
  - **The rainbow travels in slow dynamic arcs, not straight lines.** `sofArcOffset()` bends the river with a two-harmonic sine whose amplitude itself breathes over about three minutes; the offset is applied *across* the bands so they fan into concentric arcs. The hue is a function of position along the river plus a 0.0105/s clock, so a full rainbow cycle takes about 95 s — slow enough to read as one long wave instead of a strobe.
  - **Both presets are driven by real Linux PIDs in the screen saver.** `scripts/pid_stream.py` walks `/proc` about eight times a second and turns **every newly created PID** into one stream event: a hot front sweeps the river and throws **fireworks of coloured square and glyph-icon pixels** out behind it. The first scan only learns a baseline, so starting the saver never fires one burst per process already running. A storm of new processes (a build, a package upgrade, a browser tab burst) folds into **one very hot burst** rather than an unreadable stack of fronts.
  - **The same presets are audio-driven inside the player.** `StreamDriver(mode="audio")` fires the identical stream vector from `beat_pulse` / `rms_energy` with a 0.55 s floor between events, so a window inside the MP Liquid Player stays locked to the music while the screen saver stays locked to the machine. Both modes share the shader path, so the preset looks the same in either place.
  - **The fireworks composite over white instead of adding to it.** Adding light to a white page is invisible, so the burst returns a coverage + colour pair and the shader mixes it over the river (`return mix(col, burst.rgb, burst.a)`). The sparks are drawn on a quantized polar lattice of glyph cells, which is what gives them their blocky pixel-icon character.
  - **20% fewer pixels in the screen saver.** `SAVER_PIXEL_SCALE = 0.80` reduces the **pixel count** of the saver's render target (never the edge length), so a 1920×1200 panel now marches at **1717×1073** — about 1.84 Mpx instead of 2.30 Mpx — and is blitted up. `scale_pixel_count()` keeps the aspect ratio exact.
  - **New `--saver-presets` pool.** The idle daemon (`mp_visualizer_idle_saver.py`) now defaults to the Dynamic Waves pair rather than touring the whole library, via `DEFAULT_SAVER_PRESETS` and a new `--saver-pools` flag. Single-preset locking still works through `--preset`.
  - **New manager entries.** Option 23 → 2 gains **W** and **V** for the two presets, the screen-saver screen (23 → 8) names the pair and the PID event source, and a saved autostart entry runs with `--saver-pools`.
  - **Generated cover art** (`scripts/generate_sof_waves_cover.py`) renders each river's own still with Pillow, so the one-cover-per-preset rule holds for two presets that never sample a texture.
- **Start / Stop MP Web Audio in the tray** (`scripts/mp_system_tray.py`, `scripts/mp_web_service.py`, `scripts/mix_web_companion.py`, `scripts/macos_menu_bar.swift`, `scripts/show_help.sh`, `tests/test_web_audio_tray.py`):
  - The shared tray menu gains one entry that follows the service state: it reads **Stop MP Web Audio** while the companion is serving and **Start MP Web Audio** while it is not. The label is re-read when the menu opens, so it is never stale.
  - **Linux only for now.** The platform dispatch in `mp_web_service.toggle_companion()` is an explicit case table (`{"Linux": "_toggle_linux"}`), so adding macOS or Windows is one entry plus one handler. Other platforms report "not available" instead of doing something surprising.
  - Start and stop go through `scripts/mix_web_companion.sh`, so the systemd user unit, the autostart setting, and the direct-launch fallback all behave exactly as they do from the console. Linux `--action` is wired up too, which previously fell through and tried to open the tray GUI.
  - **Connected phones are told about a restart and recover by themselves.** Before stopping, the tray POSTs `/api/announce`, so every dashboard still polling shows **"MP Audio Web Companion has been restarted — reconnecting…"** instead of the stream simply dying. Each server launch reports a fresh `instance` id from `/api/status`; when a dashboard sees the id change it re-attaches the audio element in place and resumes the mix on the new instance. A full page reload is only the fallback, because a reload drops the mobile tap gesture and would make the listener tap "Engage Audio Stream" again mid-set. A native `<audio>` error triggers the same single re-attach, guarded so a genuinely broken stream cannot loop. The pending notice expires on its own, so a stale banner cannot survive into a later session.
- **Tracklist narration with the MP Harmony Agent** (`scripts/mp_tracklist_narrator.py`, `Mix_Archive_Manager.sh`, `tests/test_tracklist_narration.py`):
  - Tracklist Suite (**14**) now has **9. Read Tracklist Out Loud with MP Harmony Agent**. The page lists the current tracklist files (the file name is the title).
  - The selected tracklist is handed straight to the running MP Harmony Agent. The agent's currently configured AI model turns it into a spoken narration script, and the agent's own OpenAI-compatible speech endpoint (`/v1/audio/speech`) speaks it with a voice already installed in MP Harmony: Stephen Hawking, the Morpheus variants, Agent Smith, Neo, Trinity, the Oracle, or Zion. **Stephen Hawking is the default every time.**
  - Every recording is written to `MP_TRACKLISTS_VOICED/` inside the primary `MIX_ARCHIVE` folder as `<title>_<voice>_<timestamp>.wav`, next to a `.txt` transcript and a `.json` metadata file, so the audio can be reused for production work.
  - When the agent's model replies with commentary instead of a script (for example "No tools were invoked..."), the narrator strips the meta sentences and falls back to a clean narration built from the tracklist itself, so a recording is always produced.
  - Very long tracklists are split on sentence boundaries and the WAV segments are joined, keeping one continuous file per narration.
  - After generation the page states exactly where the audio was saved, offers to listen to the recorded version in the **MP Audio Player (Preview)**, and then asks whether to open a file browser window at that location for audio production.
  - The narrator reuses the MP Harmony Agent's Stephen Hawking synthesis chain (espeak-ng `en-us+m3` plus the Matrix bass/EQ/echo filter) as an offline fallback when the speech endpoint is unreachable.
  - The agent is started automatically when it is not already listening on port 11435.
- **Desktop launch pad in the Web Companion** (`scripts/mix_web_companion.py`, `scripts/mp_suite_control.py`, `scripts/mp_system_tray.py`, `tests/test_web_companion_launchpad.py`, `tests/test_web_companion_playlist.py`):
  - On Linux, the bottom of the dashboard now has four buttons that open **MP Gui**, **MP Mix Manager (CLI)**, **MP Liquid Player**, and **MP Liquid Visualizer** on the computer, plus a **Close All** button.
  - Each launcher is themed from the currently selected palette (`--btn-gradient`, `--badge-color`, `--accent-crimson`), so changing the theme from the Theme menu repaints the pad immediately.
  - A button is painted **pressed down** with the theme gradient while its app is running and returns to the raised state when it is not. The state is polled from `/api/launchpad`, which reads the live process table through the shared tray classifier, so a manager hosted inside the MP Gui window is not mistaken for the CLI.
  - Tapping a running app focuses its window instead of opening another one. The CLI button uses `bin/launch-manager`, the same fullscreen Konsole launcher the tray menu uses.
  - **Close All** closes all four apps but deliberately leaves the shared tray icon (and the companion) running so the suite can be relaunched from the phone. A keepalive marker (`~/.config/mix-manager/mp_system_tray.keepalive`) stops a shortcut-started tray from quitting after the last window closes, and expires on its own once the dashboard stops polling.
  - Launches from the systemd user service resolve `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY` from `systemctl --user show-environment`, so a phone-initiated launch still lands on the desktop session. The pad is not rendered on macOS or Windows.
- **Web Companion runs as a real service, at boot/login, on both test hosts** (`scripts/mix_web_companion.sh`):
  - `mix_web_companion.sh install` writes the platform service, enables it, and starts it. Linux uses a `systemd --user` unit (`mp-web-companion.service`); macOS uses a `launchd` LaunchAgent (`com.mplanetarian.mp-web-companion`). `stop`, `restart`, `status`, `enable`, `disable`, `uninstall`, `logs`, and `grant` are also available.
  - The wrapper previously checked for a unit file that nothing ever created, so `start` silently fell back to `nohup` and autostart never existed. It also ignored the app-root `config.env` when run from `scripts/`.
  - The Linux unit orders itself after the archive mount units and sets `RequiresMountsFor`, so it starts with a populated library at boot instead of before `/mnt/...` is mounted. Enabling it also turns on user lingering so the service starts at boot without a login.
  - macOS gets a minimal `MP Web Companion.app` bundle, which gives the launchd job a stable bundle identity that can be approved in **Privacy & Security → Full Disk Access**. macOS denies background jobs read access to `/Volumes` until that one-time grant; `status` and `/api/mode` now name the exact folder and app instead of reporting an empty archive, and `mix_web_companion.sh grant` opens the settings pane.
- **AppleDouble files are excluded from playlists** (`scripts/generate_archive_folder_playlists.py`, `scripts/mix_web_companion.py`):
  - macOS leaves `._name.flac` resource forks (a few KB) beside real audio on non-HFS volumes. They carry an audio extension but are metadata, so they were listed as playable mixes. Both the playlist generator and the companion scanner now skip them.
- **Reinstall / factory reset on every supported OS** (`scripts/reinstall_mp_manager.sh`, `install.sh`, `manage_installation_config.sh`, `Mix_Archive_Manager.sh`, `bin/mix-archive-manager`, `scripts/show_help.sh`, `tests/test_reinstall.py`):
  - `./install.sh --reinstall` (also `--re-install`, `--factory-reset`, `--reset`) wipes the user's configuration and then runs the normal installer again, so the program ends up exactly as it was after the first install: fresh `config.env` from `config.env.example`, empty theme/EQ/window/geometry caches, no activity history, and new symlinks, tray entry, and desktop shortcuts. `mix-archive-manager reinstall` and menu **35 → 2 → 7** open the same flow.
  - The user is asked three separate questions: preserve **playlists** (custom, generated, and `favorites.json`), preserve **music folder config** (`music_folders.json`, mount points, and the `MUSIC_SMB_*` / `FAV_EXPORT_*` settings), and preserve **mix archive folder config** (`MIX_ARCHIVE_DIR`, `EXTRA_MIX_ARCHIVE_DIRS`, and the output directories). The answers default to preserve; `--yes` accepts all three without prompting, `--fresh` keeps nothing, and `--dry-run` prints the plan and changes nothing.
  - Anything preserved is staged into a temporary directory before the wipe, then copied back afterwards. For `config.env`, only the chosen keys are carried across and written into the otherwise untouched new template, so a preserved archive path cannot drag old SMTP or theme settings back with it.
  - Configuration snapshots (`~/.config/mix-manager/backups`) and exported config bundles (`~/.config/mix-manager/exports`) are the user's own safety net, so a normal reset keeps them. `--fresh` removes them with everything else. The `config_backups/` and `exported_configs/` folders inside the install tree are never touched.
  - **Converted audio is never deleted.** `FLAC_CONVERTED_OUTPUTS`, `MP3_CONVERTED_OUTPUTS`, `WAV_CONVERTED_OUTPUTS`, `MP4_CONVERTED_OUTPUTS`, `CONVERTED_WAV_FILES`, `MIX_ARCHIVE`, `COVERS`, `SPEK_OUTPUTS`, `MP_AUDIOWAV_REC_OUTPUT`, and `TOP_5_TRACKS` are on a protected-path list, checked by exact path before every `rm`, and reported at the end so the user knows exactly what was left alone and can remove it themselves.
  - Playlist folders that live in the install tree (`PLAYLISTS_GENERATED`, `playlists`) are cleared only when the user chose not to preserve playlists. `PLAYLISTS_GENERATED` folders on the archive drive are left alone because they sit on removable storage and are regenerated on demand.
  - Mounted music folders inside `~/.config/mix-manager/mounts` are detected with `mountpoint`/`df` and left mounted instead of being recursively removed.
  - macOS also removes `~/.local/share/mp-mix-manager` (the generated Python environment and menu-bar bundle) and unloads the `com.mplanetarian.mp-mix-manager-tray` LaunchAgent. Windows stops the `MP Mix Manager.exe` and PowerShell tray processes first. Linux, WSL, and FreeBSD stop the player, tray, and visualizer so no open file survives the wipe.
  - The helper reports exit code `10` when the user aborts at the `REINSTALL` confirmation, so the installer can exit cleanly without reinstalling anything.
- **Favorites DJ Set Exporter** (`scripts/MP_Fav_Playlist_Track_Exporter.py`, `scripts/MP_Fav_Playlist_Track_Exporter.sh`, `MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `scripts/generate_traktor_playlist_from_history.py`, `config.env.example`, `tests/test_favorites_dj_export.py`):
  - Open **Favorites** in MP Audio Player and press **Shift+J** (or **Export DJ Set** in the Favorites footer) to copy the playlist for DJ set preparation. At least one favorite is required; an empty playlist refuses to run and says so.
  - Choose **1** for a folder on the local drive or **2** for the SMB share. SMB uses `smbclient` with the configured `FAV_EXPORT_SMB_*` credentials (falling back to `TRAKTOR_SMB_*` / `MUSIC_SMB_*`), or copies straight into `FAV_EXPORT_SMB_MOUNT` when that mount exists.
  - Every copy is size-checked. For SMB the first copied file is fetched back from the share and probed, and at least one file must decode as audio before the export is reported successful; truncated or unreadable copies fail the export instead of silently passing.
  - A tracklist in the existing MP Mix Manager format (`RELEASE INFO & SOURCE MANIFEST` / `TRACKLIST`, numbered `01. Artist - Title`) and an `.m3u8` playlist are written into the same folder. The default name is the date plus a random 12-character playlist ID, e.g. `MP_Fav_Set_2026-11-12_A1B2C3D4E5F6`; a custom tracklist name can be typed instead.
  - When Traktor history and `collection.nml` are available, the exporter offers to create a Traktor playlist, asks for the playlist name and an optional history file ID, and matches the export against that session (falling back to the full collection) in Key Field Ascending order.
  - **Clearing Favorites now uses a decision window.** Pressing **D** / Delete in the Favorites playlist opens a confirmation panel; **Y** clears the playlist and any other key or Esc keeps it. Audio files are never deleted. Other libraries keep the existing two-step arm/confirm.
  - Manager menu **17 → 4. Export Favorites DJ Set** launches the same exporter.
  - `scripts/generate_traktor_playlist_from_history.py` was implemented (it was a stub) and now also supports headless `--history-id`, `--playlist-name`, `--track`, `--list-history --json` and `--version-only`, which the exporter and the Manager use. Writes back up `collection.nml` first and restore it if the write fails.
- **In-window MP Visualizer on the Vulkan backend** (`scripts/liquid_glass_vulkan.py`, `MP_Audio_Player_Liquid_Glass.py`, `tests/test_visualizer_backend_parity.py`):
  - The visualizer used to appear behind the terminal text only on the OpenGL player host. Choosing `LIQUID_GLASS_BACKEND=vulkan` silently lost the background and showed plain glass.
  - Added `VisualizerBackground`, the Vulkan counterpart of `liquid_player_visualizer.PlayerVisualizerLayer`. It runs the same preset shader and the same telemetry bridge over the glass swapchain, so a preset looks the same on either backend.
  - `LiquidGlassRenderer` now exposes `draw_player_visualizer_background()` and `sync_player_visualizer_opacity()`, matching the OpenGL host API, and `draw()` swaps the glass pipeline for the visualizer pipeline while the visualizer is active. An active visualizer replaces the glass pass rather than drawing over it, which is what the OpenGL host already did.
  - The preset pipeline is rebuilt on preset change and the cover art is re-uploaded into device-local memory, so all 11 presets work in-window on both backends. The Manager GUI and the tracklist viewer stay plain glass on both, as before.
- **Backend selection for the standalone MP Visualizer** (`scripts/visualizer_backend.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `MP_Liquid_Visualizer.py`, `MP_Gui.sh`, `scripts/launch_liquid_glass.sh`, `scripts/launch-liquid-glass`, the four Liquid Glass hosts, `config.env.example`, `tests/test_visualizer_backend_parity.py`):
  - The standalone visualizer now runs on **either** backend. `LiquidVisualizerOpenGLRenderer` (`scripts/liquid_player_visualizer.py`) is the OpenGL twin of the Vulkan renderer: same preset shader, same telemetry bridge, same window sizing, same freeze-on-pause behaviour, plus F10/F11 monitor spans.
  - New `--backend auto|opengl|vulkan` flag, and a new optional `VISUALIZER_BACKEND` setting. `auto` follows `LIQUID_GLASS_BACKEND`, then prefers Vulkan when a working driver and at least one physical device are present, and falls back to OpenGL when they are not.
  - Every launcher forwards the backend it chose, so the window behind MP Gui and the window behind the player use the same GPU path as the window in front of them.
- **BigScreen VR Mode** (`scripts/bigscreen_vr.py`, `scripts/enter_vr_mode.sh`, `scripts/enter_vr_mode.bat`, `launch_vr_mode.bat`, `MP_Audio_Player.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `Mix_Archive_Manager.sh`, `scripts/mp_system_tray.py`, `install.sh`, `tests/test_bigscreen_vr.py`):
  - Main menu row **VR**, directly under **MP**: *Enter VR Mode with MP Liquid Player (BigScreen)*. Also `vr`, `vr-mode`, `bigscreen`, and `bigscreen-vr`, plus a Windows installer shortcut and a tray entry.
  - Bigscreen starts first, then the MP Liquid Visualizer opens as a large centred backdrop, then the MP Liquid Player opens centred on the primary desktop in Retro Linux Memories, **paused**.
  - Music starts only after the player host reports that its centred size has settled, through `~/.config/mix-manager/vr_player_ready.json`. If the player never reports centred, music is not started and the launcher returns an error instead of guessing.
  - Both Liquid Glass hosts read `MP_VR_LAYOUT`, open on the VR grid, keep the window centred, publish the ready report only once the size holds, and clear it on close. The renderers open directly at the centred position, so there is no corner flash.
  - The visualizer honours `MP_VR_VISUALIZER_SIZE/X/Y`, ignores "match the player" and 4K fullscreen while in VR, and stays unfocused behind the player.
  - The header shows **VR Mode** in magenta instead of `CLI Mode` / `TTY CLI Mode`, driven by `MP_VR_MODE=1` and also detected from a live VR player. MP Gui still shows neither label.
  - `BIGSCREEN_EXE` in `config.env` handles a custom install. On Linux the menu row, `enter_vr_mode.sh`, and `bigscreen_vr.py` refuse with the same message.
- **MP Package Dev Utility — arm64 and amd64 Linux packages** (`MP_Package_Dev_Utility.sh` in `BASH_SCRIPTS`, `tests/test_package_dev_utility.py`):
  - New packaging utility that turns a MP Mix Manager source tree into published Linux artifacts for **both amd64 (x86_64) and arm64 (aarch64)**.
  - Formats: Debian `.deb`, portable `.tar.gz` bundles, and optional RPM `.rpm` when `rpmbuild` is present.
  - No cross-compilation is required: the payload is interpreted Bash and Python, so an arm64 package carries the identical application code. A native ELF scanner refuses to put an x86-64 binary into an arm64 package unless `--allow-native-binaries` is passed.
  - `--arch host|amd64|arm64|all` selects targets; `--format deb,tar,rpm|all` selects formats. `--publish` uploads the artifacts, `SHA256SUMS` and generated release notes to a GitHub release with `gh`.
  - Packages install `/opt/mp-mix-manager`, add `mp-mix-manager`, `mp-mix-manager-gui`, `mp-liquid-player`, `mp-audio-player`, `mp-visualizer` and `mp-alarm` to `PATH`, and register desktop entries and hicolor icons.
  - Staging strips `.git`, archives, logs, caches, `config.env` (credentials are never shipped) and broken or out-of-tree symlinks. Portable launchers locate the bundle from their own path, so a bundle can be moved or renamed after extraction.
  - `.deb` output uses `dpkg-deb` when available and falls back to a built-in `ar` + `tar` writer, so packaging works on Fedora/Bazzite/Arch hosts.
  - Version labels are normalised for packaging: `0.4.3 Stable (Liquid)` becomes `0.4.3`, and pre-release labels use `~` so `0.6.0-rc2` sorts before `0.6.0`.
  - Every artifact is accompanied by `SHA256SUMS`, `BUILD-MANIFEST.txt` and `RELEASE_NOTES_<version>.md` in `MP_MIX_MANAGER_INSTALLERS/`.
  - Covered by `tests/test_package_dev_utility.py` (`.deb` structure/metadata, arm64 output, relocatable bundles, checksum verification, dry-run safety, foreign-ELF rejection).
- **Tracklist Viewer HTML & TEXT export** (`scripts/tracklist_export.py`, `scripts/view_tracklist_console.sh`, `MP_Audio_Player.py`, `tests/test_tracklist_viewer_scroll_export.py`):
  - Press **X** in the tracklist viewer to export the tracklist as a styled HTML document, or **Y** for a plain TEXT copy.
  - Both files are written to **`TRACKLIST_EXPORTS/` inside the MP Mix Manager program folder** the user launched, so they are always easy to find. `TRACKLIST_EXPORTS/` is git-ignored.
  - **Y** saves the pristine `.txt` tracklist; **X** writes a self-contained dark/glass-themed HTML page with a track table, remix highlighting, print styles and HTML-escaped user content.
  - Headless use is supported: `view_tracklist_console.sh --export html|text <tracklist>` and `MP_Audio_Player.py --tracklist-export html|text`.
- **MP Visualizer — Preset 11: "Dreamworlds (Chromatic Storm)"** (`shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `shaders/presets/p10.frag.spv`, `scripts/mp_liquid_visualizer_vulkan.py`, `assets/dreamworlds_chromatic_storm_cover.png`, `Mix_Archive_Manager.sh`):
  - Uses **`Pictures/MPlanetarian_Dreamworlds_Lockscreen.png`** as the sole background/environment texture, and takes its animation language from the late sequence of **MPlanetarian "BIFURCATION (2026)"** — elongated teardrop vessels with stacked chrome fins, glowing furnace interiors and trailing bead swarms.
  - **Twelve distinct animation sequences** that morph continuously in one unbroken ~95 s loop with `smoothstep` crossfades:
    1. **Furnace Hull** — split teardrop shell with a molten furnace core
    2. **Fin Cascade** — chrome fins fanning open along the hull spine
    3. **Split Keel** — twin machined keels baring a plasma channel
    4. **Bead Surge** — the perforated bead swarm becomes the subject
    5. **Ember Wake** — vessel coasting on a long fiery wake
    6. **Plasma Cocoon** — glowing orbital rings laced around the hull
    7. **Spine Ring** — bare skeleton spine with counter-rotating plate gyros
    8. **Shard Bloom** — hull shattered into orbiting chrome shards
    9. **Hull Strata** — hull revealed as sliding horizontal layers
    10. **Twin Vessel** — mirrored pair joined by a plasma tether
    11. **Nozzle Flare** — rear three-quarter view venting a plasma cone
    12. **Storm Collapse** — everything implodes into a hot compressed point
  - Every sequence carries the video's **bead swarms and graphite wire trails**.
  - New `chromaticStormPalette()` built from the artwork's pigments, and five dedicated materials: dark anodised hull plate, self-luminous furnace/plasma emission, hot ember wake, polished chrome fin with chromatic edge fringing, matte black beads and graphite wire.
  - Per-sequence camera framing (distance **and** a yaw bias), so no sequence is ever viewed purely end-on.
  - New clamped ray projection for the background so the painting never smears at grazing angles.
- **`+` / `-` preset cycling inside the MP Visualizer** (`scripts/mp_liquid_visualizer_vulkan.py`):
  - Added `cycle_preset()` plus `KEY_SLOTS` / `presets_by_slot()` / `_key_slot()`. Number keys 1–9 map to a fixed slot list; `+` and `-` (and keypad `+`/`-`) step through the **entire** library in either direction with wrap-around, reaching presets that are not on a number key.
  - These bindings exist **only in the MP Visualizer window**. The MP Liquid Player keeps its own `+`/`-` behaviour untouched — locked in by a regression test.
- **F10 / F11 multi-monitor visualizer span** (`scripts/visualizer_monitor_span.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/liquid_player_visualizer.py`, `tests/test_visualizer_monitor_span.py`):
  - **F10** asks GLFW for the monitor layout, then launches one MP Visualizer on **every monitor except the one you are using**, placed on that monitor's desktop rect. Two seconds after launch each new window goes fullscreen on its own monitor.
  - **F11** does exactly the same, but each spawned window receives a **different randomly chosen preset**, never repeating the preset on the window you are on.
  - Pressing **F10** or **F11** again closes every spawned window and leaves only the window you are using. The window **bonded behind the MP Liquid Player is never closed** — it is the owner, not a spawned copy.
  - Works on **both backends** (Vulkan and OpenGL) through a new `MonitorSpanMixin`; spawned copies inherit the owner's drawing backend. A small JSON session file in `~/.config/mix-manager/` lets the owner and its copies coordinate without a server: when the owner closes (or is killed), the copies close too, and a copy that receives F10/F11 asks the owner to tear the whole span down.
  - Spawned windows never overwrite the saved active preset, never bond to the Liquid Player, and are exempt from the player-size and 4K auto-fullscreen rules so they stay pinned to their own monitor.
- **Three new rainbow Equalizer visual effects** (`MP_Audio_Player.py`, `tests/test_rainbow_fx_visualizers.py`):
  - **Rainbow Mandala Bloom** (`rainbow_mandala_eq`) — a kaleidoscopic spectrum drawn as layered petal wreaths that bloom and contract with the music, cycling continuously through the full hue wheel.
  - **Rainbow Wormhole** (`rainbow_wormhole_eq`) — a hyperspace plasma tunnel of concentric rings, spokes and travelling motes that rush toward the viewer, saturated with dynamically shifting rainbow colour.
  - **Rainbow Aurora Cascade** (`rainbow_aurora_eq`) — holographic ribbon curtains that ripple and fold like an aurora, painted in continuously evolving rainbow gradients.
  - All three are deliberately built differently from **Stream of Frequency** and use more simultaneous effects than it: layered geometry, polar lookup tables for per-frame speed, and animated hue rotation across every element.
  - Selectable through the Equalizer HUD and by name; aliases include `mandala`, `kaleidoscope`, `bloom`, `wormhole`, `tunnel`, `aurora`, `ribbons`, and `holographic`.
- **MP Visualizer desktop screen saver** (`scripts/mp_liquid_visualizer_vulkan.py`, `scripts/mp_visualizer_idle_saver.py`, `Mix_Archive_Manager.sh`, `tests/test_visualizer_screensaver.py`):
  - `--screensaver` fills **every connected display** with its own window, tours the whole preset library every `--saver-dwell` seconds in step across all screens, keeps the scene animating with no audio player running, hides the pointer, and **exits on any key press or mouse movement** on any screen. `--saver-preset` locks it to one theme; `--saver-windowed` runs it in a window. The owner tears every child window down when it closes, and also cleans up on SIGTERM so stopping it never orphans windows.
  - **Capped render resolution.** Each screen is filled edge to edge, but the raymarch target is capped: displays that can do **1920×1200** render at 1920×1200, and every other display falls back to **1920×1080**. Panels smaller than those caps render at their native size rather than being upscaled, and a 4K panel is never driven at native 4K. Implemented as `saver_target_resolution()` / `saver_screen_plan()` plus a per-screen `_saver_render` cap honoured by both the Vulkan (`_scale_to_fit`) and OpenGL (`visualizer_internal_size`) march paths, then blitted up to the full window.
  - New `idle_seconds()` probes desktop inactivity per platform: `xprintidle` on X11, `loginctl` idle hints as a fallback, and `ioreg` on macOS. It reports `0.0` when idle time cannot be determined, so the saver never starts unexpectedly.
  - New `scripts/mp_visualizer_idle_saver.py` daemon: watches idle time, launches the saver at the threshold, waits for it to close, then applies a cooldown. Supports `--status`, `--stop`, `--idle`, `--dwell`, `--cooldown`, and never leaves an orphaned visualizer.
  - Manager **Option 23 → 8** gains a full screen-saver screen: preview now, start/stop the daemon, show status, change the idle threshold and preset dwell, and install/remove a login autostart `.desktop` entry.
- **Web Companion Favorites library** (`scripts/mix_web_companion.py`, `tests/test_web_companion_playlist.py`):
  - **Favorites** is a third library mode beside Mix and Music, backed by the same `favorites.json` the MP Audio Player writes when **`H`** marks a mix or track. `favorites.m3u8` is the fallback mirror.
  - `scan_favorite_files()` keeps the player's saved order — the archive and music scans are newest-first, but Favorites order is meaningful. Paths are de-duplicated with the player's own key, and files that no longer exist are skipped so a stale favourite cannot stall the stream.
  - The companion only ever reads this playlist; the player stays the only writer. An empty Favorites reports empty with a hint instead of borrowing another library, and the empty notice is rendered without the amber fallback warning.
  - `/api/mode` and `/api/playlist` carry the detail, and `set_library_mode` accepts `fav`, `favs`, `faves`, `favourite`, `favourites`, and `favorite`.
  - The three segmented buttons fit a 390 px phone without truncation, verified at 390 px and 768 px with no horizontal overflow.
- **`R` random preset mode in the MP Visualizer** (`scripts/visualizer_random_mode.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/liquid_player_visualizer.py`, `tests/test_visualizer_random_mode.py`):
  - Press **`R`** inside the MP Visualizer window to turn random mode on: the window switches to a new randomly chosen preset **every minute**.
  - Press **`R`** again to turn random mode off and leave the window on the preset it is showing.
  - `pick_random_preset()` never repeats the preset already on screen, so random mode cannot appear to have stalled.
  - The shared `RandomPresetMixin` owns only the on/off state and the dwell clock; each host supplies `preset` and `switch_preset()`, so the **Vulkan and OpenGL** renderers behave identically. `RANDOM_PRESET_INTERVAL` is 60 seconds.
- **Uniform navigation and mouse support on every menu page** (`Mix_Archive_Manager.sh`, `tests/test_uniform_navigation.py`):
  - 30 pages still used plain `echo` lists with `read -r -p "Enter choice"`: no arrow keys, no `/` search, and no consistent Esc/0/q. They are all converted to `run_interactive_submenu()`, so every navigable page now shares one navigation contract.
  - Converted pages include Video Cut/Split, Process Monitors, Bash CLI, Visual Launchers, Live Monitors, Video Playlists, the Spek Suite, Cover Converter, Duplicate Detector, Search & Import, Liquid Glass Backend, Weather/Ephemeris, Daily Reports, Audio Players, Audio Player & Startup config (with its three nested sub-pages), Visualizer Presets, Screen Saver, cliamp, DAWs, Network Services, System Maintenance (all four OS variants), Traktor Monitor, Promo Outreach, Bluetooth, Audio Checksums, Open Mix in DAW, Export Mixes, and the Morning Alarm/DJ Scheduler.
  - Wide `=====` banners became menu header text, so status information stays visible above the options in the scrolling viewport. Every page keeps its original option set; only the presentation and numbering changed (`14) return` becomes `0) return`).
  - **Mouse support** with SGR reporting (`1000`/`1006`), enabled only for the duration of a menu: single click moves the highlight, a double click on the same row within 600 ms activates it through the same path as Enter, and the wheel steps the highlight one row. Reporting is switched off in `cleanup_submenu_screen()` and `exit_mix_manager()`, so the terminal is never left capturing the pointer. `MP_MANAGER_MOUSE=false` opts out to keep native text selection.
  - `tests/test_uniform_navigation.py` adds 12 tests, including one that walks the call graph from the main loop and fails if any reachable page prompts outside the nav system.

- **Four-instance cap for MP Gui and MP Liquid Player** (`scripts/mp_instance_limit.py`, `scripts/mp_instance_limit.sh`, `MP_Gui.sh`, `scripts/launch_liquid_glass.sh`, `scripts/launch-liquid-glass`, `scripts/launch_mp_gui.bat`, `scripts/launch_liquid_glass.bat`, `scripts/mp_system_tray.py`, `scripts/windows_tray.ps1`, `Mix_Archive_Manager.sh`, `scripts/bigscreen_vr.py`, the four Liquid Glass hosts, `tests/test_instance_limit.py`):
  - MP Gui and MP Liquid Player can never exceed **4 live windows**. Each launch opens another window until the fourth; a fifth launch focuses a window that is already open and shows a notice instead of spawning.
  - One shared counter (`~/.config/mix-manager/instances/`) backs every entry point: desktop shortcuts, KDE global shortcut keys, the shared tray icon on Linux/macOS/Windows, the console manager menu and **`G`** / **`L`** keys, **VR Mode**, and the Liquid Glass hosts themselves. A host launched by any path also claims its own slot before it draws, so bypassing a launcher cannot create a fifth window.
  - Claims are atomic (mkdir lock + per-process claim file), so two launches that arrive together cannot both take the last slot. A claim is dropped when its process exits, and stale claims are pruned.
  - The console manager, the tracklist viewer, and the MP Liquid Visualizer stay uncapped.
  - `python3 scripts/mp_instance_limit.py status` shows the live count for both roles. `check`, `claim`, `release`, `notify`, and `focus` are the other subcommands.

### Fixed
- **Music mode in the Web Companion kept listing mixes** (`scripts/mix_web_companion.py`, `tests/test_web_companion_playlist.py`):
  - Selecting **Music** set the mode, but the playlist stayed on the Mix Archive and the header still said Music. Two independent causes stacked up.
  - **An empty scan was cached forever.** `scan_music_files()` cached its result against the *playlist file's* signature. When the service started before the SMB music share was mounted, every path in `DJ_Tracks_ALL_MUSIC_FOLDERS.json` was unresolved, the scan returned `[]`, and that empty list was reused until the playlist file changed — which it never does when a share mounts. Music mode therefore stayed on the archive fallback for the life of the process, and only a service restart cleared it. An empty result is now retried every `MUSIC_SCAN_RETRY_TTL` (20 s), so the queue fills as soon as the share is reachable; a populated result is still cached until the playlist is rewritten.
  - **Nothing ever mounted the shares for the service.** The companion runs as a background job and never goes through the Music Folders menu, so the configured SMB folders were never mounted on its behalf. Added `ensure_music_folders_mounted()`, which mounts them through the existing `music_folders_manager.connect_folder()`. The explicit **Music** tap mounts synchronously (bounded to 15 s, behind the dashboard's existing "Switching to Music…" state) so the first tap lists tracks; every other queue build starts the mount in the background and answers immediately, so `/api/playlist` never stalls.
  - **A fallback list is now labelled as one.** If the library really is unreachable, the archive stream that keeps audio flowing is titled **"Up Next · Mix Archive (Music unavailable)"**, the count reads **"N tracks (from Mix Archive)"**, and the note names the cause. Mixes can no longer look like music tracks under a Music heading.
  - Verified against the live service on the affected host: after mounting, `/api/mode?mode=MUSIC` returned `total: 89, library_fallback: false` and `/api/playlist` listed the music tracks with `mode: MUSIC`.
- **"Engage Audio Stream" did nothing on macOS** (`scripts/mix_web_companion.py`, `scripts/mix_web_companion.sh`, `tests/test_web_companion_playlist.py`, `tests/test_web_companion_macos_service.py`):
  - The page loaded, but the button had nothing to play: `/api/status` reported `active_file: ""` because the launchd job could not read its archive volume. A `play()` on an `<audio>` with no `src` changes nothing, so the tap looked dead.
  - **Why the archive was unreadable:** the bundle entry point was `#!/bin/bash … exec python3`. `exec` replaces the process image, so macOS attributed the volume read to the interpreter (`com.apple.python3`) rather than to the bundle, and any grant recorded against **MP Web Companion.app** never applied. `tccd`'s own log confirmed it: the request arrived with `responsible={identifier=com.apple.bash … binary_path=…/Python.app/…}`. `write_launchd_plist()` now compiles a native launcher into the bundle that forks Python, waits for it, and forwards `SIGTERM`/`SIGINT`/`SIGHUP`. The bundle stays the responsible process for the whole session — the same log now reads `responsible={identifier=com.mplanetarian.mp-web-companion}` — so one grant is enough, and `launchctl bootout` no longer orphans Python while it holds port 8888. When `clang` is absent it keeps the shell shim and says so.
  - **The grant the host actually needs is Removable Volumes.** `tccd` logged the denial as `service=kTCCServiceSystemPolicyRemovableVolumes` for this bundle, because the archive lives on a USB disk (`/Volumes/DATAMAC3`). The status line and the dashboard now say to allow the app under **Privacy & Security > Files and Folders > Removable Volumes**, with Full Disk Access as the alternative that also covers it, instead of naming only Full Disk Access.
  - **The button now explains itself.** `engageStreamFromServer()` refuses to build a stream URL when no file is queued and shows the cause in the overlay instead of silently doing nothing. The cause comes from `/api/status` (`library_detail` / autoplay `detail`) and is painted before the first tap. The autoplay notice prints it on screen instead of only in a `title` tooltip a phone cannot hover.
  - `/api/autoplay` no longer flattens every empty library to *"Playlist is empty"*; it forwards the reason the queue builder already found (privacy block, offline music library, empty Favorites).
  - **`mix_web_companion.sh status` no longer lies.** It probed with the *Terminal's* permissions, which usually hold their own grant, so a blocked service still reported a healthy server. It now reads `/api/status` from the running service and names both the bundle and the interpreter to approve.
  - Verified on the reported Mac mini M2: `/api/stream` served a 75 MB FLAC, `200` for the whole file and `206` with `Content-Length: 65536` for a `Range: bytes=0-65535` request (what a phone sends), so streaming itself was never broken — only the permission that finds the files.
- **The shared tray icon disappeared on Linux when the first `python3` had no PySide6** (`scripts/mp_system_tray.sh`, `tests/test_mp_system_tray.py`, `tests/test_windows_user_integration.py`):
  - `scripts/mp_system_tray.sh` called the bare name `python3` for both the capability probe and the launch. Bazzite puts `/home/linuxbrew/.linuxbrew/bin` ahead of `/usr/bin`, so that name resolved to the Homebrew interpreter, which has **no PySide6**, while `/usr/bin/python3` does. The probe therefore exited early and, when it did get as far as Qt, `run_tray()` died with `ModuleNotFoundError: No module named 'PySide6'` and left the icon gone. The log had been repeating that traceback while every launcher's `--ensure` silently reported success.
  - The launcher now asks the shared resolver (`scripts/resolve_mp_python.sh`, `mp_python_with_modules PySide6.QtWidgets`) for the first interpreter that can actually import PySide6, and launches that absolute path through `setsid`/`nohup` and `exec`. This is the same capability-first rule the MP Gui and MP Liquid Player launchers already use, so a Homebrew or conda `python3` can no longer shadow the system one.
  - When no interpreter has PySide6 the script writes **`tray: no python3 on this host can import PySide6; not starting`** to `~/.config/mix-manager/mp_system_tray.log` instead of exiting silently, which is what made the failure invisible for so long.
  - Verified on the reported Bazzite host: `bash scripts/mp_system_tray.sh --ensure --stay` now starts `/usr/bin/python3`, and the icon registers as `:1.640304/StatusNotifierItem` with `org.kde.StatusNotifierWatcher`.
- **MP Liquid Visualizer opened an invisible window on Windows** (`scripts/liquid_player_visualizer.py`):
  - The GLFW window hints were passed as `(0x00020004, 0)` commented "DECORATED" and `(0x00020005, 1)` commented "VISIBLE". GLFW defines `VISIBLE = 0x00020004` and `DECORATED = 0x00020005`, so the two were transposed: the window was created with `VISIBLE = 0` and stayed hidden for its whole life. Windows enumerated a real `MP Liquid Visualizer` window at 716x739 that reported `IsWindowVisible = false`, so the desktop shortcut looked like a no-op.
  - Both hints now use named constants (`GLFW_VISIBLE`, `GLFW_DECORATED`, `GLFW_FOCUSED`, `GLFW_RESIZABLE`, `GLFW_CONTEXT_VERSION_MAJOR`/`MINOR`, `GLFW_OPENGL_PROFILE`) declared beside the other GLFW ids, matching `scripts/mp_liquid_visualizer_vulkan.py`. Found and verified on the Windows 11 test bed (`REPORTS/WIN11_TEST_20261112_034931`): after the correction the window appears and renders the preset.
- **MP Audio Player found no FFmpeg outside a login shell** (`MP_Audio_Player.py`):
  - The decoder is invoked as a bare `ffmpeg`/`ffprobe`. A Terminal shell has `/opt/homebrew/bin` on `PATH`, but launchd, a desktop shortcut, and an SSH command do not, so on macOS the player reported a `0` second duration and played silence even though the file was valid. The player now prepends the usual Homebrew/MacPorts/Linuxbrew bin directories at import time.
- **MP Audio Player would not import on the macOS system Python** (`MP_Audio_Player.py`):
  - An f-string expression in `_normalize_reveal_path` contained a backslash, which Python 3.9 (the interpreter in `/usr/bin`) rejects. Importing the module raised `SyntaxError: f-string expression part cannot include a backslash`, so `/usr/bin/python3 MP_Audio_Player.py --status` failed outright. The replacement is now computed before the f-string.
- **"Engage Audio Stream" did nothing when the host had no streamable mix** (`scripts/mix_web_companion.py`, `tests/test_web_companion_playlist.py`):
  - The button called `play()` on an `<audio>` element that had never been given a `src`, because `/api/status` reported `active_file: ""`. A rejected/empty `play()` produced no visible change, so the tap looked dead on the phone.
  - `engageStreamFromServer()` now refuses to build a stream URL when no file is queued and opens the overlay with the real cause instead of an empty player. The same explanation is used by the Play button.
  - The cause is read from the status payload (`library_detail` / autoplay `detail`) and cached, so a macOS **Full Disk Access** block on the archive volume reports *"grant Full Disk Access to MP Web Companion.app (/Volumes/…)"* right on the overlay. The overlay fills that in on its own while the tap is still pending, and the autoplay notice prints the detail on screen rather than only in the `title` tooltip, which a phone cannot hover.
  - `/api/autoplay` no longer reduces every empty library to *"Playlist is empty"*: it forwards the reason the queue builder already found (privacy block, offline music library, empty Favorites), so `/api/status` carries the real cause to the phone.
- **The MP Visualizer window's corners did not match the Liquid Player** (`scripts/visualizer_window_shape.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/liquid_player_visualizer.py`, `tests/test_visualizer_window_shape.py`):
  - Every Liquid Glass player window is a borderless rectangle whose corners are cut away — XShape on X11, `SetWindowRgn` on Windows, and a masked layer on macOS — with a 36 px radius. The visualizer opened behind it at exactly the same size as a plain square window, so its corners poked out past the player's rounded ones and showed as **black wedges**.
  - Added `RoundedWindowShaper`, which applies the *same* 36 px rounded rectangle to the visualizer window on all three platforms, using the same primitives and the same radius. The two windows now share one silhouette.
  - Both standalone backends use it: the Vulkan renderer (`LiquidVisualizerVulkanRenderer`) and the OpenGL twin (`LiquidVisualizerOpenGLRenderer`). The mask is applied when the window opens, then re-checked on every draw so it tracks Mini/Micro resizes, the half/default window modes, and the player bond.
  - Fullscreen windows stay rectangular on purpose — the screen saver, F10/F11 spawned copies, and the VR backdrop are meant to fill their area. Leaving fullscreen restores the rounding.
- **MP Visualizer had no OpenGL path** (`scripts/liquid_player_visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`): selecting OpenGL previously still ran the standalone visualizer on Vulkan, so a machine without a Vulkan driver had no visualizer at all. It now falls back to OpenGL, and `--self-test` verifies whichever backend it selected.
- **Screensaver cursor hiding segfaulted.** `glfwSetInputMode` was called without ctypes argtypes, so the window pointer passed through varargs and corrupted the call inside libglfw. It is now bound explicitly before use.
- **Missing `_upload_solid` on `PlayerVisualizerLayer`.** The OpenGL fallback texture uploader had drifted out of its class, so every standalone OpenGL draw raised `AttributeError` and the screensaver window aborted. Restored as a layer method.
- **Owner window opened at the wrong size in screensaver mode.** The window was created before the per-screen bounds were known; it is now resized and moved in place via `apply_saver_geometry()` once the monitors are enumerated, and `poll()` no longer snaps it back to the default size.
- **Every MP desktop shortcut failed when Homebrew's python3 came first on PATH** (`scripts/resolve_mp_python.sh`, `MP_Gui.sh`, `scripts/launch_liquid_glass.sh`, `scripts/launch-liquid-glass`, `scripts/launch_liquid_visualizer.sh`, `tests/test_resolve_mp_python.py`):
  - All four shortcuts (MP Gui, MP Liquid Player, MP Mix Manager, MP Liquid Visualizer) died with `Liquid Glass (OpenGL): No module named 'gi'`. Homebrew installs `python3` at `/home/linuxbrew/.linuxbrew/bin`, ahead of `/usr/bin` on PATH, and that interpreter has no PyGObject, `soundfile` or Pillow. Each launcher used the bare name `python3`, so the GTK hosts never started.
  - `scripts/resolve_mp_python.sh` already resolved a Python by path, but not by **capability**. It gains `mp_python_with_modules()`, which returns the first interpreter that can actually import the modules a component needs, walking an ordered candidate list: explicit `MP_PYTHON`, `MP_PYTHON_CANDIDATES`, project virtualenvs, the macOS audio environment, system interpreters, then PATH.
  - `mp_gui_python_bin()` requires `gi` and `numpy`, so the GTK hosts land on `/usr/bin/python3` even when Homebrew is first on PATH. `mp_visualizer_python_bin()` requires only `numpy` and OpenGL.
  - Every launcher and background spawn now calls the right helper instead of the bare name, including the visualizer processes that MP Gui and the Liquid Player launch, and the tracklist window in the console manager (`MP_PY_BIN`, exported once at startup).
  - Windows keeps the plain `mp_python_bin` path; it does not use GTK.
- **The installer did not check the Python GTK stack** (`install.sh`): step 4 only verified command-line tools (ffmpeg, sox, rclone, jq). It now also checks that the `python3` on PATH can import **PyGObject** and **NumPy**, which MP Gui, both Liquid Glass hosts and the visualizer all need, and prints the exact package command per platform (Fedora/Bazzite, Debian/Ubuntu, Arch, macOS, FreeBSD, Windows). It separately reports the optional **VTE 2.91** typelib, which supplies the embedded terminal inside the glass window; without it the window still opens with a plain pane. This is the check that would have caught the Homebrew-Python shortcut failure during install.
- **MP Gui exited before drawing a window** (`MP_Gui.sh`): the four-instance cap called `mp_gui_stop_windowless_hosts()` behind a file-existence test but never sourced `scripts/mp_gui_focus.sh`, so every launch printed `command not found` and stopped. The guard now sources the file first, matching the limit-exceeded branch.
- **MP Audio Player banner had no right edge and the Controls hint drifted** (`MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `tests/test_player_banner_eq_alignment.py`):
  - Both symptoms had one root cause. `MP_Audio_Player.py` wrote `~/.config/mix-manager/mp_player_state.json` with `json.dumps()` defaults, so the mini EQ was stored as JSON escape text: `"\u2581\u2586\u2584 \u2581   "`. That string is 28 bytes but paints only 8 columns.
  - Bash measured the raw text, so the EQ row padded to 98 columns instead of 118, ending 20 columns short of the panel border — the right `│` was missing. The number of `\uXXXX` escapes also changes as bars light up, so the padding changed every frame and the `Controls:` hint appeared to jitter.
  - Added `_json_decode()`, which expands JSON string escapes into real glyphs including UTF-16 surrogate pairs (so emoji in titles work), and `_mp_player_eq_field()`, which pins the EQ to a fixed **8-column** slot so `Controls:` never moves.
  - The state writer now uses `ensure_ascii=False`, so new state files contain real glyphs; older escaped files still work through the decoder.
  - `tests/test_player_banner_eq_alignment.py` adds 7 tests covering the decoder, surrogate pairs, the fixed-width EQ field, the state reader, and the UTF-8 writer.
- **MP Liquid Visualizer came away from the MP Liquid Player in Mini and Micro mode** (`MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `tests/test_liquid_player_visualizer_mode.py`):
  - Switching to Mini or Micro resized the player window but not the bonded visualizer, so the visualizer stayed at the Normal geometry.
  - The visualizer now follows every Normal, Mini, and Micro transition and stays attached behind the player in all three modes.
- **Tracklist Viewer did not start at the top and could not be scrolled** (`scripts/view_tracklist_console.sh`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `tests/test_tracklist_viewer_scroll_export.py`):
  - The viewer printed the entire tracklist and then `clear`'d, so anything taller than the window left the first entries scrolled off the top.
  - It now draws a fixed viewport sized to the terminal: the header, exactly as many tracklist rows as fit, and a footer. Line 1 is always the first thing rendered.
  - Scrolling added: **↑/↓** one line, **PgUp/PgDn** and **Space** one page, **Home/End** to jump, and the **mouse wheel**. `R` reloads, and the view clamps at both ends.
  - Added `WINCH` handling so resizing the window recomputes the viewport, and the active theme keeps updating.
  - Long lines, header values and footer rows are clipped to the terminal width so wrapped lines can no longer disturb the viewport. Piped output is left unclipped.
  - The Liquid Glass hosts used to disable the VTE scrollback buffer (`set_scrollback_lines(0)`), which dropped wheel/scrollbar events. Tracklist mode now keeps a scrollback buffer so wheel input reaches the viewer.
  - The viewer runs on the terminal's alternate screen, so closing it restores the console exactly as it was.
- **Z did not open the MP Visualizer from MP Gui or MP Liquid Player on Linux** (`MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass_macOS.py`):
  - The Linux default backend is OpenGL (`LIQUID_GLASS_BACKEND=opengl`), and its `_on_key_press` handled only **K**. **Z** fell through to the child terminal, so no visualizer window ever appeared. The OpenGL host now handles **Z** the same way the Vulkan host does.
  - Added `spawn_new_visualizer()`, which always opens a window. The old auto-start path skipped spawning whenever any visualizer process existed, so **Z** did nothing after the window had been closed by hand.
  - The macOS host gained the same `spawn_new_visualizer()` so the three hosts match.
- **Menu and player navigation bar followed the dark preset instead of the theme** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_menu_number_theme_color.py`, `tests/test_playlist_theme_highlight.py`):
  - The selected row in the Mix Archive Manager / MP Gui menus emitted the theme fill and then a `1;37;40m` foreground, which carries its own black background. The foreground overwrote the fill, so the navigation bar stayed black in every theme. The foreground is now colour-only, and the fill keeps the theme hue while the idle breathing animation dims it.
  - Row text picks black or white from the theme hue's luminance, so light palettes (Stream of Frequency, Phosphor, Solarized, Emerald, Retro Linux Memories) stay readable instead of drawing white on yellow.
  - The MP Liquid Player and MP Audio Player playlist selection bar no longer uses a fixed `48;2;70;70;70` grey in the console or reverse video in Liquid Glass. It uses the active theme's highlight colour in both hosts through the new `_selection_bar_ansi()` helper, with the same luminance contrast rule.
  - `test_menu_number_theme_color.py` now asserts a filled bar in the theme hue; the previous test still expected the unfilled row from an earlier revision and failed on `main`. `test_playlist_theme_highlight.py` gained `test_selected_playlist_row_uses_theme_colour`, which checks both hosts across five themes.
- **Traktor history sync skipped from Mix Archive Manager** (`Make_SOF_FLAC_CONVERSION.sh`, `scripts/Make_SOF_FLAC_CONVERSION.sh`, `config.env`, `config.env.example`, `scripts/top_5_tracks_manager.py`):
  - `sync_missing_traktor_history()` required `TRAKTOR_SMB_HOST` and `TRAKTOR_SMB_SHARE`, but neither key existed in `config.env` or `config.env.example`, so the sync always reported "SMB host, share, user, or password is not set" even when the user and password were set.
  - Added `TRAKTOR_SMB_HOST`, `TRAKTOR_SMB_SHARE`, and `TRAKTOR_SMB_HISTORY_DIR` to `config.env.example`.
  - The skip message now names the exact keys that are missing instead of listing all four.
  - Host, user, and password fall back to `MUSIC_SMB_HOST` / `MUSIC_SMB_USER` / `MUSIC_SMB_PASSWORD` / `SMB_DEFAULT_HOST` when the Traktor-specific keys are empty.
  - Traktor Top 5 Tracks config screen (option 5) gained rows 7-11 for the history sync host, share, user, password (hidden input), and history folder.
- **Preset specialization never folded the scene** (`scripts/liquid_player_visualizer.py`). `specialize_visualizer_source()` only replaced the `u_preset.x` spelling, but the Vulkan shader uses `u.preset.x`, so every per-preset SPIR-V module was byte-identical to the full library and none of the other scenes were compiled out. Both spellings are now folded, cutting the per-preset modules substantially (e.g. preset 10 is 265 KB against 843 KB for the full shader).
- **Invalid hollow shell in the plasma-cocoon sequence.** Subtracting one ellipsoid from another produced an invalid distance field that collapsed into a solid glowing blob; the cocoon is now built from genuine torus rings and capsule tendrils.

### Changed
- **MP Liquid Visualizer Z key is now a three-step window cycle** (`scripts/mp_liquid_visualizer_vulkan.py`):
  - The first Z press still enters borderless fullscreen.
  - The second Z press leaves fullscreen at **half the default window size**, centred on the default window's position.
  - The third Z press restores the **default window size**. Further presses repeat the cycle.
  - Once a windowed size has been chosen with Z, moving the window to another monitor no longer forces it back to fullscreen on a 4K display, and the player-bonding code no longer resizes the window back to the player.
  - Double-click still toggles plain fullscreen.
  - The cycle lives in `cycle_window_mode()`; `toggle_fullscreen()` remains for `--fullscreen`, `--4k`, the screen saver, and double-click.
- **Web Companion playlist and library switching** (`scripts/mix_web_companion.py`, `tests/test_web_companion_playlist.py`):
  - The dashboard is renamed **MP LIQUID PLAYER (WEB)**, page title **MP Liquid Player (Web) - DJ Booth Remote**.
  - The playlist shows **five** tracks in a scrollable 20-entry window with touch scrolling, overscroll containment, a themed scrollbar, and uniform 48 px rows. The offline/fallback notice moved above the list so it cannot push a row out of view.
  - The window stops at the end of the queue instead of wrapping, and scroll position survives the 15-second refresh.
  - The combined music playlist (~30 MB, 51k entries) is parsed once and reused until the file changes; queue rebuilds are serialised with a lock; reselecting the active mode is a no-op so it no longer rewinds the stream to track one.
  - The Mix and Music buttons now show a pending state and a request token keeps the newest tap authoritative, so a stale poll cannot repaint the old mode. `/api/mode` reports `library_fallback` / `library_detail`.
  - The volume and pitch clusters stack so neither is clipped on a 390 px phone.
- **MP Harmony starts Ollama from the menu row** (`scripts/launch_mp_harmony.sh`, `Mix_Archive_Manager.sh`, `scripts/show_help.sh`, `tests/test_mp_harmony_menu.py`):
  - The assistant row no longer dead-ends when Ollama is not answering. The manager asks before starting it, detects a distrobox/podman container (default `ollama-container`), and otherwise uses the systemd user service or a plain `ollama serve`. Answering no returns straight to the menu.
- **`--list-presets` now prints true keyboard slot numbers**, showing `--` for presets reachable only via the `+`/`-` cycle.
- `--start-paused` is retained for the player and used by the VR launcher.
- **Version** is `0.4.3 Stable (Liquid)` in `VERSION` and in the fallback used when that file is missing. Packaging, Windows resource metadata, and the macOS bundle version strings follow the same number.

### Tests
- `tests/test_sof_dynamic_waves.py` — 37 tests: preset registration, keys and numbering for both Dynamic Waves presets, alias resolution, cycle reachability, `--list-presets` output, cover-art fallback to paper white, the shader's design contract (82% coverage, slow arc phase, PID vector, composition over white, top-to-bottom fall), shipped SPIR-V and manifest coverage, the PID source (baseline silence, one event per new PID, a storm folding into one hot burst, disabled and non-Linux degradation), the audio driver (beat triggering, the inter-event floor, demo sweep), the saver's default pair and its launch command line, and the 80% pixel-count reduction on both the GPU saver and the console river.
- `tests/test_bigscreen_vr.py` — 25 tests: launch order, the pause handshake, no music when the player is off-centre, ready-file staleness, and the player/visualizer hooks, plus VR header and shortcut/tray coverage.
- `tests/test_visualizer_screensaver.py` — 14 tests: full-library cycling with wrap in both directions, number-slot stability, proof the Player does not bind `+`/`-`, screensaver wake/close on key, click and genuine cursor motion (with the initial cursor sample ignored), CLI flag surface, and the idle-saver status/stop plumbing.
- `tests/test_web_companion_playlist.py` — 41 cases, adding saved-order streaming, de-duplication, missing-file skipping, the `.m3u8` mirror, alias resolution, three advertised modes, the five-row window, scrollable geometry, playlist parse caching, reselect-no-rewind, and the empty-Favorites behaviour.
- `tests/test_player_banner_eq_alignment.py` — 7 tests for the JSON decoder, surrogate pairs, the fixed-width EQ field, the state reader, and the UTF-8 writer.
- `tests/test_tracklist_viewer_scroll_export.py` and `tests/test_package_dev_utility.py` cover the new exports and both package architectures.
- `tests/test_visualizer_window_shape.py` — 10 tests: the 36 px rounded mask matches the player on X11, Windows and macOS, is re-applied on Mini/Micro and the half/default modes, and stays rectangular in fullscreen.
- `tests/test_tracklist_narration.py` — 18 tests: file discovery, the Stephen Hawking default voice, the meta-commentary fallback that still produces a recording, long-tracklist splitting and joining, the saved `.wav`/`.txt`/`.json` trio, and the offline espeak-ng fallback.
- `tests/test_web_companion_launchpad.py` — 15 tests: the four buttons plus Close All, pressed-state polling, focus-instead-of-relaunch, the tray keepalive marker, and `DISPLAY` resolution from the systemd user environment.
- `tests/test_reinstall.py` — the reinstall/factory-reset flow: the three preserve prompts, `--yes`/`--fresh`/`--dry-run`, staging and restoring preserved keys, the protected converted-audio paths, and the exit code 10 abort.
- `tests/test_favorites_dj_export.py` — the Favorites DJ Set exporter: empty-playlist refusal, local and SMB copy with size and decode verification, tracklist and `.m3u8` output, the decision window when clearing Favorites, and the Traktor playlist hand-off.
- `tests/test_resolve_mp_python.py` — 6 tests: capability-based interpreter selection prefers an interpreter that can import `gi`/`numpy`, honours `MP_PYTHON` and `MP_PYTHON_CANDIDATES`, and never picks a Python that lacks the modules a GTK host needs.
- `tests/test_uniform_navigation.py` — 12 tests: every reachable page routes through `run_interactive_submenu()`, mouse reporting is enabled and torn down correctly, and click/double-click/wheel map to the same selection path as the keyboard.
- `tests/test_visualizer_backend_parity.py` — 23 tests: the Vulkan in-window background matches the OpenGL layer, preset and cover re-upload on change, the standalone renderer resolves auto/OpenGL/Vulkan, and every launcher forwards the chosen backend.
- `tests/test_rainbow_fx_visualizers.py` — 17 tests: style registration, alias resolution, distinctness from Stream of Frequency, glyph safety on limited consoles, and per-style rendering for all three new rainbow effects.
- `tests/test_visualizer_random_mode.py` — 17 tests: toggling random mode on and off, the 60-second dwell, never repeating the current preset, the empty-pool guard, and identical behaviour across both backends.
- `tests/test_visualizer_monitor_span.py` — 23 tests: monitor enumeration, excluding the owner's screen, the two-second fullscreen handshake, random-preset assignment without repeating the owner's preset, the bonded-player window surviving teardown, owner-death cleanup, and cross-window F10/F11 teardown requests.
- `tests/test_instance_limit.py` — the four-window ceiling: role detection (GUI vs player, viewer/embed/helper runs ignored), the four-then-refuse boundary, separate GUI and player counters, two launches racing for the last slot, per-process de-duplication, stale-claim pruning, the shell helper, and a check that every launcher, tray, menu and host path goes through the cap.

### Notes
- The first-run welcome (`scripts/liquid_glass_reward.py`) now greets 0.4.3 (Liquid) - [Stable Release].
- `0.4.2 Stable (Liquid)` remains under [0.4.2 Stable (Liquid)](#042-stable-liquid---2026-11-08) below, unchanged.

---

## [0.4.2 Stable (Liquid)] - 2026-11-08

Previous release. `0.4.1 Stable (Liquid)` was the in-tree version label before this release and was not published with its own notes. The October 24, 2026 feature set remains under [0.4.0 Stable](#040-stable---2026-10-24).

### Added
- **MP Harmony AI Assistant** (`scripts/launch_mp_harmony.sh`, `Mix_Archive_Manager.sh`):
  - The first main-menu row is **AI**, **Load MP Mix Manager AI Assistant (MP Harmony) (New)**.
  - **Shift+A** on any menu opens it. **Shift+A** on an empty assistant line returns to the manager.
  - The option runs only when Ollama is already answering on port 11434. It does not start Ollama.
  - It loads the local model **gpt-oss-pinned:latest** (OpenAI gpt-oss-20b, Harmony format). If that tag is missing, it describes the model and the assistant, then asks before installing it.
- **Shared system tray** (`scripts/mp_system_tray.py`, `scripts/mp_system_tray.sh`, `Mix_Archive_Manager.sh`, `MP_Gui.sh`, `scripts/launch-liquid-glass`, `scripts/launch_liquid_glass.sh`, `tests/test_mp_system_tray.py`):
  - MP Mix Manager, MP Gui, and MP Liquid Player use one tray icon.
  - Right-click it to launch MP Gui, MP Liquid Player, or MP Mix Manager. A program that is already open is focused.
  - **Close MP Mix Manager (Closes everything)** stops the console manager, MP Gui, MP Liquid Player, playback, and the visualizer.
  - The icon leaves when none of the three is still open.
- **MP Mix Manager GUI** (`MP_Gui.sh`, `Mix_Archive_Manager.sh`, `MP_Audio_Player_Liquid_Glass*.py`, `tests/test_mp_gui.py`):
  - Main menu option **36**, the **MP** row (`MP GUI Mix Manager (New)`), and `mix-archive-manager gui` open the manager inside the Liquid Glass host with `--manager`.
  - OpenGL is the default. Vulkan is used when `LIQUID_GLASS_BACKEND=vulkan`. Apple Silicon uses the Metal host.
  - The GUI starts on Retro Linux Memories. `--keep-theme` leaves `~/.config/mix-manager/theme` unchanged. `--theme NAME` writes another theme.
  - A second launch focuses the window titled `MP Mix Manager (Liquid Glass`. The Liquid Player can stay open beside it.
  - Closing the GUI stops playback and force-closes the MP Liquid Visualizer. Startup autoplay does not open the player from a GUI session. **`L`** from the GUI opens the player in its own window.
- **Retro Linux Memories theme** (`themes/Retro_Linux_Memories_MP_Mix_Manager.theme`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `scripts/liquid_glass_metal.py`, `shaders/liquid_glass_gl.frag`, `scripts/mix_web_companion.py`, `scripts/traktor_monitor.py`, `scripts/view_tracklist_console.sh`):
  - Dark slate field `#2d2d2d`, selection `#15539e`, chrome `#1a1e21`, sampled from the classic VeraCrypt GTK look.
  - Theme menu option **22**. The names `veracrypt`, `charcoal_vault`, `charcoal`, `vault`, `dark_vault`, and `veracrypt_dark` select this theme. There is no separate Charcoal Vault menu row.
  - **`C`** on the main menu cycles the same theme list as the Liquid Player and saves the choice.
- **Favorites playlist** (`MP_Audio_Player.py`, `tests/test_player_favorites.py`):
  - **`H`** marks or unmarks the selected mix or music track. A star on the row does the same.
  - **`3`** or the Favorites button loads the Favorites playlist, including when it is empty.
  - Saved as `~/.config/mix-manager/favorites.json` and `favorites.m3u8`. Audio-file tags are not rewritten. SMB paths are kept.
- **Mix Archive and Music library switching** (`MP_Audio_Player.py`, `tests/test_library_mode_switch.py`):
  - **`1`** reloads the Mix Archive playlist. **`2`** reloads the Music Folders playlist.
  - A missing Music playlist leaves the current library in place and names main menu options 2 and 13.
- **Clear playlist** (`MP_Audio_Player.py`, `tests/test_player_clear_playlist.py`):
  - **`D`** or **Delete**, pressed twice, empties the open playlist and its queue and stops playback.
  - **Esc** cancels. The audio files stay on disk.
- **Skip all remaining mix splits** (`Make_SOF_FLAC_CONVERSION.sh`, `scripts/Make_SOF_FLAC_CONVERSION.sh`, `tests/test_option2_submenus.py`):
  - **Enter** still skips splitting for the current mix only.
  - **`A`**, **`All`**, or **`2`** skips splitting for that mix and every mix left in the batch.
- **Main menu Section 1 title** (`Mix_Archive_Manager.sh`, `scripts/show_help.sh`, `README.md`):
  - The section label is `SECTION 1: Listen to Mixes & Tracks / Convert Audio / Import Mixes / Plus More`.
- **Linux TTY Desktop Switcher** (`Mix_Archive_Manager.sh`, `scripts/MP_Desktop_CLI_Switch.sh`, `scripts/show_help.sh`, `tests/test_tty_desktop_switch.py`):
  - Main menu option **37** is **Linux TTY Desktop Switcher (MP Mix Manager TTY Audio Mode)**. It is shown on Linux only. Exit moves to option **38**. `q` and `exit` still leave the manager.
  - The option runs `scripts/MP_Desktop_CLI_Switch.sh`. From Plasma it enables **MP Mix Manager TTY Audio Mode** and drops to a TTY login. From that console it turns the mode off and returns to the Plasma login.
  - The mode is stored in `~/.config/mix-manager/tty_audio_mode`. Playback stays in the terminal player. Liquid Glass, MP Gui, the visualizer, and other desktop windows stay closed.
  - The console session restores the terminal on the way out. Bash traps EXIT, INT, TERM, and SEGV, runs `stty sane`, and shows the cursor. The player saves the original termios, reads single keys without waiting for Enter, and puts the console back if the process is signalled.
  - `scripts/mp_tty_runtime.py` leaves standard output unbuffered, waits for keys with `select`, and overwrites changed cells. A full screen clear happens when the console size changes (SIGWINCH), not on every frame.
  - `scripts/mp_tty_kiosk.sh` installs `mp-mix-manager-tty.service` on `/dev/tty1` with restart, `Nice=-10`, and realtime I/O. Ctrl+Alt+F2 stays a login shell. Returning to the desktop stops that service. The console font stays Terminus 24 in `/etc/vconsole.conf`, and in `/etc/default/console-setup` when that file exists.
- **Header session label** (`Mix_Archive_Manager.sh`, `tests/test_header_cli_mode.py`):
  - A desktop Bash terminal shows **CLI Mode** on the right of the main header title.
  - The Linux text console (`/dev/ttyN`, `/dev/console`, or the TTY kiosk on tty1) shows **TTY CLI Mode**.
  - MP Gui does not show either label.
- **Bash prompt on B** (`Mix_Archive_Manager.sh`, `scripts/show_help.sh`, `tests/test_manager_bash_toggle.py`):
  - Press **B** on a manager menu to turn that same window into an interactive Bash prompt. The shell loads `~/.bashrc`.
  - Press **B** again on an empty line to return to the menu. A **B** typed after other characters stays in the command.

### Fixed
- **MP Player text stayed white on every theme** (`MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py`):
  - Playlist rows, track names, and the control labels use that theme's text colour.
  - Text with no colour of its own uses the same hue. Retro Terminal's hue is white, and the pale pane darkens it to gray. Pale glass darkens a light hue so it can be read.
- **No menu bar icon or desktop shortcut image on macOS** (`scripts/macos_menu_bar.swift`, `scripts/mp_system_tray.py`, `scripts/mp_system_tray.sh`, `scripts/install_desktop_shortcuts.py`, `install.sh`):
  - The installer starts an AppKit menu-bar icon and a login item. The icon does not use PySide6 or a KDE StatusNotifier host.
  - Click the icon to launch MP Gui, MP Liquid Player, or MP Mix Manager, or to close them.
  - The desktop shortcuts use `assets/mp_liquid_glass_icon.png`. MP Gui, MP Liquid Player, and MP Liquid Visualizer get that same Finder image as `MP Mix Manager.command`, so macOS does not place the artwork on the gray application plate. Each application bundle also keeps an `.icns`.
- **Linux font for MP Liquid and MP Gui** (`scripts/liquid_glass_font.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`):
  - Both windows use Hack at 11 pt when the Hack family is installed.
  - When Hack is not installed, they use Monospace 11.
  - `--font`, `MP_LIQUID_GLASS_FONT`, and a saved font in `~/.config/mix-manager/liquid_glass_font` still choose another face.
- **MP Visualizer looked blocky on Linux** (`scripts/liquid_player_visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `tests/test_liquid_player_visualizer_mode.py`):
  - The window was already the size of the Liquid Player, but the raymarch was 224 pixels on the long edge and then scaled up to fill that window.
  - Linux now marches at the framebuffer size, one pixel of the picture for each pixel of the window. The preset shader is the folded one, so the march is the selected scene.
  - macOS stays at a 768 long edge. Windows stays at 224 unless `MP_VISUALIZER_GL_LONG_EDGE` is set. Fullscreen on a 4K TV stays 1920×1080.
- **MP Mix Manager opened on another monitor** (`scripts/desktop_launch_screen.py`, `scripts/mp_gui_focus.sh`, `tests/test_desktop_launch_screen.py`):
  - The manager opens on the primary desktop, the connected output with the lowest positive KDE priority. HDMI-A-1, the Hisense, is not that screen.
  - On Wayland, X11 positions are a multiple of the KWin position. The launch request uses that multiple, so the window does not open on the monitor beside the primary display.
  - Starting the manager again moves an existing manager window onto the primary display when it is on another screen.
- **MP Liquid Player could not be moved after it loaded on Windows** (`scripts/windows_glass_drag.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `tests/test_windows_glass_drag.py`):
  - The player console is an owned window over the borderless glass. After it appears, drags on the top of the player never reach the glass, so the window stays where it opened.
  - The top strip, the outer bezel, and Alt or the Windows key move the glass. The console follows. A second click on that strip still maximizes. The close button and the opacity slider still receive their clicks.
  - A mouse button released over the console no longer leaves the glass drag stuck on.
- **G in the macOS Liquid Player** (`MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py`, `tests/test_liquid_player_mp_gui.py`):
  - The embedded player was started with `MP_LIQUID_GLASS=0`, so **G** built a spectrogram instead of opening MP Gui.
  - That player now gets `MP_LIQUID_GLASS=1`, the same flag as the other Liquid hosts. **G** opens MP Gui.
- **No tray icon after `install.sh`** (`scripts/mp_system_tray.py`, `scripts/mp_system_tray.sh`, `install.sh`, `tests/test_mp_system_tray.py`):
  - The installer enables KDE's `statusnotifierwatcher` module, writes the autostart entry, and starts the shared tray with `--stay`.
  - The tray loads that module itself when Plasma has it turned off, then waits until the StatusNotifier host is available.
- **SMB music folder playlists hung or were not written** (`scripts/music_folders_manager.py`, `tests/test_music_folder_smb_scan.py`):
  - Playlist refresh lists the share with `rclone lsf` and uses the size and time from that listing.
  - The scanner does not `stat` an rclone FUSE mount that answers `isdir` and then never returns a file stat.
  - A named folder playlist is not replaced by `DJ_Tracks_ALL_MUSIC_FOLDERS`.
  - A failed or empty listing leaves the playlist already on disk.
- **SMB login** (`scripts/music_folders_manager.py`):
  - Music Folders option **8** writes `MUSIC_SMB_USER` and `MUSIC_SMB_PASSWORD` to `~/.config/mix-manager/config.env` with mode 600.
  - Enter keeps the current value. The password is not shown. A folder that already stores its own username keeps that username and uses this password.
- **MP Liquid desktop shortcut opened on the wrong monitor** (`scripts/desktop_launch_screen.py`, `scripts/launch-liquid-glass`, `scripts/launch_liquid_glass.sh`, `tests/test_desktop_launch_screen.py`):
  - The shortcut places the player on the main desktop. HDMI-A-1 (the Hisense TV) is not selected as that screen.
  - The visible position is read with `xwininfo`, which matches KWin. `wmctrl -lG` on this session reports a doubled origin and is not used for the correction.
- **Windowed visualizer overflowed the main desktop** (`scripts/mp_liquid_visualizer_vulkan.py`):
  - On macOS the visualizer opens at 1094×734, the same size as the Liquid Player. Other platforms stay at 700×700. On a shorter work area the square still shrinks to fit. Fullscreen on the 4K TV is unchanged.
- **Visualizer stayed open after the Liquid Player exited** (`scripts/liquid_visualizer_session.py`, `MP_Audio_Player_Liquid_Glass*.py`):
  - Exiting the Liquid Player closes the companion visualizer unless another Liquid Glass host is still open.
  - Closing the Manager GUI always stops playback and closes the visualizer.
- **`L` did nothing while the Manager GUI was open** (`Mix_Archive_Manager.sh`, `scripts/launch-liquid-glass`, `scripts/launch_liquid_glass.sh`):
  - Single-instance checks skip a Liquid Glass process started with `--manager`, so **`L`** can start or focus the player.
- **Startup loader drew a status card on the console** (`scripts/display_loader_video.sh`, `tests/test_loader_video.py`):
  - The console is cleared and the cursor is hidden while `MP_loader.mp4` plays. The version title, the loading line, and the skip hint card are gone.
  - **Esc**, **Space**, **Enter**, and **`q`** still skip the intro. The manager menu draws after the video.

### Changed
- **Default glass opacity** (`MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py`, `tests/test_liquid_glass_player_features.py`):
  - MP Liquid Player and MP Gui open at 80% opacity. The transparency slider starts there.
  - Linux and Windows still slide from 20% to 80%. macOS still slides from 40% to 80%.
- **CLI startup** (`Mix_Archive_Manager.sh`) no longer opens the MP Liquid player on macOS, Linux, or Windows. Option 3 and `L` still open it. Startup autoplay still resumes every other default player.
- **Version** is `0.4.2 Stable (Liquid)` in `VERSION` and in the fallback used when that file is missing.
- **Help** (`scripts/show_help.sh`) lists the live main menu, options 0–38, including option 36 and the first-row MP Harmony assistant. WAN2GP, Ollama, Open WebUI, DeepSeek, and Beszel remain direct commands.
- **First-run welcome** (`scripts/liquid_glass_reward.py`) greets 0.4.2 (Liquid) - [Stable Release], lists the features added since 0.4.1, gives five tips and a Mix Archive, Music Folder, and desktop-shortcut quick start, and is dated November 10, 2026 by MPlanetarian.

### macOS
- **MP Visualizer OpenGL performance** (`shaders/mp_liquid_visualizer.frag`, `scripts/liquid_player_visualizer.py`, `MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py`, `tests/test_liquid_player_visualizer_mode.py`):
  - The shader named its noise function `noise3`. That is a built-in on Apple's OpenGL, so the program never linked. The player compiled it again on every frame.
  - The function is now `valueNoise3`. A failed compile is remembered for that preset instead of being repeated.
  - The player window stays 120×40 (1094×734). The raymarch long edge is 768, so that window is drawn at 768×515 and scaled up. A full-window march is about 9 fps through MoltenVK. 768 replaces the blocky 224-wide image. `MP_VISUALIZER_GL_LONG_EDGE` still overrides the long edge.
  - Each preset is compiled on its own, so the other scenes are not left in the program. The march is 64 steps. `MP_VISUALIZER_GL_LONG_EDGE` and `MP_VISUALIZER_GL_MARCH_STEPS` override those limits.
  - PyOpenGL no longer calls `glGetError` after every command. Set `PYOPENGL_ERROR_CHECKING=1` to turn that back on.
- **MP Gui and the Liquid Player open the OpenGL host** (`MP_Gui.sh`, `scripts/launch_liquid_glass.sh`, `MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py`):
  - The macOS host is `MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py`.
  - `MP_Audio_Player_Liquid_Glass_macOS.py` remains the fallback when that OpenGL file is absent. That fallback requires Apple Silicon.
- **Focus** (`scripts/mp_gui_focus.sh`, `scripts/liquid_glass_opengl.py`):
  - A mapped window is raised through AppKit.
  - A windowless Metal process is not treated as MP Gui already being open.
  - The OpenGL host activates itself with `NSRunningApplication` when the window opens.
- **Console tab title** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_macos_console_tab_title.py`):
  - The title is written once with OSC 0, 1, and 2, then locked as Terminal's custom title on the matching tty.
  - Fullscreen window-ops are skipped on macOS so that title stays in place.
  - The player publishes its title only when the text changes.
- **Pale glass ink and the playlist overlay** (`MP_Audio_Player_Liquid_Glass_OpenGL_macOS.py`, `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `tests/test_macos_playlist_cursors.py`):
  - Pale glass uses dark ink. Retro Linux Memories keeps the charcoal pane. Ink is reapplied when the theme changes.
  - The overlay decodes 256-color ANSI, skips OSC, and erases the display without homing the cursor.
  - Font order is Menlo, Monaco, Courier New, then SFNSMono. The stream footer reads `FLOW (RIGHT TO LEFT)`. The playing row keeps one ►.
- **MP Liquid Visualizer** (`scripts/mp_liquid_visualizer_vulkan.py`, `tests/test_mp_liquid_visualizer_cross_platform.py`):
  - Homebrew `libvulkan` and MoltenVK are found before GLFW loads.
  - The process restarts with `DYLD_FALLBACK_LIBRARY_PATH` and `VK_ICD_FILENAMES`.
  - A missing Vulkan loader reports `brew install molten-vk vulkan-loader`.
- **Desktop shortcuts** (`scripts/install_desktop_shortcuts.py`, `scripts/liquid_glass_reward.py`, `install.sh`, `tests/test_desktop_shortcuts.py`):
  - The four shortcuts are MP Liquid Player, MP Mix Manager, MP Gui, and MP Liquid Visualizer.
  - The earlier MP Liquid and Mix Archive Manager shortcut names are removed.
- **Menu status line** (`Mix_Archive_Manager.sh`):
  - Uptime is read from `kern.boottime`.
  - Output volume is read from `osascript`.
  - The Traktor Kontrol S4 probe is cached.
  - The music-folder count is read in the shell from `music_folders.json`.
- **Console theme background** (`Mix_Archive_Manager.sh`, `tests/test_cli_theme_background.py`):
  - Each theme has its own dark console pane.
  - Terminals that honour OSC 11, including iTerm, receive that colour.
  - Terminal.app receives the same colour through AppleScript. The previous profile colour is restored when the manager exits.
  - MP Gui and the Liquid Player keep their own glass pane.

---

## [0.4.0 Stable] - 2026-10-24

### Added
- **New "Retro Linux Memories" Theme inspired by Classic Linux VeraCrypt GTK Styling** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `scripts/liquid_glass_metal.py`, `scripts/mix_web_companion.py`, `scripts/traktor_monitor.py`, `scripts/view_tracklist_console.sh`, `.probe_mgr.sh`, `themes/Retro_Linux_Memories_MP_Mix_Manager.theme`, `tests/test_retro_linux_memories_theme.py`):
  - **Authentic Retro Linux GTK Palette**: Accurately sampled and adapted directly from the classic VeraCrypt Linux UI screenshot (`Screenshot_20261108_040035.png`):
    - Dark Slate Gray Window Background & Wells (`#2d2d2d` / `#353535` / `#1a1e21`)
    - Classic VeraCrypt Royal Selection Blue (`#15539e` / `rgb(21, 83, 158)`)
    - Beveled Widget Borders & Steel Highlights (`#545453` / `#5b86bb`)
    - Clean White Text (`#ffffff`) and Classic Amber Title/Warning Accent (`#f0ad4e`)
  - **MP Liquid Audio Player Integration**: Full shader color matching (`u_themeA=(21, 83, 158)`, `u_themeB=(91, 134, 187)`, `u_themeC=(45, 45, 45)`) across OpenGL (`scripts/liquid_glass_opengl.py`), NVIDIA Vulkan (`scripts/liquid_glass_vulkan.py`), and macOS Metal (`scripts/liquid_glass_metal.py`).
  - **MP Audio Player Equalizer & Terminal Spectrum**: Built-in 4-tier frequency response gradient (VeraCrypt Blue -> Slate Highlight -> Amber Warning -> Pure White peak) with full alias support.
  - **Mix Archive Manager Theme Switcher & btop**: Added Option 22 in `manage_themes()`, interactive submenu selection bar in `highlight_menu_line()`, and btop theme synchronization `Retro_Linux_Memories_MP_Mix_Manager.theme`.

- **First-Load Equalizer Auto-Initialization to Flat (0 dB)** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `scripts/liquid_glass_reward.py`, `tests/test_first_run_equalizer.py`):
  - Automatically resets MP Audio Player's 10-band equalizer to Flat preset (0 dB on all bands) when MP Mix Manager loads for the first time via `python MP_Audio_Player.py --reset-eq`.
  - Added `ensure_first_run_equalizer` to `Mix_Archive_Manager.sh` startup sequence and `setup_first_run_equalizer` in `scripts/liquid_glass_reward.py`.
  - Managed via persistent marker `~/.config/mix-manager/.first_run_eq_initialized` so user custom equalizer adjustments are preserved on subsequent runs.
- **3 Brand New Presets for MP Liquid Visualizer: "Beyond Dimensions", "Particle Accelerator", and "Cosmic Universe"** (`MP_Liquid_Visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `assets/beyond_dimensions_cover.png`, `assets/particle_accelerator_cover.png`, `assets/cosmic_universe_cover.png`, `COVERS/Beyond_Dimensions_Preset.png`, `COVERS/Particle_Accelerator_Preset.png`, `COVERS/Cosmic_Universe_Preset.png`, `assets/skins/beyond_dimensions.png`, `assets/skins/particle_accelerator.png`, `assets/skins/cosmic_universe.png`, `Mix_Archive_Manager.sh`, `README.md`, `tests/test_mp_liquid_visualizer.py`):
  - **Preset 7: "Beyond Dimensions" (`beyond_dimensions`)**:
    - **Cover Art & Assets**: Sourced high-resolution 1400x1400 artwork from `/run/media/mplanetarian/WD BLACK B/MPlanetarian Bandcamp Covers/Cotangent Dimensions (In Progress) [album].png`, generating `assets/beyond_dimensions_cover.png`, `COVERS/Beyond_Dimensions_Preset.png`, and 1280x820 player skin `assets/skins/beyond_dimensions.png`.
    - **MPlanetarian Visual Inspiration**: Paired with MPlanetarian's architectural generative film *Beyond Basel 2023* (`/run/media/mplanetarian/EXT_004_P1_1TB/MPlanetarian/20231129 - BEYOND BASEL 2023： MPlanetarian [IoEpQ09v2l4].mkv`).
    - **Procedural 3D Geometry**: 4D hypersphere liquid glass core with non-linear cotangent topological wave harmonics, intersecting tesseract frame lattice rings, 4-axis braided interdimensional flux streamers, and floating octahedral rift crystals.
    - **Signature Palette**: Deep Interdimensional Void (`#070814`), Hyperdimensional Ultraviolet (`#6b11ff`), Electric Cotangent Cyan (`#00f5d4`), Non-Euclidean Prism Magenta (`#f72585`), Transdimensional Amber Gold (`#fee440`), and Luminous Quantum Pearl (`#f0f8ff`).
  - **Preset 8: "Particle Accelerator" (`particle_accelerator`)**:
    - **Cover Art & Assets**: Sourced high-resolution 3000x3000 artwork from `/run/media/mplanetarian/WD BLACK B/MPlanetarian Bandcamp Covers/Vortex Sync (Mateh's Version) [track].png`, generating 1400x1400 `assets/particle_accelerator_cover.png`, `COVERS/Particle_Accelerator_Preset.png`, and 1280x820 player skin `assets/skins/particle_accelerator.png`.
    - **MPlanetarian Visual Inspiration**: Paired with MPlanetarian's iconic magnetic collider work *TOROID (feat. Yaporigami)* (`/run/media/mplanetarian/EXT_004_P1_1TB/MPlanetarian/20191112 - TOROID (feat. Yaporigami) [_2vtEhBzPsE].mkv`).
    - **Procedural 3D Geometry**: Dual counter-rotating magnetic containment accelerator toroids in high-speed magnetic precession, central high-energy pinched plasma collision spindle with reactive audio shockwaves, quad-sector relativistic helical beam sprays, and heavy electromagnetic quadrupole focusing coils (`sdBox`).
    - **Signature Palette**: Hadron Vacuum Void (`#04050a`), Synchrotron High-Flux Blue (`#00d2ff`), Relativistic Plasma Violet (`#7928ca`), Collision Neon Pink (`#ff0080`), Cherenkov Radiation Aqua (`#00ffbf`), and Incandescent White-Hot Collision Core (`#ffffff`).
  - **Preset 9: "Cosmic Universe" (`cosmic_universe`)**:
    - **Cover Art & Assets**: Sourced high-resolution 3000x3000 artwork from `/run/media/mplanetarian/WD BLACK B/MPlanetarian Bandcamp Covers/The Cosmological Horizon (2026 Remaster) [track].png`, generating 1400x1400 `assets/cosmic_universe_cover.png`, `COVERS/Cosmic_Universe_Preset.png`, and 1280x820 player skin `assets/skins/cosmic_universe.png`.
    - **MPlanetarian Visual Inspiration**: Paired with MPlanetarian's celestial generative classic *Constellation (2012)* (`/run/media/mplanetarian/EXT_004_P1_1TB/MPlanetarian/20120811 - Constellation (2012) [ISIu20AePHw].mkv`).
    - **Procedural 3D Geometry**: Pulsating liquid glass pulsar star nucleus with high-frequency corona ripples, multi-tier cosmological event horizon accretion rings, 6-point celestial constellation web filaments, and floating diamond star nodes (`sdOctahedron`).
    - **Signature Palette**: Cosmological Abyssal Black (`#020208`), Deep Space Nebula Blue (`#0a2540`), Celestial Starlight Cyan (`#38bdf8`), Supernova Solar Gold (`#fbbf24`), Cosmic Dawn Rose Nebula (`#fb7185`), and Brilliant Stellar Diamond Core (`#fdf4ff`).
  - **Manager & Runtime Hotkeys**:
    - Added keys `[7]`, `[8]`, and `[9]` for direct runtime switching between all 9 presets.
    - Updated `manage_mp_liquid_visualizer_presets` menu in `Mix_Archive_Manager.sh` with active indicators (`★`) for all 9 presets.
    - Added unit test cases for single cover loading, ID assignments, and alias resolution in `tests/test_mp_liquid_visualizer.py`.
- **MP Visualizer Cross-Platform Support for macOS and Windows 10/11** (`scripts/mp_liquid_visualizer_vulkan.py`, `MP_Liquid_Visualizer.py`, `MP_Audio_Player.py`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `launch_liquid_visualizer.bat`, `scripts/launch_liquid_visualizer.bat`, `scripts/launch_liquid_visualizer.sh`, `scripts/launch_liquid_glass.bat`, `Mix_Archive_Manager.sh`, `bin/mix-archive-manager`, `tests/test_mp_liquid_visualizer_cross_platform.py`):
  - **macOS (Darwin & Apple Silicon MoltenVK)**:
    - Added macOS platform detection (`is_macos()`, `is_apple_silicon()`).
    - Multi-path GLFW dynamic library discovery (`/opt/homebrew/lib/libglfw.3.dylib`, `/opt/homebrew/lib/libglfw.dylib`, `/usr/local/lib/...`, and `find_library`).
    - Vulkan loader and MoltenVK discovery (`/opt/homebrew/lib/libMoltenVK.dylib`, `/opt/homebrew/lib/libvulkan.dylib`, LunarG Vulkan SDK `$VULKAN_SDK/lib/...`).
    - Seamlessly bypasses X11 dependencies and avoids setting `GLFW_PLATFORM_X11` on macOS.
    - Integrated MoltenVK portability flags: enables `VK_KHR_portability_enumeration` and sets `VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR` during instance creation.
    - Dynamically enables `VK_KHR_portability_subset` device extension when supported by the physical device.
    - Bound `glfwGetCocoaWindow` for native macOS window handle support (`cocoa_window()`).
    - Adapted mouse pointer tracking (`root_pointer()`) using window offset and cursor position.
  - **Windows 10 & 11 Support**:
    - Added Windows 10/11 platform detection (`is_windows()`, `is_windows_10_or_11()`).
    - Enhanced Windows GLFW library lookup (`glfw3.dll`, `glfw.dll`, `Program Files\glfw\lib`, `LOCALAPPDATA\Programs\glfw`).
    - Enhanced Windows Vulkan loader lookup (`vulkan-1.dll`, `System32\vulkan-1.dll`, `SysWOW64\vulkan-1.dll`, `$VULKAN_SDK\Bin\vulkan-1.dll`).
    - Bound `glfwGetWin32Window` for native Win32 window handle support (`win32_window()`).
    - Win32 `GetCursorPos` query for absolute cursor position with fallback.
    - Shift+K player termination using `taskkill` and PowerShell on Windows.
    - Created standalone double-clickable Windows launchers `launch_liquid_visualizer.bat` and `scripts/launch_liquid_visualizer.bat`.
    - Updated `scripts/launch_liquid_glass.bat` to automatically start MP Liquid Visualizer on Windows.
    - Added PowerShell CIM instance query fallback for single-instance protection on Windows when `pgrep` is absent.
  - **Direct CLI & Manager Shortcuts**:
    - Added `visualizer` command handling to `bin/mix-archive-manager` and `Mix_Archive_Manager.sh`.
    - Made `scripts/launch_liquid_visualizer.sh` fully cross-platform with Homebrew PATH and python3 discovery.
  - **Comprehensive Test Suite**:
    - Created `tests/test_mp_liquid_visualizer_cross_platform.py` with 18 unit tests covering Windows 10/11 detection, macOS Apple Silicon detection, DLL candidate resolution, Win32/Cocoa window handles, MoltenVK portability flags, and launcher scripts.
- **Automatic Random Visualizer on MP Liquid Glass Player Launch** (`MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass_macOS.py`, `MP_Audio_Player.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/launch_liquid_glass.sh`, `Mix_Archive_Manager.sh`, `tests/test_auto_start_visualizer_on_liquid_player.py`):
  - **Auto-Start Visualizer**: Whenever the user launches MP Liquid Glass Player (via Desktop shortcut `~/Desktop/MP_Liquid.desktop`, terminal launcher `launch-liquid-glass`, manager Option `3`, hotkey `[L]` / `[l]`, `--liquid`, or by directly executing `MP_Audio_Player_Liquid_Glass*.py`), an MP Liquid Visualizer window (`MP_Liquid_Visualizer.py` / `scripts/mp_liquid_visualizer_vulkan.py`) is automatically started with a randomly selected preset.
  - **Random Preset Engine**: Enhanced `get_active_preset()` and CLI arguments with `--random` / `--random-preset` (`--preset random`, `rand`, `rnd`, `any`, `shuffle`) to sample uniformly at runtime across all 6 official presets (Cosmic Dreamworlds, Orbital Void Reflux, Dreamworlds Ultra MPlanetarian, Infinite Complexity, Sensorium Interface, and Stream of Frequency).
  - **Single-Instance Protection**: Built-in process guard prevents multiple visualizer windows from spawning if an instance is already running.
  - **Test Suite Safety Flag**: Added `--no-visualizer` CLI flag across all host scripts and test suites so automated tests, benchmarks, and headless embed checks can run without spawning external Vulkan windows.
  - **Manager Preset Menu Update**: Added Stream of Frequency (`6`) and Random Mode (`7`) to `manage_mp_liquid_visualizer_presets` in `Mix_Archive_Manager.sh`.
- **Stream of Frequency Global Theme & Visualizer Preset 5** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `scripts/liquid_glass_metal.py`, `scripts/mp_software_bridge.py`, `scripts/view_tracklist_console.sh`, `themes/Stream_of_Frequency_MP_Mix_Manager.theme`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `scripts/mp_liquid_visualizer_vulkan.py`, `COVERS/Stream_of_Frequency_Preset.png`, `assets/stream_of_frequency_cover.jpg`, `tests/test_mp_liquid_visualizer.py`):
  - **Official Cover Art Palette & Global Theme Synchronization**: Built around the official cover `/home/mplanetarian/Documents/Stream_of_Frequency.jpg` and the Stream of Frequency Equalizer Effect (`stream_of_frequency_eq`). Selecting the Stream of Frequency theme in the MP Mix Manager configuration (`Option 20` in Themes) immediately and globally synchronizes the MP Audio Player, the animated dynamic equalizer, the MP Liquid Visualizer, the Liquid Glass transparent player skins, the tracklist viewer console, and the btop system monitor theme (`themes/Stream_of_Frequency_MP_Mix_Manager.theme`).
  - **MP Liquid Visualizer — Preset 5: "Stream of Frequency"**:
    - **Single Cover Art Integration**: Created high-resolution 1:1 aspect ratio assets `COVERS/Stream_of_Frequency_Preset.png` and `assets/stream_of_frequency_cover.jpg` mapped dynamically with spherical 3D ray-sampling and chromatic dispersion across the environment.
    - **Procedural 3D Raymarching Scene**: Engineered a custom procedural scene in `shaders/mp_liquid_visualizer.frag` and recompiled to SPIR-V bytecode (`shaders/mp_liquid_visualizer.frag.spv`) featuring a central resonant liquid glass frequency core, dual harmonic resonance rings, 5-tier audio ribbons modulated by live FFT frequency bands flowing right-to-left, and orbiting floating crystal octahedron nodes.
    - **Vibrant Color Grading**: Uses authentic palette tones: Electric Stream Cyan (`#00f2ff`), Frequency Emerald (`#4cd97b`), Solar Wave Gold (`#f5ed6a`), Deep Stream Indigo (`#1f2060`), Peak Crimson (`#f02020`), and Electric Violet (`#974ff9`).
    - **Runtime Switching**: Integrated hotkey `[5]`, CLI `--preset 5`, `--preset stream_of_frequency`, and automatic visualizer preset activation when the theme is selected.
- **v0.4 Release Celebration — Free Track, Collectible Art Piece & First-Run Pre-Load** (`MP_SAMPLES/`, `scripts/liquid_glass_reward.py`, `Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `MP_loader.mp4`, `tests/test_first_run_sample_track.py`, `tests/test_loader_video.py`):
  - **MP_loader.mp4 Audio Update**: Updated the startup loader video audio track using the audio from `10. Ecology of the Earth (MPlanetarian Cosmic Dreamworlds Remake).wav` with high-quality 320k stereo AAC and smooth outro fade.
  - **MP_SAMPLES Showcase Directory**: Created `MP_SAMPLES/` directory in the repository root to celebrate the v0.4 release.
  - **Free Celebratory Track**: Converted the WAV to a pristine 24-bit 48kHz FLAC file: `MP_SAMPLES/MPlanetarian - Ecology of the Earth (Cosmic Dreamworlds Remake).flac` (16:54 duration) with embedded metadata and cover art.
  - **Free Collectible Art Piece**: Converted `Documents/Cosmic_Dreamworlds.jpg` to high-resolution PNG: `MP_SAMPLES/Cosmic_Dreamworlds_Cover.png`.
  - **First-Run Pre-Load System**: On the very first run of MP Mix Archive Manager or MP Audio Player on a new system, the celebratory track is preloaded into the player ready at `00:00` in stopped state so the user can press play whenever they are ready. Controlled by persistent marker `~/.config/mix-manager/.first_run_sample_loaded` so it only happens once and never overwrites user choices on subsequent runs.
  - **Warm Welcome Banner**: Enhanced the first-run welcome presentation in `scripts/liquid_glass_reward.py` announcing the desktop shortcut reward, preloaded celebratory track, and free collectible art piece.
- **MP_Liquid_Visualizer — Preset 5: "Sensorium Interface" (MPlanetarian Earth Flipper & Sensorium_Interface.jpeg)** (`MP_Liquid_Visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `assets/sensorium_interface_cover.png`, `COVERS/Sensorium_Interface_Preset.png`, `assets/skins/sensorium_interface.png`, `themes/Sensorium_Interface_MP_Mix_Manager.theme`, `MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `tests/test_mp_liquid_visualizer.py`, `tests/test_liquid_glass_skin.py`, `tests/test_equalizer_presets.py`):
  - **Cover Image & Preset Assets**: Generated high-resolution 1400x1400 cover art assets `COVERS/Sensorium_Interface_Preset.png` and `assets/sensorium_interface_cover.png` from `/run/media/mplanetarian/GAMES1/DATA_DRIVE_MOVED/MPlanetarian Bandcamp Covers/Sensorium_Interface.jpeg` (3000x3000 JPEG), adhering strictly to the one-Bandcamp-cover-per-preset architecture.
  - **MPlanetarian "Earth Flipper" Primordial Celestial Architecture**: Inspired by MPlanetarian's 2022 generative masterpiece with Yaporigami (*Earth Flipper* [KE8zIfDsloA] from `/run/media/mplanetarian/EXT_004_P1_1TB/MPlanetarian/`):
    - **Extremely Complicated & Monumentally Slow Moving**: Engineered with slow, majestic gyroscopic precession and vast dimensional rotational speed, conveying immense cosmic scale, intricate depth, and non-Euclidean topological complexity.
    - **Tri-Axis Interlocking Gyroscopic Liquid Core**: Renders 3 concentric, counter-rotating liquid glass toroids with hydraulic harmonic wave distortion and an inner pulsing celestial liquid glass orb.
    - **Braided Quad-Helical Primordial Liquidstreams**: 4 interwoven celestial liquidstreams winding along the spine, undulating with low-mid audio frequencies.
    - **6 Articulating Biomech Flipper Wings & Origami Sensorium Shards**: Symmetrical hexagonal cyber-armor plates flanking the core that slowly flap and articulate in sync with music transients.
    - **Relativistic Accretion Halos & Resonance Rings**: Multi-tier planetary rings with fine radial ripples glowing in bioluminescent cyber cyan, sensorium rose quartz, and radiant orchid.
    - **3D Dynamic Celestial Environment**: Ray-projected celestial void with chromatic dispersion, undulating auroral sheets of Earth Flipper jade and iridescent mauve, and twinkling quantum starlight.
  - **MP Manager Theme ("Sensorium Interface")**: Created `themes/Sensorium_Interface_MP_Mix_Manager.theme` (BTOP theme) and added Option 21 to `manage_themes()` in `Mix_Archive_Manager.sh` featuring Iridescent Sensorium Pearl (`#f2e8f5`), Bioluminescent Cyber Cyan (`#00f5ff`), Sensorium Rose Quartz (`#f0a8d8`), Earth Flipper Celestial Jade (`#769371`), and Deep Obsidian Void (`#0a0b12`).
  - **MP Liquid Player Theme / Liquid Glass Skin ("Sensorium Interface")**: Created 1280x820 RGBA cover-wrapped skin `assets/skins/sensorium_interface.png` and integrated it across `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, and `scripts/liquid_glass_metal.py`.
  - **Equalizer Preset ("Sensorium Interface")**: Added custom 10-band DSP graphic equalizer preset tuned for deep IDM sub-bass weight, controlled mids, and sparkling crystalline highs (`[+4.5, +4.0, +1.5, -1.0, +0.5, +1.5, +2.5, +3.5, +4.0, +3.0] dB`), selectable via `--eq-preset "Sensorium Interface"` and interactive Equalizer HUD cycling.
  - **Runtime Preset Switching & Hotkeys**: Integrated hotkey `[5]`, CLI flags `--preset 5`, `--preset sensorium_interface`, `--set-preset`, `--list-presets`, and updated `Mix_Archive_Manager.sh` preset management menu with dynamic active indicators (`★`).
  - **Full Test Suite Verification**: Unit tests added to `tests/test_mp_liquid_visualizer.py`, `tests/test_liquid_glass_skin.py`, and `tests/test_equalizer_presets.py`.
- **MP Liquid Visualizer Branding & Liquid Glass Font / Text Overhaul** (`MP_Audio_Player.py`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `README.md`, `tests/test_liquid_visualizer_and_font_customization.py`):
  - **Renamed to MP Liquid Visualizer**: Updated all user-facing documentation, README badges, architecture tables, hotkey hints (`[Z] MP Liquid Visualizer` / `[Z] Liquid Vis`), CLI arguments, and notifications across MP Audio Player and the manager suite.
  - **Disabled Bold Text in MP Liquid Glass Player**: Removed forced bold text weights from `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, and `MP_Audio_Player.py` (`dim_s = DIM`, `hint_col = WHITE if is_lg else DIM`, unbolded playlist lines), restoring clean, crisp typography.
  - **Fluid Liquid Text Styling**: Upgraded VTE/GTK CSS styling with subtle luminous liquid refraction (`text-shadow: 0 0 3px rgba(0, 229, 255, 0.40), 0 0 7px rgba(176, 88, 255, 0.20)`), Cairo slight hinting for organic curved glyphs, and dynamic Liquid Glass banners in standard, mini, and micro player views.
  - **Configurable Default Monospace Font System**: Forced default font to `Monospace 11` (normal weight, unbolded), and added multi-font support with `--font`, `--set-font`, and `--list-fonts` CLI flags (with curated support for `Monospace`, `Fira Code`, `Hack`, `Adwaita Mono`, and `Liberation Mono`).
- **MP_Liquid_Visualizer — Preset 4: "Infinite Complexity"** (`MP_Liquid_Visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `assets/infinite_complexity_cover.png`, `COVERS/Infinite_Complexity_Preset.png`, `Mix_Archive_Manager.sh`, `tests/test_mp_liquid_visualizer.py`):
  - **Cover_v4.png Integration**: Linked and formatted high-resolution 1400x1400 cover art assets `COVERS/Infinite_Complexity_Preset.png` and `assets/infinite_complexity_cover.png` from `/run/media/mplanetarian/GAMES1/DATA_DRIVE_MOVED/MPlanetarian Bandcamp Covers/Cover_v4.png`, preserving the strict one-cover-per-preset architecture.
  - **MPlanetarian "Mirrored Madness" Aesthetic & Motion**: Inspired exclusively by MPlanetarian's *Neo Shibuya 2023: Mirrored Madness* (`/run/media/mplanetarian/GAMES1/LINUX_DATA/Videos/Neo Shibuya 2023： Mirrored Madness [CusCJn9p0dk].mkv`):
    - **Exact Authentic Color Palette**: Faithfully matches the exact colors of `Cover_v4.png`: Deep Obsidian Space Void (`#020103`), Dark Emerald Forest Cyan-Teal (`#1c2e28`), High-Voltage Neon Mint Green (`#2ae277`), Radiant Mint Glow (`#95d6aa`), Celestial Rose Quartz / Lavender Mist (`#f8e8fc`), Ethereal Rose Quartz (`#e4b1c1`), Deep Mystic Magenta (`#813952`), Cosmic Sage Mist (`#7d9184`), and Warm Starlight Gold (`#e7e5c3`).
    - **Entirely 3D & Dynamic Background**: The cover image is mapped dynamically across a 3D ray-projected manifold with dynamic depth parallax, chromatic dispersion ray-sampling, a receding MPlanetarian mirrored perspective lattice, and floating quantum starlight dust drifting in 3D space.
    - **3D Cyber Organism on Top of Dynamic Background**: Renders an unfurling cybernetic organism with segmented vertebrae capsules, biomorphic rib ripples, incandescent mirrored kinetic rings & portal arcs, 4 sinuous helical tendrils, and deconstructed origami hex plates.
    - **Fast & Strange Movements Synchronized to Music**: Executes sharp non-linear snap turns and micro-glitch saccades on audio peaks, alongside non-Euclidean space foldings and kaleidoscopic deconstructions, staying locked to the music and freezing when stopped.
    - **Gradual Dynamic Time Frame Modulation**: Implemented harmonic non-linear time frame modulation in `AudioTelemetryBridge` and `VisualizerUBO` that gradually accelerates tempo into hyper-complex motion and decelerates smoothly into hypnotic liquid suspension over time.
  - **Runtime Preset Switching & Hotkeys**: Integrated hotkey `[4]`, CLI flags `--preset 4`, `--preset infinite_complexity`, `--set-preset`, `--list-presets`, and updated `Mix_Archive_Manager.sh` preset management menu with dynamic active indicators (`★`).
  - **Full Test Verification**: Added unit tests in `tests/test_mp_liquid_visualizer.py` verifying preset registration, cover art loading, and dynamic time frame modulation.
- **Stream of Frequency Equalizer (Dynamic Spectrogram)** (`MP_Audio_Player.py`, `tests/test_stream_of_frequency_equalizer.py`, `tests/test_micro_player_eq.py`):
  - **Dynamic Spectrogram Audio River**: Implemented a new equalizer visualizer effect `stream_of_frequency_eq` that renders audio frequency bands as a continuous river flowing from Right to Left, mapping 24 frequency bands across the display vertical height (sub-bass up to high treble).
  - **Rainbow Coloured Particles & Undulating Waves**: Audio frequencies emit streams of rainbow-colored particles that ride traveling sine waves (`A * sin(omega * t + phi - k * x)`) flowing continuously down the river. Transients and beat drops trigger sparkling particle crest bursts.
  - **Continuous Rainbow Hue Evolution**: Precomputed a high-efficiency 360-degree rainbow ANSI lookup table (`_RAINBOW_ANSI_BOLD`, `_RAINBOW_ANSI_NORMAL`, `_RAINBOW_ANSI_DIM`, `_RAINBOW_ANSI_FAINT`) to generate smooth, endless rainbow wave color rotations with sub-millisecond execution (< 0.1ms per frame).
  - **New Default Equalizer for Default, Mini, and Micro Modes**: Configured `stream_of_frequency_eq` as the primary default equalizer style across all modes (Normal 120-column view, Mini 74x14 player, and Micro 74x7 player). Micro mode features a dedicated 14-character live rainbow wave particle stream in the status header.
- **Liquid Glass Tracklist Viewer** (`MP_Audio_Player.py`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass_macOS.py`, `Mix_Archive_Manager.sh`, `tests/test_tracklist_viewer_actions.py`, `tests/test_liquid_glass_player_features.py`):
  - **Seamless Liquid Glass Rendering for Tracklist Viewer**: When pressing `T` inside the MP Liquid Player (`MP_Audio_Player_Liquid_Glass*` or with `MP_LIQUID_GLASS=1`), the Tracklist Viewer opens rendered directly inside a dedicated Liquid Glass container window with dark glass panel well shading instead of spawning an external plain terminal.
  - **Dynamic Theme and Audio Integration**: Propagates `--theme` and `--audio` paths to the Liquid Glass tracklist session, matching the active player theme and enabling instant Spek generation (`[G]`) and audio extraction (`[D]`).
  - **Cross-Backend Support**: Fully supported across OpenGL (`MP_Audio_Player_Liquid_Glass_OpenGL.py`), NVIDIA Vulkan (`MP_Audio_Player_Liquid_Glass.py`), and Apple Silicon Metal (`MP_Audio_Player_Liquid_Glass_macOS.py`).
- **MP_Liquid_Visualizer — Preset 3: "Dreamworlds Ultra (MPlanetarian Biomech)"** (`MP_Liquid_Visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `assets/dreamworlds_ultra_mplanetarian_cover.png`, `COVERS/Dreamworlds_Ultra_MPlanetarian_Preset.png`, `Mix_Archive_Manager.sh`, `tests/test_mp_liquid_visualizer.py`):
  - **Start_Menu_Icon Single Cover Asset Integration**: Converted `/home/mplanetarian/Documents/Dreamworlds_Ultra_7.2.11_Theme/Start_Menu_Icon.png` into high-resolution 1200x1200 RGB PNG assets `COVERS/Dreamworlds_Ultra_MPlanetarian_Preset.png` and `assets/dreamworlds_ultra_mplanetarian_cover.png`, preserving the strict one-cover-per-preset architecture.
  - **MPlanetarian "Xinri D - Yaporigami" Procedural 3D Visualizer Architecture**: Directly inspired by MPlanetarian's seminal visual work on *XINRI D - YAPORIGAMI*:
    - **Aerodynamic Biomechanical Fuselage & Engine Spine**: Organic capsule and bulbous engine pod (`sdCapsule` + `sdSphere` with smooth `smin` blending) rendered with frosted pearlescent glass, translucent caustics, and audio-reactive ripple displacement.
    - **Twin High-Intensity Cyan Reactor Rings & Drive Gimbals**: Emissive neon rings and gimbals orbiting perpendicularly to the spine axis with dynamic bass/beat pulses and white-hot laser centers.
    - **Helical Origami Sakura Streamers & Ribbons**: Triple continuous helical ribbon tendrils wrapping around the fuselage that expand and curl in real time synced to bass phases and mid frequencies.
    - **Floating Chrome Hexagon Armor Plates & Origami Shards**: Precision hexagonal prism tiles (`sdHexPrism`) and tech plates revolving in dynamic 3D orbits around the core, reacting to transients.
    - **High-Tech Slate Cyber Studio & Laser Shafts**: Atmospheric gradient sky with refracted `Start_Menu_Icon.png` textures, intense diagonal laser light beams, and floating nano-dust origami particles.
  - **Runtime Preset Switching & Hotkeys**: Integrated hotkey `[3]`, CLI flags `--preset dreamworlds_ultra_mplanetarian`, `--set-preset`, `--list-presets`, and updated `Mix_Archive_Manager.sh` preset management menu with dynamic active indicators (`★`).
  - **Unit Test Verification**: Added comprehensive test coverage in `tests/test_mp_liquid_visualizer.py`.

- **MP_Liquid_Visualizer — Preset 2: "Orbital Void Reflux"** (`MP_Liquid_Visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `assets/orbital_void_reflux_cover.png`, `COVERS/Orbital_Void_Reflux_Preset.png`, `Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_mp_liquid_visualizer.py`):
  - **Single Bandcamp Cover Preset**: Generated 1200x1200 RGB cover art assets `COVERS/Orbital_Void_Reflux_Preset.png` and `assets/orbital_void_reflux_cover.png` from `/home/mplanetarian/Desktop/Cover_WMI_V2.png`, adhering strictly to the one-Bandcamp-cover-per-preset policy.
  - **Procedural 3D Singularity & Ergosphere Shader**: Extended `shaders/mp_liquid_visualizer.frag` and recompiled to SPIR-V bytecode (`shaders/mp_liquid_visualizer.frag.spv`) using `libshaderc_shared.so.1`. Features a gravitational singularity core with fluid reflux satellites, dual-tier relativistic accretion rings, helical axial flux jets, kinetic polyhedral shards, and relativistic gravitational lensing warping `Cover_WMI_V2.png` into concentric photon ring arcs.
  - **Dynamic Preset Switching & Texture Re-upload**: Added runtime preset hotkey switching (`[1]` and `[2]`), `--preset orbital_void_reflux`, `--set-preset`, and `--list-presets`. Vulkan texture descriptor dynamically re-uploads the cover art to GPU device-local memory on switch.
  - **Mix Manager & Suite Integration**: Integrated Preset 2 into `manage_mp_liquid_visualizer_presets()` and `launch_mp_liquid_visualizer()` in `Mix_Archive_Manager.sh` and `MP_Audio_Player.py` with dynamic active preset indicators (`★`).
- **New Skin for MP Liquid Glass Player: "Orbital Void Reflux"** (`assets/skins/orbital_void_reflux.png`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `scripts/liquid_glass_opengl.py`, `shaders/liquid_glass_gl.frag`, `Mix_Archive_Manager.sh`, `MP_Audio_Player.py`):
  - **1280x820 Liquid Glass Skin**: Generated `assets/skins/orbital_void_reflux.png` from `/home/mplanetarian/Desktop/Cover_WMI_V2.png` cropped and aligned for the liquid glass player window.
  - **Electric Reflux & Nebula Flux Palette**: Electric Reflux Cyan (`#00f0ff`), Nebula Flux Magenta (`#ff007f`), Deep Void Violet (`#9d4edd`), and Obsidian caustics.
  - **Window Dragging Fix**: Resolved window repositioning issues when running via desktop shortcuts or external launchers by attaching root pointer drag handlers to the embedded terminal, adding Alt/Super modifier dragging across the entire client area, and correcting top bezel drag margin hit-testing.

### Fixed
- **MP Liquid Visualizer — Fullscreen Multi-Desktop Focus Persistence & Dual Fullscreen Support** (`scripts/mp_liquid_visualizer_vulkan.py`, `tests/test_mp_liquid_visualizer.py`):
  - **Disabled Auto-Iconify on Focus Loss**: Explicitly configured `GLFW_AUTO_ICONIFY = GLFW_FALSE` (`0x00020006`) as both a creation window hint and runtime window attribute so the visualizer does not automatically minimize/disappear when clicking away to another monitor, workspace, or application.
  - **Simultaneous Dual Fullscreen Visualizers**: Allowed two or more MP Visualizers to remain in fullscreen mode at the same time across different monitors without minimizing each other upon mouse focus shift.
  - **Window Floating & Auto-Restore Guard**: Enabled `GLFW_FLOATING` while in fullscreen mode to keep the visualizer properly surfaced above the desktop background, and added an iconify callback and event polling guard to immediately restore the window if externally minimized while fullscreen.
  - **Multi-Monitor Bounding Overlap Geometry**: Updated `_current_monitor()` to compute the maximum intersection overlap area across all active displays, ensuring accurate monitor targeting and video mode selection across multi-screen setups.

### Changed
- **Pristine Clear Liquid Glass Bezel — Removed Edge Speckles & White Noise Patterns** (`shaders/liquid_glass.frag`, `shaders/liquid_glass.frag.spv`, `shaders/liquid_glass_metal.metal`, `tests/test_liquid_glass_player_features.py`):
  - **Eliminated Outer Rim Speckle Noise**: Removed procedural pseudo-random `bead` and `glint` hash noise speckles and fixed-offset echoes from the outer edge of the bezel in both Vulkan (`shaders/liquid_glass.frag`) and Metal (`shaders/liquid_glass_metal.metal`) shaders.
  - **Recompiled SPIR-V Bytecode**: Recompiled `shaders/liquid_glass.frag.spv` to ensure crystal-clear, smooth specular reflections without white stipple or noise patterns across the 7px bezel.
  - **Automated Regression Verification**: Added unit test `test_clear_bezel_outer_edge_no_speckles` in `tests/test_liquid_glass_player_features.py` to prevent future bezel noise regressions across all shader backends.
- **Dynamic Theme Synchronization for Tracklist Viewer** (`scripts/view_tracklist_console.sh`, `Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_tracklist_viewer_actions.py`):
  - **Full Multi-Theme Color Palette Support**: Updated `scripts/view_tracklist_console.sh` with `set_theme_colors()` and `load_tracklist_theme()` supporting all MP Mix Archive Manager and MP Audio Player themes (`dreamworlds`, `dreamworlds_ultra`, `cosmic_supernova`, `dreamworlds_liquid`, `dynamic_rainbow_liquid_glass`, `cosmic_dreamworlds_radioactive`, `cosmic_dreamworlds`, `orbital_void_reflux`, `dreamworlds_neon`, `retro_studio`, `cyber_neon`, `phosphor`, `ice_cold`, `retro_terminal`, `cyberpunk`, `dracula`, `nord`, `matrix`, `solarized`, `tokyo`, `monokai`, `gruvbox`, `emerald`, and `classic`).
  - **Automatic & Live Theme Synchronization**: Reads the active user-selected theme from `~/.config/mix-manager/theme`, `--theme` CLI flag, or `MP_TRACKLIST_THEME` / `CURRENT_THEME` environment variables, and automatically re-renders in real time if the theme is changed while the Tracklist Viewer window is open.
  - **In-Viewer Theme Cycling (`[T]` Key)**: Added `[T]` shortcut in the Tracklist Viewer to cycle through themes and persist the selection to `~/.config/mix-manager/theme`, alongside displaying the active theme name in the viewer header.
- **MP Liquid Glass Audio Player — Reduced Bezel Width by 86% for Default Window, Mini, and Micro Modes** (`scripts/liquid_glass_vulkan.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_metal.py`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass_macOS.py`, `tests/test_liquid_glass_player_features.py`, `tests/test_liquid_glass_windows.py`):
  - **86% Bezel Width Reduction**: Reduced the liquid glass bezel border width by 86% (from 52px down to 7px: `52 * (1 - 0.86) = 7.28 ≈ 7px`) across all Vulkan, OpenGL, and Apple Silicon Metal renderers.
  - **Support Across Default Window, Mini, and Micro Modes**:
    - **Default Window (NORMAL)**: Streamlined border around the 120x40 terminal, tightening outer dimensions from 1184x824 down to 1094x734 for a modern ultra-slim aesthetic.
    - **Mini Mode (MINI)**: Scaled border around the 74x14 terminal, reducing window footprint down to 680x266 while maximizing usable content area.
    - **Micro Mode (MICRO)**: Scaled border around the compact 74x7 terminal, reducing window footprint down to 680x140 and eliminating bezel bulk.
  - **Hit Testing & Terminal Docking**: Updated inner well geometry, window resize/restore handlers, top bezel drag regions, and close button hit areas to match the streamlined 7px bezel.

### Added
- **New "Retro Linux Memories" Theme inspired by Classic Linux VeraCrypt GTK Styling** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `scripts/liquid_glass_metal.py`, `scripts/mix_web_companion.py`, `scripts/traktor_monitor.py`, `scripts/view_tracklist_console.sh`, `.probe_mgr.sh`, `themes/Retro_Linux_Memories_MP_Mix_Manager.theme`, `tests/test_retro_linux_memories_theme.py`):
  - **Authentic Retro Linux GTK Palette**: Accurately sampled and adapted directly from the classic VeraCrypt Linux UI screenshot (`Screenshot_20261108_040035.png`):
    - Dark Slate Gray Window Background & Wells (`#2d2d2d` / `#353535` / `#1a1e21`)
    - Classic VeraCrypt Royal Selection Blue (`#15539e` / `rgb(21, 83, 158)`)
    - Beveled Widget Borders & Steel Highlights (`#545453` / `#5b86bb`)
    - Clean White Text (`#ffffff`) and Classic Amber Title/Warning Accent (`#f0ad4e`)
  - **MP Liquid Audio Player Integration**: Full shader color matching (`u_themeA=(21, 83, 158)`, `u_themeB=(91, 134, 187)`, `u_themeC=(45, 45, 45)`) across OpenGL (`scripts/liquid_glass_opengl.py`), NVIDIA Vulkan (`scripts/liquid_glass_vulkan.py`), and macOS Metal (`scripts/liquid_glass_metal.py`).
  - **MP Audio Player Equalizer & Terminal Spectrum**: Built-in 4-tier frequency response gradient (VeraCrypt Blue -> Slate Highlight -> Amber Warning -> Pure White peak) with full alias support.
  - **Mix Archive Manager Theme Switcher & btop**: Added Option 22 in `manage_themes()`, interactive submenu selection bar in `highlight_menu_line()`, and btop theme synchronization `Retro_Linux_Memories_MP_Mix_Manager.theme`.

- **MP_Liquid_Visualizer — Preset 5: "Sensorium Interface" (MPlanetarian Earth Flipper & Sensorium_Interface.jpeg)** (`MP_Liquid_Visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.frag.spv`, `assets/sensorium_interface_cover.png`, `COVERS/Sensorium_Interface_Preset.png`, `assets/skins/sensorium_interface.png`, `themes/Sensorium_Interface_MP_Mix_Manager.theme`, `MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `tests/test_mp_liquid_visualizer.py`, `tests/test_liquid_glass_skin.py`, `tests/test_equalizer_presets.py`):
  - **Cover Image & Preset Assets**: Generated high-resolution 1400x1400 cover art assets `COVERS/Sensorium_Interface_Preset.png` and `assets/sensorium_interface_cover.png` from `/run/media/mplanetarian/GAMES1/DATA_DRIVE_MOVED/MPlanetarian Bandcamp Covers/Sensorium_Interface.jpeg` (3000x3000 JPEG), adhering strictly to the one-Bandcamp-cover-per-preset architecture.
  - **MPlanetarian "Earth Flipper" Primordial Celestial Architecture**: Inspired by MPlanetarian's 2022 generative masterpiece with Yaporigami (*Earth Flipper* [KE8zIfDsloA] from `/run/media/mplanetarian/EXT_004_P1_1TB/MPlanetarian/`):
    - **Extremely Complicated & Monumentally Slow Moving**: Engineered with slow, majestic gyroscopic precession and vast dimensional rotational speed, conveying immense cosmic scale, intricate depth, and non-Euclidean topological complexity.
    - **Tri-Axis Interlocking Gyroscopic Liquid Core**: Renders 3 concentric, counter-rotating liquid glass toroids with hydraulic harmonic wave distortion and an inner pulsing celestial liquid glass orb.
    - **Braided Quad-Helical Primordial Liquidstreams**: 4 interwoven celestial liquidstreams winding along the spine, undulating with low-mid audio frequencies.
    - **6 Articulating Biomech Flipper Wings & Origami Sensorium Shards**: Symmetrical hexagonal cyber-armor plates flanking the core that slowly flap and articulate in sync with music transients.
    - **Relativistic Accretion Halos & Resonance Rings**: Multi-tier planetary rings with fine radial ripples glowing in bioluminescent cyber cyan, sensorium rose quartz, and radiant orchid.
    - **3D Dynamic Celestial Environment**: Ray-projected celestial void with chromatic dispersion, undulating auroral sheets of Earth Flipper jade and iridescent mauve, and twinkling quantum starlight.
  - **MP Manager Theme ("Sensorium Interface")**: Created `themes/Sensorium_Interface_MP_Mix_Manager.theme` (BTOP theme) and added Option 21 to `manage_themes()` in `Mix_Archive_Manager.sh` featuring Iridescent Sensorium Pearl (`#f2e8f5`), Bioluminescent Cyber Cyan (`#00f5ff`), Sensorium Rose Quartz (`#f0a8d8`), Earth Flipper Celestial Jade (`#769371`), and Deep Obsidian Void (`#0a0b12`).
  - **MP Liquid Player Theme / Liquid Glass Skin ("Sensorium Interface")**: Created 1280x820 RGBA cover-wrapped skin `assets/skins/sensorium_interface.png` and integrated it across `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, and `scripts/liquid_glass_metal.py`.
  - **Equalizer Preset ("Sensorium Interface")**: Added custom 10-band DSP graphic equalizer preset tuned for deep IDM sub-bass weight, controlled mids, and sparkling crystalline highs (`[+4.5, +4.0, +1.5, -1.0, +0.5, +1.5, +2.5, +3.5, +4.0, +3.0] dB`), selectable via `--eq-preset "Sensorium Interface"` and interactive Equalizer HUD cycling.
  - **Runtime Preset Switching & Hotkeys**: Integrated hotkey `[5]`, CLI flags `--preset 5`, `--preset sensorium_interface`, `--set-preset`, `--list-presets`, and updated `Mix_Archive_Manager.sh` preset management menu with dynamic active indicators (`★`).
  - **Full Test Suite Verification**: Unit tests added to `tests/test_mp_liquid_visualizer.py`, `tests/test_liquid_glass_skin.py`, and `tests/test_equalizer_presets.py`.
- **New Skin for MP Liquid Glass Player: "Cosmic Dreamworlds"** (`assets/skins/cosmic_dreamworlds.png`, `assets/cosmic_dreamworlds_cover.jpg`, `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `scripts/liquid_glass_metal.py`, `Mix_Archive_Manager.sh`, `scripts/traktor_monitor.py`, `mix_web_companion.py`, `scripts/mix_web_companion.py`, `tests/test_liquid_glass_skin.py`):
  - **Cover-Wrapped Liquid Glass Skin Image**: Generated 1280x820 RGBA skin image `assets/skins/cosmic_dreamworlds.png` from `assets/cosmic_dreamworlds_cover.jpg` cropped to the native 1280x820 liquid glass player window geometry.
  - **Crystal-Clear Liquid Refraction & Cosmic Glow**: Full integration with the OpenGL liquid glass pipeline with cover texture sampling, fluid wave refraction, and specular highlight caustics.
  - **Cosmic Palette & Spectrum**: Styled with vivid Cosmic Magenta (`#e80d68`), Crystalline Turquoise/Cyan (`#00f5d4`), and Nebula Violet (`#b058ff`), with multi-stop dynamic equalizer color mapping.
  - **Theme Cycling & Suite Integration**: Cycleable via `[C]` key in `MP_Audio_Player.py` under the display name `Cosmic Dreamworlds (Liquid Glass Skin)`, selectable as Option 18 in `Mix_Archive_Manager.sh`, and synchronized across the Web Companion and Traktor Monitor.
  - **Unit Test Coverage**: Full test verification in `tests/test_liquid_glass_skin.py`.
- **MP_Liquid_Visualizer — Native Vulkan 3D Audio-Reactive Visualizer (`Cosmic Dreamworlds (Liquid)` Preset 1)** (`MP_Liquid_Visualizer.py`, `scripts/mp_liquid_visualizer_vulkan.py`, `scripts/launch_liquid_visualizer.sh`, `shaders/mp_liquid_visualizer.vert`, `shaders/mp_liquid_visualizer.frag`, `shaders/mp_liquid_visualizer.vert.spv`, `shaders/mp_liquid_visualizer.frag.spv`, `assets/cosmic_dreamworlds_cover.jpg`, `COVERS/Cosmic_Dreamworlds_Liquid_Preset.png`, `MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `tests/test_mp_liquid_visualizer.py`):
  - **Built from the Ground Up in Vulkan**: Native Vulkan 1.x graphics pipeline (`libvulkan` + `GLFW`) featuring a UBO (`VisualizerUBO`, `binding = 0`) + combined 2D GPU image sampler (`binding = 1`) uploading the preset's cover art (`1024x1024` RGBA8) to GPU device-local memory.
  - **First Visualizer Preset — `Cosmic Dreamworlds (Liquid)`**: Exclusively inspired by the front-page Bandcamp cover for **`MPlanetarian - Cosmic Dreamworlds [Deluxe Edition]`** (`https://mplanetarian.bandcamp.com/album/mplanetarian-cosmic-dreamworlds-deluxe-edition`), adhering strictly to the one-Bandcamp-cover-per-preset policy. Renders twin translucent liquid glass planetary orbs connected by a pulsing metaball bridge, concentric incandescent crimson/gold/emerald accretion rings, cascading mycelial light conduits, kinetic polyhedral shards, and a diagonal aurora light beam over deep cosmic space.
  - **Continuous 3D Audio Reactivity & Play/Pause Freeze**: Reads real-time 24-band FFT telemetry and playback state from `MP_Audio_Player` / `MP_Audio_Player_Liquid_Glass` (`~/.config/mix-manager/mp_player_state.json` & IPC socket). Continually generates and animates 3D geometry while music is playing; immediately freezes 3D generation in-place when music is paused or stopped and waits for the user to press Play.
  - **Fixed 1400x1400 Borderless Pure Visualizer Window**: Locked to `1400x1400` non-resizable borderless dimensions (`GLFW_DECORATED = False`, `GLFW_RESIZABLE = False`) with no scrollbars and **zero text overlay ever displayed**. Closes immediately on pressing `Escape` or `q`.
  - **Preset-Only User Control**: Users cannot alter the visual appearance or shader parameters of the visualizer; only preset selection is supported (`--preset`, `--set-preset`, `--list-presets`).
  - **Player & Manager Integration**: Launchable via `[Z]` hotkey inside `MP_Audio_Player` and `MP_Audio_Player_Liquid_Glass`, via `--visualizer` CLI flag, or from `Mix_Archive_Manager.sh` (`[Z]` hotkey, Option 23 Visual Media Suite, or `visualizer` CLI shortcut).
- **Windows 10 & 11 Support for MP Liquid Glass Player (OpenGL [Default] & Vulkan)** (`MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `scripts/launch_liquid_glass.bat`, `launch_liquid_glass.bat`, `MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `tests/test_liquid_glass_windows.py`):
  - **Native Windows 10 & 11 Support**: Fully adapted both OpenGL and Vulkan Liquid Glass renderers and standalone players to run seamlessly on Windows 10 and Windows 11.
  - **Desktop Glass Transparency & DWM Acrylic Blur**: Extended DWM transparent frame into client area (`DwmExtendFrameIntoClientArea` with `-1` margins) and applied rounded region shaping (`CreateRoundRectRgn` / `SetWindowRgn` and Windows 11 `DWMWCP_ROUNDED`), achieving a borderless clear still-water glass aesthetic on Windows.
  - **Dynamic DLL Resolution**: Automatic resolution of `glfw3.dll` / `glfw.dll`, `opengl32.dll`, and `vulkan-1.dll` via system libraries and PATH on Windows, with `PYOPENGL_PLATFORM="wgl"` configuration.
  - **Windows Console Hosting**: Embedded console docking support without requiring GTK/VTE on Windows; docks `MP_Audio_Player.py` inside the glass well using Win32 API (`SetParent`, `SetWindowPos`, `SetForegroundWindow`), with synchronized dragging, double-click maximize/restore, and Normal/Mini/Micro mode switching.
  - **Multi-Vendor Vulkan Support on Windows**: `select_nvidia_device()` allows AMD Radeon and Intel Arc/Iris Vulkan GPUs on Windows while prioritizing NVIDIA discrete GPUs when present.
  - **Windows Batch Launchers**: Added `launch_liquid_glass.bat` and `scripts/launch_liquid_glass.bat` allowing one-click execution on Windows, reading `config.env` for backend preferences (OpenGL default, Vulkan optional).
  - **Player Hotkey & Manager Integration**: Updated `launch_liquid_glass_player()` in `MP_Audio_Player.py` and `launch_mp_audio_player_liquid_glass()` in `Mix_Archive_Manager.sh` to support Windows environments (including Git Bash/MSYS2).
  - **Dedicated Unit Test Suite**: Added `tests/test_liquid_glass_windows.py` verifying Windows 10 & 11 detection, DLL candidate resolution, Win32 API integration, and launcher execution.
- **macOS Apple Silicon Metal Support: `MP_Audio_Player_Liquid_Glass_macOS.py`** (`MP_Audio_Player_Liquid_Glass_macOS.py`, `scripts/liquid_glass_metal.py`, `shaders/liquid_glass_metal.metal`, `scripts/launch_liquid_glass.sh`, `Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_liquid_glass_macos.py`):
  - **Apple Silicon Metal Renderer (`scripts/liquid_glass_metal.py`)**: Built an Apple Silicon Metal low-level rendering backend leveraging `MTLCreateSystemDefaultDevice()`, `CAMetalLayer`, unified memory command encoders, and Metal Shading Language pipelines.
  - **Metal Shading Language Pipeline (`shaders/liquid_glass_metal.metal`)**: MSL vertex (`liquid_glass_vertex`) and fragment (`liquid_glass_fragment`) shaders reproducing the liquid still-water pane, theme caustics, beveled outer rim, panel well shading, cursor specular highlight, and the reduced default core transparency (10% reduction to 0.50 base alpha).
  - **Standalone macOS Liquid Glass Executable (`MP_Audio_Player_Liquid_Glass_macOS.py`)**: Dedicated macOS player executable launching `MP_Audio_Player.py` inside the Metal liquid glass container via pseudo-terminal (PTY) emulation with full support for interactive key handling, window dragging, double-click maximize/restore, and in-place Normal/Mini/Micro mode cycling.
  - **Strict Apple Silicon Enforcement & Legacy Intel Refusal**: Designed exclusively for Apple Silicon (M1/M2/M3/M4 or newer) to exploit unified memory architectures; explicitly detects and refuses legacy Intel-based (`x86_64`) Macs with a clear, helpful message.
  - **Multi-Platform Integration**: Updated `scripts/launch_liquid_glass.sh`, `Mix_Archive_Manager.sh`, and `MP_Audio_Player.py` to seamlessly auto-detect Darwin `arm64` and launch the native Metal player.
  - **Unit Test Suite (`tests/test_liquid_glass_macos.py`)**: Comprehensive test suite verifying Apple Silicon detection, Intel Mac rejection, CLI argument generation, panel rectangles, dynamic rainbow breathing alpha, and MSL shader integrity.
- **Exclusive Dynamic Theme: "Dynamic Liquid Glass [Rainbow Colours] (New)" & Reduced Core Transparency** (`scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `shaders/liquid_glass_gl.frag`, `shaders/liquid_glass.frag`, `MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `scripts/mix_web_companion.py`, `tests/test_dynamic_rainbow_liquid_glass.py`):
  - **Dynamic Rainbow Liquid Glass**: An exclusive theme for the MP Liquid Glass Audio Player derived from Dreamworlds Ultra with vibrant, dynamic hue shifting that cycles slowly through all rainbow colors in real time.
  - **Alternating Breathing Transparency**: The liquid glass transparency dynamically oscillates between -9% reduction and +9% increase over an exact 6-second sinusoidal cycle, creating an organic breathing glass aesthetic.
  - **10% Default Core Transparency Reduction**: Decreased the default transparency across the core player by 10% (increased window compositor `clear_opacity` from `0.60` to `0.70` and shader alpha from `0.40` to `0.50`), giving the window a firmer, crisper, and less transparent glass body.
  - **Web Companion Dynamic Rainbow Theme**: Added full synchronization for `Dynamic Liquid Glass [Rainbow Colours] (New)` to the Web Companion, with translucent glass cards, rainbow caustics, and matching 6-second breathing animations.
  - **Manager & Player Theme Menus**: Selectable as Option 16 in Manager Themes (`manage_themes()`) and cycleable via the `C` key in MP Audio Player.
- **Continuous Liquid Glass Music Playback Across Manager Exit** (`Mix_Archive_Manager.sh`, `tests/test_manager_exit_stops_audio_player.py`):
  - **Preserve Active Listening**: Updated manager exit routines (`exit_mix_manager`, `_stop_mp_audio_daemon`, and `kill_all_mp_audio_players`) to check whether MP Liquid Glass is open (`is_liquid_glass_open`).
  - **Uninterrupted Music Playback**: When closing MP Mix Archive Manager, if the Liquid Glass Audio Player is open, the manager safely avoids terminating the player, stopping the audio engine, or closing its window, allowing users to continue listening to music seamlessly.
  - **Selective Job Cleanup**: Background process cleanup filters out Liquid Glass process IDs (`is_liquid_glass_pid`) while cleanly terminating other manager background workers.
- **MP Liquid Transparent Glass Desktop Icon & First-Run Reward System** (`assets/mp_liquid_glass_icon.*`, `scripts/liquid_glass_reward.py`, `scripts/generate_liquid_glass_icon.py`, `scripts/launch_liquid_glass.sh`, `desktop/MP_Liquid_Glass_Audio_Player.desktop`, `Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_liquid_glass_reward.py`):
  - **High-Resolution Liquid Glass Desktop Icon**: Designed and rendered a translucent liquid glass icon (`512x512` RGBA PNG and SVG) featuring an ambient luminous glow, beveled glass border, refractive caustics, a dynamic 8-band floating EQ spectrum, play badge, and frosted MP monogram.
  - **First-Run Desktop Reward**: When the user launches MP Mix Archive Manager or MP Audio Player for the first time, a desktop shortcut (`~/Desktop/MP_Liquid_Glass_Audio_Player.desktop`) and application entry are automatically installed as a special reward for downloading and running the suite.
  - **Welcome Message from MPlanetarian**: Displays a heartfelt welcome message from MPlanetarian thanking the user and wishing them an inspiring journey with the new Liquid Transparent Audio Player.
  - **Default Player Configuration**: Set `DEFAULT_AUDIO_PLAYER="mp_liquid_glass"` as the primary default audio player across `config.env`, `config.env.example`, and `Mix_Archive_Manager.sh`.
  - **Automatic Player Launch on Manager Start**: MP Mix Archive Manager automatically launches the Liquid Glass Audio Player window beside it upon starting, ensuring an immediate liquid visual experience.

- **New Theme: "MP Dreamworlds Liquid Transparency" [Special Edition]** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `tests/test_playlist_theme_highlight.py`):
  - **Comprehensive Theme Creation**: Added the special edition theme across the entire MP Mix Archive Manager and MP Audio Player suite, featuring deep liquid ocean glass (`#4899ef`), crystalline aqua cyan (`#00f5ff`), seafoam mint (`#00f5d4`), and liquid amethyst (`#c77dff`).
  - **Menu Selection & Quick Cycling**: Selectable from the Themes & Color Palette Switcher in `Mix_Archive_Manager.sh` (Option 15 in `manage_themes()`), quickly cycleable via the **`C`** key in MP Audio Player, and natively supported across both OpenGL and Vulkan Liquid Glass renderers.
- **Top-Panel Theme Border Matching in MP Audio Player** (`MP_Audio_Player.py`, `tests/test_playlist_theme_highlight.py`):
  - **Uniform Panel Line Coloring**: Resolved an issue where pressing `C` to cycle themes only changed the playlist border while the top title banner box and visualizer box remained hardcoded to Hot Purple and Magenta.
  - **Dynamic Synchronization**: Updated `_draw_frame()` so that the top title header banner box, visualizer box, and DJ pitch fader border lines dynamically synchronize with the active theme border color (`b_col`), matching the playlist box.
- **Double-Click Top Bezel to Maximize & Restore in Liquid Glass Player** (`MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `tests/test_liquid_glass_player_features.py`):
  - **Header Double-Click Toggle**: Double-clicking the top bezel of the Liquid Glass window automatically maximizes the player to fill the active monitor workarea, accurately scaling the terminal character grid and shader panel geometry to the window.
  - **Instant Restore**: Double-clicking the top bezel again restores the window to its exact previous position and default size.
  - **Accurate Window Dragging**: Fixed mouse drag positioning and cursor tracking when moving the Liquid Glass window around the desktop, preventing jumping.
  - **Multi-Monitor Awareness**: Implemented `get_workarea()` in both OpenGL and Vulkan renderers querying GLFW monitor workarea bounds across multi-monitor setups.
- **In-Process Mini & Micro Modes for Liquid Glass Player** (`MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `MP_Audio_Player.py`, `tests/test_liquid_glass_player_features.py`):
  - **Seamless In-Window Mode Cycling**: Pressing **`M`** inside the Liquid Glass player cycles seamlessly between **`NORMAL`** (full 80x24+ player), **`MINI`** (74x14 compact), and **`MICRO`** (74x7 single-line HUD) modes.
  - **No External Terminal Spawn**: Automatically adjusts the glass window geometry and VTE terminal dimensions in-place, without spawning an external terminal window.
  - **CLI Mode Flags**: Added `--mini` and `--micro` command-line flags to both `MP_Audio_Player_Liquid_Glass_OpenGL.py` and `MP_Audio_Player_Liquid_Glass.py`.
- **MP Audio Player (Liquid Glass Mode - OpenGL & Vulkan)** (`MP_Audio_Player_Liquid_Glass_OpenGL.py`, `MP_Audio_Player_Liquid_Glass.py`, `scripts/liquid_glass_opengl.py`, `scripts/liquid_glass_vulkan.py`, `shaders/liquid_glass_gl.*`):
  - **OpenGL High-Compatibility Standalone Player (`MP_Audio_Player_Liquid_Glass_OpenGL.py`)**: Added an OpenGL Core 3.3+ version of the Liquid Glass player supporting AMD's Radeon, Intel, NVIDIA, and older OpenGL-capable GPUs that do not support Vulkan.
  - **Liquid Glass Backend Configuration in Manager (`Mix_Archive_Manager.sh`, `config.env`)**: Added `LIQUID_GLASS_BACKEND` setting (default: `"opengl"`). Users can switch between OpenGL and Vulkan at any time via Default Audio Player Configuration (Option 15 -> 14 -> 22), Audio Players Suite (Option 15 -> 17), System & Settings (Option 36 -> 7), or shortcut `liquid-config`.
  - **Dynamic Launcher & Fallback**: Main-menu option 3 automatically launches the user-configured backend (`OpenGL` by default, or `Vulkan` when selected), with graceful fallback to Vulkan if the OpenGL script is missing.
  - **OpenGL Shader Pipeline**: Shaders `shaders/liquid_glass_gl.vert` and `shaders/liquid_glass_gl.frag` replicate the clear still-water glass pane, dynamic player theme reflections, sloped outer rim, and shaded panel wells with bit-for-bit fidelity to the Vulkan renderer.
  - **Liquid Glass Hotkey Launch (`L` / `l`) & Repeat Remapping (`y` / `Y`) in MP Audio Player** (`MP_Audio_Player.py`, `scripts/mix_web_companion.py`, `tests/test_player_recording.py`, `tests/test_liquid_glass_opengl.py`):
    - **Open Liquid Glass Player via `L` / `l`**: Pressing `L` or `l` in the MP Audio Player (normal, mini, or micro view) instantly launches the standalone Liquid Glass Player in its own desktop window using the user-configured backend (`OpenGL` or `Vulkan`).
    - **Remapped Repeat / Loop Mode to `y` / `Y`**: Remapped Repeat mode cycling (`ALL` ➔ `ONE` ➔ `OFF`) from `L` to `y` / `Y` across key handlers, HUD footer hints, and the mobile web companion interface.
    - **Liquid Glass CLI Arguments**: Added `--liquid`, `--liquid-glass`, and `--glass` command-line options to `MP_Audio_Player.py` for direct headless or shortcut launching.
    - **Unit Test Coverage**: Updated `test_player_recording.py` and `test_liquid_glass_opengl.py` to test the new `y`/`Y` repeat shortcut and `l`/`L` Liquid Glass window launching.
  - **Automated Unit Tests**: Added `tests/test_liquid_glass_opengl.py` verifying theme palettes, embed check child isolation, manager backend configuration wiring, OpenGL shader compilation, offscreen frame rendering, and window shaping.
- **Automatic & Comprehensive Process Termination of All MP Audio Players on Manager Exit** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `tests/test_manager_exit_stops_audio_player.py`):
  - **Fail-Safe Cleanup on Exit & Signal Traps**: Updated `exit_mix_manager()`, `_cleanup_mix_manager_exit()`, and `_stop_mp_audio_daemon()` to unconditionally terminate all running MP Audio Player instances (daemon, interactive window, mini/micro player, shell launchers, and Liquid Glass Mode) whenever the Manager exits via menu Option 37 (`q`/`exit`) or receives signals (`SIGINT`, `SIGTERM`, `SIGHUP`, `EXIT`).
  - **Graceful Followed by Force-Kill (`SIGTERM` -> `SIGKILL`)**: First delivers IPC `stop` command and `SIGTERM` to allow active audio streams to flush state and release sound cards cleanly, followed by a guaranteed `SIGKILL` (`kill -9` / `pkill -9`) for any surviving or hung processes.
  - **Traktor S4 MIDI Service & Window Cleanup**: Automatically terminates the Traktor Kontrol S4 MIDI controller background service daemon (`traktor_s4_midi.pid`), cleans up all socket/PID runtime files, and closes lingering player terminal windows via `wmctrl` / `taskkill`.
  - **Dedicated Helper Function**: Added `kill_all_mp_audio_players()` as an exportable bash function for explicit shutdown.
  - **Interactive Terminal Signal Handling**: Added clean `SIGTERM`/`SIGINT`/`SIGHUP` handlers to `TerminalPlayerUI` in `MP_Audio_Player.py` to restore terminal cursor modes and exit immediately upon manager shutdown.
  - **Expanded Automated Unit Tests**: Extended `tests/test_manager_exit_stops_audio_player.py` with multi-instance mock process termination and Traktor S4 PID cleanup tests.
- **10-Band Graphic Equalizer & Electronic Music Presets (`MP_Audio_Player.py`)**:
  - **Genre-Specific Presets**: Curated electronic music equalizer curves designed around standard 10-band ISO center frequencies (31Hz, 62Hz, 125Hz, 250Hz, 500Hz, 1kHz, 2kHz, 4kHz, 8kHz, 16kHz):
    - **Flat (Default)**: Neutral bit-perfect passthrough (0.0 dB across all bands).
    - **Ambient**: Lush low-end warmth (+3.0 dB at 31Hz, +2.5 dB at 62Hz), decluttered midrange (-1.0 to -1.5 dB) for expansive reverb clarity, and airy high-frequency shimmer (+2.0 to +2.5 dB).
    - **Trance**: Uplifting euphoric smiley curve with driving sub-bass (+4.0 dB), punchy 62Hz kick fundamental (+5.0 dB), boxiness cut at 250Hz (-1.5 dB), forward synth pluck bite at 2–4kHz (+2.5 to +3.5 dB), and shimmering supersaw highs (+4.0 dB at 8kHz).
    - **Techno**: Underground club impact with tight sub-bass (+5.0 dB), heavy boxiness cut at 250–500Hz (-2.5 to -2.0 dB) for rolling bassline clarity, and snappy clap/open-hat presence (+2.5 to +3.0 dB).
    - **House**: Warm 4/4 groove with chest-hitting punch at 62–125Hz (+4.0 / +3.0 dB), neutral mids for vocal and chord clarity, and crisp hi-hat sizzle at 8kHz (+3.0 dB).
    - **Progressive**: Balanced progressive house/trance curve with deep foundation (+3.0 to +4.0 dB), controlled lower-mids (-1.0 dB), melody pluck definition at 2–4kHz (+2.0 to +2.5 dB), and extended 16kHz air (+3.0 dB).
    - **Vapourwave**: Authentic 1980s lo-fi cassette / VHS tape aesthetic with controlled sub-rumble (-2.0 dB), boosted warm low-mids (+2.5 to +3.5 dB at 125–250Hz) for lush nostalgic synth body, and signature steep high-frequency cassette roll-off (-4.0 dB at 8kHz, -6.0 dB at 16kHz).
  - **Real-Time DSP Audio Equalizer Engine (`EqualizerFilter`)**:
    - Zero-phase frequency-domain FIR filtering with smooth interpolation across logarithmic frequency bands.
    - Zero-overhead bit-perfect passthrough when Flat preset is active.
    - Fully continuous overlap-save buffer streaming with no phase distortion, pops, or filter instability.
  - **Interactive Equalizer HUD & Custom Preset Creator (`E` Key)**:
    - Press **`E`** in MP Audio Player to toggle the modal 10-Band Graphic Equalizer HUD.
    - **Slider Decibel Scale**: Visual decibel grid (-12.0 dB to +12.0 dB) dynamically sizing to terminal height with center 0 dB baseline and selected column highlighting.
    - **Band Selection & Adjustment**: `Left` / `Right` (or `h` / `l`) selects bands 1–10; `Up` / `Down` (or `k` / `j`, `+` / `-`) adjusts band slider by ±0.5 dB.
    - **Preset Switching**: `P` / `Tab` cycles presets; keys `1`–`7` jump directly to presets (1: Flat, 2: Ambient, 3: Trance, 4: Techno, 5: House, 6: Progressive, 7: Vapourwave).
    - **Custom User Presets**: Adjusting any slider marks preset as Custom; press **`S`** to name and save custom presets to `~/.config/mix-manager/custom_eq_presets.json`. Saved presets automatically appear in the preset list and can be deleted with **`D`**.
    - **Reset**: Press **`R`** or **`0`** to instantly reset all sliders to Flat (0.0 dB).
  - **CLI & IPC Command Integration**:
    - CLI arguments: `--list-eq-presets`, `--eq-preset <name>`, `--eq-bands <g0,g1,...g9>`, `--reset-eq`.
    - IPC commands: `get_eq`, `set_eq_preset`, `set_eq_band`, `set_eq_bands`, `save_eq_preset`, `delete_eq_preset`, `reset_eq`.
  - **Unit Test Suite (`tests/test_equalizer_presets.py`)**:
    - 11 automated unit tests verifying preset curves, EqualizerManager loading/aliases, slider adjustments/clamping, custom preset persistence, EqualizerFilter DSP audio processing, and interactive UI HUD key events.
- **Traktor Kontrol S4 Hardware MIDI Controller Support (`hw:0,0` / `/dev/snd/midiC0D0`)**:
  - Exclusively supports Native Instruments Traktor Kontrol S4 via ALSA rawmidi hardware port `hw:0,0` and `amidi`.
  - **Dual-Deck Real-Time Pitch Controls**: Physical pitch faders on either side of the unit (Deck A and Deck B) smoothly control MP Audio Player playback pitch between **-20% and +20%**, featuring automatic deadband snapping at center detent (`0.0%`).
  - **Pitch Nudge & Sync**: Pitch bend buttons (`+` / `-`) for ±1% micro-adjustments; SYNC button for instant pitch reset to 0% (Left) or shuffle mode toggle (Right).
  - **Intelligent Transport & Navigation Mapping**:
    - `PLAY ▶`: Instant Play / Pause toggle with player auto-spawn.
    - `CUE ⏹`: Stop playback and return to start (`0:00`).
    - `CUP ⏯`: Seek to `0:00` and immediately trigger playback.
    - `BROWSE Encoder`: Rotate clockwise for Next Track, counter-clockwise for Previous; push to Play/Select.
    - `LOOP IN / OUT`: Quick seek -10s / +10s.
    - `LOOP SIZE Encoder`: Scrub seek ±30s.
    - `HOTCUES 1..4`: Instant jump across mix (0%, 25%, 50%, 75%).
    - `Channel Faders (A, B, C, D) & Master Level`: Dynamic volume control (0%–100%).
    - `Headphone Cue Buttons`: Instant audio output mute / unmute.
    - `Loop Recorder REC / PLAY`: Toggle live session recording to WAV / toggle playback.
- **Traktor S4 MIDI Service Daemon (`scripts/traktor_s4_midi.py` / `traktor_s4_midi.sh`)**:
  - Lightweight background daemon supporting `--start`, `--stop`, `--restart`, `--status`, `--daemon`, `--guide`, and interactive `--monitor`.
  - IPC client communicating directly with MP Audio Player daemon (`send_ipc_command`).
- **Studio Hardware & MIDI Menu Integration (Option 18)**:
  - Option 3 in Option 18 menu: `Traktor Kontrol S4 Hardware MIDI Control & Mapping`.
  - Submenu for enabling/disabling MIDI support, status badge, restarting service, launching live MIDI packet monitor, viewing user guide, and opening official PDF manuals.
  - Direct main console shortcut aliases: `s4`, `midi`, `kontrol`, `traktor-s4`.
- **First-Time Detection Notification on Manager Main Page**:
  - Automatically notifies user at the top of the Main Page upon first detecting a connected Traktor Kontrol S4:
    `🎛️ Traktor Kontrol S4 Hardware MIDI Device Detected [hw:0,0]! Please navigate to Option 18 (Studio Hardware & MIDI) to enable and configure Traktor Kontrol S4 MIDI Device Mapping Controls.`
  - Flag persisted in `config.env` (`TRAKTOR_S4_NOTIFIED`) and `~/.config/mix-manager/traktor_s4_notified.flag`.
- **MUSIC Folders Configuration Notification**:
  - Alerts the user at the top of the Main Page if 0 MUSIC folders are configured, prompting navigation to Option 1.
- **Documentation & Official Manuals**:
  - `docs/TRAKTOR_KONTROL_S4_USER_GUIDE.md`: Comprehensive user guide with hardware MIDI mode toggle (`SHIFT + BROWSE` / `PREVIEW`), ALSA setup, and mapping tables.
  - Official downloaded manuals: `docs/Controller_Editor_Manual_English.pdf` and `docs/traktor_kontrol_s4_mk2_manual_english.pdf`.
- **Unit Test Suite (`tests/test_traktor_s4_midi.py`)**:
  - 14 automated unit tests verifying ALSA port detection, pitch CC scaling, 14-bit pitch bend decoding, deadband snapping, note/CC parsing, and player IPC command dispatching.


- **MP Audio Player Files Key** (`MP_Audio_Player.py`, `tests/test_open_in_file_manager.py`):
  - Pressing `F` opens the folder of the audio file that is playing and selects that file. Windows uses Explorer's select API, macOS uses Finder `open -R`, and Linux and FreeBSD use the desktop file-manager select interface with Dolphin, Nautilus, Nemo, and Caja as fallbacks.

- **macOS Startup Loader Video** (`scripts/display_loader_video.sh`, `scripts/macos_borderless_loader.swift`, `tests/test_loader_video.py`):
  - The logo video plays in a borderless, always-on-top window on macOS before the Manager menu appears. The Manager compiles a small AppKit player once with `swiftc` and reuses it. ffplay (`-noborder`), mpv (`--no-border`), and VLC (`--no-video-deco`) remain borderless fallbacks. QuickTime Player is not used.

- **Startup Loader Video Integration (`MP_loader.mp4`) with Already-Open Manager Detection** (`Mix_Archive_Manager.sh`, `.probe_mgr.sh`, `scripts/display_loader_video.sh`, `config.env`, `config.env.example`, `tests/test_loader_video.py`):
  - **Animated Startup Loader Video (`MP_loader.mp4`)**: Integrated `MP_loader.mp4` into the MP Mix Archive Manager startup sequence. When launching the manager in the console, `MP_loader.mp4` is displayed in a sleek, borderless, always-on-top video window (using `ffplay`, `mpv`, `vlc`, or OS native players) with audio, while the console prepares the environment, warms up archive caches, and initializes terminal geometry.
  - **Smart Already-Open Manager Detection**: Added `is_manager_already_open()` to inspect active manager sessions in `~/.config/mix-manager/bridge_status.json` and scan the process table (`pgrep -f "Mix_Archive_Manager\.sh"`). If an active instance of the manager is already open in another console or terminal window, the loader video is automatically suppressed so work is never interrupted.
  - **Interactive Skip & Safety Caps**: Users can press `Esc`, `Space`, `Enter`, or `q` either in the video player window or directly in the console to immediately dismiss the loader video and jump straight to the main menu. A 10.5-second safety timeout guarantees the loader never hangs.
  - **Visual Media Menu Integration**: Added Option 8 (`Play Startup Loader Video (MP_loader.mp4)`) to Section 2 Option 22 (Visual Media Suite), allowing users to replay, test, or enjoy the startup animation anytime.
  - **Configurable Settings**: Added `SHOW_LOADER_VIDEO="true"` and `LOADER_VIDEO_FILE="MP_loader.mp4"` settings across `config.env` and `config.env.example`.
  - **Automated Unit Tests**: Added `tests/test_loader_video.py` verifying video path discovery, already-open manager process detection, skip overrides, configuration toggles, and player dispatching.

- **macOS Traktor Auto-Launch, 64-Bit Windows Executable Launcher & Main Menu Selector Breathing Animation** (`Mix_Archive_Manager.sh`, `scripts/windows_launcher.c`, `scripts/windows_launcher.rc`, `MP_Mix_Manager.exe`, `manager.exe`, `tests/test_windows_launcher_exe.py`, `tests/test_menu_selector_breathing.py`):
  - **macOS Traktor Monitor Auto-Launch in Console Tab**: Updated `run_deferred_startup()` in `Mix_Archive_Manager.sh` so that when starting up on macOS with Traktor Pro loaded and running (`pgrep -i -f "Traktor"` or AppleScript), it automatically spawns the Traktor Live Monitor in a dedicated new console tab via `launch_in_terminal`.
  - **Native 64-Bit Windows `.EXE` Launcher (`MP_Mix_Manager.exe` / `manager.exe`)**: Built a native PE32+ (x86-64) Windows console launcher written in C (`scripts/windows_launcher.c` + `scripts/windows_launcher.rc`) compiled with `x86_64-w64-mingw32-gcc`. Automatically enables console virtual terminal processing (`ENABLE_VIRTUAL_TERMINAL_PROCESSING`), detects Git Bash / MSYS2 / WSL2 environments, and seamlessly launches `Mix_Archive_Manager.sh` with argument passthrough.
  - **10-Second Idle Menu Selector Breathing Animation**: Implemented an automated breathing effect on the Main Menu selector in `run_interactive_submenu()`. When the user stops moving the cursor for 10 seconds, the active selector highlight gently pulses and fades in and out slowly over a 3.2-second sinusoidal cycle, automatically restoring full crisp brightness as soon as any navigation or selection key is pressed.
  - **Automated Verification**: Added `tests/test_windows_launcher_exe.py` (validating PE32+ 64-bit headers, architecture, and resources) and `tests/test_menu_selector_breathing.py` (testing RGB scaling, 10s idle trigger, and macOS Traktor launch hooks).

- **Mix Queue Option (`Q` / `q`) in MP Audio Player** (`MP_Audio_Player.py`, `tests/test_player_queue.py`):
  - **Press `Q` / `q` to Queue Mixes**: Added playback queueing support to MP Audio Player. Users can select any mix or music track in the playlist and press `Q` or `q` to immediately enqueue it to play next in order.
  - **Prioritized Queue Progression**: Updated `AudioEngine._step_track()` so forward track progression (`next_track()`) automatically dequeues and loads queued tracks before resuming playlist order or shuffle selection, even when repeat mode is set to "off".
  - **Queue State Management**: Implemented `queue_track()`, `dequeue_next()`, `clear_queue()`, and `get_queue()` on `AudioEngine` with thread-safe lock protection and live state synchronization.
  - **Visual Queue Indicators**: Displayed queue count badges in the playlist header (`• Queue: <n>`), dedicated position badges (`Q1`, `Q2`, etc.) and bold cyan color highlighting in the playlist table for all queued mixes.
  - **Control Hints & Exit Key Differentiation**: Updated playlist border footer and control hints in normal, mini, and micro frames to show `[Q] Queue`. Clearly distinguished `[Q] Queue` from exit shortcuts (`[b/Esc] Exit`, `b`/`B`, `Esc`, `Ctrl+C`, `x`/`X`).
  - **IPC Server & CLI Flags**: Added `queue`, `get_queue`, and `clear_queue` socket IPC commands, along with `--queue` / `-q` and `--clear-queue` command line arguments.
  - **Automated Unit Tests**: Added `tests/test_player_queue.py` validating queue methods, step-track queue priority, UI key dispatching, banner feedback, and CLI flags.

- **Theme-Matched Playlist Highlight Line & Active Track Indication in MP Audio Player** (`MP_Audio_Player.py`, `tests/test_playlist_theme_highlight.py`):
  - **Dynamic Theme Palette Mapping**: Added `EqualizerVisualizer.get_theme_palette()` mapping visualizer themes and shell manager aliases (`dreamworlds_ultra`, `cosmic_supernova`, `dreamworlds_neon`, `retro_studio`, `cyber_neon`, `phosphor`, `ice_cold`, `retro_terminal`, etc.) to unified UI element palettes (`highlight`, `active`, `border`, `accent`).
  - **Selected Mix Row Theme Highlighting**: Replaced hardcoded green (`COLOR_TRAFFIC_GREEN`) in `TerminalPlayerUI._draw_frame` with theme-matched reverse-video bold highlight (`COLOR_HOT_PURPLE` under default `dreamworlds_ultra`).
  - **Playing Track Active Indication**: Rendered the currently playing track with theme-matched active color (`COLOR_NEON_PINK` under `dreamworlds_ultra`) when the cursor is positioned on another item, eliminating hardcoded green.
  - **Box Frame Border Consistency**: Aligned playlist header, scroll bounds, and footer borders with the active theme's border color.
  - **Unit Test Coverage**: Added `tests/test_playlist_theme_highlight.py` validating theme palette resolution, alias matching, and theme color rendering in terminal frames.

- **Live WAV Audio Recording with 3GB Auto-Rollover & Flashing Red Dot REC Indicator in MP Audio Player** (`MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `tests/test_player_recording.py`):
  - **Live WAV Recording Feature (`R` / `r`)**: Added live audio stream recording capability directly to MP Audio Player. Users can press `R` or `r` during playback to instantly start recording output audio directly to standard 16-bit PCM stereo WAV files inside `MP_AUDIOWAV_REC_OUTPUT`. Pressing `R` or `r` again cleanly finalizes and stops the recording.
  - **Continuous Recording & 3GB File Rollover**: If the user leaves recording running continuously, the audio engine automatically splits recordings at a maximum size of 3GB (`3 * 1024 * 1024 * 1024` bytes) without audio dropouts or sample loss, rolling over to sequential parts (`*_pt01.wav`, `*_pt02.wav`, etc.) to guarantee 100% standard RIFF WAV header compatibility and eliminate header overflow.
  - **Non-Blocking Background Worker Thread**: Recording chunks are enqueued via a non-blocking queue (`_rec_queue.put_nowait()`) directly in the audio engine callback, completely isolating real-time audio playback from disk I/O latency.
  - **Slow Flashing Red Dot REC Indicator**: Implemented a pulsating red dot indicator (`● REC` / `○ REC`) in Normal, Mini, and Micro player frames and the terminal window title, pulsing steadily during active recording sessions.
  - **Loop / Repeat Key Remapping (`L` / `l`)**: Remapped Repeat / Loop mode cycling to `L` / `l`, reserving `R` / `r` exclusively for start/stop recording.
  - **Manager Integration & Status Badges**: Added live `MP_PLAYER_RECORDING` detection and red recording badges (`● REC`) to `Mix_Archive_Manager.sh`'s top player banner and `manage_audio_players()` dashboard.
  - **IPC & CLI Dispatch**: Added `record`, `toggle_record`, `start_record`, and `stop_record` IPC socket commands, and added `--record` / `--toggle-record` (`-r`) CLI flags to `MP_Audio_Player.py`.
  - **Automated Unit Test Suite**: Added `tests/test_player_recording.py` covering directory resolution, start/stop/toggle lifecycle, WAV data formatting, 3GB size rollover, UI keybindings, IPC commands, and argument parsing.

- **Restored Shuffle Mode Toggle (`s` / `S`) and Volume Controls in MP Audio Player** (`MP_Audio_Player.py`, `tests/test_player_shuffle.py`):
  - **Restored `s` / `S` Shuffle Mode Shortcut**: Fixed key processing regression in `_process_key` where pressing `s` or `S` failed to toggle shuffle mode. Pressing `s` or `S` now instantly toggles playlist shuffle state in standard, Mini, and Micro player modes, giving instant visual notification feedback (`Shuffle: ON` / `Shuffle: OFF`).
  - **Restored `v` / `V` and `[` / `]` Volume Controls**: Reinstated volume adjustment shortcuts (`v` / `]` for volume up by +5%, `V` / `[` for volume down by -5%) with automatic unmute and instantaneous status bar feedback.
  - **Immediate Audio Engine State Synchronization**: Updated `toggle_shuffle()`, `toggle_mute()`, and `cycle_repeat()` on `AudioEngine` to immediately flush state updates to disk (`STATE_FILE`), eliminating UI latency.
  - **IPC & CLI Commands for Shuffle & Repeat**: Extended `PlayerIPCServer` to return updated state on `shuffle`, `repeat`, and `mute` commands, and added `--shuffle` and `--repeat` CLI arguments to `MP_Audio_Player.py`.
  - **Unit Test Coverage**: Added `tests/test_player_shuffle.py` testing key handling for `s`/`S` and `v`/`V` in both local and remote modes, along with argument parser verification.

- **Fix Equalizer Display and `E` / `e` Cycling in MP Micro Audio Player Mode** (`MP_Audio_Player.py`, `tests/test_micro_player_eq.py`):
  - **Full Animated EQ Visualizer in MICRO Mode**: Restored real-time equalizer visualizer rendering in `_draw_micro_frame` when viewport height `h >= 9`. All 6 equalizer styles (Neon Chroma Prism, Stardust Chroma, Dual Stereo Spectrum, Master Spectrum, Analog Studio VU, and CRT Oscilloscope) now fully render below the micro-status card.
  - **Style-Adaptive Micro Meter**: Overhauled the top micro-meter `meter_str` inside the micro-card so it dynamically reflects the active EQ style (oscilloscope waveform, dual stereo VU bars, stardust falling dust particles, neon prism colored spectrum, or master frequency bands).
  - **Dual `e` / `E` EQ Shortcut Handling**: Upgraded key handler `_process_key` in `MP_Audio_Player.py` so both lowercase `e` and uppercase `E` (Shift+E / CapsLock) cycle the visualizer equalizer in `MINI` and `MICRO` modes without erroneously triggering playlist export mode (`export_mode`).
  - **Aligned Border Geometry**: Corrected rectangular border widths and padding in `_draw_micro_frame` to maintain razor-sharp vertical borders across dynamic track title lengths and terminal resizes.
  - **Unit Test Coverage**: Added `tests/test_micro_player_eq.py` verifying `e` and `E` key handling, visualizer rendering across all 6 styles in micro mode, and lack of false playlist export triggers.

- **Dynamic Animated CRT Oscilloscope Equalizer Waveform on Linux & Across Platforms** (`MP_Audio_Player.py`, `tests/test_oscilloscope_visualizer.py`):
  - **Fluid Real-Time Waveform Animation**: Completely overhauled the `"oscilloscope"` style in `EqualizerVisualizer`. Added moving temporal sweep phases (`sweep_bass`, `sweep_mid`, `sweep_treble`) driven by continuous high-precision timestamps and live pitch factors, resolving the frozen/static horizontal line on Linux.
  - **Live Audio PCM Buffer Integration**: Connected `AudioEngine.get_status_dict()`'s `waveform` buffer (`recent_mono_buffer`) to the oscilloscope renderer with dynamic linear interpolation (`np.interp`) and amplitude normalization, blending authentic sound wave samples with undulating traveling harmonics.
  - **Vector Beam Continuity & Slopes**: Replaced integer-truncated quantization with smooth vertical span drawing using slope and vector characters (`╱`, `╲`, `━`, `│`), preventing gaps or quantization collapse on soft audio inputs.
  - **Authentic CRT Graticule Grid & Phosphor Themes**: Rendered a retro cathode-ray tube graticule scale with center crosshairs (`┼`), subtle coordinate ticks (`·`), and horizontal zero-axis (`─`), with dynamic theme coloring via `_get_neon_prism_color` across all 8 visualizer themes.
  - **Adaptive Width & Height Scaling**: Dynamically scales inner box widths (full terminal, 78-col standard, or compact mini-mode) and supports multi-height viewports (height 7, 5, or 3) with exact character boundaries.
  - **Comprehensive Unit Test Suite**: Added `tests/test_oscilloscope_visualizer.py` covering multi-interval animation verification, live PCM ingestion, height/width scaling, and `EqualizerVisualizer.render` dispatch.

- **Global `Space` (Toggle Audio Playback) and `M` (Open MP Audio Player Window) Shortcuts Across Manager** (`Mix_Archive_Manager.sh`, `MP_Audio_Player.py`, `scripts/music_folders_manager.py`):
  - **Global `Space` Toggle Playback**: Pressing `Space` anywhere inside the MP Mix Archive Manager (in the main menu, all interactive submenus via `run_interactive_submenu()`, and all `press_enter()` pause prompts) instantly stops or resumes audio playback if MP Audio Player is running. If playback is stopped, `Space` never starts a cold audio stream (`--only-if-running` guard flag).
  - **Global `M` Player Window**: Pressing `M` or `m` anywhere inside the manager immediately opens the interactive MP Audio Player in a dedicated standalone terminal window (`--new-window` with auto-detection for Konsole, GNOME Terminal, XDG Terminal Exec, XFCE, Terminal.app, or Windows Terminal).
  - **Preserved In-Player `M` Micro Mode**: Retained full internal player behavior where pressing `M` inside MP Audio Player toggles between Mini and Micro modes without conflict.
  - **Interactive Pause Prompt Integration**: Upgraded `press_enter()` across all 107+ manager tool execution prompts to capture `Space` and `M` with real-time inline status feedback (`[♫ Playback Toggled]`, `[♫ Player Window Launched]`) while immediately returning upon pressing `Enter`, `Esc`, or `q`.

- **Local Spek Spectrogram Generation in MP Audio Player (`G` Key)** (`MP_Audio_Player.py`):
  - **In-Player 1080p Spek Generation (`G` / `g`)**: Press `G` during playback, playlist browsing, or inside the `[I]` Track Audio Inspector HUD to asynchronously generate and save a local 1920x1080 logarithmic Spek spectrogram (`*_spectrogram.png`) for any playing/selected track or mix directly into `SPEK_OUTPUTS`.

- **MP Mini Audio Player Mode (`M` Key), Hidden MICRO Mode & Retro Unix Theme** (`MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `Congen/Congen`, `mix_web_companion.py`):
  - **MP Mini Mode (`M`)**: Launches an independent compact transparent console window configured with 30% background opacity (`Opacity=0.30`, 70% transparency) with vibrant dynamic color-shifting ambient background tints (`OSC 11`) via irrational phase modulation, real-time mini equalizer, volume/pitch controls, and full track metadata without the playlist.
  - **Hidden MICRO Mode (`M` inside Mini Mode)**: Toggles an ultra-compact single-strip playback view inside Mini Mode with 30% background opacity and dynamic color-changing tints; moved mute control to `u` / `U`.
  - **Old Style Unix Terminal Retro Mode (White on Black)**: Added classic high-contrast monochrome Unix terminal theme across the suite.

- **Extreme Low-Spec Zero-Fork Rewrite of `.probe_mgr.sh`** (`.probe_mgr.sh`):
  - **98.2% Line Reduction (14,583 → 264 lines)**: Rewrote `.probe_mgr.sh` to eliminate subshell forks (`$(...)`), external `awk`/`sed`/`grep`/`wc` invocations, and redundant formatting overhead, reducing file size by 160 KB while preserving all 439 functions, menus, and CLI entrypoints for slow systems.

- **Duplicate Mix Scanner Hardening, O(1) Partial-Hash Pre-Filter & Automated Test Suite** (`find_duplicate_mixes.py`, `scripts/find_duplicate_mixes.py`, `tests/test_find_duplicate_mixes.py`):
  - **Two-Tier Hashing (`compute_partial_hash` + `compute_hash`)**: Added a fast 128 KB head+tail partial hash pre-filter before running full-file SHA-256 on multi-gigabyte FLAC/WAV mixes, plus non-interactive EOF protection (`safe_input`).
  - **Deterministic Original Selection**: Hardened duplicate detection with canonical path and timestamp sorting (`len(basename)`, `mtime`), ensuring clean canonical track filenames (e.g. `Mix_042.flac`) are consistently preserved as original rather than copy artifacts (`Mix_042_copy.flac`), independent of filesystem traversal order.
  - **Comprehensive Unit Tests**: Created `tests/test_find_duplicate_mixes.py` with 8 test cases covering format size conversion, episode regex extraction, partial & full SHA-256 fingerprinting, exact/multi-render detection, quarantine handling with collision-safe naming, and interactive deletion confirmation.
  - **Synchronized Scripts**: Verified 100% synchronization and clean compilation between root and `scripts/` directories.

- **Menu Option Search by Pressing `/` in MP Mix Archive Manager** (`Mix_Archive_Manager.sh`, `tests/test_music_folders_presentation.py`):
  - **Instant Menu Search Filtering (`/`)**: Added real-time text search filtering to `run_interactive_submenu()` in `Mix_Archive_Manager.sh`, providing the exact same intuitive search function implemented in MP Audio Player across the Main Page and submenus.
  - **Dynamic Search Buffer & Match Navigation**: Typing after pressing `/` dynamically searches option keys, labels, and descriptions (`_find_menu_search_match`). Up and Down arrow keys cycle through matching options, Enter executes the currently highlighted match, and Esc clears the search query or exits search mode.
  - **Interactive Status Bar Prompt**: Displays `Filter / Search: [query█] ▏ [Option N: Title] ▏ (Type to search, ↑/↓ Navigate, Enter: Select, Esc: Clear)` with clear guidance in both main menu and submenus.

- **Streamlined Audio Player & Retained Track Audio Inspector HUD** (`MP_Audio_Player.py`, `scripts/music_folders_manager.py`, `tests/test_music_folders_presentation.py`):
  - **Refocused Audio Player Architecture**: Dedicated the application strictly as an audio player playing music and mixes rather than a DJ tool, removing 4 DJ features:
    1. **Removed Hot Cues System**: Cleaned out Hot Cues (Keys 1-4, Shift+1-4, C, `HotCueManager`, and `dj_hot_cues.json` handling).
    2. **Removed BPM Tap & Pitch Nudge**: Reverted Key B to standard quit/back, removed tempo tapping and ±2% deck sync nudging.
    3. **Removed Camelot Wheel Harmonic Matcher**: Removed `CAMELOT_WHEEL`, key extraction, and Key K filter toggle; restored standard up navigation.
    4. **Removed DJ Drop Audition**: Removed Key D drop skip cycling to retain pure track playback.
  - **Preserved & Polished Option I (Track Audio Inspector HUD)**: Retained the popular modal Inspector HUD (`I`), now focused purely on audio file fidelity and playback diagnostics: Track Title, Folder / SMB Share (`smb://192.168.1.138/`), Format / Codec (e.g. FLAC Lossless, 96.0 kHz Stereo), Time Position & Progress, Volume & Pitch status, Hardware Audio Output Engine, and Full Network Path.
  - **Updated Music Folders Manager**: Updated Option 7 guide and menu options in `scripts/music_folders_manager.py` to highlight the Track Audio Inspector and Player Shortcuts.

## [0.3.7 Beta] - 2026-10-16

- **MP Audio Player Option 1 Verified & Tested — Advanced MUSIC Mode & SMB Music Folders Presentation** (`MP_Audio_Player.py`, `Mix_Archive_Manager.sh`, `scripts/music_folders_manager.py`, `tests/test_music_folders_presentation.py`):
  - **Option 1 Tested & Verified**: MP Audio Player Option 1 ("Play Tracks from MUSIC Folders using MP Audio Player — Advanced Mode") has been tested and verified, confirming correct presentation of remote SMB music folders.
  - **SMB Music Folders Presentation**: Successfully mounted and presented remote network shares `/MUSIC001` (11,687 tracks) and `/MUSIC002` (19,584 tracks) from host `192.168.1.138`, presenting an aggregate collection of 31,271 DJ tracks.
  - **Instant Playlists & Metadata Cache**: Pre-cached high-speed playlists (`DJ_Tracks_ALL_MUSIC_FOLDERS.m3u8`, `DJ_Tracks_MUSIC001.m3u8`, `DJ_Tracks_MUSIC002.m3u8`) and JSON metadata caches load instantaneously without network directory traversal delays.
  - **VFS Full Cache Optimization**: Enabled rclone VFS full cache mode (`--vfs-cache-mode full`, `--vfs-read-chunk-size 4M`, `--buffer-size 16M`) ensuring smooth audio streaming, instant seek times, and preventing playback stalls over CIFS/SMB.
  - **5 Exclusive DJ Mode Features**:
    1. **DJ Hot Cues**: 4 instant hot cue pads ([1]-[4], [Shift+1]-[4]) plus CDJ Main Cue ([C]) with persistent disk storage in `dj_hot_cues.json`.
    2. **Live BPM Tap & Pitch Nudge**: Tap [B] for tempo counter; [,] / [.] for subtle ±2% deck sync nudging.
    3. **Camelot Wheel Harmonic Matcher**: [K] filters the active playlist to only show harmonically compatible tracks (±1 semitone, relative major/minor).
    4. **DJ Drop Audition**: [D] skips intro directly to breakdown and drop (25% intro, 50% drop, 75% breakdown) for fast track vetting.
    5. **Track Tag Inspector HUD**: [I] displays full audio specs, bitrate, sample rate, key, BPM, and SMB source path.
  - **Playlist Track Duration Presentation in MUSIC Mode**: Replaced the size field (`0MB`) in the playlist browser with track duration (`00:00`) when in MUSIC Mode. Includes asynchronous background duration resolution via `soundfile.info` for browsed tracks, live playback duration synchronization, and preservation of the flush 118-column panoramic layout.

## [0.3.6 Beta] - 2026-10-15

- **Intelligent Bluetooth Audio Auto-Routing & Hardware Output Monitor** (`MP_Audio_Player.py`, `scripts/monitor_bluetooth_audio.py`, `scripts/monitor_bluetooth_audio.sh`, `monitor_bluetooth_audio.sh`, `scripts/get_audio_interface.py`, `Mix_Archive_Manager.sh`):
  - **Zero-Interruption Bluetooth Auto-Switching**: MP Audio Player now intelligently monitors for Bluetooth headphone connections (e.g., Skullcandy Crusher ANC 2, Sony WH-1000XM, AirPods, Bose) in real-time via high-efficiency event subscription (`pactl subscribe`) paired with a 1.2s failsafe polling heartbeat.
  - **Automated System Default Output Migration**: Automatically switches the Linux PipeWire / WirePlumber / PulseAudio default sound output (`wpctl set-default` and `pactl set-default-sink`) upon Bluetooth headset connection, eliminating the need to manually change system output settings in KDE Plasma or GNOME.
  - **Live Stream Migration (`pactl move-sink-input`)**: Intelligently identifies active MP Audio Player audio streams (`PipeWire ALSA [python]`, matched by process PID and client profile) and seamlessly migrates playback to the Bluetooth sink live without interrupting the mix or dropping frames.
  - **Automatic Fallback Output Recovery**: Detects Bluetooth disconnections and instantly reverts system default sink and audio streams to the primary hardware output (GA102 HDMI / motherboard speakers) with automatic stream health recovery (`_ensure_stream()`).
  - **TUI & Status Display**: Real-time display of active audio output device in the MP Audio Player panoramic header (`🎧 Output: Crusher ANC 2 [Bluetooth AAC]` or `🔊 Output: Speakers`), new key binding `[a]` to force route or inspect Bluetooth audio status, and on-screen toast notifications (`🎧 Auto-switched to Bluetooth: Crusher ANC 2 (AAC)`).
  - **Audio Launchers Suite Integration**: Added Option 16 to `Mix_Archive_Manager.sh` Audio Players menu for Bluetooth device inspection, manual routing, and launching the live monitor.
  - **Standalone Daemon & CLI Control**: Created `scripts/monitor_bluetooth_audio.py` / `.sh` with `--daemon`, `--monitor`, `--route`, `--revert`, and `--status` options.

- **New Special Theme — Cosmic Supernova [Special Edition]** (`Mix_Archive_Manager.sh`, `themes/Cosmic_Supernova_MP_Mix_Manager.theme`, `MP_Audio_Player.py`, `Congen/Congen`):
  - **Interstellar 24-Bit TrueColor Palette**: Designed a bespoke deep-space celestial aesthetic inspired by uplifting space trance and astrophysics:
    - Supernova Crimson / Flare Red (`#ff3366` / `\033[38;2;255;51;102m`)
    - Aurora Borealis Aquamarine / Mint (`#00f5d4` / `\033[38;2;0;245;212m`)
    - Solar Flare Gold / Starfire Amber (`#fee440` / `\033[38;2;254;228;64m`)
    - Hyper-space Deep Indigo (`#5e60ce` / `\033[38;2;94;96;206m`)
    - Luminous Electric Nebula Orchid (`#e056fd` / `\033[38;2;224;86;253m`)
    - Pulsar Starlight Cyan (`#4cc9f0` / `\033[38;2;76;201;240m`)
  - **Companion BTOP System Monitor Theme**: Hand-crafted dedicated BTOP theme file `themes/Cosmic_Supernova_MP_Mix_Manager.theme` with deep interstellar void background (`#0a0814`), glowing nebula borders, and smooth aurora-to-supernova telemetry meters. Auto-installed and configured upon selecting the theme.
  - **Dynamic Interactive Menu Highlighting**: Integrated theme-aware menu cursor selection styling in `highlight_menu_line()` that glows in radiant Nebula Orchid, Aurora Aquamarine, Solar Flare Gold, and Deep Cosmic Indigo with custom contrast and pointers (`▶ ❯`).
  - **MP Audio Player Equalizer & High-Res Spectrum Integration**: Added `"cosmic_supernova"` to `EqualizerVisualizer.THEMES` with a custom 4-tier spectrum gradient (Aurora Mint -> Pulsar Cyan -> Nebula Orchid -> Supernova Crimson peak) and automatic active theme synchronization from `~/.config/mix-manager/theme`.
  - **Congen Remote Control & CLI Suite Integration**: Added `cosmic_supernova` to Congen's 13-theme switcher, CLI command (`congen theme cosmic_supernova`), and custom starry ASCII banner (`✦ COSMIC SUPERNOVA PRODUCTIONS ✦`).
  - **Auditory Feedback**: Automatically triggers soothing celestial audio chime feedback on theme switch via PipeWire / ALSA.

## [0.3.5] - 2026-10-09

- **MP Audio Player — DREAMWORLDS ULTRA AUDIO MODE & Layout Polish** (`MP_Audio_Player.py`):
  - **Full 118-Column Grid Widening**: Extended the top banner box and playlist window to 117 characters (115 inner columns) aligning flush with the right boundary of the DJ Pitch Window (column 118), expanding mix title display from 51 up to 92 characters.
  - **Branding & Playlist Header Updates**: Updated main banner title to `✦ MP AUDIO PLAYER — DREAMWORLDS ULTRA AUDIO MODE ✦` and playlist section title to `── MP MIX ARCHIVE PLAYLIST ──`.
  - **Instant Tracklist Viewer (`T` / `t` / `--tracklist`)**: Pressing `T` opens the associated `.txt` tracklist for the selected or currently playing mix in a dedicated borderless console window (`scripts/view_tracklist_console.sh`) with fallback notifications when absent.
  - **Universal Playlist Exporter (`E` / `w` / `--export-playlist`)**: Exports the entire archive catalog as a `.m3u` file only (`#EXTM3U` format with `#EXTINF`), prompting with native KDE `kdialog` / `zenity` file chooser dialogs, an inline terminal input fallback, or direct CLI execution.
- **Core Performance Optimization & Telemetry Caching** (`Mix_Archive_Manager.sh`):
  - Added short TTL (5–30s) caching layers for main menu status and telemetry indicators (`show_stats`, `get_system_perf_stats`, `get_active_audio_interface_display`, `get_alarm_clock_status_display`, `get_planets_above_horizon`, and banner rendering).
  - Main menu redraw latency slashed from **~1.7 seconds down to <20ms**, delivering instantaneous arrow-key and submenu navigation responsiveness.
  - Implemented `invalidate_archive_stats_cache()` for automatic event-driven cache clearing upon audio conversion, tagging, or import operations.
- **Script Directory Synchronization & Canonical Paths**:
  - Synchronized and aligned all dual-homed executables between root and `./scripts/` (`Make_SOF_FLAC_CONVERSION.sh`, `SOF_Archive_Stats.sh`, `generate_master_tracklist.py`, `import_new_mixes.py`, `manage_playlists.py`, and Plasma switchers).
  - Ensured root and subfolder scripts are identical and that `run_sub_script()` always executes the most up-to-date versions with OGG Vorbis support and subprocess safety.
- **Bug Fix**:
  - Corrected unhandled `try:` syntax exception in `scripts/schedule_mix_playback.py`, allowing the automated DJ mix playback daemon and Option 19.13 to run cleanly without silent failures.
- **Streaming & Platform Chapter Exporter** (`scripts/export_chapters.py`, `export_chapters.sh`):
  - Integrated YouTube Video Description Chapters (`00:00:00 Artist - Title`), SoundCloud/Mixcloud timestamps, and Markdown/HTML chapter tables.
  - Auto-validates timestamps against physical audio durations and copies chapters to clipboard (`wl-copy`/`xclip`/`pbcopy`).
- **Lossless In-Container Metadata & ReplayGain Suite** (`scripts/tag_audio_metadata.py`, `tag_audio_metadata.sh`):
  - Non-destructive EBU R128 loudness analysis and ReplayGain Vorbis comments (`REPLAYGAIN_TRACK_GAIN` & `REPLAYGAIN_TRACK_PEAK`) for smooth volume normalization across audio players without altering audio bits.
  - Automatic front cover art embedding (`metaflac --import-picture-from=3`) and CUE sheet container injection.
- **Audio Fingerprinting & Track Identifier** (`scripts/identify_mix_tracks.py`, `identify_mix_tracks.sh`):
  - Slices audio snippets at transition intervals to query AcoustID / Chromaprint (`fpcalc`) or online recognition endpoints to auto-identify unknown tracks in live recordings.
- **Pioneer Rekordbox XML Playlist Exporter** (`scripts/export_rekordbox_playlist.py`, `export_rekordbox_playlist.sh`):
  - Converts Traktor `.nml` histories/collections, CUE sheets, and M3U playlists into standard Pioneer Rekordbox XML (`DJ_PLAYLISTS.xml`), ready for CDJ USB export.
- **Audio Mastering Quality & Health Audit** (`scripts/verify_audio_mastering.py`, `verify_audio_mastering.sh`):
  - Multi-stage diagnostic checking True Peak / Inter-Sample Peaks (ISP), EBU R128 integrated loudness & LRA, stereo phase correlation (mono cancellation protection), and leading silence.
- **Mobile Web Companion Server for DJ Booth & Studio** (`scripts/mix_web_companion.py`, `mix_web_companion.sh`):
  - Zero-dependency local Python HTTP server providing a mobile-friendly Dreamworlds dark/neon web remote on port 8888 for phones and tablets.
- **Unified Notification Dispatcher** (`scripts/send_notification.sh`):
  - Centralized dispatcher supporting desktop notifications, Discord webhooks, Telegram bots, and Pushover alerts upon completion of long tasks.
- **Automated Verification Test Suite** (`tests/run_tests.sh`):
  - Comprehensive automated test runner verifying Bash syntax (`bash -n`), Python syntax (`py_compile`), configuration integrity, and script synchronization.

## [0.3.4] - 2026-10-01

- **System RAM & PageCache Memory Purge Suite** (`Mix_Archive_Manager.sh`):
  - Added dedicated option `6) Clear System Memory & Page Cache (sudo sync && drop_caches=3)` to the Universal System Maintenance & Cleanup Menu (Option 29).
  - Displays real-time RAM usage in the header (`used`, `cached`, `available`) before and after clearing.
  - Safely flushes dirty filesystem buffers with `sync` before invoking `drop_caches=3` to release PageCache, dentries, and inodes.
  - Automatically detects active swap usage (>50MB) and offers optional swap memory cycling via `swapoff -a && swapon -a` to flush swapped memory back into physical RAM.
  - Integrated automatic RAM cache purging directly into Option 7 (`Run Complete Cleanup Suite`) as step [2/4].

- **Fix Multi-Part Split WAV Chunk Grouping & Chronological Assembly** (`Make_SOF_FLAC_CONVERSION.sh`, `MOVE_NOT_CONVERTED_WAVS.sh`):
  - **Resolved Premature Session Fragmentation**: Fixed a critical bug where long recordings split across multiple WAV files (e.g. `..._YYYY-MM-DD_HhMMmSS.wav` and subsequent chunks `..._YYYY-MM-DD_HhMMmSS_03h02m02.wav`, `..._06h04m05.wav`) were incorrectly split into two separate FLAC outputs because naive regex stripped the session start timestamp from Part 1 while preserving it on subsequent chunks.
  - **Unified Session Grouping**: Introduced `get_session_base_name` which accurately differentiates between session start timestamps and subsequent elapsed split duration offsets, ensuring all split chunks for a recording session resolve to the exact same master session ID.
  - **Sample-Accurate Chronological Concatenation**: Replaced locale-dependent collation sort with split-offset key extraction, ensuring Part 1 (offset 0s) and subsequent parts (e.g. 03h02m02, 06h04m05, 09h06m07) are always assembled in exact chronological sequence.
  - **Unconverted WAV Verification Parity**: Updated `MOVE_NOT_CONVERTED_WAVS.sh` to use the unified session base extractor and search all standardized FLAC archive filename variants.

- **Cloud Storage & Google Drive FUSE Performance Acceleration**:
  - Replaced legacy multi-pass bash `stat` and `[ -f ]` shell loops in `show_stats()` with a single-pass Python `os.scandir` in-memory hash indexer, slashing startup & menu redraw latency from **20-30+ seconds down to ~0.3 seconds (1,000x faster)**.
  - Implemented in-memory memoization for `get_all_mix_archive_dirs`, `get_all_flac_output_dirs`, and `get_all_wav_archive_dirs`, preventing redundant network filesystem directory traversals across FUSE mounts.
  - Resolved duplicate archive container scanning where parent directories with existing `FLAC_CONVERTED_OUTPUTS` folders were scanned twice.
  - Optimized `get_cloud_backup_badge()` to exclude remote cloud paths (`*GoogleDrive*`, `*rclone*`) from being checked as files pending backup to the cloud, eliminating hundreds of remote `stat` calls.
  - Avoided redundant `mkdir -p` operations on FUSE mount points during startup when `PLAYLISTS_GENERATED` folders already exist.

- **YouTube 1080p Downloader (Videos & Shorts Suite Expansion)** (`scripts/MP_YouTube_Channel_Downloader.sh`, `MP_YouTube_Channel_Downloader.sh`):
  - **Single YouTube Video & Shorts Mode**: Download individual videos or shorts directly from any URL (`watch?v=`, `youtu.be/`, `/shorts/`) or 11-char video ID, with `--no-playlist` isolation and organized storage (`YOUTUBE_SINGLE_VIDEOS` or `YOUTUBE_SINGLE_SHORTS`).
  - **Dedicated YouTube Shorts Channel Mode**: Added options 6–9 for downloading channel shorts across **Last 1 Month**, **Last 3 Months**, **Last 6 Months**, or **Entire Channel (All Shorts)** targeting `/@channel/shorts` and saved to `YOUTUBE_[CHANNEL]_SHORTS_DOWNLOAD_[DATE]`.
  - **Full 1080p Vertical Format Selection**: Configured format selection (`bestvideo[height<=1920][width<=1080]`) so vertical 1080x1920 shorts are downloaded in full 1080p HD without being downscaled to 480p.
  - **Multi-Timeframe Channel Video Downloads**: Retained selectable download ranges for standard long-form videos (`/@channel/videos`).
  - **Smart URL & Handle Parsing**: Automatically recognizes single video links vs channel handles (`@channel`, `/channel/UC...`, `/c/...`), stripping quotes and whitespace from drag-and-drop inputs.
  - **Accelerated Streaming**: Preserves high-speed `--lazy-playlist` streaming, automatic Node/Deno JS engine detection, live network diagnostics, and optional proxy integration.
- **Interactive Audio ID3/Vorbis Tag Customizer & Cover Selector**:
  - Integrated into audio conversion pipelines (`Make_SOF_FLAC_CONVERSION.sh` and `scripts/convert_audio_format.sh`).
  - Prompts optionally before conversion to set common tags: Artist Name, Mix Title, Album, Release Year, Genre, and Description/Comment.
  - Interactive Cover Art selector: automatically scans for local/mix archive covers (`Cover.png`, `./COVERS/`, etc.) or accepts custom full filepaths with drag-and-drop quote-stripping and tilde expansion.
  - Non-blocking zero-friction defaults: pressing Enter immediately retains standard tags (`Artist=MPlanetarian`, `Album=Stream of Frequency`, auto-derived title) and default cover art (`Cover.png`).
- **Loudness Mastering, CUE Engine & Archive Bit-Rot Resilience Suite**:
  - **EBU R128 Audio Loudness & True-Peak Mastering Suite** (`scripts/master_audio_loudness.py`, `master_audio_loudness.sh`):
    - Full analysis of Integrated Loudness (LUFS), True-Peak max (dBTP), Loudness Range (LRA), and threshold headroom.
    - Two-pass transparent linear normalization targeting Streaming (-14 LUFS / -1.0 dBTP for Spotify/YouTube/Apple), Podcasts (-16 LUFS), Broadcast (-23 LUFS EBU R128), and DJ/Club (-10.5 LUFS / -0.3 dBTP).
    - Preserves metadata tags, sample rate fidelity, and dynamic expression without pumping or distortion.
  - **Standard Red Book CUE Sheet Engine & Splitter** (`scripts/generate_cue_sheet.py`, `generate_cue_sheet.sh`):
    - Generates 100% compliant CDRWIN / EAC Red Book `.cue` sheet files from text tracklists, timestamps, or Traktor `.nml` history.
    - Frame-accurate CDDA timecode indexing (`MM:SS:FF` at 75 frames/second).
    - Built-in audio splitter to cleanly cut full mixes into individual tagged FLAC/WAV tracks.
  - **Lossless Legitimacy & Spectral Cutoff Inspector** (`scripts/verify_lossless_spectral.py`, `verify_lossless_spectral.sh`):
    - Fast multi-segment FFT spectral power analysis detecting upscaled lossy transcodes ("Fake FLACs").
    - Accurately detects low-pass brickwall shelves: MP3 128k (~16 kHz), MP3 192k (~18.5 kHz), and MP3 320k / AAC 256k (~20.5 kHz).
    - Generates visual ASCII spectral power distribution charts and confidence ratings.
  - **Automated Mix Archive Bit-Rot Scrubber & Systemd User Timer** (`scripts/scrub_mix_archive.sh`, `scrub_mix_archive.sh`):
    - Non-destructive full-archive frame decode integrity audit (`flac -t` / `ffmpeg`) across all storage archives.
    - Runs in low-priority background mode (`nice 19`, `ionice 3`) with zero impact on desktop or gaming tasks.
    - Dispatches critical desktop notifications (`notify-send`) immediately upon detecting corruption.
    - Includes automated Systemd User Timer (`mix-archive-scrub.timer`) for scheduled weekly background scrubs.
  - **Real-Time Cloud Backup Live Badge**:
    - Added live sync badge in the main startup banner and `show_stats()` indicating cloud backup state (`Up-to-date` vs `N mixes pending sync`).
    - Tracks backup sessions in `~/.config/mix-manager/cloud_backup_state.json`.
  - **Menu Expansions**:
    - Enhanced **Option 2** with direct sub-options for EBU R128 scanner, 2-pass normalizer, and CUE generator.
    - Enhanced **Option 8** with spectral cutoff inspector, non-destructive bit-rot scrub, systemd timer management, and scrub logs.

## [0.3.3] - 2026-10-01

- **WAN2GP Server Manager & LTX-2.5 22B LoRA Expansion**:
  - Expanded the WAN2GP Server Manager menu (Main Menu 31 -> Option 4, and direct Option 56) to 25 operations.
  - Added dedicated options 20–23 for batch processing with LTX-2 2.5 22B and specialized LoRA models:
    - **Option 20**: Run LTX-2 2.5 22B Batch Processor with EditAnything v2 LoRA
    - **Option 21**: Run LTX-2 2.5 22B Batch Processor with BFS Head Swap IC-LoRA
    - **Option 22**: Run LTX-2 2.5 22B Batch Processor with Deblur & Decompression LoRAs
    - **Option 23**: Run LTX-2 2.5 22B Batch Processor (Clean Base / Video+Audio)
    - **Option 24**: Scan & Remove Byte-for-Byte Duplicate Images (`remove_duplicate_images.py`)
    - **Option 25**: Return to Main Menu
  - Updated `launch_wan2gp_ltx_batch_terminal` in `Mix_Archive_Manager.sh` with direct `--lora` parameter dispatch and title tagging.
  - Added `--lora editanything_ref` (EditAnything Reference v0.1 Standard) support in `scripts/wan2gp_ltx_batch.py`.

## [0.3.2] - 2026-09-28

- **Monitor System Processes and Bash Commands with System Info**:
  - Added main-menu ID **`m`** directly under option 29 (Universal System Maintenance & Cleanup).
  - Inside option 29 the same item is **8** on macOS and Windows, and **7** on Linux and FreeBSD. `m` works there too.
  - Opens `scripts/MP_Monitor_Bash.sh` in a new tab of the terminal already running Mix Manager (macOS Terminal, iTerm, GNOME Terminal, Konsole, Windows Terminal).
  - The monitor runs on macOS and Linux: last 10 shell commands, top processes, CPU, memory, disk, Wi-Fi, GPU, and temperatures. Each refresh appends one line to `running_history.log` and does not clear earlier entries.
  - Refresh choices are 10, 5, or 2 seconds. Direct shortcuts: `m`, `monitor`, `mp-monitor`.

## [0.3.1] - 2026-09-25

- **Listen to Your Top 5 Tracks Right Now (Pre-Selected - Special Option) [Unlock Mystery / Ready]**:
  - Added dedicated Main Menu Option 22 in Section 2 with dynamic custom menu status badge (`[Unlock Mystery]` vs `[Ready]`).
  - Added Option 4 to Custom Mix Playlists & Traktor History Suite (Option 14).
  - Requirement Gates & Alert: Requires Master HTML Tracklist (`master_tracklists.html`) and at least one processed mix in the archive. Automatically alerts user upon completing their first mix conversion in `Make_SOF_FLAC_CONVERSION.sh` about unlocking this special feature.
  - Top 5 Tracks Selector: Intelligently parses `master_tracklists.html` across all combined archives (1,033+ plays, 656+ unique tracks) with search by artist, title, and mix source.
  - Traktor Database & Multi-Protocol Remote Mac Import: Searches Traktor collections (`collection.nml` and `History/*.nml`) for exact `AUDIO_ID`, title, and artist matches. Resolves and transfers physical audio tracks from local drives, SMB network shares, NFS mounts, or remote macOS workstations via SSH/rsync into `TOP_5_TRACKS/`.
  - Instant Playlist & Immediate Playback: Automatically generates `Mix_Archive_Top_5_Tracks_Traktor_Playlist.m3u8`, `.m3u`, and `.xspf` in `PLAYLISTS_GENERATED/` and immediately triggers Track #1 playback in the configured audio player (`audacious`, `strawberry`, `cliamp`, `vlc`, etc.).
  - Continuous Joined Mix Export Suite: Joins the 5 tracks into a continuous mix file with custom artist, title, and cover image metadata, automatically named `Mix_Archive_Top_5_Tracks_[Date]_Auto_Generated_by_MP_Mix.FLAC`. Generates matching master WAV, 320kbps MP3 with embedded artwork, high-resolution Spek spectrogram PNG, and timestamped Tracklist (.txt) & Cue sheet (.cue).
  - CLI and Fast-Path Dispatch: Added `--top5`, `--top-5`, and `top5` shortcuts to `Mix_Archive_Manager.sh`, `bin/mix-archive-manager`, and created `scripts/manage_top_5_tracks.sh`.

## [0.3.0] - 2026-09-24

- **Congen (KDE Connect Commands Generator) Full Integration**:
  - Fully integrated Congen (`Congen/`) into the MP Mix Archive Manager codebase with executable launchers in root (`congen`, `congen.sh`), `scripts/`, and `bin/`.
  - Added Congen management to Section 3 Option 26 (**Network Services, Congen & Internet Control**) providing direct access to KDE Connect command generation, inventory inspection, remote task scheduling, audit logging, audio alert daemon (`.congen_alert_daemon.sh`), and template imports (`CONGEN_IMPORT/`).
  - Added direct CLI shortcuts: `mix-archive-manager congen`, `./Mix_Archive_Manager.sh --congen`, and main menu shortcut `congen` / `kdeconnect`.
  - Documented mobile remote execution commands for MP Mix Archive Manager, Strawberry player, and studio workflows across the application manuals and GitHub README.

- **MPlanetarians Alarm Clock (Wake Up Edition) Integration & Live Dashboard Status**:
  - Fully integrated `mplanetarians-alarm-clock/` into the manager codebase with executable launchers in root, `bin/`, and `scripts/`.
  - Added live Alarm Clock Status Banner to the Main Page dashboard showing arm state (`Armed & Ready` / `Disabled`), next alarm countdown with systemd timer integration, sound source, Steam library breakfast readiness, morning wake-up action, and active theme.
  - Upgraded Main Menu Option 18 to **Morning Alarm Clock & DJ Mix Playback Suite** with an interactive 14-point control menu: daily brief morning screen, full TUI alarm control, alarm scheduling, timer listing, silence/snooze, theme switcher, wake actions, and notes.
  - Added direct CLI shortcuts: `mix-archive-manager alarm`, `./Mix_Archive_Manager.sh --alarm`, and main menu shortcut `alarm`.
  - Featured prominent spotlight on GitHub README advertising random mix wake-up, 3-minute volume ramp, mouse motion wake detection, secondary right-monitor browser launch, and 3 random Steam video games for breakfast (Linux only).

- **Master HTML Tracklist no longer hangs on the Google Drive archive**:
  - The generator was opening tracklists and FLAC headers through the rclone mount. Those reads blocked forever in uninterruptible sleep while scanning Additional Storage #2.
  - Cloud archives are now read from the local rclone VFS cache, with `rclone cat` timeouts for anything not cached. Local disks are unchanged.
  - The manager launches the repository copy of `Generate_Master_Tracklist.sh` so an older copy inside the mix archive cannot shadow the fix.

- **Record Video of DJ Mix using GPU Screen Recorder (Linux)**:
  - Added main menu Section 2, option 21 in [`Mix_Archive_Manager.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/Mix_Archive_Manager.sh).
  - Launches GPU Screen Recorder in a new desktop window (`gpu-screen-recorder-gtk`, or Flatpak `com.dec05eba.gpu_screen_recorder`) and returns to the menu.
  - Direct shortcut: `mix-archive-manager gpu-screen-recorder` (or `record-mix`).
  - Section 3 menu numbers moved from 21–31 to 22–32. Exit is option 32 (`0`, `q`, and legacy `77` still exit).

- **CLI Help & Options Reference (`mix-archive-manager --help` / `-h`)**:
  - Added `-h`, `--help`, and `help` launch flags to [`bin/mix-archive-manager`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/bin/mix-archive-manager) and [`Mix_Archive_Manager.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/Mix_Archive_Manager.sh).
  - Built dedicated help engine in [`scripts/show_help.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/scripts/show_help.sh) displaying full usage instructions, launch flags, direct shortcuts, and exhaustive listings of all 31 main menu options across all 3 sections.
  - Added direct execution support for menu options and shortcuts from the command line (e.g. `mix-archive-manager 13`).

- **Project Promotion to Version 0.3.0 (`MP_Mix_Manager_v0.3`)**:
  - Full project upgrade and version promotion across all launchers, scripts, configurations, and documentation.
  - Centralized version tracking bumped to `0.3.0` in [`VERSION`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/VERSION).

- **Dedicated Converted Output Directories & Media Routing**:
  - Added dedicated output folders:
    - `MP3_CONVERTED_OUTPUTS/` for all converted MP3 audio files.
    - `WAV_CONVERTED_OUTPUTS/` for all converted WAV audio files.
    - `MP4_CONVERTED_OUTPUTS/` for all synthesized YouTube videos.
  - Automatically scaffolded across local workspace, user home directory, and external storage (`MIX_ARCHIVE_DIR`).
  - Added `.gitkeep` markers and updated `.gitignore` rules.
  - Integrated into [`install.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/install.sh) and [`Mix_Archive_Manager.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/Mix_Archive_Manager.sh).
  - Updated archive statistics counter to monitor file counts across all 5 archive directories.

- **Audio Conversion & YouTube Video Routing**:
  - Re-routed YouTube video synthesis scripts ([`generate_youtube_video.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/generate_youtube_video.sh), 4K, 1080p, 720p, and MP4/WAV loop generators) to output into `MP4_CONVERTED_OUTPUTS/`.
  - Re-routed universal audio converter ([`convert_audio_format.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/convert_audio_format.sh)) to automatically route MP3 outputs to `MP3_CONVERTED_OUTPUTS/` and WAV outputs to `WAV_CONVERTED_OUTPUTS/`.
  - Expanded search paths to automatically discover audio candidates across `FLAC_CONVERTED_OUTPUTS`, `CONVERTED_WAV_FILES`, `MP3_CONVERTED_OUTPUTS`, and `WAV_CONVERTED_OUTPUTS`.

- **Condensed Main Menu**:
  - Streamlined the primary console interface into 3 clean, uncluttered sections with 10 direct, organized options each (30 options total on the main screen).

- **Cross-Platform Compatibility**:
  - Fixed macOS Bash 3.2 compatibility issues in FLAC conversion and batch scripts (`declare -A` associative array syntax replaced with POSIX/Bash 3-compatible lookups).

---

## [0.2.1] - 2026-09-18

- **Header Text Refresh with Dynamic Date, Version & Time**:
  - Updated the top application banner header in [`Mix_Archive_Manager.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/Mix_Archive_Manager.sh):
    - Replaced static `Mix Archive Manager (MP_Mix_Manager_v0.3)` with `MP Mix Archive Manager (<Date>) | Version: <Version> | Current Time: <Time>`.
    - Added `get_header_ordinal_date()` helper for formatted dates with ordinal day suffixes (e.g. `18th of September, 2026`).
    - Added `get_manager_version()` helper reading centrally from `VERSION`.
    - Real-time timestamp refresh (`date "+%T"`).

- **Planetary Horizon Ephemeris Calculator & Banner Integration**:
  - Added standalone ephemeris calculator [`scripts/get_planets.py`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/scripts/get_planets.py) and [`scripts/get_planets.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/scripts/get_planets.sh) (mirrored to root):
    - Computes real-time altitude, azimuth, and visibility for all 7 major planets using NASA JPL Standish Keplerian orbital elements with 100% offline calculation.
    - Integrated live planetary visibility banner below weather in the manager main application loop.
    - Added planetary ephemeris configuration (`PLANETS_ENABLED`) and full celestial viewer in Meteorological & Planetary Settings.

- **Manager Self-Update System (`mix-archive-manager update` / Option 73)**:
  - Added dedicated system updater [`scripts/update_manager.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/scripts/update_manager.sh) (mirrored to `update_manager.sh`):
    - Run `mix-archive-manager update` (or `manager update`, `~/manager.sh update`) to automatically check for and install the latest version from remote Git.
    - If already running the latest version, cleanly reports: `Mix Archive Manager is running the latest version: vX.Y.Z` and active commit hash.
    - If updates are available, safely stashes uncommitted local changes, pulls/rebases changes, restores permissions, refreshes symlinks in `~/.local/bin`, and updates the dynamic MOTD banner.
    - Added `--version` / `-v` flag to display currently installed version and commit date.
    - Added `--check` flag to probe for remote updates without installing.
  - Added fast-path CLI dispatch in [`bin/mix-archive-manager`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/bin/mix-archive-manager) and [`Mix_Archive_Manager.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/Mix_Archive_Manager.sh) to bypass disk mounting wait during updates or version queries.
  - Added Option 6 ("Check & Install System Updates") to [`scripts/manage_installation_config.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/scripts/manage_installation_config.sh) (Menu Option 73).
  - Created [`VERSION`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/VERSION) file for centralized version tracking.

- **Full File Name MOTD Display & Recent Releases Count Update**:
  - Upgraded [`scripts/manage_motd.py`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/scripts/manage_motd.py) and [`manage_motd.py`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/manage_motd.py):
    - Replaced 38-character filename truncation with the full, un-truncated file name.
    - Updated column header to `Full File Name`.
    - Dynamically scales banner borders, headers, and dividers (`banner_width = max(78, 49 + max_name_len)`) to maintain clean alignment with filenames of any length.
    - Reduced default displayed releases from 5 to 3 (`find_recent_mixes(limit=3)` with `--limit` flag support).
    - Auto-regenerated active terminal MOTD at [`~/.config/mix-manager/motd`](file:///home/mplanetarian/.config/mix-manager/motd).

- **Multi-Display Window Positioning (Manager on Primary, Strawberry & Cover on Secondary)**:
  - Upgraded [`scripts/align_mix_windows.py`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/scripts/align_mix_windows.py) to automatically detect active monitor configuration:
    - **Multi-Display Mode (> 1 Displays Active)**:
      * Mix Archive Manager window is positioned onto the **Primary Display** (Priority 1 screen in KDE Plasma 6 KWin / X11).
      * Strawberry Audio Player and Cover Art Viewer (Gwenview, feh, loupe, eog) are placed onto the **Secondary Display** (not primary).
      * Windows on the secondary display are arranged side-by-side with zero overlap (square 1:1 album cover art on the left, Strawberry music player with controls and playlist on the right).
      * If Tracklist Viewer is also open, windows are arranged in a 3-column layout on the secondary screen.
    - **Single-Display Mode (<= 1 Display Active)**:
      * Only triggers multi-display placement if > 1 displays are active. On single-display setups, keeps windows on the active screen with the centered floating HUD layout.
  - Enhanced [`Mix_Archive_Manager.sh`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/Mix_Archive_Manager.sh) & [`bin/mix-archive-manager`](file:///var/home/mplanetarian/MP_Mix_Manager_v0.3/bin/mix-archive-manager):
    - Emits dynamic terminal window title escape sequences (`printf '\033]0;Mix Archive Manager\007'`) so Konsole and other terminals are immediately recognized.
    - Updated `align_mix_windows_on_screen()` to pass manager PID and parent terminal PID to `align_mix_windows.py`.
    - Automatically triggers window alignment on manager startup, during autoplay, on active audio detection, and when launching Strawberry.

- **DeepSeek Harness Web Server & Mobile Interface Management (`dsh-mobile` - Option 59)**:
  - Added standalone controller script `scripts/dsh_mobile.sh` (mirrored to `dsh_mobile.sh`, symlinked to `bin/dsh-mobile`, `bin/manage-dsh-mobile`, `~/.local/bin/dsh-mobile`, and `~/.local/bin/manage-dsh-mobile`):
    - Manages `pnpm dsh web` running at `/var/home/mplanetarian/beszel-hub/deepseek-harness` with LAN mobile trust (`--trusted-host 192.168.1.11:3080 --trusted-host 192.168.1.11 --no-open`) on port 3080 (`http://192.168.1.11:3080`).
    - Supports starting in a dedicated terminal window (Konsole tab, xdg-terminal-exec, gnome-terminal, xterm) or in background daemon mode (logging to `/tmp/dsh-mobile.log`).
    - Fast process and socket detection (`ss`, `lsof`, `pgrep`) with clean termination (`SIGTERM` and fallback `SIGKILL`).
    - Server status and environment inspection: reports process state, PID, local URL (`http://localhost:3080`), LAN mobile URL (`http://192.168.1.11:3080`), port listening status, harness directory, and latest git commit.
    - One-click browser dispatch to open the mobile web interface in the default browser.
    - Live server log viewer inspecting `/tmp/dsh-mobile.log`.
  - Integrated into `Mix_Archive_Manager.sh`:
    - **Option 59**: `Manage DeepSeek Harness Server (dsh-mobile - Start, Stop, Mobile Web UI :3080)` in Section 6 (System, Network & Hardware Management).
    - Added to **Option 70 (manage_ai_models)**: Options 7 & 8 to launch and manage `dsh-mobile` alongside Claude Sonnet, Claude Opus, GPT-OSS, Gemini, and Ollama.
    - Live task detection in `show_stats`: actively detects running `dsh-mobile` processes and displays them under running background tasks with PID and URL.
    - CLI flags: `--dsh-mobile`, `--dsh`, `--dsh-start`, `--dsh-bg`, `--dsh-web`, `--dsh-stop`, `--dsh-restart`, `--dsh-logs`, and `--dsh-status`.
    - Subcommand arguments: `59 1` (Start Window), `59 2` (Start BG), `59 3` (Web UI), `59 4` (Stop), `59 5` (Restart), `59 6` (Logs), `59 status`.
    - Menu prompt keyword triggers: `dsh`, `dsh-mobile`, `dsh_mobile`, `dsh-start`, `dsh-web`, `deepseek`, `deepseek-harness`.
    - Renumbered Section 6 & 7 operations up to Option 75 (Exit).
  - Updated `scripts/kc_launch_dsh_mobile.sh` and KDE Connect integration to delegate to the unified `dsh_mobile.sh` engine.
  - Updated `install.sh` to install `bin/dsh-mobile`, `bin/manage-dsh-mobile`, and user local bin symlinks.

- **Ollama Server & Local LLM Management (Option 58)**:
  - Added standalone controller script `scripts/manage_ollama.sh` (mirrored to `manage_ollama.sh`, symlinked to `bin/manage-ollama` and `~/.local/bin/manage-ollama`):
    - Manages `ollama serve` running inside distrobox container `ollama-container` on port 11434 (`http://127.0.0.1:11434`).
    - Supports starting in background daemon mode or in a dedicated terminal window (Konsole tab, xdg-terminal-exec, gnome-terminal, xterm) for viewing live inference logs.
    - Includes automatic podman container pre-check (`podman start ollama-container`) and endpoint responsiveness loop.
    - Graceful process termination with `SIGTERM` and fallback `SIGKILL`.
    - Live server status and hardware inspection: reports HTTP 200 health, API endpoint, PID, container state, NVIDIA GPU model/VRAM (CUDA enabled), and lists all installed local models with parameter size, quantisation level, and disk footprint.
    - Interactive CLI chat launcher allowing selection from installed local models (Qwen 2.5, LLaMA 3.1, Nemotron, SmolLM2, etc.) for direct inference inside the container.
    - Server log viewer inspecting `/tmp/ollama-serve.log`.
  - Integrated into `Mix_Archive_Manager.sh`:
    - **Option 58**: `Manage Ollama Server` in Section 6 (System, Network & Hardware Management).
    - Added to **Option 69 (manage_ai_models)**: Direct access to manage Ollama and run local models alongside Claude Sonnet, Claude Opus, GPT-OSS, and Gemini.
    - CLI flags: `--ollama`, `--ollama-serve`, `--ollama-start`, `--ollama-window`, `--ollama-stop`, `--ollama-restart`, `--ollama-chat`, `--ollama-logs`, and `--ollama-status`.
    - Subcommand arguments: `58 1` (Start BG), `58 2` (Start Window), `58 3` (Stop), `58 4` (Restart), `58 5` (Chat), `58 6` (Logs), `58 status`.
    - Menu prompt keyword triggers: `ollama`, `ollama-serve`, `ollama-start`.
    - Renumbered Section 6 & 7 operations up to Option 74 (Exit).
  - Updated `install.sh` to automatically install `bin/manage-ollama` and `~/.local/bin/manage-ollama` symlinks.

- **Strawberry Music Player Integration & Startup Autoplay Fix**:
  - Configured Strawberry as default audio player (`DEFAULT_AUDIO_PLAYER="strawberry"`) across `Mix_Archive_Manager.sh`, `config.env`, and `config.env.example`.
  - Resolved issue where launching the manager automatically opened `cliamp` even when Strawberry was already playing.
  - Added active playback pre-check in `execute_startup_autoplay`: detects if Strawberry or any other player is currently playing before initiating playback or launching external windows.
  - Implemented `get_strawberry_track_info` via MPRIS D-Bus (`qdbus`, `dbus-send`, and `playerctl` fallbacks) to report live playback status, track title, artist, album, elapsed time, total duration, progress percentage, and resolved file path.
- **Fix Startup Display Behavior**:
  - Eliminated unexpected intermediate Live Tracklist / Now Playing preview screen and delay on manager launch.
  - When background playback is active (e.g. Strawberry), `execute_startup_autoplay` now returns cleanly and immediately without calling `auto_show_playing_mix_assets` or popping up tracklist windows.
  - Removed disruptive 3-second preview delay from the startup sequence so the manager main window loads instantly.
  - Updated `scripts/view_tracklist_console.sh` interactive loop to ignore unconsumed newline / empty inputs and avoid premature window closing.

- **FLAC & YouTube MP4 Video Splitting Utilities**:
  - Implemented standalone interactive utility `Split_FLAC_File.sh` (mirrored to `scripts/Split_FLAC_File.sh`, symlinked to `bin/split-flac` and `~/.local/bin/split-flac`):
    - Discovers candidate `.flac` files in `FLAC_CONVERTED_OUTPUTS` and allows manual entry/pasting of custom full paths.
    - Prompts user for the number of parts $N$ and calculates duration per part with sample-accurate division.
    - Losslessly splits FLAC audio using `ffmpeg` (`-c:a flac -map_metadata 0`) with the final part capturing to the exact end without truncation.
    - Always outputs split parts (`<stem>_Part01.flac`, `<stem>_Part02.flac`...) into the exact directory of the input file and opens the file manager.
  - Implemented standalone interactive utility `Split_Video_File.sh` (mirrored to `scripts/Split_Video_File.sh`, symlinked to `bin/split-video` and `~/.local/bin/split-video`):
    - Discovers candidate `.mp4` video files across archive directories (`downloaded_videos`, `RENDERED_SOF_EPISODES`, etc.) and allows manual entry/pasting of custom full paths.
    - Prompts user for number of parts $N$ and split mode (Fast Lossless Stream Copy or Frame-Accurate Re-encode).
    - Divides video files into equal parts, naming them `<stem>_Part01.mp4`, `<stem>_Part02.mp4`... directly in the input video's directory and opens the file manager.
  - Integrated into `Mix_Archive_Manager.sh`:
    - **Option 2 (manage_audio_conversion)**: Added Option 6 to launch `Split_FLAC_File.sh`.
    - **Option 38 (manage_video_cut_and_split)**: Upgraded Option 38 to a Video Cutting & Splitting Suite (`Cut_Video.sh` & `Split_Video_File.sh`).
    - Added CLI flags `--split-flac`, `--split-video`, `--split-mp4`, and `--cut-video`.
    - Added direct console menu keyword triggers `split-flac` and `split-video`.
- **Beszel Server Monitoring Hub & Hardware/GPU Agent (Option 57)**:
  - Added standalone management script `scripts/beszel.sh` (mirrored to `beszel.sh`, symlinked to `bin/beszel` and `~/.local/bin/beszel`):
    - Controls Beszel Hub (`beszel` container on port 8090) and Beszel Agent (`beszel-agent` container with NVIDIA GPU passthrough and Podman socket access).
    - Features starting Hub, Agent, or both simultaneously with automatic container existence checks and launch script fallbacks (`launch_beszel_hub_replace.sh`, `launch_beszel_agent_replace.sh`).
    - Verifies user `podman.socket` service status and activates it automatically when starting the agent.
    - Includes live status inspection (container health, uptime, web dashboard HTTP health, agent-to-hub WebSocket status, NVIDIA GPU model/VRAM).
    - Added direct browser dispatch to open Beszel Web Dashboard (`http://localhost:8090`).
    - Added live container log viewer and streaming log follower for both Hub and Agent.
  - Integrated into `Mix_Archive_Manager.sh`:
    - **Option 57**: `Manage Beszel Server & Monitoring Agent` in Section 6 (System, Network & Hardware Management).
    - Added CLI flags `--beszel`, `--beszel-start`, `--beszel-hub`, `--beszel-agent`, `--beszel-dashboard`, `--beszel-stop`, and `--beszel-status`.
    - Added direct sub-option dispatch arguments (`57 1` to start both, `57 2` for Hub, `57 3` for Agent, `57 4` for Dashboard, `57 5` to stop).
    - Added direct console menu keyword triggers `beszel`, `beszel-start`, `beszel-hub`, and `beszel-agent`.
  - Registered into `install.sh` for automatic symlink and desktop entry management.
- **KDE Connect & Desktop Shortcuts for WAN2GP Server Management**:
  - Created dedicated KDE Connect remote commands and scripts (`kc_start_wan2gp_profile45.sh`, `kc_stop_wan2gp.sh`):
    - **Option 56 / 2**: `Start WAN2GP (Profile 4.5 - Low VRAM)` launches WAN2GP in Profile 4.5 in a new Konsole tab/window with active-process pre-check and desktop notification feedback.
    - **Option 56 / 3**: `Stop WAN2GP Server` safely terminates the active WAN2GP server with status notifications.
  - Registered remote commands into KDE Connect device configuration (`~/.config/kdeconnect/*/kdeconnect_runcommand/config`) and deployed to `~/KDE_CONNECT_CMDS/`.
  - Added direct command-line arguments to `Mix_Archive_Manager.sh` and `bin/mix-archive-manager`: `--wan2gp-start-4.5`, `--wan2gp-stop`, `56 2`, and `56 3`.
  - Created `.desktop` application entries (`wan2gp-start-profile45.desktop`, `wan2gp-stop.desktop`) in `desktop/`, `~/.local/share/applications/`, and `~/Desktop/` for KRunner/application menu launcher and global shortcut integration.
  - Added symlinks `wan2gp-start-profile45` and `wan2gp-stop` into `bin/` and `~/.local/bin/`.
  - Updated `install.sh` to install all desktop shortcuts, KDE Connect scripts, and symlinks automatically.
- **KDE Connect & Desktop Shortcuts for Ollama Server (distrobox: ollama-container)**:
  - Created dedicated KDE Connect remote commands and scripts (`kc_run_ollama_serve.sh`, `kc_stop_ollama.sh`):
    - `Run Ollama Server (distrobox)` enters `ollama-container` via `distrobox enter ollama-container -- ollama serve`, with active endpoint pre-check on port 11434, container auto-start, and desktop notifications.
    - `Stop Ollama Server` terminates active `ollama serve` processes with notification confirmation.
  - Registered remote commands into KDE Connect device configuration (`~/.config/kdeconnect/*/kdeconnect_runcommand/config`) and deployed to `~/KDE_CONNECT_CMDS/`.
  - Created `.desktop` application entries (`ollama-serve.desktop`, `ollama-stop.desktop`) in `desktop/`, `~/.local/share/applications/`, and `~/Desktop/`.
  - Added symlinks `ollama-serve` and `ollama-stop` into `bin/` and `~/.local/bin/`.
- **KDE Connect & Desktop Shortcuts for Beszel Hub & Agent and dsh-mobile**:
  - Created dedicated KDE Connect remote commands and scripts:
    - `Launch Beszel Hub & Agent` (`kc_launch_beszel_hub_and_agent.sh`): Verifies and starts both `beszel` (hub on port 8090) and `beszel-agent` (GPU/system metrics agent) containers with notifications.
    - `Launch dsh-mobile (DeepSeek Harness)` (`kc_launch_dsh_mobile.sh`): Boots DeepSeek Harness web UI with LAN mobile trust (`--trusted-host 192.168.1.11:3080 --trusted-host 192.168.1.11 --no-open`) in a dedicated Konsole session with port 3080 pre-check and notifications.
  - Registered remote commands into KDE Connect device configuration (`~/.config/kdeconnect/*/kdeconnect_runcommand/config`) and deployed to `~/KDE_CONNECT_CMDS/`.
  - Created `.desktop` application entries (`beszel-hub-and-agent.desktop`, `dsh-mobile.desktop`) in `desktop/`, `~/.local/share/applications/`, and `~/Desktop/`.
  - Added symlinks `beszel-hub-and-agent` and `dsh-mobile` into `bin/` and `~/.local/bin/`.

## [0.2.0] - 2026-09-14

- **Cross-Platform Traktor Pro Live Monitor & Audio Recorder Control (Option 48)**:
  - Downloaded and upgraded Traktor monitor suite from remote macOS host (`192.168.1.138`) into core repository (`scripts/traktor_monitor.py`, `scripts/traktor_monitor.sh`, `traktor_monitor.sh`, `bin/traktor-monitor`).
  - Upgraded engine with comprehensive **Microsoft Windows 10 & 11** native support:
    - Multi-process detection for `Traktor.exe`, `Traktor Pro 3.exe`, and `Traktor Pro 4.exe` via `tasklist` and PowerShell CIM/WMI queries.
    - Real-time CPU % load and RSS RAM memory footprint profiling.
    - Native Windows console non-blocking hotkey input using `msvcrt.kbhit()` and `msvcrt.getch()`.
    - Active deck audio tracking via Traktor session history XML (`history_*.nml`) parsing and file handle inspection.
    - Real-time recording file status and multi-directory file size growth detector (`RECORDING_DIRS`) to identify active mix recordings and standby files.
    - Upgraded recording monitor dashboard to display the last 3 recordings ordered by newest at the top, showing real-time file size, modification date, live write badge (`[● LIVE: Growing]`), and storage path.
    - Added automatic application launch check (`ensure_traktor_running`): if Traktor Pro is not running when the monitor is launched, it automatically opens Traktor, waits for process initialization, and immediately begins displaying the live monitor.
    - Traktor recording control via Windows `WScript.Shell` / `AppActivate("Traktor")` automation and keyboard shortcuts.
    - Native double-clickable Windows launchers: `traktor_monitor.bat` and `traktor_monitor.ps1`.
  - Maintained full macOS support (`traktor_monitor_macos.command`) with AppleScript System Events menu clicking and CoreAudio queries.
  - Native Linux support with `/proc/$pid/fd` inspection, PipeWire audio endpoint probe, and Wine/Proton path detection.
  - Interactive hotkeys: `[S]` Start Recording, `[X]` Stop Recording, `[T]` Toggle Recording, `[R]` Refresh, `[Q]` Quit.
  - Integrated into `Mix_Archive_Manager.sh` as dedicated **Option 48** in Section 5 (Live Monitors) launching in a new terminal window (`launch_in_terminal` in Konsole, Terminal.app, Windows Terminal `wt.exe`, Alacritty, Foot, XTerm).
  - Added companion launch option in Section 3 Digital Audio Workstations (`manage_daws`).
  - Added process tracking in `view_tasks` for `traktor_monitor` and `Traktor`.
  - Added minimal pending OS updates status indicator (`get_os_update_status`) to main menu system info banner (cross-platform: macOS SoftwareUpdate, Linux rpm-ostree/update-notifier, Windows reboot-required).
  - Expanded master operations to **72 operations** across 7 logical sections.

- **Advanced Audio File Specification & Stream Inspector (Option 28)**:
  - Added dedicated audio inspector engine (`scripts/inspect_playing_audio.py`, `scripts/inspect_playing_audio.sh`, `bin/view-mix-specs`, and `view_playing_specs.sh`).
  - Probes and displays deep technical stream specifications of currently playing audio (or selected archive mix):
    - Container & Encoded Format (e.g. `WAV`, `FLAC`, `MP3`, `AAC`, `OGG`)
    - Exact Bit Depth (`24-Bit`, `16-Bit`, `32-Bit Float/Int`)
    - Sampling Frequency / Rate (`48,000 Hz / 48.0 kHz`, `44.1 kHz`, `96.0 kHz`, etc.)
    - Full File Path & File Name
    - Duration (`HH:MM:SS.ms`) & Audio File Size (Bytes, MB, GB)
    - Track Title, Artist, Album, and Encoded Metadata
    - Audio Codec long name, Bitrate (`kbps`), Channels (`Stereo`), and Total PCM Samples
    - Storage Drive mount point, filesystem, and remaining free space
    - FLAC Compression Ratio (`% of raw PCM`) and exact MBs saved
    - Associated Companion Assets (Embedded Artwork, High-Res Cover Art, Tracklist with track count, Spectrogram, and Companion Video)
  - Interactive Terminal HUD with hotkeys: `[T]` View tracklist in console, `[C]` View cover art, `[S]` View spectrogram, `[Y]` Copy path to clipboard, `[D]` Open in DAW, `[F]` Open containing folder in file manager, `[R]` Refresh playback position, `[Q]` Return to menu.
  - Real-time stream summary displayed in the Live Status Box (`show_stats`), startup autoplay playback HUD (`execute_startup_autoplay`), now-playing asset HUD (`auto_show_playing_mix_assets`), and `cliamp` track control menu (`manage_cliamp`).

- **Active Audio Interface & Latency Display on Boot**:
  - Real-time detection of active default sound output device and hardware buffer latency (`scripts/get_audio_interface.py`).
  - High-performance probe (<120ms):
    - Linux PipeWire & WirePlumber via `wpctl inspect @DEFAULT_AUDIO_SINK@` and `pw-metadata -n settings` (calculates `(quantum / clock_rate) * 1000` ms)
    - ALSA hardware buffer parameters via `/proc/asound/card*/pcm*p/sub*/hw_params`
    - PulseAudio fallback via `pactl`
    - macOS CoreAudio via AppleScript / `system_profiler`
    - Windows WASAPI via PowerShell `Win32_SoundDevice`
    - FreeBSD OSS via `/dev/sndstat`
  - Integrated directly into the main application banner:
    `🎧 Audio Interface: Crusher ANC 2  │  ⚡ Latency: 21.3ms (1024 @ 48kHz)  │  🎛️ Engine: PipeWire`
  - Also displayed in the startup playback and now-playing HUDs.

- **Centered & Vertically Aligned Cover Art & Borderless Tracklist HUD**:
  - Positions the Cover Art Viewer and Tracklist Console side-by-side in the middle of the screen floating directly on top of the manager window (`keepAbove = true`).
  - Seamlessly integrates with KDE Plasma 6 KWin Scripting DBus API on Bazzite Linux Wayland (`scripts/align_mix_windows.py`), with automatic fallbacks for X11 (`wmctrl`/`xdotool`) and macOS AppleScript.
  - Zero window overlap: cover art positioned on the left and tracklist console on the right with matching vertical alignment and identical height.
  - Automatically loads and displays the playing mix's cover art in an image viewer window (`open_cover_art_window` via Gwenview / Loupe / Preview / Photos) **and** spawns the complete mix tracklist in a dedicated window on the operating system's default console.
  - **Entirely Borderless on Bazzite Linux**: Seamlessly leverages KDE Plasma 6 KWin rules (`noborder=true`, `noborderrule=2`, `above=true`, `aboverule=2`) and Konsole flags (`--hide-menubar`, `--hide-tabbar`, `-p TerminalMargin=0`) to present tracklists in a clean, frameless, titlebar-free floating HUD window.
  - Interactive console viewer (`scripts/view_tracklist_console.sh` & `bin/view-tracklist`):
    - Syntax-highlighted track entries with artist, title, remix/version tags, and metadata headers.
    - Hotkey controls: `[Q]` close/exit, `[C]` copy tracklist to system clipboard (`wl-copy` / `xclip` / `pbcopy`), `[S]` full interactive paging via `less`, and `[R]` reload tracklist from disk.
  - Cross-platform console integration: Konsole / foot / alacritty / xterm on Linux & FreeBSD, Terminal.app on macOS, and Windows Terminal (`wt.exe`) / CMD on Windows 10 & 11.
  - Automated KWin rule injection during `./install.sh` and runtime fallback via `ensure_bazzite_borderless_kwin_rule`.
  - Added archive-wide deep discovery (`find_mix_tracklist` & `find_mix_cover`) locating episode tracklists and cover artwork even within nested series subdirectories (e.g., `Stream of Frequency/070/`).
  - Enhanced startup autoplay sorting to select the true newest mix by modification time (`mtime`) rather than alphabetical sorting.

- **Live Meteorological Weather Display**:
  - Displays live weather conditions below the main startup banner (`scripts/get_weather.sh`).
  - Default configured location: **Swansea, UK**.
  - Meteorological cache with 20-minute TTL (`assets/weather_cache.txt`) ensures 0ms latency during banner startup renders.
  - Complete configuration menu (`manage_weather_menu`) to toggle weather display, change location, force live refresh, or clear location and disable completely.

- **Go Shopping for New Music (Option 20)**:
  - Added `scripts/shop_music.sh` to quickly launch browser tabs for **Beatport** (`https://www.beatport.com`), **Apple Music** (`https://music.apple.com`), and **Bandcamp** (`https://bandcamp.com`).
  - Native browser tab integration with Flatpak Google Chrome / Firefox, native browsers, or `xdg-open` / `open` / `cmd.exe`.

- **Dedicated Video Player Launcher & Video Dispatcher (Option 39)**:
  - Added `scripts/launch_specific_video.sh` to launch specific mix videos or streams in user's default video player (VLC default, mpv, Haruna, Kodi).
  - Allows selecting from archive videos, NFT video library, custom file paths, or streaming/YouTube URLs.

- **Startup Custom YouTube Video URL Autoplay**:
  - Automatically plays custom YouTube video URL on manager start **only if a mix is playing already** in a music player.
  - Configurable via `config.env` (`DEFAULT_VIDEO_PLAYER="vlc"`, `AUTO_PLAY_YOUTUBE_ON_STARTUP="false"`, `STARTUP_YOUTUBE_URL=""`) and interactive settings menu (Option 26 > 18 & 19).

- **Mix Publishing Schedule Calendar & Multi-Platform Syndication**:
  - Added `publish_calendar_scheduler.py` and `publish_calendar_scheduler.sh` (Option 19).
  - Enables release scheduling across all major music and podcast platforms: Apple Podcasts, Spotify for Podcasters, YouTube, SoundCloud, Mixcloud, DI.FM, Proton Radio, and Bandcamp.
  - Persistent schedule database in `assets/publishing_calendar.json`.
  - Automated Apple Podcasts compliant RSS feed generator (`podcast_feed.xml`).
  - Standard RFC 5545 iCalendar (`.ics`) file export with mix URLs, artwork, and descriptions for Google Calendar, Apple Calendar, and Outlook.

- **Expanded Acoustic Spectrogram Suite**:
  - Expanded acoustic analysis beyond Spek in `generate_spek.sh` (Option 22).
  - Added **Sonic Visualiser** launcher and profile inspection for deep frequency analysis.
  - Added **SoX** high-resolution 24-bit multi-colormap spectrogram rendering (`sox -n -p synth spectrogram`) with channel separation and custom frequency scales.
  - Added support for **Praat** phonetic and acoustic sonagram analysis, **Kwave**, and **Audacity** spectrogram mode.

- **Studio Diagnostics & Hardware/Software Inspector**:
  - Added `inspect_audio_studio.py` and `inspect_audio_studio.sh` (Option 33).
  - Complete audio architecture detection: PipeWire, PulseAudio, and ALSA cards, sinks, sources, active sample rates, and buffer latencies.
  - Hardware MIDI surface and controller detection (AKAI MPKmini2, Arturia MiniLab mkII, Valve controllers, synthesizers).
  - Software audit scanning installed DAWs and audio software (REAPER, Logic Pro, FL Studio, Traktor, Audacity, Ardour, Bitwig, Picard, Spek, cliamp, VLC, Haruna, Kodi).

- **Master Audio Control & Instant Mute Toggle**:
  - Added `toggle_audio_mute` and `get_audio_volume_and_mute` (Option 34).
  - Instant hardware-level mute toggle via PipeWire (`wpctl`), PulseAudio (`pactl`), ALSA (`amixer`), and AppleScript on macOS.
  - Real-time master volume and mute state displayed live in the manager loop prompt bar.

- **Procedural Netpbm P6 Binary .PPM Cover Art Generator**:
  - Added `generate_ppm_cover.py` and `generate_ppm_cover.sh` (Option 42).
  - Pure Python mathematical generator emitting 24-bit binary Netpbm P6 PPM files without heavy external dependencies.
  - Linear, radial, plasma wave, and angular gradient algorithms.
  - 8 curated color palettes: Cyberpunk Neon, Solarized Dusk, Deep Space Trance, Sunset Horizon, Emerald Matrix, Acid Gold, Retro Synthwave, and Mono Chrome.
  - High-res composited typography overlays (Artist, Title, Episode Number, Subtitle, Category Tags).
  - Automated conversion to anti-aliased high-res PNG (`1400x1400` / `3000x3000`).

- **Synchronized Mix-Video Companion Player Daemon**:
  - Added `sync_video_companion.py` and `sync_video_companion.sh` (Option 44).
  - Intelligent background daemon that detects the currently playing mix via `cliamp` or MPRIS.
  - Discovers matching companion video files (`.mp4`, `.mkv`, `.webm`) in the archive.
  - Launches companion video in VLC, Haruna, MPV, or Kodi, keeping video playback aligned with audio.
  - Automatically terminates video playback when audio playback stops.

- **Custom Mix Playlists Suite (.m3u8 / .xspf)**:
  - Added `manage_playlists.py` and `manage_playlists.sh` (Option 27).
  - Build, browse, search, and edit custom playlists from archive mixes.
  - Calculates total playlist duration, track count, and disk size.
  - Exports standard UTF-8 extended `.m3u8` and XML `.xspf` playlists.
  - Direct one-click launcher into `cliamp`, `strawberry`, `vlc`, or `mpv`.

- **Dynamic System MOTD Banner Manager**:
  - Added `manage_motd.py` and `update_system_motd.sh` (Option 63).
  - Scans the archive and generates a dynamic Message Of The Day summarizing the last 5 created mixes with dates, times, file sizes, formats, and sample rates.
  - Installs to `/etc/motd` or prints on demand in stylized ANSI format.

- **Exact Distro Name & Version Detection**:
  - Upgraded `get_os_badge()` in `Mix_Archive_Manager.sh` to read `/etc/os-release`.
  - Accurately displays full distribution name and version (e.g., `Bazzite Linux 44.20260902.0 (Kinoite)`) alongside kernel and architecture.

- **Live Manager & System Uptime Display**:
  - Added `get_manager_uptime()` tracking manager session uptime and host system uptime.
  - Displayed live at the bottom of the menu loop right above the user choice prompt.

- **Startup Autoplay & Now Playing Asset Discovery**:
  - Added `detect_currently_playing_mix()`, `auto_show_playing_mix_assets()`, and `check_and_show_currently_playing_mix()`.
  - If a mix is already playing when the manager launches or begins playing in an audio player, matching cover artwork and tracklists are automatically opened and displayed.

### 🛠️ Improvements & Enhancements

- **Menu Expansion**: Expanded master operations menu to **71 operations** across 7 logical sections with 1:1 case-dispatch synchronization.
- **Cross-Platform Launchers**: Updated all native platform launchers (`manager.sh`, `manager_macos.command`, `manager.bat`, `manager.ps1`, `manager_freebsd.sh`, `desktop/Mix_Archive_Manager.desktop`) to version `v0.2`.
- **Dual-Mirror Sync**: Maintained full synchronization between working directory and `/var/home/mplanetarian/Documents/BASH_SCRIPTS/`.

---

## [0.1.0] - 2026-09-13

### 🎧 Initial Release

- **Master Interactive Console**: 62 core operations organized in 7 logical sections.
- **Audio Ingestion & 32-bit FLAC Conversion**: Multi-part WAV concatenation, 32-bit sample depth FLAC encoding (`Make_SOF_FLAC_CONVERSION.sh`), embedded artwork, and 1080p spectrogram generation.
- **Audio Format & Bit Depth Conversion**: Universal conversion between WAV, MP3, Ogg Vorbis, Opus, AAC, ALAC, and bit depths (32-bit float, 32-bit int, 24-bit, 16-bit).
- **Duplicate Audio Finder**: Content-based hashing and episode duplicate detection (`find_duplicate_mixes.py`).
- **Traktor Pro History Integration**: Automatic parsing of Traktor history XML (`collection.nml`) to generate timestamped cue tracklists.
- **HTML & PDF Tracklist Documentation**: Master HTML index and printable vector PDF tracklists (`generate_tracklist_docs.py`).
- **Promotional Outreach Suite**: HTML and Plaintext email pitcher for promoters, publishers, radio stations, and record labels (`send_promo_email.py`).
- **DAW Integration**: One-click dispatch to REAPER, Logic Pro, FL Studio, Traktor Pro, GarageBand, Audacity, Ardour, and Bitwig.
- **cliamp Terminal Music Player**: Integrated retro music player with MPRIS monitoring, clipboard copy, and real-time path resolution.
- **YouTube Video Synthesis**: Hardware-accelerated 4K UHD, 1080p Full HD, and 720p HD video generation with audio fading.
- **Cloud & Network Sync**: Google Drive rclone synchronization and SMB network share importer (`search_and_import_mixes.sh`).
- **Installation & Config Migration**: Full migration wizard, snapshot backups, portable `.tar.gz` bundle export and import with SHA-256 validation.
- **Terminal Theme Engine**: 9 color themes (Cyberpunk, Dracula, Nord, Matrix, Solarized, Tokyo Night, Monokai, Gruvbox, Emerald, Classic).
- **Cross-Platform Compatibility**: Full validation on Linux (Bazzite / SteamOS / Fedora / Ubuntu), macOS, Windows 10/11, and FreeBSD.
