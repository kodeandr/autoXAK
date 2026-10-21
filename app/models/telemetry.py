"""Finite, bounded legacy telemetry input; missing coordinates stay missing."""
import uuid
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

class TelemetryPoint(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    t: float = Field(ge=0)
    ax: float = Field(ge=-200, le=200)
    ay: float = Field(ge=-200, le=200)
    az: float = Field(ge=-200, le=200)
    gx: float = 0.0
    gy: float = 0.0
    gz: float = 0.0
    speed: float = Field(ge=0, le=150)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)

class TripSessionPayload(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=64)
    car_id: str = Field(min_length=1, max_length=100)
    telemetry_stream: list[TelemetryPoint] = Field(min_length=50, max_length=500000)
    board_computer_consumption_l100km: Optional[float] = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def ordered_samples(self):
        times = [point.t for point in self.telemetry_stream]
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("Telemetry timestamps must be strictly increasing")
        if times[-1] - times[0] > 86400:
            raise ValueError("A session cannot exceed 24 hours")
        return self
