import json
import tempfile
import unittest
from pathlib import Path

from codex_monitor_app.services import MonitorStateService
from codex_monitor_app.storage import UsageStorage
from codex_monitor_app.ui import CodexMonitorApp


class FakeRoot:
    def __init__(self):
        self.after_calls = []
        self.cancelled = []
        self._next_job_id = 0

    def after(self, delay_ms, callback):
        self._next_job_id += 1
        job_id = f"job-{self._next_job_id}"
        self.after_calls.append((job_id, delay_ms, callback))
        return job_id

    def after_cancel(self, job_id):
        self.cancelled.append(job_id)

    def after_idle(self, callback):
        callback()


class FakeStatusVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeState:
    def __init__(self, current_email="user@example.com", latest_jwt="latest-jwt"):
        self.current_account_email = current_email
        self.latest_jwt = latest_jwt
        self.clear_calls = 0

    def get_latest_jwt_for_fetch(self, _email=None):
        return self.latest_jwt

    def clear_session_credentials(self):
        self.clear_calls += 1
        return True

    def remember_auth_jwt(self, jwt):
        self.latest_jwt = jwt


class FakeAuthFileService:
    def __init__(self, exists=True, tokens=None):
        self.exists = exists
        self.tokens = list(tokens or [])
        self.auth_file_path = "/tmp/auth.json"

    def auth_file_exists(self):
        return self.exists

    def load_access_token(self):
        if self.tokens:
            return self.tokens.pop(0)
        return None

    def load_snapshot(self):
        return {
            "auth_mode": "chatgpt",
            "tokens": {"access_token": self.load_access_token()},
            "last_refresh": "2026-04-24T15:17:00.949966Z",
        }


class MonitorStateServiceTests(unittest.TestCase):
    def test_restores_current_account_from_saved_auto_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "usage.json"
            storage_path.write_text(
                json.dumps(
                    {
                        "user@example.com": {
                            "reset_ts": 123,
                            "used_percent": 10,
                            "auto_fetch": "1 Hr",
                            "last_fetched": 100,
                        }
                    }
                ),
                encoding="utf-8",
            )

            state = MonitorStateService(UsageStorage(str(storage_path)))

            self.assertEqual(state.current_account_email, "user@example.com")
            self.assertEqual(
                state.get_display_auto_fetch("user@example.com"),
                "1 Hr",
            )


class AuthFlowUiTests(unittest.TestCase):
    def _build_app(self, auth_service, state=None):
        app = CodexMonitorApp.__new__(CodexMonitorApp)
        app.root = FakeRoot()
        app.status_var = FakeStatusVar()
        app.auth_file_service = auth_service
        app.state = state or FakeState()
        app._auth_retry_job = None
        app._auth_retry_attempts = 0
        app._missing_token_retry_job = None
        app._missing_token_retry_attempts = 0
        app._auth_change_job = None
        app._last_seen_auth_signature = None
        app._last_auth_refresh_marker = None
        app._last_seen_access_token = None
        app._pending_fetches = 0
        app.refresh_ui_calls = 0
        app.finish_messages = []
        app.begin_messages = []
        app.notifications = []
        app.refresh_ui = lambda *args, **kwargs: setattr(
            app, "refresh_ui_calls", app.refresh_ui_calls + 1
        )
        app._begin_fetch = lambda message: app.begin_messages.append(message)
        app._finish_fetch = lambda message: app.finish_messages.append(message)
        app._notify_user = lambda title, message: app.notifications.append(
            (title, message)
        )
        return app

    def test_process_auth_file_retries_missing_token_before_logout(self):
        app = self._build_app(FakeAuthFileService(exists=True, tokens=[None]))

        app.process_auth_file()

        self.assertEqual(app._missing_token_retry_attempts, 1)
        self.assertEqual(len(app.root.after_calls), 1)
        self.assertEqual(
            app.root.after_calls[0][1],
            CodexMonitorApp.MISSING_TOKEN_RETRY_MS,
        )
        self.assertEqual(app.state.clear_calls, 0)
        self.assertEqual(app.begin_messages, [])
        self.assertIn("Retrying read", app.status_var.get())

    def test_finalize_logout_clears_session_when_auth_file_has_no_token(self):
        app = self._build_app(FakeAuthFileService(exists=True, tokens=[None]))

        app._finalize_logout("Logged out.")

        self.assertEqual(app.state.clear_calls, 1)
        self.assertEqual(app.refresh_ui_calls, 1)
        self.assertEqual(app.finish_messages, ["Logged out."])

    def test_process_auth_file_notifies_when_auth_refresh_is_detected(self):
        auth_service = FakeAuthFileService(exists=True, tokens=["new-token"])
        app = self._build_app(auth_service)
        app._last_seen_access_token = "old-token"
        app._last_auth_refresh_marker = "2026-04-24T14:17:00.000000Z"

        original_thread = __import__("threading").Thread

        class ImmediateThread:
            def __init__(self, target=None, args=(), daemon=None):
                self.target = target
                self.args = args

            def start(self):
                if self.target:
                    self.target(*self.args)

        import codex_monitor_app.ui as ui_module

        ui_module.threading.Thread = ImmediateThread
        app._bg_fetch_single = lambda expected_email, jwt: app.finish_messages.append(jwt)
        try:
            app.process_auth_file()
        finally:
            ui_module.threading.Thread = original_thread

        self.assertTrue(app.begin_messages)
        self.assertIn("Detected auth refresh", app.begin_messages[0])
        self.assertEqual(len(app.notifications), 1)
        self.assertIn("Detected Codex auth refresh", app.notifications[0][1])
        self.assertEqual(app.state.latest_jwt, "new-token")


if __name__ == "__main__":
    unittest.main()
