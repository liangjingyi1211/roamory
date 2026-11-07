"""Deterministic date calculations; no budget or demo catalog."""
from datetime import date

def count_nights(start_date: str, end_date: str) -> dict:
    try:
        nights = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
    except ValueError:
        return {"error": "日期必须为 YYYY-MM-DD"}
    return {"nights": nights} if nights >= 0 else {"error": "结束日期不能早于开始日期"}
