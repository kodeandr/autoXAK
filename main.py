from fastapi import FastAPI, HTTPException, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import numpy as np
import json
import uuid
from contextlib import asynccontextmanager

from app.core.database import engine, Base, get_db
from app.core.redis_client import init_redis, get_redis_client, CacheService
from app.models.telemetry import TripSessionPayload
from app.models.results import (
    TripCalculationResult, 
    DashboardResponse, 
    TripHistoryResponse, 
    TripSummaryItem
)
from app.models.db_models import User, Trip
from app.core.dsp.filters import SignalFilter
from app.services.wear_engine import WearEngine
from app.services.twin_engine import AggressiveTwinEngine

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_redis()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

app = FastAPI(
    title="АвтоХАК Telemetry Engine",
    description="Пайплайн цифровой фильтрации, предиктивного расчета износа, персистентности и кэширования",
    version="1.2.0",
    default_response_class=UTF8JSONResponse,
    lifespan=lifespan
)

signal_filter = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
wear_engine = WearEngine(ambient_temp_c=20.0)
twin_engine = AggressiveTwinEngine()

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "AutoHACK Wear & Cost Processor",
        "version": "1.2.0"
    }

@app.post("/api/v1/telemetry/session", response_model=TripCalculationResult)
async def process_telemetry_session(
    payload: TripSessionPayload, 
    db: AsyncSession = Depends(get_db),
    redis = Depends(get_redis_client)
):
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(
            status_code=422, 
            detail="Недостаточно точек телеметрии для валидации (минимум 1 секунда / 50 точек)."
        )

    # 1. DSP фильтрация
    raw_ax = [p.ax for p in stream]
    raw_ay = [p.ay for p in stream]
    raw_az = [p.az for p in stream]
    speeds = np.array([p.speed for p in stream], dtype=np.float64)

    filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
    horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

    # 2. Физический расчет износа
    wear_stats = wear_engine.compute_oil_wear(speeds_mps=speeds, horizontal_acc=horiz_acc, dt=0.02)
    
    distance_meters = np.sum(speeds * 0.02)
    distance_km = float(distance_meters / 1000.0)
    duration_sec = len(stream) * 0.02

    # 3. Синтез агрессивного двойника
    savings = twin_engine.simulate_twin_and_delta(
        speeds_mps=speeds,
        user_wear_percent=wear_stats["oil_wear_percent"],
        dt=0.02
    )

    # 4. Проверка и сохранение в PostgreSQL
    user_stmt = select(User).where(User.id == payload.user_id)
    result = await db.execute(user_stmt)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            id=payload.user_id,
            total_savings_rub=0.0,
            current_oil_wear_percent=0.0
        )
        db.add(user)

    user.total_savings_rub += savings["total_savings_rub"]
    user.current_oil_wear_percent = min(100.0, user.current_oil_wear_percent + wear_stats["oil_wear_percent"])

    # Защита от коллизии первичного ключа при повторной отправке того же JSON
    trip_check = await db.execute(select(Trip).where(Trip.id == payload.session_id))
    actual_session_id = payload.session_id if trip_check.scalar_one_or_none() is None else str(uuid.uuid4())

    new_trip = Trip(
        id=actual_session_id,
        user_id=payload.user_id,
        car_id=payload.car_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        equivalent_engine_hours=wear_stats["equivalent_engine_hours"],
        oil_wear_percent=wear_stats["oil_wear_percent"],
        idle_ratio=wear_stats["idle_ratio"],
        fuel_saved_rub=savings["fuel_saved_rub"],
        oil_saved_rub=savings["oil_saved_rub"],
        total_savings_rub=savings["total_savings_rub"]
    )
    db.add(new_trip)
    await db.commit()

    # 5. Инвалидация кэша в Redis
    await CacheService.invalidate_dashboard(redis, payload.user_id)

    return TripCalculationResult(
        session_id=actual_session_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        oil_wear_percent=wear_stats["oil_wear_percent"],
        cost_savings_rub=savings["total_savings_rub"],
        status="PROCESSED"
    )

@app.get("/api/v1/users/{user_id}/dashboard", response_model=DashboardResponse)
async def get_user_dashboard(
    user_id: str, 
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis = Depends(get_redis_client)
):
    # 1. Проверяем кэш Redis
    cached_data = await CacheService.get_dashboard(redis, user_id)
    if cached_data:
        response.headers["X-Cache-Status"] = "HIT"
        return json.loads(cached_data)

    response.headers["X-Cache-Status"] = "MISS"

    # 2. Выборка из PostgreSQL при Cache Miss
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    remaining_oil = max(0.0, round(100.0 - user.current_oil_wear_percent, 2))
    cpa_active = remaining_oil <= 10.0
    cpa_text = "Пора менять масло. Скидка 15% на Shell Helix по вашей манере езды" if cpa_active else None

    dashboard = DashboardResponse(
        user_id=user.id,
        month_savings_rub=round(user.total_savings_rub, 2),
        oil_remaining_percent=remaining_oil,
        ghost_twin_status="Лихач-новичок (Средняя сложность)",
        cpa_recommended=cpa_active,
        cpa_offer_text=cpa_text
    )

    # 3. Запись в кэш на 60 секунд
    await CacheService.set_dashboard(redis, user_id, dashboard.model_dump_json(), ttl_sec=60)
    return dashboard

@app.get("/api/v1/users/{user_id}/trips", response_model=TripHistoryResponse)
async def get_user_trips(user_id: str, limit: int = 10, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Trip)
        .where(Trip.user_id == user_id)
        .order_by(Trip.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    trips = res.scalars().all()

    trip_items = [
        TripSummaryItem(
            session_id=t.id,
            created_at=t.created_at,
            duration_seconds=t.duration_seconds,
            distance_km=t.distance_km,
            total_savings_rub=t.total_savings_rub,
            oil_wear_percent=t.oil_wear_percent
        )
        for t in trips
    ]

    return TripHistoryResponse(
        user_id=user_id,
        total_trips=len(trip_items),
        trips=trip_items
    )