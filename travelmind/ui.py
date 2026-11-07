"""Streamlit bridge for a dependency-free, persistent timeline/map component."""
import os
from pathlib import Path
import streamlit.components.v1 as components
from .timeline import opening_for_date

_component = components.declare_component("travelmind_workspace", path=str(Path(__file__).parent / "frontend"))

def browser_map_config():
    key = os.getenv("GOOGLE_MAPS_BROWSER_API_KEY", "")
    backend = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if key and key == backend:
        return "", "浏览器密钥与后端密钥相同，未向页面发送。请配置单独、带 HTTP Referer 限制的浏览器密钥。"
    if not key:
        return "", "尚未配置浏览器地图密钥 GOOGLE_MAPS_BROWSER_API_KEY。时间线与编辑正常可用。"
    return key, ""

def workspace_payload(plan):
    data = plan.model_dump(mode="json")
    for source, day in zip(plan.days, data["days"]):
        places = [*source.activities, source.breakfast, source.lunch, source.dinner, source.hotel]
        day["places"] = {p.id: {**p.model_dump(mode="json"), "opening_today": opening_for_date(p, source.date)}
            for p in places if p}
    return data

def workspace(plan, acknowledgment=None, conversation=None):
    key, message = browser_map_config()
    return _component(plan=workspace_payload(plan), browser_key=key, map_message=message,
        acknowledgment=acknowledgment, conversation=conversation or [], default=None, key="workspace:" + plan.trip_id)

def apply_workspace_event(store, trip_id, event):
    if not isinstance(event, dict) or event.get("trip_id") != trip_id:
        raise ValueError("行程编辑请求无效")
    allowed = {"name", "item_id", "direction", "start_time", "end_time"}
    arguments = event.get("arguments", {})
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError("不支持的编辑字段")
    return store.edit(trip_id, event["day_id"], event["slot"], event["action"],
        expected_version=event.get("trip_version"), **arguments)
