"""Creation, trip library and personal journal views, using the existing service."""
import asyncio
import inspect
from datetime import date, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import streamlit as st
from . import service
from .agent import safe_error
from .journal import JournalStore, TravelJournalEntry
from .models import TripPreferences, TripRequest

_UPLOAD_LIMIT = {"max_upload_size": 5} if "max_upload_size" in inspect.signature(st.file_uploader).parameters else {}


def open_trip(trip_id):
    st.query_params["trip"] = trip_id
    st.query_params.pop("view", None)
    st.rerun()


def page_navigation():
    st.markdown('''<nav class="site-nav" aria-label="主要导航"><a class="site-brand" href="?view=new" target="_self">Roamory</a>
      <div><a href="?view=new" target="_self">规划旅程</a><a href="?view=trips" target="_self">我的行程</a>
      <a href="?view=journals" target="_self">我的旅行日记</a></div></nav>''', unsafe_allow_html=True)


def render_home(store, trips, journals):
    st.markdown('<span class="home-mode" hidden></span>', unsafe_allow_html=True)
    page_navigation()
    illustration = (Path(__file__).parent / "frontend" / "journey-hero.svg").read_text(encoding="utf-8")
    st.markdown('<section class="home-hero"><div class="hero-copy"><p class="hero-kicker">从一份计划，到一路故事</p>'
        '<h1>让下一段旅程，<br>值得期待与回味。</h1><p>规划每天的精彩，也留住旅途中的小小瞬间。</p></div>'
        + illustration + '</section>', unsafe_allow_html=True)
    left, right = st.columns([1.65, 1], gap="large")
    with left:
        with st.form("new_trip"):
            st.subheader("下一站，去哪里？")
            st.caption("告诉我们你的想法，让每日景点、餐饮与住宿有条理地展开。")
            destination = st.text_input("目的地", "Sydney, Australia", placeholder="城市与国家，例如 Kyoto, Japan")
            a, b = st.columns(2)
            start = a.date_input("开始日期", date.today())
            end = b.date_input("结束日期", date.today() + timedelta(days=2))
            interests = st.text_input("旅行兴趣", "公园, 博物馆", help="用逗号分隔，例如：海岸、建筑、艺术")
            food = st.text_input("餐饮偏好", help="例如：日料、不吃辣、素食")
            pace = st.selectbox("旅行节奏", ["relaxed", "moderate", "intensive"], index=1,
                format_func=lambda value: {"relaxed": "悠闲慢游", "moderate": "张弛有度", "intensive": "充实探索"}[value])
            notes = st.text_area("补充说明", placeholder="例如：第一天下午抵达，最后一天中午离开")
            submitted = st.form_submit_button("创建行程", type="primary", use_container_width=True)
        if submitted:
            try:
                split = lambda text: [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]
                request = TripRequest(destination=destination.strip(), start_date=start, end_date=end,
                    preferences=TripPreferences(interests=split(interests), food_preferences=split(food),
                        travel_pace=pace, additional_notes=notes or None))
                with st.spinner("正在规划每日景点、附近餐饮与住宿…"):
                    plan, trace = asyncio.run(service.generate_plan(request))
                    store.save(plan)
                st.session_state["trace"] = trace
                open_trip(plan.trip_id)
            except Exception as exc:
                st.error("创建失败，输入已保留：" + safe_error(exc))
    with right:
        st.markdown(f'''<div class="home-rail"><p class="rail-label">你的旅行空间</p>
          <a class="navigation-card" href="?view=trips" target="_self"><span class="nav-card-number">01 · PLAN</span>
          <h2>我的行程 <span aria-hidden="true">↗</span></h2><p>回到已规划的每一段旅程，继续探索与调整。</p><strong>{len(trips)} 段已保存旅程</strong></a>
          <a class="navigation-card" href="?view=journals" target="_self"><span class="nav-card-number">02 · REMEMBER</span>
          <h2>我的旅行日记 <span aria-hidden="true">↗</span></h2><p>把美味、见闻和偶然的惊喜，写进自己的故事。</p><strong>{len(journals.list_entries())} 条旅行记录</strong></a>
          <p class="rail-note">计划可以调整，感受值得留下。<br>打开行程，点击地点卡片上的「记录」即可开始。</p></div>''', unsafe_allow_html=True)


def render_trip_library(store, trips):
    page_navigation()
    st.title("我的行程")
    st.caption("所有已保存的旅程都在这里，随时打开，继续规划。")
    if not trips:
        st.info("还没有行程。从「规划旅程」开始，设计你的下一站。")
    for trip_id, _ in trips:
        plan = store.get(trip_id)
        with st.container(border=True):
            st.subheader(plan.destination)
            st.caption(f"{plan.start_date} — {plan.end_date} · {plan.total_days} 天")
            if st.button("打开行程", key="open-trip:" + trip_id):
                open_trip(trip_id)


def render_journal_library(journals):
    st.markdown('<span class="journal-library-mode" hidden></span>', unsafe_allow_html=True)
    page_navigation()
    st.title("我的旅行日记")
    st.caption("地点会成为回忆，文字把它们留下。按最近更新时间排列。")
    entries = journals.list_entries()
    if not entries:
        st.info("还没有旅行记录。打开「我的行程」，点击景点、餐厅或酒店卡片的「记录」。")
        return
    names = {entry.trip_id: f"{entry.trip_destination} · {entry.day_date}" for entry in reversed(entries)}
    selected = st.selectbox("按行程查看", ["all", *names], format_func=lambda value: "全部行程" if value == "all" else names[value])
    for entry in entries:
        if selected != "all" and entry.trip_id != selected:
            continue
        with st.container(border=True):
            heading, action = st.columns([5, 1])
            with heading:
                st.subheader(entry.title)
            with action:
                if st.button("编辑", key="edit-journal:" + entry.id, use_container_width=True):
                    st.session_state["journal_editor"] = {"entry": entry.model_dump(mode="json"), "token": uuid4().hex}
            st.caption(f"{entry.trip_destination} · {entry.day_date} · {entry.place_name}")
            st.caption(f"记录于 {entry.created_at:%Y-%m-%d %H:%M} UTC · 更新于 {entry.updated_at:%Y-%m-%d %H:%M} UTC")
            editor = st.session_state.get("journal_editor")
            editing = editor and editor["entry"]["id"] == entry.id
            if editing:
                original = TravelJournalEntry.model_validate(editor["entry"])
                token = editor["token"]
                with st.form("journal:" + token):
                    content = st.text_area("旅行感想", original.content, max_chars=10000, height=160, key="journal-content:" + token)
                    keep = []
                    if original.images:
                        st.caption("已有图片：勾选后在保存时移除；取消编辑不会删除图片。")
                    for offset in range(0, len(original.images), 3):
                        columns = st.columns(min(3, len(original.images) - offset))
                        for column, image in zip(columns, original.images[offset:offset + 3]):
                            with column:
                                data = journals.image_bytes(entry.id, image.id)
                                if data:
                                    st.image(data, caption=image.name, use_container_width=True)
                                else:
                                    st.caption("这张图片已被其他窗口移除，请刷新后再编辑。")
                                if not st.checkbox("移除这张图片", key=f"remove:{token}:{image.id}"):
                                    keep.append(image.id)
                    uploads = st.file_uploader("添加图片", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
                        help="每条最多 6 张，每张不超过 5MB。可以只写文字、只上传图片，或同时记录。", key="journal-files:" + token, **_UPLOAD_LIMIT)
                    saved = st.form_submit_button("保存记录", type="primary")
                    cancelled = st.form_submit_button("取消")
                if cancelled:
                    st.session_state.pop("journal_editor", None)
                    st.rerun()
                if saved:
                    try:
                        journals.save(trip_id=entry.trip_id, day_id=entry.day_id, item_type=entry.item_type,
                            item_id=entry.item_id, content=content, expected_revision=original.revision,
                            keep_image_ids=keep, new_images=[{"name": image.name, "data": image.getvalue()} for image in uploads])
                        st.session_state.pop("journal_editor", None)
                        st.rerun()
                    except Exception as exc:
                        st.error("记录未保存，输入已保留：" + safe_error(exc))
            else:
                if entry.content:
                    st.markdown('<div class="journal-prose">' + escape(entry.content) + '</div>', unsafe_allow_html=True)
                for offset in range(0, len(entry.images), 3):
                    columns = st.columns(min(3, len(entry.images) - offset))
                    for column, image in zip(columns, entry.images[offset:offset + 3]):
                        with column:
                            data = journals.image_bytes(entry.id, image.id)
                            if data:
                                st.image(data, caption=image.name, use_container_width=True)
                st.markdown('<a class="journal-trip-link" href="?' + escape(urlencode({"trip": entry.trip_id})) + '" target="_self">查看所属行程 ↗</a>', unsafe_allow_html=True)
