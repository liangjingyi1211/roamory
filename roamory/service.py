"""Shared ADK entry point; both UIs use the same coordinator."""
import uuid
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from .agent import root_agent
from .models import TripRequest, TripPlan

async def generate_plan(request: TripRequest):
    sessions = InMemorySessionService()
    sid = str(uuid.uuid4())
    await sessions.create_session(app_name="roamory", user_id="local_user", session_id=sid)
    runner = Runner(agent=root_agent, app_name="roamory", session_service=sessions)
    trace, plan = [], None
    message = types.Content(role="user", parts=[types.Part(text=request.model_dump_json())])
    async for event in runner.run_async(user_id="local_user", session_id=sid, new_message=message):
        trace.append(event.author)
        if event.author == "roamory" and event.actions.state_delta.get("trip_plan"):
            plan = TripPlan.model_validate(event.actions.state_delta["trip_plan"])
    if plan is None:
        raise ValueError("协调器未生成有效行程，请检查输入与执行日志")
    return plan, trace
