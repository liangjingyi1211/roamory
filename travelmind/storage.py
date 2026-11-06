"""Local single-user SQLite persistence and atomic, model-free edits."""
import os
import sqlite3
from pathlib import Path
from .models import TripPlan, Restaurant, Activity, Hotel
from .tools import estimate_cost

class TripStore:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("TRAVELMIND_DB", "data/travelmind.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS trips (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated TEXT DEFAULT CURRENT_TIMESTAMP)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, plan: TripPlan):
        plan = TripPlan.model_validate(plan.model_dump())
        with self.connect() as db:
            db.execute("INSERT INTO trips(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=CURRENT_TIMESTAMP",
                (plan.trip_id, plan.model_dump_json()))

    def get(self, trip_id: str) -> TripPlan:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM trips WHERE id=?", (trip_id,)).fetchone()
        if row is None:
            raise ValueError("行程不存在")
        return TripPlan.model_validate_json(row[0])

    def list_trips(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM trips ORDER BY updated DESC,rowid DESC").fetchall()
        return [(key, f'{(p := TripPlan.model_validate_json(payload)).destination} · {p.start_date} — {p.end_date}') for key, payload in rows]

    def edit(self, trip_id, day_id, slot, action, *, name=None, item_id=None, direction=None):
        """Read-modify-write under one transaction; never calls generation or Places."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM trips WHERE id=?", (trip_id,)).fetchone()
            if row is None:
                raise ValueError("行程不存在")
            plan = TripPlan.model_validate_json(row[0])
            day = next((d for d in plan.days if d.day_id == day_id), None)
            if day is None:
                raise ValueError("日期不存在")
            if slot not in {"breakfast", "lunch", "dinner", "hotel", "activities"}:
                raise ValueError("非法槽位")
            if action not in {"save", "delete", "move"}:
                raise ValueError("非法操作")
            if action == "save" and (not name or not name.strip()):
                raise ValueError("名称不能为空")
            if slot == "activities":
                index = next((i for i, a in enumerate(day.activities) if a.id == item_id), None)
                if item_id and index is None:
                    raise ValueError("景点不存在")
                if action == "save":
                    # Replacement clears sourced metadata; a manual name is not a verified place.
                    item = Activity(name=name.strip(), **({"id": item_id} if item_id else {}))
                    if index is None:
                        day.activities.append(item)
                    else:
                        day.activities[index] = item
                elif index is None:
                    raise ValueError("请指定景点")
                elif action == "delete":
                    day.activities.pop(index)
                elif direction in {-1, 1} and 0 <= index + direction < len(day.activities):
                    day.activities[index], day.activities[index + direction] = day.activities[index + direction], day.activities[index]
                else:
                    raise ValueError("无效排序位置")
            elif action == "move":
                raise ValueError("只有景点支持排序")
            else:
                cls = Hotel if slot == "hotel" else Restaurant
                setattr(day, slot, cls(name=name.strip()) if action == "save" else None)
            plan.estimated_total_cost = estimate_cost(plan)
            plan = TripPlan.model_validate(plan.model_dump())
            db.execute("UPDATE trips SET payload=?,updated=CURRENT_TIMESTAMP WHERE id=?", (plan.model_dump_json(), trip_id))
        return plan
