import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .config import AUTH_FILE_PATH, AUTO_FETCH_INTERVALS
from .models import UsageMap, UsageResponse
from .storage import UsageStorage


class MonitorStateService:
    def __init__(self, storage: UsageStorage):
        self.storage = storage
        self.usage_map: UsageMap = storage.load()
        self.session_tokens: Dict[str, str] = {}
        self.current_account_email: Optional[str] = None

    def save_data(self) -> None:
        try:
            self.storage.save(self.usage_map)
        except Exception as error:
            print(f"Failed to save data: {error}")

    def clear_session_credentials(self) -> bool:
        had_current_account = (
            self.current_account_email is not None or bool(self.session_tokens)
        )
        self.session_tokens.clear()
        self.current_account_email = None
        return had_current_account

    def set_current_account(self, email: str, jwt: str) -> None:
        self.current_account_email = email
        self.session_tokens = {email: jwt}

        changed = False
        for account_email, data in self.usage_map.items():
            if account_email != email and data.get("auto_fetch", "None") != "None":
                data["auto_fetch"] = "None"
                changed = True

        if changed:
            self.save_data()

    def get_display_auto_fetch(self, email: str) -> str:
        if email != self.current_account_email:
            return "-"
        return self.usage_map[email].get("auto_fetch", "None")

    def save_auto_fetch_value(self, email: str, new_value: str) -> bool:
        if email in self.usage_map and email == self.current_account_email:
            self.usage_map[email]["auto_fetch"] = new_value
            self.save_data()
            return True
        return False

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
        if "auto_fetch" not in self.usage_map[email]:
            self.usage_map[email]["auto_fetch"] = "None"

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

        interval_label = data.get("auto_fetch", "None")
        if interval_label == "None":
            return None

        interval_seconds = AUTO_FETCH_INTERVALS.get(interval_label, 0)
        if interval_seconds <= 0:
            return None

        if (now - data.get("last_fetched", 0)) < interval_seconds:
            return None

        data["last_fetched"] = now
        return self.session_tokens.get(current_email)

    def sorted_usage_items(self) -> List[Tuple[str, dict]]:
        return sorted(
            self.usage_map.items(),
            key=lambda item: item[1].get("reset_ts", 0),
        )


class AuthFileService:
    def __init__(self, auth_file_path: str = AUTH_FILE_PATH):
        self.auth_file_path = auth_file_path

    def auth_file_exists(self) -> bool:
        return os.path.exists(self.auth_file_path)

    def load_access_token(self) -> Optional[str]:
        with open(self.auth_file_path, "r", encoding="utf-8") as file:
            auth_data = json.load(file)
        return auth_data.get("tokens", {}).get("access_token")
