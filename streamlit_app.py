"""TravelMind V2: persistent daily cards and local edits."""
import asyncio
from datetime import date, timedelta
import streamlit as st
from travelmind.models import TripRequest, TripPreferences
from travelmind.service import generate_plan
from travelmind.storage import TripStore

st.set_page_config(page_title="TravelMind", page_icon="✈️", layout="centered")
st.markdown("""<style>
.stApp {background:#f6f7fb;color:#202938}
.block-container {max-width:960px;padding-top:2.5rem}
h1,h2,h3 {letter-spacing:-.03em}
div[data-testid="stExpander"] {background:white;border:1px solid #e5e8ef;border-radius:16px;margin-bottom:16px}
div[data-testid="stForm"] {border-radius:14px;background:white}
</style>""", unsafe_allow_html=True)
st.title("TravelMind")
st.caption("把旅程安排成每一天 · 真实地点推荐 · 随时手动调整")
store = TripStore()

def apply_edit(plan, day, slot, action, **kwargs):
    try:
        with st.spinner("正在保存…"):
            store.edit(plan.trip_id, day.day_id, slot, action, **kwargs)
        st.session_state["saved_message"] = "已保存到本机"
        st.rerun()
    except Exception as exc:
        st.error(f"保存失败，原行程未替换：{exc}")

def details(item):
    if item is None:
        return
    with st.expander("查看详情"):
        st.write(item.name)
        for field, label in [("address", "地址"), ("description", "描述"), ("category", "类别"),
            ("price_level", "价格等级"), ("rating", "评分"), ("planned_start_time", "建议开始"),
            ("planned_end_time", "建议结束"), ("estimated_duration_minutes", "建议时长（分钟）")]:
            value = getattr(item, field, None)
            if value is not None:
                st.write(f"{label}：{value}")
        if item.latitude is not None and item.longitude is not None:
            st.caption(f"坐标：{item.latitude}, {item.longitude}")
        if getattr(item, "cuisine", []):
            st.write("菜系：" + "、".join(item.cuisine))
        for field, label in [("ticket_price", "参考门票"), ("price_per_night", "参考每晚价格")]:
            amount = getattr(item, field, None)
            if amount is not None:
                st.write(f"{label}：{amount} {item.currency or '币种未知'}")
        if hasattr(item, "opening_hours"):
            if not item.opening_hours:
                st.caption("暂无营业时间")
            for hours in item.opening_hours:
                suffix = "（次日或跨午夜）" if hours.close_time <= hours.open_time else ""
                st.write(f"周{hours.day_of_week} {hours.open_time:%H:%M}–{hours.close_time:%H:%M}{suffix}")
        if item.source_url:
            st.link_button("在 Google Maps 查看", item.source_url)
        else:
            st.caption("未提供地点来源链接")

def name_editor(plan, day, slot, item=None):
    item_id = item.id if item and slot == "activities" else None
    key = f"{day.day_id}:{slot}:{item_id or 'slot'}"
    label = "编辑 / 更换" if item else ("添加景点" if slot == "activities" else "添加酒店" if slot == "hotel" else "添加餐厅")
    if st.button(label, key="open:" + key):
        st.session_state["editor:" + key] = True
    if st.session_state.get("editor:" + key):
        with st.form("edit:" + key):
            name = st.text_input("名称（仅保存输入，不自动搜索或补全）", value=item.name if item else "")
            a, b = st.columns(2)
            save = a.form_submit_button("保存")
            cancel = b.form_submit_button("取消")
        if cancel:
            st.session_state["editor:" + key] = False
            st.rerun()
        if save:
            if not name.strip():
                st.error("名称不能为空")
            else:
                # On success the rerun closes this form; failed saves retain the entered value.
                try:
                    with st.spinner("正在保存…"):
                        store.edit(plan.trip_id, day.day_id, slot, "save", name=name, item_id=item_id)
                    st.session_state["editor:" + key] = False
                    st.session_state["saved_message"] = "已保存到本机"
                    st.rerun()
                except Exception as exc:
                    st.error(f"保存失败：{exc}")
    if item and st.button("删除", key="delete:" + key):
        apply_edit(plan, day, slot, "delete", item_id=item_id)

with st.expander("规划新旅程", expanded=not bool(st.query_params.get("trip"))):
    with st.form("new_trip"):
        destination = st.text_input("目的地", "Tokyo")
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
            request = TripRequest(destination=destination.strip(), start_date=start, end_date=end,
                preferences=TripPreferences(interests=[x.strip() for x in interests.replace("，", ",").split(",") if x.strip()],
                    food_preferences=[x.strip() for x in food.replace("，", ",").split(",") if x.strip()],
                    travel_pace=pace, additional_notes=notes or None))
            with st.spinner("正在检索真实地点并规划旅程…"):
                plan, trace = asyncio.run(generate_plan(request))
                store.save(plan)
            st.query_params["trip"] = plan.trip_id
            st.session_state["trace"] = trace
            st.rerun()
        except Exception as exc:
            st.error(f"生成失败：{exc}")

trips = store.list_trips()
if trips:
    trip_ids = [key for key, _ in trips]
    labels = dict(trips)
    active = st.query_params.get("trip")
    selected = st.selectbox("已保存的旅程", trip_ids, index=trip_ids.index(active) if active in trip_ids else 0,
        format_func=labels.get)
    if selected != active:
        st.query_params["trip"] = selected
    plan = store.get(selected)
    if message := st.session_state.pop("saved_message", None):
        st.success(message)
    st.header(f"{plan.destination} · {plan.total_days} 天")
    st.caption(f"{plan.start_date} — {plan.end_date} · Google Maps 地点资料；非实时价格或预订承诺")
    cost = plan.estimated_total_cost
    if cost:
        if cost.low is not None:
            st.metric("整段旅程预估费用（不含机票）", f"{cost.currency} {cost.low:g}–{cost.high:g}")
        else:
            st.info(cost.note)
        with st.expander("费用范围与估算依据"):
            for category in cost.categories:
                value = f"{category.currency} {category.low:g}–{category.high:g}（已知项目小计）" if category.low is not None else "未知"
                st.write(f"{category.category}：{value}")
                st.caption(f"{category.basis}；{category.coverage_note}")
            st.write("不含：" + "、".join(cost.excluded_categories))
            for info in cost.missing_information:
                st.caption(info)
    for day in plan.days:
        with st.expander(f"Day {day.day_index} · {day.date} {day.title}", expanded=False):
            for slot, label in [("breakfast", "早餐"), ("lunch", "午餐"), ("dinner", "晚餐")]:
                item = getattr(day, slot)
                st.subheader(f"{label} · {item.name if item else '尚未安排'}")
                details(item)
                name_editor(plan, day, slot, item)
            st.subheader("景点与活动")
            if not day.activities:
                st.caption("尚未安排景点")
            for i, item in enumerate(day.activities):
                st.write(f"{i + 1}. {item.name}")
                details(item)
                name_editor(plan, day, "activities", item)
                a, b = st.columns(2)
                if a.button("↑ 上移", key="up:" + item.id, disabled=i == 0):
                    apply_edit(plan, day, "activities", "move", item_id=item.id, direction=-1)
                if b.button("↓ 下移", key="down:" + item.id, disabled=i == len(day.activities) - 1):
                    apply_edit(plan, day, "activities", "move", item_id=item.id, direction=1)
            name_editor(plan, day, "activities")
            st.subheader("当晚住宿 · " + (day.hotel.name if day.hotel else "尚未安排"))
            details(day.hotel)
            name_editor(plan, day, "hotel", day.hotel)
            for note in day.notes:
                st.warning(note)
    st.download_button("导出行程 JSON", plan.model_dump_json(indent=2), file_name=f"travelmind-{plan.trip_id}.json", mime="application/json")
    if st.session_state.get("trace"):
        with st.expander("生成轨迹"):
            st.code(" → ".join(st.session_state["trace"]))
st.caption("行程与修改保存在当前电脑的 SQLite 文件中；单用户本地使用，不具备账号隔离。地点资料来源：Google Maps。")
