"""Local-clock scheduling and edit reconciliation; no model or transport calls.

Timeline is authoritative. Activity.planned_* are compatibility projections.
"""
from datetime import time
from uuid import NAMESPACE_URL, uuid5
from .models import DailyPlan, TimelineItem
from .config import LEGACY_TIMELINE_NAMESPACE

MEALS = {"breakfast": (8 * 60, 9 * 60), "lunch": (12 * 60, 13 * 60), "dinner": (18 * 60, 19 * 60)}
LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}

def timeline_id(day, kind, key):
    # Branding must not change persisted edit targets or legacy migration IDs.
    return str(uuid5(NAMESPACE_URL, f"{LEGACY_TIMELINE_NAMESPACE}:{day.day_id}:{kind}:{key}"))

def minute(value):
    return value.hour * 60 + value.minute if value is not None else None

def clock(value):
    return time(value // 60, value % 60)

def schedule_day(day: DailyPlan):
    """Initial suggested slots, using activity times/durations and meal windows."""
    day.timeline = []
    reserved = list(MEALS.values())
    cursor = 9 * 60 + 30
    for activity in day.activities:
        start, end = minute(activity.planned_start_time), minute(activity.planned_end_time)
        duration = activity.estimated_duration_minutes
        if duration is None:
            duration = end - start if start is not None and end is not None and end > start else 120
        duration = max(duration, 1)
        start = max(cursor, start if start is not None else cursor)
        while True:
            conflict = next(((a, b) for a, b in reserved if start < b and start + duration > a), None)
            if conflict is None:
                break
            start = conflict[1]
        scheduled = start + duration < 24 * 60
        day.timeline.append(TimelineItem(id=timeline_id(day, "activity", activity.id),
            item_type="activity", reference_id=activity.id, title=activity.name,
            start_time=clock(start) if scheduled else None,
            end_time=clock(start + duration) if scheduled else None,
            notes="建议当地时间，未计算交通或验证开放时间" if scheduled else "当天时间不足，时间待安排"))
        if scheduled:
            reserved.append((start, start + duration))
            cursor = start + duration
    sync_day(day, new_meal_times=True)

def sync_day(day: DailyPlan, *, new_meal_times=False):
    """Preserve manual times, remove stale references, add new slots, sort and validate."""
    old = list(day.timeline)
    by_ref = {t.reference_id: t for t in old if t.reference_id}
    items = []
    for meal, (start, end) in MEALS.items():
        place = getattr(day, meal)
        item = next((t for t in old if t.item_type == "meal" and t.meal_type == meal), None)
        if item is None:
            item = TimelineItem(id=timeline_id(day, "meal", meal), item_type="meal", meal_type=meal, title=LABELS[meal],
                start_time=clock(start) if new_meal_times else None,
                end_time=clock(end) if new_meal_times else None)
        item.reference_id = place.id if place else None
        item.title = place.name if place else f"{LABELS[meal]} · 尚未安排"
        items.append(item)
    for activity in day.activities:
        item = by_ref.get(activity.id) or TimelineItem(id=timeline_id(day, "activity", activity.id), item_type="activity", reference_id=activity.id,
            title=activity.name, notes="时间待安排")
        item.title = activity.name
        items.append(item)
    if day.hotel:
        item = next((t for t in old if t.item_type == "hotel"), None)
        if item is None:
            last_end = max((minute(t.end_time or t.start_time) or 0 for t in items), default=0)
            item = TimelineItem(id=timeline_id(day, "hotel", "night"), item_type="hotel", title=day.hotel.name,
                start_time=clock(max(20 * 60, last_end)) if last_end < 24 * 60 else None)
        item.reference_id = day.hotel.id
        item.title = day.hotel.name
        items.append(item)
    items.extend(t for t in old if t.item_type == "custom" and t.reference_id is None)
    day.timeline = sorted(items, key=lambda t: (t.start_time is None, minute(t.start_time) or 0))
    for activity in day.activities:
        item = next(t for t in items if t.reference_id == activity.id)
        activity.planned_start_time, activity.planned_end_time = item.start_time, item.end_time
    day.timeline_warnings = check_conflicts(day)

def check_conflicts(day):
    warnings, ranges = [], []
    for item in day.timeline:
        start, end = minute(item.start_time), minute(item.end_time)
        if start is None:
            warnings.append(f"{item.title}：时间待安排")
            continue
        if end is not None and end <= start:
            warnings.append(f"{item.title}：开始时间必须早于结束时间")
        if item.item_type == "meal" and item.reference_id:
            limits = {"breakfast": (5 * 60, 11 * 60), "lunch": (11 * 60, 15 * 60), "dinner": (16 * 60, 23 * 60)}
            low, high = limits[item.meal_type]
            if not low <= start < high:
                warnings.append(f"{item.title}：不在通常{LABELS[item.meal_type]}时段内")
        if not item.reference_id and item.item_type != "custom":
            continue
        effective_end = end if end is not None else start + 1
        for other_start, other_end, title in ranges:
            if start < other_end and effective_end > other_start:
                warnings.append(f"{title} 与 {item.title} 时间重叠")
        ranges.append((start, effective_end, item.title))
    return warnings

def migrate_plan(plan):
    for day in plan.days:
        if not day.timeline:
            schedule_day(day)
        else:
            sync_day(day)
    return plan

def opening_for_date(place, day_date):
    """Only known source periods; missing weekday is unknown, never assumed closed."""
    weekday = day_date.isoweekday()
    previous = (weekday - 2) % 7 + 1
    result = []
    for hours in getattr(place, "opening_hours", []):
        if hours.day_of_week == weekday:
            suffix = "（次日）" if hours.close_time <= hours.open_time else ""
            result.append(f"{hours.open_time:%H:%M}–{hours.close_time:%H:%M}{suffix}")
        elif hours.day_of_week == previous and hours.close_time <= hours.open_time:
            result.append(f"00:00–{hours.close_time:%H:%M}（前日延续）")
    return result
