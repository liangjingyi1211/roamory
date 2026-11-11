"""V8 activity priority, sourced containment, fallback and edit regressions."""
import asyncio
import json
from datetime import time

import pytest
from google.adk.agents import LlmAgent
from google.adk.events import Event, EventActions
from google.genai import types

from roamory import agent
from roamory.models import Activity, Hotel, Restaurant, TripPlan, TripRequest, initialize_plan
from roamory.places import PlacesClient, PlacesError, opening_hours
from roamory.service import generate_plan
from roamory.storage import TripStore
from roamory.timeline import check_conflicts, meal_slot, open_during, schedule_day, sync_day
from roamory.ui import workspace_payload


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(agent, "audit", lambda *a, **kw: None)
    monkeypatch.setattr("roamory.places.audit", lambda *a, **kw: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not call live Google APIs")
    monkeypatch.setattr(PlacesClient, "_request", forbidden)


def make_plan(start="11:00", end="14:00"):
    plan = initialize_plan(TripRequest(destination="Los Angeles, US", start_date="2026-11-11", end_date="2026-11-11"))
    day = plan.days[0]
    day.activities = [Activity(name="Park", place_id="park", latitude=34.1, longitude=-118.3,
        planned_start_time=start, planned_end_time=end)]
    schedule_day(day)
    return plan


def row(key="restaurant", parent=None, hours=(8, 22)):
    data = {"id": key, "displayName": {"text": key}, "types": ["restaurant"],
        "location": {"latitude": 34.1, "longitude": -118.3},
        "addressComponents": [{"types": ["country"], "shortText": "US"}]}
    if parent:
        data["containingPlaces"] = [{"id": parent}]
    if hours:
        data["regularOpeningHours"] = {"periods": [{"open": {"day": 3, "hour": hours[0]},
            "close": {"day": 3, "hour": hours[1]}}]}
    return data


def slot(day, meal="lunch"):
    return next(t for t in day.timeline if t.meal_type == meal)


def select_lunch(plan, candidates):
    agent.merge_recommendations(plan, "food", agent.FoodOutput(days=[{
        "day_index": 1, "lunch": candidates["1"]["lunch"][0]["id"]}]), candidates)
    schedule_day(plan.days[0])


def test_nonconflicting_search_and_schedule_are_unchanged(monkeypatch):
    plan = make_plan("09:30", "11:30")
    day = plan.days[0]
    day.activities.append(Activity(name="Afternoon", latitude=34.12, longitude=-118.3,
        planned_start_time="14:00", planned_end_time="16:00"))
    schedule_day(day)
    calls = []
    def nearby(destination, kind, lat, lon, radius):
        calls.append((kind, lat, lon, radius))
        return [row(hours=None)]  # Preserve ordinary unknown-opening-hours behavior.
    def no_inside(*args):
        raise AssertionError("Nonconflicting meals must use the original nearby flow")
    monkeypatch.setattr(agent.client, "search_nearby", nearby)
    monkeypatch.setattr(agent.client, "search_inside", no_inside)
    candidates = asyncio.run(agent.dynamic_candidates(plan, "food"))
    assert calls == [("restaurant", 34.1, -118.3, 2000),
        ("restaurant", (34.1 + 34.12) / 2, -118.3, 2000), ("restaurant", 34.12, -118.3, 2000)]
    assert all("dining_schedule" not in pool[0] for pool in candidates["1"].values())
    select_lunch(plan, candidates)
    assert (slot(day).start_time, slot(day).end_time) == (time(12), time(13))
    assert [(a.planned_start_time, a.planned_end_time) for a in day.activities] == [(time(9, 30), time(11, 30)), (time(14), time(16))]
    assert not day.timeline_warnings


def test_all_day_internal_dining_keeps_original_activity(monkeypatch):
    plan = make_plan("10:00", "17:00")
    day = plan.days[0]
    day.activities[0].estimated_duration_minutes = 420
    monkeypatch.setattr(agent.client, "search_inside", lambda *a: [row("inside", "park")])
    def forbidden(*args):
        raise AssertionError("Verified internal options should be tried first")
    monkeypatch.setattr(agent.client, "search_nearby", forbidden)
    candidates = asyncio.run(agent.dynamic_candidates(plan, "food", meal_types={"lunch"}))
    select_lunch(plan, candidates)
    assert day.lunch.containing_place_id == "park"
    assert day.activities[0].planned_start_time == time(10) and day.activities[0].planned_end_time == time(17)
    assert day.activities[0].estimated_duration_minutes == 420
    assert (slot(day).start_time, slot(day).end_time) == (time(12), time(13))
    assert "不额外增加" in slot(day).notes and not day.timeline_warnings
    payload = workspace_payload(TripPlan.model_validate_json(plan.model_dump_json()))
    assert len([t for t in payload["days"][0]["timeline"] if t["item_type"] == "activity"]) == 1


@pytest.mark.parametrize("failure", ["empty", "error", "unverified", "closed", "unknown"])
def test_internal_failure_uses_external_off_peak(monkeypatch, failure):
    plan = make_plan()
    def inside(*args):
        if failure == "error":
            raise PlacesError("internal lookup unavailable")
        if failure == "empty":
            return []
        return [row("inside", None if failure == "unverified" else "park",
            None if failure == "unknown" else (16, 18) if failure == "closed" else (8, 22))]
    calls = []
    def nearby(destination, kind, lat, lon, radius):
        calls.append((lat, lon, radius))
        return [row("outside")]
    monkeypatch.setattr(agent.client, "search_inside", inside)
    monkeypatch.setattr(agent.client, "search_nearby", nearby)
    candidates = asyncio.run(agent.dynamic_candidates(plan, "food", meal_types={"lunch"}))
    assert calls == [(34.1, -118.3, 2000)]
    select_lunch(plan, candidates)
    day = plan.days[0]
    assert day.lunch.place_id == "outside" and day.lunch.containing_place_id is None
    assert (slot(day).start_time, slot(day).end_time) == (time(14), time(15))
    assert day.activities[0].planned_start_time == time(11) and day.activities[0].planned_end_time == time(14)
    assert slot(day, "breakfast").start_time == time(8) and slot(day, "dinner").start_time == time(18)
    assert not day.timeline_warnings


def test_external_expansion_filters_hours_and_uses_conflicting_place(monkeypatch):
    plan = make_plan()
    day = plan.days[0]
    day.activities.insert(0, Activity(name="Morning", latitude=35, longitude=-119,
        planned_start_time="09:30", planned_end_time="10:30"))
    schedule_day(day)
    monkeypatch.setattr(agent.client, "search_inside", lambda *a: [])
    calls = []
    def nearby(destination, kind, lat, lon, radius):
        calls.append((lat, lon, radius))
        return [row("unknown", hours=None)] if radius == 2000 else [row("open")]
    monkeypatch.setattr(agent.client, "search_nearby", nearby)
    candidates = asyncio.run(agent.dynamic_candidates(plan, "food", meal_types={"lunch"}))
    assert calls == [(34.1, -118.3, 2000), (34.1, -118.3, 8000)]
    assert candidates["1"]["lunch"][0]["id"] == "open"


def test_no_feasible_dining_leaves_clear_problem_without_moving_park(monkeypatch):
    plan = make_plan("10:00", "17:00")
    monkeypatch.setattr(agent.client, "search_inside", lambda *a: [])
    monkeypatch.setattr(agent.client, "search_nearby", lambda *a: [row("outside")])
    candidates = asyncio.run(agent.dynamic_candidates(plan, "food", meal_types={"lunch"}))
    assert candidates["1"]["lunch"] == []
    schedule_day(plan.days[0])
    day = plan.days[0]
    assert day.activities[0].planned_start_time == time(10) and day.activities[0].planned_end_time == time(17)
    assert slot(day).start_time is None and slot(day).end_time is None
    assert "景点时间已保留" in slot(day).notes
    assert any("午餐" in w and "时间待安排" in w for w in day.timeline_warnings)


def test_google_containment_must_match_id_not_distance_or_name(monkeypatch):
    client = PlacesClient()
    region = {"addressComponents": [{"types": ["country"], "shortText": "US"}],
        "viewport": {"low": {"latitude": 34, "longitude": -119}, "high": {"latitude": 35, "longitude": -118}}}
    monkeypatch.setattr(client, "resolve_destination", lambda *a: region)
    calls = []
    def request(payload, fields):
        assert "containingPlaces" in fields and payload["includedType"] == "restaurant"
        calls.append(payload)
        named = row("Park Restaurant")
        resource = row("resource")
        resource["containingPlaces"] = [{"name": "places/park"}]
        return [row("verified", "park"), resource, named, row("different-parent", "other")]
    monkeypatch.setattr(client, "_request", request)
    assert [r["id"] for r in client.search_inside("LA", "park", "Park")] == ["verified", "resource"]
    client.search_inside("LA", "park", "Park")
    assert len(calls) == 1


@pytest.mark.parametrize("parent,begin,end,conflict", [
    ("park", "12:00", "13:00", False),
    (None, "12:00", "13:00", True),
    ("other", "12:00", "13:00", True),
    ("park", "13:30", "14:30", True),
])
def test_overlap_exemption_requires_exact_parent_and_full_containment(parent, begin, end, conflict):
    plan = make_plan()
    day = plan.days[0]
    day.lunch = Restaurant(name="Lunch", place_id="r", containing_place_id=parent)
    sync_day(day)
    item = slot(day)
    item.start_time, item.end_time = time.fromisoformat(begin), time.fromisoformat(end)
    warnings = check_conflicts(day)
    assert any("时间重叠" in w for w in warnings) == conflict
    # The exception must be independent of list order.
    day.timeline.reverse()
    assert any("时间重叠" in w for w in check_conflicts(day)) == conflict


def test_internal_meal_does_not_hide_other_activity_conflicts():
    day = make_plan().days[0]
    day.lunch = Restaurant(name="Lunch", place_id="r", containing_place_id="park",
        opening_hours=opening_hours(row()))
    schedule_day(day)
    day.activities.append(Activity(name="Unrelated", planned_start_time="12:30", planned_end_time="13:30"))
    # Rebuild then manually place lunch to test validation independently of the scheduler.
    schedule_day(day)
    slot(day).start_time, slot(day).end_time = time(12), time(13)
    assert any("Lunch" in w and "Unrelated" in w for w in check_conflicts(day))


def test_manual_changes_preserve_times_and_revoke_stale_containment(tmp_path, monkeypatch):
    plan = make_plan()
    day = plan.days[0]
    day.lunch = Restaurant(name="Inside", place_id="r", containing_place_id="park",
        opening_hours=opening_hours(row()))
    schedule_day(day)
    store = TripStore(tmp_path / "v8.sqlite3")
    store.save(plan)
    def forbidden(*a, **kw):
        raise AssertionError("Manual edits must not search or invoke agents")
    monkeypatch.setattr(agent, "dynamic_candidates", forbidden)
    monkeypatch.setattr(agent.client, "search_inside", forbidden)
    monkeypatch.setattr(agent.client, "search_nearby", forbidden)
    updated = store.edit(plan.trip_id, day.day_id, "lunch", "save", name="Manual restaurant")
    assert updated.days[0].lunch.containing_place_id is None
    assert slot(updated.days[0]).start_time == time(12)
    assert any("时间重叠" in w for w in updated.days[0].timeline_warnings)
    assert slot(updated.days[0]).notes is None
    # Replacing the parent activity keeps its instance ID but loses sourced place identity.
    store = TripStore(tmp_path / "parent.sqlite3")
    store.save(plan)
    updated = store.edit(plan.trip_id, day.day_id, "activities", "save", item_id=day.activities[0].id, name="Different park")
    assert any("时间重叠" in w for w in updated.days[0].timeline_warnings)
    assert updated.days[0].activities[0].planned_start_time == time(11)


def test_hours_cover_whole_meal_and_handle_overnight_and_24h():
    day = make_plan().days[0]
    restaurant = Restaurant(name="Late", opening_hours=[{"day_of_week": 2, "open_time": "22:00", "close_time": "06:00"}])
    assert open_during(restaurant, day.date, 5 * 60, 6 * 60)
    assert not open_during(restaurant, day.date, 5 * 60 + 1, 6 * 60 + 1)
    restaurant.opening_hours = Restaurant(name="24h", opening_hours=opening_hours({"regularOpeningHours": {
        "periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]}})).opening_hours
    assert open_during(restaurant, day.date, 12 * 60, 13 * 60)
    restaurant.opening_hours = []
    assert not open_during(restaurant, day.date, 12 * 60, 13 * 60)


def test_partial_overlap_uses_full_internal_slot_and_preserves_explicit_times():
    day = make_plan("10:30", "12:30").days[0]
    inside = agent.restaurant_from_candidate(row("inside", "park"), day, "lunch")
    assert meal_slot(day, "lunch", inside) == (11 * 60 + 30, 12 * 60 + 30)
    outside = agent.restaurant_from_candidate(row("outside"), day, "lunch")
    assert meal_slot(day, "lunch", outside) == (12 * 60 + 30, 13 * 60 + 30)
    # An inconsistent estimate must not overwrite the explicitly requested period.
    day.activities[0].estimated_duration_minutes = 180
    schedule_day(day)
    assert day.activities[0].planned_start_time == time(10, 30)
    assert day.activities[0].planned_end_time == time(12, 30)


def test_internal_search_can_work_without_parent_coordinates(monkeypatch):
    plan = make_plan()
    plan.days[0].activities[0].latitude = None
    plan.days[0].activities[0].longitude = None
    monkeypatch.setattr(agent.client, "resolve_destination", lambda *a: {})
    monkeypatch.setattr(agent.client, "search_inside", lambda *a: [row("inside", "park")])
    candidates = asyncio.run(agent.dynamic_candidates(plan, "food", meal_types={"lunch"}))
    assert candidates["1"]["lunch"][0]["id"] == "inside"


@pytest.mark.parametrize("reject_internal", [False, True])
def test_ai_meal_replacement_uses_v8_without_changing_other_slots(tmp_path, monkeypatch, reject_internal):
    from roamory import assistant as ai
    plan = make_plan()
    day = plan.days[0]
    day.lunch = Restaurant(name="Old lunch", place_id="old", opening_hours=opening_hours(row()))
    schedule_day(day)
    store = TripStore(tmp_path / "local.sqlite3")
    store.save(plan)
    before = store.get(plan.trip_id)
    ai._last_request.clear()
    monkeypatch.setattr(ai, "audit", lambda *a, **kw: None)
    monkeypatch.setattr(agent.client, "search_inside", lambda *a: [row("inside", "park")])
    monkeypatch.setattr(agent.client, "search_nearby", lambda *a: [row("outside")])
    async def parse(*a):
        return ai.EditRequest(status="ready", intents=[ai.EditIntent(action="replace", target="meal",
            day_index=1, meal_type="lunch")])
    calls = []
    async def specialist(role, plan, target, candidates, intent):
        assert role == "food" and set(candidates["1"]) == {"lunch"}
        pool = candidates["1"]["lunch"]
        calls.append(pool[0]["id"])
        return agent.FoodOutput(days=[{"day_index": 1, "lunch": None
            if reject_internal and pool[0]["id"] == "inside" else pool[0]["id"]}])
    monkeypatch.setattr(ai, "parse_edit", parse)
    monkeypatch.setattr(ai, "run_specialist", specialist)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "把第一天午餐换一家"))
    assert result.status == "success", result.message
    updated = store.get(plan.trip_id).days[0]
    assert calls == ["inside"] + (["outside"] if reject_internal else [])
    assert updated.lunch.place_id == ("outside" if reject_internal else "inside")
    assert slot(updated).start_time == (time(14) if reject_internal else time(12))
    assert updated.activities == before.days[0].activities
    assert slot(updated, "breakfast") == slot(before.days[0], "breakfast")
    assert slot(updated, "dinner") == slot(before.days[0], "dinner")
    assert not updated.timeline_warnings


def test_late_external_meal_keeps_hotel_after_dinner():
    plan = make_plan("16:00", "21:00")
    day = plan.days[0]
    day.dinner = Restaurant(name="Late dinner", place_id="r", opening_hours=opening_hours(row(hours=(8, 23))))
    day.hotel = Hotel(name="Hotel")
    schedule_day(day)
    assert (slot(day, "dinner").start_time, slot(day, "dinner").end_time) == (time(21), time(22))
    assert next(t for t in day.timeline if t.item_type == "hotel").start_time == time(22)
    assert not day.timeline_warnings


@pytest.mark.parametrize("reject_internal", [False, True])
def test_coordinator_detects_before_food_and_retries_only_rejected_internal(monkeypatch, reject_internal):
    monkeypatch.setattr(agent.client, "search", lambda *a: [row("park")])
    monkeypatch.setattr(agent.client, "search_inside", lambda *a: [row("inside", "park")])
    monkeypatch.setattr(agent.client, "search_nearby", lambda *a: [row("outside")])
    calls = []
    async def run(self, ctx):
        calls.append(self.name)
        if self.name == "activity_agent":
            data = {"days": [{"day_index": 1, "activities": [{"place_id": "park", "planned_start_time": "11:00",
                "planned_end_time": "14:00", "estimated_duration_minutes": 180}]}]}
        else:
            context = json.loads(ctx.session.state["current_plan"])
            assert context["days"][0]["activities"][0]["planned_start_time"] == "11:00:00"
            if len(calls) == 2:
                assert not any(t.get("notes") for t in context["days"][0]["timeline"] if t["item_type"] == "meal")
            pools = json.loads(ctx.session.state["food_candidates"])["1"]
            if len(calls) == 3:
                assert set(pools) == {"lunch"}
            data = {"days": [{"day_index": 1, **{meal: (None if reject_internal and meal == "lunch"
                and pool[0]["id"] == "inside" else pool[0]["id"]) for meal, pool in pools.items() if pool}}]}
        yield Event(author=self.name, content=types.Content(parts=[types.Part(text=json.dumps(data))]),
            actions=EventActions(state_delta={self.output_key: json.dumps(data)}))
    monkeypatch.setattr(LlmAgent, "_run_async_impl", run)
    plan, trace = asyncio.run(generate_plan(TripRequest(destination="Los Angeles, US",
        start_date="2026-11-11", end_date="2026-11-11")))
    day = plan.days[0]
    assert calls == ["activity_agent", "food_agent"] + (["food_agent"] if reject_internal else [])
    assert day.lunch.place_id == ("outside" if reject_internal else "inside")
    assert slot(day).start_time == (time(14) if reject_internal else time(12))
    assert day.activities[0].planned_start_time == time(11) and not day.timeline_warnings
