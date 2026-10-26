import asyncio
from copy import deepcopy
import time
import uuid

import pytest


def capture(owner, *, gps=True, motion=False):
    start = int(time.time() * 1000) - 60000
    return {"session_id": str(uuid.uuid4()), "user_id": owner, "car_id": "haval_jolion_15t",
            "telemetry_stream": [], "raw_recording": {
                "started_at_ms": start, "ended_at_ms": start + 60000,
                "gps": [{"timestamp_ms": start+i*1000, "latitude": 55.75+i*0.0001, "longitude": 37.61,
                         "speed_mps": 10, "speed_source": "gps", "accuracy_m": 5} for i in range(61)] if gps else [],
                "motion": [{"timestamp_ms": start, "ax": None, "ay": None, "az": None,
                            "gx": 12.5, "gy": None, "gz": None, "acceleration_includes_gravity": False}] if motion else [],
                "permissions": {"motion": "granted" if motion else "denied", "geolocation": "granted" if gps else "unavailable"}}}


async def test_gps_only_capture_is_durable_visible_and_idempotent(committed_client):
    client = committed_client
    payload = capture(client.audit_user_id)
    first, duplicate = await asyncio.gather(*[client.post('/api/v1/telemetry/session', json=payload) for _ in range(2)])
    assert first.status_code == duplicate.status_code == 200
    assert first.json() == duplicate.json()
    data = first.json()
    assert data['status'] == 'RECORDED' and data['distance_km'] == pytest.approx(0.6)
    assert data['cost_savings_rub'] is data['oil_wear_percent'] is None
    assert data['metrics']['capture_quality']['mode'] == 'gps_only'
    dashboard = (await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
    assert dashboard['total_trips'] == 1 and dashboard['total_distance_km'] == pytest.approx(0.6)
    history = (await client.get(f'/api/v1/users/{client.audit_user_id}/analytics/period')).json()['trips']
    assert len(history) == 1 and history[0]['capture_quality']['analysis_status'] == 'unavailable'
    exported = (await client.get(f'/api/v2/trips/{data["session_id"]}/export')).json()
    assert exported['gps'] == payload['raw_recording']['gps']
    assert exported['point_counts'] == {'gps':61, 'motion':0}
    assert [point['timestamp_ms'] for point in exported['gps']] == [point['timestamp_ms'] for point in payload['raw_recording']['gps']]
    changed = deepcopy(payload)
    changed['raw_recording']['gps'][0]['latitude'] += 0.1
    assert (await client.post('/api/v1/telemetry/session', json=changed)).status_code == 409


@pytest.mark.parametrize('has_motion', [False, True])
async def test_motion_only_and_empty_capture_keep_history_without_inventing_readings(committed_client, has_motion):
    client = committed_client
    payload = capture(client.audit_user_id, gps=False, motion=has_motion)
    response = await client.post('/api/v1/telemetry/session', json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'RECORDED' and result['duration_seconds'] == 60
    assert result['distance_km'] == 0
    assert result['cost_savings_rub'] is result['oil_wear_percent'] is None
    exported = (await client.get(f'/api/v2/trips/{result["session_id"]}/export')).json()
    assert exported['point_counts'] == {'gps':0, 'motion':int(has_motion)}
    if has_motion:
        assert exported['motion'][0]['ax'] is None and exported['motion'][0]['gx'] == 12.5
    assert (await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips']


def add_complete_motion(payload, synthetic):
    start = payload['raw_recording']['started_at_ms']
    payload['telemetry_stream'] = [dict(point, t=round(start/1000+point['t'],3)) for point in synthetic['telemetry_stream']]
    payload['raw_recording']['motion'] = [{"timestamp_ms": int(round(point['t']*1000)),
        "ax":point['ax'], "ay":point['ay'], "az":point['az'], "acceleration_includes_gravity": True}
        for point in payload['telemetry_stream']]
    return payload


async def test_complete_capture_still_runs_existing_calculation(committed_client, synthetic_telemetry_batch):
    client = committed_client
    payload = add_complete_motion(capture(client.audit_user_id), synthetic_telemetry_batch)
    response = await client.post('/api/v1/telemetry/session', json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'PROCESSED' and result['distance_km'] > 0
    assert result['metrics']['capture_quality']['analysis_status'] == 'available'
    exported = (await client.get(f'/api/v2/trips/{result["session_id"]}/export')).json()
    assert exported['point_counts']['motion'] == len(payload['telemetry_stream'])


async def test_background_sensor_gap_is_saved_without_scoring(committed_client, synthetic_telemetry_batch):
    client = committed_client
    payload = add_complete_motion(capture(client.audit_user_id), synthetic_telemetry_batch)
    payload['telemetry_stream'] = payload['telemetry_stream'][:25] + payload['telemetry_stream'][1000:]
    payload['raw_recording']['motion'] = payload['raw_recording']['motion'][:25] + payload['raw_recording']['motion'][1000:]
    response = await client.post('/api/v1/telemetry/session', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'RECORDED'
    assert 'motion_interrupted' in response.json()['metrics']['capture_quality']['quality_flags']


async def test_calculation_failure_rolls_back_raw_capture_before_retry(committed_client, synthetic_telemetry_batch, monkeypatch):
    import app.main as main
    client = committed_client
    payload = add_complete_motion(capture(client.audit_user_id), synthetic_telemetry_batch)

    class FailedEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError('injected calculation failure')

    with monkeypatch.context() as patch:
        patch.setattr(main, 'WearEngine', FailedEngine)
        assert (await client.post('/api/v1/telemetry/session', json=payload)).status_code == 500
    assert (await client.get(f'/api/v2/trips/{payload["session_id"]}')).status_code == 404
    assert (await client.post('/api/v1/telemetry/session', json=payload)).status_code == 200


@pytest.mark.parametrize('mutation', ['outside', 'duplicate', 'invalid_permission'])
async def test_invalid_raw_capture_is_rejected_without_creating_history(committed_client, mutation):
    client = committed_client
    payload = capture(client.audit_user_id)
    raw = payload['raw_recording']
    if mutation == 'outside':
        raw['gps'][0]['timestamp_ms'] -= 1
    elif mutation == 'duplicate':
        raw['gps'][1]['timestamp_ms'] = raw['gps'][0]['timestamp_ms']
    else:
        raw['permissions']['motion'] = 'pretend'
    assert (await client.post('/api/v1/telemetry/session', json=payload)).status_code == 422
    assert (await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'] == []


async def test_history_accepts_a_stored_result_with_null_metrics(committed_client):
    from app.core.database import async_session_factory
    from app.models.db_models import Trip
    client = committed_client
    response = await client.post('/api/v1/telemetry/session', json=capture(client.audit_user_id))
    result = response.json()
    async with async_session_factory() as db:
        trip = await db.get(Trip, result['session_id'])
        trip.calculation_result = dict(result, metrics=None)
        await db.commit()
    history = await client.get(f'/api/v1/users/{client.audit_user_id}/analytics/period')
    assert history.status_code == 200
    assert history.json()['trips'][0]['capture_quality'] is None
    assert history.json()['trips'][0]['total_savings_rub'] is None
    assert history.json()['trips'][0]['oil_wear_percent'] is None
