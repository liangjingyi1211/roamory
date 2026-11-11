"""V9 ordered edits, isolated patches, rollback and one transactional commit."""
import asyncio
import json
from datetime import time

import pytest

from roamory import assistant as ai
from roamory.models import Activity, Restaurant, TripRequest, initialize_plan
from roamory.storage import TripStore


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "audit", lambda *a, **kw: None)
    ai._last_request.clear()
    store = TripStore(tmp_path / "batch.sqlite3")
    plan = initialize_plan(TripRequest(destination="Singapore", start_date="2027-01-01", end_date="2027-01-03"))
    for day in plan.days:
        day.activities = [Activity(name="Old museum", place_id="old", latitude=1.3, longitude=103.8)]
        day.lunch = Restaurant(name="Old lunch")
        day.breakfast = Restaurant(name="Old breakfast")
        day.dinner = Restaurant(name="Old dinner")
    store.save(plan)
    base = store.get(plan.trip_id)
    saves = []
    original = store.save
    def save(value, **kwargs):
        saves.append(value.model_copy(deep=True))
        return original(value, **kwargs)
    monkeypatch.setattr(store, "save", save)
    return store, base, saves


def intent(action="replace", target="meal", day_index=2, **kw):
    if target == "meal":
        kw.setdefault("meal_type", "lunch")
    return ai.EditIntent(action=action, target=target, day_index=day_index, **kw)


def run(batch, monkeypatch, intents, builder, message="修改第二天和第三天"):
    store, base, _ = batch
    async def parse(*args):
        return ai.EditRequest(status="ready", intents=intents)
    monkeypatch.setattr(ai, "parse_edit", parse)
    monkeypatch.setattr(ai, "build_patch", builder)
    return asyncio.run(ai.assistant_edit(store, base.trip_id, message, trip_version=base.version))


async def food_patch(plan, scope, day, old):
    return {scope.meal_type: Restaurant(name=f"New {scope.meal_type}")}


def test_cross_day_batch_saves_once(batch, monkeypatch):
    store, base, saves = batch
    calls = []
    async def build(plan, scope, day, old):
        calls.append((day.day_index, scope.target))
        if scope.target == "activity":
            return {"activities": [*day.activities, Activity(name="New gallery", planned_start_time="16:00", planned_end_time="17:00")]}
        return await food_patch(plan, scope, day, old)
    result = run(batch, monkeypatch, [intent(), intent("add", "activity", 3)], build)
    updated = store.get(base.trip_id)
    assert result.status == "success" and len(saves) == 1
    assert updated.version == base.version + 1 and result.version == updated.version
    assert calls == [(2, "meal"), (3, "activity")]
    assert updated.days[0] == base.days[0]
    assert updated.days[1].lunch.name == "New lunch"
    assert updated.days[1].activities == base.days[1].activities
    assert len(updated.days[2].activities) == 2
    assert len(result.summary["operations"]) == len(result.updated_day_ids) == 2


def test_different_meals_preserve_other_fields(batch, monkeypatch):
    store, base, saves = batch
    result = run(batch, monkeypatch, [intent(day_index=1, meal_type=s) for s in ("breakfast", "dinner")], food_patch)
    updated = store.get(base.trip_id)
    assert result.status == "success" and len(saves) == 1
    assert updated.days[0].lunch == base.days[0].lunch
    assert updated.days[0].activities == base.days[0].activities
    assert updated.days[1:] == base.days[1:]


def test_later_retrieval_observes_new_coordinates(batch, monkeypatch):
    store, base, saves = batch
    calls = []
    async def recommend(plan, day, role, scope):
        calls.append(role)
        if role == "activity":
            return [Activity(name="New museum", place_id="new", latitude=1.4, longitude=103.9)]
        # This is the snapshot used by dynamic_candidates, not the original plan.
        assert day.activities[0].place_id == "new"
        assert day.activities[0].latitude == 1.4
        assert plan.days[1].activities[0] == day.activities[0]
        return {"lunch": Restaurant(name="Lunch near new museum")}
    async def parse(*args):
        return ai.EditRequest(status="ready", intents=[intent(target="activity"), intent()])
    monkeypatch.setattr(ai, "parse_edit", parse)
    monkeypatch.setattr(ai, "recommend", recommend)
    result = asyncio.run(ai.assistant_edit(store, base.trip_id, "换第二天景点，再换第二天附近午餐"))
    assert result.status == "success" and calls == ["activity", "food"] and len(saves) == 1
    assert base.days[1].activities[0].place_id == "old"


@pytest.mark.parametrize("failure", ["timeout", "scope", "invalid", "stale_target"])
def test_mid_batch_failure_leaves_payload_and_version_unchanged(batch, monkeypatch, failure):
    store, base, saves = batch
    calls = []
    async def build(plan, scope, day, old):
        calls.append(day.day_index)
        if day.day_index == 3:
            if failure == "timeout":
                raise TimeoutError("private upstream detail")
            if failure == "scope":
                return {"hotel": None}
            if failure == "invalid":
                return {"lunch": {"name": ""}}
            raise ai.Clarification("目标已失效")
        return await food_patch(plan, scope, day, old)
    result = run(batch, monkeypatch, [intent(), intent(day_index=3)], build)
    assert result.status in {"failed", "needs_clarification"}
    assert "未保存任何修改" in result.message and "第 2 项" in result.message
    assert "private upstream detail" not in result.message
    assert calls == [2, 3] and saves == []
    assert store.get(base.trip_id).model_dump() == base.model_dump()


@pytest.mark.parametrize("intents", [
    [intent(), intent()],
    [intent("replan", "day"), intent()],
    [intent("replan", "meal", meal_type=None), intent()],
    [intent(target="activity"), intent(target="activity")],
])
def test_conflicts_preflight_before_any_agent(batch, monkeypatch, intents):
    store, base, saves = batch
    async def forbidden(*args):
        raise AssertionError("conflicts must be detected before network work")
    result = run(batch, monkeypatch, intents, forbidden, "重新规划第二天，然后换第二天午餐")
    assert result.status == "needs_clarification" and "第 2 项" in result.message
    assert saves == [] and store.get(base.trip_id) == base


def test_version_change_during_build_does_not_overwrite(batch, monkeypatch):
    store, base, saves = batch
    async def build(plan, scope, day, old):
        if day.day_index == 3:
            # Deterministic synchronization point while the batch is in flight.
            store.edit(base.trip_id, base.days[0].day_id, "lunch", "save", name="Concurrent edit")
        return await food_patch(plan, scope, day, old)
    result = run(batch, monkeypatch, [intent(), intent(day_index=3)], build)
    updated = store.get(base.trip_id)
    assert result.status == "conflict" and "未保存任何修改" in result.message
    assert len(saves) == 1 and updated.version == base.version + 1
    assert updated.days[0].lunch.name == "Concurrent edit"
    assert updated.days[1:] == base.days[1:]


def test_new_time_overlap_rolls_back(batch, monkeypatch):
    store, base, saves = batch
    async def build(plan, scope, day, old):
        if scope.target == "activity":
            return {"activities": [*day.activities, Activity(name="Overlap", planned_start_time=time(10), planned_end_time=time(11))]}
        return await food_patch(plan, scope, day, old)
    result = run(batch, monkeypatch, [intent(), intent("add", "activity", 3)], build)
    assert result.status == "failed" and "时间冲突" in result.message
    assert saves == [] and store.get(base.trip_id) == base


def test_in_memory_patch_is_detached_and_never_writes(batch):
    store, base, saves = batch
    replacement = Restaurant(name="Replacement")
    updated = store.apply_patch_in_memory(base, base.days[0].day_id, {"lunch": replacement})
    updated.days[0].lunch.name = "Changed later"
    assert replacement.name == "Replacement"
    assert base.days[0].lunch.name == "Old lunch"
    assert saves == [] and store.get(base.trip_id) == base


def test_request_schema_rejects_empty_overlimit_and_unknown_actions():
    for count in (1, 5):
        assert len(ai.EditRequest(status="ready", intents=[intent()] * count).intents) == count
    for data in [dict(status="ready", intents=[]), dict(status="ready", intents=[intent()] * 6),
                 dict(status="not_supported", intents=[intent()]),
                 dict(status="ready", intents=[{"action": "delete", "target": "meal"}])]:
        with pytest.raises(ValueError):
            ai.EditRequest.model_validate(data)


def test_single_edit_uses_batch_and_compatibility_fields(batch, monkeypatch):
    store, base, saves = batch
    result = run(batch, monkeypatch, [intent()], food_patch)
    assert result.status == "success" and len(saves) == 1
    assert result.summary["action"] == "replace"
    assert result.updated_day_id == base.days[1].day_id
    assert result.updated_day_ids == [result.updated_day_id]


def test_five_operations_commit_once(batch, monkeypatch):
    store, base, saves = batch
    intents = [intent(day_index=day, meal_type=slot) for day, slot in
        [(1, "breakfast"), (1, "dinner"), (2, "breakfast"), (2, "dinner"), (3, "lunch")]]
    result = run(batch, monkeypatch, intents, food_patch)
    assert result.status == "success" and len(result.summary["operations"]) == 5
    assert len(saves) == 1 and store.get(base.trip_id).version == base.version + 1


def test_two_distinct_activity_replacements_keep_ids(batch, monkeypatch):
    store, base, saves = batch
    day = base.days[1]
    day.activities.append(Activity(name="Second old", planned_start_time="16:00", planned_end_time="17:00"))
    store.save(base)
    saves.clear()
    base = store.get(base.trip_id)
    intents = [intent(target="activity", target_id=a.id) for a in base.days[1].activities]
    async def recommend(plan, day, role, scope):
        return [Activity(name="New " + scope.target_id)]
    async def parse(*args):
        return ai.EditRequest(status="ready", intents=intents)
    monkeypatch.setattr(ai, "recommend", recommend)
    monkeypatch.setattr(ai, "parse_edit", parse)
    result = asyncio.run(ai.assistant_edit(store, base.trip_id, "替换第二天的两个景点"))
    assert result.status == "success" and len(saves) == 1
    updated = store.get(base.trip_id)
    assert [a.id for a in updated.days[1].activities] == [a.id for a in base.days[1].activities]
    assert all(a.name.startswith("New ") for a in updated.days[1].activities)


def test_activity_patch_cannot_modify_unselected_object(batch, monkeypatch):
    store, base, saves = batch
    async def malicious(plan, scope, day, old):
        day.activities[0].name = "Unauthorized overwrite"
        return {"activities": [*day.activities, Activity(name="New") ]}
    result = run(batch, monkeypatch, [intent("add", "activity")], malicious)
    assert result.status == "failed" and "已有景点" in result.message
    assert not saves and store.get(base.trip_id) == base


def test_third_operation_timeout_no_intermediate_commit(batch, monkeypatch):
    store, base, saves = batch
    seen = []
    async def build(plan, scope, day, old):
        seen.append(day.day_index)
        if day.day_index == 3:
            raise asyncio.TimeoutError()
        return await food_patch(plan, scope, day, old)
    result = run(batch, monkeypatch, [intent(day_index=d) for d in (1, 2, 3)], build)
    assert seen == [1, 2, 3] and "第 3 项" in result.message
    assert result.status == "failed" and not saves and store.get(base.trip_id) == base


def test_deleted_trip_cannot_be_recreated_by_batch(batch, monkeypatch):
    store, base, saves = batch
    async def build(plan, scope, day, old):
        with store.connect() as db:
            db.execute("DELETE FROM trips WHERE id=?", (base.trip_id,))
        return await food_patch(plan, scope, day, old)
    result = run(batch, monkeypatch, [intent()], build)
    assert result.status == "conflict"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0


def test_parser_overlimit_is_not_truncated(batch, monkeypatch):
    from google.adk.models.lite_llm import LiteLLMClient
    from litellm import ModelResponse
    _, base, _ = batch
    captured = {}
    async def generate(self, **kwargs):
        captured.update(kwargs)
        return ModelResponse(choices=[{"message": {"role": "assistant",
            "content": json.dumps({"status": "ready",
                "intents": [intent().model_dump(mode="json")] * 6})}, "finish_reason": "stop"}])
    monkeypatch.setattr(LiteLLMClient, "acompletion", generate)
    result = asyncio.run(ai.parse_edit("六项修改", base))
    assert result.status == "not_supported" and result.intents == []
    assert "5" in result.message and captured["response_format"] == {"type": "json_object"}


def test_logging_io_failure_does_not_report_committed_batch_as_failed(batch, monkeypatch):
    store, base, saves = batch
    def unavailable(*args, **kwargs):
        raise OSError("Log directory unavailable")
    monkeypatch.setattr(ai, "audit", unavailable)
    result = run(batch, monkeypatch, [intent()], food_patch)
    assert result.status == "success" and len(saves) == 1
    assert store.get(base.trip_id).version == result.version == base.version + 1
