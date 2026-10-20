"""Durable, authenticated uploads independent of sensor frequency and connection."""

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import require_user
from app.core.database import get_db
from app.models.db_models import User
from app.models.recordings import RecordingChunk, RecordingVehicle, TripRecording, utcnow
from app.schemas.recordings import MAX_CHUNKS, ControlMeasurement, TripChunk, TripComplete, TripCreate, VehicleProfile


router = APIRouter(prefix="/api/v2", tags=["Trip recordings"])
logger = logging.getLogger(__name__)
MAX_SESSION_MS = 24 * 60 * 60 * 1000


def _gps_payload_query(trip_id: str):
    # JSON indexing is translated to SQLite JSON_EXTRACT / PostgreSQL ->.
    # Projecting the stream in the database avoids decoding high-rate motion.
    return (select(RecordingChunk.payload["gps"])
            .where(RecordingChunk.trip_id == trip_id, RecordingChunk.gps_count > 0)
            .order_by(RecordingChunk.sequence))


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def trip_detail(trip: TripRecording) -> dict:
    return {
        "schema_version": 2,
        "trip_id": trip.id,
        "started_at_ms": trip.started_at_ms,
        "ended_at_ms": trip.ended_at_ms,
        "status": trip.status,
        "end_reason": trip.end_reason,
        "chunk_count": trip.chunk_count,
        "vehicle": trip.vehicle,
        "device": trip.device,
        "control": trip.control,
        "fuel_estimate": trip.fuel_estimate,
        "savings_status": "unavailable",
        "engine_running_assumption": "from_start_until_end_including_stops",
    }


async def owned_trip(db: AsyncSession, user: User, trip_id: str, *, lock: bool = False) -> TripRecording:
    if lock:
        # A write acquires a row lock on PostgreSQL and a write lock on SQLite.
        # All chunk/finalization writes serialize here, including their retries.
        result = await db.execute(update(TripRecording).where(TripRecording.id == trip_id, TripRecording.user_id == user.id).values(revision=TripRecording.revision + 1))
        if result.rowcount != 1:
            raise HTTPException(404, "Trip not found")
    trip = (await db.execute(select(TripRecording).where(TripRecording.id == trip_id, TripRecording.user_id == user.id).execution_options(populate_existing=True))).scalar_one_or_none()
    if trip is None:
        raise HTTPException(404, "Trip not found")
    return trip


@router.get("/vehicle")
async def get_vehicle(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    row = await db.get(RecordingVehicle, user.id)
    return {"vehicle": row.profile if row else None}


@router.put("/vehicle")
async def put_vehicle(body: VehicleProfile, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    owner_id = user.id
    # Lock a stable owner row even before their first profile exists.
    await db.execute(select(User.id).where(User.id == owner_id).with_for_update())
    data = body.model_dump(mode="json")
    existing = await db.get(RecordingVehicle, owner_id)
    if existing is None:
        db.add(RecordingVehicle(user_id=owner_id, profile=data))
    else:
        existing.profile, existing.updated_at = data, utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Concurrent first saves on SQLite: a single-row update is atomic.
        await db.execute(update(RecordingVehicle).where(RecordingVehicle.user_id == owner_id).values(profile=data, updated_at=utcnow()))
        await db.commit()
    return {"vehicle": data}


@router.post("/trips")
async def create_trip(body: TripCreate, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    owner_id = user.id
    data = body.model_dump(mode="json")
    digest = payload_hash(data)
    trip = await db.get(TripRecording, body.trip_id)
    if trip is None:
        trip = TripRecording(id=body.trip_id, user_id=owner_id, started_at_ms=body.started_at_ms, vehicle=data["vehicle"], device=data["device"], create_hash=digest)
        db.add(trip)
        try:
            await db.commit()
            await db.refresh(trip)
            return trip_detail(trip)
        except IntegrityError:
            await db.rollback()
            trip = await db.get(TripRecording, body.trip_id)
    if trip is None or trip.user_id != owner_id or trip.create_hash != digest:
        raise HTTPException(409, "Trip identifier already exists with a different request")
    return trip_detail(trip)


@router.put("/trips/{trip_id}/chunks/{sequence}")
async def upload_chunk(body: TripChunk, trip_id: str, sequence: int = Path(ge=0, lt=MAX_CHUNKS), user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    trip = await owned_trip(db, user, trip_id, lock=True)
    data = body.model_dump(mode="json")
    digest = payload_hash(data)
    existing = await db.get(RecordingChunk, (trip.id, sequence))
    if existing is not None:
        if existing.payload_hash != digest:
            raise HTTPException(409, "Chunk sequence already contains different data")
        await db.commit()
        return {"trip_id": trip.id, "sequence": sequence, "status": "stored", "duplicate": True}
    if trip.status != "recording":
        raise HTTPException(409, "A completed trip cannot accept new chunks")
    timestamps = [p["timestamp_ms"] for p in data["gps"] + data["motion"]]
    if min(timestamps) < trip.started_at_ms or max(timestamps) > trip.started_at_ms + MAX_SESSION_MS:
        raise HTTPException(422, "Point timestamp is outside the maximum recording interval")
    db.add(RecordingChunk(trip_id=trip.id, sequence=sequence, payload=data, payload_hash=digest, min_timestamp_ms=min(timestamps), max_timestamp_ms=max(timestamps), gps_count=len(data["gps"]), motion_count=len(data["motion"])))
    trip.chunk_count += 1
    await db.commit()
    return {"trip_id": trip.id, "sequence": sequence, "status": "stored", "duplicate": False}


@router.post("/trips/{trip_id}/complete")
async def complete_trip(body: TripComplete, trip_id: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    trip = await owned_trip(db, user, trip_id, lock=True)
    digest = payload_hash(body.model_dump(mode="json"))
    if trip.status == "completed":
        if trip.complete_hash != digest:
            raise HTTPException(409, "Trip was completed with a different request")
        await db.commit()
        return trip_detail(trip)
    if not 0 <= body.ended_at_ms - trip.started_at_ms <= MAX_SESSION_MS:
        raise HTTPException(422, "Trip duration must be nonnegative and at most 24 hours")
    chunks = (await db.execute(select(RecordingChunk.sequence, RecordingChunk.max_timestamp_ms, RecordingChunk.gps_count).where(RecordingChunk.trip_id == trip.id).order_by(RecordingChunk.sequence))).all()
    if [c.sequence for c in chunks] != list(range(body.chunk_count)):
        raise HTTPException(409, "Upload every chunk from 0 through chunk_count - 1 before completion")
    if any(c.max_timestamp_ms > body.ended_at_ms for c in chunks):
        raise HTTPException(422, "A point is later than the requested trip end")
    try:
        from app.services.fuel_prediction import MAX_GPS_SAMPLES, predict_fuel
        gps_count = sum(c.gps_count for c in chunks)
        gps = None
        if gps_count <= MAX_GPS_SAMPLES:
            gps = []
            rows = await db.stream_scalars(_gps_payload_query(trip.id))
            try:
                async for samples in rows:
                    gps.extend(samples)
            finally:
                await rows.close()
        # None deliberately invokes the service's invalid-input result for an
        # oversized recording, without allocating its GPS or motion arrays.
        estimate = await run_in_threadpool(predict_fuel, gps, trip.vehicle, trip.started_at_ms, body.ended_at_ms)
        if not isinstance(estimate, dict):
            raise ValueError("Fuel estimate must be an object")
        if gps_count > MAX_GPS_SAMPLES:
            estimate.setdefault("quality_flags", []).append("gps_sample_limit_exceeded")
            estimate["gps_sample_counts"] = {"received": gps_count}
        # Reject accidental non-serializable model output before touching the trip.
        json.dumps(estimate, allow_nan=False)
    except Exception:
        logger.exception("Fuel estimate failed for recording %s; raw data retained", trip.id)
        estimate = {"status": "model_unavailable", "estimated_fuel_l": None, "complete_trip_fuel_l": None, "quality_flags": ["prediction_failed"]}
    trip.ended_at_ms, trip.end_reason = body.ended_at_ms, body.end_reason
    trip.status, trip.complete_hash = "completed", digest
    trip.control = body.control.model_dump(mode="json") if body.control else None
    trip.fuel_estimate, trip.chunk_count = estimate, body.chunk_count
    await db.commit()
    return trip_detail(trip)


@router.get("/trips")
async def list_trips(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    trips = (await db.execute(select(TripRecording).where(TripRecording.user_id == user.id).order_by(TripRecording.started_at_ms.desc(), TripRecording.id).offset(offset).limit(limit))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(TripRecording).where(TripRecording.user_id == user.id))).scalar_one()
    return {"trips": [trip_detail(t) for t in trips], "total": total, "limit": limit, "offset": offset}


@router.get("/trips/{trip_id}")
async def get_trip(trip_id: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return trip_detail(await owned_trip(db, user, trip_id))


@router.get("/trips/{trip_id}/export")
async def export_trip(trip_id: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    trip = await owned_trip(db, user, trip_id)
    # Capture immutable chunk identities before responding. This also freezes an
    # active recording's export: later uploads, including out-of-order sequences,
    # cannot appear in only one of its independently streamed sensor arrays.
    chunks = (await db.execute(select(
        RecordingChunk.sequence, RecordingChunk.payload_hash,
        RecordingChunk.gps_count, RecordingChunk.motion_count,
    ).where(RecordingChunk.trip_id == trip.id).order_by(RecordingChunk.sequence))).all()
    data = trip_detail(trip)
    data["units"] = {"speed": "m/s", "acceleration": "m/s2", "gyro": "deg/s", "gyro_axes": "x=beta,y=gamma,z=alpha", "timestamps": "epoch_ms", "coordinates": "degrees", "accuracy": "m", "event_timestamp": "browser_event_ms", "interval": "ms"}
    data["chunk_count"] = len(chunks)
    point_counts = {"gps": sum(c.gps_count for c in chunks), "motion": sum(c.motion_count for c in chunks)}
    # FastAPI 0.110 closes yielded request dependencies before sending the body.
    # A stream must own its session rather than retain the request's db object.
    export_sessions = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    def encode(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    async def content():
        yield encode(data)[:-1]
        yield ',"chunks":['
        for index, chunk in enumerate(chunks):
            yield ("," if index else "") + encode({
                "sequence": chunk.sequence, "sha256": chunk.payload_hash,
                "gps_count": chunk.gps_count, "motion_count": chunk.motion_count,
            })
        yield "]"
        async with export_sessions() as export_db:
            for field in ("gps", "motion"):
                yield ',"' + field + '":['
                separator = ""
                # Bound query parameters and decoded payloads independently of
                # trip length; projecting one JSON stream avoids loading the other.
                for offset in range(0, len(chunks), 100):
                    sequences = [c.sequence for c in chunks[offset:offset + 100]]
                    query = (select(RecordingChunk.payload[field])
                             .where(RecordingChunk.trip_id == trip_id,
                                    RecordingChunk.sequence.in_(sequences))
                             .order_by(RecordingChunk.sequence)
                             .execution_options(yield_per=1))
                    rows = await export_db.stream_scalars(query)
                    try:
                        async for points in rows:
                            if points:
                                yield separator + encode(points)[1:-1]
                                separator = ","
                    finally:
                        await rows.close()
                yield "]"
        yield ',"point_counts":' + encode(point_counts) + "}"

    return StreamingResponse(content(), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="autoxak-trip-{trip.id}.json"',
    })


@router.put("/trips/{trip_id}/control")
async def update_control(body: ControlMeasurement, trip_id: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    trip = await owned_trip(db, user, trip_id, lock=True)
    if trip.status != "completed":
        raise HTTPException(409, "Control measurement can be attached after completion")
    trip.control = body.model_dump(mode="json")
    await db.commit()
    return trip_detail(trip)
