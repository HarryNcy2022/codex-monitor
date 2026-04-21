import json
import os

from .config import LOCAL_STORAGE_FILE
from .models import AccountUsage, UsageMap


class UsageStorage:
    def __init__(self, storage_path: str = LOCAL_STORAGE_FILE):
        self.storage_path = storage_path

    def load(self) -> UsageMap:
        data: UsageMap = {}
        needs_resave = False

        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as file:
                    raw = json.load(file)
            except Exception:
                raw = {}

            for email, value in raw.items():
                if isinstance(value, (int, float)):
                    data[email] = {
                        "reset_ts": value,
                        "used_percent": 0,
                        "auto_fetch": "None",
                        "last_fetched": 0,
                    }
                    needs_resave = True
                    continue

                sanitized = self._sanitize_account_data(value)
                if "jwt" in value:
                    needs_resave = True
                data[email] = sanitized

        if needs_resave:
            self.save(data)

        return data

    def save(self, data: UsageMap) -> None:
        with open(self.storage_path, "w", encoding="utf-8") as file:
            json.dump(self._sanitize_usage_map(data), file)

    def _sanitize_usage_map(self, data: UsageMap) -> UsageMap:
        return {
            email: self._sanitize_account_data(account_data)
            for email, account_data in data.items()
        }

    def _sanitize_account_data(self, account_data: dict) -> AccountUsage:
        clean_account = dict(account_data)
        clean_account.pop("jwt", None)
        return clean_account
