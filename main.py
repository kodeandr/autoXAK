from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import numpy as np
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List, Dict
from app.services.benchmark_engine import BenchmarkEngine, VerificationReport
from fastapi.responses import FileResponse

from app.core.database import engine, Base, get_db
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
from app.services.gis_service import GISService

import os
import uuid

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title="autoXAK Telemetry Engine",
    description="Пайплайн цифровой фильтрации, предиктивного расчета износа, LBS 2GIS и персистентности данных",
    version="1.2.0",
    lifespan=lifespan
)

benchmark_engine = BenchmarkEngine()
signal_filter = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
wear_engine = WearEngine(ambient_temp_c=20.0)
twin_engine = AggressiveTwinEngine()
gis_service = GISService()

class VerificationPayload(BaseModel):
    session_id: str
    user_id: str
    car_id: str
    telemetry_stream: List[dict]
    obd_ground_truth: Dict[str, List[float]]

@app.post("/api/v1/analytics/verify-run", response_model=VerificationReport)
async def verify_experiment_run(payload: VerificationPayload):
    """
    Академический эндпоинт верификации: сопоставление оценки autoXAK 
    с эталоном CAN-шины OBD2 для подтверждения исследовательской гипотезы диплома.
    """
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(status_code=422, detail="Недостаточный объем телеметрии.")

    # 1. Извлечение векторов
    raw_ax = [p["ax"] for p in stream]
    raw_ay = [p["ay"] for p in stream]
    raw_az = [p["az"] for p in stream]
    speeds = np.array([p["speed"] for p in stream], dtype=np.float64)

    # 2. 2GIS контекст
    coords = [{"lat": p.get("lat", 55.75), "lon": p.get("lon", 37.61)} for p in stream]
    road_context = await gis_service.get_route_context(coords)

    # 3. Фильтрация DSP
    filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
    horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

    # 4. Расчет autoXAK
    wear_stats = wear_engine.compute_oil_wear(
        speeds_mps=speeds, 
        horizontal_acc=horiz_acc, 
        traffic_score=road_context.traffic_score,
        dt=0.02
    )

    # 5. Верификация через Benchmark Engine
    obd = payload.obd_ground_truth
    report = benchmark_engine.evaluate_experiment(
        autoxak_engine_hours=wear_stats["equivalent_engine_hours"],
        obd_rpm=obd["engine_rpm"],
        obd_load=obd["engine_load"],
        obd_temp=obd["oil_temperature"],
        user_savings=[6.67, 8.20, 5.40, 7.80, 9.10],  # Историческая выборка экономии
        dt=0.02
    )

    return report

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "autoXAK Wear & Cost Processor",
        "version": "1.2.0"
    }

@app.post("/api/v1/telemetry/session", response_model=TripCalculationResult)
async def process_telemetry_session(
    payload: TripSessionPayload, 
    db: AsyncSession = Depends(get_db)
):
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(
            status_code=422, 
            detail="Недостаточно точек телеметрии для валидации (минимум 1 секунда / 50 точек)."
        )

    # 1. Извлечение векторов сенсоров
    raw_ax = [p.ax for p in stream]
    raw_ay = [p.ay for p in stream]
    raw_az = [p.az for p in stream]
    speeds = np.array([p.speed for p in stream], dtype=np.float64)

    # 2. Обогащение дорожным контекстом через API 2GIS
    coords = [{"lat": p.lat, "lon": p.lon} for p in stream if p.lat and p.lon]
    road_context = await gis_service.get_route_context(coords)

    # 3. Цифровая фильтрация (DSP)
    filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
    horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

    # 4. Физико-математический расчет износа с учетом пробок из 2GIS
    wear_stats = wear_engine.compute_oil_wear(
        speeds_mps=speeds, 
        horizontal_acc=horiz_acc, 
        traffic_score=road_context.traffic_score,
        dt=0.02
    )
    
    distance_meters = np.sum(speeds * 0.02)
    distance_km = float(distance_meters / 1000.0)
    duration_sec = len(stream) * 0.02

    # 5. Сравнение с агрессивным двойником
    savings = twin_engine.simulate_twin_and_delta(
        speeds_mps=speeds,
        user_wear_percent=wear_stats["oil_wear_percent"],
        dt=0.02
    )

    # 6. Персистентность в PostgreSQL
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

# Генерируем гарантированно валидный UUID для БД
    actual_id = str(uuid.uuid4())

    new_trip = Trip(
        id=actual_id,
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

    return TripCalculationResult(
        session_id=actual_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        oil_wear_percent=wear_stats["oil_wear_percent"],
        cost_savings_rub=savings["total_savings_rub"],
        status="PROCESSED"
    )

@app.get("/api/v1/users/{user_id}/dashboard", response_model=DashboardResponse)
async def get_user_dashboard(user_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    remaining_oil = max(0.0, round(100.0 - user.current_oil_wear_percent, 1))
    cpa_active = remaining_oil <= 10.0
    cpa_text = "Пора менять масло. Скидка 15% на рекомендованное масло по вашей манере езды" if cpa_active else None

    return DashboardResponse(
        user_id=user.id,
        month_savings_rub=round(user.total_savings_rub, 2),
        oil_remaining_percent=remaining_oil,
        ghost_twin_status="Лихач-новичок (Средняя сложность)",
        cpa_recommended=cpa_active,
        cpa_offer_text=cpa_text
    )

from sqlalchemy import select, func

@app.get("/api/v1/users/{user_id}/trips", response_model=TripHistoryResponse)
async def get_user_trips(user_id: str, limit: int = 100, db: AsyncSession = Depends(get_db)):
    # 1. Считаем реальное общее количество поездок пользователя в БД
    count_stmt = select(func.count(Trip.id)).where(Trip.user_id == user_id)
    total_count = (await db.execute(count_stmt)).scalar() or 0

    # 2. Выбираем последние поездки с учетом расширенного лимита
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
        total_trips=total_count,  # Честное суммарное количество из БД
        trips=trip_items
    )
    
@app.get("/", include_in_schema=False)
async def root():
    html_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
    if not os.path.exists(html_path):
        html_path = "static/index.html"
    return FileResponse(html_path)