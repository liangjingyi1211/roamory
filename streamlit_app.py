"""TravelMind V3: master-detail daily timeline and Google Maps workspace."""
import asyncio
from datetime import date, timedelta
import streamlit as st
from travelmind.models import TripRequest, TripPreferences
from travelmind.service import generate_plan
from travelmind.storage import TripStore
from travelmind.ui import workspace, apply_workspace_event
from travelmind.agent import safe_error

st.set_page_config(page_title="TravelMind", page_icon="✈️", layout="wide")
st.markdown("""<style>
.stApp {background:#f6f7fb;color:#253047}
.block-container {max-width:1540px;padding:2rem 2.5rem}
h1,h2,h3 {letter-spacing:-.035em}
div[data-testid="stForm"],div[data-testid="stExpander"] {border-radius:14px;border-color:#e3e8f0;background:white}
@media(max-width:850px){.block-container{padding:1rem}}
</style>""", unsafe_allow_html=True)
st.title("TravelMind")
st.caption("你的每一天，有条不紊。真实地点 · 每日时间线 · 自由调整")
store = TripStore()

with st.expander("规划新旅程", expanded=not bool(st.query_params.get("trip"))):
    with st.form("new_trip"):
        destination = st.text_input("目的地", "Sydney, Australia", help="建议填写具体城市；国家级目的地可能涉及多个城市。")
        left, right = st.columns(2)
        start = left.date_input("开始日期", date.today())
        end = right.date_input("结束日期", date.today() + timedelta(days=2))
        interests = st.text_input("旅行兴趣（逗号分隔）", "公园, 博物馆")
        food = st.text_input("餐饮偏好（逗号分隔）")
        pace = st.selectbox("旅行节奏", ["relaxed", "moderate", "intensive"], index=1)
        notes = st.text_area("补充说明", placeholder="例如：第一天下午抵达，最后一天中午离开")
        submitted = st.form_submit_button("生成每日行程", type="primary")
    if submitted:
        try:
            split = lambda text: [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]
            request = TripRequest(destination=destination.strip(), start_date=start, end_date=end,
                preferences=TripPreferences(interests=split(interests), food_preferences=split(food),
                    travel_pace=pace, additional_notes=notes or None))
            with st.spinner("正在安排景点，并根据每天的位置寻找餐厅和住宿…"):
                plan, trace = asyncio.run(generate_plan(request))
                store.save(plan)
            st.query_params["trip"] = plan.trip_id
            st.session_state["trace"] = trace
            st.rerun()
        except Exception as exc:
            st.error(f"生成失败：{safe_error(exc)}")

trips = store.list_trips()
if trips:
    trip_ids, labels = [key for key, _ in trips], dict(trips)
    active = st.query_params.get("trip")
    selected = st.selectbox("已保存的旅程", trip_ids, index=trip_ids.index(active) if active in trip_ids else 0,
        format_func=labels.get)
    if selected != active:
        st.query_params["trip"] = selected
    plan = store.get(selected)
    st.header(f"{plan.destination} · {plan.total_days} 天")
    st.caption(f"{plan.start_date} — {plan.end_date} · Google Maps 地点资料；时间为建议当地时间，不含交通规划")
    cost = plan.estimated_total_cost
    if cost:
        if cost.low is not None:
            st.metric("预估费用（不含机票和交通）", f"{cost.currency} {cost.low:g}–{cost.high:g}")
        else:
            st.caption(cost.note)
        with st.expander("费用范围与估算依据"):
            for category in cost.categories:
                value = f"{category.currency} {category.low:g}–{category.high:g}（已知项目小计）" if category.low is not None else "未知"
                st.write(f"{category.category}：{value}")
                st.caption(f"{category.basis}；{category.coverage_note}")
            st.write("不含：" + "、".join(cost.excluded_categories))
            for missing in cost.missing_information:
                st.caption(missing)
    ack_key = "ack:" + plan.trip_id
    event = workspace(plan, st.session_state.get(ack_key))
    if isinstance(event, dict) and event.get("event_id") and event["event_id"] != st.session_state.get("processed:" + plan.trip_id):
        try:
            apply_workspace_event(store, plan.trip_id, event)
            acknowledgment = {"event_id": event["event_id"], "success": True}
        except Exception as exc:
            acknowledgment = {"event_id": event["event_id"], "success": False, "message": "保存失败，已保留编辑内容：" + safe_error(exc)}
        st.session_state[ack_key] = acknowledgment
        st.session_state["processed:" + plan.trip_id] = event["event_id"]
        st.rerun()
    st.download_button("导出行程 JSON", plan.model_dump_json(indent=2), file_name=f"travelmind-{plan.trip_id}.json", mime="application/json")
    if st.session_state.get("trace"):
        with st.expander("生成轨迹"):
            st.code(" → ".join(st.session_state["trace"]))
else:
    st.info("先规划一段旅程，或从已保存的旅程继续。")
st.caption("本机 SQLite 持久保存 · 单用户使用 · 地点资料来源：Google Maps · 地图不提供导航或交通估算")
