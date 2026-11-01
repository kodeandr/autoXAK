"""Import/activation contracts with a real PostgreSQL and owner-visible product flows."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import delete, select, update, func
from sqlalchemy.sql.dml import Insert

from app.cli.normalize_vehicle_catalog import run as normalize
from app.core.database import async_session_factory, engine
from app.models.catalog import CatalogImport, CatalogModel, CatalogState
from app.models.db_models import User, Trip, VehicleMake, VehicleModel, VehicleTrim
from app.services.catalog_import import plan_review, load_plan, set_active, STATE_ID, CatalogInputError, vehicle_id


@pytest.fixture
def make_review(tmp_path):
    source_id='fixture_'+uuid.uuid4().hex
    def make(*, license_status='project_license', suffix='', count=2):
        directory=tmp_path/('review-'+uuid.uuid4().hex)
        rows=[{'id':'AuditMake','name':'AuditMake','models':[
            {'id':str(i),'mark_id':'AuditMake','name':f'Model {i}{suffix}','year_from':2015} for i in range(count)]}]
        raw=json.dumps(rows).encode()
        data_path=tmp_path/(uuid.uuid4().hex+'.json');data_path.write_bytes(raw)
        manifest=tmp_path/(uuid.uuid4().hex+'.source.json')
        manifest.write_text(json.dumps({'source_id':source_id,'format':'carsbase_taxonomy','revision':'test'+suffix,
            'source_url':'test:synthetic-fixture','expected_sha256':hashlib.sha256(raw).hexdigest(),
            'license_status':license_status,'license_evidence':'Project-owned synthetic fixture; no real vehicle specifications.'}))
        normalize(data_path,manifest,directory)
        return directory,plan_review(directory)
    return make


@pytest.fixture
async def catalog_guard():
    async with async_session_factory() as db:
        state=await db.get(CatalogState,STATE_ID)
        previous=state.active_import_id if state else None
    ids=[]
    try:
        yield ids
    finally:
        async with async_session_factory() as db:
            await db.execute(update(CatalogState).where(CatalogState.id==STATE_ID).values(active_import_id=previous))
            if ids:
                await db.execute(delete(CatalogModel).where(CatalogModel.import_id.in_(ids)))
                await db.execute(delete(CatalogImport).where(CatalogImport.id.in_(ids)))
            await db.commit()
        await engine.dispose()


async def apply(plan,activate=True):
    async with async_session_factory() as db,db.begin():
        return await load_plan(db,plan,activate=activate)


async def legacy_rows():
    async with async_session_factory() as db:
        return {model.__tablename__:[dict(row) for row in (await db.execute(select(model.__table__).order_by(model.id))).mappings()]
                for model in (VehicleMake,VehicleModel,VehicleTrim,User,Trip)}


def test_plan_can_run_without_database_access(make_review):
    directory,plan=make_review()
    env=dict(os.environ,DATABASE_URL='postgresql+asyncpg://none:none@127.0.0.1:1/unreachable')
    reply=subprocess.run([sys.executable,'-m','app.cli.import_vehicle_catalog','plan',str(directory)],env=env,capture_output=True,text=True,timeout=10)
    assert reply.returncode==0,reply.stderr
    result=json.loads(reply.stdout)
    assert result['snapshot_id']==plan.snapshot_id and result['models']==2 and result['modifications']==0


async def test_unresolved_source_cannot_write_or_activate(make_review,catalog_guard):
    _,plan=make_review(license_status='unresolved')
    assert plan.summary()['can_load'] is False
    before=await legacy_rows()
    with pytest.raises(CatalogInputError,match='unresolved'):
        await apply(plan)
    assert await legacy_rows()==before
    async with async_session_factory() as db:
        assert await db.get(CatalogImport,plan.snapshot_id) is None


@pytest.mark.parametrize('tamper',['missing_completion','bad_checksum','changed_normalized'])
def test_incomplete_or_tampered_package_is_rejected(make_review,tamper):
    directory,_=make_review()
    if tamper=='missing_completion':
        (directory/'COMPLETE.json').unlink()
    else:
        target=directory/'normalized.json';data=json.loads(target.read_text());data['models'][0]['name']='Forged Model'
        target.write_text(json.dumps(data))
        if tamper=='changed_normalized':
            completion=directory/'COMPLETE.json';meta=json.loads(completion.read_text())
            meta['files_sha256']['normalized.json']=hashlib.sha256(target.read_bytes()).hexdigest();completion.write_text(json.dumps(meta))
    with pytest.raises(CatalogInputError):plan_review(directory)


async def test_load_is_hidden_until_activation_and_does_not_change_legacy_data(committed_client,make_review,catalog_guard):
    _,plan=make_review();catalog_guard.append(plan.snapshot_id)
    before=await legacy_rows()
    result=await apply(plan,activate=False)
    assert result['loaded'] and not result['activated']
    assert (await committed_client.get('/api/v1/vehicles/catalog/taxonomy/makes')).json()['makes']==[]
    async with async_session_factory() as db,db.begin():await set_active(db,plan.snapshot_id)
    makes=(await committed_client.get('/api/v1/vehicles/catalog/taxonomy/makes')).json()
    assert makes['snapshot_id']==plan.snapshot_id and makes['makes'][0]['models']==2
    make_id=plan.bundle['makes'][0]['id']
    first=(await committed_client.get('/api/v1/vehicles/catalog/taxonomy/models',params={'make_id':make_id,'snapshot_id':plan.snapshot_id,'limit':1})).json()
    second=(await committed_client.get('/api/v1/vehicles/catalog/taxonomy/models',params={'make_id':make_id,'snapshot_id':plan.snapshot_id,'offset':1,'limit':1})).json()
    assert first['total']==2 and first['models'][0]['id']!=second['models'][0]['id']
    assert first['models'][0]['technical_parameters'] is None and first['models'][0]['modifications_available'] is False
    assert await legacy_rows()==before


async def test_concurrent_repeated_load_is_idempotent(make_review,catalog_guard):
    _,plan=make_review();catalog_guard.append(plan.snapshot_id)
    replies=await asyncio.gather(*[apply(plan) for _ in range(3)])
    assert all(r['snapshot_id']==plan.snapshot_id for r in replies)
    async with async_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(CatalogImport).where(CatalogImport.id==plan.snapshot_id))==1
        assert await db.scalar(select(func.count()).select_from(CatalogModel).where(CatalogModel.import_id==plan.snapshot_id))==2


async def test_mid_import_failure_rolls_back_rows_and_active_pointer(make_review,catalog_guard,monkeypatch):
    _,old=make_review();catalog_guard.append(old.snapshot_id);await apply(old)
    _,new=make_review(suffix='next',count=501);catalog_guard.append(new.snapshot_id)
    async with async_session_factory() as db:
        execute=db.execute;inserts=0
        async def failing(statement,*args,**kwargs):
            nonlocal inserts
            if isinstance(statement,Insert) and statement.table.name=='catalog_models':
                inserts+=1
                if inserts==2:raise RuntimeError('Injected second batch failure')
            return await execute(statement,*args,**kwargs)
        monkeypatch.setattr(db,'execute',failing)
        with pytest.raises(RuntimeError):
            async with db.begin():await load_plan(db,new,activate=True)
    async with async_session_factory() as db:
        assert await db.get(CatalogImport,new.snapshot_id) is None
        assert await db.scalar(select(func.count()).select_from(CatalogModel).where(CatalogModel.import_id==new.snapshot_id))==0
        assert (await db.get(CatalogState,STATE_ID)).active_import_id==old.snapshot_id


async def test_model_selection_earns_money_and_survives_deactivation(committed_client,make_review,catalog_guard,synthetic_telemetry_batch):
    client=committed_client
    _,plan=make_review();catalog_guard.append(plan.snapshot_id);await apply(plan)
    car_id=vehicle_id(plan.bundle['models'][0]['id'])
    url=f'/api/v1/users/{client.audit_user_id}/profile'
    assert (await client.post(url,json={'car_id':car_id})).status_code==422
    reply=await client.post(url,json={'car_id':car_id,'fuel_consumption':8.5})
    assert reply.status_code==200,reply.text
    profile=(await client.get(url)).json()
    assert profile['car_id']==car_id and profile['fuel_consumption']==8.5
    async with async_session_factory() as db:
        user=await db.get(User,client.audit_user_id)
        assert user.custom_profile['curb_weight_kg'] is None
        assert user.custom_profile['engine_displacement_l'] is None
    body=dict(synthetic_telemetry_batch,user_id=client.audit_user_id,car_id=car_id,session_id=str(uuid.uuid4()))
    result=await client.post('/api/v1/telemetry/session',json=body)
    assert result.status_code==200 and result.json()['cost_savings_rub']>0,result.text
    async with async_session_factory() as db,db.begin():await set_active(db,None)
    assert (await client.get('/api/v1/vehicles/catalog/taxonomy/makes')).json()['makes']==[]
    body['session_id']=str(uuid.uuid4())
    second=await client.post('/api/v1/telemetry/session',json=body)
    assert second.status_code==200 and second.json()['cost_savings_rub']==result.json()['cost_savings_rub']
    dashboard=(await client.get(f'/api/v1/users/{client.audit_user_id}/dashboard')).json()
    assert dashboard['total_trips']==2 and dashboard['total_savings_rub']==round(2*result.json()['cost_savings_rub'],2)


async def test_snapshot_switch_rejects_mixed_pages_and_rollback_restores_previous(make_review,catalog_guard,committed_client):
    _,old=make_review();_,new=make_review(suffix='next')
    catalog_guard.extend([old.snapshot_id,new.snapshot_id]);await apply(old);await apply(new)
    client=committed_client;params={'make_id':old.bundle['makes'][0]['id'],'snapshot_id':old.snapshot_id}
    assert (await client.get('/api/v1/vehicles/catalog/taxonomy/models',params=params)).status_code==409
    async with async_session_factory() as db,db.begin():await set_active(db,old.snapshot_id)
    assert (await client.get('/api/v1/vehicles/catalog/taxonomy/models',params=params)).status_code==200


async def test_bulk_taxonomy_does_not_write_legacy_tables(make_review,catalog_guard):
    _,plan=make_review(count=5000);catalog_guard.append(plan.snapshot_id)
    before=await legacy_rows()
    await apply(plan)
    async with async_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(CatalogModel).where(CatalogModel.import_id==plan.snapshot_id))==5000
    assert await legacy_rows()==before
