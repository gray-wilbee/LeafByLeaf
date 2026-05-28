from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

os.environ["TRACKER_SCHEDULER_DISABLED"] = "1"

import db
import journal
import topics
import trackers
import llm_service


class TempTrackerDB(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.temp_db = os.path.join(self.tmp, "app.db")
        self.original_get_db = db.get_db
        self.original_journal_dir = journal.JOURNAL_DIR

        def temp_get_db(path=None):
            return self.original_get_db(self.temp_db)

        db.get_db = temp_get_db
        journal.JOURNAL_DIR = os.path.join(self.tmp, "journal")
        topics.init_db()
        journal.init_entries_db()
        trackers.init_db()

    def tearDown(self):
        db.get_db = self.original_get_db
        journal.JOURNAL_DIR = self.original_journal_dir
        self.stack.close()


class TrackerScheduleTests(TempTrackerDB):
    def test_due_trackers_match_any_cron_time_on_date(self):
        monday = trackers.create_tracker(
            1,
            name="Monday morning",
            cron_expression="0 9 * * 1",
        )
        daily = trackers.create_tracker(
            1,
            name="Daily evening",
            cron_expression="0 22 * * *",
        )

        due_monday = {t["id"] for t in trackers.list_trackers_due(1, "2026-05-25")}
        due_tuesday = {t["id"] for t in trackers.list_trackers_due(1, "2026-05-26")}

        self.assertEqual(due_monday, {monday, daily})
        self.assertEqual(due_tuesday, {daily})

    def test_interval_uses_previous_scheduled_occurrence_for_first_entry(self):
        topics.set_setting(1, "timezone", "America/Chicago")
        tracker_id = trackers.create_tracker(
            1,
            name="Daily cleanup",
            cron_expression="0 0 * * *",
        )
        tracker = trackers.get_tracker(1, tracker_id)

        start, end, latest = llm_service._tracker_interval_bounds(
            1,
            tracker,
            "2026-05-27",
            interval_end="2026-05-27T22:00:00-05:00",
            eval_time="22:00",
        )

        self.assertIsNone(latest)
        self.assertEqual(start.isoformat(), "2026-05-26T22:00:00-05:00")
        self.assertEqual(end.isoformat(), "2026-05-27T22:00:00-05:00")

    def test_interval_uses_latest_tracker_entry_when_present(self):
        topics.set_setting(1, "timezone", "America/Chicago")
        tracker_id = trackers.create_tracker(
            1,
            name="Daily cleanup",
            cron_expression="0 0 * * *",
        )
        trackers.upsert_entry(1, tracker_id, "2026-05-24", "true", source="manual")
        tracker = trackers.get_tracker(1, tracker_id)

        start, end, latest = llm_service._tracker_interval_bounds(
            1,
            tracker,
            "2026-05-27",
            interval_end="2026-05-27T22:00:00-05:00",
            eval_time="22:00",
        )

        self.assertEqual(latest, "2026-05-24")
        self.assertEqual(start.isoformat(), "2026-05-24T22:00:00-05:00")
        self.assertEqual(end.isoformat(), "2026-05-27T22:00:00-05:00")


class AppSchedulerTests(unittest.TestCase):
    def test_scheduler_runs_once_after_user_eval_time(self):
        import app

        settings = {
            "timezone": "America/Chicago",
            "tracker_eval_time": "22:00",
            "tracker_capture_day": "previous",
            "tracker_capture_last_run": "",
        }
        saved = {}

        def fake_get_setting(user_id, key, default=""):
            return settings.get(key, default)

        def fake_set_setting(user_id, key, value):
            saved[key] = value
            settings[key] = value

        with patch.object(app.users, "list_all_users", return_value=[{"id": 1, "disabled_at": None}]), \
             patch.object(app.topics, "get_setting", side_effect=fake_get_setting), \
            patch.object(app.topics, "set_setting", side_effect=fake_set_setting), \
             patch.object(app.llm_service, "run_tracker_cron", return_value={"processed": 3, "results": []}) as run:
            result = app.run_scheduled_tracker_batches(
                datetime(2026, 5, 28, 3, 30, tzinfo=timezone.utc)
            )
            second = app.run_scheduled_tracker_batches(
                datetime(2026, 5, 28, 3, 45, tzinfo=timezone.utc)
            )

        self.assertEqual(result[0]["entry_date"], "2026-05-26")
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["eval_time"], "22:00")
        self.assertEqual(run.call_args.kwargs["interval_end"], "2026-05-27T22:00:00-05:00")
        self.assertIn("tracker_capture_last_run", saved)
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
