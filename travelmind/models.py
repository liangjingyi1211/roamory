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

class CostCategoryEstimate(Model):
    category: Literal["food", "activities", "hotel", "local_transport"]
    low: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    high: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    basis: str | None = None
    coverage_note: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if (self.low is None) != (self.high is None):
            raise ValueError("费用上下限必须同时提供")
        if self.low is not None and (self.low > self.high or not self.currency):
            raise ValueError("费用区间或币种无效")
        return self

class CostEstimate(Model):
    low: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    high: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    is_complete: bool = False
    categories: list[CostCategoryEstimate] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=lambda: ["flights"])
    missing_information: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if (self.low is None) != (self.high is None):
            raise ValueError("费用上下限必须同时提供")
        if self.low is not None and (self.low > self.high or not self.currency):
            raise ValueError("费用区间或币种无效")
        if self.is_complete and self.low is None:
            raise ValueError("完整估算必须有金额")
        return self

class TripPlan(TripRequest):
    trip_id: str = Field(default_factory=new_id)
    total_days: int = Field(ge=1)
    days: list[DailyPlan] = Field(default_factory=list)
    estimated_total_cost: CostEstimate | None = None

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
