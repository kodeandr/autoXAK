"""Finite, bounded legacy telemetry input; missing coordinates stay missing."""
import uuid
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.recordings import DeviceMetadata, GPSPoint, MotionPoint, MAX_TIMESTAMP_MS


class RawCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    started_at_ms: int = Field(gt=0, le=MAX_TIMESTAMP_MS, strict=True)
    ended_at_ms: int = Field(gt=0, le=MAX_TIMESTAMP_MS, strict=True)
    gps: list[GPSPoint] = Field(default_factory=list, max_length=100000)
    motion: list[MotionPoint] = Field(default_factory=list, max_length=500000)
    device: DeviceMetadata = Field(default_factory=DeviceMetadata)
    permissions: dict[Literal["motion", "geolocation"], Literal["unknown", "granted", "denied", "unsupported", "unavailable"]] = Field(default_factory=dict)
    end_reason: Literal["user", "interrupted", "duration_limit"] = "user"

    @model_validator(mode="after")
    def bounded_streams(self):
        if not 0 <= self.ended_at_ms - self.started_at_ms <= 86400000:
            raise ValueError("A recording must last between zero and 24 hours")
        for stream in (self.gps, self.motion):
            previous = self.started_at_ms - 1
            for point in stream:
                if not self.started_at_ms <= point.timestamp_ms <= self.ended_at_ms or point.timestamp_ms <= previous:
                    raise ValueError("Raw points must be ordered and inside the recording interval")
                previous = point.timestamp_ms
        return self

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
    telemetry_stream: list[TelemetryPoint] = Field(default_factory=list, max_length=500000)
    raw_recording: Optional[RawCapture] = None
    board_computer_consumption_l100km: Optional[float] = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def ordered_samples(self):
        times = [point.t for point in self.telemetry_stream]
        if self.raw_recording is None and len(times) < 50:
            raise ValueError("At least 50 telemetry points or a raw recording are required")
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("Telemetry timestamps must be strictly increasing")
        if times and times[-1] - times[0] > 86400:
            raise ValueError("A session cannot exceed 24 hours")
        if self.raw_recording and times and (times[0] * 1000 < self.raw_recording.started_at_ms - 1 or times[-1] * 1000 > self.raw_recording.ended_at_ms + 1):
            raise ValueError("Derived telemetry must be inside its raw recording interval")
        return self


class TelemetryPreviewPayload(TripSessionPayload):
    """Bounded, non-persistent input for a provisional calculation."""
    telemetry_stream: list[TelemetryPoint] = Field(min_length=50, max_length=6000)
    raw_recording: None = None
    sampling_stride: int = Field(default=1, ge=1, le=10000)
