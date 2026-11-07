"""Representative V4 scope, rollback, concurrency and compatibility checks."""
import asyncio
import json
import sqlite3
import pytest
from roamory import assistant as ai
from roamory.models import TripRequest, Restaurant, Activity, Hotel, initialize_plan
from roamory.storage import TripStore, VersionConflict
from roamory.agent import FoodOutput, ActivityOutput, HotelOutput

@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "audit", lambda *a, **kw: None)
    monkeypatch.setattr("roamory.agent.audit", lambda *a, **kw: None)
    ai._last_request.clear()
    store = TripStore(tmp_path / "v4.sqlite3")
    plan = initialize_plan(TripRequest(destination="Sydney, Australia", start_date="2027-04-01", end_date="2027-04-03"))
    for day in plan.days:
        day.activities = [Activity(name=f"Museum {day.day_index}", latitude=-33.8, longitude=151.2)]
        day.lunch = Restaurant(name="Original lunch", place_id="old")
        day.hotel = Hotel(name="Original hotel")
    store.save(plan)
    return store, store.get(plan.trip_id)

def test_intent_contract_and_resolution(saved, monkeypatch):
    store, plan = saved
    with pytest.raises(ValueError):
        ai.EditIntent(action="delete", target="meal")
    with pytest.raises(ValueError):
        ai.EditRequest(status="not_supported", intents=[{"action": "add", "target": "activity"}])
    with pytest.raises(ValueError):
        ai.EditRequest(status="ready", intents=[])
    with pytest.raises(ValueError):
        ai.EditRequest(status="ready", intents=[{"action": "add", "target": "activity"}] * 2)
    for data in [dict(action="replace", target="meal", day_index=2, day_id=plan.days[0].day_id, meal_type="lunch"),
                 dict(action="add", target="meal", day_index=2, meal_type="lunch"),
                 dict(action="replace", target="activity", day_index=2, target_id="forged"),
                 dict(action="replace", target="meal", day_index=2, meal_type="lunch", selection_mode="named", new_place_name="invented")]:
        with pytest.raises(ai.Clarification):
            ai.resolve_intent(plan, ai.EditIntent(**data), "把第二天午餐换成附近日料")
    with pytest.raises(ai.Clarification):
        ai.resolve_intent(plan, ai.EditIntent(action="replan", target="day", day_index=1), "重做一下")
    with pytest.raises(ValueError):
        store.apply_scoped_patch(plan, plan.days[0].day_id, {"destination": "outside scope"})
    async def unclear(*args):
        return ai.EditRequest(status="needs_clarification", message="请明确哪一天的哪一餐")
    async def forbidden(*args):
        raise AssertionError("unresolved requests must not invoke specialists")
    monkeypatch.setattr(ai, "parse_edit", unclear)
    monkeypatch.setattr(ai, "recommend", forbidden)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "把那家店换掉"))
    assert result.status == "needs_clarification" and store.get(plan.trip_id).version == plan.version

def test_named_replace_is_local_and_model_free_after_parse(saved, monkeypatch):
    store, plan = saved
    intent = ai.EditIntent(action="replace", target="meal", day_index=2, meal_type="lunch",
        selection_mode="named", new_place_name="User Choice")
    async def parse(*args):
        return ai.EditRequest(status="ready", intents=[intent])
    async def forbidden(*args):
        raise AssertionError("named edits must not use specialists or Places")
    monkeypatch.setattr(ai, "parse_edit", parse)
    monkeypatch.setattr(ai, "recommend", forbidden)
    before = [d.model_dump() for d in plan.days]
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "把第二天午餐换成 User Choice", trip_version=plan.version))
    assert result.status == "success"
    updated = store.get(plan.trip_id)
    assert updated.version == plan.version + 1
    assert updated.days[0].model_dump() == before[0] and updated.days[2].model_dump() == before[2]
    assert updated.days[1].activities == plan.days[1].activities and updated.days[1].hotel == plan.days[1].hotel
    lunch = updated.days[1].lunch
    assert lunch.name == "User Choice" and lunch.place_id is None and lunch.latitude is None
    old_time = next(t for t in plan.days[1].timeline if t.meal_type == "lunch")
    new_time = next(t for t in updated.days[1].timeline if t.meal_type == "lunch")
    assert new_time.start_time == old_time.start_time and new_time.reference_id == lunch.id

def test_recommend_only_routes_food_and_rejects_unverified(saved, monkeypatch):
    store, plan = saved
    async def parse(*args):
        return ai.EditRequest(status="ready", intents=[ai.EditIntent(action="replace", target="meal", day_index=2, meal_type="lunch", preferences="Japanese")])
    calls = []
    async def candidates(plan, role, **kwargs):
        assert kwargs["day_ids"] == {plan.days[1].day_id} and kwargs["meal_types"] == {"lunch"}
        calls.append(role)
        return {"2": {"lunch": [{"id": "verified", "displayName": {"text": "API restaurant"}}]}}
    async def run(role, plan, day, candidates, intent):
        calls.append(role)
        return FoodOutput(days=[{"day_index": 2, "lunch": "verified"}])
    monkeypatch.setattr(ai, "parse_edit", parse)
    monkeypatch.setattr(ai, "dynamic_candidates", candidates)
    monkeypatch.setattr(ai, "run_specialist", run)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "把第二天午餐换成附近的日料"))
    assert result.status == "success" and calls == ["food", "food"]
    assert store.get(plan.trip_id).days[1].lunch.place_id == "verified"
    ai._last_request.clear()
    baseline = store.get(plan.trip_id).model_dump()
    async def invalid(*args):
        return FoodOutput(days=[{"day_index": 2, "lunch": "invented"}])
    monkeypatch.setattr(ai, "run_specialist", invalid)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "把第二天午餐换成附近的日料"))
    assert result.status == "failed" and store.get(plan.trip_id).model_dump() == baseline

def test_replan_add_unsupported_and_concurrent_rollback(saved, monkeypatch):
    store, plan = saved
    state = {"intent": ai.EditIntent(action="add", target="activity", day_index=2)}
    async def parse(*args):
        return ai.EditRequest(status="ready", intents=[state["intent"]])
    async def recommend(plan, day, role, intent):
        assert role == "activity"
        return [Activity(name="New verified candidate", place_id="verified", planned_start_time="16:00", planned_end_time="17:00")]
    monkeypatch.setattr(ai, "parse_edit", parse)
    monkeypatch.setattr(ai, "recommend", recommend)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "第二天加一个博物馆"))
    assert result.status == "success"
    updated = store.get(plan.trip_id)
    assert len(updated.days[1].activities) == 2 and updated.days[1].lunch == plan.days[1].lunch
    assert updated.days[0] == plan.days[0] and updated.days[2] == plan.days[2]
    ai._last_request.clear()
    state["intent"] = ai.EditIntent(action="replan", target="activity", day_index=2)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "重新规划第二天的景点"))
    assert result.status == "success" and len(store.get(plan.trip_id).days[1].activities) == 1
    assert store.get(plan.trip_id).days[1].lunch == plan.days[1].lunch
    baseline = store.get(plan.trip_id)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "删除第二天午餐"))
    assert result.status == "not_supported" and store.get(plan.trip_id) == baseline
    ai._last_request.clear()
    state["intent"] = ai.EditIntent(action="add", target="activity", day_index=2)
    async def concurrent(plan, day, role, intent):
        store.edit(plan.trip_id, plan.days[0].day_id, "breakfast", "save", name="Concurrent manual change")
        return [Activity(name="Must not commit")]
    monkeypatch.setattr(ai, "recommend", concurrent)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "第二天加一个博物馆", trip_version=baseline.version))
    assert result.status == "conflict"
    assert store.get(plan.trip_id).days[1] == baseline.days[1]
    assert store.get(plan.trip_id).days[0].breakfast.name == "Concurrent manual change"

def test_v3_database_backup_and_version_guard(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    plan = initialize_plan(TripRequest(destination="X", start_date="2027-01-01", end_date="2027-01-01"))
    payload = plan.model_dump(mode="json")
    payload.pop("version")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE trips(id TEXT PRIMARY KEY,payload TEXT,updated TEXT DEFAULT CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO trips(id,payload) VALUES (?,?)", (plan.trip_id, json.dumps(payload)))
    store = TripStore(path)
    assert path.with_suffix(".v3-backup.sqlite3").exists()
    assert store.get(plan.trip_id).version == 1
    store.edit(plan.trip_id, plan.days[0].day_id, "lunch", "save", name="Manual", expected_version=1)
    with pytest.raises(VersionConflict):
        store.edit(plan.trip_id, plan.days[0].day_id, "dinner", "save", name="Stale", expected_version=1)

def test_hotel_and_single_day_replan_are_atomic(saved, monkeypatch):
    store, plan = saved
    intent = {"value": ai.EditIntent(action="replace", target="hotel", day_index=2)}
    async def parse(*args):
        return ai.EditRequest(status="ready", intents=[intent["value"]])
    calls = []
    async def recommend(plan, day, role, scope):
        calls.append(role)
        if role == "activity":
            return [Activity(name="Scoped activity", place_id="a")]
        if role == "food":
            return {s: Restaurant(name=f"Scoped {s}", place_id=s) for s in ("breakfast", "lunch", "dinner")}
        return Hotel(name="Scoped hotel", place_id="h")
    monkeypatch.setattr(ai, "parse_edit", parse)
    monkeypatch.setattr(ai, "recommend", recommend)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "把第二天酒店换一个"))
    assert result.status == "success" and calls == ["hotel"]
    updated = store.get(plan.trip_id)
    assert updated.days[1].lunch == plan.days[1].lunch
    assert updated.days[1].activities == plan.days[1].activities
    ai._last_request.clear()
    calls.clear()
    intent["value"] = ai.EditIntent(action="replan", target="day", day_index=2)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "重新规划第二天"))
    assert result.status == "success" and calls == ["activity", "food", "hotel"]
    updated = store.get(plan.trip_id)
    assert updated.days[0] == plan.days[0] and updated.days[2] == plan.days[2]
    baseline = updated.model_dump()
    ai._last_request.clear()
    async def failing(plan, day, role, scope):
        if role == "food":
            raise TimeoutError("Food unavailable")
        return await recommend(plan, day, role, scope)
    monkeypatch.setattr(ai, "recommend", failing)
    result = asyncio.run(ai.assistant_edit(store, plan.trip_id, "重新规划第二天"))
    assert result.status == "failed" and store.get(plan.trip_id).model_dump() == baseline
