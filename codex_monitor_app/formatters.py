from datetime import datetime
from typing import List, Optional


def format_reset_display(reset_ts: Optional[float], now_ts: float) -> str:
    if not reset_ts:
        return "-"

    reset_time_str = datetime.fromtimestamp(reset_ts).strftime("%Y-%m-%d %H:%M")

    if now_ts >= reset_ts:
        countdown_text = "0m"
    else:
        diff = int(reset_ts - now_ts)
        days = diff // 86400
        hours = (diff % 86400) // 3600
        minutes = (diff % 3600) // 60

        time_parts: List[str] = []
        if days > 0:
            time_parts.append(f"{days}d")
        if hours > 0:
            time_parts.append(f"{hours}h")
        time_parts.append(f"{minutes}m")
        countdown_text = " ".join(time_parts)

    return f"{reset_time_str} ({countdown_text})"


def format_quota_left(used_percent: float) -> str:
    return f"{100 - used_percent}%"
