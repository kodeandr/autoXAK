from fastapi import FastAPI
from app.models.telemetry import TripSessionPayload
from app.models.results import TripCalculationResult

app = FastAPI(title="АвтоХАК Telemetry Engine", version="0.1.0")

@app.get("/health")
def health_check():
    return {"status": "ok", "engine": "Wear & Cost Processor active"}

@app.post("/api/v1/telemetry/session", response_model=TripCalculationResult)
def ingest_session(payload: TripSessionPayload):
    # Базовая заглушка: расчет длительности по числу сэмплов при частоте 50 Гц (dt=0.02)
    n_samples = len(payload.telemetry_stream)
    duration = n_samples * 0.02

    # Заглушка расчета: средний пробег и экономия
    distance = sum(p.speed * 0.02 for p in payload.telemetry_stream) / 1000.0

    return TripCalculationResult(
        session_id=payload.session_id,
        duration_seconds=round(duration, 2),
        distance_km=round(distance, 2),
        oil_wear_percent=0.15,
        cost_savings_rub=38.50,
        status="PROCESSED"
    )