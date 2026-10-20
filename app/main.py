"""AutoXAK recording and gasoline prediction API.

The previous oil/savings demonstration is not an active calculation path.
Version 2 stores raw trips and reports research fuel predictions with coverage.
"""
import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import router as auth_router
from app.api.dependencies import require_user
from app.api.recordings import router as recordings_router
from app.core.database import Base, engine, get_db
from app.core.security import create_access_token
from app.models.db_models import User, VehicleMake, VehicleModel, VehicleTrim
from app.models import recordings as recording_tables  # register additive tables

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="АвтоХАК — запись поездок и расход бензина", version="2.0.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(recordings_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def prevent_private_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def home():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.api_route("/setup", methods=["GET", "HEAD"], include_in_schema=False)
async def setup():
    return FileResponse(STATIC_DIR / "setup.html", headers={"Cache-Control": "no-cache"})


@app.api_route("/history", methods=["GET", "HEAD"], include_in_schema=False)
async def history():
    return FileResponse(STATIC_DIR / "history.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "AutoXAK gasoline recording", "version": "2.0.0",
            "savings_status": "unavailable_until_twin_validation"}


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


class AuthHandshakePayload(BaseModel):
    # The opaque device identifier is a guest credential, not a public user ID.
    device_id: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    preferred_car_id: Optional[str] = Field(default=None, max_length=100)


@app.post("/api/v1/auth/handshake")
async def handshake(payload: AuthHandshakePayload, db: AsyncSession = Depends(get_db)):
    user_id = "dev_" + hashlib.sha256(payload.device_id.encode()).hexdigest()[:40]
    user = await db.get(User, user_id)
    is_new = user is None
    if user is None:
        user = User(id=user_id, is_guest=True, name="Гость", car_id=payload.preferred_car_id or "unconfigured")
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            user = await db.get(User, user_id)
            if user is None:
                raise
            is_new = False
    return {"token": create_access_token(user.id), "user_id": user.id, "car_id": user.car_id,
            "is_new_user": is_new}


@app.get("/api/v1/users/me")
async def me(user: User = Depends(require_user)):
    return {"user_id": user.id, "name": user.name, "is_guest": user.is_guest, "car_id": user.car_id}


@app.get("/api/v1/vehicles/catalog")
async def catalog(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(VehicleTrim, VehicleModel, VehicleMake)
            .join(VehicleModel, VehicleTrim.model_id == VehicleModel.id)
            .join(VehicleMake, VehicleModel.make_id == VehicleMake.id)
            .order_by(VehicleMake.name, VehicleModel.name))).all()
    entries = [{"id": trim.id, "make": make.name, "model": model.name, "trim": trim.badge_name,
                "full_name": f"{make.name} {model.name} {trim.badge_name}",
                "mass_kg": trim.curb_weight_kg, "displacement_l": trim.engine_displacement_l,
                "transmission": trim.transmission_type, "fuel_type": "unknown", "turbo": None}
               for trim, model, make in rows]
    return {"catalog": entries, "total": len(entries)}


# Old clients must not write synthetic savings or mix them with research predictions.
@app.api_route("/api/v1/telemetry/session", methods=["POST"], deprecated=True)
@app.api_route("/api/v1/trips/complete", methods=["POST"], deprecated=True)
@app.api_route("/api/v1/analytics/verify-run", methods=["POST"], deprecated=True)
@app.api_route("/api/v1/users/{user_id}/{legacy_path:path}", methods=["GET", "POST"], deprecated=True)
async def retired_calculation(user_id: str = "", legacy_path: str = ""):
    return JSONResponse(status_code=410, content={"status": "legacy_endpoint_retired",
        "detail": "Обновите приложение: запись поездок и оценка бензина доступны через /api/v2.",
        "savings_status": "unavailable_until_twin_validation"})
