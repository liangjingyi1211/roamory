"""State-aware parsing contract and safe singleton assignment regressions.

DeepSeek responses are mocked: these tests verify its actual input and the execution
fallback, not the semantic accuracy of a live model.
"""
import asyncio
import json
from google.adk.models.lite_llm import LiteLLMClient
from litellm import ModelResponse

import pytest

from roamory import assistant as ai
from roamory.models import Activity, Hotel, Restaurant, TripRequest, initialize_plan
from roamory.storage import TripStore


@pytest.fixture
def saved(tmp_path, monkeypatch):
    ai._last_request.clear()
    monkeypatch.setattr(ai, "audit", lambda *args, **kwargs: None)
    store = TripStore(tmp_path / "state.sqlite3")
    plan = initialize_plan(TripRequest(destination="Chengdu", start_date="2027-01-01", end_date="2027-01-03"))
    plan.days[0].lunch = Restaurant(name="原午餐")
    plan.days[0].activities = [Activity(name="原景点")]
    store.save(plan)
    return store, store.get(plan.trip_id)


def fake_deepseek(monkeypatch, response):
    captured = {}
    async def generate(self, **kwargs):
        captured.update(kwargs)
        return ModelResponse(choices=[{"message": {"role": "assistant",
            "content": json.dumps(response, ensure_ascii=False)}, "finish_reason": "stop"}])
    monkeypatch.setattr(LiteLLMClient, "acompletion", generate)
    return captured


@pytest.mark.parametrize("message,day,action", [
    ("你找找有名的火锅店和川菜馆，第三天的午餐换成当地有名的川菜", 3, "add"),
    ("第三天午餐安排一家当地有名的川菜馆", 3, "add"),
    ("第三天中午想吃当地川菜，帮我选一家", 3, "add"),
    ("第一天午餐换成川菜", 1, "replace"),
    ("第一天午餐加一家川菜馆", 1, "replace"),
])
def test_deepseek_receives_full_message_and_slot_state(saved, monkeypatch, message, day, action):
    _, plan = saved
    selected = {"dayId": plan.days[0].day_id, "itemType": "meal", "id": plan.days[0].lunch.id}
    capture = fake_deepseek(monkeypatch, {"status": "ready", "intents": [{
        "action": action, "target": "meal", "day_index": day, "meal_type": "lunch",
        "preferences": "当地有名的川菜", "selection_mode": "recommend"}]})
    result = asyncio.run(ai.parse_edit(message, plan, plan.days[0].day_id, selected))
    payload = json.loads(next(m["content"] for m in capture["messages"] if m["role"] == "user"))
    assert payload["message"] == message
    context = payload["context"]
    assert context["days"][2]["lunch"] is None
    assert context["days"][0]["lunch"] == {"id": plan.days[0].lunch.id, "name": "原午餐"}
    assert context["days"][2]["day_index"] == 3
    assert context["selected_day_id"] == plan.days[0].day_id and context["selected_place_ref"] == selected
    instruction = next(m["content"] for m in capture["messages"] if m["role"] == "system")
    assert "WHOLE message" in instruction and "NOT extra edits" in instruction
    assert "choose add if empty and replace if occupied" in instruction
    assert len(result.intents) == 1 and result.intents[0].action == action
    assert result.intents[0].preferences == "当地有名的川菜"
    assert capture["model"].startswith("deepseek/")
    assert capture["response_format"] == {"type": "json_object"}
    assert '"status"' in instruction and '"intents"' in instruction
    assert '"days": []' not in instruction


@pytest.mark.parametrize("target", ["meal", "hotel"])
@pytest.mark.parametrize("occupied", [False, True])
@pytest.mark.parametrize("predicted_action", ["add", "replace"])
def test_resolver_normalizes_singleton_action_without_mutating_input(saved, target, occupied, predicted_action):
    _, plan = saved
    day = plan.days[2]
    slot = "lunch" if target == "meal" else "hotel"
    place = Restaurant(name="旧餐厅") if target == "meal" else Hotel(name="旧酒店")
    setattr(day, slot, place if occupied else None)
    original = ai.EditIntent(action=predicted_action, target=target, day_index=3,
        meal_type="lunch" if target == "meal" else None)
    resolved, selected_day, old = ai.resolve_intent(plan, original, "第三天安排一个新地点")
    assert resolved.action == ("replace" if occupied else "add")
    assert old == (place if occupied else None) and selected_day.day_id == day.day_id
    assert original.action == predicted_action


@pytest.mark.parametrize("action", ["add", "replace"])
@pytest.mark.parametrize("target", ["meal", "hotel", "activity"])
@pytest.mark.parametrize("reference", ["target_name", "target_id"])
def test_missing_explicit_old_place_requires_clarification(saved, action, target, reference):
    _, plan = saved
    scope = ai.EditIntent(action=action, target=target, day_index=3,
        meal_type="lunch" if target == "meal" else None, **{reference: "不存在的旧地点"})
    with pytest.raises(ai.Clarification):
        ai.resolve_intent(plan, scope, "把第三天的不存在的旧地点换成川菜馆")


def test_explicit_old_place_mismatch_is_not_silently_replaced(saved):
    _, plan = saved
    scope = ai.EditIntent(action="add", target="meal", day_index=1, meal_type="lunch", target_name="别的餐厅")
    with pytest.raises(ai.Clarification):
        ai.resolve_intent(plan, scope, "把第一天别的餐厅换成川菜")


def test_page_selection_resolves_slot_before_normalization(saved):
    _, plan = saved
    day = plan.days[0]
    scope = ai.EditIntent(action="add", target="meal")
    resolved, _, old = ai.resolve_intent(plan, scope, "这顿想吃川菜", day.day_id,
        {"dayId": day.day_id, "itemType": "meal", "id": day.lunch.id})
    assert resolved.meal_type == "lunch" and resolved.action == "replace" and old == day.lunch


def test_replan_and_activity_add_semantics_are_unchanged(saved):
    _, plan = saved
    for scope, message in [
        (ai.EditIntent(action="replan", target="meal", day_index=3), "重新规划第三天的餐饮"),
        (ai.EditIntent(action="add", target="activity", day_index=1), "给第一天增加一个景点"),
    ]:
        resolved, _, _ = ai.resolve_intent(plan, scope, message)
        assert resolved.action == scope.action


def test_search_preferences_one_edit_with_wrong_model_action_succeeds(saved, monkeypatch):
    store, base = saved
    message = "你找找有名的火锅店和川菜馆，第三天的午餐换成当地有名的川菜"
    fake_deepseek(monkeypatch, {"status": "ready", "intents": [{"action": "replace", "target": "meal",
        "day_index": 3, "meal_type": "lunch", "preferences": "当地有名的川菜，参考火锅店和川菜馆"}]})
    calls = []
    async def recommend(plan, day, role, scope):
        calls.append(scope.model_copy())
        assert role == "food" and day.lunch is None and scope.action == "add"
        assert "川菜" in scope.preferences
        return {"lunch": Restaurant(name="新川菜馆")}
    monkeypatch.setattr(ai, "recommend", recommend)
    result = asyncio.run(ai.assistant_edit(store, base.trip_id, message))
    updated = store.get(base.trip_id)
    assert result.status == "success" and len(calls) == 1
    assert result.summary["action"] == "add" and len(result.summary["operations"]) == 1
    assert updated.version == base.version + 1
    assert updated.days[:2] == base.days[:2] and updated.days[2].lunch.name == "新川菜馆"


@pytest.mark.parametrize("second_fails", [False, True])
def test_mixed_empty_and_occupied_batch_atomicity(saved, monkeypatch, second_fails):
    store, base = saved
    fake_deepseek(monkeypatch, {"status": "ready", "intents": [
        {"action": "replace", "target": "meal", "day_index": 3, "meal_type": "lunch", "preferences": "川菜"},
        {"action": "add", "target": "meal", "day_index": 1, "meal_type": "lunch", "preferences": "火锅"}]})
    calls, saves = [], []
    save = store.save
    def counted(*args, **kwargs):
        saves.append(1)
        return save(*args, **kwargs)
    monkeypatch.setattr(store, "save", counted)
    async def recommend(plan, day, role, scope):
        calls.append(scope.action)
        if day.day_index == 1:
            assert plan.days[2].lunch.name == "新川菜馆"
            if second_fails:
                raise TimeoutError()
        return {"lunch": Restaurant(name="新川菜馆" if day.day_index == 3 else "新火锅店")}
    monkeypatch.setattr(ai, "recommend", recommend)
    result = asyncio.run(ai.assistant_edit(store, base.trip_id, "第三天午餐换成川菜，再给第一天午餐安排火锅"))
    updated = store.get(base.trip_id)
    assert calls == ["add", "replace"]
    if second_fails:
        assert result.status == "failed" and "第 2 项" in result.message
        assert updated == base and not saves
    else:
        assert result.status == "success" and saves == [1]
        assert updated.version == base.version + 1 and updated.days[1] == base.days[1]
        assert [op["action"] for op in result.summary["operations"]] == ["add", "replace"]


def test_missing_old_object_in_batch_prevents_all_execution(saved, monkeypatch):
    store, base = saved
    fake_deepseek(monkeypatch, {"status": "ready", "intents": [
        {"action": "replace", "target": "meal", "day_index": 3, "meal_type": "lunch"},
        {"action": "add", "target": "meal", "day_index": 1, "meal_type": "lunch", "target_name": "不存在的旧店"}]})
    async def forbidden(*args):
        raise AssertionError("missing old objects must be rejected during preflight")
    monkeypatch.setattr(ai, "recommend", forbidden)
    result = asyncio.run(ai.assistant_edit(store, base.trip_id, "安排第三天午餐，再把第一天不存在的旧店换掉"))
    assert result.status == "needs_clarification" and "第 2 项" in result.message
    assert store.get(base.trip_id) == base
