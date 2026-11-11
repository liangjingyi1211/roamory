"""Exercise the actual ADK transport conversion without network calls."""
import asyncio
import json

import pytest
from google.adk.models.lite_llm import LiteLLMClient
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from litellm import ModelResponse
from pydantic import ValidationError

from roamory.agent import ActivityOutput, FoodOutput, HotelOutput, get_llm


@pytest.mark.parametrize("schema", [ActivityOutput, FoodOutput, HotelOutput])
def test_json_mode_reaches_transport_and_preserves_schema(monkeypatch, schema):
    captured = {}

    async def complete(self, **kwargs):
        captured.update(kwargs)
        return ModelResponse(choices=[{"message": {
            "role": "assistant", "content": '{"days": []}'
        }, "finish_reason": "stop"}])

    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    request = LlmRequest(contents=[types.Content(role="user", parts=[
        types.Part(text="Plan the requested trip.")])],
        config=types.GenerateContentConfig(response_schema=schema,
            response_mime_type="application/json", system_instruction="Select real IDs."))

    async def run():
        return [r async for r in get_llm().generate_content_async(request)]

    responses = asyncio.run(run())
    assert captured["response_format"] == {"type": "json_object"}
    system = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "Select real IDs." in system
    assert json.dumps(schema.model_json_schema(), ensure_ascii=False) in system
    assert 'Example JSON envelope' in system
    assert request.config.response_schema is schema
    assert request.config.system_instruction == "Select real IDs."
    assert schema.model_validate_json(responses[0].content.parts[0].text).days == []


def test_local_validation_rejects_invalid_selection():
    with pytest.raises(ValidationError):
        ActivityOutput.model_validate_json(
            '{"days": [{"day_index": 1, "activities": [{"place_id": "real-id", '
            '"estimated_duration_minutes": -5}]}]}')


def test_assistant_parse_failure_is_logged_without_credentials(monkeypatch):
    from roamory import assistant
    from roamory.models import TripRequest, initialize_plan

    events = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")
    monkeypatch.setattr(assistant, "audit_edit", lambda event, **fields: events.append((event, fields)))

    async def fail(self, **kwargs):
        raise RuntimeError("upstream rejected private-test-key")

    monkeypatch.setattr(LiteLLMClient, "acompletion", fail)
    plan = initialize_plan(TripRequest(destination="Sydney", start_date="2027-01-01", end_date="2027-01-02"))
    with pytest.raises(RuntimeError):
        asyncio.run(assistant.parse_edit("Replace Day 1 lunch", plan))
    assert events[0][0] == "assistant_parse_failed"
    assert events[0][1]["trip_id"] == plan.trip_id
    assert "private-test-key" not in events[0][1]["error"]
    assert "[REDACTED]" in events[0][1]["error"]
