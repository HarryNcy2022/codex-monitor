from typing import Any, Dict, Optional, TypedDict


class AccountUsage(TypedDict, total=False):
    reset_ts: float
    used_percent: float
    last_fetched: float


class AuthTokens(TypedDict, total=False):
    id_token: Optional[str]
    access_token: Optional[str]
    refresh_token: Optional[str]
    account_id: Optional[str]


class AuthFileSnapshot(TypedDict, total=False):
    auth_mode: Optional[str]
    OPENAI_API_KEY: Optional[str]
    tokens: AuthTokens
    last_refresh: Optional[str]


UsageMap = Dict[str, AccountUsage]
UsageResponse = Dict[str, Any]
