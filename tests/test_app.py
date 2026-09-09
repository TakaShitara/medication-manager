import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from app.app import create_app  # noqa: E402


class MedicationAppTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.now_value = datetime(2026, 9, 8, 8, 15, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.notifications = []

        def notifier(url, message):
            self.notifications.append((url, message))
            return None

        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.temp_dir.name,
                "TZ": "Asia/Tokyo",
                "DISCORD_WEBHOOK_URL": "https://example.com/webhook",
                "NOW_PROVIDER": lambda: self.now_value,
                "NOTIFIER": notifier,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    @property
    def state_path(self):
        return os.path.join(self.temp_dir.name, "state.json")

    def read_state_file(self):
        with open(self.state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_initial_status_creates_state_for_today(self):
        response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["date"], "2026-09-08")
        self.assertFalse(data["morning"]["taken"])
        self.assertFalse(data["night"]["taken"])
        self.assertTrue(os.path.exists(self.state_path))

    def test_take_is_idempotent_and_notifies_once(self):
        first = self.client.post("/api/take/morning").get_json()
        second = self.client.post("/api/take/morning").get_json()

        self.assertTrue(first["morning"]["taken"])
        self.assertEqual(first["morning"]["time"], "08:15")
        self.assertTrue(first["operation"]["changed"])
        self.assertFalse(second["operation"]["changed"])
        self.assertEqual(len(self.notifications), 1)
        self.assertIn("朝のお薬を服用しました", self.notifications[0][1])

    def test_cancel_is_idempotent_and_notifies_once(self):
        self.client.post("/api/take/night")
        self.now_value = datetime(2026, 9, 8, 21, 16, tzinfo=ZoneInfo("Asia/Tokyo"))

        first = self.client.post("/api/cancel/night").get_json()
        second = self.client.post("/api/cancel/night").get_json()

        self.assertFalse(first["night"]["taken"])
        self.assertIsNone(first["night"]["time"])
        self.assertTrue(first["operation"]["changed"])
        self.assertFalse(second["operation"]["changed"])
        self.assertEqual(len(self.notifications), 2)
        self.assertIn("夜のお薬の服薬記録を取り消しました", self.notifications[1][1])

    def test_status_resets_when_state_date_is_old(self):
        os.makedirs(self.temp_dir.name, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "date": "2026-09-07",
                    "morning": {"taken": True, "time": "08:00"},
                    "night": {"taken": True, "time": "21:00"},
                },
                handle,
            )

        data = self.client.get("/api/status").get_json()

        self.assertEqual(data["date"], "2026-09-08")
        self.assertFalse(data["morning"]["taken"])
        self.assertFalse(data["night"]["taken"])
        self.assertEqual(self.read_state_file()["date"], "2026-09-08")

    def test_broken_state_file_recovers_to_today(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            handle.write("{broken")

        data = self.client.get("/api/status").get_json()

        self.assertEqual(data["date"], "2026-09-08")
        self.assertFalse(data["morning"]["taken"])
        self.assertFalse(data["night"]["taken"])

    def test_invalid_taken_state_without_time_recovers_to_today(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "date": "2026-09-08",
                    "morning": {"taken": True, "time": None},
                    "night": {"taken": False, "time": None},
                },
                handle,
            )

        data = self.client.get("/api/status").get_json()

        self.assertFalse(data["morning"]["taken"])
        self.assertIsNone(data["morning"]["time"])

    def test_discord_failure_returns_warning_but_keeps_state(self):
        def failing_notifier(url, message):
            return "Discordへの通知に失敗しました"

        app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.temp_dir.name,
                "TZ": "Asia/Tokyo",
                "DISCORD_WEBHOOK_URL": "https://example.com/webhook",
                "NOW_PROVIDER": lambda: self.now_value,
                "NOTIFIER": failing_notifier,
            }
        )

        data = app.test_client().post("/api/take/morning").get_json()

        self.assertTrue(data["morning"]["taken"])
        self.assertEqual(data["warning"], "Discordへの通知に失敗しました")
        self.assertFalse(data["operation"]["notification"]["sent"])

    def test_unknown_period_returns_404_json(self):
        response = self.client.post("/api/take/lunch")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
