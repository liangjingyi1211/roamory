"""Local-clock scheduling and edit reconciliation; no model or transport calls.

Timeline is authoritative. Activity.planned_* are compatibility projections.
"""
from datetime import time
from uuid import NAMESPACE_URL, uuid5
from .models import DailyPlan, TimelineItem
from .config import LEGACY_TIMELINE_NAMESPACE

MEALS = {"breakfast": (8 * 60, 9 * 60), "lunch": (12 * 60, 13 * 60), "dinner": (18 * 60, 19 * 60)}
LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
MEAL_LIMITS = {"breakfast": (5 * 60, 11 * 60), "lunch": (11 * 60, 15 * 60), "dinner": (16 * 60, 23 * 60)}

def timeline_id(day, kind, key):
    # Branding must not change persisted edit targets or legacy migration IDs.
    return str(uuid5(NAMESPACE_URL, f"{LEGACY_TIMELINE_NAMESPACE}:{day.day_id}:{kind}:{key}"))

def minute(value):
    return value.hour * 60 + value.minute if value is not None else None

def clock(value):
    return time(value // 60, value % 60)

def schedule_day(day: DailyPlan, *, arrange_meals=True):
    """Keep suggested activity periods; reconcile only conflicting meal slots."""
    day.timeline = []
    cursor = 9 * 60 + 30
    for activity in day.activities:
        start, end = minute(activity.planned_start_time), minute(activity.planned_end_time)
        duration = activity.estimated_duration_minutes
        if duration is None:
            duration = end - start if start is not None and end is not None and end > start else 120
        duration = max(duration, 1)
        start = start if start is not None else cursor
        # Explicit start/end suggestions take precedence over a duration estimate.
        end = end if end is not None and end > start else start + duration
        scheduled = end < 24 * 60
        day.timeline.append(TimelineItem(id=timeline_id(day, "activity", activity.id),
            item_type="activity", reference_id=activity.id, title=activity.name,
            start_time=clock(start) if scheduled else None,
            end_time=clock(end) if scheduled else None,
            notes="建议当地时间，未计算交通或验证开放时间" if scheduled else "当天时间不足，时间待安排"))
        if scheduled:
            cursor = max(cursor, end)
    sync_day(day, new_meal_times=True)
    if arrange_meals:
        schedule_meals(day)
    hotel = next((t for t in day.timeline if t.item_type == "hotel"), None)
    if hotel:
        last_end = max((minute(t.end_time or t.start_time) or 0 for t in day.timeline if t is not hotel), default=0)
        hotel.start_time = clock(max(20 * 60, last_end))
        sync_day(day)


def activity_periods(day):
    """Use authoritative timeline times, with suggestions before initial scheduling."""
    by_ref = {t.reference_id: t for t in day.timeline if t.item_type == "activity"}
    result = []
    for activity in day.activities:
        item = by_ref.get(activity.id)
        start = minute(item.start_time if item else activity.planned_start_time)
        end = minute(item.end_time if item else activity.planned_end_time)
        if start is not None and end is not None and start < end:
            result.append((activity, start, end))
    return result


def conflicting_activities(day, meal):
    start, end = MEALS[meal]
    return [a for a, low, high in activity_periods(day) if start < high and end > low]


def open_during(restaurant, day_date, start, end):
    """Require positive source hours for the whole slot; unknown is not open."""
    weekday = day_date.isoweekday()
    previous = (weekday - 2) % 7 + 1
    intervals = []
    for hours in restaurant.opening_hours:
        low, high = minute(hours.open_time), minute(hours.close_time)
        if hours.day_of_week == weekday:
            intervals.append((low, high if high > low else high + 24 * 60))
        if hours.day_of_week == previous and high <= low:
            intervals.append((0, high))
    # Adjacent source periods can jointly cover a meal without a closure.
    covered = start
    for low, high in sorted(intervals):
        if low <= covered < high:
            covered = high
        if covered >= end:
            return True
    return False


def meal_slot(day, meal, restaurant):
    """Find a full meal inside a verified parent or outside all activity periods."""
    default_start, default_end = MEALS[meal]
    conflicts = conflicting_activities(day, meal)
    if not conflicts:
        return default_start, default_end
    if restaurant is None:
        return None
    periods = activity_periods(day)
    parents = [a for a in conflicts if a.place_id and a.place_id == restaurant.containing_place_id]
    if restaurant.containing_place_id and not parents:
        return None
    duration = default_end - default_start
    low, high = MEAL_LIMITS[meal]
    starts = sorted(range(low, high - duration + 1), key=lambda s: (abs(s - default_start), s < default_start))
    for start in starts:
        end = start + duration
        if not open_during(restaurant, day.date, start, end):
            continue
        parent = next((a for a, a_start, a_end in periods if a in parents and a_start <= start and end <= a_end), None)
        if restaurant.containing_place_id and parent is None:
            continue
        if any(start < a_end and end > a_start for a, a_start, a_end in periods if a is not parent):
            continue
        # Respect other manually placed meals and custom events as well.
        if any(t.item_type in {"meal", "custom"} and t.meal_type != meal and t.start_time is not None
               and start < (minute(t.end_time) if t.end_time else minute(t.start_time) + 1)
               and end > minute(t.start_time) for t in day.timeline):
            continue
        return start, end
    return None


def schedule_meals(day, meals=MEALS):
    """Automatic generation/AI edits only; manual sync never changes times."""
    for meal in meals:
        conflicts = conflicting_activities(day, meal)
        if not conflicts:
            continue
        item = next(t for t in day.timeline if t.meal_type == meal)
        restaurant = getattr(day, meal)
        slot = meal_slot(day, meal, restaurant)
        item.start_time, item.end_time = (clock(slot[0]), clock(slot[1])) if slot else (None, None)
        if slot is None:
            item.notes = (f"{LABELS[meal]}默认时段与{'、'.join(a.name for a in conflicts)}冲突；"
                "未找到可核实营业的内部用餐或合理的外部错峰安排，景点时间已保留。")
        elif restaurant.containing_place_id:
            parent = next(a for a in conflicts if a.place_id == restaurant.containing_place_id)
            item.notes = f"已核实位于{parent.name}内部，在游玩期间用餐，不额外增加游玩时长。"
        else:
            item.notes = "景点时间优先，已调整为外部错峰用餐；未计算交通时间。"
    sync_day(day)

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
        if item.reference_id != (place.id if place else None):
            item.notes = None
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
    for item in items:
        if item.item_type == "meal" and (item.notes or "").startswith("已核实位于"):
            if not any(internal_meal_overlap(day, item, other) for other in items if other.item_type == "activity"):
                item.notes = None
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
            low, high = MEAL_LIMITS[item.meal_type]
            if not low <= start < high:
                warnings.append(f"{item.title}：不在通常{LABELS[item.meal_type]}时段内")
        if not item.reference_id and item.item_type != "custom":
            continue
        effective_end = end if end is not None else start + 1
        for other_start, other_end, other in ranges:
            if start < other_end and effective_end > other_start and not internal_meal_overlap(day, item, other):
                warnings.append(f"{other.title} 与 {item.title} 时间重叠")
        ranges.append((start, effective_end, item))
    return warnings


def internal_meal_overlap(day, first, second):
    meal, activity = (first, second) if first.item_type == "meal" else (second, first)
    if meal.item_type != "meal" or activity.item_type != "activity":
        return False
    restaurant = getattr(day, meal.meal_type)
    parent = next((a for a in day.activities if a.id == activity.reference_id), None)
    return bool(restaurant and restaurant.place_id and restaurant.id == meal.reference_id and parent
        and parent.place_id and restaurant.containing_place_id == parent.place_id
        and meal.start_time is not None and meal.end_time is not None
        and activity.start_time is not None and activity.end_time is not None
        and activity.start_time <= meal.start_time < meal.end_time <= activity.end_time)

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
