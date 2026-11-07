"""Shared V2 contracts."""
from datetime import date, time, timedelta
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator

def new_id() -> str:
    return str(uuid4())

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

class TripPreferences(Model):
    interests: list[str] = Field(default_factory=list)
    food_preferences: list[str] = Field(default_factory=list)
    travel_pace: Literal["relaxed", "moderate", "intensive"] = "moderate"
    additional_notes: str | None = None

class TripRequest(Model):
    destination: str = Field(min_length=1)
    start_date: date
    end_date: date
    preferences: TripPreferences = Field(default_factory=TripPreferences)

    @model_validator(mode="after")
    def valid_request(self):
        if not self.destination.strip() or self.end_date < self.start_date:
            raise ValueError("目的地不能为空，结束日期不得早于开始日期")
        return self

class OpeningHours(Model):
    day_of_week: int = Field(ge=1, le=7)
    open_time: time
    close_time: time

class PlaceBase(Model):
    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1)
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    description: str | None = None
    source_url: str | None = None

    @model_validator(mode="after")
    def valid_name(self):
        if not self.name.strip():
            raise ValueError("名称不能为空")
        return self

class Restaurant(PlaceBase):
    place_id: str | None = None
    cuisine: list[str] = Field(default_factory=list)
    price_level: str | None = None
    opening_hours: list[OpeningHours] = Field(default_factory=list)

class Activity(PlaceBase):
    place_id: str | None = None
    category: str | None = None
    planned_start_time: time | None = None
    planned_end_time: time | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=0)
    ticket_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    opening_hours: list[OpeningHours] = Field(default_factory=list)

class Hotel(PlaceBase):
    place_id: str | None = None
    price_per_night: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)

class TimelineItem(Model):
    id: str = Field(default_factory=new_id)
    item_type: Literal["activity", "meal", "hotel", "custom"]
    reference_id: str | None = None
    meal_type: Literal["breakfast", "lunch", "dinner"] | None = None
    title: str
    start_time: time | None = None
    end_time: time | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def valid_time(self):
        # All times are destination-local wall times, never UTC instants.
        self.start_time = self.start_time.replace(tzinfo=None) if self.start_time else None
        self.end_time = self.end_time.replace(tzinfo=None) if self.end_time else None
        if self.end_time and not self.start_time:
            raise ValueError("结束时间需要对应开始时间")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValueError("开始时间必须早于结束时间；跨日项目请拆分")
        if (self.item_type == "meal") != (self.meal_type is not None):
            raise ValueError("餐饮时间线必须有有效餐次")
        return self

class DailyPlan(Model):
    day_id: str = Field(default_factory=new_id)
    day_index: int = Field(ge=1)
    date: date
    title: str = ""
    breakfast: Restaurant | None = None
    lunch: Restaurant | None = None
    dinner: Restaurant | None = None
    activities: list[Activity] = Field(default_factory=list)
    hotel: Hotel | None = None
    notes: list[str] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)
    timeline_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_references(self):
        places = {p.id: p for p in [*self.activities, self.breakfast, self.lunch, self.dinner, self.hotel] if p}
        seen, timeline_ids = set(), set()
        for item in self.timeline:
            if item.id in timeline_ids:
                raise ValueError("时间线 ID 重复")
            timeline_ids.add(item.id)
            if not item.reference_id:
                continue
            if item.reference_id not in places or item.reference_id in seen:
                raise ValueError("时间线包含无效或重复地点引用")
            seen.add(item.reference_id)
            place = places[item.reference_id]
            if item.item_type == "meal" and getattr(self, item.meal_type) is not place:
                raise ValueError("餐次引用不一致")
            if item.item_type == "activity" and not isinstance(place, Activity):
                raise ValueError("景点引用类型不一致")
            if item.item_type == "hotel" and place is not self.hotel:
                raise ValueError("酒店引用类型不一致")
        return self

class TripPlan(TripRequest):
    trip_id: str = Field(default_factory=new_id)
    version: int = Field(default=1, ge=1)
    total_days: int = Field(ge=1)
    days: list[DailyPlan] = Field(default_factory=list)
    @model_validator(mode="before")
    @classmethod
    def discard_legacy_cost(cls, data):
        # Accept historical payloads without weakening validation of other fields.
        if isinstance(data, dict) and "estimated_total_cost" in data:
            data = dict(data)
            data.pop("estimated_total_cost")
        return data


    @model_validator(mode="after")
    def valid_days(self):
        n = (self.end_date - self.start_date).days + 1
        if self.total_days != n or len(self.days) != n:
            raise ValueError("行程天数与日期不一致")
        ids = set()
        for i, day in enumerate(self.days):
            if day.date != self.start_date + timedelta(days=i) or day.day_index != i + 1:
                raise ValueError("每日行程必须按日期连续排列")
            for item in filter(None, [day, *day.activities, day.breakfast, day.lunch, day.dinner, day.hotel]):
                key = item.day_id if isinstance(item, DailyPlan) else item.id
                if key in ids:
                    raise ValueError("行程实例 ID 必须唯一")
                ids.add(key)
        return self

def initialize_plan(request: TripRequest) -> TripPlan:
    n = (request.end_date - request.start_date).days + 1
    return TripPlan(**request.model_dump(), total_days=n, days=[
        DailyPlan(day_index=i + 1, date=request.start_date + timedelta(days=i)) for i in range(n)])
