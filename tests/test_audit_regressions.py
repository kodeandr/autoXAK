"""Regressions for authentication, durable writes, boundaries, and model evidence."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import uuid

import numpy as np
import pytest
from pydantic import ValidationError

from app.core.security import SECRET_KEY, create_access_token, decode_access_token
from app.core.dsp.filters import SignalFilter
from app.core.dsp.simplifier import PathSimplifier
from app.models.telemetry import TripSessionPayload
from app.schemas.vehicle import CustomVehicleInput
from app.services.benchmark_engine import BenchmarkEngine
from app.services.twin_engine import TwinEngine
from app.services.profile_synthesizer import ProfileSynthesizer


def signed(claims, algorithm='HS256'):
    encode = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')
    content = encode({'alg': algorithm, 'typ': 'JWT'}) + '.' + encode(claims)
    signature = hmac.new(SECRET_KEY.encode(), content.encode(), hashlib.sha256).digest()
    return content + '.' + base64.urlsafe_b64encode(signature).decode().rstrip('=')


@pytest.mark.parametrize('claims', [[], {}, {'sub': 'u'}, {'sub': 'u', 'exp': False},
    {'sub': 'u', 'exp': 0}, {'sub': 'u', 'exp': float('nan')}, {'sub': [], 'exp': 99999999999}])
def test_invalid_signed_claims_are_rejected(claims):
    assert decode_access_token(signed(claims)) is None


def test_reserved_claims_and_zero_expiry_cannot_extend_session():
    token = create_access_token('owner', {'sub': 'attacker', 'exp': 999999999999})
    assert decode_access_token(token)['sub'] == 'owner'
    assert decode_access_token(token)['exp'] < 999999999999
    assert decode_access_token(create_access_token('owner', expires_delta=timedelta(0))) is None
    assert decode_access_token(signed({'sub': 'u', 'exp': 99999999999}, 'none')) is None


async def test_me_never_mints_token_from_user_id(async_client):
    response = await async_client.get('/api/v1/auth/me?user_id=test_user_coverage', headers={'Authorization': ''})
    assert response.status_code == 401
    assert (await async_client.get('/api/v1/users/me', headers={'Authorization': ''})).status_code == 401


@pytest.mark.parametrize('path', ['profile', 'dashboard', 'trips', 'analytics/period'])
async def test_v1_rejects_cross_owner_reads(async_client, path):
    assert (await async_client.get('/api/v1/users/someone_else/' + path)).status_code == 403


async def test_cross_owner_mutations_are_denied(async_client, synthetic_telemetry_batch):
    payload = dict(synthetic_telemetry_batch, user_id='victim')
    assert (await async_client.post('/api/v1/telemetry/session', json=payload)).status_code == 403
    assert (await async_client.post('/api/v1/users/victim/profile', json={'car_id': 'other'})).status_code == 403
    assert (await async_client.post('/api/v1/cpa/claim', json={'user_id': 'victim', 'promo_code': 'test'})).status_code == 403
    assert (await async_client.post('/api/v1/trips/missing/board-computer', json={'board_computer_consumption_l100km': 8})).status_code == 404


async def test_guest_id_is_not_a_password(async_client):
    guest = (await async_client.post('/api/v1/auth/guest', json={})).json()
    body = {'guest_id': guest['user_id']}
    assert (await async_client.post('/api/v1/auth/guest', json=body, headers={'Authorization': ''})).status_code == 401
    assert (await async_client.post('/api/v1/auth/guest', json=body)).status_code == 403
    authorized = await async_client.post('/api/v1/auth/guest', json=body, headers={'Authorization': 'Bearer ' + guest['token']})
    assert authorized.status_code == 200
    assert authorized.json()['user_id'] == guest['user_id']


async def test_device_id_is_not_a_password(async_client):
    first = (await async_client.post('/api/v1/auth/handshake', json={'device_id': 'public-device'})).json()
    second = (await async_client.post('/api/v1/auth/handshake', json={'device_id': 'public-device'})).json()
    assert first['user_id'] != second['user_id']
    assert first['is_guest'] is True


async def test_oauth_requires_state_and_demo_is_opt_in(async_client):
    assert (await async_client.get('/api/v1/auth/yandex/callback?code=attacker&state=victim')).status_code == 400
    assert (await async_client.get('/api/v1/auth/yandex/demo?guest_id=test_user_coverage')).status_code == 404


async def test_oauth_state_is_signed_bound_and_uses_authenticated_guest(async_client, monkeypatch):
    monkeypatch.setenv('YANDEX_CLIENT_ID', 'test-client')
    response = await async_client.post('/api/v1/auth/yandex/login?guest_id=victim')
    from urllib.parse import urlparse, parse_qs
    state = parse_qs(urlparse(response.json()['url']).query)['state'][0]
    assert state != 'victim'
    assert 'HttpOnly' in response.headers['set-cookie']
    claims = decode_access_token(async_client.cookies['autoxak_oauth_state'])
    assert claims['guest_id'] == 'test_user_coverage'
    # A state JWT is not an access JWT.
    assert (await async_client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + async_client.cookies['autoxak_oauth_state']})).status_code == 401
    assert (await async_client.get('/api/v1/auth/yandex/callback?code=test&state=wrong')).status_code == 400


async def test_legacy_session_retry_returns_identical_persisted_result(async_client, synthetic_telemetry_batch):
    payload = dict(synthetic_telemetry_batch, session_id='legacy_non_uuid')
    first = await async_client.post('/api/v1/telemetry/session', json=payload)
    second = await async_client.post('/api/v1/telemetry/session', json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    dash = (await async_client.get('/api/v1/users/test_user_coverage/dashboard')).json()
    assert dash['total_trips'] == 1
    payload['car_id'] = 'different-car'
    assert (await async_client.post('/api/v1/telemetry/session', json=payload)).status_code == 409


async def test_profile_update_preserves_omitted_settings(async_client):
    route = '/api/v1/users/test_user_coverage/profile'
    assert (await async_client.post(route, json={'current_oil_wear_percent': 82, 'service_cost_rub': 15000})).status_code == 200
    assert (await async_client.post(route, json={'car_name': 'Мой автомобиль'})).status_code == 200
    profile = (await async_client.get(route)).json()
    assert profile['current_oil_wear_percent'] == 82
    assert profile['service_cost_rub'] == 15000
    assert profile['car_name'] == 'Мой автомобиль'


async def test_mixed_timezone_analytics_is_normalized(async_client):
    response = await async_client.get('/api/v1/users/test_user_coverage/analytics/period', params={'start_date': '2025-01-01', 'end_date': '2026-10-21T12:00:00+03:00'})
    assert response.status_code == 200
    assert response.json()['start_date'].endswith('Z')


async def test_manual_vehicle_endpoint_and_real_parameters(async_client):
    body = {'custom_name': 'My Car', 'curb_weight_kg': 2100, 'engine_displacement_l': 3.0, 'drivetrain_type': 'AWD', 'transmission_type': 'CVT'}
    response = await async_client.post('/api/v1/vehicles/synthesize', json=body)
    assert response.status_code == 200
    result = response.json()
    assert result['engine_displacement_l'] == 3.0
    assert result['drivetrain_efficiency'] == pytest.approx(0.86)
    assert result['curb_weight_kg'] == 2100
    assert result['car_name'] == 'My Car'
    await async_client.post('/api/v1/users/test_user_coverage/profile', json={'car_id': result['car_id']})
    from app.main import resolve_car_profile_async
    from app.core.database import get_db
    from app.main import app
    from app.models.db_models import User
    async for db in app.dependency_overrides[get_db]():
        user = await db.get(User, 'test_user_coverage')
        profile = await resolve_car_profile_async(result['car_id'], db, user)
        assert profile.curb_weight_kg == 2100


@pytest.mark.parametrize('field,value', [('speed', float('nan')), ('lat', 91), ('lon', 181), ('t', -1)])
def test_bad_telemetry_is_validated(synthetic_telemetry_batch, field, value):
    synthetic_telemetry_batch['telemetry_stream'][0][field] = value
    with pytest.raises(ValidationError): TripSessionPayload.model_validate(synthetic_telemetry_batch)


def test_duplicate_timestamps_and_invalid_aliases_are_validation_errors(synthetic_telemetry_batch):
    synthetic_telemetry_batch['telemetry_stream'][1]['t'] = 0
    with pytest.raises(ValidationError): TripSessionPayload.model_validate(synthetic_telemetry_batch)
    for payload in ({'engine_disp_l': 'invalid'}, {'engine_disp_l': 0}, {'power_hp': 0}):
        with pytest.raises(ValidationError): CustomVehicleInput.model_validate(payload)


def test_custom_ids_include_physical_parameters():
    a = ProfileSynthesizer.synthesize_profile(CustomVehicleInput(curb_weight_kg=1200))
    b = ProfileSynthesizer.synthesize_profile(CustomVehicleInput(curb_weight_kg=2200))
    assert a.car_id != b.car_id


def test_gravity_does_not_mask_acceleration_or_count_as_road_impact():
    ax = np.full(100, 2.5)
    clean, count = TwinEngine.filter_road_anomalies(ax, np.full(100, 9.81))
    np.testing.assert_array_equal(clean, ax)
    assert count == 0
    # Large sample periods used to create a zero-width suppression window/infinite loop.
    clean, count = TwinEngine.filter_road_anomalies(np.array([4., 0.]), np.array([16., 9.81]), dt=1)
    assert np.isfinite(clean).all()


def test_filter_handles_short_data_for_custom_order():
    assert len(SignalFilter().apply_butterworth_lpf(np.arange(20), order=8)) == 20
    assert all(len(axis) == 0 for axis in SignalFilter().isolate_linear_acceleration([], [], []))


def test_rdp_uses_meters_for_closed_tracks_and_retains_endpoints():
    simplifier = PathSimplifier()
    assert simplifier._perpendicular_distance(np.array([55.751, 37.61]), np.array([55.75, 37.61]), np.array([55.75, 37.61])) > 100
    coords = [{'lat': 55.75 + i * 0.0001, 'lon': 37.61 + (i % 2) * 0.001} for i in range(80)]
    output = simplifier.downsample_gps_stream(coords, 7)
    assert len(output) <= 7
    assert output[0] == coords[0] and output[-1] == coords[-1]


def test_no_invented_benchmark_significance_or_zero_reference_accuracy():
    result = BenchmarkEngine().evaluate_experiment(0.1, [0.]*5, [0.]*5, [90.]*5, [], dt=1)
    assert result.mape_percent is None
    assert result.hypothesis_confirmed is False
    assert result.t_statistic is None and result.p_value is None
    with pytest.raises(ValueError): BenchmarkEngine.calculate_obd_ground_truth_hours([800], [], [90])


async def test_service_worker_has_root_scope(async_client):
    response = await async_client.get('/sw.js')
    assert response.status_code == 200
    assert response.headers['service-worker-allowed'] == '/'
    assert 'no-cache' in response.headers['cache-control']


async def test_invalid_verification_is_422(async_client):
    response = await async_client.post('/api/v1/analytics/verify-run', json={'session_id': 's', 'user_id': 'test_user_coverage', 'car_id': 'c', 'telemetry_stream': [{}] * 50, 'obd_ground_truth': {}})
    assert response.status_code == 422


async def test_concurrent_retries_and_distinct_sessions_do_not_lose_totals(committed_client, synthetic_telemetry_batch):
    import asyncio
    body = dict(synthetic_telemetry_batch, user_id=committed_client.audit_user_id)
    responses = await asyncio.gather(*(committed_client.post('/api/v1/telemetry/session', json=body) for _ in range(3)))
    assert [response.status_code for response in responses] == [200] * 3
    assert all(response.json() == responses[0].json() for response in responses)
    different = dict(body, session_id='another-session')
    responses = await asyncio.gather(committed_client.post('/api/v1/telemetry/session', json=body), committed_client.post('/api/v1/telemetry/session', json=different))
    assert all(response.status_code == 200 for response in responses)
    dashboard = (await committed_client.get('/api/v1/users/' + committed_client.audit_user_id + '/dashboard')).json()
    assert dashboard['total_trips'] == 2
    assert dashboard['total_savings_rub'] == pytest.approx(sum(response.json()['cost_savings_rub'] for response in responses), abs=0.01)


async def test_recording_chunks_completion_ownership_and_streaming_export(committed_client):
    client = committed_client
    trip_id = 'audit_' + uuid.uuid4().hex
    start = 1700000000000
    vehicle = {'vehicle_id': 'car', 'fuel_type': 'gasoline', 'mass_kg': 1500, 'displacement_l': 1.5, 'turbo': True}
    create = {'trip_id': trip_id, 'started_at_ms': start, 'vehicle': vehicle}
    assert (await client.post('/api/v2/trips', json=create)).status_code == 200
    assert (await client.post('/api/v2/trips', json=create)).status_code == 200
    chunk = {'gps': [{'timestamp_ms': start + i * 1000, 'latitude': 55.75, 'longitude': 37.61, 'speed_mps': 10.0, 'accuracy_m': 5.0, 'speed_source': 'gps'} for i in range(61)]}
    assert (await client.put(f'/api/v2/trips/{trip_id}/chunks/0', json=chunk)).json()['duplicate'] is False
    assert (await client.put(f'/api/v2/trips/{trip_id}/chunks/0', json=chunk)).json()['duplicate'] is True
    assert (await client.get(f'/api/v2/trips/{trip_id}', headers={'Authorization': 'Bearer ' + create_access_token('missing')})).status_code == 401
    complete = {'ended_at_ms': start + 60000, 'chunk_count': 2, 'end_reason': 'user'}
    assert (await client.post(f'/api/v2/trips/{trip_id}/complete', json=complete)).status_code == 409
    complete['chunk_count'] = 1
    result = await client.post(f'/api/v2/trips/{trip_id}/complete', json=complete)
    assert result.status_code == 200
    assert result.json()['fuel_estimate']['status'] == 'research_estimate'
    assert result.json()['fuel_estimate']['estimated_fuel_l'] > 0
    assert (await client.post(f'/api/v2/trips/{trip_id}/complete', json=complete)).json() == result.json()
    exported = await client.get(f'/api/v2/trips/{trip_id}/export')
    assert exported.status_code == 200
    assert exported.json()['gps'] == chunk['gps']
    assert exported.json()['point_counts']['gps'] == 61


async def test_guest_merge_moves_all_data_once_and_preserves_retry_identity(committed_client, synthetic_telemetry_batch):
    from sqlalchemy import select, delete
    from app.core.database import async_session_factory
    from app.models.db_models import User, Trip, CPALead
    from app.models.recordings import TripRecording, RecordingVehicle
    from app.api.auth import _get_or_merge_yandex_user, _build_anonymized_yandex_id
    client = committed_client
    guest_id = client.audit_user_id
    raw_yandex_id = 'audit-' + uuid.uuid4().hex
    ya_id = _build_anonymized_yandex_id(raw_yandex_id)
    body = dict(synthetic_telemetry_batch, session_id='legacy-merge-retry', user_id=guest_id)
    original = (await client.post('/api/v1/telemetry/session', json=body)).json()
    recorded_id = 'merge_' + uuid.uuid4().hex
    await client.put('/api/v2/vehicle', json={'vehicle_id': 'guest-car'})
    await client.post('/api/v2/trips', json={'trip_id': recorded_id, 'started_at_ms': 1700000000000, 'vehicle': {'vehicle_id': 'guest-car'}})
    await client.post('/api/v1/cpa/claim', json={'user_id': guest_id, 'promo_code': 'merge-test'})
    try:
        for _ in range(2):
            async with async_session_factory() as session:
                target = await _get_or_merge_yandex_user(session, raw_yandex_id, guest_id)
                assert target.total_trips == 1
                assert target.total_savings_rub == original['cost_savings_rub']
        response = await client.post('/api/v1/telemetry/session', json=dict(body, user_id=ya_id), headers={'Authorization': 'Bearer ' + create_access_token(ya_id)})
        assert response.status_code == 200, response.text
        assert response.json() == original
        async with async_session_factory() as session:
            assert (await session.get(User, guest_id)).total_trips == 0
            assert (await session.get(TripRecording, recorded_id)).user_id == ya_id
            assert await session.get(RecordingVehicle, guest_id) is None
            assert (await session.get(RecordingVehicle, ya_id)).profile['vehicle_id'] == 'guest-car'
            assert (await session.scalar(select(CPALead).where(CPALead.user_id == ya_id))).promo_code == 'merge-test'
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(User).where(User.id == ya_id))
            await session.commit()


async def test_additive_migration_preserves_old_data_and_is_repeatable(monkeypatch):
    from sqlalchemy import text, select
    from sqlalchemy.ext.asyncio import create_async_engine
    import app.core.database as database
    from app.models.db_models import User, Trip
    schema = 'audit_migration_' + uuid.uuid4().hex
    original = database.engine
    async with original.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
    isolated = create_async_engine(database.DATABASE_URL, connect_args={'server_settings': {'search_path': schema}})
    try:
        async with isolated.begin() as connection:
            await connection.run_sync(database.Base.metadata.create_all)
            await connection.execute(User.__table__.insert().values(id='migration-user'))
            await connection.execute(Trip.__table__.insert().values(id=str(uuid.uuid4()), user_id='migration-user', car_id='car', distance_km=2.5, duration_seconds=60))
            for name in ['board_computer_consumption_l100km', 'client_session_id', 'request_hash', 'calculation_result']:
                await connection.execute(text(f'ALTER TABLE trips DROP COLUMN {name}'))
            await connection.execute(text('ALTER TABLE users DROP COLUMN car_name, DROP COLUMN custom_profile'))
        monkeypatch.setattr(database, 'engine', isolated)
        await database.initialize_schema()
        await database.initialize_schema()
        async with isolated.connect() as connection:
            assert await connection.scalar(select(Trip.distance_km)) == 2.5
            assert await connection.scalar(select(User.id)) == 'migration-user'
            assert await connection.scalar(select(Trip.calculation_result)) is None
    finally:
        monkeypatch.setattr(database, 'engine', original)
        await isolated.dispose()
        async with original.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await original.dispose()


async def test_cross_owner_concurrent_uuid_collision_is_409_not_500(committed_client, synthetic_telemetry_batch):
    import asyncio
    from app.core.database import async_session_factory
    from app.models.db_models import User
    from sqlalchemy import delete
    client = committed_client
    guest = (await client.post('/api/v1/auth/guest', json={})).json()
    shared_id = str(uuid.uuid4())
    first = dict(synthetic_telemetry_batch, session_id=shared_id, user_id=client.audit_user_id)
    second = dict(first, user_id=guest['user_id'])
    try:
        results = await asyncio.gather(client.post('/api/v1/telemetry/session', json=first), client.post('/api/v1/telemetry/session', json=second, headers={'Authorization': 'Bearer ' + guest['token']}))
        assert sorted(response.status_code for response in results) == [200, 409]
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(User).where(User.id == guest['user_id']))
            await session.commit()


async def test_dashboard_month_total_and_fuel_are_independent(committed_client):
    from app.core.database import async_session_factory
    from app.models.db_models import User, Trip
    current = datetime.now(timezone.utc)
    previous = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    owner = committed_client.audit_user_id
    async with async_session_factory() as session:
        user = await session.get(User, owner)
        user.total_savings_rub = 50
        user.total_trips = 2
        session.add_all([Trip(id=str(uuid.uuid4()), user_id=owner, car_id='car', duration_seconds=60, distance_km=1, total_savings_rub=amount, created_at=date) for amount,date in [(45,previous),(5,current)]])
        await session.commit()
    result = (await committed_client.get(f'/api/v1/users/{owner}/dashboard')).json()
    assert result['total_savings_rub'] == 50
    assert result['month_savings_rub'] == 5
    assert result['total_fuel_saved_liters'] == 0
