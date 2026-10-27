"""Retire fuel inference without retiring the v1/v2 data collection paths."""

from pathlib import Path
import subprocess
import sys
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.recordings import TripRecording


UNTRAINED = {"status": "model_not_trained", "estimated_fuel_l": None,
             "complete_trip_fuel_l": None, "quality_flags": []}


def test_app_imports_and_exposes_recording_and_math_routes_without_legacy_ml():
    code = '''
import importlib.abc, sys
class NoLegacyML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "catboost" or fullname.startswith("catboost.") or fullname == "app.ml" or fullname == "app.services.fuel_prediction":
            raise ModuleNotFoundError("Legacy ML is unavailable: " + fullname)
sys.meta_path.insert(0, NoLegacyML())
from app.main import app
paths = set(app.openapi()["paths"])
assert {"/api/v1/telemetry/session", "/api/v2/trips/{trip_id}/complete", "/api/v2/trips/{trip_id}/export", "/api/v3/math/evaluate", "/health"} <= paths
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_fuel_column_remains_nullable_json():
    column = TripRecording.__table__.c.fuel_estimate
    assert column.nullable
    assert str(column.type) == "JSON"


async def test_v2_completion_retains_every_sensor_point_and_post_trip_control(committed_client, monkeypatch):
    client = committed_client
    trip_id = uuid.uuid4().hex
    start = 1700000000000
    assert (await client.post('/api/v2/trips', json={
        'trip_id': trip_id, 'started_at_ms': start, 'vehicle': {'vehicle_id': 'new-research-car'},
    })).status_code == 200
    gps = [{'timestamp_ms': start+i*1000, 'latitude': 55.75, 'longitude': 37.61,
            'speed_mps': 10., 'accuracy_m': 5., 'speed_source': 'gps'} for i in range(61)]
    motion = [{'timestamp_ms': start+i*500, 'ax': .1, 'ay': None, 'az': 9.81,
               'gx': 1., 'gy': 2., 'gz': 3., 'acceleration_includes_gravity': True,
               'event_timestamp_ms': None, 'interval_ms': None}
              for i in range(120)]
    # Out-of-order uploads retain separate sensor clocks and full precision.
    assert (await client.put(f'/api/v2/trips/{trip_id}/chunks/1', json={'gps': gps})).status_code == 200
    assert (await client.put(f'/api/v2/trips/{trip_id}/chunks/0', json={'motion': motion})).status_code == 200
    assert (await client.put(f'/api/v2/trips/{trip_id}/chunks/0', json={'motion': motion})).json()['duplicate']
    complete = {'ended_at_ms': start+60000, 'chunk_count': 2, 'end_reason': 'user'}
    assert (await client.post(f'/api/v2/trips/{trip_id}/complete', json={**complete, 'chunk_count': 3})).status_code == 409
    assert (await client.post(f'/api/v2/trips/{trip_id}/complete', json={**complete, 'ended_at_ms': start+59999})).status_code == 422

    async def forbidden_payload_read(*args, **kwargs):
        raise AssertionError('Completion must not load raw arrays for removed inference')

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, 'stream_scalars', forbidden_payload_read)
        first = await client.post(f'/api/v2/trips/{trip_id}/complete', json=complete)
    assert first.status_code == 200, first.text
    assert first.json()['fuel_estimate'] == UNTRAINED
    assert first.json()['status'] == 'completed'
    assert (await client.post(f'/api/v2/trips/{trip_id}/complete', json=complete)).json() == first.json()
    control = {'distance_km': .6, 'average_l_per_100km': 8.2, 'source': 'trip_computer', 'same_interval': True}
    checked = await client.put(f'/api/v2/trips/{trip_id}/control', json=control)
    assert checked.status_code == 200
    assert checked.json()['fuel_estimate'] == UNTRAINED
    exported = await client.get(f'/api/v2/trips/{trip_id}/export')
    assert exported.status_code == 200
    data = exported.json()
    assert data['gps'] == gps and data['motion'] == motion
    assert data['point_counts'] == {'gps': 61, 'motion': 120}
    assert data['control'] == control
    assert data['fuel_estimate'] == UNTRAINED


async def test_previous_completed_estimate_and_retry_identity_are_not_rewritten(committed_client):
    client = committed_client
    trip_id = uuid.uuid4().hex
    start = 1700000000000
    await client.post('/api/v2/trips', json={'trip_id': trip_id, 'started_at_ms': start,
                                           'vehicle': {'vehicle_id': 'historical-car'}})
    complete = {'ended_at_ms': start, 'chunk_count': 0, 'end_reason': 'interrupted'}
    assert (await client.post(f'/api/v2/trips/{trip_id}/complete', json=complete)).status_code == 200
    historical = {'status': 'research_estimate', 'estimated_fuel_l': .125, 'model_version': 'historical-only'}
    async with async_session_factory() as db:
        record = await db.get(TripRecording, trip_id)
        original_hash = record.complete_hash
        record.fuel_estimate = historical
        await db.commit()
    retried = await client.post(f'/api/v2/trips/{trip_id}/complete', json=complete)
    assert retried.status_code == 200
    assert retried.json()['fuel_estimate'] == historical
    async with async_session_factory() as db:
        assert (await db.get(TripRecording, trip_id)).complete_hash == original_hash


async def test_v1_capture_remains_idempotent_and_accepts_manual_control(committed_client):
    client = committed_client
    start = int(time.time()*1000)-1000
    payload = {'session_id': str(uuid.uuid4()), 'user_id': client.audit_user_id,
               'car_id': 'new-research-car', 'telemetry_stream': [], 'raw_recording': {
                   'started_at_ms': start, 'ended_at_ms': start+1000,
                   'gps': [{'timestamp_ms': start+i*1000, 'latitude': 55.75, 'longitude': 37.61,
                            'speed_mps': 10., 'accuracy_m': 5., 'speed_source': 'gps'} for i in range(2)],
                   'motion': [{'timestamp_ms': start+500, 'ax': None, 'ay': None, 'az': None,
                               'gx': 1.25, 'gy': None, 'gz': None, 'event_timestamp_ms': None,
                               'interval_ms': None, 'acceleration_includes_gravity': False}],
               }}
    first = await client.post('/api/v1/telemetry/session', json=payload)
    assert first.status_code == 200, first.text
    assert (await client.post('/api/v1/telemetry/session', json=payload)).json() == first.json()
    trip_id = first.json()['session_id']
    control = {'distance_km': .01, 'average_l_per_100km': 7., 'source': 'obd', 'same_interval': True}
    assert (await client.put(f'/api/v2/trips/{trip_id}/control', json=control)).status_code == 200
    exported = (await client.get(f'/api/v2/trips/{trip_id}/export')).json()
    assert exported['gps'] == payload['raw_recording']['gps']
    assert exported['motion'] == payload['raw_recording']['motion']
    assert exported['control'] == control
    estimate = exported['fuel_estimate']
    assert estimate['status'] == 'model_not_trained'
    assert estimate['estimated_fuel_l'] is estimate['complete_trip_fuel_l'] is None
    history = (await client.get(f'/api/v1/users/{client.audit_user_id}/analytics/period')).json()
    assert history['summary']['total_trips'] == 1
