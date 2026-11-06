"""Deterministic date and cost calculations; no budget or demo catalog."""
from datetime import date
from decimal import Decimal
from .models import CostCategoryEstimate, CostEstimate, TripPlan

def count_nights(start_date: str, end_date: str) -> dict:
    try:
        nights = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
    except ValueError:
        return {"error": "日期必须为 YYYY-MM-DD"}
    return {"nights": nights} if nights >= 0 else {"error": "结束日期不能早于开始日期"}

def estimate_cost(plan: TripPlan) -> CostEstimate:
    categories, missing = [], []
    groups = {
        "food": [(None, None) for d in plan.days for _ in (d.breakfast, d.lunch, d.dinner)],
        "activities": [(a.ticket_price, a.currency) for d in plan.days for a in d.activities],
        "hotel": [(d.hotel.price_per_night, d.hotel.currency) if d.hotel else (None, None)
                  for i, d in enumerate(plan.days) if d.hotel is not None or i < len(plan.days) - 1],
        "local_transport": [(None, None)],
    }
    for category, items in groups.items():
        known = [(v, c) for v, c in items if v is not None and c]
        currencies = {c for _, c in known}
        complete = bool(items) and len(known) == len(items) and len(currencies) == 1
        if not complete:
            missing.append(f"{category}：价格、安排或统一币种信息不足")
        for currency in sorted(currencies):
            total = float(sum((Decimal(str(v)) for v, c in known if c == currency), Decimal(0)))
            categories.append(CostCategoryEstimate(category=category, low=total, high=total,
                currency=currency, basis="当前条目中已提供的参考金额，非实时报价",
                coverage_note="已知项目小计，不包括未安排或缺价项目"))
        if not known:
            categories.append(CostCategoryEstimate(category=category,
                basis="地点价格等级不等于金额，未推断价格", coverage_note="没有可信价格，不能按 0 计算"))
    currencies = {c.currency for c in categories if c.low is not None}
    complete = not missing and len(currencies) == 1
    total = float(sum((Decimal(str(c.low)) for c in categories if c.low is not None), Decimal(0))) if complete else None
    return CostEstimate(low=total, high=total, currency=next(iter(currencies)) if complete else None,
        is_complete=complete, categories=categories, missing_information=missing,
        note="总费用暂无法估算（价格信息不足）；类别金额仅为已知项目小计。机票不包含在内。" if not complete else "不含机票的参考费用。")
