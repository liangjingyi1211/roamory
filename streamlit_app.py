"""V4 workspace and independent creation/history view on the existing service."""
import asyncio
from datetime import date, timedelta
from urllib.parse import urlencode
import streamlit as st
from travelmind.models import TripRequest, TripPreferences
from travelmind.service import generate_plan
from travelmind.storage import TripStore
from travelmind.ui import workspace, apply_workspace_event
from travelmind.assistant import assistant_edit
from travelmind.agent import safe_error

st.set_page_config(page_title="TravelMind", page_icon="✈️", layout="wide")
st.markdown("""<style>
.stApp {background:#f6f7fb;color:#253047}
.block-container {max-width:1540px;padding:1.8rem 2.5rem 5rem}
h1,h2,h3 {letter-spacing:-.035em}
div[data-testid="stForm"],div[data-testid="stExpander"]{border-radius:14px;border-color:#e3e8f0;background:white}
.new-trip-fab{position:fixed;bottom:25px;left:25px;z-index:99999;width:52px;height:52px;display:flex;align-items:center;justify-content:center;border-radius:50%;background:#326db8;color:white!important;font-size:30px;text-decoration:none;box-shadow:0 4px 18px #25304720;transition:background .15s}
.new-trip-fab:hover{background:#22579b}.new-trip-fab:focus-visible{outline:3px solid #a4c4ff;outline-offset:4px}
@media(max-width:850px){.block-container{padding:1rem 1rem 5rem}.new-trip-fab{bottom:18px;left:18px;width:46px;height:46px}}
</style>""", unsafe_allow_html=True)
store = TripStore()
trips = store.list_trips()
trip_ids = [key for key, _ in trips]
active = st.query_params.get("trip")
if active not in trip_ids:
    active = trip_ids[0] if trip_ids else None

if st.query_params.get("view") == "new" or active is None:
    st.title("规划新旅程")
    st.caption("创建独立的新行程，已有旅程与修改都会保留。")
    if active and st.button("返回当前行程"):
        st.query_params["trip"] = active
        st.query_params.pop("view", None)
        st.rerun()
    with st.form("new_trip"):
        destination = st.text_input("目的地", "Sydney, Australia")
        a, b = st.columns(2)
        start = a.date_input("开始日期", date.today())
        end = b.date_input("结束日期", date.today() + timedelta(days=2))
        interests = st.text_input("旅行兴趣（逗号分隔）", "公园, 博物馆")
        food = st.text_input("餐饮偏好（逗号分隔）")
        pace = st.selectbox("旅行节奏", ["relaxed", "moderate", "intensive"], index=1)
        notes = st.text_area("补充说明", placeholder="例如：第一天下午抵达，最后一天中午离开")
        submitted = st.form_submit_button("创建行程", type="primary")
    if submitted:
        try:
            split = lambda text: [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]
            request = TripRequest(destination=destination.strip(), start_date=start, end_date=end,
                preferences=TripPreferences(interests=split(interests), food_preferences=split(food),
                    travel_pace=pace, additional_notes=notes or None))
            with st.spinner("正在规划每日景点、附近餐饮与住宿…"):
                plan, trace = asyncio.run(generate_plan(request))
                store.save(plan)
            st.query_params["trip"] = plan.trip_id
            st.query_params.pop("view", None)
            st.session_state["trace"] = trace
            st.rerun()
        except Exception as exc:
            st.error("创建失败，输入已保留：" + safe_error(exc))
    if trips:
        st.subheader("最近旅程")
        for trip_id, title in trips:
            if st.button(title, key="open-trip:" + trip_id):
                st.query_params["trip"] = trip_id
                st.query_params.pop("view", None)
                st.rerun()
    st.stop()

st.query_params["trip"] = active
plan = store.get(active)
st.title("TravelMind")
st.header(f"{plan.destination} · {plan.total_days} 天")
st.caption(f"{plan.start_date} — {plan.end_date} · 真实地点 · 建议当地时间 · 不含交通规划")
cost = plan.estimated_total_cost
if cost:
    if cost.low is not None:
        st.caption(f"预估费用：{cost.currency} {cost.low:g}–{cost.high:g}（不含机票和交通）")
    else:
        st.caption(cost.note)

ack_key, history_key = "ack:" + active, "chat:" + active
history = st.session_state.get(history_key, [])
event = workspace(plan, st.session_state.get(ack_key), history)
if isinstance(event, dict) and event.get("event_id") and event["event_id"] != st.session_state.get("processed:" + active):
    try:
        if event.get("trip_id") != active:
            raise ValueError("请求不属于当前旅程")
        if event.get("kind") == "assistant":
            message = event.get("message", "")
            response = asyncio.run(assistant_edit(store, active, message,
                selected_day_id=event.get("selected_day_id"), selected_place_ref=event.get("selected_place_ref"),
                trip_version=event.get("trip_version"), history=history))
            st.session_state[history_key] = [*history, {"role": "user", "text": message},
                {"role": "assistant", "text": response.message, "status": response.status}][-20:]
            # Parsing/clarification failures are completed requests, not failed CRUD.
            acknowledgment = {"event_id": event["event_id"], "success": True, "message": response.message,
                "assistant_status": response.status}
        else:
            apply_workspace_event(store, active, event)
            acknowledgment = {"event_id": event["event_id"], "success": True}
    except Exception as exc:
        acknowledgment = {"event_id": event["event_id"], "success": False,
            "message": "操作未保存，已保留编辑内容：" + safe_error(exc)}
    st.session_state[ack_key] = acknowledgment
    st.session_state["processed:" + active] = event["event_id"]
    st.rerun()

if cost:
    with st.expander("费用范围与估算依据"):
        for category in cost.categories:
            value = f"{category.currency} {category.low:g}–{category.high:g}（已知项目小计）" if category.low is not None else "未知"
            st.write(f"{category.category}：{value}")
            st.caption(f"{category.basis}；{category.coverage_note}")
        st.caption("不含：" + "、".join(cost.excluded_categories))

create_url = "?" + urlencode({"view": "new", "trip": active})
st.markdown(f'<a class="new-trip-fab" href="{create_url}" target="_self" aria-label="新建旅程" title="新建旅程">+</a>', unsafe_allow_html=True)
st.caption("SQLite 本机持久保存 · 单用户 · 对话记录按旅程隔离，刷新页面可能清空 · 地点来源：Google Maps")
