import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from app.app import create_app, redact_log_value, send_discord_message  # noqa: E402


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        self.server.received_requests.append(
            {
                "path": self.path,
                "body": body,
                "content_type": self.headers.get("Content-Type"),
                "user_agent": self.headers.get("User-Agent"),
            }
        )

        self.send_response(self.server.response_status)
        self.end_headers()
        if self.server.response_body:
            self.wfile.write(self.server.response_body)

    def log_message(self, format, *args):
        return


def start_webhook_server(status, body=b""):
    server = HTTPServer(("127.0.0.1", 0), WebhookHandler)
    server.response_status = status
    server.response_body = body
    server.received_requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


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

    def test_discord_sender_treats_204_no_content_as_success(self):
        server, thread = start_webhook_server(204)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/api/webhooks/secret-token"

            result = send_discord_message(url, "08:15 💊 朝のお薬を服用しました。")

            self.assertIsNone(result)
            self.assertEqual(len(server.received_requests), 1)
            request_body = json.loads(server.received_requests[0]["body"].decode("utf-8"))
            self.assertEqual(request_body["content"], "08:15 💊 朝のお薬を服用しました。")
            self.assertEqual(server.received_requests[0]["user_agent"], "medication-manager/1.0")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_discord_sender_logs_http_error_without_webhook_url(self):
        body = b'{"message":"bad webhook https://discord.com/api/webhooks/secret/token"}'
        server, thread = start_webhook_server(500, body)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/api/webhooks/secret-token"

            with self.assertLogs("app.app", level="WARNING") as logs:
                result = send_discord_message(url, "test")

            log_text = "\n".join(logs.output)
            self.assertEqual(result, "Discordへの通知に失敗しました")
            self.assertIn("status=500", log_text)
            self.assertIn("[redacted-url]", log_text)
            self.assertNotIn(url, log_text)
            self.assertNotIn("secret/token", log_text)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_discord_sender_logs_invalid_url_without_webhook_url(self):
        webhook_url = "https://discord.com/api/webhooks/secret/token with spaces"

        with self.assertLogs("app.app", level="WARNING") as logs:
            result = send_discord_message(webhook_url, "test")

        log_text = "\n".join(logs.output)
        self.assertEqual(result, "Discordへの通知に失敗しました")
        self.assertIn("exception=", log_text)
        self.assertNotIn(webhook_url, log_text)
        self.assertNotIn("secret/token", log_text)

    def test_redact_log_value_masks_urls(self):
        text = redact_log_value("failed https://discord.com/api/webhooks/secret/token")

        self.assertEqual(text, "failed [redacted-url]")

    def test_unknown_period_returns_404_json(self):
        response = self.client.post("/api/take/lunch")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
