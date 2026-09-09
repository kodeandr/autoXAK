from pydantic import BaseModel, Field
import uuid
from typing import List, Optional
from datetime import datetime

class TripCalculationResult(BaseModel):
    session_id: uuid.UUID
    duration_seconds: float
    distance_km: float
    oil_wear_percent: float
    cost_savings_rub: float
    status: str = "PROCESSED"

class DashboardResponse(BaseModel):
    user_id: str
    month_savings_rub: float = Field(..., description="Накоплено в виртуальной копилке за текущий месяц (₽)")
    oil_remaining_percent: float = Field(..., description="Остаточный ресурс моторного масла (100% - накопленный износ)")
    ghost_twin_status: str = Field(default="Лихач-новичок (Средняя сложность)")
    cpa_recommended: bool = Field(..., description="Триггер CPA-предложения (активно при ресурсе <= 10%)")
    cpa_offer_text: Optional[str] = None

class TripSummaryItem(BaseModel):
    session_id: uuid.UUID
    created_at: datetime
    duration_seconds: float
    distance_km: float
    total_savings_rub: float
    oil_wear_percent: float

class TripHistoryResponse(BaseModel):
    user_id: str
    total_trips: int
    trips: List[TripSummaryItem]