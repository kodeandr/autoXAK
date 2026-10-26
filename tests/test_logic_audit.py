"""Cross-flow regressions discovered by the October 2026 logic audit."""
from datetime import datetime, timedelta, timezone
import math
import uuid

import numpy as np
import pytest

from app.core.database import async_session_factory
from app.main import count_maneuver_events
from app.models.db_models import Trip


async def test_history_can_page_all_dates_with_stable_ties(committed_client):
    owner = committed_client.audit_user_id
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=90)
    async with async_session_factory() as db:
        db.add_all([Trip(id=str(uuid.uuid4()), user_id=owner, car_id='car',
                         created_at=old if i < 5 else now,
                         duration_seconds=60, distance_km=1, total_savings_rub=1)
                    for i in range(205)])
        await db.commit()
    path = f'/api/v1/users/{owner}/analytics/period'
    recent = (await committed_client.get(path)).json()
    assert recent['summary']['total_trips'] == 200
    pages = [(await committed_client.get(path, params={'all_time':True, 'offset':offset})).json()
             for offset in (0, 200)]
    ids = [trip['session_id'] for page in pages for trip in page['trips']]
    assert len(ids) == len(set(ids)) == 205
    assert pages[0]['summary']['total_savings_rub'] == 205
    assert pages[1]['offset'] == 200
    assert pages[1]['trips'][-1]['created_at'].startswith(old.date().isoformat())
    assert (await committed_client.get(path, params={'limit':201})).status_code == 422
    assert (await committed_client.get(path, params={'offset':-1})).status_code == 422


async def test_period_idle_ratio_is_weighted_by_trip_duration(committed_client):
    async with async_session_factory() as db:
        db.add_all([Trip(id=str(uuid.uuid4()), user_id=committed_client.audit_user_id,
                         car_id='car', duration_seconds=duration, distance_km=0, idle_ratio=idle)
                    for duration,idle in [(60,1), (540,0)]])
        await db.commit()
    summary = (await committed_client.get(f'/api/v1/users/{committed_client.audit_user_id}/analytics/period')).json()['summary']
    assert summary['avg_idle_ratio'] == pytest.approx(0.1)


@pytest.mark.parametrize('rate', [10, 25, 50, 100])
def test_maneuver_duration_and_cooldown_use_seconds(rate):
    # A 120ms acceleration qualifies at every supported sampling frequency.
    signal = np.zeros(rate*3)
    width = math.ceil(0.12*rate)
    for start in [int(0.2*rate), int(0.8*rate), int(2*rate)]:
        signal[start:start+width] = 3
    dt = 1/rate
    assert count_maneuver_events(signal, 2, min_points=math.ceil(0.1/dt), dt=dt, min_duration_s=.1) == 3


async def test_traffic_score_cannot_manufacture_oil_or_money(committed_client, synthetic_telemetry_batch, monkeypatch):
    from app.services.wear_engine import WearEngine
    calls = []
    original = WearEngine.evaluate_trip_wear

    def evaluate(self, *args, **kwargs):
        calls.append(kwargs.get('traffic_score', 1))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(WearEngine, 'evaluate_trip_wear', evaluate)
    body = dict(synthetic_telemetry_batch, user_id=committed_client.audit_user_id)
    response = await committed_client.post('/api/v1/telemetry/preview', json=body)
    assert response.status_code == 200, response.text
    assert calls == [1]
    assert response.json()['cost_savings_rub'] is None


async def test_dsp_runs_outside_request_event_loop(committed_client, synthetic_telemetry_batch, monkeypatch):
    import threading
    import app.main as main
    request_thread = threading.get_ident()
    workers = []
    original = main._calculate_session_values_sync

    def calculate(*args, **kwargs):
        workers.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(main, '_calculate_session_values_sync', calculate)
    payload = dict(synthetic_telemetry_batch, user_id=committed_client.audit_user_id)
    response = await committed_client.post('/api/v1/telemetry/preview', json=payload)
    assert response.status_code == 200, response.text
    assert workers and all(worker != request_thread for worker in workers)


async def test_unrealistic_sample_frequency_is_validation_error(committed_client, synthetic_telemetry_batch):
    payload = dict(synthetic_telemetry_batch, user_id=committed_client.audit_user_id)
    payload['telemetry_stream'] = [dict(point, t=i*1e-10) for i,point in enumerate(payload['telemetry_stream'][:50])]
    response = await committed_client.post('/api/v1/telemetry/preview', json=payload)
    assert response.status_code == 422, response.text


async def test_irregular_samples_do_not_inflate_idle_time(committed_client, synthetic_telemetry_batch):
    times = np.r_[np.arange(80)*0.01, np.linspace(1,10,20)]
    payload = dict(synthetic_telemetry_batch, user_id=committed_client.audit_user_id)
    payload['telemetry_stream'] = [dict(synthetic_telemetry_batch['telemetry_stream'][0],
                                      t=float(t), speed=0 if i<80 else 10)
                                   for i,t in enumerate(times)]
    response = await committed_client.post('/api/v1/telemetry/session', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['traffic_jam_seconds'] == pytest.approx(0.81, abs=0.01)


async def test_board_computer_accepts_same_uuid_representation(committed_client, synthetic_telemetry_batch):
    identifier = str(uuid.uuid4())
    payload = dict(synthetic_telemetry_batch, user_id=committed_client.audit_user_id, session_id=identifier.upper())
    assert (await committed_client.post('/api/v1/telemetry/session', json=payload)).status_code == 200
    response = await committed_client.post(f'/api/v1/trips/{identifier.upper()}/board-computer',
                                           json={'board_computer_consumption_l100km':8.5})
    assert response.status_code == 200, response.text


async def test_raw_capture_with_unsupported_frequency_is_still_saved(committed_client):
    start = 1700000000000
    payload = {'session_id':str(uuid.uuid4()), 'user_id':committed_client.audit_user_id, 'car_id':'haval_jolion_15t',
               'telemetry_stream':[{'t':start/1000+i*0.0001,'ax':0,'ay':0,'az':9.81,'speed':10,
                                    'lat':55.75,'lon':37.61} for i in range(50)],
               'raw_recording':{'started_at_ms':start,'ended_at_ms':start+50,
                                'motion':[{'timestamp_ms':start+i,'ax':0,'ay':0,'az':9.81,
                                           'acceleration_includes_gravity':True} for i in range(50)]}}
    response = await committed_client.post('/api/v1/telemetry/session', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'RECORDED'
    assert 'unsupported_motion_rate' in response.json()['metrics']['capture_quality']['quality_flags']
    assert response.json()['cost_savings_rub'] is None
    exported = await committed_client.get('/api/v2/trips/'+response.json()['session_id']+'/export')
    assert exported.status_code == 200
    assert exported.json()['point_counts']['motion'] == 50
