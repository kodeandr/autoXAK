from fastapi import FastAPI, HTTPException
import numpy as np

from app.models.telemetry import TripSessionPayload
from app.models.results import TripCalculationResult
from app.core.dsp.filters import SignalFilter
from app.services.wear_engine import WearEngine
from app.services.twin_engine import AggressiveTwinEngine

app = FastAPI(
    title="АвтоХАК Telemetry Engine",
    description="Пайплайн цифровой фильтрации и расчета эксплуатационных издержек",
    version="1.0.0"
)

signal_filter = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
wear_engine = WearEngine(ambient_temp_c=20.0)
twin_engine = AggressiveTwinEngine()

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "AutoHACK Wear & Cost Processor",
        "version": "1.0.0"
    }

@app.post("/api/v1/telemetry/session", response_model=TripCalculationResult)
async def process_telemetry_session(payload: TripSessionPayload):
    stream = payload.telemetry_stream
    if not stream or len(stream) < 50:
        raise HTTPException(
            status_code=422, 
            detail="Недостаточно точек телеметрии для валидации (минимум 1 секунда / 50 точек)."
        )

    # 1. Извлечение векторов
    raw_ax = [p.ax for p in stream]
    raw_ay = [p.ay for p in stream]
    raw_az = [p.az for p in stream]
    speeds = np.array([p.speed for p in stream], dtype=np.float64)

    # 2. Цифровая фильтрация (DSP)
    filt_x, filt_y, _ = signal_filter.isolate_linear_acceleration(raw_ax, raw_ay, raw_az)
    horiz_acc = signal_filter.calculate_horizontal_acceleration(filt_x, filt_y)

    # 3. Расчет деградации масла
    wear_stats = wear_engine.compute_oil_wear(speeds_mps=speeds, horizontal_acc=horiz_acc, dt=0.02)
    
    # 4. Расчет пробега и базовых характеристик
    distance_meters = np.sum(speeds * 0.02)
    distance_km = float(distance_meters / 1000.0)
    duration_sec = len(stream) * 0.02

    # 5. Сравнение с агрессивным двойником
    savings = twin_engine.simulate_twin_and_delta(
        speeds_mps=speeds,
        user_wear_percent=wear_stats["oil_wear_percent"],
        dt=0.02
    )

    return TripCalculationResult(
        session_id=payload.session_id,
        duration_seconds=round(duration_sec, 2),
        distance_km=round(distance_km, 2),
        oil_wear_percent=wear_stats["oil_wear_percent"],
        cost_savings_rub=savings["total_savings_rub"],
        status="PROCESSED"
    )