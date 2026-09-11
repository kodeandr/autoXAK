from pydantic import BaseModel, Field
from typing import List, Optional
import uuid

class TelemetryPoint(BaseModel):
    t: float = Field(..., description="Временная метка эпохи UNIX в секундах")
    ax: float = Field(..., description="Продольное ускорение (м/с^2)")
    ay: float = Field(..., description="Поперечное ускорение (м/с^2)")
    az: float = Field(..., description="Вертикальное ускорение с учетом g (м/с^2)")
    gx: float = Field(default=0.0, description="Угловая скорость X (рад/с)")
    gy: float = Field(default=0.0, description="Угловая скорость Y (рад/с)")
    gz: float = Field(default=0.0, description="Угловая скорость Z (рад/с)")
    speed: float = Field(..., description="Мгновенная скорость по GPS (м/с)")
    lat: Optional[float] = Field(default=55.751244, description="Широта GPS")
    lon: Optional[float] = Field(default=37.618423, description="Долгота GPS")

class TripSessionPayload(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    car_id: str
    telemetry_stream: List[TelemetryPoint]