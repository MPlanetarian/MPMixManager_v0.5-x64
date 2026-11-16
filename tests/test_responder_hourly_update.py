"""Hourly unique-cloner + weather update in the MP Audio Responder.

The responder used to speak the combined 0.4 + 0.5 clone totals once a day at
21:00. It now speaks one short update every hour for the 0.5.x repository:

  * unique cloners only, never raw clone counts;
  * the total since the repository opened, which GitHub cannot answer on its
    own because it keeps just 14 days of traffic data, so the total is carried
    in a ledger;
  * how many cloners are new since the previous announcement;
  * the next 24 hours for Swansea City, United Kingdom;
  * spoken only: no audio or text is archived, and no e-mail is sent.

These tests pin the parts that break silently:

  * the since-start total never shrinks when a repeat cloner drops out of the
    rolling window, and aged-out days are frozen rather than lost;
  * the new-cloner delta is measured against the last *announced* total, and
    the mark only advances once the audio has played;
  * wording is correct for the first report, zero-new and some-new cases;
  * the weather summary turns WMO codes into speech and never says "22:00";
  * the two paragraphs are joined into one announcement, and a missing half is
    announced rather than silently dropped;
  * the monitor runs on an hourly cadence, not a daily 21:00 slot;
  * the job is ephemeral: its audio and text are deleted after playback.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
RESPONDER_CANDIDATES = (
    Path.home() / "MP_AI_Agent_Audio_Responder-0.1-x64" / "MP_OpenCode_Audio_Responder.sh",
    Path.home() / "Documents" / "BASH_SCRIPTS" / "MP_OpenCode_Audio_Responder.sh",
)
HELPER_CANDIDATES = (
    Path.home() / "MP_AI_Agent_Audio_Responder-0.1-x64" / "scripts",
    Path.home() / "Documents" / "BASH_SCRIPTS" / "scripts",
)


def _first(candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


RESPONDER = _first(RESPONDER_CANDIDATES)
GITCLONE = _first(tuple(d / "gitclone_stats.py" for d in HELPER_CANDIDATES))
WEATHER = _first(tuple(d / "weather_forecast.py" for d in HELPER_CANDIDATES))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)      # type: ignore[union-attr]
    return module


def load_engine():
    """Load the responder's embedded Python engine with an isolated state dir."""
    text = RESPONDER.read_text(encoding="utf-8")
    start = text.index("exec python3 -u - <<'PYEOF'")
    start = text.index("\n", start) + 1
    end = text.index("\nPYEOF", start)
    tmp = Path(tempfile.mkdtemp(prefix="hourly-engine-"))
    env = {
        "STATE_DIR": str(tmp),
        "LOG_FILE": str(tmp / "r.log"),
        "LOCK_FILE": str(tmp / "r.lock"),
        "PAUSE_FILE": str(tmp / "paused"),
        "STATE_FILE": str(tmp / "state.json"),
        "MP_OPENCODE_SCRIPT_DIR": str(RESPONDER.parent),
        "MP_OPENCODE_HELPER_DIR": str(RESPONDER.parent / "scripts"),
        "MP_OPENCODE_ERROR_LOG": str(tmp / "Error_log.log"),
        "MP_OPENCODE_GITCLONE_LEDGER": str(tmp / "gitclone-ledger.json"),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        ns = {"__name__": "responder_engine"}
        exec(compile(text[start:end], "<responder>", "exec"), ns)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return ns


TRAFFIC = {
    "count": 28,
    "uniques": 20,
    "clones": [
        {"timestamp": "2026-11-01T00:00:00Z", "count": 0, "uniques": 0},
        {"timestamp": "2026-11-12T00:00:00Z", "count": 0, "uniques": 0},
        {"timestamp": "2026-11-13T00:00:00Z", "count": 18, "uniques": 13},
        {"timestamp": "2026-11-14T00:00:00Z", "count": 10, "uniques": 9},
    ],
}


@unittest.skipUnless(GITCLONE.is_file(), "gitclone_stats.py is not installed")
class CloneLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module(GITCLONE, "gitclone_stats_under_test")

    def test_first_report_counts_everyone_in_the_window(self):
        data = self.mod.update_ledger({}, TRAFFIC,
                                      "2026-11-12")
        self.assertEqual(data["total_uniques_since_start"], 20)
        self.assertIsNone(data["new_uniques"])

    def test_since_start_total_never_shrinks_when_a_day_ages_out(self):
        ledger = {}
        first = self.mod.update_ledger(ledger, TRAFFIC, "2026-11-12")
        ledger["announced"] = {"total": first["total_uniques_since_start"],
                               "at": "2026-11-15T23:00:00"}
        # Three days later two cloners have dropped out of GitHub's window.
        later = {"count": 0, "uniques": 0, "clones": [
            {"timestamp": "2026-11-15T00:00:00Z", "count": 0, "uniques": 0},
            {"timestamp": "2026-11-16T00:00:00Z", "count": 0, "uniques": 0},
        ]}
        data = self.mod.update_ledger(ledger, later, "2026-11-12")
        self.assertEqual(data["total_uniques_since_start"], 20,
                         "a rolling window must not shrink the audience")
        self.assertEqual(data["new_uniques"], 0)
        self.assertEqual(data["frozen_uniques"], 20,
                         "the whole departed window is frozen, not the per-day "
                         "sum, which would recount its repeat cloners")

    def test_frozen_days_plus_the_live_window_is_the_since_start_total(self):
        ledger = {}
        self.mod.update_ledger(ledger, TRAFFIC, "2026-11-12")
        # The 13th and 14th have aged out; the window now covers two new days
        # with 5 unique cloners between them. Only the departed days' share is
        # frozen, so a repeat cloner is not counted twice at the boundary.
        later = {"count": 6, "uniques": 5, "clones": [
            {"timestamp": "2026-11-15T00:00:00Z", "count": 4, "uniques": 4},
            {"timestamp": "2026-11-16T00:00:00Z", "count": 2, "uniques": 1},
        ]}
        data = self.mod.update_ledger(ledger, later, "2026-11-12")
        self.assertEqual(data["frozen_uniques"], 20)
        self.assertEqual(data["total_uniques_since_start"], 25)

    def test_new_cloners_are_measured_against_the_last_announced_total(self):
        ledger = {}
        first = self.mod.update_ledger(ledger, TRAFFIC, "2026-11-12")
        ledger["announced"] = {"total": first["total_uniques_since_start"] - 3,
                               "at": "2026-11-15T22:00:00"}
        data = self.mod.update_ledger(ledger, TRAFFIC, "2026-11-12")
        self.assertEqual(data["new_uniques"], 3)

    def test_zero_new_cloners_is_explicit_not_first_report(self):
        ledger = {}
        first = self.mod.update_ledger(ledger, TRAFFIC, "2026-11-12")
        ledger["announced"] = {"total": first["total_uniques_since_start"],
                               "at": "2026-11-15T22:00:00"}
        data = self.mod.update_ledger(ledger, TRAFFIC, "2026-11-12")
        self.assertEqual(data["new_uniques"], 0)
        spoken = self.mod.build_spoken(data)
        self.assertIn("No new unique cloners", spoken)

    def test_spoken_text_names_the_product_and_the_total_only(self):
        data = self.mod.update_ledger({}, TRAFFIC,
                                      "2026-11-12")
        data["product"] = "MP Mix Manager 0.5.3"
        spoken = self.mod.build_spoken(data)
        self.assertIn("MP Mix Manager 0.5.3", spoken)
        self.assertIn("20 unique cloners", spoken)
        self.assertNotIn("28 clones", spoken)
        self.assertNotIn("combined", spoken)

    def test_spoken_text_never_double_stops(self):
        data = self.mod.update_ledger({}, TRAFFIC,
                                      "2026-11-12")
        data["product"] = "MP Mix Manager 0.5.3"
        data["announced"] = {"total": 19, "at": "2026-11-15T23:00:00"}
        spoken = self.mod.build_spoken(data)
        self.assertNotIn("..", spoken)

    def test_console_report_speaks_unique_cloners_only(self):
        data = self.mod.update_ledger({}, TRAFFIC,
                                      "2026-11-12")
        data["product"] = "MP Mix Manager 0.5.3"
        text = self.mod.build_text(data)
        self.assertIn("Total unique cloners since the start : 20", text)
        self.assertIn("Unique cloners only", text)

    def test_mark_advances_the_reported_total(self):
        path = Path(tempfile.mkdtemp(prefix="ledger-")) / "ledger.json"
        try:
            self.mod.save_ledger(path, {"since_start": 41})
            ledger = self.mod.load_ledger(path)
            ledger["announced"] = {"total": 40, "at": "2026-11-15T22:00:00"}
            self.mod.save_ledger(path, ledger)
            result = subprocess.run(
                [sys.executable, str(GITCLONE), "--state", str(path), "--mark"],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            marked = json.loads(path.read_text())
            self.assertEqual(marked["announced"]["total"], 41)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_ledger_survives_a_corrupt_file(self):
        path = Path(tempfile.mkdtemp(prefix="ledger-")) / "ledger.json"
        path.write_text("{not json", encoding="utf-8")
        try:
            ledger = self.mod.load_ledger(path)
            self.assertEqual(ledger.get("window") or {}, {})
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)


@unittest.skipUnless(WEATHER.is_file(), "weather_forecast.py is not installed")
class WeatherSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module(WEATHER, "weather_forecast_under_test")

    def payload(self, **over):
        hourly = {
            "time": ["2026-11-15T23:00", "2026-11-16T00:00", "2026-11-16T01:00"],
            "temperature_2m": [8.2, 5.5, 9.1],
            "weather_code": [3, 61, 1],
            "precipitation_probability": [10, 85, 20],
            "wind_speed_10m": [13.0, 30.0, 9.0],
        }
        hourly.update(over.pop("hourly", {}))
        data = {
            "timezone": "Europe/London",
            "current": {
                "time": "2026-11-15T23:00",
                "temperature_2m": 8.2,
                "apparent_temperature": 5.2,
                "weather_code": 3,
                "wind_speed_10m": 13.0,
                "precipitation": 0.0,
            },
            "hourly": hourly,
        }
        data.update(over)
        return data

    def test_summary_reports_the_high_and_low(self):
        summary = self.mod.summarise(self.payload(), "Swansea")
        self.assertEqual(summary["next_24h"]["high_c"], 9.1)
        self.assertEqual(summary["next_24h"]["low_c"], 5.5)
        self.assertEqual(summary["next_24h"]["hours"], 3)

    def test_wmo_codes_become_words_not_numbers(self):
        summary = self.mod.summarise(self.payload(), "Swansea")
        self.assertEqual(summary["current"]["condition"], "overcast")
        self.assertNotIn("3", summary["current"]["condition"])

    def test_spoken_forecast_avoids_a_24_hour_clock(self):
        spoken = self.mod.build_spoken(self.mod.summarise(self.payload(), "Swansea"))
        self.assertNotIn("23:00", spoken)
        self.assertIn("11 p.m.", spoken)
        self.assertIn("Swansea", spoken)

    def test_spoken_forecast_mentions_the_rain_peak(self):
        spoken = self.mod.build_spoken(self.mod.summarise(self.payload(), "Swansea"))
        self.assertIn("85 percent", spoken)
        self.assertIn("midnight", spoken.lower()) if "00:00" in spoken else None

    def test_spoken_forecast_covers_the_next_24_hours(self):
        spoken = self.mod.build_spoken(self.mod.summarise(self.payload(), "Swansea"))
        self.assertIn("next 24 hours", spoken)

    def test_missing_hourly_data_does_not_raise(self):
        summary = self.mod.summarise({"current": {}, "hourly": {}}, "Swansea")
        spoken = self.mod.build_spoken(summary)
        self.assertIn("Swansea", spoken)
        self.assertIn("unavailable", spoken.lower())

    def test_console_report_names_the_place(self):
        text = self.mod.build_text(self.mod.summarise(self.payload(), "Swansea"))
        self.assertIn("Swansea City, United Kingdom", text)
        self.assertIn("Next 24h high", text)


@unittest.skipUnless(RESPONDER.is_file(), "audio responder is not installed")
class HourlyUpdateEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_engine()

    def test_interval_is_one_hour(self):
        self.assertEqual(self.ns["GITCLONE_INTERVAL"], 3600)

    def test_the_update_is_a_scheduled_notice(self):
        self.assertIn("gitclone", self.ns["NOTICE_TAGS"])
        self.assertIn("gitclone", self.ns["SCHEDULED_NOTICE_TAGS"])

    def test_daily_slot_machinery_is_gone(self):
        source = RESPONDER.read_text(encoding="utf-8")
        self.assertNotIn("GITCLONE_TIME", source)
        self.assertNotIn("parse_daily_time", source)
        self.assertNotIn("GITCLONE_CATCHUP_HOURS", source)

    def test_defaults_are_the_05_repository(self):
        self.assertEqual(self.ns["GITCLONE_SCRIPT"].name, "gitclone_stats.py")
        self.assertEqual(self.ns["WEATHER_SCRIPT"].name, "weather_forecast.py")
        self.assertEqual(self.ns["WEATHER_NAME"], "Swansea")

    def test_two_paragraphs_are_joined_into_one_announcement(self):
        clones = {"spoken": "Clone paragraph."}
        weather = {"spoken": "Weather paragraph."}
        text = self.ns["build_hourly_update"](clones, weather)
        self.assertEqual(text, "Clone paragraph. Weather paragraph.")

    def test_clone_paragraph_comes_first(self):
        text = self.ns["build_hourly_update"](
            {"spoken": "CLONES"}, {"spoken": "WEATHER"})
        self.assertLess(text.index("CLONES"), text.index("WEATHER"))

    def test_missing_weather_is_announced_not_dropped(self):
        text = self.ns["build_hourly_update"]({"spoken": "CLONES."}, None,
                                             weather_expected=True)
        self.assertIn("CLONES.", text)
        self.assertIn("weather forecast is unavailable", text)

    def test_missing_clone_stats_is_announced_not_dropped(self):
        text = self.ns["build_hourly_update"](None, {"spoken": "WEATHER."},
                                             weather_expected=True)
        self.assertIn("clone figures are unavailable", text)
        self.assertIn("WEATHER.", text)

    def test_both_missing_says_nothing(self):
        self.assertEqual(self.ns["build_hourly_update"](None, None), "")

    def test_weather_can_be_disabled_without_complaining(self):
        text = self.ns["build_hourly_update"]({"spoken": "CLONES."}, None,
                                             weather_expected=False)
        self.assertEqual(text, "CLONES.")

    def test_mark_is_only_applied_after_playback(self):
        source = RESPONDER.read_text(encoding="utf-8")
        self.assertIn("if played and entry.get(\"mark_ledger\")", source)
        self.assertIn("mark_clone_stats_reported", source)

    def test_handler_deletes_audio_and_text(self):
        source = RESPONDER.read_text(encoding="utf-8")
        handler = source[source.index("def handle_gitclone_job"):]
        handler = handler[:handler.index("# System error monitor")]
        self.assertIn("unlink()", handler)
        self.assertIn("prune_gitclone_history", handler)
        self.assertIn("finally", handler)

    def test_handler_never_archives_to_mp_reporter(self):
        source = RESPONDER.read_text(encoding="utf-8")
        handler = source[source.index("def handle_gitclone_job"):]
        handler = handler[:handler.index("# System error monitor")]
        self.assertNotIn("mp_reporter_archive_dir", handler)
        self.assertNotIn("shutil.copy2", handler)

    def test_responder_still_hard_disables_email(self):
        source = RESPONDER.read_text(encoding="utf-8")
        self.assertIn('export EMAIL_REPORTS_ENABLED="false"', source)
        self.assertIn('export MP_REPORTER_NO_EMAIL="1"', source)
        # The helpers inherit the guard rather than re-enabling mail.
        self.assertIn('env["EMAIL_REPORTS_ENABLED"] = "false"', source)

    def test_unit_file_schedules_the_hourly_interval(self):
        source = RESPONDER.read_text(encoding="utf-8")
        self.assertIn("MP_OPENCODE_GITCLONE_INTERVAL=3600", source)
        self.assertIn("MP_OPENCODE_WEATHER=true", source)

    def test_monitor_waits_one_interval_between_updates(self):
        source = RESPONDER.read_text(encoding="utf-8")
        monitor = source[source.index("def gitclone_monitor"):]
        monitor = monitor[:monitor.index("def prune_gitclone_history")]
        self.assertIn("STOP_EVENT.wait(wait)", monitor)
        self.assertIn("wait = float(GITCLONE_INTERVAL)", monitor)

    def test_manual_run_does_not_need_the_schedule_to_have_passed(self):
        source = RESPONDER.read_text(encoding="utf-8")
        self.assertIn("MP_OPENCODE_ONESHOT=gitclone MP_OPENCODE_FORCE_PLAY=1",
                      source)
        self.assertIn("last_manual", source)


@unittest.skipUnless(RESPONDER.is_file(), "audio responder is not installed")
class HourlyUpdateWiringTests(unittest.TestCase):
    """The command surface and the status view must agree with the new design."""

    @classmethod
    def setUpClass(cls):
        cls.source = RESPONDER.read_text(encoding="utf-8")

    def test_status_view_reports_unique_cloners_and_weather(self):
        self.assertIn("unique since the start", self.source)
        self.assertIn("Clone ledger", self.source)
        self.assertIn("24-hour forecast", self.source)

    def test_cli_accepts_hourly_aliases(self):
        self.assertIn("gitclone|clone-stats|gitstats|hourly|update)", self.source)

    def test_tray_label_mentions_the_hourly_update(self):
        tray = (RESPONDER.parent / "scripts" / "MP_Audio_Responder_Tray.py")
        if not tray.is_file():
            self.skipTest("tray script not installed")
        text = tray.read_text(encoding="utf-8")
        self.assertIn("Announce Hourly Update", text)

    def test_help_text_documents_the_interval(self):
        self.assertIn("MP_OPENCODE_GITCLONE_INTERVAL", self.source)
        self.assertIn("MP_OPENCODE_WEATHER_NAME", self.source)


if __name__ == "__main__":
    unittest.main()
