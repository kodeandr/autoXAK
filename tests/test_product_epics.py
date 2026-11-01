import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import numpy as np
import pytest
from sqlalchemy import delete, select

from app.core.database import async_session_factory
from app.core.security import create_access_token
from app.models.db_models import User, Trip, TripTelemetryRecord, LoginAttempt
from app.services.progress import summarize_progress
from app.services.twin_engine import TwinEngine


def credentials():
    # Letters only, including the random portion.
    return {"login": "Test" + uuid.uuid4().hex[:10].translate(str.maketrans('0123456789','klmnopqrst')),
            "password": "Abc!12345"}


@pytest.mark.parametrize('login,password', [('abcd','Abc!12345'), ('a'*16,'Abc!12345'), ('abc12','Abc!12345'), ('логин','Abc!12345'), ('Abcde','1234567'), ('Abcde','x'*16)])
async def test_credentials_reject_invalid_inputs(async_client, login, password):
    for route in ('login','register'):
        assert (await async_client.post('/api/v1/auth/'+route,json={'login':login,'password':password})).status_code == 422


async def test_registration_preserves_guest_and_login_four_attempt_lock(committed_client):
    client=committed_client
    data=credentials()
    request=dict(data,guest_id=client.audit_user_id)
    response=await client.post('/api/v1/auth/register',json=request)
    assert response.status_code==200, response.text
    assert response.json()['user_id']==client.audit_user_id
    assert response.json()['is_guest'] is False
    # Same proven guest can recover a lost registration response.
    assert (await client.post('/api/v1/auth/register',json=request)).json()['user_id']==client.audit_user_id
    duplicate=await client.post('/api/v1/auth/register',json=data,headers={'Authorization':''})
    assert duplicate.status_code==409
    for attempt in range(4):
        invalid=await client.post('/api/v1/auth/login',json=dict(data,password='abc!12345'),headers={'Authorization':''})
        assert invalid.status_code==(429 if attempt==3 else 401)
    locked=await client.post('/api/v1/auth/login',json=data,headers={'Authorization':''})
    assert locked.status_code==429 and 0<int(locked.headers['Retry-After'])<=300
    import hashlib
    key=hashlib.sha256(data['login'].lower().encode()).hexdigest()
    async with async_session_factory() as db:
        attempt=await db.get(LoginAttempt,key)
        attempt.blocked_until=datetime.now(timezone.utc)-timedelta(seconds=1)
        attempt.window_started_at=datetime.now(timezone.utc)-timedelta(minutes=6)
        await db.commit()
    accepted=await client.post('/api/v1/auth/login',json=data,headers={'Authorization':''})
    assert accepted.status_code==200


async def test_login_merges_guest_atomically_once_and_revokes_old_identity(committed_client, synthetic_telemetry_batch):
    client=committed_client; data=credentials()
    await client.post('/api/v1/auth/register',json=dict(data,guest_id=client.audit_user_id))
    guest=(await client.post('/api/v1/auth/guest',json={},headers={'Authorization':''})).json()
    headers={'Authorization':'Bearer '+guest['token']}
    try:
        trip=await client.post('/api/v1/telemetry/session',json=dict(synthetic_telemetry_batch,user_id=guest['user_id']),headers=headers)
        assert trip.status_code==200
        request=dict(data,guest_id=guest['user_id'])
        replies=await asyncio.gather(*[client.post('/api/v1/auth/login',json=request,headers=headers) for _ in range(2)])
        assert [r.status_code for r in replies]==[200,200]
        dash=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
        assert dash['total_trips']==1
        assert dash['total_savings_rub']==trip.json()['cost_savings_rub']
        assert (await client.get('/api/v1/auth/me',headers=headers)).status_code==401
        forged=await client.post('/api/v1/auth/login',json=request,headers={'Authorization':''})
        assert forged.status_code==403
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(User).where(User.id==guest['user_id']));await db.commit()


@pytest.mark.parametrize('fraction,expected', [(0,40.62),(.1,27.08),(.3,0),(.9,0)])
def test_approved_multiplier_policy_exact_examples(fraction,expected):
    profile=SimpleNamespace(base_city_fuel_rate_l100km=8.5)
    ax=np.r_[np.full(int(100*fraction),1.81), np.zeros(100-int(100*fraction))]
    result=TwinEngine(profile=profile,fuel_price_rub=72.4).evaluate_financial_delta(distance_km=10,user_ax=ax)
    assert result['total_savings_rub']==expected
    assert result['k_user']==pytest.approx(1+fraction*2.2)
    assert result['causal_savings_verified'] is False
    assert result['policy_version']=='twin-fuel-mvp-1'
    assert TwinEngine().evaluate_financial_delta(distance_km=.01,user_ax=[])['total_savings_rub']==0


async def test_full_array_and_concurrent_post_replays_credit_exactly_once(committed_client, synthetic_telemetry_batch):
    client=committed_client
    body=dict(deepcopy(synthetic_telemetry_batch),user_id=client.audit_user_id)
    result=await client.post('/api/v1/telemetry/session',json=body)
    assert result.status_code==200, result.text
    receipt=result.json();sid=receipt['session_id']
    before=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
    assert before['total_savings_rub']==receipt['cost_savings_rub'] and before['total_trips']==1
    assert len((await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'])==1
    async with async_session_factory() as db:
        stored=await db.get(TripTelemetryRecord,sid)
        assert len(stored.payload)==len(body['telemetry_stream'])
        assert [p['t'] for p in stored.payload]==[p['t'] for p in body['telemetry_stream']]
    claims=await asyncio.gather(*[client.post('/api/v1/telemetry/session',json=body) for _ in range(4)])
    assert all(r.status_code==200 for r in claims)
    assert all(r.json()==receipt for r in claims)
    after=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
    assert after['total_trips']==1 and after['total_savings_rub']==receipt['cost_savings_rub']
    assert (await client.post('/api/v1/telemetry/session',json=body)).json()==receipt
    assert len((await client.get(f'/api/v1/users/{client.audit_user_id}/trips')).json()['trips'])==1
    assert (await client.post('/api/v1/telemetry/session',json=body,headers={'Authorization':'Bearer '+create_access_token('missing')})).status_code==401


def test_progress_ignores_legacy_scores_and_separates_calendar_periods():
    now=datetime(2026,11,1,12,tzinfo=timezone.utc)
    def trip(days,score,policy=True):
        return SimpleNamespace(created_at=now-timedelta(days=days),calculation_result={
            'smooth_score':score,'metrics':{'math_status':{'style_score_policy':'smooth-sample-fraction-1' if policy else 'old'}}})
    result=summarize_progress([trip(0,90),trip(1,80),trip(8,70),trip(9,60),trip(0,100,False)],7,180,now)
    assert result['average']==85 and result['previous_average']==65 and result['delta']==20
    assert result['trip_count']==2 and result['sufficient_data']
    assert len(result['points'])==7 and sum(p['score'] is not None for p in result['points'])==2
    assert summarize_progress([trip(0,90)],7,0,now)['delta'] is None


def test_shock_at_speed_boundary_does_not_create_derivative_event():
    ax=np.zeros(1000);z=np.full(1000,.6)
    eligible=np.arange(1000)>500
    assert TwinEngine.filter_road_anomalies(ax,z,dt=.02,eligible_mask=eligible)[1]==0
    z[600:610]=5
    assert TwinEngine.filter_road_anomalies(ax,z,dt=.02,eligible_mask=eligible)[1]==1
    assert TwinEngine.filter_road_anomalies(ax,z,dt=.02,eligible_mask=np.zeros(1000,bool))[1]==0


@pytest.mark.parametrize('distance', [0, .001, .01])
def test_short_trip_never_earns_money_or_liters(distance):
    result=TwinEngine().evaluate_financial_delta(distance_km=distance,user_ax=[])
    assert result['fuel_savings_rub']==result['total_savings_rub']==result['saved_fuel_liters']==0.0
    assert result['n_total']==1


def test_dynamic_stress_threshold_is_strict_and_uses_full_sample_fraction():
    profile=SimpleNamespace(base_city_fuel_rate_l100km=8.5)
    result=TwinEngine(profile=profile).evaluate_financial_delta(distance_km=10,user_ax=np.r_[np.full(90,1.8),np.full(10,1.800001)])
    assert result['n_hard']==10 and result['n_total']==100
    assert result['price_rub_per_l']==72.4 and result['total_savings_rub']==27.08
    assert result['saved_fuel_liters']==pytest.approx(.374)


@pytest.mark.parametrize('distance,ax', [(float('nan'),[0]),(-1,[0]),(1,[float('inf')]),(1,[[0]])])
def test_invalid_financial_observations_are_rejected(distance,ax):
    with pytest.raises(ValueError):
        TwinEngine().evaluate_financial_delta(distance_km=distance,user_ax=ax)


async def test_two_distinct_sessions_add_server_receipts_atomically(committed_client, synthetic_telemetry_batch):
    client=committed_client
    bodies=[dict(synthetic_telemetry_batch,user_id=client.audit_user_id,session_id=str(uuid.uuid4())) for _ in range(2)]
    responses=await asyncio.gather(*[client.post('/api/v1/telemetry/session',json=body) for body in bodies])
    assert all(response.status_code==200 for response in responses)
    dashboard=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
    assert dashboard['total_trips']==2
    assert dashboard['total_savings_rub']==round(sum(response.json()['cost_savings_rub'] for response in responses),2)
    async with async_session_factory() as db:
        user=await db.get(User,client.audit_user_id)
        assert user.total_fuel_saved_liters==pytest.approx(sum(response.json()['fuel_saved_liters'] for response in responses))
