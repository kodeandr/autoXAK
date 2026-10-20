"""Recording API tests against a real isolated SQLite database."""

import asyncio
import copy
import json
import math
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.recordings import _gps_payload_query, export_trip, payload_hash, router, trip_detail
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.models.db_models import User
from app.models.recordings import RecordingChunk, RecordingVehicle, TripRecording
from app.schemas.recordings import TripChunk, TripComplete


START = 1800000000000
PROFILE = {"vehicle_id": "audi-pilot", "make": "Audi", "model": "A4", "fuel_type": "gasoline", "mass_kg": 1500, "displacement_l": 1.4, "turbo": True}


def gps(offset=1000, speed=5):
    return {"timestamp_ms": START + offset, "latitude": 55.75, "longitude": 37.6, "speed_mps": speed, "accuracy_m": 5, "speed_source": "missing" if speed is None else "gps"}


def motion(offset=1000):
    return {"timestamp_ms": START + offset, "event_timestamp_ms": offset, "ax": 0.1, "ay": 0.2, "az": 9.8, "gx": 1, "gy": 2, "gz": 3, "acceleration_includes_gravity": True, "interval_ms": 20}


def creation(trip_id="trip-1"):
    return {"trip_id": trip_id, "started_at_ms": START, "vehicle": copy.deepcopy(PROFILE), "device": {"app_version": "test"}}


@pytest_asyncio.fixture
async def recording_api(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "recordings.sqlite"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        session.add_all([User(id="alice", is_guest=True), User(id="bob", is_guest=True)])
        await session.commit()

    calls = []

    def fake_prediction(points, profile, start, end):
        calls.append((points, profile, start, end))
        return {"status": "research_estimate", "estimated_fuel_l": 0.123, "complete_trip_fuel_l": None, "coverage_fraction": 0.5, "quality_flags": ["test_prediction"]}

    monkeypatch.setattr("app.services.fuel_prediction.predict_fuel", fake_prediction)
    app = FastAPI()
    app.include_router(router)

    async def override_database():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_database
    headers = {"Authorization": "Bearer " + create_access_token("alice")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        yield client, sessions, calls
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_recording_retry_snapshot_and_export(recording_api):
    client, sessions, calls = recording_api
    assert (await client.put("/api/v2/vehicle", json=PROFILE)).status_code == 200
    assert (await client.get("/api/v2/vehicle")).json()["vehicle"]["mass_kg"] == 1500
    body = creation()
    assert (await client.post("/api/v2/trips", json=body)).status_code == 200
    assert (await client.post("/api/v2/trips", json=body)).status_code == 200
    changed = copy.deepcopy(PROFILE)
    changed["mass_kg"] = 1700
    await client.put("/api/v2/vehicle", json=changed)
    # More than the old 500-point truncation limit, and two independent streams.
    chunks = [{"gps": [gps(i + 1) for i in range(600)], "motion": [motion(500)]}, {"gps": [gps(2000, None)], "motion": [motion(2100)]}]
    for i, chunk in enumerate(chunks):
        first = await client.put(f"/api/v2/trips/trip-1/chunks/{i}", json=chunk)
        retry = await client.put(f"/api/v2/trips/trip-1/chunks/{i}", json=chunk)
        assert first.status_code == retry.status_code == 200
        assert retry.json()["duplicate"] is True
    complete = {"ended_at_ms": START + 10000, "chunk_count": 2, "end_reason": "user"}
    first = await client.post("/api/v2/trips/trip-1/complete", json=complete)
    retry = await client.post("/api/v2/trips/trip-1/complete", json=complete)
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert first.json()["fuel_estimate"]["estimated_fuel_l"] == 0.123
    assert first.json()["savings_status"] == "unavailable"
    assert len(calls) == 1
    assert calls[0][1]["mass_kg"] == 1500  # Frozen at recording start.
    exported = (await client.get("/api/v2/trips/trip-1/export")).json()
    assert exported["point_counts"] == {"gps": 601, "motion": 2}
    assert exported["gps"][-1]["speed_mps"] is None
    assert exported["motion"][0]["gx"] == 1
    assert len(exported["chunks"]) == 2
    assert exported["units"]["gyro"] == "deg/s"
    assert exported["units"]["gyro_axes"] == "x=beta,y=gamma,z=alpha"
    assert exported["units"]["timestamps"] == "epoch_ms"
    async with sessions() as session:
        user = await session.get(User, "alice")
        assert user.total_savings_rub == 0 and user.total_trips == 0


@pytest.mark.asyncio
async def test_strict_auth_and_owner_isolation(recording_api):
    client, _, _ = recording_api
    await client.post("/api/v2/trips", json=creation())
    for header in ["", "Bearer malformed", "Bearer " + create_access_token("missing"), "Bearer " + create_access_token("alice", expires_delta=timedelta(seconds=-30))]:
        assert (await client.get("/api/v2/trips", headers={"Authorization": header})).status_code == 401
    bob = {"Authorization": "Bearer " + create_access_token("bob")}
    for endpoint in ["/api/v2/trips/trip-1", "/api/v2/trips/trip-1/export"]:
        assert (await client.get(endpoint, headers=bob)).status_code == 404
    assert (await client.put("/api/v2/trips/trip-1/chunks/0", json={"gps": [gps()]}, headers=bob)).status_code == 404
    assert (await client.post("/api/v2/trips/trip-1/complete", json={"ended_at_ms": START + 5000, "chunk_count": 0, "end_reason": "user"}, headers=bob)).status_code == 404
    assert (await client.get("/api/v2/trips", headers=bob)).json()["total"] == 0


@pytest.mark.asyncio
async def test_conflicts_gaps_and_time_boundaries(recording_api):
    client, _, calls = recording_api
    await client.post("/api/v2/trips", json=creation())
    changed = creation()
    changed["started_at_ms"] += 1
    assert (await client.post("/api/v2/trips", json=changed)).status_code == 409
    chunk = {"gps": [gps(4000)]}
    assert (await client.put("/api/v2/trips/trip-1/chunks/1", json=chunk)).status_code == 200
    completion = {"ended_at_ms": START + 5000, "chunk_count": 2, "end_reason": "user"}
    assert (await client.post("/api/v2/trips/trip-1/complete", json=completion)).status_code == 409
    assert (await client.put("/api/v2/trips/trip-1/chunks/1", json={"gps": [gps(4001)]})).status_code == 409
    assert (await client.put("/api/v2/trips/trip-1/chunks/0", json={"gps": [gps(-1)]})).status_code == 422
    assert (await client.put("/api/v2/trips/trip-1/chunks/0", json={"gps": [gps(1000)]})).status_code == 200
    assert (await client.post("/api/v2/trips/trip-1/complete", json={**completion, "ended_at_ms": START + 3000})).status_code == 422
    assert (await client.post("/api/v2/trips/trip-1/complete", json={**completion, "ended_at_ms": START + 86400001})).status_code == 422
    assert (await client.post("/api/v2/trips/trip-1/complete", json=completion)).status_code == 200
    assert (await client.post("/api/v2/trips/trip-1/complete", json={**completion, "end_reason": "interrupted"})).status_code == 409
    assert (await client.put("/api/v2/trips/trip-1/chunks/2", json=chunk)).status_code == 409
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_control_kept_separate_from_prediction(recording_api):
    client, _, calls = recording_api
    await client.post("/api/v2/trips", json=creation())
    control = {"distance_km": 5, "average_l_per_100km": 8, "source": "trip_computer", "same_interval": True}
    assert (await client.put("/api/v2/trips/trip-1/control", json=control)).status_code == 409
    completion = {"ended_at_ms": START + 10000, "chunk_count": 0, "end_reason": "user", "control": control}
    assert (await client.post("/api/v2/trips/trip-1/complete", json=completion)).status_code == 200
    corrected = {**control, "same_interval": False, "distance_km": 6}
    assert (await client.put("/api/v2/trips/trip-1/control", json=corrected)).status_code == 200
    response = await client.post("/api/v2/trips/trip-1/complete", json=completion)
    assert response.json()["control"] == corrected
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_simultaneous_retry_is_stored_once(recording_api):
    client, sessions, calls = recording_api
    responses = await asyncio.gather(*[client.post("/api/v2/trips", json=creation()) for _ in range(3)])
    assert all(response.status_code == 200 for response in responses)
    chunk = {"gps": [gps(1000), gps(2000)]}
    responses = await asyncio.gather(*[client.put("/api/v2/trips/trip-1/chunks/0", json=chunk) for _ in range(3)])
    assert all(response.status_code == 200 for response in responses)
    assert sum(not response.json()["duplicate"] for response in responses) == 1
    completion = {"ended_at_ms": START + 5000, "chunk_count": 1, "end_reason": "user"}
    responses = await asyncio.gather(*[client.post("/api/v2/trips/trip-1/complete", json=completion) for _ in range(3)])
    assert all(response.status_code == 200 for response in responses)
    assert len(calls) == 1
    async with sessions() as session:
        assert len((await session.execute(select(RecordingChunk))).scalars().all()) == 1
        assert (await session.get(TripRecording, "trip-1")).chunk_count == 1


def test_nonfinite_unknown_fields_and_oversized_chunks_are_rejected():
    invalid = [
        {"gps": [{**gps(), "speed_mps": math.nan}]},
        {"motion": [{**motion(), "gx": math.inf}]},
        {"gps": [{**gps(), "speed_mps": None}]},
        {"gps": [gps()] * 1000, "motion": [motion()]},
        {"gps": []},
        {"gps": [gps()], "fuel_l": 1},
        {"gps": [{**gps(), "timestamp_ms": 10**30}]},
    ]
    for payload in invalid:
        with pytest.raises(ValueError):
            TripChunk.model_validate(payload)


@pytest.mark.asyncio
async def test_prediction_failure_does_not_lose_recording(recording_api, monkeypatch):
    client, _, _ = recording_api

    def failed_prediction(*args):
        raise RuntimeError("simulated missing artifact")

    monkeypatch.setattr("app.services.fuel_prediction.predict_fuel", failed_prediction)
    await client.post("/api/v2/trips", json=creation())
    await client.put("/api/v2/trips/trip-1/chunks/0", json={"gps": [gps()]})
    response = await client.post("/api/v2/trips/trip-1/complete", json={"ended_at_ms": START + 3000, "chunk_count": 1, "end_reason": "user"})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["fuel_estimate"]["status"] == "model_unavailable"
    assert response.json()["fuel_estimate"]["estimated_fuel_l"] is None
    assert len((await client.get("/api/v2/trips/trip-1/export")).json()["gps"]) == 1


@pytest.mark.asyncio
async def test_empty_interrupted_recording_and_daily_chunk_capacity(recording_api):
    client, _, _ = recording_api
    await client.post("/api/v2/trips", json=creation())
    assert TripComplete.model_validate({"ended_at_ms": START + 86400000, "chunk_count": 86400, "end_reason": "duration_limit"}).chunk_count == 86400
    response = await client.post("/api/v2/trips/trip-1/complete", json={"ended_at_ms": START, "chunk_count": 0, "end_reason": "interrupted"})
    assert response.status_code == 200
    assert response.json()["ended_at_ms"] == START


@pytest.mark.asyncio
async def test_completion_projects_only_gps_and_caps_before_loading(recording_api, monkeypatch):
    client, sessions, calls = recording_api
    await client.post("/api/v2/trips", json=creation())
    await client.put("/api/v2/trips/trip-1/chunks/0", json={"gps": [gps(1000), gps(2000)], "motion": [motion(1500)] * 900})
    sql = []

    def capture_sql(connection, cursor, statement, parameters, context, executemany):
        sql.append(statement)

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        response = await client.post("/api/v2/trips/trip-1/complete", json={"ended_at_ms": START + 3000, "chunk_count": 1, "end_reason": "user"})
        assert response.status_code == 200
        assert len(calls[0][0]) == 2
        payload_queries = [statement for statement in sql if statement.lstrip().upper().startswith("SELECT") and "recording_chunks.payload" in statement]
        assert len(payload_queries) == 1
        assert "JSON_EXTRACT" in payload_queries[0]

        await client.post("/api/v2/trips", json=creation("trip-limit"))
        await client.put("/api/v2/trips/trip-limit/chunks/0", json={"gps": [gps(1000), gps(2000)]})
        monkeypatch.setattr("app.services.fuel_prediction.MAX_GPS_SAMPLES", 1)

        def bounded_prediction(points, *args):
            assert points is None
            return {"status": "invalid_trip", "estimated_fuel_l": None, "quality_flags": ["invalid_gps_sample_count"]}

        monkeypatch.setattr("app.services.fuel_prediction.predict_fuel", bounded_prediction)
        sql.clear()
        response = await client.post("/api/v2/trips/trip-limit/complete", json={"ended_at_ms": START + 3000, "chunk_count": 1, "end_reason": "user"})
        assert response.status_code == 200
        assert response.json()["fuel_estimate"]["estimated_fuel_l"] is None
        assert response.json()["fuel_estimate"]["gps_sample_counts"]["received"] == 2
        assert "gps_sample_limit_exceeded" in response.json()["fuel_estimate"]["quality_flags"]
        assert not any("recording_chunks.payload" in statement for statement in sql)
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)


def test_gps_projection_compiles_for_sqlite_and_postgresql():
    query = _gps_payload_query("trip-1")
    sqlite_sql = str(query.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    postgres_sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "JSON_EXTRACT" in sqlite_sql and "gps" in sqlite_sql
    assert "recording_chunks.payload -> 'gps'" in postgres_sql
    assert "recording_chunks.gps_count > 0" in postgres_sql


@pytest.mark.asyncio
async def test_streamed_active_export_freezes_chunks_across_batches_and_session_close(recording_api):
    client, sessions, _ = recording_api
    await client.post("/api/v2/trips", json=creation("trip-export"))
    original = []
    # More than one query batch, inserted out of order with sequence gaps.
    for index in reversed(range(105)):
        payload = TripChunk.model_validate({
            "gps": [gps(index + 1, index / 10)],
            "motion": [motion(index + 1)] if index % 2 == 0 else [],
        }).model_dump(mode="json")
        original.append(RecordingChunk(
            trip_id="trip-export", sequence=index * 2, payload=payload,
            payload_hash=payload_hash(payload), min_timestamp_ms=START + index + 1,
            max_timestamp_ms=START + index + 1,
            gps_count=len(payload["gps"]), motion_count=len(payload["motion"]),
        ))
    original.sort(key=lambda chunk: chunk.sequence)
    async with sessions() as session:
        session.add_all(original)
        trip = await session.get(TripRecording, "trip-export")
        trip.chunk_count = len(original)
        await session.commit()
        user = await session.get(User, "alice")
        expected = trip_detail(trip)
        response = await export_trip("trip-export", user, session)
    # The request session is already closed when FastAPI starts the body. Later
    # inserts both below and above the captured watermark must remain excluded.
    async with sessions() as session:
        for sequence in (1, 1000):
            payload = {"gps": [gps(1000)], "motion": [motion(1000)]}
            session.add(RecordingChunk(
                trip_id="trip-export", sequence=sequence, payload=payload,
                payload_hash=payload_hash(payload), min_timestamp_ms=START + 1000,
                max_timestamp_ms=START + 1000, gps_count=1, motion_count=1,
            ))
        await session.commit()
    pieces = []
    async for piece in response.body_iterator:
        pieces.append(piece.decode() if isinstance(piece, bytes) else piece)
    exported = json.loads("".join(pieces))
    expected.update(
        units={"speed": "m/s", "acceleration": "m/s2", "gyro": "deg/s",
               "gyro_axes": "x=beta,y=gamma,z=alpha", "timestamps": "epoch_ms",
               "coordinates": "degrees", "accuracy": "m",
               "event_timestamp": "browser_event_ms", "interval": "ms"},
        chunks=[{"sequence": chunk.sequence, "sha256": chunk.payload_hash,
                 "gps_count": chunk.gps_count, "motion_count": chunk.motion_count}
                for chunk in original],
        gps=[point for chunk in original for point in chunk.payload["gps"]],
        motion=[point for chunk in original for point in chunk.payload["motion"]],
        point_counts={"gps": 105, "motion": 53},
    )
    assert exported == expected
    assert response.media_type == "application/json"
    assert response.headers["content-disposition"] == 'attachment; filename="autoxak-trip-trip-export.json"'
    assert not hasattr(response, "body")
