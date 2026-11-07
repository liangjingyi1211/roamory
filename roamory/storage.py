"""Local single-user SQLite persistence and atomic, model-free edits."""
import sqlite3
from datetime import time
from .models import TripPlan, Restaurant, Activity, Hotel
from .timeline import migrate_plan, sync_day
from .models import TimelineItem
from .config import database_path

def load_plan(payload):
    plan = migrate_plan(TripPlan.model_validate_json(payload))
    return plan

class TripStore:
    def __init__(self, path=None):
        self.path = database_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS trips (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated TEXT DEFAULT CURRENT_TIMESTAMP, version INTEGER NOT NULL DEFAULT 1)")
            if "version" not in {row[1] for row in db.execute("PRAGMA table_info(trips)")}:
                backup = self.path.with_suffix(".v3-backup.sqlite3")
                if not backup.exists():
                    with sqlite3.connect(backup) as target:
                        db.backup(target)
                db.execute("ALTER TABLE trips ADD COLUMN version INTEGER NOT NULL DEFAULT 1")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, plan: TripPlan):
        plan = migrate_plan(TripPlan.model_validate(plan.model_dump()))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM trips WHERE id=?", (plan.trip_id,)).fetchone()
            if row and row[0] != plan.version:
                raise VersionConflict("行程已被修改，请刷新后重新提交")
            if row:
                plan.version += 1
            db.execute("INSERT INTO trips(id,payload,version) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,version=excluded.version,updated=CURRENT_TIMESTAMP",
                (plan.trip_id, plan.model_dump_json(), plan.version))

    def get(self, trip_id: str) -> TripPlan:
        with self.connect() as db:
            row = db.execute("SELECT payload,version FROM trips WHERE id=?", (trip_id,)).fetchone()
        if row is None:
            raise ValueError("行程不存在")
        plan = load_plan(row[0])
        plan.version = row[1]
        return plan

    def list_trips(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM trips ORDER BY updated DESC,rowid DESC").fetchall()
        return [(key, f'{(p := TripPlan.model_validate_json(payload)).destination} · {p.start_date} — {p.end_date}') for key, payload in rows]

    def edit(self, trip_id, day_id, slot, action, *, name=None, item_id=None, direction=None,
             start_time=None, end_time=None, expected_version=None):
        """Read-modify-write under one transaction; never calls generation or Places."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload,version FROM trips WHERE id=?", (trip_id,)).fetchone()
            if row is None:
                raise ValueError("行程不存在")
            plan = load_plan(row[0])
            plan.version = row[1]
            if expected_version is not None and row[1] != expected_version:
                raise VersionConflict("行程已被修改，请刷新后重试")
            day = next((d for d in plan.days if d.day_id == day_id), None)
            if day is None:
                raise ValueError("日期不存在")
            if slot not in {"breakfast", "lunch", "dinner", "hotel", "activities", "timeline"}:
                raise ValueError("非法槽位")
            if action not in {"save", "delete", "move", "time"}:
                raise ValueError("非法操作")
            if action == "save" and (not name or not name.strip()):
                raise ValueError("名称不能为空")
            if slot == "timeline":
                if action != "time":
                    raise ValueError("时间线只接受时间编辑")
                index = next((i for i, t in enumerate(day.timeline) if t.id == item_id), None)
                if index is None:
                    raise ValueError("时间线项目不存在")
                values = day.timeline[index].model_dump()
                values.update(start_time=start_time or None, end_time=end_time or None)
                day.timeline[index] = TimelineItem.model_validate(values)
            elif action == "time":
                raise ValueError("时间编辑需要时间线槽位")
            elif slot == "activities":
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
                    time_slots = sorted([(a.planned_start_time, a.planned_end_time) for a in day.activities],
                        key=lambda pair: (pair[0] is None, pair[0] or time.min))
                    day.activities[index], day.activities[index + direction] = day.activities[index + direction], day.activities[index]
                    by_ref = {t.reference_id: t for t in day.timeline}
                    for activity, (start, end) in zip(day.activities, time_slots):
                        if activity.id in by_ref:
                            by_ref[activity.id].start_time, by_ref[activity.id].end_time = start, end
                else:
                    raise ValueError("无效排序位置")
            elif action == "move":
                raise ValueError("只有景点支持排序")
            else:
                cls = Hotel if slot == "hotel" else Restaurant
                setattr(day, slot, cls(name=name.strip()) if action == "save" else None)
            sync_day(day)
            plan = TripPlan.model_validate(plan.model_dump())
            plan.version += 1
            db.execute("UPDATE trips SET payload=?,version=?,updated=CURRENT_TIMESTAMP WHERE id=?", (plan.model_dump_json(), plan.version, trip_id))
        return plan

    def apply_scoped_patch(self, base, day_id, changes, *, reschedule=False):
        """Only whitelisted day fields, optimistic lock, one atomic write."""
        if not changes or set(changes) - {"breakfast", "lunch", "dinner", "activities", "hotel"}:
            raise ValueError("局部补丁包含越界字段")
        plan = base.model_copy(deep=True)
        day = next((d for d in plan.days if d.day_id == day_id), None)
        if day is None:
            raise ValueError("日期不存在")
        for field, value in changes.items():
            setattr(day, field, value)
        if reschedule:
            from .timeline import schedule_day
            schedule_day(day)
        else:
            # New AI activities may carry suggestions. Preserve every existing
            # timeline time; only attach times for genuinely new references.
            from .timeline import timeline_id
            refs = {t.reference_id for t in day.timeline}
            for activity in day.activities:
                if activity.id not in refs:
                    day.timeline.append(TimelineItem(id=timeline_id(day, "activity", activity.id),
                        item_type="activity", reference_id=activity.id, title=activity.name,
                        start_time=activity.planned_start_time, end_time=activity.planned_end_time,
                        notes="AI 建议当地时间，未验证开放时间"))
            sync_day(day)
        plan = TripPlan.model_validate(plan.model_dump())
        # Version check occurs inside save's write transaction, after all remote work.
        self.save(plan)
        return self.get(plan.trip_id)

class VersionConflict(ValueError):
    pass
