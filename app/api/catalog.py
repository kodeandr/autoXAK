"""Additive paged model taxonomy API; the legacy trim catalog remains unchanged."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.catalog import CatalogImport, CatalogModel, CatalogState
from app.services.catalog_import import STATE_ID, lookup_model

router = APIRouter(prefix='/api/v1/vehicles/catalog/taxonomy', tags=['catalog'])


def model_view(model):
    return dict(id=model.id, make_id=model.make_id, make=model.make_name, name=model.name,
                snapshot_id=model.import_id, years=model.details.get('years_claim'),
                selection_level='model', modifications_available=False,
                technical_parameters=None, requires_fuel_rate=True)


@router.get('/makes')
async def makes(db: AsyncSession = Depends(get_db)):
    state = await db.get(CatalogState, STATE_ID)
    imported = await db.get(CatalogImport, state.active_import_id) if state and state.active_import_id else None
    if imported is None:
        return {'snapshot_id': None, 'makes': []}
    counts = dict((await db.execute(select(CatalogModel.make_id, func.count()).where(CatalogModel.import_id == imported.id).group_by(CatalogModel.make_id))).all())
    return {'snapshot_id': imported.id, 'makes': [dict(id=m['id'], name=m['name'], models=counts.get(m['id'], 0))
                                                for m in imported.makes if counts.get(m['id'], 0)]}


@router.get('/models')
async def models(make_id: str = Query(min_length=1, max_length=100), snapshot_id: str = Query(pattern='^[a-f0-9]{64}$'),
                 offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500), db: AsyncSession = Depends(get_db)):
    state = await db.get(CatalogState, STATE_ID)
    if not state or state.active_import_id != snapshot_id:
        raise HTTPException(409, 'Каталог обновлён. Повторите выбор автомобиля.')
    condition = (CatalogModel.import_id == snapshot_id, CatalogModel.make_id == make_id)
    total = await db.scalar(select(func.count()).select_from(CatalogModel).where(*condition))
    rows = (await db.scalars(select(CatalogModel).where(*condition).order_by(CatalogModel.name, CatalogModel.id).offset(offset).limit(limit))).all()
    return {'snapshot_id': snapshot_id, 'models': [model_view(m) for m in rows], 'total': total}


@router.get('/models/{car_id}')
async def model(car_id: str, db: AsyncSession = Depends(get_db)):
    row = await lookup_model(db, car_id)
    if row is None:
        raise HTTPException(404, 'Модель не найдена')
    return model_view(row)
