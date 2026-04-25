import json
import os
from typing import Any, Dict, Optional

from .config import LOCAL_STORAGE_FILE
from .models import AccountUsage, UsageMap


class UsageStorage:
    ACCOUNTS_KEY = "accounts"
    META_KEY = "__meta__"

    def __init__(self, storage_path: str = LOCAL_STORAGE_FILE):
        self.storage_path = storage_path
        self.meta: Dict[str, Any] = {}

    def load(self) -> UsageMap:
        data: UsageMap = {}
        needs_resave = False
        self.meta = {}

        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as file:
                    raw = json.load(file)
            except Exception:
                raw = {}

            if isinstance(raw, dict) and isinstance(raw.get(self.ACCOUNTS_KEY), dict):
                raw_accounts = raw.get(self.ACCOUNTS_KEY, {})
                self.meta = self._sanitize_meta(raw.get(self.META_KEY, {}))
            elif isinstance(raw, dict):
                raw_accounts = raw
            else:
                raw_accounts = {}

            for email, value in raw_accounts.items():
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
        payload = {
            self.ACCOUNTS_KEY: self._sanitize_usage_map(data),
            self.META_KEY: self._sanitize_meta(self.meta),
        }
        with open(self.storage_path, "w", encoding="utf-8") as file:
            json.dump(payload, file)

    def get_meta_value(self, key: str) -> Optional[Any]:
        return self.meta.get(key)

    def set_meta_value(self, key: str, value: Optional[Any]) -> None:
        if value in (None, ""):
            self.meta.pop(key, None)
            return
        self.meta[key] = value

    def _sanitize_usage_map(self, data: UsageMap) -> UsageMap:
        return {
            email: self._sanitize_account_data(account_data)
            for email, account_data in data.items()
        }

    def _sanitize_meta(self, meta: dict) -> Dict[str, str]:
        if not isinstance(meta, dict):
            return {}

        current_account_email = meta.get("current_account_email")
        if isinstance(current_account_email, str) and current_account_email:
            return {"current_account_email": current_account_email}
        return {}

    def _sanitize_account_data(self, account_data: dict) -> AccountUsage:
        clean_account = dict(account_data)
        clean_account.pop("jwt", None)
        return clean_account
