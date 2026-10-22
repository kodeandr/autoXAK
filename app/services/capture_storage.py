"""Persist real GPS/IMU independently, including captures unsuitable for scoring."""
import hashlib
import json
import math
from datetime import datetime, timezone

from app.models.db_models import Trip
from app.models.recordings import RecordingChunk, TripRecording


def capture_quality(payload):
    raw = payload.raw_recording
    good_gps = [point for point in raw.gps if point.accuracy_m is not None and point.accuracy_m <= 50]
    duration = (raw.ended_at_ms - raw.started_at_ms) / 1000
    stream = payload.telemetry_stream
    reasons = []
    if len(stream) < 50 or len(raw.motion) < 50:
        reasons.append("insufficient_motion_samples")
    elif any(b.t - a.t > 2 for a, b in zip(stream, stream[1:])):
        reasons.append("motion_interrupted")
    if len(good_gps) < 2:
        reasons.append("insufficient_accurate_gps")
    if stream:
        coverage = (stream[-1].t - stream[0].t) / duration if duration > 0 else 0
        if coverage < 0.9:
            reasons.append("incomplete_motion_coverage")
        if sum(point.lat is not None and point.lon is not None for point in stream) / len(stream) < 0.9:
            reasons.append("incomplete_gps_coverage")
    if raw.end_reason == "interrupted":
        reasons.append("recording_interrupted")
    available = not reasons
    return {"motion_samples": len(raw.motion), "gps_samples": len(raw.gps), "accurate_gps_samples": len(good_gps),
            "permissions": raw.permissions, "analysis_status": "available" if available else "unavailable",
            "quality_flags": reasons, "style_metrics_available": available,
            "mode": "gps_imu" if raw.gps and raw.motion else "gps_only" if raw.gps else "motion_only" if raw.motion else "no_sensor_data"}


def measured_distance(raw):
    """Integrate actual adjacent GPS fixes; missing intervals are never filled."""
    distance = 0.0
    for previous, current in zip(raw.gps, raw.gps[1:]):
        if any(p.accuracy_m is None or p.accuracy_m > 50 for p in (previous, current)):
            continue
        dt = (current.timestamp_ms - previous.timestamp_ms) / 1000
        if not 0 < dt <= 10:
            continue
        speeds = [previous.speed_mps, current.speed_mps]
        if all(speed is not None and 0 <= speed <= 75 for speed in speeds):
            distance += sum(speeds) * 0.5 * dt / 1000
        else:
            latitude = math.radians(current.latitude - previous.latitude)
            longitude = math.radians(current.longitude - previous.longitude)
            a = math.sin(latitude / 2) ** 2 + math.cos(math.radians(previous.latitude)) * math.cos(math.radians(current.latitude)) * math.sin(longitude / 2) ** 2
            meters = 6371000 * 2 * math.atan2(math.sqrt(min(1, a)), math.sqrt(max(0, 1 - a)))
            if 3 <= meters <= 75 * dt:
                distance += meters / 1000
    return distance


async def store_raw_capture(db, payload, trip_id, digest, vehicle, quality):
    raw = payload.raw_recording
    recording = TripRecording(id=trip_id, user_id=payload.user_id, started_at_ms=raw.started_at_ms,
                              ended_at_ms=raw.ended_at_ms, status="completed", end_reason=raw.end_reason,
                              vehicle=vehicle, device=raw.device.model_dump(mode="json"),
                              create_hash=digest, complete_hash=digest,
                              fuel_estimate={"status": "not_calculated", "estimated_fuel_l": None,
                                             "quality_flags": quality["quality_flags"], "capture_quality": quality})
    db.add(recording)
    await db.flush()
    sequence = 0
    # Keep each chunk bounded; preserve the two original time bases and missing values.
    for name, stream in (("gps", raw.gps), ("motion", raw.motion)):
        for offset in range(0, len(stream), 1000):
            points = [point.model_dump(mode="json") for point in stream[offset:offset + 1000]]
            body = {"gps": [], "motion": [], name: points}
            checksum = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            db.add(RecordingChunk(trip_id=trip_id, sequence=sequence, payload=body, payload_hash=checksum,
                                  min_timestamp_ms=points[0]["timestamp_ms"], max_timestamp_ms=points[-1]["timestamp_ms"],
                                  gps_count=len(points) if name == "gps" else 0,
                                  motion_count=len(points) if name == "motion" else 0))
            sequence += 1
    recording.chunk_count = sequence


def unscored_trip(payload, trip_id, digest, owner, quality):
    raw = payload.raw_recording
    duration = (raw.ended_at_ms - raw.started_at_ms) / 1000
    distance = round(measured_distance(raw), 3)
    result = {"session_id": trip_id, "duration_seconds": duration, "distance_km": distance,
              "oil_wear_percent": 0.0, "cost_savings_rub": 0.0, "status": "RECORDED",
              "metrics": {"capture_quality": quality}}
    trip = Trip(id=trip_id, client_session_id=payload.session_id, user_id=owner.id, car_id=payload.car_id,
                created_at=datetime.fromtimestamp(raw.started_at_ms / 1000, tz=timezone.utc),
                request_hash=digest, calculation_result=result, duration_seconds=duration, distance_km=distance,
                equivalent_engine_hours=0.0, oil_wear_percent=0.0,
                board_computer_consumption_l100km=payload.board_computer_consumption_l100km)
    owner.total_trips = int(owner.total_trips or 0) + 1
    owner.total_distance_km = round(float(owner.total_distance_km or 0) + distance, 3)
    return trip, result
