import json
import tempfile
import unittest
from pathlib import Path

from codex_monitor_app.services import MonitorStateService
from codex_monitor_app.storage import UsageStorage
from codex_monitor_app.updater import ReleaseInfo
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
        self.saved_auto_fetch = []
        self.due_auto_fetch_jwt = None
        self.auto_fetch_interval = "None"
        self.usage_map = {}

    def get_latest_jwt_for_fetch(self, _email=None):
        return self.latest_jwt

    def clear_session_credentials(self):
        self.clear_calls += 1
        return True

    def remember_auth_jwt(self, jwt):
        self.latest_jwt = jwt

    def save_auto_fetch_value(self, email, new_value):
        self.saved_auto_fetch.append((email, new_value))
        self.auto_fetch_interval = new_value
        return True

    def get_due_auto_fetch_jwt(self, _now):
        return self.due_auto_fetch_jwt

    def get_auto_fetch_value(self):
        return self.auto_fetch_interval


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
            meta_path = Path(temp_dir) / "usage.meta.json"
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

            state = MonitorStateService(UsageStorage(str(storage_path), str(meta_path)))

            self.assertEqual(state.current_account_email, "user@example.com")
            self.assertEqual(
                state.get_display_auto_fetch("user@example.com"),
                "1 Hr",
            )
            self.assertEqual(
                state.storage.get_meta_value("auto_fetch"),
                "1 Hr",
            )
            self.assertNotIn("auto_fetch", state.usage_map["user@example.com"])

    def test_migrates_mixed_storage_without_leaving_fake_accounts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "usage.json"
            meta_path = Path(temp_dir) / "usage.meta.json"
            storage_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "real@example.com": {
                                "reset_ts": 100,
                                "used_percent": 20,
                                "auto_fetch": "1 Hr",
                                "last_fetched": 10,
                            }
                        },
                        "__meta__": {"current_account_email": "real@example.com"},
                        "real@example.com": {
                            "reset_ts": 200,
                            "used_percent": 40,
                            "auto_fetch": "None",
                            "last_fetched": 30,
                        },
                    }
                ),
                encoding="utf-8",
            )

            storage = UsageStorage(str(storage_path), str(meta_path))
            usage_map = storage.load()

            self.assertEqual(sorted(usage_map.keys()), ["real@example.com"])
            self.assertEqual(usage_map["real@example.com"]["used_percent"], 40)
            self.assertNotIn("auto_fetch", usage_map["real@example.com"])
            self.assertEqual(
                storage.get_meta_value("current_account_email"),
                "real@example.com",
            )
            self.assertEqual(storage.get_meta_value("auto_fetch"), "1 Hr")

            repaired_storage = json.loads(storage_path.read_text(encoding="utf-8"))
            repaired_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertNotIn("accounts", repaired_storage)
            self.assertNotIn("__meta__", repaired_storage)
            self.assertEqual(sorted(repaired_storage.keys()), ["real@example.com"])
            self.assertEqual(
                repaired_meta.get("current_account_email"),
                "real@example.com",
            )
            self.assertEqual(repaired_meta.get("auto_fetch"), "1 Hr")
            self.assertNotIn("auto_fetch", repaired_storage["real@example.com"])

    def test_auto_fetch_config_survives_account_switch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "usage.json"
            meta_path = Path(temp_dir) / "usage.meta.json"
            storage_path.write_text(
                json.dumps(
                    {
                        "first@example.com": {
                            "reset_ts": 100,
                            "used_percent": 20,
                            "last_fetched": 10,
                        },
                        "second@example.com": {
                            "reset_ts": 200,
                            "used_percent": 40,
                            "last_fetched": 20,
                        },
                    }
                ),
                encoding="utf-8",
            )

            state = MonitorStateService(UsageStorage(str(storage_path), str(meta_path)))
            state.set_current_account("first@example.com", "first-jwt")
            self.assertTrue(state.save_auto_fetch_value("first@example.com", "3 Hrs"))

            state.set_current_account("second@example.com", "second-jwt")

            self.assertEqual(state.get_display_auto_fetch("second@example.com"), "3 Hrs")
            self.assertNotIn("auto_fetch", state.usage_map["first@example.com"])
            self.assertEqual(state.storage.get_meta_value("auto_fetch"), "3 Hrs")

    def test_sorted_usage_items_pins_active_account_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_path = Path(temp_dir) / "usage.json"
            meta_path = Path(temp_dir) / "usage.meta.json"
            storage_path.write_text(
                json.dumps(
                    {
                        "old@example.com": {"reset_ts": 10, "last_fetched": 1},
                        "active@example.com": {"reset_ts": 30, "last_fetched": 3},
                        "middle@example.com": {"reset_ts": 20, "last_fetched": 2},
                    }
                ),
                encoding="utf-8",
            )
            meta_path.write_text(
                json.dumps({"current_account_email": "active@example.com"}),
                encoding="utf-8",
            )

            state = MonitorStateService(UsageStorage(str(storage_path), str(meta_path)))

            self.assertEqual(
                [email for email, _data in state.sorted_usage_items()],
                ["active@example.com", "old@example.com", "middle@example.com"],
            )

    def test_export_import_merges_accounts_and_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_storage_path = Path(temp_dir) / "source.json"
            source_meta_path = Path(temp_dir) / "source.meta.json"
            target_storage_path = Path(temp_dir) / "target.json"
            target_meta_path = Path(temp_dir) / "target.meta.json"
            source = UsageStorage(str(source_storage_path), str(source_meta_path))
            target = UsageStorage(str(target_storage_path), str(target_meta_path))

            source.meta = {
                "current_account_email": "home@example.com",
                "auto_fetch": "12 Hrs",
            }
            payload = source.export_data(
                {
                    "home@example.com": {
                        "reset_ts": 200,
                        "used_percent": 50,
                        "last_fetched": 20,
                        "auto_fetch": "1 Hr",
                        "jwt": "secret",
                    }
                }
            )

            target_storage_path.write_text(
                json.dumps(
                    {
                        "friend@example.com": {
                            "reset_ts": 100,
                            "used_percent": 10,
                            "last_fetched": 10,
                        }
                    }
                ),
                encoding="utf-8",
            )
            imported = target.import_data(payload)

            self.assertEqual(
                sorted(imported.keys()),
                ["friend@example.com", "home@example.com"],
            )
            self.assertEqual(target.get_meta_value("auto_fetch"), "12 Hrs")
            self.assertEqual(
                target.get_meta_value("current_account_email"),
                "home@example.com",
            )
            self.assertNotIn("jwt", imported["home@example.com"])
            self.assertNotIn("auto_fetch", imported["home@example.com"])


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
        app._update_check_timer_id = None
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

    def _release(self):
        return ReleaseInfo(
            tag_name="v9.9.9",
            version="9.9.9",
            asset_name="CodexMonitor-macOS.zip",
            asset_url="https://example.com/CodexMonitor-macOS.zip",
            html_url="https://example.com/releases/v9.9.9",
        )

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

    def test_save_auto_fetch_value_notifies_when_user_changes_setting(self):
        state = FakeState()
        state.usage_map = {"user@example.com": {}}
        app = self._build_app(FakeAuthFileService(), state=state)

        app.save_auto_fetch_value("user@example.com", "1 Hr")

        self.assertEqual(state.saved_auto_fetch, [("user@example.com", "1 Hr")])
        self.assertEqual(app.refresh_ui_calls, 1)
        self.assertEqual(app.status_var.get(), "Auto-fetch set to 1 Hr.")
        self.assertEqual(len(app.notifications), 1)
        self.assertEqual(
            app.notifications[0][1],
            "Auto-fetch enabled for the active account: every 1 Hr.",
        )

    def test_check_auto_fetch_notifies_when_auto_fetch_is_triggered(self):
        state = FakeState()
        state.due_auto_fetch_jwt = "due-jwt"
        state.auto_fetch_interval = "3 Hrs"
        state.usage_map = {"user@example.com": {}}
        app = self._build_app(FakeAuthFileService(), state=state)
        app._bg_fetch_single = lambda expected_email, jwt: app.finish_messages.append(
            (expected_email, jwt)
        )

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
        try:
            app.check_auto_fetch()
        finally:
            ui_module.threading.Thread = original_thread

        self.assertEqual(
            app.notifications[0][1],
            "Auto-fetch triggered for user@example.com (3 Hrs).",
        )
        self.assertEqual(app.begin_messages, ["Auto-fetching quota for user@example.com..."])
        self.assertEqual(app.finish_messages, [("user@example.com", "due-jwt")])

    def test_update_check_prepares_download_before_button_press(self):
        app = self._build_app(FakeAuthFileService())
        app.manual_button = None
        app.update_button = None
        app.theme_button = None
        app._available_release = None
        app._prepared_update = None
        app._update_check_in_progress = True
        app._update_prepare_in_progress = False
        app._update_in_progress = False
        release = self._release()

        original_thread = __import__("threading").Thread

        class ImmediateThread:
            def __init__(self, target=None, args=(), daemon=None):
                self.target = target
                self.args = args

            def start(self):
                if self.target:
                    self.target(*self.args)

        import codex_monitor_app.ui as ui_module

        original_prepare_update = ui_module.prepare_update
        ui_module.threading.Thread = ImmediateThread
        ui_module.prepare_update = lambda _release: (
            "/tmp/source.app",
            "/tmp/target.app",
            "/tmp/update-root",
        )
        try:
            app._finish_update_check(release, None)
            _job_id, _delay, callback = app.root.after_calls[-2]
            callback()
        finally:
            ui_module.threading.Thread = original_thread
            ui_module.prepare_update = original_prepare_update

        self.assertFalse(app._update_prepare_in_progress)
        self.assertEqual(app._prepared_update[0], release)
        self.assertEqual(
            app.status_var.get(),
            "Update v9.9.9 is downloaded and ready to install.",
        )

    def test_update_button_installs_prepared_update_without_redownloading(self):
        app = self._build_app(FakeAuthFileService())
        app.manual_button = None
        app.update_button = None
        app.theme_button = None
        app._update_in_progress = False
        app._update_prepare_in_progress = False
        release = self._release()
        app._available_release = release
        app._prepared_update = (
            release,
            "/tmp/source.app",
            "/tmp/target.app",
            "/tmp/update-root",
        )
        install_calls = []

        original_thread = __import__("threading").Thread

        class ImmediateThread:
            def __init__(self, target=None, args=(), daemon=None):
                self.target = target
                self.args = args

            def start(self):
                if self.target:
                    self.target(*self.args)

        import codex_monitor_app.ui as ui_module

        original_prepare_update = ui_module.prepare_update
        original_install_update = ui_module.install_update_and_restart
        ui_module.threading.Thread = ImmediateThread
        ui_module.prepare_update = lambda _release: self.fail("Should not redownload")
        ui_module.install_update_and_restart = (
            lambda source, target, temp_root: install_calls.append(
                (source, target, temp_root)
            )
        )
        try:
            app.update_application()
            _job_id, _delay, callback = app.root.after_calls[-1]
            callback()
        finally:
            ui_module.threading.Thread = original_thread
            ui_module.prepare_update = original_prepare_update
            ui_module.install_update_and_restart = original_install_update

        self.assertEqual(
            install_calls,
            [("/tmp/source.app", "/tmp/target.app", "/tmp/update-root")],
        )
        self.assertIsNone(app._prepared_update)
        self.assertIsNone(app._available_release)


if __name__ == "__main__":
    unittest.main()
