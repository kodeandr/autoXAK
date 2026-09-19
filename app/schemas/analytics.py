from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class TripSummaryItem(BaseModel):
    session_id: str
    created_at: datetime
    duration_seconds: float
    distance_km: float
    idle_ratio: float
    oil_wear_percent: float
    total_savings_rub: float


class TripsPeriodSummaryMetrics(BaseModel):
    total_trips: int = Field(..., description="Количество поездок за период")
    total_distance_km: float = Field(..., description="Суммарная дистанция, км")
    total_duration_hours: float = Field(..., description="Суммарное время за рулем, ч")
    total_savings_rub: float = Field(..., description="Совокупная экономия, ₽")
    total_oil_wear_percent: float = Field(..., description="Накопленный износ масла, %")
    avg_speed_kmh: float = Field(..., description="Средняя скорость движения, км/ч")
    avg_idle_ratio: float = Field(..., description="Средняя доля пробок / холостого хода")


class PeriodAnalyticsResponse(BaseModel):
    user_id: str
    start_date: datetime
    end_date: datetime
    summary: TripsPeriodSummaryMetrics
    trips: List[TripSummaryItem]