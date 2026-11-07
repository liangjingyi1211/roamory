"""Personal notes stored separately from plans; never supplied to an agent."""
import sqlite3
import base64
import binascii
from contextlib import closing
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field, field_validator, model_validator
from .models import Model
from .storage import load_plan
from .config import database_path


MAX_IMAGES = 6
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class JournalImage(Model):
    id: str
    name: str
    mime_type: str
    width: int
    height: int
    size: int


def prepare_image(upload):
    """Verify real raster bytes, retain the original, and make a small preview."""
    if not isinstance(upload, dict) or not isinstance(upload.get("data"), bytes):
        raise ValueError("图片上传数据无效")
    data = upload["data"]
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("每张图片不能超过 5MB")
    try:
        with Image.open(BytesIO(data)) as image:
            kind = image.format
            if kind not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("仅支持 JPG、PNG、WebP 图片")
            if image.width * image.height > 40_000_000:
                raise ValueError("图片尺寸过大，请选择不超过 4000 万像素的图片")
            if getattr(image, "is_animated", False):
                raise ValueError("暂不支持动图，请上传静态图片")
            width, height = image.size
            image.verify()
        with Image.open(BytesIO(data)) as image:
            preview = ImageOps.exif_transpose(image)
            preview.thumbnail((480, 360))
            rgba = preview.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            output = BytesIO()
            background.save(output, format="JPEG", quality=80)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法读取，请选择有效的 JPG、PNG 或 WebP 文件") from exc
    name = str(upload.get("name") or "旅行照片").replace("\\", "/").rsplit("/", 1)[-1][:160]
    metadata = JournalImage(id=str(uuid4()), name=name, mime_type={"JPEG":"image/jpeg", "PNG":"image/png", "WEBP":"image/webp"}[kind],
        width=width, height=height, size=len(data))
    return metadata, data, output.getvalue()


def decode_image(upload):
    if not isinstance(upload, dict) or not isinstance(upload.get("data_url"), str):
        raise ValueError("图片上传数据无效")
    url = upload["data_url"]
    if len(url) > (MAX_IMAGE_BYTES * 4 // 3) + 128:
        raise ValueError("每张图片不能超过 5MB")
    header, separator, encoded = url.partition(",")
    if not separator or header not in {"data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64"}:
        raise ValueError("仅支持 JPG、PNG、WebP 图片")
    try:
        return {"name": upload.get("name"), "data": base64.b64decode(encoded, validate=True)}
    except (ValueError, binascii.Error) as exc:
        raise ValueError("图片上传数据不完整，请重新选择图片") from exc


class TravelJournalEntry(Model):
    id: str
    trip_id: str
    day_id: str
    item_type: Literal["activity", "restaurant", "hotel"]
    item_id: str
    title: str
    content: str = Field(default="", max_length=10000)
    images: list[JournalImage] = Field(default_factory=list, max_length=MAX_IMAGES)
    created_at: datetime
    updated_at: datetime
    revision: int = Field(default=1, ge=1)
    trip_destination: str
    day_date: date
    place_name: str

    @field_validator("content")
    @classmethod
    def trimmed_content(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def has_content(self):
        if not self.content and not self.images:
            raise ValueError("请写下感想或添加图片")
        return self


class JournalStore:
    def __init__(self, path=None):
        self.path = database_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS travel_journals (
                id TEXT PRIMARY KEY, trip_id TEXT NOT NULL, day_id TEXT NOT NULL,
                item_type TEXT NOT NULL, item_id TEXT NOT NULL, payload TEXT NOT NULL,
                revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(trip_id,day_id,item_type,item_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS journal_recent ON travel_journals(updated_at DESC)")
            db.execute("""CREATE TABLE IF NOT EXISTS journal_images (
                id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, data BLOB NOT NULL, preview BLOB NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS journal_image_entry ON journal_images(entry_id)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def list_entries(self, trip_id=None):
        query = "SELECT payload FROM travel_journals"
        args = ()
        if trip_id is not None:
            query += " WHERE trip_id=?"
            args = (trip_id,)
        with closing(self.connect()) as db:
            rows = db.execute(query + " ORDER BY updated_at DESC,id DESC", args).fetchall()
        return [TravelJournalEntry.model_validate_json(row[0]) for row in rows]

    def image_bytes(self, entry_id, image_id, *, preview=False):
        with closing(self.connect()) as db:
            row = db.execute("SELECT preview,data FROM journal_images WHERE entry_id=? AND id=?", (entry_id, image_id)).fetchone()
        return bytes(row[0 if preview else 1]) if row else None

    def workspace_entries(self, trip_id):
        entries = self.list_entries(trip_id)
        result = []
        for entry in entries:
            item = entry.model_dump(mode="json")
            for image in item["images"]:
                data = self.image_bytes(entry.id, image["id"], preview=True)
                if data:
                    image["thumbnail_url"] = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
            result.append(item)
        return result

    def save(self, *, trip_id, day_id, item_type, item_id, content, expected_revision=None,
             new_images=None, keep_image_ids=None):
        if item_type not in {"activity", "restaurant", "hotel"}:
            raise ValueError("不支持的记录类型")
        if not isinstance(content, str) or len(content) > 10000:
            raise ValueError("记录正文不能超过 10000 个字符")
        if new_images is not None and (not isinstance(new_images, list) or len(new_images) > MAX_IMAGES):
            raise ValueError("每条记录最多上传 6 张图片")
        prepared = [prepare_image(upload) for upload in (new_images or [])]
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError("记录版本无效，请刷新后重试")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute("SELECT payload FROM travel_journals WHERE trip_id=? AND day_id=? AND item_type=? AND item_id=?",
                (trip_id, day_id, item_type, item_id)).fetchone()
            old = TravelJournalEntry.model_validate_json(saved[0]) if saved else None
            if (old.revision if old else None) != expected_revision:
                raise ValueError("记录已在其他页面修改，请刷新后再编辑；当前输入未保存")
            old_images = {image.id: image for image in old.images} if old else {}
            keep = list(old_images) if keep_image_ids is None else keep_image_ids
            if not isinstance(keep, list) or any(not isinstance(key, str) for key in keep) or len(set(keep)) != len(keep) or set(keep) - old_images.keys():
                raise ValueError("图片归属无效，请刷新后再编辑")
            images = [old_images[key] for key in keep] + [metadata for metadata, _, _ in prepared]
            if len(images) > MAX_IMAGES:
                raise ValueError("每条记录最多保留 6 张图片")
            if not content.strip() and not images:
                raise ValueError("请写下感想或添加图片")
            row = db.execute("SELECT payload FROM trips WHERE id=?", (trip_id,)).fetchone()
            if row is None:
                raise ValueError("所属行程不存在")
            plan = load_plan(row[0])
            day = next((d for d in plan.days if d.day_id == day_id), None)
            if day is None:
                raise ValueError("所属日期不存在")
            places = day.activities if item_type == "activity" else [day.hotel] if item_type == "hotel" else [day.breakfast, day.lunch, day.dinner]
            place = next((p for p in places if p and p.id == item_id), None)
            if place is None and old is None:
                raise ValueError("地点已被替换或删除，请刷新行程；不会将记录关联到其他地点")
            now = datetime.now(timezone.utc)
            entry = TravelJournalEntry(id=old.id if old else str(uuid4()), trip_id=trip_id, day_id=day_id,
                item_type=item_type, item_id=item_id, title=old.title if old else place.name,
                content=content, images=images, created_at=old.created_at if old else now, updated_at=now,
                revision=old.revision + 1 if old else 1, trip_destination=old.trip_destination if old else plan.destination,
                day_date=old.day_date if old else day.date, place_name=old.place_name if old else place.name)
            db.execute("""INSERT INTO travel_journals(id,trip_id,day_id,item_type,item_id,payload,revision,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at""",
                (entry.id, trip_id, day_id, item_type, item_id, entry.model_dump_json(), entry.revision, now.isoformat()))
            for metadata, data, preview in prepared:
                db.execute("INSERT INTO journal_images(id,entry_id,data,preview) VALUES(?,?,?,?)", (metadata.id, entry.id, data, preview))
            for removed in old_images.keys() - set(keep):
                db.execute("DELETE FROM journal_images WHERE entry_id=? AND id=?", (entry.id, removed))
        return entry


def apply_journal_event(store, trip_id, event):
    if not isinstance(event, dict) or event.get("kind") != "journal" or event.get("trip_id") != trip_id:
        raise ValueError("旅行记录请求无效")
    uploads = event.get("new_images", [])
    if not isinstance(uploads, list) or len(uploads) > MAX_IMAGES:
        raise ValueError("每条记录最多上传 6 张图片")
    return store.save(trip_id=trip_id, day_id=event.get("day_id"), item_type=event.get("item_type"),
        item_id=event.get("item_id"), content=event.get("content"), expected_revision=event.get("revision"),
        new_images=[decode_image(upload) for upload in uploads], keep_image_ids=event.get("keep_image_ids"))
