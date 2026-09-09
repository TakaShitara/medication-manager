import http.client
import json
import logging
import os
import re
import tempfile
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, abort, current_app, jsonify, render_template, request


PERIODS = {
    "morning": "朝のお薬",
    "night": "夜のお薬",
}

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]
MAX_DISCORD_LOG_BODY_BYTES = 1024
MAX_DISCORD_LOG_TEXT_CHARS = 1000
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
WEBHOOK_PATH_PATTERN = re.compile(r"/?api/webhooks/[^\s\"'<>]+")

logger = logging.getLogger(__name__)


def default_state(date_text):
    return {
        "date": date_text,
        "morning": {"taken": False, "time": None},
        "night": {"taken": False, "time": None},
    }


def format_display_date(date_text):
    date_value = datetime.strptime(date_text, "%Y-%m-%d").date()
    weekday = WEEKDAYS_JA[date_value.weekday()]
    return f"{date_value.year}年{date_value.month}月{date_value.day}日（{weekday}）"


def validate_state(value):
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("date"), str):
        return False

    for period in PERIODS:
        entry = value.get(period)
        if not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("taken"), bool):
            return False
        time_value = entry.get("time")
        if time_value is not None and not isinstance(time_value, str):
            return False
        if entry["taken"] and not time_value:
            return False
        if not entry["taken"] and time_value is not None:
            return False

    try:
        datetime.strptime(value["date"], "%Y-%m-%d")
    except ValueError:
        return False

    return True


class MedicationStore:
    def __init__(self, data_dir, timezone_name="Asia/Tokyo", now_provider=None):
        self.data_dir = data_dir
        self.state_path = os.path.join(data_dir, "state.json")
        self.timezone = ZoneInfo(timezone_name)
        self.now_provider = now_provider
        self.lock = threading.Lock()

    def now(self):
        if self.now_provider is None:
            return datetime.now(self.timezone)

        value = self.now_provider()
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    def today_text(self):
        return self.now().date().isoformat()

    def current_time_text(self):
        return self.now().strftime("%H:%M")

    def load_current(self):
        with self.lock:
            state, should_save = self._load_current_unlocked()
            if should_save:
                self._write_unlocked(state)
            return deepcopy(state)

    def take(self, period):
        self._require_period(period)
        with self.lock:
            state, should_save = self._load_current_unlocked()
            if should_save:
                self._write_unlocked(state)

            entry = state[period]
            if entry["taken"]:
                return deepcopy(state), False, None

            taken_time = self.current_time_text()
            entry["taken"] = True
            entry["time"] = taken_time
            self._write_unlocked(state)
            return deepcopy(state), True, taken_time

    def cancel(self, period):
        self._require_period(period)
        with self.lock:
            state, should_save = self._load_current_unlocked()
            if should_save:
                self._write_unlocked(state)

            entry = state[period]
            if not entry["taken"]:
                return deepcopy(state), False, None

            cancel_time = self.current_time_text()
            entry["taken"] = False
            entry["time"] = None
            self._write_unlocked(state)
            return deepcopy(state), True, cancel_time

    def _load_current_unlocked(self):
        today = self.today_text()
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return default_state(today), True

        if not validate_state(state):
            return default_state(today), True

        if state["date"] != today:
            return default_state(today), True

        return state, False

    def _write_unlocked(self, state):
        os.makedirs(self.data_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix="state-",
            suffix=".tmp",
            dir=self.data_dir,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.state_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _require_period(self, period):
        if period not in PERIODS:
            raise ValueError("unknown period")


def get_discord_logger():
    try:
        return current_app.logger
    except RuntimeError:
        return logger


def redact_log_value(value):
    text = str(value)
    text = URL_PATTERN.sub("[redacted-url]", text)
    text = WEBHOOK_PATH_PATTERN.sub("[redacted-webhook-path]", text)
    if len(text) > MAX_DISCORD_LOG_TEXT_CHARS:
        return f"{text[:MAX_DISCORD_LOG_TEXT_CHARS]}... (truncated)"
    return text


def read_response_body_for_log(response):
    try:
        body = response.read(MAX_DISCORD_LOG_BODY_BYTES)
    except OSError as error:
        return f"<failed to read response body: {redact_log_value(error)}>"

    if not body:
        return ""

    return redact_log_value(body.decode("utf-8", errors="replace"))


def log_discord_http_failure(status_code, reason, response_body):
    details = f"status={status_code} reason={redact_log_value(reason or '')}"
    if response_body:
        details = f"{details} response_body={response_body}"

    get_discord_logger().warning("Discord webhook notification failed: %s", details)


def log_discord_exception(error):
    reason = getattr(error, "reason", None)
    details = f"exception={error.__class__.__name__} message={redact_log_value(error)}"
    if reason is not None:
        details = f"{details} reason={redact_log_value(reason)}"

    get_discord_logger().warning("Discord webhook notification failed: %s", details)


def send_discord_message(webhook_url, content):
    webhook_url = (webhook_url or "").strip()
    if not webhook_url:
        get_discord_logger().warning("Discord webhook notification skipped: DISCORD_WEBHOOK_URL is not set.")
        return "Discord Webhookが未設定です"

    payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")

    try:
        request_obj = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "medication-manager/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request_obj, timeout=5) as response:
            status_code = response.getcode()
            if status_code < 200 or status_code >= 300:
                log_discord_http_failure(
                    status_code,
                    getattr(response, "reason", ""),
                    read_response_body_for_log(response),
                )
                return "Discordへの通知に失敗しました"
    except urllib.error.HTTPError as error:
        log_discord_http_failure(error.code, error.reason, read_response_body_for_log(error))
        return "Discordへの通知に失敗しました"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.InvalidURL) as error:
        log_discord_exception(error)
        return "Discordへの通知に失敗しました"

    return None


def make_payload(state, warning=None, operation=None):
    payload = {
        "date": state["date"],
        "displayDate": format_display_date(state["date"]),
        "morning": deepcopy(state["morning"]),
        "night": deepcopy(state["night"]),
    }
    if warning:
        payload["warning"] = warning
    if operation:
        payload["operation"] = operation
    return payload


def create_app(config=None):
    flask_app = Flask(__name__)
    flask_app.config.update(
        DATA_DIR=os.environ.get("DATA_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))),
        TZ=os.environ.get("TZ", "Asia/Tokyo"),
        DISCORD_WEBHOOK_URL=os.environ.get("DISCORD_WEBHOOK_URL", ""),
        NOTIFIER=send_discord_message,
        NOW_PROVIDER=None,
    )
    if config:
        flask_app.config.update(config)

    store = MedicationStore(
        flask_app.config["DATA_DIR"],
        flask_app.config["TZ"],
        now_provider=flask_app.config["NOW_PROVIDER"],
    )
    flask_app.extensions["medication_store"] = store

    @flask_app.after_request
    def no_store(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @flask_app.get("/")
    def index():
        return render_template("index.html")

    @flask_app.get("/api/status")
    def status():
        state = store.load_current()
        return jsonify(make_payload(state))

    @flask_app.post("/api/take/<period>")
    def take(period):
        if period not in PERIODS:
            abort(404)

        state, changed, action_time = store.take(period)
        warning = None
        notification = {"sent": False, "skipped": not changed}

        if changed:
            message = f"{action_time} 💊 {PERIODS[period]}を服用しました。"
            warning = flask_app.config["NOTIFIER"](flask_app.config["DISCORD_WEBHOOK_URL"], message)
            notification = {"sent": warning is None, "skipped": False}
            if warning:
                notification["error"] = warning

        return jsonify(
            make_payload(
                state,
                warning=warning,
                operation={"type": "take", "period": period, "changed": changed, "notification": notification},
            )
        )

    @flask_app.post("/api/cancel/<period>")
    def cancel(period):
        if period not in PERIODS:
            abort(404)

        state, changed, action_time = store.cancel(period)
        warning = None
        notification = {"sent": False, "skipped": not changed}

        if changed:
            message = f"{action_time} ↩️ {PERIODS[period]}の服薬記録を取り消しました。"
            warning = flask_app.config["NOTIFIER"](flask_app.config["DISCORD_WEBHOOK_URL"], message)
            notification = {"sent": warning is None, "skipped": False}
            if warning:
                notification["error"] = warning

        return jsonify(
            make_payload(
                state,
                warning=warning,
                operation={"type": "cancel", "period": period, "changed": changed, "notification": notification},
            )
        )

    @flask_app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found"}), 404
        return error

    return flask_app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
