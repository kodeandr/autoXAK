from pydantic import BaseModel
import uuid

class TripCalculationResult(BaseModel):
    session_id: uuid.UUID
    duration_seconds: float
    distance_km: float
    oil_wear_percent: float
    cost_savings_rub: float
    status: str = "PROCESSED"