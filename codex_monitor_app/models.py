from typing import Any, Dict, TypedDict


class AccountUsage(TypedDict, total=False):
    reset_ts: float
    used_percent: float
    auto_fetch: str
    last_fetched: float


UsageMap = Dict[str, AccountUsage]
UsageResponse = Dict[str, Any]
