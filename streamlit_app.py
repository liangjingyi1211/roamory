"""V7 home, personal libraries and the existing planning workspace."""
import asyncio
from pathlib import Path
from urllib.parse import urlencode
import streamlit as st
from roamory.storage import TripStore
from roamory.journal import JournalStore, apply_journal_event
from roamory.pages import render_home, render_trip_library, render_journal_library
from roamory.ui import workspace, apply_workspace_event
from roamory.assistant import assistant_edit
from roamory.agent import safe_error

st.set_page_config(page_title="Roamory", page_icon="✈️", layout="wide")
_frontend = Path(__file__).parent / "roamory" / "frontend"
st.markdown("<style>" + (_frontend / "theme.css").read_text(encoding="utf-8")
    + (_frontend / "shell.css").read_text(encoding="utf-8") + "</style>", unsafe_allow_html=True)
store = TripStore()
trips = store.list_trips()
trip_ids = [key for key, _ in trips]
active = st.query_params.get("trip")
if active not in trip_ids:
    active = trip_ids[0] if trip_ids else None

journals = JournalStore(store.path)
view = st.query_params.get("view") or ("trip" if st.query_params.get("trip") in trip_ids else "new")
if view == "trips":
    render_trip_library(store, trips)
    st.stop()
if view == "journals":
    render_journal_library(journals)
    st.stop()
if view != "trip" or active is None:
    render_home(store, trips, journals)
    st.stop()

st.query_params["trip"] = active
plan = store.get(active)
st.markdown('<span class="workspace-mode" hidden></span>', unsafe_allow_html=True)

ack_key, history_key = "ack:" + active, "chat:" + active
history = st.session_state.get(history_key, [])
event = workspace(plan, st.session_state.get(ack_key), history)
if isinstance(event, dict) and event.get("event_id") and event["event_id"] != st.session_state.get("processed:" + active):
    try:
        if event.get("trip_id") != active:
            raise ValueError("请求不属于当前旅程")
        if event.get("kind") == "journal":
            apply_journal_event(journals, active, event)
            acknowledgment = {"event_id": event["event_id"], "kind": "journal", "success": True, "message": "旅行记录已保存"}
        elif event.get("kind") == "assistant":
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

create_url = "?" + urlencode({"view": "new", "trip": active})
st.markdown(f'<a class="new-trip-fab" href="{create_url}" target="_self" aria-label="新建旅程" title="新建旅程">+</a>', unsafe_allow_html=True)
