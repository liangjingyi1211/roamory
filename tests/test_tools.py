from datetime import date
from pathlib import Path
import json
import pytest
from google.adk.agents import LlmAgent
from google.adk.events import Event, EventActions
from google.genai import types
from streamlit.testing.v1 import AppTest
from travelmind.tools import estimate_cost, count_nights
from travelmind.models import TripRequest, TripPlan, Activity, Hotel, initialize_plan
from travelmind.storage import TripStore
from travelmind.agent import FoodOutput, merge_recommendations
from travelmind.places import PlacesClient, PlacesError, opening_hours, within_destination

def make_plan():
    return initialize_plan(TripRequest(destination="Tokyo", start_date=date(2027, 4, 1), end_date=date(2027, 4, 3)))

def test_cost():
    plan = make_plan()
    plan.days[0].activities = [Activity(name="A", ticket_price=10, currency="JPY"), Activity(name="B", ticket_price=20, currency="SGD")]
    plan.days[0].hotel = Hotel(name="H", price_per_night=100, currency="SGD")
    result = estimate_cost(plan)
    assert result.low is None and result.high is None and not result.is_complete
    assert result.excluded_categories == ["flights"]
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
    monkeypatch.setattr("travelmind.service.generate_plan", forbidden)
    store, plan = TripStore(), make_plan()
    store.save(plan)
    day = plan.days[0]
    store.edit(plan.trip_id, day.day_id, "lunch", "save", name="手动餐厅")
    store.edit(plan.trip_id, day.day_id, "hotel", "save", name="手动酒店")
    store.edit(plan.trip_id, day.day_id, "activities", "save", name="景点一")
    plan = store.edit(plan.trip_id, day.day_id, "activities", "save", name="景点二")
    first = plan.days[0].activities[0].id
    plan = store.edit(plan.trip_id, day.day_id, "activities", "move", item_id=first, direction=1)
    assert plan.days[0].activities[1].id == first
    assert plan.days[0].lunch.place_id is None and plan.days[0].lunch.address is None
    assert plan.days[1] == make_plan().days[1].model_copy(update={"day_id": plan.days[1].day_id})
    with pytest.raises(ValueError):
        store.edit(plan.trip_id, day.day_id, "lunch", "save", name=" ")
    assert store.get(plan.trip_id).days[0].lunch.name == "手动餐厅"
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "streamlit_app.py")
    app.query_params["trip"] = plan.trip_id
    app.run(timeout=20)
    assert not app.exception
    assert any("手动餐厅" in h.value for h in app.subheader)
    app.button(key=f"open:{day.day_id}:breakfast:slot").click().run()
    next(t for t in app.text_input if t.label.startswith("名称（")).set_value("页面新增早餐")
    next(b for b in app.button if b.label == "保存").click().run()
    assert not app.exception
    assert store.get(plan.trip_id).days[0].breakfast.name == "页面新增早餐"
    refreshed = AppTest.from_file(Path(__file__).resolve().parents[1] / "streamlit_app.py")
    refreshed.query_params["trip"] = plan.trip_id
    refreshed.run(timeout=20)
    assert any("页面新增早餐" in h.value for h in refreshed.subheader)
    store.edit(plan.trip_id, day.day_id, "lunch", "delete")
    reloaded = TripStore(path).get(plan.trip_id)
    assert reloaded.days[0].lunch is None
    assert "lunch" in reloaded.days[0].model_dump()
    assert reloaded.estimated_total_cost.low is None

def test_coordinator_flow(monkeypatch):
    import asyncio
    from travelmind.service import generate_plan
    rows = [{"id": "verified", "displayName": {"text": "API fixture"}, "types": ["restaurant"]}]
    def search(destination, kind, preferences):
        return rows
    monkeypatch.setattr("travelmind.agent.client.search", search)
    calls = []
    async def fake_run(self, ctx):
        calls.append(self.name)
        if self.name == "activity_agent":
            data = {"days": [{"day_index": 1, "activities": [{"place_id": "verified"}]}]}
        elif self.name == "food_agent":
            data = {"days": [{"day_index": 1, "lunch": "verified", "dinner": "invented"}]}
        else:
            data = {"days": [{"day_index": 1, "place_id": "verified"}, {"day_index": 3, "place_id": "verified"}]}
        yield Event(author=self.name, content=types.Content(parts=[types.Part(text=json.dumps(data))]),
            actions=EventActions(state_delta={self.output_key: json.dumps(data)}))
    monkeypatch.setattr(LlmAgent, "_run_async_impl", fake_run)
    plan, trace = asyncio.run(generate_plan(TripRequest(destination="Tokyo", start_date="2027-04-01", end_date="2027-04-03")))
    assert calls == ["activity_agent", "food_agent", "hotel_agent"]
    assert plan.days[0].lunch.place_id == "verified" and plan.days[0].dinner is None
    assert plan.days[-1].hotel is None
    TripPlan.model_validate_json(plan.model_dump_json())
    def failing_food(destination, kind, preferences):
        if kind == "restaurant":
            raise PlacesError("explicit API failure")
        return rows
    monkeypatch.setattr("travelmind.agent.client.search", failing_food)
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
