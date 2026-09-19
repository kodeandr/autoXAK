import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager

import numpy as np
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

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

try:
    from app.services.twin_engine import TwinEngine, AggressiveTwinEngine
except ImportError:
    from app.services.twin_engine import TwinEngine
    AggressiveTwinEngine = TwinEngine

try:
    from app.models.vehicle_profiles import VehiclePhysicalProfile, VEHICLE_REGISTRY
except ImportError:
    VehiclePhysicalProfile = None
    VEHICLE_REGISTRY = {}

from app.services.gis_service import GISService
from app.services.benchmark_engine import BenchmarkEngine, VerificationReport


# --- Резервный и эталонный профили ТС ---

def get_default_profile() -> Any:
    if VEHICLE_REGISTRY:
        if "haval_jolion_15t" in VEHICLE_REGISTRY:
            return VEHICLE_REGISTRY["haval_jolion_15t"]
        if "test_car_vag_2.0tsi" in VEHICLE_REGISTRY:
            return VEHICLE_REGISTRY["test_car_vag_2.0tsi"]
        return next(iter(VEHICLE_REGISTRY.values()))

    class _FallbackOilProfile:
        nominal_service_hours: float = 250.0
        base_activation_energy_jmol: float = 75000.0
        aged_activation_energy_jmol: float = 45000.0
        zddp_activation_volume_m3: float = 1.2e-29
        zddp_activation_energy_jmol: float = 85000.0
        oil_grade: str = "5W-30"

    class _FallbackProfile:
        car_id: str = "haval_jolion_15t"
        brand: str = "Haval"
        model: str = "Jolion 1.5T 4WD"
        curb_weight_kg: float = 1505.0
        rolling_resistance_coeff: float = 0.012
        drag_coefficient_area: float = 0.32 * 2.38
        drivetrain_efficiency: float = 0.90
        base_city_fuel_rate_l100km: float = 8.5
        engine_displacement_l: float = 1.5
        rated_power_kw: float = 110.0
        oil_capacity_l: float = 3.8
        oil_profile: Any = _FallbackOilProfile()

    return _FallbackProfile()


def resolve_car_profile(car_id: Optional[str]) -> Any:
    if car_id and VEHICLE_REGISTRY and car_id in VEHICLE_REGISTRY:
        return VEHICLE_REGISTRY[car_id]
    return get_default_profile()


# --- DTO Схемы ---

class TripPeriodItem(BaseModel):
    session_id: str
    created_at: datetime
    duration_seconds: float
    distance_km: float
    idle_ratio: float
    oil_wear_percent: float
    total_savings_rub: float


class TripsPeriodSummaryMetrics(BaseModel):
    total_trips: int = Field(..., description="Количество поездок за период")
    total_distance_km: float = Field(..., description="Суммарная дистанция, км")
    total_duration_hours: float = Field(..., description="Суммарное время за рулем, ч")
    total_savings_rub: float = Field(..., description="Совокупная экономия, ₽")
    total_oil_wear_percent: float = Field(..., description="Накопленный износ масла, %")
    avg_speed_kmh: float = Field(..., description="Средняя скорость движения, км/ч")
    avg_idle_ratio: float = Field(..., description="Средняя доля пробок / холостого хода")


class PeriodAnalyticsResponse(BaseModel):
    user_id: str
    start_date: datetime
    end_date: datetime
    summary: TripsPeriodSummaryMetrics
    trips: List[TripPeriodItem]


class UserProfileUpdatePayload(BaseModel):
    user_id: str
    car_id: str = Field(default="haval_jolion_15t", description="Идентификатор ТС в реестре")
    current_oil_wear_percent: float = Field(default=0.0, ge=0.0, le=100.0, description="Текущий накопленный износ масла, %")
    fuel_price_rub: float = Field(default=62.00, gt=0.0, description="Цена литра топлива, ₽")
    service_cost_rub: float = Field(default=9500.0, gt=0.0, description="Стоимость планового ТО, ₽")


class VerificationPayload(BaseModel):
    session_id: str
    user_id: str
    car_id: str
    telemetry_stream: List[dict]
    obd_ground_truth: Dict[str, List[float]]


# --- Инициализация приложения FastAPI ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="autoXAK Telemetry Engine",
    description="Пайплайн цифровой фильтрации, предиктивного расчета износа, LBS 2GIS и персистентности данных",
    version="1.3.0",
    lifespan=lifespan
)

# Монтирование статики
static_candidates = [
    os.path.join(os.path.dirname(__file__), "..", "static"),
    os.path.join(os.path.dirname(__file__), "static"),
    "static",
    "/app/static"
]
STATIC_DIR = next((cand for cand in static_candidates if os.path.isdir(cand)), None)
if STATIC_DIR:
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DEFAULT_PROFILE = get_default_profile()
benchmark_engine = BenchmarkEngine()
signal_filter = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)

try:
    wear_engine = WearEngine(profile=DEFAULT_PROFILE)
except TypeError:
    wear_engine = WearEngine(DEFAULT_PROFILE)

try:
    twin_engine = TwinEngine(profile=DEFAULT_PROFILE)
except TypeError:
    twin_engine = TwinEngine(DEFAULT_PROFILE)

gis_service = GISService()


# --- Раздача страниц ---

def _resolve_static_file(filename: str) -> str:
    search_paths = [
        os.path.join(os.path.dirname(__file__), "..", "static", filename),
        os.path.join(os.path.dirname(__file__), "static", filename),
        os.path.join("static", filename),
        os.path.join("/app/static", filename)
    ]
    for path in search_paths:
        if os.path.exists(path):
            return path
    raise HTTPException(status_code=404, detail=f"Файл {filename} не найден")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(_resolve_static_file("index.html"))


@app.get("/history", include_in_schema=False)
async def history_page():
    return FileResponse(_resolve_static_file("history.html"))


@app.get("/setup", include_in_schema=False)
async def setup_page():
    return FileResponse(_resolve_static_file("setup.html"))


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "autoXAK Wear & Cost Processor",
        "version": "1.3.0"
    }


# --- Основные эндпоинты ---

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

    raw_ax = np.array([p.ax for p in stream], dtype=np.float64)
    raw_ay = np.array([p.ay for p in stream], dtype=np.float64)
    raw_az = np.array([p.az for p in stream], dtype=np.float64)
    speeds = np.array([p.speed for p in stream], dtype=np.float64)

    coords = [{"lat": p.lat, "lon": p.lon} for p in stream if p.lat and p.lon]
    road_context = await gis_service.get_route_context(coords)

    filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
    horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

    profile = resolve_car_profile(payload.car_id)
    try:
        trip_wear_engine = WearEngine(profile=profile)
    except TypeError:
        trip_wear_engine = WearEngine(profile)

    try:
        trip_twin_engine = TwinEngine(profile=profile)
    except TypeError:
        trip_twin_engine = TwinEngine(profile)

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

    current_life_pct = max(0.0, 100.0 - user.current_oil_wear_percent)
    dt = 0.02
    duration_sec = len(stream) * dt
    distance_meters = np.sum(speeds * dt)
    distance_km = float(distance_meters / 1000.0)

    # 1. Расчет износа масла
    if hasattr(trip_wear_engine, "evaluate_trip_wear"):
        wear_stats = trip_wear_engine.evaluate_trip_wear(
            dt=dt, 
            speed_mps=speeds, 
            ax_mps2=horiz_acc, 
            current_life_pct=current_life_pct
        )
        equiv_hours = float(wear_stats["equivalent_hours"])
        oil_wear_pct = float(wear_stats["oil_wear_percent"])
        idle_duration = float(wear_stats.get("idle_duration_sec", 0.0))
        idle_ratio = idle_duration / duration_sec if duration_sec > 0 else 0.0
    else:
        wear_stats = trip_wear_engine.compute_oil_wear(
            speeds_mps=speeds, 
            horizontal_acc=horiz_acc, 
            traffic_score=getattr(road_context, 'traffic_score', 1.0) if road_context else 1.0,
            dt=dt
        )
        equiv_hours = float(wear_stats.get("equivalent_engine_hours", wear_stats.get("equivalent_hours", 0.0)))
        oil_wear_pct = float(wear_stats.get("oil_wear_percent", 0.0))
        idle_ratio = float(wear_stats.get("idle_ratio", 0.0))

    # 2. Моделирование агрессивного двойника
    if hasattr(trip_twin_engine, "evaluate_financial_delta") and hasattr(trip_twin_engine, "simulate_aggressive_twin_kinematics"):
        twin_speed, twin_ax = trip_twin_engine.simulate_aggressive_twin_kinematics(speeds, dt=dt)
        twin_wear_stats = trip_wear_engine.evaluate_trip_wear(
            dt=dt, 
            speed_mps=twin_speed, 
            ax_mps2=twin_ax, 
            current_life_pct=current_life_pct
        ) if hasattr(trip_wear_engine, "evaluate_trip_wear") else wear_stats

        twin_equiv_hours = float(twin_wear_stats.get("equivalent_hours", equiv_hours))
        savings = trip_twin_engine.evaluate_financial_delta(
            distance_km=distance_km,
            user_equiv_hours=equiv_hours,
            user_ax=horiz_acc,
            twin_equiv_hours=twin_equiv_hours
        )
        fuel_saved_rub = float(savings.get("fuel_savings_rub", 0.0))
        oil_saved_rub = float(savings.get("oil_savings_rub", 0.0))
        total_savings_rub = float(savings.get("total_savings_rub", fuel_saved_rub + oil_saved_rub))
    else:
        savings = trip_twin_engine.simulate_twin_and_delta(
            speeds_mps=speeds,
            user_wear_percent=oil_wear_pct,
            dt=dt
        )
        fuel_saved_rub = float(savings.get("fuel_saved_rub", 0.0))
        oil_saved_rub = float(savings.get("oil_saved_rub", 0.0))
        total_savings_rub = float(savings.get("total_savings_rub", 0.0))

    user.total_savings_rub += total_savings_rub
    user.current_oil_wear_percent = min(100.0, user.current_oil_wear_percent + oil_wear_pct)

    actual_id = str(uuid.uuid4())

    new_trip = Trip(
        id=actual_id,
        user_id=payload.user_id,
        car_id=payload.car_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        equivalent_engine_hours=round(equiv_hours, 5),
        oil_wear_percent=round(oil_wear_pct, 5),
        idle_ratio=round(idle_ratio, 3),
        fuel_saved_rub=round(fuel_saved_rub, 2),
        oil_saved_rub=round(oil_saved_rub, 2),
        total_savings_rub=round(total_savings_rub, 2)
    )
    db.add(new_trip)
    await db.commit()

    return TripCalculationResult(
        session_id=actual_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        oil_wear_percent=round(oil_wear_pct, 5),
        cost_savings_rub=round(total_savings_rub, 2),
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
        ghost_twin_status="\u041b\u0438\u0445\u0430\u0447-\u043d\u043e\u0432\u0438\u0447\u043e\u043a (\u0421\u0440\u0435\u0434\u043d\u044f\u044f \u0441\u043b\u043e\u0436\u043d\u043e\u0441\u0442\u044c)",
        cpa_recommended=cpa_active,
        cpa_offer_text=cpa_text
    )


@app.get("/api/v1/users/{user_id}/trips", response_model=TripHistoryResponse)
async def get_user_trips(user_id: str, limit: int = 100, db: AsyncSession = Depends(get_db)):
    count_stmt = select(func.count(Trip.id)).where(Trip.user_id == user_id)
    total_count = (await db.execute(count_stmt)).scalar() or 0

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
            session_id=str(t.id),
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
        total_trips=total_count,
        trips=trip_items
    )


@app.get("/api/v1/users/{user_id}/analytics/period", response_model=PeriodAnalyticsResponse)
async def get_period_analytics(
    user_id: str,
    start_date: Optional[datetime] = Query(
        None, description="Начало периода (ISO 8601). По умолчанию: 30 дней назад"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Конец периода (ISO 8601). По умолчанию: текущий момент"
    ),
    db: AsyncSession = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    if end_date is None:
        end_date = now
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    if start_date > end_date:
        raise HTTPException(
            status_code=400, 
            detail="Параметр start_date не может быть позже end_date"
        )

    start_naive = start_date.astimezone(timezone.utc).replace(tzinfo=None) if start_date.tzinfo else start_date
    end_naive = end_date.astimezone(timezone.utc).replace(tzinfo=None) if end_date.tzinfo else end_date

    agg_stmt = select(
        func.count(Trip.id).label("total_trips"),
        func.coalesce(func.sum(Trip.distance_km), 0.0).label("total_dist"),
        func.coalesce(func.sum(Trip.duration_seconds), 0.0).label("total_seconds"),
        func.coalesce(func.sum(Trip.total_savings_rub), 0.0).label("total_savings"),
        func.coalesce(func.sum(Trip.oil_wear_percent), 0.0).label("total_oil_wear"),
        func.coalesce(func.avg(Trip.idle_ratio), 0.0).label("avg_idle")
    ).where(
        and_(
            Trip.user_id == user_id,
            Trip.created_at >= start_naive,
            Trip.created_at <= end_naive
        )
    )

    agg_res = await db.execute(agg_stmt)
    agg_row = agg_res.one()

    total_trips = int(agg_row.total_trips or 0)
    total_dist = float(agg_row.total_dist or 0.0)
    total_secs = float(agg_row.total_seconds or 0.0)
    total_savings = float(agg_row.total_savings or 0.0)
    total_wear = float(agg_row.total_oil_wear or 0.0)
    avg_idle = float(agg_row.avg_idle or 0.0)

    total_hours = total_secs / 3600.0
    avg_speed = (total_dist / total_hours) if total_hours > 0 else 0.0

    summary_metrics = TripsPeriodSummaryMetrics(
        total_trips=total_trips,
        total_distance_km=round(total_dist, 2),
        total_duration_hours=round(total_hours, 2),
        total_savings_rub=round(total_savings, 2),
        total_oil_wear_percent=round(total_wear, 4),
        avg_speed_kmh=round(avg_speed, 1),
        avg_idle_ratio=round(avg_idle, 3)
    )

    trips_stmt = (
        select(Trip)
        .where(
            and_(
                Trip.user_id == user_id,
                Trip.created_at >= start_naive,
                Trip.created_at <= end_naive
            )
        )
        .order_by(Trip.created_at.desc())
        .limit(200)
    )
    trips_res = await db.execute(trips_stmt)
    trips_rows = trips_res.scalars().all()

    trip_items = [
        TripPeriodItem(
            session_id=str(t.id),
            created_at=t.created_at,
            duration_seconds=round(float(t.duration_seconds or 0.0), 1),
            distance_km=round(float(t.distance_km or 0.0), 2),
            idle_ratio=round(float(t.idle_ratio or 0.0), 3),
            oil_wear_percent=round(float(t.oil_wear_percent or 0.0), 4),
            total_savings_rub=round(float(t.total_savings_rub or 0.0), 2)
        )
        for t in trips_rows
    ]

    return PeriodAnalyticsResponse(
        user_id=user_id,
        start_date=start_naive,
        end_date=end_naive,
        summary=summary_metrics,
        trips=trip_items
    )


@app.post("/api/v1/users/{user_id}/profile")
async def update_user_vehicle_profile(
    user_id: str,
    payload: UserProfileUpdatePayload,
    db: AsyncSession = Depends(get_db)
):
    user_stmt = select(User).where(User.id == user_id)
    res = await db.execute(user_stmt)
    user = res.scalar_one_or_none()

    if not user:
        user = User(
            id=user_id,
            total_savings_rub=0.0,
            current_oil_wear_percent=payload.current_oil_wear_percent
        )
        db.add(user)
    else:
        user.current_oil_wear_percent = payload.current_oil_wear_percent

    await db.commit()

    return {
        "status": "CONFIG_SAVED",
        "user_id": user_id,
        "car_id": payload.car_id,
        "current_oil_wear_percent": user.current_oil_wear_percent,
        "fuel_price_rub": payload.fuel_price_rub,
        "service_cost_rub": payload.service_cost_rub
    }


@app.post("/api/v1/analytics/verify-run", response_model=VerificationReport)
async def verify_experiment_run(payload: VerificationPayload):
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(status_code=422, detail="Недостаточный объем телеметрии.")

    raw_ax = np.array([p["ax"] for p in stream], dtype=np.float64)
    raw_ay = np.array([p["ay"] for p in stream], dtype=np.float64)
    raw_az = np.array([p["az"] for p in stream], dtype=np.float64)
    speeds = np.array([p["speed"] for p in stream], dtype=np.float64)

    filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
    horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

    profile = resolve_car_profile(payload.car_id)
    engine_inst = WearEngine(profile=profile)
    
    if hasattr(engine_inst, "evaluate_trip_wear"):
        wear_stats = engine_inst.evaluate_trip_wear(dt=0.02, speed_mps=speeds, ax_mps2=horiz_acc)
        autoxak_hours = float(wear_stats["equivalent_hours"])
    else:
        wear_stats = engine_inst.compute_oil_wear(speeds_mps=speeds, horizontal_acc=horiz_acc, dt=0.02)
        autoxak_hours = float(wear_stats.get("equivalent_engine_hours", wear_stats.get("equivalent_hours", 0.0)))

    obd = payload.obd_ground_truth
    report = benchmark_engine.evaluate_experiment(
        autoxak_engine_hours=autoxak_hours,
        obd_rpm=obd.get("engine_rpm", []),
        obd_load=obd.get("engine_load", []),
        obd_temp=obd.get("oil_temperature", []),
        user_savings=[6.67, 8.20, 5.40, 7.80, 9.10],
        dt=0.02
    )

    return report
