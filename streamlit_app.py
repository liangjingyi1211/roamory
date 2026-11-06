"""Streamlit UI for local ADK agent (run at repository root)."""
import asyncio
import uuid
import streamlit as st
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

load_dotenv()
from travelmind.agent import root_agent  # noqa: E402

APP_NAME = "travelmind"

async def ask_agent(query: str):
    sessions = InMemorySessionService()
    user_id = "local_user"
    session_id = str(uuid.uuid4())
    await sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=sessions)
    final_text = ""
    trace = []
    message = types.Content(role="user", parts=[types.Part(text=query)])
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
        author = getattr(event, "author", "unknown")
        calls = event.get_function_calls() if hasattr(event, "get_function_calls") else []
        if calls:
            trace.extend(f"{author}: tool call {x.name}" for x in calls)
        if event.is_final_response() and event.content and event.content.parts:
            text_parts = [p.text for p in event.content.parts if getattr(p, "text", None)]
            if text_parts:
                final_text = "\n".join(text_parts)
                trace.append(f"final response by {author}")
    return final_text or "No final text response. Check the execution trace and server logs.", trace

st.set_page_config(page_title="TravelMind", page_icon="✈️")
st.title("✈️ TravelMind · Multi-Agent Demo")
st.warning("Demo data only: example prices are not live quotes, inventory or bookable offers.")
with st.form("trip"):
    origin = st.text_input("出发地", "Singapore")
    destination = st.text_input("目的地（演示数据：Tokyo / Kyoto / Singapore）", "Tokyo")
    start = st.date_input("出发日期")
    end = st.date_input("结束日期")
    budget = st.number_input("预算（SGD）", min_value=1, value=1500)
    interests = st.text_input("偏好", "动漫、美食、不想太累")
    submitted = st.form_submit_button("规划旅行")
if submitted:
    if end <= start:
        st.error("结束日期必须晚于出发日期。")
    else:
        query = (f"请规划旅行：从 {origin} 到 {destination}，{start.isoformat()} 至 {end.isoformat()}，"
                 f"预算 {budget} SGD，偏好：{interests}。按需使用专职 Agent 和工具，"
                 "给出费用估算、剩余预算以及数据限制；不要把模拟价格当实时价格。")
        with st.spinner("Agent 正在处理..."):
            try:
                answer, trace = asyncio.run(ask_agent(query))
                st.markdown(answer)
                with st.expander("执行轨迹（工具调用）"):
                    st.code("\n".join(trace) or "No tool calls captured")
            except Exception as exc:
                st.error(f"请求失败：{type(exc).__name__}: {exc}")
