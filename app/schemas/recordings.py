"""Validated wire format for durable smartphone recordings (epoch milliseconds)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TIMESTAMP_MS = 253402300799999
MAX_CHUNKS = 120000


class RecordingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class VehicleProfile(RecordingModel):
    vehicle_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    make: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    year: int | None = Field(default=None, ge=1900, le=2100)
    transmission: str | None = Field(default=None, max_length=50)
    fuel_type: Literal["gasoline", "diesel", "unknown"] = "unknown"
    mass_kg: float | None = Field(default=None, ge=200, le=10000)
    displacement_l: float | None = Field(default=None, ge=0.3, le=12)
    turbo: bool | None = None


class DeviceMetadata(RecordingModel):
    app_version: str | None = Field(default=None, max_length=100)
    user_agent: str | None = Field(default=None, max_length=1000)
    placement: str | None = Field(default=None, max_length=300)


class GPSPoint(RecordingModel):
    timestamp_ms: int = Field(gt=0, le=MAX_TIMESTAMP_MS, strict=True)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    speed_mps: float | None = Field(default=None, ge=0, le=150)
    accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    speed_source: Literal["gps", "derived", "missing"]

    @model_validator(mode="after")
    def speed_and_source_agree(self):
        if (self.speed_mps is None) != (self.speed_source == "missing"):
            raise ValueError("speed_source must be missing exactly when speed_mps is null")
        return self


class MotionPoint(RecordingModel):
    timestamp_ms: int = Field(gt=0, le=MAX_TIMESTAMP_MS, strict=True)
    event_timestamp_ms: float | None = Field(default=None, ge=0)
    ax: float | None = None
    ay: float | None = None
    az: float | None = None
    gx: float | None = None
    gy: float | None = None
    gz: float | None = None
    acceleration_includes_gravity: bool
    interval_ms: float | None = Field(default=None, ge=0)


class TripCreate(RecordingModel):
    trip_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    started_at_ms: int = Field(gt=0, le=MAX_TIMESTAMP_MS, strict=True)
    vehicle: VehicleProfile
    device: DeviceMetadata = Field(default_factory=DeviceMetadata)


class TripChunk(RecordingModel):
    gps: list[GPSPoint] = Field(default_factory=list, max_length=1000)
    motion: list[MotionPoint] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def bound_total_points(self):
        if not 1 <= len(self.gps) + len(self.motion) <= 1000:
            raise ValueError("a chunk must contain 1 to 1000 points in total")
        return self


class ControlMeasurement(RecordingModel):
    distance_km: float = Field(gt=0, le=5000)
    average_l_per_100km: float = Field(gt=0, le=100)
    source: Literal["trip_computer", "obd", "fillup"]
    same_interval: bool


class TripComplete(RecordingModel):
    ended_at_ms: int = Field(gt=0, le=MAX_TIMESTAMP_MS, strict=True)
    chunk_count: int = Field(ge=0, le=MAX_CHUNKS, strict=True)
    end_reason: Literal["user", "interrupted", "duration_limit"]
    control: ControlMeasurement | None = None
