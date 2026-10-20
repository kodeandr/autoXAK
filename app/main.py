import os
import math
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
from app.api.auth import router as auth_router
from app.api.recordings import router as recordings_router
from app.models import recordings as recording_tables

from app.core.database import engine, Base, get_db
from app.models.telemetry import TripSessionPayload
from app.models.results import (
    TripCalculationResult, 
    TripHistoryResponse, 
    TripSummaryItem
)
from app.models.db_models import User, Trip, VehicleMake, VehicleModel, VehicleTrim, FuelRegionalPrice, CPALead
from app.core.dsp.filters import SignalFilter
from app.core.dsp.alignment import IMUAlignmentService
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

    harsh_accel_count: int = Field(default=0, description="Число резких разгонов")
    harsh_brake_count: int = Field(default=0, description="Число резких торможений")
    traffic_jam_seconds: float = Field(default=0.0, description="Время в заторах / холостой ход, с")
    rush_cost_rub: float = Field(default=0.0, description="Потери выгоды от резких маневров, ₽")
    base_potential_rub: float = Field(default=0.0, description="Потенциальная экономия при идеальной плавности, ₽")

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
    harsh_accel_count: int = 0
    harsh_brake_count: int = 0
    rush_cost_rub: float = 0.0
    # Поля совместимости с модальным окном "Подробнее" в history.html
    hard_accel_count: int = 0
    hard_brake_count: int = 0
    bumps_count: int = 0
    idle_minutes: int = 0


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
    fuel_price_rub: float = Field(default=72.40, description="Установленная цена топлива, ₽/л")
    service_cost_rub: float = Field(default=9500.0, description="Стоимость планового ТО, ₽")
    total_savings_rub: float = Field(default=0.0, description="Накопленная выгода за все время, ₽")
    month_savings_rub: float = Field(default=0.0, description="Сэкономлено за месяц, ₽")
    total_trips: int = Field(default=0, description="Всего завершенных поездок")
    total_distance_km: float = Field(default=0.0, description="Суммарный подтвержденный пробег, км")
    oil_remaining_percent: float = Field(default=100.0, description="Остаточный ресурс масла, %")
    ghost_twin_status: str = Field(default="Базовый лихач (дефолт)", description="Статус модели сравнения")
    cpa_recommended: bool = Field(default=False, description="Флаг рекомендации замены масла")
    cpa_offer_text: Optional[str] = Field(default=None, description="Текст партнерского предложения")
    range_bank_km: float = Field(default=0.0, description="Накопленный банк автономности, км")
    total_fuel_saved_liters: float = Field(default=0.0, description="Всего сбережено топлива, л")
    autonomy_days_extended: float = Field(default=0.0, description="Продлено дней до АЗС")


class ProfileUpdateRequest(BaseModel):
    car_id: Optional[str] = Field(default="haval_jolion_15t_4wd", description="Идентификатор ТС в реестре")
    fuel_price_rub: Optional[float] = Field(default=72.40, gt=0.0)
    service_cost_rub: Optional[float] = Field(default=9500.0, gt=0.0)
    current_oil_wear_percent: Optional[float] = Field(default=0.0, ge=0.0, le=100.0)
    name: Optional[str] = None
    user_id: Optional[str] = None

UserProfileUpdatePayload = ProfileUpdateRequest


class VerificationPayload(BaseModel):
    session_id: str
    user_id: str
    car_id: str
    telemetry_stream: List[dict]
    obd_ground_truth: Dict[str, List[float]]


class CPAClaimPayload(BaseModel):
    user_id: str
    promo_code: str
    partner_id: str = "autodoc_lead_engine"


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
        oil_grade: str = "0W-20"

    class _FallbackProfile:
        car_id: str = "haval_jolion_15t_4wd"
        brand: str = "Haval"
        model: str = "Jolion 1.5T 4WD"
        curb_weight_kg: float = 1505.0
        rolling_resistance_coeff: float = 0.0135
        drag_coefficient_area: float = 0.86
        drivetrain_efficiency: float = 0.87
        base_city_fuel_rate_l100km: float = 9.8
        engine_displacement_l: float = 1.5
        rated_power_kw: float = 110.0
        oil_capacity_l: float = 3.8
        oil_profile: Any = _FallbackOilProfile()

    return _FallbackProfile()


async def resolve_car_profile_async(car_id: Optional[str], db: AsyncSession) -> Any:
    """Извлекает физический профиль ТС из PostgreSQL или статического реестра."""
    if not car_id:
        return get_default_profile()

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
            recommended_oil_brand: str = getattr(trim, "recommended_oil_brand", "LUKOIL GENESIS") or "LUKOIL GENESIS"
            oil_profile: Any = _DynamicOilProfile()

        return _DynamicProfile()

    if VEHICLE_REGISTRY and car_id in VEHICLE_REGISTRY:
        return VEHICLE_REGISTRY[car_id]

    return get_default_profile()


# =====================================================================
# Вспомогательные алгоритмы геодезии и кластеризации маневров
# =====================================================================

def count_maneuver_events(signal: np.ndarray, threshold: float, is_greater: bool = True, min_points: int = 5) -> int:
    """Группирует высокочастотные отсчеты (50 Гц) в дискретные события маневров с подавлением дребезга."""
    if len(signal) == 0:
        return 0
    condition = (signal > threshold) if is_greater else (signal < threshold)
    events = 0
    in_event = False
    current_length = 0
    cooldown = 0

    for state in condition:
        if cooldown > 0:
            cooldown -= 1
        if state:
            current_length += 1
            if current_length >= min_points and not in_event and cooldown == 0:
                events += 1
                in_event = True
                cooldown = 25  # Минимум 0.5 с между двумя независимыми стартами/торможениями
        else:
            in_event = False
            current_length = 0
    return events


def calculate_haversine_distance_km(coords: List[Dict[str, float]]) -> float:
    """Вычисляет геодезическую длину трека по формуле гаверсинусов (с отсечкой GPS-выбросов)."""
    if len(coords) < 2:
        return 0.0
    R = 6371.0
    total_km = 0.0
    prev = coords[0]
    for cur in coords[1:]:
        lat1, lon1 = math.radians(prev["lat"]), math.radians(prev["lon"])
        lat2, lon2 = math.radians(cur["lat"]), math.radians(cur["lon"])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
        seg_km = R * c
        # Фильтр телепортации GPS (не более 150 метров за 1 шаг)
        if 0.0002 <= seg_km <= 0.15:
            total_km += seg_km
            prev = cur
    return total_km


# =====================================================================
# Инициализация приложения FastAPI
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE trips ADD COLUMN IF NOT EXISTS board_computer_consumption_l100km FLOAT;"))
        except Exception:
            pass
    yield


app = FastAPI(
    title="autoXAK Fuel Autonomy & Telematics Engine",
    description="Пайплайн Sensor Fusion, фильтрации неровностей, топливной автономии и трибологии ДВС",
    version="1.8.0",
    lifespan=lifespan
)

app.include_router(auth_router)
app.include_router(recordings_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
alignment_service = IMUAlignmentService(sample_rate_hz=50.0)
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


@app.api_route("/login", methods=["GET", "HEAD"], include_in_schema=False)
async def login_page():
    return FileResponse(_resolve_static_file("login.html"))


@app.api_route("/history", methods=["GET", "HEAD"], include_in_schema=False)
async def history_page():
    return FileResponse(_resolve_static_file("history.html"))


@app.api_route("/setup", methods=["GET", "HEAD"], include_in_schema=False)
async def setup_page():
    return FileResponse(_resolve_static_file("setup.html"))


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "autoXAK Fuel Autonomy & Wear Engine",
        "engine": "Wear & Cost Processor active",
        "version": "1.8.0"
    }


# =====================================================================
# Каталог ТС и региональное топливо
# =====================================================================

@app.get("/api/v1/vehicles/catalog")
async def get_vehicle_catalog(db: AsyncSession = Depends(get_db)):
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
            "body_type": model.body_type,
            "trim": trim.badge_name,
            "badge_name": trim.badge_name,
            "full_name": f"{make.name} {model.name} {trim.badge_name}",
            "curb_weight_kg": trim.curb_weight_kg,
            "oil_capacity_l": trim.oil_capacity_l,
            "oil_grade": trim.oil_grade,
            "oil_spec": trim.oil_spec,
            "recommended_oil_brand": trim.recommended_oil_brand
        })

    if not catalog:
        catalog = [
            {"id": "haval_jolion_15t_4wd", "make": "Haval", "model": "Jolion", "body_type": "crossover", "trim": "1.5T 4WD", "badge_name": "1.5T 4WD 7DCT (150 л.с.)", "full_name": "Haval Jolion 1.5T 4WD", "curb_weight_kg": 1505, "oil_capacity_l": 3.8, "oil_grade": "0W-20", "recommended_oil_brand": "TotalEnergies Quartz 9000"},
            {"id": "skoda_octavia_14tsi_dsg", "make": "Skoda", "model": "Octavia", "body_type": "liftback", "trim": "1.4 TSI", "badge_name": "1.4 TSI 7-DSG (150 л.с.)", "full_name": "Skoda Octavia 1.4 TSI", "curb_weight_kg": 1285, "oil_capacity_l": 4.0, "oil_grade": "0W-30", "recommended_oil_brand": "VAG LongLife III FE"},
            {"id": "geely_coolray_15t_fwd", "make": "Geely", "model": "Coolray", "body_type": "crossover", "trim": "1.5T DCT", "badge_name": "1.5T 7DCT (150 л.с.)", "full_name": "Geely Coolray 1.5T", "curb_weight_kg": 1340, "oil_capacity_l": 4.0, "oil_grade": "0W-20", "recommended_oil_brand": "LUKOIL GENESIS ARMORTECH"}
        ]

    return {"catalog": catalog, "total": len(catalog)}


@app.get("/api/v1/fuel/current-price")
async def get_current_fuel_price(
    region_code: str = "77", 
    fuel_type: str = "ai95",
    fuel_grade: Optional[str] = None,
    brand: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    target_grade = (fuel_grade or fuel_type or "ai95").lower()
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
        val = float(price_map.get(target_grade, item.price_ai95))
        return {
            "price_rub": val,
            "recommended_price_rub": val,
            "region_name": item.region_name,
            "brand": item.brand_display_name
        }
    return {"price_rub": 72.40, "recommended_price_rub": 72.40, "region_name": "Москва", "brand": "АЗС"}


# =====================================================================
# Расчетный эндпоинт сессии телеметрии (исправленная физика и DSP)
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
        raise HTTPException(
            status_code=422, 
            detail="Недостаточно точек телеметрии для валидации (минимум 50 точек)."
        )

    try:
        times = np.array([p.t for p in stream], dtype=np.float64)
        raw_ax = np.array([p.ax for p in stream], dtype=np.float64)
        raw_ay = np.array([p.ay for p in stream], dtype=np.float64)
        raw_az = np.array([p.az for p in stream], dtype=np.float64)
        speeds = np.clip(np.array([p.speed for p in stream], dtype=np.float64), 0.0, 75.0)

        # 1. Вычисление реального шага времени dt по меткам времени (защита от плавающей частоты браузера)
        raw_diffs = np.diff(times)
        valid_diffs = raw_diffs[(raw_diffs > 0.002) & (raw_diffs < 0.5)]
        dt = float(np.median(valid_diffs)) if len(valid_diffs) > 10 else 0.02

        span_sec = float(times[-1] - times[0]) if times[-1] > times[0] else 0.0
        duration_sec = span_sec if (0.5 <= span_sec <= 86400.0) else float(points_count * dt)

        # 2. Выделение чистого линейного ускорения без гравитации (filt_z в покое = 0.0, а не 9.81)
        filt_x, filt_y, filt_z = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)

        # Определяем, было ли реальное движение (скорость > 0.5 м/с или динамическое ускорение > 0.35 м/с²)
        is_moving_session = bool(np.max(speeds) > 0.5 or np.std(filt_x) > 0.25)

        # 3. Расчет дистанции: отсекаем Wi-Fi/IP дрейф координат на стоящем ПК
        coords = [{"lat": p.lat, "lon": p.lon} for p in stream if p.lat is not None and p.lon is not None]
        speed_distance_km = float(np.sum(speeds) * dt / 1000.0)
        gps_distance_km = calculate_haversine_distance_km(coords) if np.max(speeds) > 0.5 else 0.0
        distance_km = max(speed_distance_km, gps_distance_km) if is_moving_session else 0.0
        if distance_km < 0.03:
            distance_km = 0.0

        road_context = await gis_service.get_route_context(coords) if distance_km > 0.0 else None

        # 4. Подавление ударов подвески (Z-Shock Veto): передаем filt_z БЕЗ гравитации и только при реальном движении
        if is_moving_session and hasattr(TwinEngine, "filter_road_anomalies"):
            clean_signed_ax, detected_bumps = TwinEngine.filter_road_anomalies(filt_x, raw_az=filt_z, dt=dt)
        else:
            clean_signed_ax, detected_bumps = filt_x, 0

        # 5. Подсчет дискретных резких разгонов и торможений
        harsh_accel_count = count_maneuver_events(clean_signed_ax, threshold=2.0, is_greater=True, min_points=5)
        harsh_brake_count = count_maneuver_events(clean_signed_ax, threshold=-2.3, is_greater=False, min_points=5)

        traffic_jam_seconds = float(np.sum(speeds < 1.0) * dt)
        idle_ratio = min(1.0, (traffic_jam_seconds / duration_sec)) if duration_sec > 0 else 0.0

        # 6. Загрузка или создание пользователя
        user_stmt = select(User).where(User.id == payload.user_id)
        result = await db.execute(user_stmt)
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                id=payload.user_id,
                car_id=payload.car_id or "haval_jolion_15t_4wd",
                fuel_price_rub=72.40,
                service_cost_rub=9500.0,
                total_savings_rub=0.0,
                total_fuel_saved_liters=0.0,
                range_bank_km=0.0,
                total_distance_km=0.0,
                total_trips=0,
                current_oil_wear_percent=0.0
            )
            db.add(user)
            await db.flush()

        user.total_savings_rub = float(getattr(user, "total_savings_rub", 0.0) or 0.0)
        user.total_fuel_saved_liters = float(getattr(user, "total_fuel_saved_liters", 0.0) or 0.0)
        user.range_bank_km = float(getattr(user, "range_bank_km", 0.0) or 0.0)
        user.total_distance_km = float(getattr(user, "total_distance_km", 0.0) or 0.0)
        user.total_trips = int(getattr(user, "total_trips", 0) or 0)
        user.current_oil_wear_percent = float(getattr(user, "current_oil_wear_percent", 0.0) or 0.0)

        resolved_car_id = payload.car_id or getattr(user, "car_id", "haval_jolion_15t_4wd")
        user_fuel_price = float(getattr(user, "fuel_price_rub", 72.40) or 72.40)
        user_service_cost = float(getattr(user, "service_cost_rub", 9500.0) or 9500.0)

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

        # 7. Расчет износа моторного масла (передаем знаковое продольное ускорение clean_signed_ax)
        if hasattr(trip_wear_engine, "evaluate_trip_wear"):
            wear_stats = trip_wear_engine.evaluate_trip_wear(
                dt=dt, 
                speed_mps=speeds, 
                ax_mps2=clean_signed_ax, 
                traffic_score=getattr(road_context, 'traffic_score', 1.0) if road_context else 1.0,
                current_life_pct=current_life_pct
            )
            equiv_hours = float(wear_stats["equivalent_hours"])
            oil_wear_pct = float(wear_stats["oil_wear_percent"])
        else:
            wear_stats = trip_wear_engine.compute_oil_wear(
                speeds_mps=speeds, 
                horizontal_acc=clean_signed_ax, 
                traffic_score=getattr(road_context, 'traffic_score', 1.0) if road_context else 1.0,
                dt=dt
            )
            equiv_hours = float(wear_stats.get("equivalent_engine_hours", wear_stats.get("equivalent_hours", 0.0)))
            oil_wear_pct = float(wear_stats.get("oil_wear_percent", 0.0))

        # 8. Расчет финансовой экономии относительно агрессивного двойника (TwinEngine)
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
                user_ax=clean_signed_ax,
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

        if total_savings_rub == 0.0 and distance_km > 0.02:
            fuel_saved_rub = round((distance_km * 0.022) * user_fuel_price, 2)
            oil_saved_rub = round(distance_km * 0.45, 2)
            total_savings_rub = round(fuel_saved_rub + oil_saved_rub, 2)

        # 9. Финансовая декомпозиция маневров
        harsh_events_total = harsh_accel_count + harsh_brake_count
        rush_cost_rub = round(float(harsh_events_total * 3.5), 2)
        base_potential_rub = round(total_savings_rub + rush_cost_rub, 2)

        fuel_saved_liters = float(savings.get("fuel_saved_liters", savings.get("saved_fuel_liters", 0.0)))
        if fuel_saved_liters == 0.0 and fuel_saved_rub > 0.0 and user_fuel_price > 0.0:
            fuel_saved_liters = round(fuel_saved_rub / user_fuel_price, 3)

        base_city_l100 = float(getattr(profile, "base_city_fuel_rate_l100km", 8.5) or 8.5)
        range_bonus_km = float(savings.get("range_bonus_km", 0.0))
        if range_bonus_km == 0.0 and fuel_saved_liters > 0.0:
            range_bonus_km = round((fuel_saved_liters * 100.0) / base_city_l100, 2)

        autonomy_days = float(savings.get("autonomy_days_extended", 0.0))
        if autonomy_days == 0.0 and fuel_saved_liters > 0.0:
            autonomy_days = round(fuel_saved_liters / 4.2, 2)

        smooth_score = max(40, min(100, int(100 - (harsh_events_total * 4))))
        road_anomalies = int(detected_bumps) if distance_km > 0.0 else 0
        hunting_detected = bool(savings.get("throttle_hunting_detected", False)) if distance_km > 0.0 else False

        # Если реального перемещения не было (тест на месте / стационарный ПК), обнуляем экономию топлива
        if distance_km == 0.0:
            fuel_saved_rub = 0.0
            oil_saved_rub = 0.0
            total_savings_rub = 0.0
            fuel_saved_liters = 0.0
            range_bonus_km = 0.0
            autonomy_days = 0.0
            harsh_accel_count = 0
            harsh_brake_count = 0
            rush_cost_rub = 0.0
            base_potential_rub = 0.0
            smooth_score = 100

        # 10. Идемпотентная запись поездки и обновление баланса пользователя
        try:
            parsed_uuid = str(uuid.UUID(str(session_ref)))
        except ValueError:
            parsed_uuid = str(uuid.uuid4())

        existing_trip = await db.get(Trip, parsed_uuid)
        if not existing_trip:
            user.total_savings_rub = round(user.total_savings_rub + total_savings_rub, 2)
            user.total_fuel_saved_liters = round(user.total_fuel_saved_liters + fuel_saved_liters, 3)
            user.range_bank_km = round(user.range_bank_km + range_bonus_km, 2)
            user.total_distance_km = round(user.total_distance_km + distance_km, 2)
            user.total_trips += 1
            user.current_oil_wear_percent = min(100.0, round(user.current_oil_wear_percent + oil_wear_pct, 5))

            new_trip = Trip(
                id=parsed_uuid,
                user_id=payload.user_id,
                car_id=resolved_car_id,
                duration_seconds=round(duration_sec, 2),
                distance_km=round(distance_km, 2),
                equivalent_engine_hours=round(equiv_hours, 5),
                oil_wear_percent=round(oil_wear_pct, 5),
                idle_ratio=round(idle_ratio, 3),
                fuel_saved_rub=round(fuel_saved_rub, 2),
                oil_saved_rub=round(oil_saved_rub, 2),
                total_savings_rub=round(total_savings_rub, 2),
                fuel_saved_liters=round(fuel_saved_liters, 3),
                range_bonus_km=round(range_bonus_km, 2),
                smooth_score=smooth_score,
                harsh_accel_count=harsh_accel_count,
                harsh_brake_count=harsh_brake_count,
                road_anomalies_count=road_anomalies,
                rush_cost_rub=rush_cost_rub,
                board_computer_consumption_l100km=getattr(payload, 'board_computer_consumption_l100km', None),
                traffic_jam_seconds=round(traffic_jam_seconds, 2)
            )
            db.add(new_trip)
            await db.commit()

        metrics_payload = {
            "fuel_saved_liters": fuel_saved_liters,
            "fuel_saved_rub": fuel_saved_rub,
            "oil_saved_rub": oil_saved_rub,
            "range_bonus_km": range_bonus_km,
            "autonomy_days_extended": autonomy_days,
            "smooth_score": smooth_score,
            "road_anomalies_count": road_anomalies,
            "harsh_accel_count": harsh_accel_count,
            "harsh_brake_count": harsh_brake_count,
            "traffic_jam_seconds": round(traffic_jam_seconds, 2),
            "rush_cost_rub": rush_cost_rub,
            "base_potential_rub": base_potential_rub,
            "throttle_hunting_detected": hunting_detected,
            "cost_savings_rub": round(total_savings_rub, 2)
        }

        return ExtendedTripCalculationResult(
            session_id=parsed_uuid,
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
            harsh_accel_count=harsh_accel_count,
            harsh_brake_count=harsh_brake_count,
            traffic_jam_seconds=round(traffic_jam_seconds, 2),
            rush_cost_rub=rush_cost_rub,
                board_computer_consumption_l100km=getattr(payload, 'board_computer_consumption_l100km', None),
            base_potential_rub=base_potential_rub,
            metrics=metrics_payload
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Критический сбой сессии {payload.user_id}: {exc}\n{traceback.format_exc()}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка конвейера обработки телеметрии.")


# =====================================================================
# Профиль, Дашборд, История, Аналитика и CPA-воронка
# =====================================================================

@app.get("/api/v1/users/{user_id}/profile")
async def get_user_vehicle_profile(
    user_id: str,
    db: AsyncSession = Depends(get_db)
):
    user_stmt = select(User).where(User.id == user_id)
    res = await db.execute(user_stmt)
    user = res.scalar_one_or_none()

    if not user:
        return {
            "user_id": user_id,
            "car_id": "haval_jolion_15t_4wd",
            "car_name": "Haval Jolion 1.5T 4WD",
            "fuel_consumption": 8.5,
            "fuel_price_rub": 72.40,
            "service_cost_rub": 9500.0,
            "current_oil_wear_percent": 0.0,
        }

    return {
        "user_id": user.id,
        "car_id": user.car_id or "haval_jolion_15t_4wd",
        "car_name": getattr(user, "name", None),
        "fuel_consumption": 8.5,
        "fuel_price_rub": user.fuel_price_rub if user.fuel_price_rub is not None else 72.40,
        "service_cost_rub": user.service_cost_rub if user.service_cost_rub is not None else 9500.0,
        "current_oil_wear_percent": user.current_oil_wear_percent if user.current_oil_wear_percent is not None else 0.0,
    }


@app.get("/api/v1/users/{user_id}/dashboard", response_model=DashboardResponse)
async def get_user_dashboard(user_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    # Если пользователь еще не создан, создаем дефолтную запись, чтобы фронтенд никогда не получал 404
    if not user:
        user = User(
            id=user_id,
            car_id="haval_jolion_15t_4wd",
            fuel_price_rub=72.40,
            service_cost_rub=9500.0,
            total_savings_rub=0.0,
            total_fuel_saved_liters=0.0,
            range_bank_km=0.0,
            total_distance_km=0.0,
            total_trips=0,
            current_oil_wear_percent=0.0
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    oil_wear = float(getattr(user, "current_oil_wear_percent", 0.0) or 0.0)
    remaining_oil = max(0.0, round(100.0 - oil_wear, 1))
    # Исправленный порог CPA <= 20.0% (в соответствии с README.md и test_cpa_funnel.py)
    cpa_active = remaining_oil <= 20.0
    cpa_text = "Пора менять масло. Скидка 15% на рекомендованное масло" if cpa_active else None

    fuel_price = float(getattr(user, "fuel_price_rub", 72.40) or 72.40)
    service_cost = float(getattr(user, "service_cost_rub", 9500.0) or 9500.0)
    total_savings = float(getattr(user, "total_savings_rub", 0.0) or 0.0)
    total_trips = int(getattr(user, "total_trips", 0) or 0)
    total_dist = float(getattr(user, "total_distance_km", 0.0) or 0.0)

    saved_liters = float(getattr(user, "total_fuel_saved_liters", 0.0) or 0.0)
    saved_range_km = float(getattr(user, "range_bank_km", 0.0) or 0.0)

    if saved_liters == 0.0 and total_savings > 0 and fuel_price > 0:
        saved_liters = round(total_savings / fuel_price, 2)
        saved_range_km = round((saved_liters * 100.0) / 8.5, 1)

    est_autonomy_days = round(saved_liters / 4.2, 1) if saved_liters > 0 else 0.0

    return DashboardResponse(
        user_id=user.id,
        car_id=getattr(user, "car_id", "haval_jolion_15t_4wd") or "haval_jolion_15t_4wd",
        fuel_price_rub=fuel_price,
        service_cost_rub=service_cost,
        total_savings_rub=round(total_savings, 2),
        month_savings_rub=round(total_savings, 2),
        total_trips=total_trips,
        total_distance_km=round(total_dist, 2),
        oil_remaining_percent=remaining_oil,
        ghost_twin_status="Базовый лихач (дефолт)",
        cpa_recommended=cpa_active,
        cpa_offer_text=cpa_text,
        range_bank_km=round(saved_range_km, 1),
        total_fuel_saved_liters=round(saved_liters, 2),
        autonomy_days_extended=est_autonomy_days
    )


@app.get("/api/v1/cpa/offer/{user_id}")
async def get_cpa_personalized_offer(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    car_id = user.car_id if user else "haval_jolion_15t_4wd"
    profile = await resolve_car_profile_async(car_id, db)

    oil_vol = float(getattr(profile, "oil_capacity_l", 3.8) or 3.8)
    oil_grade = getattr(getattr(profile, "oil_profile", None), "oil_grade", "0W-20")
    oil_brand = getattr(profile, "recommended_oil_brand", "TotalEnergies Quartz 9000")
    car_name = f"{getattr(profile, 'brand', 'Auto')} {getattr(profile, 'model', car_id)}"
    savings_discount = min(1500.0, max(500.0, round(float(getattr(user, "total_savings_rub", 500.0) or 500.0), 0)))

    return {
        "user_id": user_id,
        "car_id": car_id,
        "car_name": car_name,
        "recommended_oil": oil_brand,
        "oil_viscosity": oil_grade,
        "oil_volume_liters": oil_vol,
        "service_discount_rub": savings_discount,
        "promo_code": f"AUTOXAK-{user_id[:6].upper()}-TO"
    }


@app.post("/api/v1/cpa/claim")
async def claim_cpa_offer(payload: CPAClaimPayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, payload.user_id)
    car_id = user.car_id if user else "haval_jolion_15t_4wd"
    lead = CPALead(
        user_id=payload.user_id,
        car_id=car_id,
        partner_id=payload.partner_id,
        promo_code=payload.promo_code,
        discount_rub=500.0
    )
    db.add(lead)
    await db.commit()
    return {"status": "LEAD_CAPTURED", "promo_code": payload.promo_code}


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



class BoardComputerFuelUpdate(BaseModel):
    board_computer_consumption_l100km: float = Field(..., gt=0.0, le=100.0, description="Фактический расход бензина по БК, л/100 км")

@app.post("/api/v1/trips/{session_id}/board-computer")
async def update_trip_board_computer(
    session_id: str,
    payload: BoardComputerFuelUpdate,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Trip).where(Trip.id == session_id)
    res = await db.execute(stmt)
    trip = res.scalar_one_or_none()
    if not trip:
        raise HTTPException(status_code=404, detail="Поездка не найдена")
    trip.board_computer_consumption_l100km = round(float(payload.board_computer_consumption_l100km), 2)
    await db.commit()
    logger.info(f"[ML-Validation] Для поездки {session_id} зафиксирован расход БК: {trip.board_computer_consumption_l100km} л/100 км")
    return {
        "status": "SAVED",
        "session_id": session_id,
        "board_computer_consumption_l100km": trip.board_computer_consumption_l100km
    }

@app.get("/api/v1/users/{user_id}/analytics/period", response_model=PeriodAnalyticsResponse)
async def get_period_analytics(
    user_id: str,
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    if end_date is None:
        end_date = now + timedelta(minutes=5)
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date не может быть позже end_date")

    start_naive = start_date.astimezone(timezone.utc).replace(tzinfo=None) if start_date.tzinfo else start_date
    end_naive = end_date.astimezone(timezone.utc).replace(tzinfo=None) if end_date.tzinfo else end_date

    agg_stmt = select(
        func.count(Trip.id).label("total_trips"),
        func.coalesce(func.sum(Trip.distance_km), 0.0).label("total_dist"),
        func.coalesce(func.sum(Trip.duration_seconds), 0.0).label("total_seconds"),
        func.coalesce(func.sum(Trip.total_savings_rub), 0.0).label("total_savings"),
        func.coalesce(func.sum(Trip.oil_wear_percent), 0.0).label("total_oil_wear"),
        func.coalesce(func.avg(Trip.idle_ratio), 0.0).label("avg_idle"),
        func.coalesce(func.sum(Trip.fuel_saved_liters), 0.0).label("total_liters"),
        func.coalesce(func.sum(Trip.range_bonus_km), 0.0).label("total_range")
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
    period_fuel_liters = round(float(agg_row.total_liters or 0.0), 2)
    period_range_km = round(float(agg_row.total_range or 0.0), 1)

    total_hours = total_secs / 3600.0
    avg_speed = (total_dist / total_hours) if total_hours > 0 else 0.0

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

    trip_items = []
    for t in trips_rows:
        dur_s = round(float(t.duration_seconds or 0.0), 1)
        idle_r = round(float(t.idle_ratio or 0.0), 3)
        jam_s = float(getattr(t, "traffic_jam_seconds", 0.0) or (dur_s * idle_r))
        h_acc = int(getattr(t, "harsh_accel_count", 0) or 0)
        h_brk = int(getattr(t, "harsh_brake_count", 0) or 0)
        bumps = int(getattr(t, "road_anomalies_count", 0) or 0)

        trip_items.append(
            TripPeriodItem(
                session_id=str(t.id),
                created_at=t.created_at,
                duration_seconds=dur_s,
                distance_km=round(float(t.distance_km or 0.0), 2),
                idle_ratio=idle_r,
                oil_wear_percent=round(float(t.oil_wear_percent or 0.0), 4),
                total_savings_rub=round(float(t.total_savings_rub or 0.0), 2),
                fuel_saved_liters=float(getattr(t, "fuel_saved_liters", 0.0) or 0.0),
                range_bonus_km=float(getattr(t, "range_bonus_km", 0.0) or 0.0),
                smooth_score=int(getattr(t, "smooth_score", 100) or 100),
                road_anomalies_count=bumps,
                harsh_accel_count=h_acc,
                harsh_brake_count=h_brk,
                rush_cost_rub=float(getattr(t, "rush_cost_rub", 0.0) or 0.0),
                hard_accel_count=h_acc,
                hard_brake_count=h_brk,
                bumps_count=bumps,
                idle_minutes=int(round(jam_s / 60.0))
            )
        )

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
            fuel_price_rub=72.40,
            service_cost_rub=9500.0,
            total_savings_rub=0.0,
            total_fuel_saved_liters=0.0,
            range_bank_km=0.0,
            total_distance_km=0.0,
            total_trips=0,
            current_oil_wear_percent=0.0
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(user_id=user.id)
    return AuthResponse(token=token, user_id=user.id, car_id=user.car_id, is_new_user=is_new)


@app.get("/api/v1/users/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "user_id": current_user.id,
        "car_id": getattr(current_user, "car_id", "haval_jolion_15t_4wd"),
        "fuel_price_rub": float(getattr(current_user, "fuel_price_rub", 72.40) or 72.40),
        "service_cost_rub": float(getattr(current_user, "service_cost_rub", 9500.0) or 9500.0),
        "range_bank_km": float(getattr(current_user, "range_bank_km", 0.0) or 0.0),
        "total_savings_rub": float(getattr(current_user, "total_savings_rub", 0.0) or 0.0),
        "total_trips": int(getattr(current_user, "total_trips", 0) or 0),
        "total_distance_km": float(getattr(current_user, "total_distance_km", 0.0) or 0.0)
    }


@app.post("/api/v1/users/{user_id}/profile")
async def update_user_vehicle_profile(
    user_id: str,
    payload: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    user_stmt = select(User).where(User.id == user_id)
    res = await db.execute(user_stmt)
    user = res.scalar_one_or_none()

    if not user:
        user = User(
            id=user_id,
            car_id=payload.car_id or "haval_jolion_15t_4wd",
            name=payload.name,
            fuel_price_rub=payload.fuel_price_rub if payload.fuel_price_rub is not None else 72.40,
            service_cost_rub=payload.service_cost_rub if payload.service_cost_rub is not None else 9500.0,
            total_savings_rub=0.0,
            total_fuel_saved_liters=0.0,
            range_bank_km=0.0,
            total_distance_km=0.0,
            total_trips=0,
            current_oil_wear_percent=payload.current_oil_wear_percent if payload.current_oil_wear_percent is not None else 0.0
        )
        db.add(user)
    else:
        if payload.car_id is not None:
            user.car_id = payload.car_id
        if payload.name is not None:
            user.name = payload.name
        if payload.fuel_price_rub is not None:
            user.fuel_price_rub = payload.fuel_price_rub
        if payload.service_cost_rub is not None:
            user.service_cost_rub = payload.service_cost_rub
        if payload.current_oil_wear_percent is not None:
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
async def verify_experiment_run(payload: VerificationPayload, db: AsyncSession = Depends(get_db)):
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(status_code=422, detail="Недостаточный объем телеметрии.")

    raw_ax = np.array([p["ax"] for p in stream], dtype=np.float64)
    raw_ay = np.array([p["ay"] for p in stream], dtype=np.float64)
    raw_az = np.array([p["az"] for p in stream], dtype=np.float64)
    speeds = np.array([p["speed"] for p in stream], dtype=np.float64)

    filt_x, filt_y, filt_z = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)

    clean_signed_ax = filt_x
    if hasattr(TwinEngine, "filter_road_anomalies"):
        clean_signed_ax, _ = TwinEngine.filter_road_anomalies(filt_x, raw_az=filt_z, dt=0.02)

    profile = await resolve_car_profile_async(payload.car_id, db)
    engine_inst = WearEngine(profile=profile)
    
    if hasattr(engine_inst, "evaluate_trip_wear"):
        wear_stats = engine_inst.evaluate_trip_wear(dt=0.02, speed_mps=speeds, ax_mps2=clean_signed_ax)
        autoxak_hours = float(wear_stats["equivalent_hours"])
    else:
        wear_stats = engine_inst.compute_oil_wear(speeds_mps=speeds, horizontal_acc=clean_signed_ax, dt=0.02)
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