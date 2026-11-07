from datetime import date, time
from pathlib import Path
import json
import pytest
from google.adk.agents import LlmAgent
from google.adk.events import Event, EventActions
from google.genai import types
from streamlit.testing.v1 import AppTest
from travelmind.tools import estimate_cost, count_nights
from travelmind.models import TripRequest, TripPlan, Activity, Hotel, Restaurant, initialize_plan
from travelmind.storage import TripStore
from travelmind.agent import FoodOutput, merge_recommendations
from travelmind.places import PlacesClient, PlacesError, opening_hours, within_destination
from travelmind.timeline import schedule_day, sync_day, opening_for_date
from travelmind.ui import workspace_payload, apply_workspace_event, browser_map_config

@pytest.fixture(autouse=True)
def isolate_audit(monkeypatch):
    monkeypatch.setattr("travelmind.agent.audit", lambda *args, **kwargs: None)
    monkeypatch.setattr("travelmind.places.audit", lambda *args, **kwargs: None)

def make_plan():
    return initialize_plan(TripRequest(destination="Tokyo", start_date=date(2027, 4, 1), end_date=date(2027, 4, 3)))

def test_cost():
    plan = make_plan()
    plan.days[0].activities = [Activity(name="A", ticket_price=10, currency="JPY"), Activity(name="B", ticket_price=20, currency="SGD")]
    plan.days[0].hotel = Hotel(name="H", price_per_night=100, currency="SGD")
    result = estimate_cost(plan)
    assert result.low is None and result.high is None and not result.is_complete
    assert result.excluded_categories == ["flights", "local_transport"]
    amounts = {(c.category, c.currency): c.low for c in result.categories}
    assert amounts[("activities", "JPY")] == 10
    assert amounts[("activities", "SGD")] == 20
    assert amounts[("food", None)] is None

def test_nights():
    assert count_nights("2027-04-01", "2027-04-08")["nights"] == 7

def test_dates_and_verification():
    plan = make_plan()
    assert len(plan.days) == plan.total_days == 3
    assert all({"breakfast", "lunch", "dinner"} <= set(d.model_dump()) for d in plan.days)
    assert initialize_plan(TripRequest(destination="X", start_date=date.today(), end_date=date.today())).total_days == 1
    with pytest.raises(ValueError):
        TripRequest(destination="X", start_date="2027-04-03", end_date="2027-04-01")
    with pytest.raises(ValueError):
        TripRequest(destination="X", start_date=date.today(), end_date=date.today(), budget=100)
    merge_recommendations(plan, "food", FoodOutput(days=[{"day_index": 1, "breakfast": "invented"}]), {})
    assert plan.days[0].breakfast is None
    assert plan.days[0].notes
    assert opening_hours({}) == []
    assert opening_hours({"regularOpeningHours": {"periods": [{"open": {"day": 0, "hour": 22}, "close": {"day": 1, "hour": 2}}]}})[0]["day_of_week"] == 7

def test_persistent_edits_and_page(tmp_path, monkeypatch):
    path = tmp_path / "trip.sqlite3"
    monkeypatch.setenv("TRAVELMIND_DB", str(path))
    def forbidden(*args, **kwargs):
        raise AssertionError("editing must not invoke Places or agents")
    monkeypatch.setattr("travelmind.places.client.search", forbidden)
    monkeypatch.setattr("travelmind.places.client.search_nearby", forbidden)
    monkeypatch.setattr("travelmind.service.generate_plan", forbidden)
    store, plan = TripStore(), make_plan()
    store.save(plan)
    unchanged_day = store.get(plan.trip_id).days[1].model_dump()
    day = plan.days[0]
    store.edit(plan.trip_id, day.day_id, "lunch", "save", name="手动餐厅")
    store.edit(plan.trip_id, day.day_id, "hotel", "save", name="手动酒店")
    store.edit(plan.trip_id, day.day_id, "activities", "save", name="景点一")
    plan = store.edit(plan.trip_id, day.day_id, "activities", "save", name="景点二")
    first = plan.days[0].activities[0].id
    plan = store.edit(plan.trip_id, day.day_id, "activities", "move", item_id=first, direction=1)
    assert plan.days[0].activities[1].id == first
    assert plan.days[0].lunch.place_id is None and plan.days[0].lunch.address is None
    assert plan.days[1].model_dump() == unchanged_day
    with pytest.raises(ValueError):
        store.edit(plan.trip_id, day.day_id, "lunch", "save", name=" ")
    assert store.get(plan.trip_id).days[0].lunch.name == "手动餐厅"
    events = [{"event_id": "test-save", "trip_id": plan.trip_id, "day_id": day.day_id,
        "slot": "breakfast", "action": "save", "arguments": {"name": "页面新增早餐"}}]
    monkeypatch.setattr("travelmind.ui.workspace", lambda plan, ack, conversation=None: events.pop() if events else None)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "streamlit_app.py")
    app.query_params["trip"] = plan.trip_id
    app.run(timeout=20)
    assert not app.exception
    assert store.get(plan.trip_id).days[0].breakfast.name == "页面新增早餐"
    refreshed = AppTest.from_file(Path(__file__).resolve().parents[1] / "streamlit_app.py")
    refreshed.query_params["trip"] = plan.trip_id
    refreshed.run(timeout=20)
    assert not refreshed.exception
    assert any("Tokyo" in h.value for h in refreshed.header)
    payload = workspace_payload(store.get(plan.trip_id))
    assert any(p["name"] == "页面新增早餐" for p in payload["days"][0]["places"].values())
    store.edit(plan.trip_id, day.day_id, "lunch", "delete")
    reloaded = TripStore(path).get(plan.trip_id)
    assert reloaded.days[0].lunch is None
    assert "lunch" in reloaded.days[0].model_dump()
    assert reloaded.estimated_total_cost.low is None

def test_coordinator_flow(monkeypatch):
    import asyncio
    from travelmind.service import generate_plan
    rows = [{"id": f"a{i}", "displayName": {"text": f"Activity {i}"},
        "location": {"latitude": 35 + i, "longitude": 139}, "types": ["tourist_attraction"]} for i in range(3)]
    def search(destination, kind, preferences):
        return rows
    monkeypatch.setattr("travelmind.agent.client.search", search)
    calls = []
    searches = []
    def nearby(destination, kind, latitude, longitude, radius):
        assert "activity_agent" in calls
        searches.append((kind, latitude, radius))
        key = ("r" if kind == "restaurant" else "h") + str(int(latitude - 35))
        return [{"id": key, "displayName": {"text": key}, "location": {"latitude": latitude, "longitude": longitude}}]
    monkeypatch.setattr("travelmind.agent.client.search_nearby", nearby)
    async def fake_run(self, ctx):
        calls.append(self.name)
        if self.name == "activity_agent":
            data = {"days": [{"day_index": i + 1, "activities": [{"place_id": f"a{i}"}]} for i in range(3)]}
        elif self.name == "food_agent":
            assert len(searches) == 9
            assert len(json.loads(ctx.session.state["current_plan"])["days"][1]["activities"]) == 1
            data = {"days": [{"day_index": i + 1, "lunch": f"r{i}", "dinner": "r0" if i else "invented"} for i in range(3)]}
        else:
            data = {"days": [{"day_index": 1, "place_id": "h0"}, {"day_index": 2, "place_id": "h1"}, {"day_index": 3, "place_id": "h1"}]}
        yield Event(author=self.name, content=types.Content(parts=[types.Part(text=json.dumps(data))]),
            actions=EventActions(state_delta={self.output_key: json.dumps(data)}))
    monkeypatch.setattr(LlmAgent, "_run_async_impl", fake_run)
    plan, trace = asyncio.run(generate_plan(TripRequest(destination="Tokyo", start_date="2027-04-01", end_date="2027-04-03")))
    assert calls == ["activity_agent", "food_agent", "hotel_agent"]
    assert plan.days[0].lunch.place_id == "r0" and plan.days[0].dinner is None
    assert plan.days[1].lunch.place_id == "r1" and plan.days[1].dinner is None
    assert plan.days[0].hotel.place_id != plan.days[1].hotel.place_id
    assert plan.days[-1].hotel is None
    TripPlan.model_validate_json(plan.model_dump_json())
    def failing_food(destination, kind, latitude, longitude, radius):
        if kind == "restaurant":
            raise PlacesError("explicit API failure")
        return nearby(destination, kind, latitude, longitude, radius)
    monkeypatch.setattr("travelmind.agent.client.search_nearby", failing_food)
    plan, _ = asyncio.run(generate_plan(TripRequest(destination="Tokyo", start_date="2027-04-01", end_date="2027-04-03")))
    assert plan.days[0].lunch is None and plan.days[0].activities
    assert any("explicit API failure" in note for note in plan.days[0].notes)

def test_places_geographic_restriction(monkeypatch):
    """A real place ID in Singapore must never qualify for an Australian trip."""
    client = PlacesClient()
    region = {"id": "au", "types": ["country"],
        "addressComponents": [{"shortText": "AU", "types": ["country"]}],
        "viewport": {"low": {"latitude": -44, "longitude": 112}, "high": {"latitude": -10, "longitude": 154}}}
    def place(key, country, latitude, longitude):
        return {"id": key, "displayName": {"text": key}, "types": ["restaurant"],
            "location": {"latitude": latitude, "longitude": longitude},
            "addressComponents": [{"shortText": country, "types": ["country"]}]}
    valid = place("Sydney", "AU", -33.8, 151.2)
    singapore = place("Singapore", "SG", 1.3, 103.8)
    wrong_country = place("WrongCountryInsideBounds", "SG", -33.8, 151.2)
    missing_coordinates = place("NoCoordinates", "AU", None, None)
    calls = []
    def request(payload, fields):
        calls.append(payload)
        if payload["textQuery"] == "Australia":
            return [region]
        assert payload["locationRestriction"]["rectangle"] == region["viewport"]
        assert payload["textQuery"] == "restaurant in Australia"
        return [valid, singapore, wrong_country, missing_coordinates]
    monkeypatch.setattr(client, "_request", request)
    assert client.search("Australia", "restaurant", "不爱吃辣的") == [valid]
    assert not within_destination(singapore, region)
    assert client.search("Australia", "restaurant", "other preference") == [valid]
    assert len(calls) == 2  # one destination lookup and one category lookup
    unknown = PlacesClient()
    monkeypatch.setattr(unknown, "_request", lambda *args: [])
    with pytest.raises(PlacesError, match="无法确定目的地"):
        unknown.search("Unknown", "restaurant")

def test_timeline_migration_and_edits(tmp_path, monkeypatch):
    plan = make_plan()
    day = plan.days[0]
    day.activities = [Activity(name="Museum", planned_start_time="11:00", planned_end_time="14:00"),
        Activity(name="Park", estimated_duration_minutes=60)]
    day.lunch = Restaurant(name="Lunch")
    day.hotel = Hotel(name="Hotel")
    schedule_day(day)
    assert not any("时间重叠" in warning for warning in day.timeline_warnings)
    assert all(item.reference_id in {p.id for p in [*day.activities, day.lunch, day.hotel]} for item in day.timeline if item.reference_id)
    store = TripStore(tmp_path / "v3.sqlite3")
    store.save(plan)
    original_second = store.get(plan.trip_id).days[1].model_dump()
    target = next(t for t in day.timeline if t.reference_id == day.activities[0].id)
    updated = store.edit(plan.trip_id, day.day_id, "timeline", "time", item_id=target.id, start_time="12:10", end_time="12:50")
    assert updated.days[0].activities[0].planned_start_time == time(12, 10)
    assert any("时间重叠" in message for message in updated.days[0].timeline_warnings)
    with pytest.raises(ValueError):
        store.edit(plan.trip_id, day.day_id, "timeline", "time", item_id=target.id, start_time="14:00", end_time="13:00")
    assert store.get(plan.trip_id).days[0].activities[0].planned_start_time == time(12, 10)
    updated = store.edit(plan.trip_id, day.day_id, "activities", "save", item_id=day.activities[0].id, name="Manual replacement")
    assert updated.days[0].activities[0].planned_start_time == time(12, 10)
    deleted_id = updated.days[0].activities[0].id
    updated = store.edit(plan.trip_id, day.day_id, "activities", "delete", item_id=deleted_id)
    assert deleted_id not in [t.reference_id for t in updated.days[0].timeline]
    assert updated.days[1].model_dump() == original_second
    # Simulate an untouched V2 payload, not just an empty new V3 instance.
    old = plan.model_dump(mode="json")
    for entry in old["days"]:
        entry.pop("timeline")
        entry.pop("timeline_warnings")
    with store.connect() as db:
        db.execute("UPDATE trips SET payload=? WHERE id=?", (json.dumps(old), plan.trip_id))
    migrated = store.get(plan.trip_id)
    assert migrated.days[0].timeline
    assert [t.id for t in migrated.days[0].timeline] == [t.id for t in store.get(plan.trip_id).days[0].timeline]
    old_activity = next(t for t in migrated.days[0].timeline if t.item_type == "activity")
    migrated = store.edit(plan.trip_id, day.day_id, "timeline", "time", item_id=old_activity.id,
        start_time="10:00", end_time="11:00")
    assert next(t for t in migrated.days[0].timeline if t.id == old_activity.id).start_time == time(10, 0)
    hours = Restaurant(name="Known hours", opening_hours=[
        {"day_of_week": 5, "open_time": "22:00", "close_time": "02:00"},
        {"day_of_week": 6, "open_time": "11:00", "close_time": "14:00"},
        {"day_of_week": 6, "open_time": "17:00", "close_time": "21:00"}])
    assert opening_for_date(hours, date(2026, 11, 7)) == ["00:00–02:00（前日延续）", "11:00–14:00", "17:00–21:00"]
    assert opening_for_date(hours, date(2026, 11, 9)) == []

def test_nearby_filtering_and_browser_config(monkeypatch):
    client = PlacesClient()
    region = {"addressComponents": [{"types": ["country"], "shortText": "AU"}]}
    monkeypatch.setattr(client, "resolve_destination", lambda destination: region)
    def row(key, lat, country):
        return {"id": key, "displayName": {"text": key}, "types": ["restaurant"],
            "location": {"latitude": lat, "longitude": 151.2},
            "addressComponents": [{"types": ["country"], "shortText": country}]}
    calls = []
    def request(payload, fields, endpoint):
        assert endpoint == "searchNearby"
        calls.append(payload)
        return [row("valid", -33.86, "AU"), row("far", -35, "AU"), row("wrong", -33.86, "SG")]
    monkeypatch.setattr(client, "_request", request)
    assert [r["id"] for r in client.search_nearby("Australia", "restaurant", -33.86, 151.2)] == ["valid"]
    client.search_nearby("Australia", "restaurant", -33.86, 151.2)
    assert len(calls) == 1
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "server-secret")
    monkeypatch.delenv("GOOGLE_MAPS_BROWSER_API_KEY", raising=False)
    assert browser_map_config()[0] == ""
    monkeypatch.setenv("GOOGLE_MAPS_BROWSER_API_KEY", "server-secret")
    assert browser_map_config()[0] == ""
    monkeypatch.setenv("GOOGLE_MAPS_BROWSER_API_KEY", "public-restricted-key")
    assert browser_map_config()[0] == "public-restricted-key"
