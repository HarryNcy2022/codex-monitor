import json
import os
from typing import Any, Dict, Optional

from .config import LOCAL_STORAGE_FILE, LOCAL_STORAGE_META_FILE
from .models import AccountUsage, UsageMap


class UsageStorage:
    ACCOUNTS_KEY = "accounts"
    META_KEY = "__meta__"

    def __init__(
        self,
        storage_path: str = LOCAL_STORAGE_FILE,
        meta_path: str = LOCAL_STORAGE_META_FILE,
    ):
        self.storage_path = storage_path
        self.meta_path = meta_path
        self.meta: Dict[str, Any] = {}

    def load(self) -> UsageMap:
        data: UsageMap = {}
        needs_resave = False
        needs_meta_save = False
        self.meta = self._load_meta_file()

        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as file:
                    raw = json.load(file)
            except Exception:
                raw = {}

            if isinstance(raw, dict) and isinstance(raw.get(self.ACCOUNTS_KEY), dict):
                raw_accounts = dict(raw.get(self.ACCOUNTS_KEY, {}))
                legacy_meta = self._sanitize_meta(raw.get(self.META_KEY, {}))
                if legacy_meta:
                    self.meta.update(legacy_meta)
                    needs_meta_save = True

                for key, value in raw.items():
                    if key in (self.ACCOUNTS_KEY, self.META_KEY):
                        continue
                    if self._looks_like_account_payload(value):
                        raw_accounts[key] = self._merge_account_payloads(
                            raw_accounts.get(key),
                            value,
                        )

                needs_resave = True
            elif isinstance(raw, dict):
                raw_accounts = raw
            else:
                raw_accounts = {}

            for email, value in raw_accounts.items():
                if email in (self.ACCOUNTS_KEY, self.META_KEY):
                    needs_resave = True
                    continue

                if isinstance(value, (int, float)):
                    data[email] = {
                        "reset_ts": value,
                        "used_percent": 0,
                        "auto_fetch": "None",
                        "last_fetched": 0,
                    }
                    needs_resave = True
                    continue

                if not self._looks_like_account_payload(value):
                    needs_resave = True
                    continue

                sanitized = self._sanitize_account_data(value)
                if "jwt" in value:
                    needs_resave = True
                data[email] = sanitized

        if needs_resave:
            self.save(data)
        elif needs_meta_save:
            self._save_meta_file()

        return data

    def save(self, data: UsageMap) -> None:
        with open(self.storage_path, "w", encoding="utf-8") as file:
            json.dump(self._sanitize_usage_map(data), file)
        self._save_meta_file()

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

    def _load_meta_file(self) -> Dict[str, str]:
        if not os.path.exists(self.meta_path):
            return {}

        try:
            with open(self.meta_path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except Exception:
            return {}

        return self._sanitize_meta(raw)

    def _save_meta_file(self) -> None:
        with open(self.meta_path, "w", encoding="utf-8") as file:
            json.dump(self._sanitize_meta(self.meta), file)

    def _looks_like_account_payload(self, value: object) -> bool:
        if not isinstance(value, dict):
            return False

        known_fields = {"reset_ts", "used_percent", "auto_fetch", "last_fetched", "jwt"}
        return any(field in value for field in known_fields)

    def _merge_account_payloads(
        self,
        existing_value: Optional[dict],
        new_value: object,
    ) -> dict:
        if not isinstance(existing_value, dict):
            existing_value = {}
        if not isinstance(new_value, dict):
            return dict(existing_value)

        merged = dict(existing_value)

        existing_last_fetched = existing_value.get("last_fetched", 0) or 0
        new_last_fetched = new_value.get("last_fetched", 0) or 0
        prefer_newer_usage = new_last_fetched >= existing_last_fetched

        if prefer_newer_usage:
            for field in ("reset_ts", "used_percent", "last_fetched"):
                if field in new_value:
                    merged[field] = new_value[field]

        new_auto_fetch = new_value.get("auto_fetch")
        existing_auto_fetch = existing_value.get("auto_fetch")
        if new_auto_fetch not in (None, "", "None"):
            merged["auto_fetch"] = new_auto_fetch
        elif existing_auto_fetch not in (None, ""):
            merged["auto_fetch"] = existing_auto_fetch

        if "jwt" in new_value:
            merged["jwt"] = new_value["jwt"]

        return merged
