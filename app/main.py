import os
import math
import asyncio
import uuid
import time
import traceback
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager

import numpy as np
from pydantic import BaseModel, Field, model_validator
from fastapi import FastAPI, HTTPException, Depends, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.exc import IntegrityError

from app.core.logger import logger
from app.api.auth import router as auth_router
from app.api.recordings import router as recordings_router
from app.api.reference_math import router as reference_math_router
from app.services.reference_math import VERSION as MATH_VERSION, trajectory_metrics, threshold_events

from app.core.database import engine, get_db, initialize_schema
from app.api.dependencies import require_user, require_owner
from app.services.profile_synthesizer import ProfileSynthesizer
from app.schemas.vehicle import CustomVehicleInput
from fastapi.encoders import jsonable_encoder
from app.models.telemetry import TripSessionPayload, TelemetryPoint, TelemetryPreviewPayload
from app.models.results import (
    TripCalculationResult, 
    TripHistoryResponse, 
    TripSummaryItem
)
from app.models.db_models import User, Trip, VehicleMake, VehicleModel, VehicleTrim, FuelRegionalPrice, CPALead
from app.core.dsp.filters import SignalFilter
from app.core.dsp.alignment import IMUAlignmentService
from app.services.wear_engine import WearEngine
from app.services.capture_storage import capture_quality, store_raw_capture, unscored_trip

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
    device_id: str = Field(..., min_length=1, max_length=200, description="Уникальный отпечаток устройства / браузера")
    preferred_car_id: Optional[str] = Field(default="haval_jolion_15t_4wd")


class AuthResponse(BaseModel):
    token: str
    user_id: str
    car_id: str
    is_new_user: bool
    is_guest: bool = True
    name: str = "Гость"


class ExtendedTripCalculationResult(TripCalculationResult):
    fuel_saved_liters: Optional[float] = Field(default=None, description="Сбереженное топливо, л")
    fuel_saved_rub: Optional[float] = Field(default=None, description="Сбережения на топливе, ₽")
    oil_saved_rub: Optional[float] = Field(default=None, description="Сбережения на масле, ₽")
    range_bonus_km: Optional[float] = Field(default=None, description="Добавленный запас хода, км")
    autonomy_days_extended: Optional[float] = Field(default=None, description="Продление дней до заправки")
    smooth_score: Optional[int] = Field(default=None, description="Индекс плавности вождения 0..100")
    road_anomalies_count: int = Field(default=0, description="Пройденные лежачие полицейские и ямы")
    throttle_hunting_detected: Optional[bool] = Field(default=None, description="Обнаружено рыскание газом")

    harsh_accel_count: Optional[int] = Field(default=None, description="Число резких разгонов")
    harsh_brake_count: Optional[int] = Field(default=None, description="Число резких торможений")
    traffic_jam_seconds: float = Field(default=0.0, description="Время ниже порога скорости; не измерение работы двигателя, с")
    rush_cost_rub: Optional[float] = Field(default=None, description="Потери выгоды от резких маневров, ₽")
    base_potential_rub: Optional[float] = Field(default=None, description="Потенциальная экономия при идеальной плавности, ₽")

    metrics: Optional[Dict[str, Any]] = Field(default=None, description="Полный словарь метрик для PWA")


class TripPeriodItem(BaseModel):
    session_id: str
    created_at: datetime
    duration_seconds: float
    distance_km: float
    idle_ratio: float
    oil_wear_percent: Optional[float]
    total_savings_rub: Optional[float]
    fuel_saved_liters: Optional[float] = None
    range_bonus_km: Optional[float] = None
    smooth_score: Optional[int] = None
    road_anomalies_count: int = 0
    harsh_accel_count: Optional[int] = None
    harsh_brake_count: Optional[int] = None
    rush_cost_rub: Optional[float] = None
    # Поля совместимости с модальным окном "Подробнее" в history.html
    hard_accel_count: Optional[int] = None
    hard_brake_count: Optional[int] = None
    bumps_count: int = 0
    idle_minutes: int = 0
    capture_quality: Optional[Dict[str, Any]] = None
    math_status: Optional[Dict[str, Any]] = None


class TripsPeriodSummaryMetrics(BaseModel):
    total_trips: int = Field(..., description="Количество поездок за период")
    total_distance_km: float = Field(..., description="Суммарная дистанция, км")
    total_duration_hours: float = Field(..., description="Суммарное время за рулем, ч")
    total_savings_rub: float = Field(..., description="Совокупная экономия, ₽")
    total_oil_wear_percent: Optional[float] = Field(default=None, description="Накопленный износ масла, %")
    avg_speed_kmh: float = Field(..., description="Средняя скорость движения, км/ч")
    avg_idle_ratio: float = Field(..., description="Доля времени ниже порога скорости, взвешенная по длительности поездок")
    total_fuel_saved_liters: float = Field(default=0.0, description="Всего сбережено топлива, л")
    total_range_bonus_km: float = Field(default=0.0, description="Всего добавлено запаса хода, км")
    balance_provenance: str = 'legacy_or_user_reported_not_reference_validated'


class PeriodAnalyticsResponse(BaseModel):
    user_id: str
    start_date: datetime
    end_date: datetime
    summary: TripsPeriodSummaryMetrics
    trips: List[TripPeriodItem]
    limit: int = 200
    offset: int = 0


class DashboardResponse(BaseModel):
    user_id: str
    car_id: str = Field(default="haval_jolion_15t_4wd", description="Идентификатор автомобиля пользователя")
    fuel_price_rub: float = Field(default=72.40, description="Установленная цена топлива, ₽/л")
    service_cost_rub: float = Field(default=9500.0, description="Стоимость планового ТО, ₽")
    total_savings_rub: float = Field(default=0.0, description="Накопленная выгода за все время, ₽")
    month_savings_rub: float = Field(default=0.0, description="Сэкономлено за месяц, ₽")
    total_trips: int = Field(default=0, description="Всего завершенных поездок")
    total_distance_km: float = Field(default=0.0, description="Суммарный подтвержденный пробег, км")
    oil_remaining_percent: Optional[float] = Field(default=None, description="Остаточный ресурс масла, %")
    ghost_twin_status: str = Field(default="Для сравнения нужна контрольная поездка", description="Статус модели сравнения")
    math_version: str = MATH_VERSION
    balance_provenance: str = 'legacy_or_user_reported_not_reference_validated'
    oil_resource_status: str = 'unavailable_without_validated_degradation_model'
    cpa_recommended: bool = Field(default=False, description="Флаг рекомендации замены масла")
    cpa_offer_text: Optional[str] = Field(default=None, description="Текст партнерского предложения")
    range_bank_km: float = Field(default=0.0, description="Накопленный банк автономности, км")
    total_fuel_saved_liters: float = Field(default=0.0, description="Всего сбережено топлива, л")
    autonomy_days_extended: Optional[float] = Field(default=None, description="Продлено дней до АЗС")


class ProfileUpdateRequest(BaseModel):
    model_config = {"allow_inf_nan": False}
    car_name: Optional[str] = Field(default=None, max_length=200)
    car_id: Optional[str] = Field(default=None, min_length=1, max_length=100, description="Идентификатор ТС в реестре")
    fuel_price_rub: Optional[float] = Field(default=None, gt=0.0, le=10000)
    service_cost_rub: Optional[float] = Field(default=None, gt=0.0, le=10000000)
    current_oil_wear_percent: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    name: Optional[str] = Field(default=None, max_length=100)
    user_id: Optional[str] = None

UserProfileUpdatePayload = ProfileUpdateRequest


class VerificationPayload(BaseModel):
    session_id: str
    user_id: str
    car_id: str
    telemetry_stream: List[TelemetryPoint] = Field(min_length=50, max_length=500000)
    user_savings: List[float] = Field(default_factory=list, max_length=100000)
    model_config = {"allow_inf_nan": False}
    obd_ground_truth: Dict[str, List[float]]

    @model_validator(mode="after")
    def aligned_obd_signals(self):
        times = [point.t for point in self.telemetry_stream]
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("Telemetry timestamps must increase")
        expected = len(times)
        for key in ("engine_rpm", "engine_load", "oil_temperature"):
            if len(self.obd_ground_truth.get(key, [])) != expected:
                raise ValueError("Every OBD signal must match telemetry length")
        BenchmarkEngine.calculate_obd_ground_truth_hours(
            self.obd_ground_truth["engine_rpm"], self.obd_ground_truth["engine_load"], self.obd_ground_truth["oil_temperature"],times_s=times)
        return self



class CPAClaimPayload(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    promo_code: str = Field(min_length=1, max_length=64)
    partner_id: str = Field(default="autodoc_lead_engine", min_length=1, max_length=64)


# =====================================================================
# Авторизация и контекст пользователя
# =====================================================================

get_current_user = require_user

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

    return VehiclePhysicalProfile(car_id='unknown',brand='Unknown',model='Unknown')


async def resolve_car_profile_async(car_id: Optional[str], db: AsyncSession, user: Optional[User] = None) -> Any:
    """Извлекает физический профиль ТС из PostgreSQL или статического реестра."""
    if user is not None and user.custom_profile and user.custom_profile.get("car_id") == car_id:
        return VehiclePhysicalProfile.model_validate(user.custom_profile)
    if not car_id:
        return get_default_profile()

    stmt = select(VehicleTrim).where(VehicleTrim.id == car_id)
    res = await db.execute(stmt)
    trim = res.scalar_one_or_none()

    if trim:
        return VehiclePhysicalProfile(
            car_id=trim.id,brand='Catalogue',model=trim.badge_name,
            curb_weight_kg=trim.curb_weight_kg,rolling_resistance_coeff=trim.rolling_resistance_coeff,
            drag_coefficient_area=trim.drag_coefficient_area,drivetrain_efficiency=trim.drivetrain_efficiency,
            engine_displacement_l=trim.engine_displacement_l,rated_power_kw=trim.rated_power_kw,
            oil_capacity_l=trim.oil_capacity_l)
    if VEHICLE_REGISTRY and car_id in VEHICLE_REGISTRY:
        return VEHICLE_REGISTRY[car_id]
    # An unknown ID must not silently inherit the physics of a Haval.
    return VehiclePhysicalProfile(car_id=car_id,brand='Unknown',model='Unknown')


# =====================================================================
# Вспомогательные алгоритмы геодезии и кластеризации маневров
# =====================================================================

def count_maneuver_events(signal: np.ndarray, threshold: float, is_greater: bool = True, min_points: int = 5, dt: float = 0.02, min_duration_s=None) -> int:
    if len(signal)<2:
        return 0
    if not math.isfinite(dt) or dt<=0:
        raise ValueError('A positive finite sample period is required')
    return threshold_events(np.arange(len(signal))*dt,signal,threshold=threshold,above=is_greater,
                            min_duration_s=min_points*dt if min_duration_s is None else min_duration_s,
                            refractory_s=.5)['count']


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
    await initialize_schema()
    try:
        yield
    finally:
        await engine.dispose()
        await gis_service.aclose()


app = FastAPI(
    title="autoXAK Fuel Autonomy & Telematics Engine",
    description="Пайплайн Sensor Fusion, фильтрации неровностей, топливной автономии и трибологии ДВС",
    version="2.0.0",
    lifespan=lifespan
)

app.include_router(auth_router)
app.include_router(recordings_router)
app.include_router(reference_math_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()],
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


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(_resolve_static_file("sw.js"), media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/ready", include_in_schema=False)
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Separate process liveness from readiness for database-backed requests."""
    try:
        async with asyncio.timeout(3):
            await db.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Readiness check: database unavailable")
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ready"}


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "autoXAK Fuel Autonomy & Wear Engine",
        "engine": "Reference mathematics and durable recordings",
        "version": "2.0.0"
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
    if target_grade not in {"ai92", "ai95", "ai100", "dt"}:
        raise HTTPException(422, "Unknown fuel grade")
    stmt = select(FuelRegionalPrice).where(FuelRegionalPrice.region_code == region_code)
    if brand:
        stmt = stmt.where(FuelRegionalPrice.brand == brand)
    res = await db.execute(stmt.order_by(FuelRegionalPrice.brand))
    item = res.scalars().first()
    if brand and item is None:
        raise HTTPException(404, "Fuel brand is unavailable in this region")

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

def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _stored_trip_result(trip: Trip) -> ExtendedTripCalculationResult:
    return ExtendedTripCalculationResult(
        session_id=trip.id, duration_seconds=trip.duration_seconds, distance_km=trip.distance_km,
        oil_wear_percent=trip.oil_wear_percent, cost_savings_rub=trip.total_savings_rub,
        fuel_saved_liters=trip.fuel_saved_liters, fuel_saved_rub=trip.fuel_saved_rub,
        oil_saved_rub=trip.oil_saved_rub, range_bonus_km=trip.range_bonus_km,
        smooth_score=trip.smooth_score, road_anomalies_count=trip.road_anomalies_count,
        harsh_accel_count=trip.harsh_accel_count, harsh_brake_count=trip.harsh_brake_count,
        traffic_jam_seconds=trip.traffic_jam_seconds, rush_cost_rub=trip.rush_cost_rub)


@app.post("/api/v1/vehicles/synthesize")
async def synthesize_vehicle(payload: CustomVehicleInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)):
    profile = ProfileSynthesizer.synthesize_profile(payload)
    user.custom_profile = jsonable_encoder(profile)
    await db.commit()
    return user.custom_profile


async def _calculate_session_values(payload, db, user, *, max_gap_seconds=2.0):
    """Shared calculation only: no trip writes, balance changes or commits."""
    resolved_car_id = payload.car_id or getattr(user, "car_id", "haval_jolion_15t_4wd")
    profile = await resolve_car_profile_async(resolved_car_id, db, user)
    values = await run_in_threadpool(
        _calculate_session_values_sync,
        payload,
        profile,
        user,
        None,
        max_gap_seconds=max_gap_seconds,
    )
    return values


async def _evaluate_reference_evidence(payload):
    """Bind externally supplied model inputs to the recorded trajectory, atomically."""
    from app.api.reference_math import evaluate_run
    from app.services.reference_math import compare_fuel, timeline

    evidence = payload.reference_math
    if evidence is None:
        return None
    run = evidence.run
    if payload.raw_recording is not None:
        gps = payload.raw_recording.gps
        actual_times = [point.timestamp_ms / 1000 for point in gps]
        actual_speeds = [point.speed_mps for point in gps]
        if any(
            point.speed_mps is None or point.accuracy_m is None or point.accuracy_m > 50
            for point in gps
        ):
            return {
                "status": "rejected",
                "reason": "Reference run requires complete accurate GPS speed observations",
                "math_version": MATH_VERSION,
                "raw_recording_retained": True,
            }
    else:
        actual_times = [point.t for point in payload.telemetry_stream]
        actual_speeds = [point.speed for point in payload.telemetry_stream]
    try:
        if len(run.times_s) != len(actual_times) or not np.allclose(
            timeline(run.times_s), timeline(actual_times), rtol=0, atol=1e-6
        ):
            raise ValueError("Reference timestamps do not match recorded observations")
        if not np.allclose(run.speeds_mps, actual_speeds, rtol=0, atol=1e-9):
            raise ValueError("Reference speeds do not match recorded observations")
        evaluated = await run_in_threadpool(evaluate_run, run)
        if evidence.fuel_comparison is not None:
            comparison = evidence.fuel_comparison
            actual = comparison.actual
            if (
                actual.vehicle_id != payload.car_id
                or not math.isclose(
                    actual.distance_m,
                    evaluated["kinematics"]["distance_m"],
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
                or not math.isclose(
                    actual.duration_s,
                    evaluated["kinematics"]["duration_s"],
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
            ):
                raise ValueError(
                    "Actual fuel comparison must describe this recorded trajectory"
                )
            if evaluated["observed_fuel_l"] is not None and not math.isclose(
                actual.fuel_l, evaluated["observed_fuel_l"], rel_tol=1e-9, abs_tol=1e-9
            ):
                raise ValueError(
                    "Actual fuel volume conflicts with the supplied observed fuel flow"
                )
            evaluated["fuel_comparison"] = compare_fuel(
                actual.model_dump(),
                comparison.reference.model_dump(),
                price_rub_per_l=comparison.price_rub_per_l,
            )
        evaluated["trajectory_binding"] = (
            "recorded_gps"
            if payload.raw_recording is not None
            else "recorded_legacy_telemetry"
        )
        if payload.raw_recording is not None:
            raw = payload.raw_recording
            evaluated["recording_coverage"] = {
                "recording_duration_s": (raw.ended_at_ms - raw.started_at_ms) / 1000,
                "evaluated_duration_s": evaluated["kinematics"]["duration_s"],
                "start_offset_s": (gps[0].timestamp_ms - raw.started_at_ms) / 1000,
                "end_offset_s": (raw.ended_at_ms - gps[-1].timestamp_ms) / 1000,
            }
        return evaluated
    except (
        ValueError,
        OverflowError,
        FloatingPointError,
        np.linalg.LinAlgError,
    ) as error:
        return {
            "status": "rejected",
            "reason": str(error),
            "math_version": MATH_VERSION,
            "raw_recording_retained": payload.raw_recording is not None,
        }


async def _persist_math_evidence(db, payload, trip_id, result):
    if payload.reference_math is None:
        return
    from app.models.recordings import TripMathEvidenceRecord
    from app.services.reference_math import fingerprint

    inputs = payload.reference_math.model_dump(mode="json")
    # Flush the trip parent before its dependent immutable evidence.
    await db.flush()
    db.add(
        TripMathEvidenceRecord(
            trip_id=trip_id,
            math_version=MATH_VERSION,
            inputs_sha256=fingerprint(inputs),
            inputs=inputs,
            result=result,
        )
    )


def _calculate_session_values_sync(
    payload, profile, user, road_context, *, max_gap_seconds=2.0
):
    stream = payload.telemetry_stream
    points_count = len(stream)
    times = np.array([p.t for p in stream], dtype=np.float64)
    raw_ax = np.array([p.ax for p in stream], dtype=np.float64)
    raw_ay = np.array([p.ay for p in stream], dtype=np.float64)
    raw_az = np.array([p.az for p in stream], dtype=np.float64)
    speeds = np.array([p.speed for p in stream], dtype=np.float64)

    # 1. Вычисление реального шага времени dt по меткам времени (защита от плавающей частоты браузера)
    times -= times[0]
    raw_diffs = np.diff(times)
    dt = float(np.mean(raw_diffs))
    if not math.isfinite(dt) or dt < 0.001:
        raise HTTPException(
            422, "Telemetry sampling period must be at least one millisecond"
        )
    if np.max(raw_diffs) > max_gap_seconds:
        raise HTTPException(422, "Telemetry contains gaps longer than two seconds")

    span_sec = float(times[-1] - times[0]) if times[-1] > times[0] else 0.0
    duration_sec = span_sec

    # 2. Выделение чистого линейного ускорения без гравитации (filt_z в покое = 0.0, а не 9.81)
    local_filter = SignalFilter(sample_rate_hz=1.0 / dt, cutoff_hz=min(2.5, 0.45 / dt))
    grid = np.linspace(times[0], times[-1], points_count)
    axes = [np.interp(grid, times, axis) for axis in (raw_ax, raw_ay, raw_az)]
    aligned = IMUAlignmentService(sample_rate_hz=1.0 / dt).calibrate_and_transform(
        *axes, np.interp(grid, times, speeds)
    )
    filt_x = local_filter.apply_butterworth_lpf(aligned[0])

    shock_z = SignalFilter(
        sample_rate_hz=1.0 / dt, cutoff_hz=min(12.0, 0.45 / dt)
    ).apply_butterworth_lpf(aligned[2])

    kinematics = trajectory_metrics(times, speeds, idle_threshold_mps=1.0)
    duration_sec = kinematics["duration_s"]
    distance_km = kinematics["distance_m"] / 1000
    is_moving_session = bool(np.max(speeds) > 0.5)

    # 4. Вертикальные удары сохраняют полосу до 12 Гц; продольный сигнал сглажен до 2.5 Гц.
    if is_moving_session and hasattr(TwinEngine, "filter_road_anomalies"):
        clean_signed_ax, detected_bumps = TwinEngine.filter_road_anomalies(
            filt_x, raw_az=shock_z, dt=dt
        )
    else:
        clean_signed_ax, detected_bumps = filt_x, 0

    # 5. Подсчет дискретных резких разгонов и торможений
    maneuver_points = max(1, math.ceil(0.1 / dt))
    harsh_accel_count = count_maneuver_events(
        clean_signed_ax,
        threshold=2.0,
        is_greater=True,
        min_points=maneuver_points,
        dt=dt,
        min_duration_s=0.1,
    )
    harsh_brake_count = count_maneuver_events(
        clean_signed_ax,
        threshold=-2.3,
        is_greater=False,
        min_points=maneuver_points,
        dt=dt,
        min_duration_s=0.1,
    )

    if not aligned[3].get("is_aligned"):
        horizontal_norm = np.hypot(
            filt_x, local_filter.apply_butterworth_lpf(aligned[1])
        )
        if np.max(horizontal_norm) >= 2.0:
            harsh_accel_count = harsh_brake_count = None
    traffic_jam_seconds = kinematics["below_threshold_s"]
    idle_ratio = traffic_jam_seconds / duration_sec
    # A numerical output is not evidence of oil condition or counterfactual fuel.
    # Legacy public entry points now return unavailable instead of fitted constants.
    wear = WearEngine(profile=profile).evaluate_trip_wear(
        times_s=times, speed_mps=speeds, ax_mps2=clean_signed_ax
    )
    savings = TwinEngine(profile=profile).evaluate_financial_delta()
    if distance_km == 0:
        harsh_accel_count = harsh_brake_count = detected_bumps = 0
    math_status = {
        "version": MATH_VERSION,
        "kinematics": kinematics,
        "imu_alignment": aligned[3],
        "low_speed_time_is_not_engine_runtime": True,
        "dsp_interpolation": "linear_regular_grid_for_filtering_only",
        "fuel_savings": {"status": savings["status"], "reason": savings["reason"]},
        "oil_wear": {"status": wear["status"], "reason": wear["reason"]},
        "monetary_credit_status": "not_verified",
        "style_score_status": "unavailable_without_validated_scoring_policy",
        "counterfactual_status": "explicit_matched_reference_required",
    }
    return {
        "duration_sec": duration_sec,
        "distance_km": distance_km,
        "idle_ratio": idle_ratio,
        "resolved_car_id": payload.car_id,
        "equiv_hours": None,
        "oil_wear_pct": None,
        "total_savings_rub": None,
        "fuel_saved_liters": None,
        "range_bonus_km": None,
        "oil_saved_rub": None,
        "fuel_saved_rub": None,
        "autonomy_days": None,
        "harsh_accel_count": harsh_accel_count,
        "harsh_brake_count": harsh_brake_count,
        "rush_cost_rub": None,
        "base_potential_rub": None,
        "smooth_score": None,
        "road_anomalies": int(detected_bumps),
        "hunting_detected": None,
        "traffic_jam_seconds": traffic_jam_seconds,
        "math_status": math_status,
    }
@app.post("/api/v1/telemetry/preview")
async def preview_telemetry_session(payload: TelemetryPreviewPayload,
                                    db: AsyncSession = Depends(get_db),
                                    owner: User = Depends(require_user)):
    if payload.user_id != owner.id:
        raise HTTPException(403, "Access to another user is forbidden")
    stream = payload.telemetry_stream
    if sum(p.lat is not None and p.lon is not None for p in stream) / len(stream) < 0.9:
        raise HTTPException(422, "Accurate GPS coverage is required for a provisional estimate")
    values = await _calculate_session_values(payload, db, owner, max_gap_seconds=2.0 * payload.sampling_stride)
    return {"status": "PREVIEW", "provisional": True, "session_id": payload.session_id,
            "cost_savings_rub": None, "math_status": values["math_status"],
            "distance_km": round(values["distance_km"], 3),
            "duration_seconds": round(values["duration_sec"], 2),
            "sample_count": len(stream), "sampling_stride": payload.sampling_stride}


@app.post("/api/v1/telemetry/session", response_model=ExtendedTripCalculationResult)
async def process_telemetry_session(
    payload: TripSessionPayload,
    db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_user)
):
    if payload.user_id != owner.id:
        raise HTTPException(403, "Access to another user is forbidden")

    stream = payload.telemetry_stream or []
    points_count = len(stream)
    session_ref = getattr(payload, "session_id", "new_session")

    logger.info(f"Получена сессия {session_ref} (user={payload.user_id}, точек={points_count})")

    if points_count < 50 and payload.raw_recording is None:
        raise HTTPException(
            status_code=422,
            detail="Недостаточно точек телеметрии для валидации (минимум 50 точек)."
        )

    try:
        parsed_uuid = str(uuid.UUID(session_ref)) if _is_uuid(session_ref) else str(uuid.uuid5(uuid.NAMESPACE_URL, f"autoxak:{owner.id}:{session_ref}"))
        excluded = {"user_id"} if payload.raw_recording is not None else {"user_id", "raw_recording"}
        if payload.reference_math is None:
            excluded.add('reference_math')  # Preserve canonical hashes of pre-v2 queued requests.
        digest = hashlib.sha256(json.dumps(payload.model_dump(mode="json", exclude=excluded), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        user = (await db.execute(select(User).where(User.id == owner.id).with_for_update().execution_options(populate_existing=True))).scalar_one()
        existing_trip = (await db.execute(select(Trip).where(or_(Trip.id == parsed_uuid, and_(Trip.user_id == owner.id, Trip.client_session_id == session_ref))))).scalar_one_or_none()
        if existing_trip is not None:
            if existing_trip.user_id != owner.id or (existing_trip.request_hash and existing_trip.request_hash != digest):
                raise HTTPException(409, "Session identifier already contains different data")
            if existing_trip.calculation_result:
                return ExtendedTripCalculationResult.model_validate(existing_trip.calculation_result)
            return _stored_trip_result(existing_trip)
        reference_evaluation=await _evaluate_reference_evidence(payload)
        quality = None
        if payload.raw_recording is not None:
            quality = capture_quality(payload)
            profile = await resolve_car_profile_async(payload.car_id, db, user)
            vehicle = {"vehicle_id": payload.car_id, "fuel_type": "unknown",
                       "mass_kg": profile.curb_weight_kg, "displacement_l": profile.engine_displacement_l, "parameter_status":"declared_not_reference_validated"}
            recording=await store_raw_capture(db, payload, parsed_uuid, digest, vehicle, quality)
            if reference_evaluation is not None:
                recording.fuel_estimate={**recording.fuel_estimate,'reference_evaluation':reference_evaluation}
            if quality["analysis_status"] != "available":
                trip, result = unscored_trip(payload, parsed_uuid, digest, user, quality)
                if reference_evaluation is not None:
                    result['metrics']['math_status']['reference_evaluation']=reference_evaluation
                db.add(trip)
                calculation = ExtendedTripCalculationResult.model_validate(result)
                trip.calculation_result = calculation.model_dump(mode="json")
                await _persist_math_evidence(db,payload,parsed_uuid,reference_evaluation)
                await db.commit()
                return calculation
        values = await _calculate_session_values(payload, db, user)
        if reference_evaluation is not None:
            values['math_status']['reference_evaluation']=reference_evaluation
        user.total_savings_rub = float(getattr(user, "total_savings_rub", 0.0) or 0.0)
        user.total_fuel_saved_liters = float(getattr(user, "total_fuel_saved_liters", 0.0) or 0.0)
        user.range_bank_km = float(getattr(user, "range_bank_km", 0.0) or 0.0)
        user.total_distance_km = float(getattr(user, "total_distance_km", 0.0) or 0.0)
        user.total_trips = int(getattr(user, "total_trips", 0) or 0)
        user.current_oil_wear_percent = float(getattr(user, "current_oil_wear_percent", 0.0) or 0.0)

        duration_sec = values["duration_sec"]
        distance_km = values["distance_km"]
        equiv_hours = values["equiv_hours"]
        oil_wear_pct = values["oil_wear_pct"]
        idle_ratio = values["idle_ratio"]
        resolved_car_id = values["resolved_car_id"]
        total_savings_rub = values["total_savings_rub"]
        fuel_saved_liters = values["fuel_saved_liters"]
        range_bonus_km = values["range_bonus_km"]
        oil_saved_rub = values["oil_saved_rub"]
        fuel_saved_rub = values["fuel_saved_rub"]
        autonomy_days = values["autonomy_days"]
        harsh_accel_count = values["harsh_accel_count"]
        harsh_brake_count = values["harsh_brake_count"]
        rush_cost_rub = values["rush_cost_rub"]
        base_potential_rub = values["base_potential_rub"]
        smooth_score = values["smooth_score"]
        road_anomalies = values["road_anomalies"]
        hunting_detected = values["hunting_detected"]
        traffic_jam_seconds = values["traffic_jam_seconds"]

        # Only measured trajectory counters change. Legacy balances are retained;
        # unavailable reference quantities cannot create new financial/oil credits.
        user.total_distance_km = float(user.total_distance_km) + distance_km
        user.total_trips += 1

        new_trip = Trip(
            id=parsed_uuid,
            client_session_id=session_ref,
            user_id=payload.user_id,
            car_id=resolved_car_id,
            duration_seconds=duration_sec,
            distance_km=distance_km,
            equivalent_engine_hours=round(equiv_hours or 0.0, 5),
            oil_wear_percent=round(oil_wear_pct or 0.0, 5),
            idle_ratio=idle_ratio,
            fuel_saved_rub=round(fuel_saved_rub or 0.0, 2),
            oil_saved_rub=round(oil_saved_rub or 0.0, 2),
            total_savings_rub=round(total_savings_rub or 0.0, 2),
            fuel_saved_liters=round(fuel_saved_liters or 0.0, 3),
            range_bonus_km=round(range_bonus_km or 0.0, 2),
            smooth_score=smooth_score if smooth_score is not None else 100,
            harsh_accel_count=harsh_accel_count or 0,
            harsh_brake_count=harsh_brake_count or 0,
            road_anomalies_count=road_anomalies,
            rush_cost_rub=rush_cost_rub or 0.0,
            board_computer_consumption_l100km=getattr(payload, 'board_computer_consumption_l100km', None),
            traffic_jam_seconds=traffic_jam_seconds,
            request_hash=digest
        )
        db.add(new_trip)

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
            "cost_savings_rub": total_savings_rub,
            "math_status": values["math_status"]
        }
        if quality is not None:
            metrics_payload["capture_quality"] = quality

        calculation = ExtendedTripCalculationResult(
            session_id=parsed_uuid,
            duration_seconds=duration_sec,
            distance_km=distance_km,
            oil_wear_percent=oil_wear_pct,
            cost_savings_rub=total_savings_rub,
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
            traffic_jam_seconds=traffic_jam_seconds,
            rush_cost_rub=rush_cost_rub,
                board_computer_consumption_l100km=getattr(payload, 'board_computer_consumption_l100km', None),
            base_potential_rub=base_potential_rub,
            metrics=metrics_payload
        )

        if payload.raw_recording is not None:
            new_trip.created_at = datetime.fromtimestamp(payload.raw_recording.started_at_ms / 1000, tz=timezone.utc)
        new_trip.calculation_result = calculation.model_dump(mode="json")
        await _persist_math_evidence(db,payload,parsed_uuid,reference_evaluation)
        await db.commit()
        return calculation

    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Session identifier conflicts with an existing trip")
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
    db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_owner)
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
        "car_name": user.car_name,
        "fuel_consumption": (user.custom_profile or {}).get("base_city_fuel_rate_l100km"),
        "fuel_price_rub": user.fuel_price_rub if user.fuel_price_rub is not None else 72.40,
        "service_cost_rub": user.service_cost_rub if user.service_cost_rub is not None else 9500.0,
        "current_oil_wear_percent": user.current_oil_wear_percent if user.current_oil_wear_percent is not None else 0.0,
    }


@app.get("/api/v1/users/{user_id}/dashboard", response_model=DashboardResponse)
async def get_user_dashboard(user_id: str, db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_owner)):
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    remaining_oil = None
    # Исправленный порог CPA <= 20.0% (в соответствии с README.md и test_cpa_funnel.py)
    cpa_active = False
    cpa_text = "Пора менять масло. Скидка 15% на рекомендованное масло" if cpa_active else None

    fuel_price = float(getattr(user, "fuel_price_rub", 72.40) or 72.40)
    service_cost = float(getattr(user, "service_cost_rub", 9500.0) or 9500.0)
    total_savings = float(getattr(user, "total_savings_rub", 0.0) or 0.0)
    total_trips = int(getattr(user, "total_trips", 0) or 0)
    total_dist = float(getattr(user, "total_distance_km", 0.0) or 0.0)

    saved_liters = float(getattr(user, "total_fuel_saved_liters", 0.0) or 0.0)
    saved_range_km = float(getattr(user, "range_bank_km", 0.0) or 0.0)

    est_autonomy_days = None

    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_savings = await db.scalar(select(func.coalesce(func.sum(Trip.total_savings_rub), 0.0)).where(Trip.user_id == user_id, Trip.created_at >= month_start))
    return DashboardResponse(
        user_id=user.id,
        car_id=getattr(user, "car_id", "haval_jolion_15t_4wd") or "haval_jolion_15t_4wd",
        fuel_price_rub=fuel_price,
        service_cost_rub=service_cost,
        total_savings_rub=round(total_savings, 2),
        month_savings_rub=round(float(month_savings), 2),
        total_trips=total_trips,
        total_distance_km=round(total_dist, 2),
        oil_remaining_percent=remaining_oil,
        ghost_twin_status="Для сравнения нужна контрольная поездка",
        cpa_recommended=cpa_active,
        cpa_offer_text=cpa_text,
        range_bank_km=round(saved_range_km, 1),
        total_fuel_saved_liters=round(saved_liters, 2),
        autonomy_days_extended=est_autonomy_days
    )


@app.get("/api/v1/cpa/offer/{user_id}")
async def get_cpa_personalized_offer(user_id: str, db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_owner)):
    user = await db.get(User, user_id)
    car_id = user.car_id if user else "haval_jolion_15t_4wd"
    profile = await resolve_car_profile_async(car_id, db, user)

    oil_vol = getattr(profile, "oil_capacity_l", None)
    oil_grade = getattr(getattr(profile, "oil_profile", None), "oil_grade", None)
    oil_brand = getattr(profile, "recommended_oil_brand", None)
    car_name = f"{getattr(profile, 'brand', 'Auto')} {getattr(profile, 'model', car_id)}"
    savings_discount = min(1500.0, max(500.0, round(float(getattr(user, "total_savings_rub", 500.0) or 500.0), 0)))

    return {
        "user_id": user_id,
        "car_id": car_id,
        "car_name": car_name,
        "offer_status": "oem_specification_required" if not (oil_brand and oil_grade and oil_vol) else "catalogue_information_not_model_verified",
        "recommended_oil": oil_brand,
        "oil_viscosity": oil_grade,
        "oil_volume_liters": oil_vol,
        "service_discount_rub": savings_discount,
        "promo_code": f"AUTOXAK-{user_id[:6].upper()}-TO"
    }


@app.post("/api/v1/cpa/claim")
async def claim_cpa_offer(payload: CPAClaimPayload, db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_user)):
    if payload.user_id != owner.id:
        raise HTTPException(403, "Access to another user is forbidden")

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
async def get_user_trips(user_id: str, limit: int = Query(100, ge=1, le=200), db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_owner)):
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
            total_savings_rub=(t.calculation_result or {}).get("cost_savings_rub",t.total_savings_rub),
            oil_wear_percent=(t.calculation_result or {}).get("oil_wear_percent",t.oil_wear_percent)
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
    db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_user)
):
    canonical_id = str(uuid.UUID(session_id)) if _is_uuid(session_id) else str(uuid.uuid5(uuid.NAMESPACE_URL, f"autoxak:{owner.id}:{session_id}"))
    stmt = select(Trip).where(Trip.id == canonical_id, Trip.user_id == owner.id)
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
    all_time: bool = Query(False),
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_owner)
):
    now = datetime.now(timezone.utc)
    if end_date is None:
        end_date = now + timedelta(minutes=5)
    if start_date is None:
        start_date = datetime(1970, 1, 1, tzinfo=timezone.utc) if all_time else end_date - timedelta(days=30)

    start_date = start_date.replace(tzinfo=timezone.utc) if start_date.tzinfo is None else start_date.astimezone(timezone.utc)
    end_date = end_date.replace(tzinfo=timezone.utc) if end_date.tzinfo is None else end_date.astimezone(timezone.utc)
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date не может быть позже end_date")

    start_naive = start_date
    end_naive = end_date

    agg_stmt = select(
        func.count(Trip.id).label("total_trips"),
        func.coalesce(func.sum(Trip.distance_km), 0.0).label("total_dist"),
        func.coalesce(func.sum(Trip.duration_seconds), 0.0).label("total_seconds"),
        func.coalesce(func.sum(Trip.total_savings_rub), 0.0).label("total_savings"),
        func.coalesce(func.sum(Trip.oil_wear_percent), 0.0).label("total_oil_wear"),
        func.coalesce(func.sum(Trip.idle_ratio * Trip.duration_seconds), 0.0).label("idle_seconds"),
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
    avg_idle = float(agg_row.idle_seconds or 0.0) / total_secs if total_secs > 0 else 0.0
    period_fuel_liters = round(float(agg_row.total_liters or 0.0), 2)
    period_range_km = round(float(agg_row.total_range or 0.0), 1)

    total_hours = total_secs / 3600.0
    avg_speed = (total_dist / total_hours) if total_hours > 0 else 0.0

    summary_metrics = TripsPeriodSummaryMetrics(
        total_trips=total_trips,
        total_distance_km=round(total_dist, 2),
        total_duration_hours=round(total_hours, 2),
        total_savings_rub=round(total_savings, 2),
        total_oil_wear_percent=None,
        avg_speed_kmh=round(avg_speed, 1),
        avg_idle_ratio=round(avg_idle, 3),
        total_fuel_saved_liters=period_fuel_liters,
        total_range_bonus_km=period_range_km
    )

    trips_stmt = (
        select(Trip)
        .where(and_(Trip.user_id == user_id, Trip.created_at >= start_naive, Trip.created_at <= end_naive))
        .order_by(Trip.created_at.desc(), Trip.id)
        .offset(offset)
        .limit(limit)
    )
    trips_rows = (await db.execute(trips_stmt)).scalars().all()

    trip_items = []
    for t in trips_rows:
        dur_s = round(float(t.duration_seconds or 0.0), 1)
        idle_r = round(float(t.idle_ratio or 0.0), 3)
        jam_s = float(getattr(t, "traffic_jam_seconds", 0.0) or (dur_s * idle_r))
        h_acc = (t.calculation_result or {}).get("harsh_accel_count",t.harsh_accel_count)
        h_brk = (t.calculation_result or {}).get("harsh_brake_count",t.harsh_brake_count)
        bumps = int(getattr(t, "road_anomalies_count", 0) or 0)

        trip_items.append(
            TripPeriodItem(
                session_id=str(t.id),
                created_at=t.created_at,
                duration_seconds=dur_s,
                distance_km=round(float(t.distance_km or 0.0), 3),
                idle_ratio=idle_r,
                oil_wear_percent=(t.calculation_result or {}).get("oil_wear_percent",t.oil_wear_percent),
                total_savings_rub=(t.calculation_result or {}).get("cost_savings_rub",t.total_savings_rub),
                fuel_saved_liters=(t.calculation_result or {}).get("fuel_saved_liters", t.fuel_saved_liters),
                range_bonus_km=(t.calculation_result or {}).get("range_bonus_km", t.range_bonus_km),
                smooth_score=(t.calculation_result or {}).get("smooth_score",t.smooth_score),
                road_anomalies_count=bumps,
                harsh_accel_count=h_acc,
                harsh_brake_count=h_brk,
                rush_cost_rub=(t.calculation_result or {}).get("rush_cost_rub", t.rush_cost_rub),
                hard_accel_count=h_acc,
                hard_brake_count=h_brk,
                bumps_count=bumps,
                idle_minutes=int(round(jam_s / 60.0)),
                capture_quality=((t.calculation_result or {}).get("metrics") or {}).get("capture_quality"),
                math_status=((t.calculation_result or {}).get("metrics") or {}).get("math_status")
            )
        )

    return PeriodAnalyticsResponse(
        user_id=user_id,
        start_date=start_naive,
        end_date=end_naive,
        summary=summary_metrics,
        trips=trip_items,
        limit=limit,
        offset=offset,
    )


# =====================================================================
# Регистрация устройства и профиль
# =====================================================================

@app.post("/api/v1/auth/handshake", response_model=AuthResponse)
async def auth_handshake(payload: AuthHandshakePayload, db: AsyncSession = Depends(get_db)):
    from app.api.auth import continue_as_guest
    guest = await continue_as_guest(None, db, None)
    return AuthResponse(token=guest.token, user_id=guest.user_id, car_id=guest.car_id,
                        is_new_user=True, is_guest=True, name="Гость")


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
    db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_owner)
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
        if payload.car_name is not None:
            user.car_name = payload.car_name
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
async def verify_experiment_run(payload: VerificationPayload, db: AsyncSession = Depends(get_db),
    owner: User = Depends(require_user)):
    if payload.user_id != owner.id:
        raise HTTPException(403, "Access to another user is forbidden")

    return benchmark_engine.evaluate_experiment(
        autoxak_engine_hours=None,
        obd_rpm=payload.obd_ground_truth['engine_rpm'],
        obd_load=payload.obd_ground_truth['engine_load'],
        obd_temp=payload.obd_ground_truth['oil_temperature'],
        user_savings=[], times_s=[point.t for point in payload.telemetry_stream])
