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
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.core.database import engine, Base, get_db
from app.core.security import create_access_token
from app.core.dsp.filters import SignalFilter
from app.core.dsp.alignment import IMUAlignmentService
from app.models.telemetry import TripSessionPayload
from app.models.results import TripCalculationResult, TripHistoryResponse, TripSummaryItem
from app.models.db_models import (
    User, Trip, CPALead, VehicleMake, VehicleModel, VehicleTrim, FuelRegionalPrice
)
from app.services.wear_engine import WearEngine
from app.services.twin_engine import TwinEngine
from app.services.gis_service import GISService
from app.services.benchmark_engine import BenchmarkEngine, VerificationReport
from app.services.profile_synthesizer import ProfileSynthesizer, SyntheticCarInput
from app.models.vehicle_profiles import VEHICLE_REGISTRY, VehiclePhysicalProfile, OilProfile

security_scheme = HTTPBearer(auto_error=False)


async def resolve_car_profile_async(car_id: Optional[str], db: AsyncSession) -> Any:
    if not car_id:
        car_id = "haval_jolion_15t_4wd"

    try:
        stmt = (
            select(VehicleTrim)
            .options(selectinload(VehicleTrim.model).selectinload(VehicleModel.make))
            .where(VehicleTrim.id == car_id)
        )
        res = await db.execute(stmt)
        trim = res.scalar_one_or_none()

        if trim:
            brand_name = "Auto"
            if trim.model and trim.model.make:
                brand_name = trim.model.make.name

            return VehiclePhysicalProfile(
                car_id=trim.id,
                brand=brand_name,
                model=trim.badge_name,
                curb_weight_kg=trim.curb_weight_kg,
                rolling_resistance_coeff=trim.rolling_resistance_coeff,
                drag_coefficient_area=trim.drag_coefficient_area,
                drivetrain_efficiency=trim.drivetrain_efficiency,
                engine_displacement_l=trim.engine_displacement_l,
                rated_power_kw=trim.rated_power_kw,
                oil_capacity_l=trim.oil_capacity_l,
                oil_profile=OilProfile(
                    nominal_service_hours=trim.nominal_service_hours,
                    oil_grade=trim.oil_grade
                )
            )
    except Exception:
        pass

    if car_id in VEHICLE_REGISTRY:
        return VEHICLE_REGISTRY[car_id]

    return VEHICLE_REGISTRY.get("haval_jolion_15t") or next(iter(VEHICLE_REGISTRY.values()))


class AuthHandshakePayload(BaseModel):
    device_id: str
    preferred_car_id: Optional[str] = "haval_jolion_15t_4wd"


class AuthResponse(BaseModel):
    token: str
    user_id: str
    car_id: str
    is_new_user: bool


class CPAOfferResponse(BaseModel):
    user_id: str
    car_name: str
    recommended_oil: str
    oil_viscosity: str
    oil_volume_liters: float
    service_discount_rub: float
    promo_code: str
    partner_url: str
    headline: str
    description: str


class CPAClickPayload(BaseModel):
    user_id: str
    promo_code: str
    partner_id: Optional[str] = "autodoc_partner_01"


class DashboardResponse(BaseModel):
    user_id: str
    car_id: str
    car_badge: str
    fuel_price_rub: float
    service_cost_rub: float
    month_savings_rub: float
    oil_remaining_percent: float
    ghost_twin_status: str
    cpa_recommended: bool
    cpa_offer_text: Optional[str]


class UserProfileUpdatePayload(BaseModel):
    user_id: str
    car_id: str
    current_oil_wear_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    fuel_price_rub: float = Field(default=72.40, gt=0.0)
    service_cost_rub: float = Field(default=9500.0, gt=0.0)


class VerificationPayload(BaseModel):
    session_id: str
    user_id: str
    car_id: str
    telemetry_stream: List[dict]
    obd_ground_truth: Dict[str, List[float]]


class FuelPriceResolveResponse(BaseModel):
    region_code: str
    region_name: str
    brand: str
    brand_display_name: str
    recommended_price_rub: float
    fuel_grade: str
    prices: Dict[str, float]
    availability_status: str
    source_label: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="autoXAK Telemetry Engine",
    description="Тяговый баланс ТС, динамические цены топлива и CPA-монетизация",
    version="2.1.0",
    lifespan=lifespan
)

static_candidates = [
    os.path.join(os.path.dirname(__file__), "..", "static"),
    os.path.join(os.path.dirname(__file__), "static"),
    "static",
    "/app/static"
]
STATIC_DIR = next((c for c in static_candidates if os.path.isdir(c)), None)
if STATIC_DIR:
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

benchmark_engine = BenchmarkEngine()
signal_filter = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
imu_alignment = IMUAlignmentService(sample_rate_hz=50.0)
gis_service = GISService()


def _resolve_static(filename: str) -> str:
    for base in [os.path.join(os.path.dirname(__file__), "..", "static"), "static", "/app/static"]:
        p = os.path.join(base, filename)
        if os.path.exists(p):
            return p
    raise HTTPException(status_code=404, detail="File not found")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root():
    return FileResponse(_resolve_static("index.html"))


@app.api_route("/history", methods=["GET", "HEAD"], include_in_schema=False)
async def history_page():
    return FileResponse(_resolve_static("history.html"))


@app.api_route("/setup", methods=["GET", "HEAD"], include_in_schema=False)
async def setup_page():
    return FileResponse(_resolve_static("setup.html"))


@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "2.1.0"}


# ---------------- ДИНАМИЧЕСКИЕ ЦЕНЫ ТОПЛИВА ----------------

@app.get("/api/v1/fuel/current-price", response_model=FuelPriceResolveResponse)
async def get_fuel_price_estimate(
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    region_code: Optional[str] = Query("77"),
    brand: Optional[str] = Query("rosneft"),
    fuel_grade: Optional[str] = Query("ai95"),
    db: AsyncSession = Depends(get_db)
):
    target_region = region_code or "77"
    if lat is not None and lon is not None:
        if 55.0 <= lat <= 56.5 and 36.5 <= lon <= 38.5:
            target_region = "77"  # Москва
        elif 59.5 <= lat <= 60.5 and 29.5 <= lon <= 31.0:
            target_region = "78"  # СПб
        elif 43.5 <= lat <= 46.5 and 37.0 <= lon <= 41.0:
            target_region = "23"  # Краснодар
        elif 56.0 <= lat <= 57.5 and 59.5 <= lon <= 62.0:
            target_region = "66"  # Свердловск

    target_brand = brand or "rosneft"
    entry = None

    try:
        stmt = select(FuelRegionalPrice).where(
            FuelRegionalPrice.region_code == target_region,
            FuelRegionalPrice.brand == target_brand
        )
        res = await db.execute(stmt)
        entry = res.scalar_one_or_none()

        if not entry:
            stmt_fb = select(FuelRegionalPrice).where(
                FuelRegionalPrice.region_code == "77",
                FuelRegionalPrice.brand == "rosneft"
            )
            res_fb = await db.execute(stmt_fb)
            entry = res_fb.scalar_one_or_none()
    except Exception:
        pass

    norm_grade = (fuel_grade or "ai95").lower().replace("-", "")

    if entry:
        prices_map = {
            "ai92": float(entry.price_ai92),
            "ai95": float(entry.price_ai95),
            "ai100": float(entry.price_ai100),
            "dt": float(entry.price_dt)
        }
        reg_code = entry.region_code
        reg_name = entry.region_name
        b_code = entry.brand
        b_name = entry.brand_display_name
        avail = entry.availability_status
        src_label = f"{entry.brand_display_name} ({entry.region_name})"
    else:
        # Гарантированный in-memory fallback при пустой таблице БД
        prices_map = {
            "ai92": 65.20,
            "ai95": 72.40,
            "ai100": 101.50,
            "dt": 80.60
        }
        reg_code = target_region
        reg_name = "Москва" if target_region == "77" else "Регион РФ"
        b_code = target_brand
        b_name = target_brand.capitalize()
        avail = "NORMAL"
        src_label = f"{b_name} ({reg_name})"

    target_price = prices_map.get(norm_grade, prices_map.get("ai95", 72.40))

    return FuelPriceResolveResponse(
        region_code=reg_code,
        region_name=reg_name,
        brand=b_code,
        brand_display_name=b_name,
        recommended_price_rub=round(target_price, 2),
        fuel_grade=norm_grade.upper(),
        prices=prices_map,
        availability_status=avail,
        source_label=src_label
    )


# ---------------- КАТАЛОГ АВТОМОБИЛЕЙ И СИНТЕЗАТОР ----------------

@app.get("/api/v1/vehicles/catalog")
async def get_vehicle_catalog(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(VehicleMake)
        .options(selectinload(VehicleMake.models).selectinload(VehicleModel.trims))
        .order_by(VehicleMake.name)
    )
    res = await db.execute(stmt)
    makes = res.scalars().all()

    catalog = []
    for mk in makes:
        mk_dict = {"id": mk.id, "name": mk.name, "models": []}
        for md in mk.models:
            md_dict = {"id": md.id, "name": md.name, "body_type": md.body_type, "trims": []}
            for tr in md.trims:
                md_dict["trims"].append({
                    "id": tr.id,
                    "badge_name": tr.badge_name,
                    "curb_weight_kg": tr.curb_weight_kg,
                    "power_hp": int(round(tr.rated_power_kw * 1.35962)),
                    "oil_grade": tr.oil_grade,
                    "oil_capacity_l": tr.oil_capacity_l,
                    "recommended_oil_brand": tr.recommended_oil_brand
                })
            mk_dict["models"].append(md_dict)
        catalog.append(mk_dict)

    return catalog


@app.post("/api/v1/vehicles/synthesize")
async def synthesize_car_profile(input_data: SyntheticCarInput, db: AsyncSession = Depends(get_db)):
    profile_dict = ProfileSynthesizer.synthesize_profile(input_data)
    trim_id = profile_dict["car_id"]

    custom_make = await db.get(VehicleMake, "custom")
    if not custom_make:
        custom_make = VehicleMake(id="custom", name="Пользовательский", country="Universal")
        db.add(custom_make)
        await db.flush()

    custom_model_id = f"custom_{input_data.body_type}"
    custom_model = await db.get(VehicleModel, custom_model_id)
    if not custom_model:
        custom_model = VehicleModel(
            id=custom_model_id,
            make_id="custom",
            name=f"{input_data.body_type.capitalize()} (Кастом)",
            body_type=input_data.body_type
        )
        db.add(custom_model)
        await db.flush()

    trim = await db.get(VehicleTrim, trim_id)
    if not trim:
        trim = VehicleTrim(
            id=trim_id,
            model_id=custom_model.id,
            badge_name=f"{input_data.engine_disp_l}L {input_data.transmission} ({input_data.drivetrain})",
            curb_weight_kg=profile_dict["curb_weight_kg"],
            drag_coefficient_area=profile_dict["drag_coefficient_area"],
            rolling_resistance_coeff=profile_dict["rolling_resistance_coeff"],
            drivetrain_efficiency=profile_dict["drivetrain_efficiency"],
            engine_displacement_l=profile_dict["engine_displacement_l"],
            rated_power_kw=profile_dict["rated_power_kw"],
            drivetrain_type=input_data.drivetrain,
            transmission_type=input_data.transmission,
            oil_capacity_l=profile_dict["oil_capacity_l"],
            oil_grade=profile_dict["oil_grade"],
            oil_spec=profile_dict["oil_spec"],
            recommended_oil_brand="LUKOIL GENESIS",
            nominal_service_hours=250.0
        )
        db.add(trim)
        await db.commit()

    return {"status": "SYNTHESIZED", "car_id": trim_id, "profile": profile_dict}


# ---------------- АВТОРИЗАЦИЯ И ДАШБОРД ----------------

@app.post("/api/v1/auth/handshake", response_model=AuthResponse)
async def auth_handshake(payload: AuthHandshakePayload, db: AsyncSession = Depends(get_db)):
    user_id = f"dev_{payload.device_id[:16]}"
    user = await db.get(User, user_id)
    is_new = False

    if not user:
        is_new = True
        user = User(
            id=user_id,
            car_id=payload.preferred_car_id or "haval_jolion_15t_4wd",
            fuel_price_rub=72.40,
            service_cost_rub=9500.0,
            total_savings_rub=0.0,
            current_oil_wear_percent=0.0
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(user_id=user.id)
    return AuthResponse(token=token, user_id=user.id, car_id=user.car_id, is_new_user=is_new)


@app.get("/api/v1/users/{user_id}/dashboard", response_model=DashboardResponse)
async def get_user_dashboard(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    trim = await db.get(VehicleTrim, user.car_id)
    badge = trim.badge_name if trim else user.car_id

    remaining_oil = max(0.0, round(100.0 - user.current_oil_wear_percent, 1))
    cpa_active = remaining_oil <= 20.0
    cpa_text = "Ресурс масла на исходе. Нажмите, чтобы забрать скидку на ТО" if cpa_active else None

    return DashboardResponse(
        user_id=user.id,
        car_id=user.car_id,
        car_badge=badge,
        fuel_price_rub=user.fuel_price_rub,
        service_cost_rub=user.service_cost_rub,
        month_savings_rub=round(user.total_savings_rub, 2),
        oil_remaining_percent=remaining_oil,
        ghost_twin_status="Агрессивный двойник (DCT/Turbo)",
        cpa_recommended=cpa_active,
        cpa_offer_text=cpa_text
    )


@app.get("/api/v1/cpa/offer/{user_id}", response_model=CPAOfferResponse)
async def get_personalized_cpa_offer(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    trim = await db.get(VehicleTrim, user.car_id)
    if trim:
        car_name = trim.badge_name
        rec_oil = trim.recommended_oil_brand
        oil_grade = trim.oil_grade
        oil_volume = trim.oil_capacity_l
    else:
        car_name = "Haval Jolion 1.5T"
        rec_oil = "TotalEnergies Quartz 9000"
        oil_grade = "0W-20"
        oil_volume = 3.8

    savings_discount = float(min(1500.0, max(500.0, round(user.total_savings_rub * 0.3, 0))))
    promo = f"XAK-{user.id[-4:].upper()}-{int(savings_discount)}"

    return CPAOfferResponse(
        user_id=user.id,
        car_name=car_name,
        recommended_oil=rec_oil,
        oil_viscosity=oil_grade,
        oil_volume_liters=oil_volume,
        service_discount_rub=savings_discount,
        promo_code=promo,
        partner_url=f"https://auto-partner.ru/order?promo={promo}",
        headline=f"Рекомендованное ТО для {car_name}",
        description=f"Масло {rec_oil} ({oil_grade}, {oil_volume} л). Скидка из вашей копилки: {int(savings_discount)} ₽."
    )


@app.post("/api/v1/cpa/claim")
async def claim_cpa_offer(payload: CPAClickPayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, payload.user_id)
    car_id = user.car_id if user else "haval_jolion_15t_4wd"

    lead = CPALead(
        id=str(uuid.uuid4()),
        user_id=payload.user_id,
        car_id=car_id,
        partner_id=payload.partner_id or "autodoc_partner_01",
        promo_code=payload.promo_code,
        discount_rub=500.0
    )
    db.add(lead)
    await db.commit()
    return {"status": "LEAD_RECORDED", "promo_code": payload.promo_code}


@app.post("/api/v1/telemetry/session", response_model=TripCalculationResult)
async def process_telemetry_session(payload: TripSessionPayload, db: AsyncSession = Depends(get_db)):
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(status_code=422, detail="Недостаточно точек телеметрии.")

    raw_ax = np.array([p.ax for p in stream], dtype=np.float64)
    raw_ay = np.array([p.ay for p in stream], dtype=np.float64)
    raw_az = np.array([p.az for p in stream], dtype=np.float64)
    speeds = np.array([p.speed for p in stream], dtype=np.float64)

    a_long, a_lat, _, _ = imu_alignment.calibrate_and_transform(raw_ax, raw_ay, raw_az, speeds)
    filt_long = signal_filter.apply_butterworth_lpf(a_long)
    filt_lat = signal_filter.apply_butterworth_lpf(a_lat)

    cornering = 0.08 * (filt_lat ** 2)
    effective_acc = np.where(filt_long >= 0, filt_long + cornering, filt_long)

    user = await db.get(User, payload.user_id)
    if not user:
        user = User(id=payload.user_id, car_id=payload.car_id or "haval_jolion_15t_4wd")
        db.add(user)

    resolved_car_id = payload.car_id or user.car_id
    profile = await resolve_car_profile_async(resolved_car_id, db)
    wear_eng = WearEngine(profile=profile)
    twin_eng = TwinEngine(profile=profile)

    dt = 0.02
    duration_sec = len(stream) * dt
    distance_km = float(np.sum(speeds * dt) / 1000.0)

    wear_stats = wear_eng.evaluate_trip_wear(dt=dt, speed_mps=speeds, ax_mps2=effective_acc)
    equiv_hours = float(wear_stats["equivalent_hours"])
    oil_wear_pct = float(wear_stats["oil_wear_percent"])
    idle_ratio = wear_stats["idle_duration_sec"] / duration_sec if duration_sec > 0 else 0.0

    twin_speed, twin_ax = twin_eng.simulate_aggressive_twin_kinematics(speeds, dt=dt)
    twin_wear = wear_eng.evaluate_trip_wear(dt=dt, speed_mps=twin_speed, ax_mps2=twin_ax)
    twin_equiv_h = float(twin_wear["equivalent_hours"])

    savings = twin_eng.evaluate_financial_delta(
        distance_km=distance_km,
        user_equiv_hours=equiv_hours,
        user_ax=effective_acc,
        twin_equiv_hours=twin_equiv_h,
        fuel_price_rub=user.fuel_price_rub,
        service_cost_rub=user.service_cost_rub
    )

    user.total_savings_rub += savings["total_savings_rub"]
    user.current_oil_wear_percent = min(100.0, user.current_oil_wear_percent + oil_wear_pct)

    actual_id = str(uuid.uuid4())
    new_trip = Trip(
        id=actual_id,
        user_id=payload.user_id,
        car_id=resolved_car_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        equivalent_engine_hours=round(equiv_hours, 5),
        oil_wear_percent=round(oil_wear_pct, 5),
        idle_ratio=round(idle_ratio, 3),
        fuel_saved_rub=round(savings["fuel_savings_rub"], 2),
        oil_saved_rub=round(savings["oil_savings_rub"], 2),
        total_savings_rub=round(savings["total_savings_rub"], 2)
    )
    db.add(new_trip)
    await db.commit()

    return TripCalculationResult(
        session_id=actual_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        oil_wear_percent=round(oil_wear_pct, 5),
        cost_savings_rub=round(savings["total_savings_rub"], 2),
        status="PROCESSED"
    )


@app.post("/api/v1/users/{user_id}/profile")
async def update_user_vehicle_profile(user_id: str, payload: UserProfileUpdatePayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        user = User(
            id=user_id,
            car_id=payload.car_id,
            fuel_price_rub=payload.fuel_price_rub,
            service_cost_rub=payload.service_cost_rub,
            total_savings_rub=0.0,
            current_oil_wear_percent=payload.current_oil_wear_percent
        )
        db.add(user)
    else:
        user.car_id = payload.car_id
        user.fuel_price_rub = payload.fuel_price_rub
        user.service_cost_rub = payload.service_cost_rub
        user.current_oil_wear_percent = payload.current_oil_wear_percent

    await db.commit()
    return {"status": "CONFIG_SAVED", "car_id": user.car_id}


@app.post("/api/v1/analytics/verify-run", response_model=VerificationReport)
async def verify_experiment_run(payload: VerificationPayload, db: AsyncSession = Depends(get_db)):
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(status_code=422, detail="Недостаточный объем телеметрии.")

    raw_ax = np.array([p["ax"] for p in stream], dtype=np.float64)
    raw_ay = np.array([p["ay"] for p in stream], dtype=np.float64)
    raw_az = np.array([p["az"] for p in stream], dtype=np.float64)
    speeds = np.array([p["speed"] for p in stream], dtype=np.float64)

    a_long, a_lat, _, _ = imu_alignment.calibrate_and_transform(raw_ax, raw_ay, raw_az, speeds)
    filt_long = signal_filter.apply_butterworth_lpf(a_long)
    filt_lat = signal_filter.apply_butterworth_lpf(a_lat)

    cornering = 0.08 * (filt_lat ** 2)
    effective_acc = np.where(filt_long >= 0, filt_long + cornering, filt_long)

    profile = await resolve_car_profile_async(payload.car_id, db)
    wear_eng = WearEngine(profile=profile)
    wear_stats = wear_eng.evaluate_trip_wear(dt=0.02, speed_mps=speeds, ax_mps2=effective_acc)
    autoxak_hours = float(wear_stats["equivalent_hours"])

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
