#!/usr/bin/env python3
"""MP Auto Responder GitHub project monitor: updates, progress and dispatch.

The monitor runs from a twenty-minute systemd timer, so the contract is:
  * an open-item baseline is seeded before the first delta,
  * every meaningful kind of update is classified once,
  * the text, speech and HTML reports agree on the progress numbers,
  * one tick sends one email with the report, transcript and audio,
  * a second tick in the same bucket sends nothing.
"""

import importlib.util
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "mp_auto_responder.py"
WRAPPER = ROOT / "scripts" / "mp_auto_responder.sh"
EXAMPLE = ROOT / "config.env.example"
README = ROOT / "README.md"


def _load():
    spec = importlib.util.spec_from_file_location("mp_auto_responder",
                                                  MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _record(number=100, title="A change", state="open", is_pr=False,
            merged=False, comments=0, author="contributor", draft=False):
    return {
        "number": number,
        "title": title,
        "url": "https://github.com/example/project/issues/%d" % number,
        "author": author,
        "state": state,
        "is_pr": is_pr,
        "merged": merged,
        "draft": draft,
        "comments": comments,
        "labels": [],
        "updated": "2026-11-22T19:00:00Z",
        "closed_at": "",
    }


def _update(number=100, title="A change", kinds=None, is_pr=False,
            state="open", merged=False, comment_delta=0, author="contributor"):
    return {
        "number": number, "title": title, "url": "",
        "author": author, "state": state, "is_pr": is_pr, "merged": merged,
        "draft": False, "labels": [], "comment_delta": comment_delta,
        "kinds": kinds or ["updated"],
    }


def _progress(**extra):
    progress = {
        "issues_open": 50, "issues_closed": 77,
        "prs_open": 961, "prs_closed": 2026, "prs_merged": 1776,
        "created_24h": 3003, "closed_24h": 1872, "merged_24h": 1696,
        "issues_total": 127, "prs_unmerged_closed": 250, "prs_total": 2987,
        "prs_decided": 2026, "items_total": 3114, "items_completed": 1853,
        "issues_resolved_pct": 60.6, "prs_merged_pct": 59.5,
        "overall_pct": 59.5, "open_remaining": 1011, "fallback": False,
        "captured_at": "2026-11-22T19:20:00+00:00", "deltas": {},
    }
    progress.update(extra)
    return progress


def _data(**extra):
    now = datetime.now(timezone.utc)
    updates = [
        _update(1, "Fix the loader", ["new"], is_pr=False),
        _update(2, "Merge the shader path", ["merged"], is_pr=True,
                state="closed", merged=True),
        _update(3, "Tighten the parser", ["closed"], is_pr=False,
                state="closed"),
        _update(4, "A live pull request", ["comments"], is_pr=True,
                comment_delta=2),
        _update(5, "Push to a live pull request", ["updated"], is_pr=True),
    ]
    groups = {
        "new_issues": [updates[0]], "closed_issues": [updates[2]],
        "reopened_issues": [], "new_prs": [], "merged_prs": [updates[1]],
        "closed_prs": [], "comment_items": [updates[3]],
        "updated_prs": [updates[4]], "updated_issues": [], "retitled": [],
    }
    data = {
        "generated": now, "window_start": now - timedelta(minutes=20),
        "window_end": now, "window_minutes": 20, "hostname": "X299ROG",
        "repo": {"name": "example/project",
                 "description": "Example project",
                 "html_url": "https://github.com/example/project"},
        "progress": _progress(), "updates": updates, "groups": groups,
        "counts": {"new_issues": 1, "closed_issues": 1, "reopened_issues": 0,
                   "new_prs": 0, "merged_prs": 1, "closed_prs": 0,
                   "comments": 1, "updated_prs": 1, "updated_issues": 0,
                   "total": len(updates)},
        "fresh_items": {"1": {}, "2": {}, "3": {}}, "fetch_complete": True,
        "github_reachable": True, "baseline": False, "tracked_open": 0,
    }
    data.update(extra)
    return data


class TestClassification(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_first_seen_item_is_new(self):
        self.assertEqual(self.mod.classify(None, _record()), ["new"])

    def test_state_transitions_are_classified_once(self):
        previous = _record(state="open")
        merged = self.mod.classify(previous, _record(state="closed",
                                                     is_pr=True, merged=True))
        closed = self.mod.classify(previous, _record(state="closed"))
        reopened = self.mod.classify(_record(state="closed"),
                                     _record(state="open"))
        self.assertEqual(merged, ["merged"])
        self.assertEqual(closed, ["closed"])
        self.assertEqual(reopened, ["reopened"])

    def test_comment_delta_and_retitle_survive_together(self):
        previous = _record(title="Old", comments=1)
        current = _record(title="New", comments=4)
        kinds = self.mod.classify(previous, current)
        self.assertIn("comments", kinds)
        self.assertIn("retitled", kinds)

    def test_an_edit_without_a_comment_still_counts_as_updated(self):
        previous = _record(comments=3)
        current = _record(comments=3)
        self.assertEqual(self.mod.classify(previous, current), ["updated"])

    def test_an_old_closed_item_is_not_announced_as_new(self):
        old = _record(state="closed")
        old["created"] = "2026-11-01T00:00:00Z"
        window_start = datetime.now(timezone.utc) - timedelta(minutes=20)
        self.assertEqual(
            self.mod.classify(None, old, window_start), ["updated"])

    def test_a_freshly_created_item_is_new(self):
        fresh = _record(state="open")
        fresh["created"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        window_start = datetime.now(timezone.utc) - timedelta(minutes=20)
        self.assertEqual(self.mod.classify(None, fresh, window_start), ["new"])

    def test_categorize_routes_each_kind_to_its_section(self):
        groups = self.mod.categorize([
            _update(1, "new issue", ["new"]),
            _update(2, "merged pr", ["merged"], is_pr=True),
            _update(3, "closed issue", ["closed"], state="closed"),
            _update(4, "comment", ["comments"], is_pr=True),
            _update(5, "push", ["updated"], is_pr=True),
        ])
        self.assertEqual([u["number"] for u in groups["new_issues"]], [1])
        self.assertEqual([u["number"] for u in groups["merged_prs"]], [2])
        self.assertEqual([u["number"] for u in groups["closed_issues"]], [3])
        self.assertEqual([u["number"] for u in groups["comment_items"]], [4])
        self.assertEqual([u["number"] for u in groups["updated_prs"]], [5])


class TestReportRendering(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_text_report_carries_progress_and_every_section(self):
        text = self.mod.build_text_report(_data(), max_listed=100)
        self.assertIn("MP AUTO RESPONDER", text)
        self.assertIn("Issues resolved    : 77 of 127 (60.6%)", text)
        self.assertIn("Pull requests      : 1,776 merged of 2,987 (59.5%)", text)
        self.assertIn("Overall completion : 59.5%", text)
        self.assertIn("UPDATES IN THIS WINDOW: 5 item(s)", text)
        self.assertIn("New issues (1)", text)
        self.assertIn("Pull requests merged (1)", text)
        self.assertIn("New comments (1)", text)
        self.assertIn("Merge the shader path", text)

    def test_baseline_report_explains_the_first_briefing(self):
        data = _data(baseline=True, tracked_open=1033, updates=[],
                     groups={key: [] for key in (
                         "new_issues", "closed_issues", "reopened_issues",
                         "new_prs", "merged_prs", "closed_prs",
                         "comment_items", "updated_prs", "updated_issues",
                         "retitled")},
                     counts={"total": 0})
        text = self.mod.build_text_report(data, max_listed=100)
        self.assertIn("BASELINE ESTABLISHED", text)
        self.assertIn("tracking 1,033 open issue(s)", text)
        self.assertNotIn("New pull requests (", text)

    def test_speech_script_names_the_counts_and_completion(self):
        script = self.mod.build_speech_script(_data(), max_spoken=8)
        self.assertIn("MP Auto Responder", script)
        self.assertIn("5 updates in the last 20 minutes", script)
        self.assertIn("60.6 percent of issues resolved", script)
        self.assertIn("Overall completion stands at 59.5 percent", script)
        self.assertIn("End of transmission", script)

    def test_baseline_speech_explains_the_tracking_store(self):
        data = _data(baseline=True, tracked_open=1033)
        script = self.mod.build_speech_script(data, max_spoken=8)
        self.assertIn("first briefing", script)
        self.assertIn("1,033 open items", script)
        self.assertNotIn("Highlights", script)

    def test_quiet_window_says_nothing_changed(self):
        data = _data(updates=[], groups={key: [] for key in (
            "new_issues", "closed_issues", "reopened_issues", "new_prs",
            "merged_prs", "closed_prs", "comment_items", "updated_prs",
            "updated_issues", "retitled")}, counts={"total": 0})
        script = self.mod.build_speech_script(data, max_spoken=8)
        self.assertIn("No changes were detected", script)

    def test_html_escapes_markup_in_titles(self):
        data = _data()
        data["groups"]["merged_prs"][0]["title"] = "Fix <script> & ship"
        html = self.mod.build_html_report(data, "report.txt", "audio.mp3",
                                          "transcript.txt")
        self.assertIn("MP AUTO RESPONDER", html)
        self.assertIn("59.5", html)
        self.assertIn("Fix &lt;script&gt; &amp; ship", html)
        self.assertNotIn("<script>", html)

    def test_list_cap_is_announced(self):
        text = self.mod.build_text_report(_data(), max_listed=2)
        self.assertIn("omitted", text)

    def test_announcement_only_report_does_not_promise_an_email(self):
        data = _data(email_enabled=False)
        text = self.mod.build_text_report(data, max_listed=100)
        script = self.mod.build_speech_script(data, max_spoken=8)
        self.assertIn("Email delivery is disabled", text)
        self.assertIn("announced locally", text)
        self.assertIn("Email delivery is disabled", script)
        self.assertNotIn("attached to your email", script)


class TestCollectionAndProgress(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_baseline_is_used_before_the_first_delta(self):
        with mock.patch.object(self.mod, "collect_repo", return_value={"name": "r"}):
            with mock.patch.object(self.mod, "collect_progress",
                                   return_value=_progress()):
                with mock.patch.object(self.mod, "collect_baseline",
                                       return_value=({"1": _record()}, True)):
                    with mock.patch.object(self.mod, "collect_updates") as fetch:
                        data = self.mod.build_report_data(
                            {}, {}, "token", "o/r",
                            datetime.now(timezone.utc) - timedelta(minutes=20),
                            datetime.now(timezone.utc))
        self.assertTrue(data["baseline"])
        self.assertEqual(data["tracked_open"], 1)
        self.assertEqual(data["updates"], [])
        fetch.assert_not_called()

    def test_normal_run_reads_updates_against_the_state(self):
        state = {"items": {"99": _record(99)}}
        with mock.patch.object(self.mod, "collect_repo", return_value={"name": "r"}):
            with mock.patch.object(self.mod, "collect_progress",
                                   return_value=_progress()):
                with mock.patch.object(
                        self.mod, "collect_updates",
                        return_value=([_update(1, "new", ["new"])],
                                      {"1": _record(1)}, True, True)):
                    data = self.mod.build_report_data(
                        {}, state, "token", "o/r",
                        datetime.now(timezone.utc) - timedelta(minutes=20),
                        datetime.now(timezone.utc))
        self.assertFalse(data["baseline"])
        self.assertEqual(data["counts"]["new_issues"], 1)

    def test_rate_limited_search_keeps_the_last_count(self):
        state = {"snapshot": {"issues_open": 50, "issues_closed": 77}}
        with mock.patch.object(self.mod, "search_total", return_value=None):
            progress = self.mod.collect_progress("token", "o/r", state)
        self.assertEqual(progress["issues_open"], 50)
        self.assertEqual(progress["issues_closed"], 77)
        self.assertTrue(progress["fallback"])

    def test_window_is_clamped_after_a_long_outage(self):
        old = "2026-01-01T00:00:00+00:00"
        start, end = self.mod.window_bounds({"last_sent_timestamp": old}, 20)
        self.assertLessEqual((end - start).total_seconds(), 1200)

    def test_bucket_is_one_dispatch_per_interval(self):
        state = {"last_bucket": self.mod.current_bucket(20)}
        self.assertFalse(self.mod.bucket_due(state, 20))
        self.assertTrue(self.mod.bucket_due(state, 20, force=True))


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.paths = {
            "REPORTS_DIR": root / "REPORTS",
            "TTS_DIR": root / "TTS",
            "STATE_FILE": root / "state.json",
            "LOG_FILE": root / "reporter.log",
        }
        for attr, path in self.paths.items():
            patch = mock.patch.object(self.mod, attr, path)
            patch.start()
            self.addCleanup(patch.stop)

    def _cfg(self, **extra):
        cfg = dict(self.mod.CONFIG_DEFAULTS)
        cfg.update({
            "AUTO_RESPONDER_ENABLED": "true",
            "AUTO_RESPONDER_GITHUB_REPO": "example/project",
            "AUTO_RESPONDER_INTERVAL_MINUTES": "20",
            "AUTO_RESPONDER_RECIPIENTS": "owner@example.com",
            "AUTO_RESPONDER_SUBJECT_PREFIX": "MP Auto Responder",
            "AUTO_RESPONDER_ANNOUNCE_AUDIO": "false",
            "AUTO_RESPONDER_ALERT_SOUND": "false",
            "AUTO_RESPONDER_ATTACH_TEXT": "true",
            "AUTO_RESPONDER_ATTACH_TRANSCRIPT": "true",
            "AUTO_RESPONDER_ATTACH_AUDIO": "true",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USE_TLS": "true",
            "SMTP_USE_SSL": "false",
            "SMTP_USER": "sender@example.com",
            "SMTP_PASSWORD": "secret",
            "EMAIL_SENDER_NAME": "MP Reporter",
            "EMAIL_SENDER_ADDR": "sender@example.com",
            "EMAIL_RECIPIENTS": "daily@example.com",
            "CONFIRMATION_ALERT_SOUND": "",
        })
        cfg.update(extra)
        return cfg

    def _fakes(self, data=None):
        audio = Path(self.tmp.name) / "brief.mp3"
        audio.write_bytes(b"ID3fake")
        return [
            mock.patch.object(self.mod, "collect_repo",
                              return_value={"name": "example/project",
                                            "description": "d"}),
            mock.patch.object(self.mod, "collect_progress",
                              return_value=_progress()),
            mock.patch.object(self.mod, "collect_baseline",
                              return_value=({"1": _record(1)}, True)),
            mock.patch.object(self.mod, "collect_updates",
                              return_value=([_update(1, "new", ["new"])],
                                            {"1": _record(1)}, True, True)),
            mock.patch.object(self.mod, "resolve_github_token",
                              return_value="token"),
            mock.patch.object(self.mod, "synthesize_audio",
                              return_value={"mp3": audio, "wav": None,
                                            "size_kb": 1.0}),
            mock.patch.object(self.mod, "play_audio", return_value=True),
            mock.patch.object(self.mod, "play_confirmation_alert"),
        ]

    def test_first_dispatch_seeds_the_baseline_and_attaches_every_report(self):
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=60, context=None):
                sent["host"] = host
            def ehlo(self):
                return None
            def starttls(self, context=None):
                return None
            def login(self, user, password):
                sent["user"] = user
            def sendmail(self, user, recipients, payload):
                sent["recipients"] = recipients
                sent["payload"] = payload
            def quit(self):
                return None

        patches = self._fakes()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg()):
            with mock.patch.object(self.mod.smtplib, "SMTP", FakeSMTP):
                ok = self.mod.execute_dispatch(force=True)

        self.assertTrue(ok)
        self.assertEqual(sent["recipients"], ["owner@example.com"])
        payload = sent["payload"]
        self.assertIn("Content-Type: text/plain", payload)
        self.assertIn("Content-Type: audio/mpeg", payload)
        self.assertIn("MP_Auto_Responder_", payload)
        self.assertIn("MP_Auto_Responder_Transcript_", payload)
        self.assertEqual(
            len(list(self.paths["REPORTS_DIR"].glob("*.txt"))), 2)
        state = json.loads(self.paths["STATE_FILE"].read_text())
        self.assertEqual(state["total_emails_sent"], 1)
        self.assertEqual(list(state["items"]), ["1"])
        self.assertIn("last_cursor", state)
        self.assertEqual(state["snapshot"]["issues_open"], 50)

    def test_second_tick_in_the_same_bucket_is_skipped(self):
        patches = self._fakes()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg()):
            with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                smtp.return_value.login.return_value = None
                self.assertTrue(self.mod.execute_dispatch(force=True))
                calls = smtp.return_value.sendmail.call_count
                self.assertTrue(self.mod.execute_dispatch(force=False))
                self.assertEqual(smtp.return_value.sendmail.call_count, calls)

    def test_no_repository_is_a_quiet_no_op(self):
        with mock.patch.object(
                self.mod, "load_config",
                return_value=self._cfg(AUTO_RESPONDER_GITHUB_REPO="")):
            with mock.patch.object(self.mod, "collect_repo") as repo:
                self.assertTrue(self.mod.execute_dispatch(force=True))
        repo.assert_not_called()

    def test_disabled_responder_does_nothing(self):
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg(
                                   AUTO_RESPONDER_ENABLED="false")):
            self.assertFalse(self.mod.execute_dispatch(force=True))

    def test_no_recipients_fails_before_smtp(self):
        patches = self._fakes()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg(
                                   AUTO_RESPONDER_RECIPIENTS="",
                                   EMAIL_RECIPIENTS="")):
            with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                self.assertFalse(self.mod.execute_dispatch(force=True))
                smtp.assert_not_called()

    def test_missing_password_fails_before_smtp(self):
        patches = self._fakes()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg(SMTP_PASSWORD="")):
            with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                self.assertFalse(self.mod.execute_dispatch(force=True))
                smtp.assert_not_called()

    def test_dry_run_builds_reports_without_smtp(self):
        patches = self._fakes()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(self.mod, "load_config",
                               return_value=self._cfg()):
            with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                self.assertTrue(self.mod.execute_dispatch(
                    force=True, dry_run=True))
                smtp.assert_not_called()
        self.assertFalse(self.paths["STATE_FILE"].exists())
        self.assertEqual(
            len(list(self.paths["REPORTS_DIR"].glob("*.txt"))), 2)

    def test_email_disabled_still_announces_and_records_state(self):
        patches = self._fakes()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        cfg = self._cfg(AUTO_RESPONDER_EMAIL_ENABLED="false",
                        AUTO_RESPONDER_RECIPIENTS="",
                        AUTO_RESPONDER_ANNOUNCE_AUDIO="true",
                        SMTP_PASSWORD="")
        with mock.patch.object(self.mod, "load_config", return_value=cfg):
            with mock.patch.object(self.mod.smtplib, "SMTP") as smtp:
                with mock.patch.object(self.mod, "play_audio",
                                       return_value=True) as play:
                    ok = self.mod.execute_dispatch(force=True)

        self.assertTrue(ok)
        smtp.assert_not_called()
        play.assert_called_once()
        state = json.loads(self.paths["STATE_FILE"].read_text())
        self.assertEqual(state["total_emails_sent"], 0)
        self.assertEqual(state["total_announcements"], 1)
        self.assertEqual(state["history"][-1]["delivery"], "announcement")
        self.assertEqual(state["history"][-1]["status"], "announced")
        self.assertEqual(state["history"][-1]["recipients"], [])

    def test_master_email_switch_does_not_stop_the_announcement(self):
        patches = self._fakes()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        cfg = self._cfg(EMAIL_REPORTS_ENABLED="false",
                        AUTO_RESPONDER_EMAIL_ENABLED="false",
                        AUTO_RESPONDER_ANNOUNCE_AUDIO="true")
        with mock.patch.object(self.mod, "load_config", return_value=cfg):
            with mock.patch.object(self.mod, "play_audio",
                                   return_value=True) as play:
                self.assertTrue(self.mod.execute_dispatch(force=True))
        play.assert_called_once()

    def test_recipients_fall_back_to_the_daily_list(self):
        cfg = self._cfg(AUTO_RESPONDER_RECIPIENTS="")
        self.assertEqual(self.mod.recipients(cfg), ["daily@example.com"])

    def test_interval_defaults_to_twenty_minutes(self):
        cfg = self._cfg(AUTO_RESPONDER_INTERVAL_MINUTES="not-a-number")
        self.assertEqual(self.mod.interval_minutes(cfg), 20)


class TestWiring(unittest.TestCase):
    def test_ships_disabled_with_no_repository(self):
        mod = _load()
        self.assertEqual(mod.repo_slug(dict(mod.CONFIG_DEFAULTS)), "")
        self.assertFalse(mod.truthy(
            mod.CONFIG_DEFAULTS["AUTO_RESPONDER_ENABLED"]))

    def test_config_example_documents_the_twenty_minute_timer(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        self.assertIn('AUTO_RESPONDER_INTERVAL_MINUTES="20"', text)
        self.assertIn("AUTO_RESPONDER_GITHUB_REPO", text)
        self.assertIn('AUTO_RESPONDER_GITHUB_REPO=""', text)
        self.assertIn('AUTO_RESPONDER_ENABLED="false"', text)
        self.assertIn("AUTO_RESPONDER_ANNOUNCE_AUDIO", text)
        self.assertIn("AUTO_RESPONDER_EMAIL_ENABLED", text)

    def test_wrapper_installs_the_timer_with_twenty_minute_default(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("mp-auto-responder.timer", text)
        self.assertIn("OnUnitActiveSec", text)
        self.assertIn("DEFAULT_MINUTES=20", text)

    def test_readme_documents_the_monitor(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("mp_auto_responder.py", text)
        self.assertIn("AUTO_RESPONDER_GITHUB_REPO", text)

    def test_the_removed_project_is_not_referenced(self):
        for path in (MODULE_PATH, WRAPPER, EXAMPLE, README):
            with self.subTest(path=path.name):
                self.assertNotIn("Leonida", path.read_text(encoding="utf-8"))

    def test_module_and_wrapper_ship(self):
        self.assertTrue(MODULE_PATH.is_file())
        self.assertTrue(WRAPPER.is_file())


if __name__ == "__main__":
    unittest.main()
