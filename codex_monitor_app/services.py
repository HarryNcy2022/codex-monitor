import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .config import AUTH_FILE_PATH, AUTO_FETCH_INTERVALS, AUTO_FETCH_OPTIONS
from .models import AuthFileSnapshot, UsageMap, UsageResponse
from .storage import UsageStorage


class MonitorStateService:
    def __init__(self, storage: UsageStorage):
        self.storage = storage
        self.usage_map: UsageMap = storage.load()
        self.session_tokens: Dict[str, str] = {}
        self.current_account_email: Optional[str] = self._restore_current_account_email()
        self.auto_fetch_interval: str = self._restore_auto_fetch_value()
        self.latest_auth_jwt: Optional[str] = None

    def save_data(self) -> None:
        try:
            self.storage.save(self.usage_map)
        except Exception as error:
            print(f"Failed to save data: {error}")

    def clear_session_credentials(self) -> bool:
        had_current_account = (
            self.current_account_email is not None
            or bool(self.session_tokens)
            or bool(self.latest_auth_jwt)
        )
        self.session_tokens.clear()
        self.current_account_email = None
        self.latest_auth_jwt = None
        self.storage.set_meta_value("current_account_email", None)
        self.save_data()
        return had_current_account

    def remember_auth_jwt(self, jwt: Optional[str]) -> None:
        self.latest_auth_jwt = jwt

    def set_current_account(self, email: str, jwt: str) -> None:
        self.current_account_email = email
        self.session_tokens = {email: jwt}
        self.latest_auth_jwt = jwt
        self.storage.set_meta_value("current_account_email", email)

    def get_display_auto_fetch(self, email: str) -> str:
        if email != self.current_account_email:
            return "-"
        return self.auto_fetch_interval

    def get_auto_fetch_value(self) -> str:
        return self.auto_fetch_interval

    def save_auto_fetch_value(self, email: str, new_value: str) -> bool:
        if email in self.usage_map and email == self.current_account_email:
            self.auto_fetch_interval = (
                new_value if new_value in AUTO_FETCH_OPTIONS else "None"
            )
            self.storage.set_meta_value("auto_fetch", self.auto_fetch_interval)
            self.save_data()
            return True
        return False

    def import_data(self, payload: object) -> None:
        self.usage_map = self.storage.import_data(payload)
        self.current_account_email = self._restore_current_account_email()
        self.auto_fetch_interval = self._restore_auto_fetch_value()
        self.session_tokens.clear()
        self.latest_auth_jwt = None

    def apply_usage_response(
        self, response_data: UsageResponse, jwt: str
    ) -> Optional[str]:
        email = response_data.get("email")
        rate_limit = response_data.get("rate_limit", {})
        primary_window = rate_limit.get("primary_window") if rate_limit else {}

        reset_at = primary_window.get("reset_at") if primary_window else None
        used_percent = primary_window.get("used_percent", 0) if primary_window else 0

        if not email or not reset_at:
            return None

        if email not in self.usage_map:
            self.usage_map[email] = {}

        self.usage_map[email].update(
            {
                "reset_ts": reset_at,
                "used_percent": used_percent,
                "last_fetched": time.time(),
            }
        )
        self.set_current_account(email, jwt)
        self.save_data()
        return email

    def get_due_auto_fetch_jwt(self, now: float) -> Optional[str]:
        current_email = self.current_account_email
        if not current_email:
            return None

        data = self.usage_map.get(current_email)
        if not data:
            return None

        interval_label = self.auto_fetch_interval
        if interval_label == "None":
            return None

        interval_seconds = AUTO_FETCH_INTERVALS.get(interval_label, 0)
        if interval_seconds <= 0:
            return None

        if (now - data.get("last_fetched", 0)) < interval_seconds:
            return None

        data["last_fetched"] = now
        return self.latest_auth_jwt or self.session_tokens.get(current_email)

    def get_latest_jwt_for_fetch(self, email: Optional[str] = None) -> Optional[str]:
        if self.latest_auth_jwt:
            return self.latest_auth_jwt
        if email:
            return self.session_tokens.get(email)
        current_email = self.current_account_email
        if not current_email:
            return None
        return self.session_tokens.get(current_email)

    def sorted_usage_items(self) -> List[Tuple[str, dict]]:
        return sorted(
            self.usage_map.items(),
            key=lambda item: (
                0 if item[0] == self.current_account_email else 1,
                item[1].get("reset_ts", 0),
            ),
        )

    def _restore_current_account_email(self) -> Optional[str]:
        saved_current_email = self.storage.get_meta_value("current_account_email")
        if saved_current_email in self.usage_map:
            return saved_current_email

        if len(self.usage_map) == 1:
            return next(iter(self.usage_map))

        return None

    def _restore_auto_fetch_value(self) -> str:
        saved_auto_fetch = self.storage.get_meta_value("auto_fetch")
        if isinstance(saved_auto_fetch, str) and saved_auto_fetch in AUTO_FETCH_OPTIONS:
            return saved_auto_fetch
        return "None"


class AuthFileService:
    def __init__(self, auth_file_path: str = AUTH_FILE_PATH):
        self.auth_file_path = auth_file_path

    def auth_file_exists(self) -> bool:
        return os.path.exists(self.auth_file_path)

    def load_snapshot(self) -> AuthFileSnapshot:
        with open(self.auth_file_path, "r", encoding="utf-8") as file:
            auth_data = json.load(file)
        if not isinstance(auth_data, dict):
            raise ValueError("auth.json root must be an object")
        return auth_data

    def load_access_token(self) -> Optional[str]:
        return self.load_snapshot().get("tokens", {}).get("access_token")
