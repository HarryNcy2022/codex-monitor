import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .config import AUTH_FILE_PATH, AUTO_FETCH_INTERVALS, AUTO_FETCH_OPTIONS
from .models import AuthFileSnapshot, RateLimitWindow, UsageMap, UsageResponse
from .storage import UsageStorage


class MonitorStateService:
    def __init__(self, storage: UsageStorage):
        self.storage = storage
        self.usage_map: UsageMap = storage.load()
        self.session_tokens: Dict[str, str] = {}
        self.current_account_email: Optional[str] = self._restore_current_account_email()
        self.auto_fetch_interval: str = self._restore_auto_fetch_value()
        self.latest_auth_jwt: Optional[str] = None
        self.sort_column: Optional[str] = self.storage.get_meta_value("sort_column")
        sort_asc_raw = self.storage.get_meta_value("sort_asc")
        self.sort_asc: bool = True if sort_asc_raw is None else bool(sort_asc_raw)
        self.show_archived: bool = bool(self.storage.get_meta_value("show_archived"))
        show_5h_columns_raw = self.storage.get_meta_value("show_5h_columns")
        self.show_5h_columns: bool = (
            True if show_5h_columns_raw is None else bool(show_5h_columns_raw)
        )
        self.logs_expanded: bool = bool(self.storage.get_meta_value("logs_expanded"))

    def save_sort_preference(self, column: Optional[str], asc: bool) -> None:
        self.sort_column = column
        self.sort_asc = asc
        self.storage.set_meta_value("sort_column", column)
        self.storage.set_meta_value("sort_asc", asc)
        self.save_data()

    def save_show_archived_preference(self, show_archived: bool) -> None:
        self.show_archived = show_archived
        self.storage.set_meta_value("show_archived", show_archived)
        self.save_data()

    def save_show_5h_columns_preference(self, show_5h_columns: bool) -> None:
        self.show_5h_columns = show_5h_columns
        self.storage.set_meta_value("show_5h_columns", show_5h_columns)
        self.save_data()

    def save_logs_expanded_preference(self, logs_expanded: bool) -> None:
        self.logs_expanded = logs_expanded
        self.storage.set_meta_value("logs_expanded", logs_expanded)
        self.save_data()

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

    def remove_account(self, email: str) -> bool:
        if email not in self.usage_map:
            return False

        self.usage_map.pop(email, None)
        self.session_tokens.pop(email, None)
        if email == self.current_account_email:
            self.current_account_email = None
            self.latest_auth_jwt = None
            self.storage.set_meta_value("current_account_email", None)

        self.save_data()
        return True

    def archive_account(self, email: str) -> bool:
        if email not in self.usage_map:
            return False

        self.usage_map[email]["archived"] = True
        self.save_data()
        return True

    def unarchive_account(self, email: str) -> bool:
        if email not in self.usage_map:
            return False

        self.usage_map[email].pop("archived", None)
        self.save_data()
        return True

    def import_data(self, payload: object) -> None:
        self.usage_map = self.storage.import_data(payload)
        self.current_account_email = self._restore_current_account_email()
        self.auto_fetch_interval = self._restore_auto_fetch_value()
        self.show_archived = bool(self.storage.get_meta_value("show_archived"))
        show_5h_columns_raw = self.storage.get_meta_value("show_5h_columns")
        self.show_5h_columns = (
            True if show_5h_columns_raw is None else bool(show_5h_columns_raw)
        )
        self.logs_expanded = bool(self.storage.get_meta_value("logs_expanded"))
        self.session_tokens.clear()
        self.latest_auth_jwt = None

    def apply_usage_response(
        self, response_data: UsageResponse, jwt: str
    ) -> Optional[str]:
        email = response_data.get("email")
        rate_limit = response_data.get("rate_limit", {})
        primary_window = self._sanitize_rate_limit_window(
            rate_limit.get("primary_window") if rate_limit else None
        )
        secondary_window = self._sanitize_rate_limit_window(
            rate_limit.get("secondary_window") if rate_limit else None
        )
        short_window, weekly_window = self._classify_rate_limit_windows(
            primary_window,
            secondary_window,
        )

        reset_at = weekly_window.get("reset_at") or primary_window.get("reset_at")
        used_percent = weekly_window.get("used_percent", primary_window.get("used_percent", 0))

        if not email or not (primary_window or secondary_window):
            return None

        if email not in self.usage_map:
            self.usage_map[email] = {}

        next_usage = {
            "reset_ts": reset_at or 0,
            "used_percent": used_percent,
            "last_fetched": time.time(),
        }
        if primary_window:
            next_usage["primary_window"] = primary_window
        if secondary_window:
            next_usage["secondary_window"] = secondary_window
        if short_window:
            next_usage["short_window"] = short_window
        if weekly_window:
            next_usage["weekly_window"] = weekly_window

        self.usage_map[email].update(next_usage)
        self.set_current_account(email, jwt)
        self.save_data()
        return email

    def _sanitize_rate_limit_window(self, value: object) -> RateLimitWindow:
        if not isinstance(value, dict):
            return {}

        clean_window: RateLimitWindow = {}
        for field in ("used_percent", "limit_window_seconds", "reset_after_seconds", "reset_at"):
            raw_value = value.get(field)
            if raw_value in (None, ""):
                continue
            if isinstance(raw_value, bool):
                continue
            if isinstance(raw_value, (int, float)):
                clean_window[field] = raw_value

        if not clean_window.get("reset_at"):
            return {}
        return clean_window

    def _classify_rate_limit_windows(
        self,
        primary_window: RateLimitWindow,
        secondary_window: RateLimitWindow,
    ) -> Tuple[RateLimitWindow, RateLimitWindow]:
        windows = [window for window in (primary_window, secondary_window) if window]
        if not windows:
            return {}, {}

        weekly_candidates = [
            window
            for window in windows
            if (window.get("limit_window_seconds") or 0) >= 6 * 24 * 60 * 60
        ]
        weekly_window = weekly_candidates[0] if weekly_candidates else max(
            windows,
            key=lambda window: window.get("limit_window_seconds") or 0,
        )

        short_candidates = [window for window in windows if window is not weekly_window]
        short_window = short_candidates[0] if short_candidates else {}
        return short_window, weekly_window

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
                self._account_weekly_reset_ts(item[1]),
            ),
        )

    def _account_weekly_reset_ts(self, account_data: dict) -> float:
        weekly_window = account_data.get("weekly_window")
        if isinstance(weekly_window, dict):
            reset_at = weekly_window.get("reset_at")
            if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool):
                return reset_at
        return account_data.get("reset_ts", 0) or 0

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
