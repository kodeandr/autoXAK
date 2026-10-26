from copy import deepcopy
import uuid

import pytest


async def test_preview_uses_final_model_without_credits_or_history(committed_client, synthetic_telemetry_batch):
    client = committed_client
    payload = dict(deepcopy(synthetic_telemetry_batch), user_id=client.audit_user_id, session_id=str(uuid.uuid4()))
    before = (await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
    previews = [(await client.post('/api/v1/telemetry/preview', json=payload)) for _ in range(2)]
    assert all(response.status_code == 200 for response in previews)
    preview = previews[0].json()
    assert preview == previews[1].json()
    assert preview['status'] == 'PREVIEW' and preview['provisional'] is True
    assert preview['cost_savings_rub'] > 0
    after = (await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
    assert before == after
    assert (await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'] == []
    assert (await client.get(f'/api/v2/trips/{payload["session_id"]}')).status_code == 404
    final = await client.post('/api/v1/telemetry/session', json=payload)
    assert final.status_code == 200, final.text
    assert final.json()['cost_savings_rub'] == preview['cost_savings_rub']
    assert (await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()['total_trips'] == 1


async def test_preview_requires_authentication(committed_client, synthetic_telemetry_batch):
    response = await committed_client.post('/api/v1/telemetry/preview', json=synthetic_telemetry_batch,
                                           headers={'Authorization':''})
    assert response.status_code == 401


@pytest.mark.parametrize('case', ['owner', 'missing_gps', 'too_many', 'raw_recording', 'gap'])
async def test_preview_rejects_invalid_data_without_persisting(committed_client, synthetic_telemetry_batch, case):
    client = committed_client
    payload = dict(deepcopy(synthetic_telemetry_batch), user_id=client.audit_user_id)
    expected = 422
    if case == 'owner':
        payload['user_id'] = 'someone-else'
        expected = 403
    elif case == 'missing_gps':
        for point in payload['telemetry_stream']:
            point['lat'] = point['lon'] = None
    elif case == 'too_many':
        payload['telemetry_stream'] *= 3
    elif case == 'raw_recording':
        payload['raw_recording'] = {'started_at_ms':1,'ended_at_ms':1,'gps':[],'motion':[]}
    else:
        payload['telemetry_stream'] = payload['telemetry_stream'][:50] + payload['telemetry_stream'][500:]
    response = await client.post('/api/v1/telemetry/preview', json=payload)
    assert response.status_code == expected
    assert (await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'] == []


async def test_stationary_sensor_shocks_do_not_generate_money(committed_client, synthetic_telemetry_batch):
    payload = dict(deepcopy(synthetic_telemetry_batch), user_id=committed_client.audit_user_id)
    for index, point in enumerate(payload['telemetry_stream']):
        point.update(speed=0, ax=0, ay=0, az=9.81+(5 if index%100<5 else 0))
    response = await committed_client.post('/api/v1/telemetry/preview', json=payload)
    assert response.status_code == 200
    assert response.json()['distance_km'] == response.json()['cost_savings_rub'] == 0


async def test_short_vertical_impacts_survive_driving_filter(committed_client, synthetic_telemetry_batch):
    payload = dict(deepcopy(synthetic_telemetry_batch), user_id=committed_client.audit_user_id)
    for index, point in enumerate(payload['telemetry_stream']):
        point.update(speed=10, ax=0, ay=0, az=9.81+(6 if 500<=index<504 or 1500<=index<1504 else 0))
    result = await committed_client.post('/api/v1/telemetry/session', json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['road_anomalies_count'] >= 2
    assert result.json()['harsh_accel_count'] == result.json()['harsh_brake_count'] == 0
