#!/usr/bin/env python3
"""MP Liquid Mixer - the Settings window (GTK 3 dialog).

Kept apart from the mixer window so the drawing code never has to know about
widgets: the mixer hands over one ``Settings`` snapshot, this module returns the
edited snapshot, and the mixer applies it. Nothing here imports the mixer, and
GTK is only imported inside :func:`show_settings_dialog`, so ``mixer_settings``
and its tests stay headless.

Version 0.0.1
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import mixer_settings


def _label(text: str, *, bold: bool = False, dim: bool = False) -> Any:
    from gi.repository import GLib, Gtk
    widget = Gtk.Label(label=text)
    widget.set_xalign(0.0)
    widget.set_line_wrap(True)
    widget.set_max_width_chars(52)
    if bold:
        widget.set_markup(f"<b>{GLib.markup_escape_text(str(text))}</b>")
    if dim:
        widget.get_style_context().add_class("dim-label")
    return widget


def _combo(options: List[Tuple[str, str]], current: str) -> Tuple[Any, Callable[[], str]]:
    """A combo of ``(value, label)`` rows; returns the widget and a getter."""
    from gi.repository import Gtk
    combo = Gtk.ComboBoxText()
    for value, label in options:
        combo.append(value or "__empty__", label)
    wanted = current or "__empty__"
    if not any(value == wanted for value, _label_text in options):
        wanted = options[0][0] if options else "__empty__"
    combo.set_active_id(wanted)

    def value() -> str:
        selected = combo.get_active_id() or ""
        return "" if selected == "__empty__" else selected

    return combo, value


def _check(text: str, active: bool, hint: str = "") -> Any:
    from gi.repository import Gtk
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    widget = Gtk.CheckButton(label=text)
    widget.set_active(bool(active))
    box.pack_start(widget, False, False, 0)
    if hint:
        note = _label(hint, dim=True)
        note.set_margin_start(26)
        box.pack_start(note, False, False, 0)
    box.check = widget  # type: ignore[attr-defined]
    return box


def _section(grid: Any, row: int, title: str) -> int:
    from gi.repository import GLib, Gtk
    header = Gtk.Label()
    header.set_markup(f"<b>{GLib.markup_escape_text(str(title))}</b>")
    header.set_xalign(0.0)
    header.set_margin_top(10 if row else 2)
    grid.attach(header, 0, row, 2, 1)
    line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    grid.attach(line, 0, row + 1, 2, 1)
    return row + 2


def _css_rgb(color: Tuple[int, int, int], alpha: float = 1.0) -> str:
    return "rgba(%d,%d,%d,%.3f)" % (int(color[0]), int(color[1]), int(color[2]), float(alpha))


def _install_theme_css(dialog: Any, theme: str, colors: Any, field: Any, ink: Any) -> None:
    """Paint the dialog in the mixer's active theme.

    The Settings window is part of the mixer, so it uses the same palette the
    main window paints with: the theme's field as the surface, its accent for
    the section rules and the default button, and its ink for text. A theme
    without a palette (a link to the filler table) still gets a readable
    dialog rather than a crash.
    """
    from gi.repository import Gdk, Gtk
    try:
        accent, secondary, tertiary = colors(theme)
    except Exception:
        accent, secondary, tertiary = (166, 77, 255), (247, 37, 133), (0, 229, 255)
    try:
        surface = field(theme)
    except Exception:
        surface = (196, 204, 221)
    try:
        text = ink(theme)
    except Exception:
        text = (24, 28, 32)
    luma = (0.2126 * surface[0] + 0.7152 * surface[1] + 0.0722 * surface[2]) / 255.0
    dark = luma < 0.55
    panel = tuple(int(round(c * (0.62 if dark else 1.05))) for c in surface)
    panel = tuple(max(0, min(255, c)) for c in panel)
    text = text if not dark else tuple(max(c, 210) for c in text)
    css = f"""
    dialog, .background {{ background-color: {_css_rgb(surface)}; color: {_css_rgb(text)}; }}
    label {{ color: {_css_rgb(text)}; }}
    .dim-label {{ color: {_css_rgb(text, 0.62)}; }}
    headerbar {{ background-color: {_css_rgb(panel)}; color: {_css_rgb(text)}; }}
    grid {{ background-color: {_css_rgb(surface)}; }}
    separator {{ background-color: {_css_rgb(accent, 0.55)}; }}
    button {{
        background-image: none;
        background-color: {_css_rgb(panel)};
        color: {_css_rgb(text)};
        border: 1px solid {_css_rgb(accent, 0.65)};
        border-radius: 7px;
    }}
    button:hover {{ background-color: {_css_rgb(accent, 0.22)}; }}
    button.suggested-action {{
        background-color: {_css_rgb(accent, 0.30)};
        border: 1px solid {_css_rgb(accent, 0.95)};
        color: {_css_rgb(text)};
    }}
    entry, combobox button, spinbutton {{
        background-color: {_css_rgb(panel)};
        color: {_css_rgb(text)};
        border: 1px solid {_css_rgb(accent, 0.45)};
        border-radius: 6px;
    }}
    combobox arrow {{ color: {_css_rgb(accent)}; }}
    scrolledwindow, viewport {{ background-color: {_css_rgb(surface)}; }}
    checkbutton {{ color: {_css_rgb(text)}; }}
    checkbutton check {{ border: 1px solid {_css_rgb(accent, 0.75)}; }}
    checkbutton check:checked {{
        background-color: {_css_rgb(accent, 0.85)};
        color: {_css_rgb(surface)};
    }}
    """
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(css.encode("utf-8"))
    except Exception:
        return
    screen = None
    try:
        screen = dialog.get_screen()
    except Exception:
        try:
            screen = Gdk.Screen.get_default()
        except Exception:
            screen = None
    if screen is not None:
        try:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass


def show_settings_dialog(parent: Any, settings: "mixer_settings.Settings",
                         sinks: Optional[List[Dict[str, Any]]] = None,
                         themes: Optional[List[Tuple[str, str]]] = None,
                         theme: str = "",
                         on_cycle_theme: Optional[Callable[[], str]] = None,
                         theme_colors: Optional[Callable[[str], Any]] = None,
                         theme_label: Optional[Callable[[str], str]] = None,
                         theme_field: Optional[Callable[[str], Any]] = None,
                         theme_ink: Optional[Callable[[str], Any]] = None) -> Optional["mixer_settings.Settings"]:
    """Run the modal Settings window; returns the new Settings or None.

    ``sinks`` and ``themes`` are passed in by the mixer so the dialog does not
    re-probe the sound server or the palette when its caller already did. A
    missing list is fetched here, which keeps the dialog usable on its own.

    ``theme`` is the mixer's live theme and ``on_cycle_theme`` is the mixer's
    own C handler. The dialog installs themed CSS for that theme and calls the
    callback on C, so the Settings window follows the same cycle the main
    window uses instead of keeping a stale palette while the mixer repaints.
    """
    from gi.repository import Gdk, Gtk

    sinks = list(sinks if sinks is not None else mixer_settings.list_audio_sinks(force=True))
    themes = list(themes if themes is not None else mixer_settings.theme_options())
    default_sink = mixer_settings.default_sink_name()

    dialog = Gtk.Dialog(title="MP Liquid Mixer Settings", transient_for=parent, modal=True)
    dialog.set_default_size(760, 760)
    dialog.set_resizable(True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    save_button = dialog.add_button("Save Settings", Gtk.ResponseType.OK)
    try:
        save_button.get_style_context().add_class("suggested-action")
    except Exception:
        pass
    dialog.set_default_response(Gtk.ResponseType.OK)

    # A light scroller keeps every section reachable on a small screen.
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_border_width(16)
    grid = Gtk.Grid()
    grid.set_row_spacing(8)
    grid.set_column_spacing(18)
    grid.set_column_homogeneous(False)
    grid.set_hexpand(True)
    scroller.add(grid)
    dialog.get_content_area().pack_start(scroller, True, True, 0)

    sink_options: List[Tuple[str, str]] = [("", f"System default ({default_sink or 'auto'})")]
    for sink in sinks:
        label = mixer_settings.sink_label(sink)
        if str(sink.get("name")) == default_sink:
            label += "  — system default"
        sink_options.append((str(sink.get("name")), label))
    if settings.master_output_device and not any(
            value == settings.master_output_device for value, _text in sink_options):
        sink_options.append((settings.master_output_device,
                             f"{settings.master_output_device}  (not currently connected)"))

    theme_options: List[Tuple[str, str]] = [("", "Use the current shared theme")]
    theme_options.extend(themes)

    eq_options = [(name, name) for name in mixer_settings.EQ_PRESETS]
    format_options = [("WAV", "WAV — PCM 16-bit (default)"), ("FLAC", "FLAC — lossless compressed")]
    limit_options = [("4", "4 GB per part — the WAV maximum (default)"),
                     ("2", "2 GB per part"),
                     ("1", "1 GB per part")]

    row = 0
    # ---------------- Recording ----------------
    row = _section(grid, row, "Recording")

    folder_label = _label("Output folder")
    folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    current_dir = settings.recording_dir or str(
        mixer_settings.default_recording_dir())  # the archive default
    folder_button = Gtk.FileChooserButton(title="Recording output folder",
                                          action=Gtk.FileChooserAction.SELECT_FOLDER)
    folder_button.set_filename(current_dir)
    folder_button.set_hexpand(True)
    default_button = Gtk.Button(label="Use default")

    def _use_default(_button=None) -> None:
        folder_button.set_filename(str(mixer_settings.default_recording_dir()))

    default_button.connect("clicked", _use_default)
    folder_row.pack_start(folder_button, True, True, 0)
    folder_row.pack_start(default_button, False, False, 0)
    grid.attach(folder_label, 0, row, 1, 1)
    grid.attach(folder_row, 1, row, 1, 1)
    row += 1
    grid.attach(_label("Where finished recordings land. The default follows the Mix Archive.", dim=True), 1, row, 1, 1)
    row += 1

    format_combo, format_value = _combo(format_options, settings.recording_format)
    grid.attach(_label("Audio format"), 0, row, 1, 1)
    grid.attach(format_combo, 1, row, 1, 1)
    row += 1

    limit_combo, limit_value = _combo(limit_options, str(settings.recording_part_limit_gib))
    grid.attach(_label("Part size"), 0, row, 1, 1)
    grid.attach(limit_combo, 1, row, 1, 1)
    row += 1
    grid.attach(_label("A long set is split into parts at this size. "
                       "The file continues as _PART_2, _PART_3 and so on.", dim=True), 1, row, 1, 1)
    row += 1

    capture_options: List[Tuple[str, str]] = [("", "Master output (the room mix)")]
    for sink in sinks:
        capture_options.append((str(sink.get("name")),
                                f"{mixer_settings.sink_label(sink)} monitor"))
    if settings.recording_device and not any(
            value == settings.recording_device for value, _text in capture_options):
        capture_options.append((settings.recording_device,
                                f"{settings.recording_device}  (not currently connected)"))
    capture_combo, capture_value = _combo(capture_options, settings.recording_device)
    grid.attach(_label("Record from"), 0, row, 1, 1)
    grid.attach(capture_combo, 1, row, 1, 1)
    row += 1
    record_start = _check("Start recording when the mixer opens",
                          settings.record_on_start)
    grid.attach(record_start, 1, row, 1, 1)
    row += 1

    # ---------------- Audio output ----------------
    row = _section(grid, row, "Audio Output")
    master_combo, master_value = _combo(sink_options, settings.master_output_device)
    master_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    master_row.pack_start(master_combo, True, True, 0)
    refresh_button = Gtk.Button(label="Refresh")
    master_row.pack_start(refresh_button, False, False, 0)
    grid.attach(_label("Master output"), 0, row, 1, 1)
    grid.attach(master_row, 1, row, 1, 1)
    row += 1
    grid.attach(_label("Both decks play through this device. Leave it on the system "
                       "default to follow the desktop's sound settings.", dim=True), 1, row, 1, 1)
    row += 1

    grid.attach(_label("A Bluetooth headset that the desktop has connected appears "
                       "here and both decks play through it.", dim=True), 1, row, 1, 1)
    row += 1

    # A second interface for cueing: any channel's headphone button sends that
    # deck here while every other deck stays on the master output.
    monitor_options: List[Tuple[str, str]] = [("", "Same as master (no split)")]
    for sink in sinks:
        monitor_options.append((str(sink.get("name")), mixer_settings.sink_label(sink)))
    if settings.monitor_output_device and not any(
            value == settings.monitor_output_device for value, _text in monitor_options):
        monitor_options.append((settings.monitor_output_device,
                                f"{settings.monitor_output_device}  (not currently connected)"))
    monitor_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    monitor_combo, monitor_value = _combo(monitor_options, settings.monitor_output_device)
    monitor_row.pack_start(monitor_combo, True, True, 0)
    grid.attach(_label("Monitor output"), 0, row, 1, 1)
    grid.attach(monitor_row, 1, row, 1, 1)
    row += 1
    grid.attach(_label("The headphone-cue interface. Each channel's DJ headphones "
                       "button routes that deck here, leaving the rest on the master "
                       "output. Leave it on Same as master when there is one interface.",
                       dim=True), 1, row, 1, 1)
    row += 1

    def _refresh_devices(_button=None) -> None:
        fresh = mixer_settings.list_audio_sinks(force=True)
        labels = [(str(item.get("name")), mixer_settings.sink_label(item)) for item in fresh]
        for combo in (master_combo, monitor_combo):
            chosen = combo.get_active_id() or ""
            combo.remove_all()
            combo.append("__empty__", "System default" if combo is master_combo
                         else "Same as master (no split)")
            for value, text in labels:
                combo.append(value, text)
            if chosen and chosen != "__empty__" and not any(value == chosen for value, _t in labels):
                combo.append(chosen, f"{chosen}  (not currently connected)")
            combo.set_active_id(chosen if chosen in [value for value, _t in labels]
                                or chosen == "__empty__" else "__empty__")

    refresh_button.connect("clicked", _refresh_devices)

    # ---------------- Appearance ----------------
    row = _section(grid, row, "Appearance")
    theme_combo, theme_value = _combo(theme_options, settings.theme)
    grid.attach(_label("Default theme"), 0, row, 1, 1)
    grid.attach(theme_combo, 1, row, 1, 1)
    row += 1
    grid.attach(_label("The startup theme. Theme changes made with C persist too; "
                       "choose a theme here to make it the starting point.", dim=True), 1, row, 1, 1)
    row += 1

    # The checkbox is worded the way the requirement was: checking it disables
    # the visualizer, so a glance at a ticked box reads "it is off".
    visualizer_check = _check("Disable the visualizer window behind the mixer",
                              not settings.visualizer_enabled,
                              "Checked means the visualizer is never started at launch. "
                              "V still opens one on demand.")
    grid.attach(visualizer_check, 1, row, 1, 1)
    row += 1

    fullscreen_check = _check("Start full screen", settings.fullscreen,
                              "Off starts in the centred 1920x1080 window.")
    grid.attach(fullscreen_check, 1, row, 1, 1)
    row += 1

    # ---------------- Startup and exit ----------------
    row = _section(grid, row, "Startup & Exit")
    startup_check = _check("Play the startup video (MP_loader.mp4)",
                          settings.startup_video,
                          "Uncheck to open the mixer immediately with no intro movie.")
    grid.attach(startup_check, 1, row, 1, 1)
    row += 1
    exit_check = _check("Play the exit video (Exit_MP_Video.mp4) when the mixer closes",
                        settings.exit_video)
    grid.attach(exit_check, 1, row, 1, 1)
    row += 1
    autoplay_check = _check("Load the first track of each deck ready to play",
                            settings.decks_autoplay,
                            "Off keeps both decks paused so nothing starts by itself.")
    grid.attach(autoplay_check, 1, row, 1, 1)
    row += 1

    # ---------------- Deck audio ----------------
    row = _section(grid, row, "Deck Audio")
    eq_combo, eq_value = _combo(eq_options, settings.eq_preset)
    grid.attach(_label("Default equaliser"), 0, row, 1, 1)
    grid.attach(eq_combo, 1, row, 1, 1)
    row += 1
    grid.attach(_label("Applied to both decks when the mixer opens. Moving a mixer "
                       "EQ knob afterwards overrides it for that deck.", dim=True), 1, row, 1, 1)
    row += 1

    # The live theme line, so C visibly changes something in this window.
    live_theme = _label("", bold=True)
    grid.attach(live_theme, 1, row, 1, 1)

    def _paint_theme(name: str) -> None:
        label = theme_label(name) if theme_label is not None else name
        try:
            live_theme.set_markup(
                f"<b>{label}</b>   —   press C to change the theme")
        except Exception:
            live_theme.set_text(f"{label}   -   press C to change the theme")
        if theme_colors and theme_field and theme_ink:
            _install_theme_css(dialog, name, theme_colors, theme_field, theme_ink)

    current_theme = theme or ""
    if current_theme:
        _paint_theme(current_theme)

    def _on_key(_widget=None, event=None) -> bool:
        # C cycles the mixer's theme through the same handler the main window
        # uses, then repaints this dialog in the new palette. The modal window
        # swallows keys, so without this the mixer's own handler never runs
        # while Settings is open.
        from gi.repository import Gdk as _Gdk
        # Called as a GTK signal handler it gets (widget, event); called
        # directly it may be handed the event alone.
        if event is None:
            event = _widget
        try:
            keyval = event.keyval
        except AttributeError:
            return False
        if keyval in (_Gdk.KEY_c, _Gdk.KEY_C, ord("c")):
            if on_cycle_theme is None:
                return False
            try:
                name = on_cycle_theme()
            except Exception:
                return False
            if name:
                _paint_theme(name)
                theme_combo.set_active_id(name)
            return True
        return False

    # The handler is kept on the dialog (unbound, so a caller can pass any
    # widget) so a test can invoke the same C handling without synthesising a
    # GdkEvent GTK would reject.
    dialog._settings_theme_key_handler = _on_key
    dialog.connect("key-press-event", _on_key)

    grid.show_all()
    dialog.show_all()
    response = dialog.run()
    result: Optional["mixer_settings.Settings"] = None
    if response == Gtk.ResponseType.OK:
        result = settings.copy()
        folder = str(folder_button.get_filename() or "").strip()
        # Choosing the default folder must not pin an absolute path that would
        # stop following a moved Mix Archive, so it is stored as "use default".
        if folder and Path(folder) == mixer_settings.default_recording_dir():
            folder = ""
        result.recording_dir = folder
        result.recording_format = format_value()
        result.recording_part_limit_gib = int(limit_value() or 4)
        result.recording_device = capture_value()
        result.record_on_start = bool(record_start.check.get_active())
        result.master_output_device = master_value()
        result.monitor_output_device = monitor_value()
        result.theme = theme_value()
        result.visualizer_enabled = not bool(visualizer_check.check.get_active())
        result.fullscreen = bool(fullscreen_check.check.get_active())
        result.startup_video = bool(startup_check.check.get_active())
        result.exit_video = bool(exit_check.check.get_active())
        result.decks_autoplay = bool(autoplay_check.check.get_active())
        result.eq_preset = eq_value()
        # Run the same validation a load would, so a hand-edited JSON and the
        # dialog cannot disagree about what a legal snapshot is.
        result = mixer_settings.Settings(**result.to_dict())
    dialog.destroy()
    return result
