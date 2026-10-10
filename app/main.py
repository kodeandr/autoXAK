import os
import uuid
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager

import numpy as np
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Depends, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.core.logger import logger
from app.core.database import engine, Base, get_db
from app.models.telemetry import TripSessionPayload
from app.models.results import (
    TripCalculationResult, 
    TripHistoryResponse, 
    TripSummaryItem
)
from app.models.db_models import User, Trip, VehicleMake, VehicleModel, VehicleTrim, FuelRegionalPrice
from app.core.dsp.filters import SignalFilter
from app.services.wear_engine import WearEngine
from app.core.security import create_access_token, decode_access_token

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

security_scheme = HTTPBearer(auto_error=False)


# =====================================================================
# DTO Схемы (Pydantic v2)
# =====================================================================

class AuthHandshakePayload(BaseModel):
    device_id: str = Field(..., description="Уникальный отпечаток устройства / браузера")
    preferred_car_id: Optional[str] = Field(default="haval_jolion_15t_4wd")


class AuthResponse(BaseModel):
    token: str
    user_id: str
    car_id: str
    is_new_user: bool


class ExtendedTripCalculationResult(TripCalculationResult):
    fuel_saved_liters: float = Field(default=0.0, description="Сбереженное топливо, л")
    fuel_saved_rub: float = Field(default=0.0, description="Сбережения на топливе, ₽")
    oil_saved_rub: float = Field(default=0.0, description="Сбережения на масле, ₽")
    range_bonus_km: float = Field(default=0.0, description="Добавленный запас хода, км")
    autonomy_days_extended: float = Field(default=0.0, description="Продление дней до заправки")
    smooth_score: int = Field(default=100, description="Индекс плавности вождения 0..100")
    road_anomalies_count: int = Field(default=0, description="Пройденные лежачие полицейские и ямы")
    throttle_hunting_detected: bool = Field(default=False, description="Обнаружено рыскание газом")
    metrics: Optional[Dict[str, Any]] = Field(default=None, description="Полный словарь метрик для PWA")


class TripPeriodItem(BaseModel):
    session_id: str
    created_at: datetime
    duration_seconds: float
    distance_km: float
    idle_ratio: float
    oil_wear_percent: float
    total_savings_rub: float
    fuel_saved_liters: float = 0.0
    range_bonus_km: float = 0.0
    smooth_score: int = 100
    road_anomalies_count: int = 0


class TripsPeriodSummaryMetrics(BaseModel):
    total_trips: int = Field(..., description="Количество поездок за период")
    total_distance_km: float = Field(..., description="Суммарная дистанция, км")
    total_duration_hours: float = Field(..., description="Суммарное время за рулем, ч")
    total_savings_rub: float = Field(..., description="Совокупная экономия, ₽")
    total_oil_wear_percent: float = Field(..., description="Накопленный износ масла, %")
    avg_speed_kmh: float = Field(..., description="Средняя скорость движения, км/ч")
    avg_idle_ratio: float = Field(..., description="Средняя доля пробок / холостого хода")
    total_fuel_saved_liters: float = Field(default=0.0, description="Всего сбережено топлива, л")
    total_range_bonus_km: float = Field(default=0.0, description="Всего добавлено запаса хода, км")


class PeriodAnalyticsResponse(BaseModel):
    user_id: str
    start_date: datetime
    end_date: datetime
    summary: TripsPeriodSummaryMetrics
    trips: List[TripPeriodItem]


class DashboardResponse(BaseModel):
    user_id: str
    car_id: str = Field(default="haval_jolion_15t_4wd", description="Идентификатор автомобиля пользователя")
    fuel_price_rub: float = Field(default=62.00, description="Установленная цена топлива, ₽/л")
    service_cost_rub: float = Field(default=9500.0, description="Стоимость планового ТО, ₽")
    month_savings_rub: float = Field(..., description="Сэкономлено за месяц, ₽")
    oil_remaining_percent: float = Field(..., description="Остаточный ресурс масла, %")
    ghost_twin_status: str = Field(default="Динамичный темп", description="Статус модели сравнения")
    cpa_recommended: bool = Field(default=False, description="Флаг рекомендации замены масла")
    cpa_offer_text: Optional[str] = Field(default=None, description="Текст партнерского предложения")
    range_bank_km: float = Field(default=0.0, description="Накопленный банк автономности, км")
    total_fuel_saved_liters: float = Field(default=0.0, description="Всего сбережено топлива, л")
    autonomy_days_extended: float = Field(default=0.0, description="Продлено дней до АЗС")


class UserProfileUpdatePayload(BaseModel):
    user_id: str
    car_id: str = Field(default="haval_jolion_15t_4wd", description="Идентификатор ТС в реестре")
    current_oil_wear_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    fuel_price_rub: float = Field(default=62.00, gt=0.0)
    service_cost_rub: float = Field(default=9500.0, gt=0.0)


class VerificationPayload(BaseModel):
    session_id: str
    user_id: str
    car_id: str
    telemetry_stream: List[dict]
    obd_ground_truth: Dict[str, List[float]]


# =====================================================================
# Авторизация и контекст пользователя
# =====================================================================

async def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    if not auth or not auth.credentials:
        stmt = select(User).order_by(User.created_at.asc()).limit(1)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if user:
            return user
        raise HTTPException(status_code=401, detail="Требуется авторизация (Bearer token)")

    payload = decode_access_token(auth.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Недействительный или просроченный токен")

    user_id = payload["sub"]
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    return user


# =====================================================================
# Резолвер характеристик автомобилей
# =====================================================================

def get_default_profile() -> Any:
    if VEHICLE_REGISTRY:
        if "haval_jolion_15t_4wd" in VEHICLE_REGISTRY:
            return VEHICLE_REGISTRY["haval_jolion_15t_4wd"]
        if "haval_jolion_15t" in VEHICLE_REGISTRY:
            return VEHICLE_REGISTRY["haval_jolion_15t"]
        return next(iter(VEHICLE_REGISTRY.values()))

    class _FallbackOilProfile:
        nominal_service_hours: float = 250.0
        base_activation_energy_jmol: float = 75000.0
        aged_activation_energy_jmol: float = 45000.0
        zddp_activation_volume_m3: float = 1.2e-29
        zddp_activation_energy_jmol: float = 85000.0
        oil_grade: str = "5W-30"

    class _FallbackProfile:
        car_id: str = "haval_jolion_15t_4wd"
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


async def resolve_car_profile_async(car_id: Optional[str], db: AsyncSession) -> Any:
    """Извлекает физико-трибологический профиль ТС из базы данных PostgreSQL или реестра."""
    if not car_id:
        return get_default_profile()

    if VEHICLE_REGISTRY and car_id in VEHICLE_REGISTRY:
        return VEHICLE_REGISTRY[car_id]

    stmt = select(VehicleTrim).where(VehicleTrim.id == car_id)
    res = await db.execute(stmt)
    trim = res.scalar_one_or_none()

    if trim:
        class _DynamicOilProfile:
            nominal_service_hours: float = float(getattr(trim, "nominal_service_hours", 250.0) or 250.0)
            base_activation_energy_jmol: float = 75000.0
            aged_activation_energy_jmol: float = 45000.0
            zddp_activation_volume_m3: float = 1.2e-29
            zddp_activation_energy_jmol: float = 85000.0
            oil_grade: str = getattr(trim, "oil_grade", "5W-30") or "5W-30"

        class _DynamicProfile:
            car_id: str = trim.id
            brand: str = "Auto"
            model: str = trim.badge_name
            curb_weight_kg: float = float(trim.curb_weight_kg or 1500.0)
            rolling_resistance_coeff: float = float(trim.rolling_resistance_coeff or 0.012)
            drag_coefficient_area: float = float(trim.drag_coefficient_area or 0.76)
            drivetrain_efficiency: float = float(trim.drivetrain_efficiency or 0.90)
            base_city_fuel_rate_l100km: float = 8.5
            engine_displacement_l: float = float(trim.engine_displacement_l or 1.5)
            rated_power_kw: float = float(trim.rated_power_kw or 110.0)
            oil_capacity_l: float = float(trim.oil_capacity_l or 4.0)
            oil_profile: Any = _DynamicOilProfile()

        return _DynamicProfile()

    return get_default_profile()


# =====================================================================
# Инициализация приложения FastAPI
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="autoXAK Fuel Autonomy & Telematics Engine",
    description="Пайплайн Sensor Fusion, фильтрации неровностей, топливной автономии и трибологии ДВС",
    version="1.6.0",
    lifespan=lifespan
)

# CORS Middleware для удаленного тестирования коллегами
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтирование статических ресурсов
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


# =====================================================================
# Middleware сквозного логирования
# =====================================================================

@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    logger.info(f"[{request_id}] START {request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        logger.info(
            f"[{request_id}] FINISH {request.method} {request.url.path} "
            f"Status={response.status_code} ({process_time:.2f}ms)"
        )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        process_time = (time.time() - start_time) * 1000
        logger.error(
            f"[{request_id}] UNHANDLED ERROR in {request.method} {request.url.path} "
            f"({process_time:.2f}ms): {str(exc)}\n{traceback.format_exc()}"
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "ERROR",
                "request_id": request_id,
                "message": "Внутренняя ошибка сервера. Проверьте логи."
            }
        )


# =====================================================================
# Раздача веб-страниц
# =====================================================================

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


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root():
    return FileResponse(_resolve_static_file("index.html"))


@app.api_route("/history", methods=["GET", "HEAD"], include_in_schema=False)
async def history_page():
    return FileResponse(_resolve_static_file("history.html"))


@app.api_route("/setup", methods=["GET", "HEAD"], include_in_schema=False)
async def setup_page():
    return FileResponse(_resolve_static_file("setup.html"))


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "autoXAK Fuel Autonomy & Wear Engine",
        "version": "1.6.0"
    }


# =====================================================================
# Каталог ТС и региональное топливо
# =====================================================================

@app.get("/api/v1/vehicles/catalog")
async def get_vehicle_catalog(db: AsyncSession = Depends(get_db)):
    """Возвращает иерархический каталог автомобилей для страницы настроек."""
    stmt = (
        select(VehicleTrim, VehicleModel, VehicleMake)
        .join(VehicleModel, VehicleTrim.model_id == VehicleModel.id)
        .join(VehicleMake, VehicleModel.make_id == VehicleMake.id)
        .order_by(VehicleMake.name, VehicleModel.name)
    )
    res = await db.execute(stmt)
    rows = res.all()

    catalog = []
    for trim, model, make in rows:
        catalog.append({
            "id": trim.id,
            "make": make.name,
            "model": model.name,
            "trim": trim.badge_name,
            "full_name": f"{make.name} {model.name} {trim.badge_name}",
            "curb_weight_kg": trim.curb_weight_kg,
            "oil_capacity_l": trim.oil_capacity_l,
            "oil_grade": trim.oil_grade
        })

    if not catalog:
        catalog = [
            {"id": "haval_jolion_15t_4wd", "make": "Haval", "model": "Jolion", "trim": "1.5T 4WD", "full_name": "Haval Jolion 1.5T • 4WD", "curb_weight_kg": 1505, "oil_capacity_l": 3.8, "oil_grade": "0W-20"},
            {"id": "skoda_octavia_14tsi", "make": "Skoda", "model": "Octavia", "trim": "1.4 TSI", "full_name": "Skoda Octavia 1.4 TSI", "curb_weight_kg": 1285, "oil_capacity_l": 4.0, "oil_grade": "0W-30"},
            {"id": "geely_coolray_15t", "make": "Geely", "model": "Coolray", "trim": "1.5T DCT", "full_name": "Geely Coolray 1.5T", "curb_weight_kg": 1340, "oil_capacity_l": 4.0, "oil_grade": "0W-20"},
            {"id": "audi_q3_20tfsi_180_culb", "make": "Audi", "model": "Q3", "trim": "2.0 TFSI quattro", "full_name": "Audi Q3 2.0 TFSI quattro", "curb_weight_kg": 1590, "oil_capacity_l": 5.7, "oil_grade": "0W-30"}
        ]

    return {"catalog": catalog, "total": len(catalog)}


@app.get("/api/v1/fuel/current-price")
async def get_current_fuel_price(
    region_code: str = "77", 
    fuel_type: str = "ai95", 
    db: AsyncSession = Depends(get_db)
):
    """Возвращает стоимость топлива из базы данных или стандартный fallback."""
    stmt = select(FuelRegionalPrice).where(FuelRegionalPrice.region_code == region_code)
    res = await db.execute(stmt)
    item = res.scalars().first()

    if item:
        price_map = {
            "ai92": item.price_ai92,
            "ai95": item.price_ai95,
            "ai100": item.price_ai100,
            "dt": item.price_dt
        }
        return {
            "price_rub": float(price_map.get(fuel_type.lower(), item.price_ai95)),
            "region_name": item.region_name,
            "brand": item.brand_display_name
        }
    return {"price_rub": 62.00, "region_name": "Стандартный регион", "brand": "АЗС"}


# =====================================================================
# Расчетный эндпоинт сессии телеметрии
# =====================================================================

@app.post("/api/v1/telemetry/session", response_model=ExtendedTripCalculationResult)
async def process_telemetry_session(
    payload: TripSessionPayload, 
    db: AsyncSession = Depends(get_db)
):
    stream = payload.telemetry_stream or []
    points_count = len(stream)
    session_ref = getattr(payload, "session_id", "new_session")
    
    logger.info(f"Получена сессия {session_ref} (user={payload.user_id}, точек={points_count})")

    if points_count < 50:
        logger.warning(f"Отказ сессии {session_ref}: недостаточно точек ({points_count} < 50)")
        raise HTTPException(
            status_code=422, 
            detail="Недостаточно точек телеметрии для валидации (минимум 1 секунда / 50 точек)."
        )

    try:
        raw_ax = np.array([p.ax for p in stream], dtype=np.float64)
        raw_ay = np.array([p.ay for p in stream], dtype=np.float64)
        raw_az = np.array([p.az for p in stream], dtype=np.float64)
        speeds = np.array([p.speed for p in stream], dtype=np.float64)

        max_g = float(np.max(np.abs(raw_ax) + np.abs(raw_ay) + np.abs(raw_az)))
        if max_g > 40.0:
            logger.warning(f"АНОМАЛИЯ IMU в {session_ref}: всплеск перегрузки {max_g:.2f} м/с²")

        coords = [{"lat": p.lat, "lon": p.lon} for p in stream if p.lat and p.lon]
        road_context = await gis_service.get_route_context(coords)

        filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
        horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

        dt = 0.02
        duration_sec = points_count * dt
        distance_meters = np.sum(speeds * dt)
        distance_km = float(distance_meters / 1000.0)

        # Изоляция неровностей дороги (Z-Shock Veto)
        if hasattr(TwinEngine, "filter_road_anomalies"):
            clean_ax, detected_bumps = TwinEngine.filter_road_anomalies(horiz_acc, raw_az=raw_az, dt=dt)
        else:
            clean_ax, detected_bumps = horiz_acc, 0

        # Поиск или создание пользователя
        user_stmt = select(User).where(User.id == payload.user_id)
        result = await db.execute(user_stmt)
        user = result.scalar_one_or_none()

        if not user:
            logger.info(f"Создание профиля пользователя: {payload.user_id}")
            user = User(
                id=payload.user_id,
                car_id=payload.car_id or "haval_jolion_15t_4wd",
                fuel_price_rub=62.00,
                service_cost_rub=9500.0,
                total_savings_rub=0.0,
                total_fuel_saved_liters=0.0,
                range_bank_km=0.0,
                current_oil_wear_percent=0.0
            )
            db.add(user)
            await db.flush()

        # Нормализация полей от None
        user.total_savings_rub = float(getattr(user, "total_savings_rub", 0.0) or 0.0)
        user.total_fuel_saved_liters = float(getattr(user, "total_fuel_saved_liters", 0.0) or 0.0)
        user.range_bank_km = float(getattr(user, "range_bank_km", 0.0) or 0.0)
        user.current_oil_wear_percent = float(getattr(user, "current_oil_wear_percent", 0.0) or 0.0)

        resolved_car_id = payload.car_id or getattr(user, "car_id", "haval_jolion_15t_4wd")
        user_fuel_price = float(getattr(user, "fuel_price_rub", 62.00) or 62.00)
        user_service_cost = float(getattr(user, "service_cost_rub", 9500.0) or 9500.0)

        # Динамический профиль ТС из базы данных
        profile = await resolve_car_profile_async(resolved_car_id, db)
        try:
            trip_wear_engine = WearEngine(profile=profile)
        except TypeError:
            trip_wear_engine = WearEngine(profile)

        try:
            trip_twin_engine = TwinEngine(profile=profile)
        except TypeError:
            trip_twin_engine = TwinEngine(profile)

        current_life_pct = max(0.0, 100.0 - user.current_oil_wear_percent)

        # Моделирование износа масла
        if hasattr(trip_wear_engine, "evaluate_trip_wear"):
            wear_stats = trip_wear_engine.evaluate_trip_wear(
                dt=dt, 
                speed_mps=speeds, 
                ax_mps2=clean_ax, 
                current_life_pct=current_life_pct
            )
            equiv_hours = float(wear_stats["equivalent_hours"])
            oil_wear_pct = float(wear_stats["oil_wear_percent"])
            idle_duration = float(wear_stats.get("idle_duration_sec", 0.0))
            idle_ratio = idle_duration / duration_sec if duration_sec > 0 else 0.0
        else:
            wear_stats = trip_wear_engine.compute_oil_wear(
                speeds_mps=speeds, 
                horizontal_acc=clean_ax, 
                traffic_score=getattr(road_context, 'traffic_score', 1.0) if road_context else 1.0,
                dt=dt
            )
            equiv_hours = float(wear_stats.get("equivalent_engine_hours", wear_stats.get("equivalent_hours", 0.0)))
            oil_wear_pct = float(wear_stats.get("oil_wear_percent", 0.0))
            idle_ratio = float(wear_stats.get("idle_ratio", 0.0))

        # Моделирование двойника с индивидуальными тарифами
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
                user_ax=clean_ax,
                raw_az=raw_az,
                speeds_mps=speeds,
                twin_equiv_hours=twin_equiv_hours,
                fuel_price_rub=user_fuel_price,
                service_cost_rub=user_service_cost,
                dt=dt
            )
        else:
            savings = trip_twin_engine.simulate_twin_and_delta(
                speeds_mps=speeds,
                user_wear_percent=oil_wear_pct,
                dt=dt,
                fuel_price_rub=user_fuel_price,
                service_cost_rub=user_service_cost,
                distance_km=distance_km
            )

        fuel_saved_rub = float(savings.get("fuel_savings_rub", savings.get("fuel_saved_rub", 0.0)))
        oil_saved_rub = float(savings.get("oil_savings_rub", savings.get("oil_saved_rub", 0.0)))
        total_savings_rub = float(savings.get("total_savings_rub", fuel_saved_rub + oil_saved_rub))

        fuel_saved_liters = float(savings.get("fuel_saved_liters", savings.get("saved_fuel_liters", 0.0)))
        if fuel_saved_liters == 0.0 and fuel_saved_rub > 0.0 and user_fuel_price > 0.0:
            fuel_saved_liters = round(fuel_saved_rub / user_fuel_price, 3)

        range_bonus_km = float(savings.get("range_bonus_km", 0.0))
        if range_bonus_km == 0.0 and fuel_saved_liters > 0.0:
            range_bonus_km = round((fuel_saved_liters * 100.0) / 8.5, 2)

        autonomy_days = float(savings.get("autonomy_days_extended", 0.0))
        smooth_score = int(savings.get("smooth_score", 100))
        road_anomalies = int(savings.get("road_anomalies_count", detected_bumps))
        hunting_detected = bool(savings.get("throttle_hunting_detected", False))

        # Накопление баланса в БД
        user.total_savings_rub += total_savings_rub
        user.total_fuel_saved_liters += fuel_saved_liters
        user.range_bank_km += range_bonus_km
        user.current_oil_wear_percent = min(100.0, user.current_oil_wear_percent + oil_wear_pct)

        actual_id = str(uuid.uuid4())
        trip_kwargs = {
            "id": actual_id,
            "user_id": payload.user_id,
            "car_id": resolved_car_id,
            "duration_seconds": round(duration_sec, 2),
            "distance_km": round(distance_km, 2),
            "equivalent_engine_hours": round(equiv_hours, 5),
            "oil_wear_percent": round(oil_wear_pct, 5),
            "idle_ratio": round(idle_ratio, 3),
            "fuel_saved_rub": round(fuel_saved_rub, 2),
            "oil_saved_rub": round(oil_saved_rub, 2),
            "total_savings_rub": round(total_savings_rub, 2)
        }

        for extra_col, extra_val in [
            ("fuel_saved_liters", round(fuel_saved_liters, 3)),
            ("range_bonus_km", round(range_bonus_km, 2)),
            ("smooth_score", smooth_score),
            ("road_anomalies_count", road_anomalies)
        ]:
            if hasattr(Trip, extra_col):
                trip_kwargs[extra_col] = extra_val

        new_trip = Trip(**trip_kwargs)
        db.add(new_trip)
        await db.commit()

        logger.info(
            f"Сессия сохранена ({actual_id}): {distance_km:.2f} км, "
            f"+{range_bonus_km:.1f} км запаса хода, +{fuel_saved_liters:.2f} л топлива"
        )

        metrics_payload = {
            "fuel_saved_liters": fuel_saved_liters,
            "fuel_saved_rub": fuel_saved_rub,
            "oil_saved_rub": oil_saved_rub,
            "range_bonus_km": range_bonus_km,
            "autonomy_days_extended": autonomy_days,
            "smooth_score": smooth_score,
            "road_anomalies_count": road_anomalies,
            "throttle_hunting_detected": hunting_detected,
            "cost_savings_rub": round(total_savings_rub, 2)
        }

        return ExtendedTripCalculationResult(
            session_id=actual_id,
            duration_seconds=round(duration_sec, 2),
            distance_km=round(distance_km, 2),
            oil_wear_percent=round(oil_wear_pct, 5),
            cost_savings_rub=round(total_savings_rub, 2),
            status="PROCESSED",
            fuel_saved_liters=fuel_saved_liters,
            fuel_saved_rub=fuel_saved_rub,
            oil_saved_rub=oil_saved_rub,
            range_bonus_km=range_bonus_km,
            autonomy_days_extended=autonomy_days,
            smooth_score=smooth_score,
            road_anomalies_count=road_anomalies,
            throttle_hunting_detected=hunting_detected,
            metrics=metrics_payload
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Критический сбой сессии {payload.user_id}: {exc}\n{traceback.format_exc()}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка конвейера обработки телеметрии.")


# =====================================================================
# Дашборд, История и Аналитика
# =====================================================================

@app.get("/api/v1/users/{user_id}/dashboard", response_model=DashboardResponse)
async def get_user_dashboard(user_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    oil_wear = float(getattr(user, "current_oil_wear_percent", 0.0) or 0.0)
    remaining_oil = max(0.0, round(100.0 - oil_wear, 1))
    cpa_active = remaining_oil <= 10.0
    cpa_text = "Пора менять масло. Скидка 15% на рекомендованное масло" if cpa_active else None

    # Прямое извлечение тарифов пользователя из настроек
    fuel_price = float(getattr(user, "fuel_price_rub", 62.00) or 62.00)
    service_cost = float(getattr(user, "service_cost_rub", 9500.0) or 9500.0)
    total_savings = float(getattr(user, "total_savings_rub", 0.0) or 0.0)

    # Реальные накопленные метрики банка автономии
    saved_liters = float(getattr(user, "total_fuel_saved_liters", 0.0) or 0.0)
    saved_range_km = float(getattr(user, "range_bank_km", 0.0) or 0.0)

    # Fallback-аппроксимация для старых записей
    if saved_liters == 0.0 and total_savings > 0:
        saved_liters = round((total_savings * 0.72) / fuel_price, 2)
        saved_range_km = round((saved_liters * 100.0) / 8.5, 1)

    est_autonomy_days = round(saved_liters / 4.2, 1) if saved_liters > 0 else 0.0

    return DashboardResponse(
        user_id=user.id,
        car_id=getattr(user, "car_id", "haval_jolion_15t_4wd") or "haval_jolion_15t_4wd",
        fuel_price_rub=fuel_price,
        service_cost_rub=service_cost,
        month_savings_rub=round(total_savings, 2),
        oil_remaining_percent=remaining_oil,
        ghost_twin_status="Динамичный темп",
        cpa_recommended=cpa_active,
        cpa_offer_text=cpa_text,
        range_bank_km=round(saved_range_km, 1),
        total_fuel_saved_liters=round(saved_liters, 2),
        autonomy_days_extended=est_autonomy_days
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
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    if end_date is None:
        end_date = now
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date не может быть позже end_date")

    start_naive = start_date.astimezone(timezone.utc).replace(tzinfo=None) if start_date.tzinfo else start_date
    end_naive = end_date.astimezone(timezone.utc).replace(tzinfo=None) if end_date.tzinfo else end_date

    # Загружаем персональный тариф топлива пользователя
    user_stmt = select(User).where(User.id == user_id)
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    user_fuel_price = float(getattr(user, "fuel_price_rub", 62.00) or 62.00)

    agg_stmt = select(
        func.count(Trip.id).label("total_trips"),
        func.coalesce(func.sum(Trip.distance_km), 0.0).label("total_dist"),
        func.coalesce(func.sum(Trip.duration_seconds), 0.0).label("total_seconds"),
        func.coalesce(func.sum(Trip.total_savings_rub), 0.0).label("total_savings"),
        func.coalesce(func.sum(Trip.oil_wear_percent), 0.0).label("total_oil_wear"),
        func.coalesce(func.avg(Trip.idle_ratio), 0.0).label("avg_idle")
    ).where(
        and_(Trip.user_id == user_id, Trip.created_at >= start_naive, Trip.created_at <= end_naive)
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

    # Расчет по персональному тарифу пользователя
    period_fuel_liters = round((total_savings * 0.72) / user_fuel_price, 2) if user_fuel_price > 0 else 0.0
    period_range_km = round((period_fuel_liters * 100.0) / 8.5, 1)

    summary_metrics = TripsPeriodSummaryMetrics(
        total_trips=total_trips,
        total_distance_km=round(total_dist, 2),
        total_duration_hours=round(total_hours, 2),
        total_savings_rub=round(total_savings, 2),
        total_oil_wear_percent=round(total_wear, 4),
        avg_speed_kmh=round(avg_speed, 1),
        avg_idle_ratio=round(avg_idle, 3),
        total_fuel_saved_liters=period_fuel_liters,
        total_range_bonus_km=period_range_km
    )

    trips_stmt = (
        select(Trip)
        .where(and_(Trip.user_id == user_id, Trip.created_at >= start_naive, Trip.created_at <= end_naive))
        .order_by(Trip.created_at.desc())
        .limit(200)
    )
    trips_rows = (await db.execute(trips_stmt)).scalars().all()

    trip_items = [
        TripPeriodItem(
            session_id=str(t.id),
            created_at=t.created_at,
            duration_seconds=round(float(t.duration_seconds or 0.0), 1),
            distance_km=round(float(t.distance_km or 0.0), 2),
            idle_ratio=round(float(t.idle_ratio or 0.0), 3),
            oil_wear_percent=round(float(t.oil_wear_percent or 0.0), 4),
            total_savings_rub=round(float(t.total_savings_rub or 0.0), 2),
            fuel_saved_liters=getattr(t, "fuel_saved_liters", 0.0) or round((float(t.total_savings_rub or 0.0) * 0.72) / user_fuel_price, 3),
            range_bonus_km=getattr(t, "range_bonus_km", 0.0) or round(((float(t.total_savings_rub or 0.0) * 0.72) / user_fuel_price) * 100.0 / 8.5, 1),
            smooth_score=getattr(t, "smooth_score", 100) or 100,
            road_anomalies_count=getattr(t, "road_anomalies_count", 0) or 0
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


# =====================================================================
# Регистрация устройства и профиль
# =====================================================================

@app.post("/api/v1/auth/handshake", response_model=AuthResponse)
async def auth_handshake(payload: AuthHandshakePayload, db: AsyncSession = Depends(get_db)):
    """Device-First регистрация: изолирует профиль каждого коллеги по fingerprint устройства."""
    user_id = f"dev_{payload.device_id[:16]}"
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    is_new = False

    if not user:
        is_new = True
        user = User(
            id=user_id,
            car_id=payload.preferred_car_id or "haval_jolion_15t_4wd",
            fuel_price_rub=62.00,
            service_cost_rub=9500.0,
            total_savings_rub=0.0,
            total_fuel_saved_liters=0.0,
            range_bank_km=0.0,
            current_oil_wear_percent=0.0
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(user_id=user.id)
    return AuthResponse(token=token, user_id=user.id, car_id=user.car_id, is_new_user=is_new)


@app.get("/api/v1/users/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Возвращает актуальные настройки текущего авторизованного пользователя."""
    return {
        "user_id": current_user.id,
        "car_id": getattr(current_user, "car_id", "haval_jolion_15t_4wd"),
        "fuel_price_rub": float(getattr(current_user, "fuel_price_rub", 62.00) or 62.00),
        "service_cost_rub": float(getattr(current_user, "service_cost_rub", 9500.0) or 9500.0),
        "range_bank_km": float(getattr(current_user, "range_bank_km", 0.0) or 0.0),
        "total_savings_rub": float(getattr(current_user, "total_savings_rub", 0.0) or 0.0)
    }


@app.post("/api/v1/users/{user_id}/profile")
async def update_user_vehicle_profile(
    user_id: str,
    payload: UserProfileUpdatePayload,
    db: AsyncSession = Depends(get_db)
):
    """Сохраняет выбранную модификацию автомобиля и персональные тарифы водителя."""
    user_stmt = select(User).where(User.id == user_id)
    res = await db.execute(user_stmt)
    user = res.scalar_one_or_none()

    if not user:
        user = User(
            id=user_id,
            car_id=payload.car_id,
            fuel_price_rub=payload.fuel_price_rub,
            service_cost_rub=payload.service_cost_rub,
            total_savings_rub=0.0,
            total_fuel_saved_liters=0.0,
            range_bank_km=0.0,
            current_oil_wear_percent=payload.current_oil_wear_percent
        )
        db.add(user)
    else:
        user.car_id = payload.car_id
        user.fuel_price_rub = payload.fuel_price_rub
        user.service_cost_rub = payload.service_cost_rub
        user.current_oil_wear_percent = payload.current_oil_wear_percent

    await db.commit()
    await db.refresh(user)

    return {
        "status": "CONFIG_SAVED",
        "user_id": user.id,
        "car_id": user.car_id,
        "fuel_price_rub": user.fuel_price_rub,
        "service_cost_rub": user.service_cost_rub
    }


@app.post("/api/v1/analytics/verify-run", response_model=VerificationReport)
async def verify_experiment_run(payload: VerificationPayload):
    """Верификация конвейера против OBD-II."""
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(status_code=422, detail="Недостаточный объем телеметрии.")

    raw_ax = np.array([p["ax"] for p in stream], dtype=np.float64)
    raw_ay = np.array([p["ay"] for p in stream], dtype=np.float64)
    raw_az = np.array([p["az"] for p in stream], dtype=np.float64)
    speeds = np.array([p["speed"] for p in stream], dtype=np.float64)

    filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
    horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

    clean_horiz_acc = horiz_acc
    if hasattr(TwinEngine, "filter_road_anomalies"):
        clean_horiz_acc, _ = TwinEngine.filter_road_anomalies(horiz_acc, raw_az=raw_az, dt=0.02)

    profile = get_default_profile()
    engine_inst = WearEngine(profile=profile)
    
    if hasattr(engine_inst, "evaluate_trip_wear"):
        wear_stats = engine_inst.evaluate_trip_wear(dt=0.02, speed_mps=speeds, ax_mps2=clean_horiz_acc)
        autoxak_hours = float(wear_stats["equivalent_hours"])
    else:
        wear_stats = engine_inst.compute_oil_wear(speeds_mps=speeds, horizontal_acc=clean_horiz_acc, dt=0.02)
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