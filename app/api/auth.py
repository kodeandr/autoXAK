import os
import json
import uuid
import hashlib
import secrets
import hmac
from datetime import timedelta
import asyncio
import urllib.request
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.core.database import get_db
from app.models.db_models import User, Trip
from app.core.security import create_access_token, decode_access_token, SECRET_KEY
from app.api.dependencies import require_user, bearer
from fastapi.security import HTTPAuthorizationCredentials
from app.models.recordings import TripRecording, RecordingVehicle
from app.models.db_models import CPALead

router = APIRouter(prefix="/api/v1/auth", tags=["Аутентификация (Яндекс ID + Гость)"])

SECRET_SALT = os.getenv("YANDEX_ID_SALT", "").strip() or SECRET_KEY


class GuestPayload(BaseModel):
    guest_id: Optional[str] = Field(default=None, max_length=64)
    car_id: Optional[str] = Field(default="haval_jolion_15t_4wd", max_length=100)


class HandshakePayload(BaseModel):
    device_id: str = Field(..., description="Уникальный технический отпечаток устройства")
    preferred_car_id: Optional[str] = Field(default="haval_jolion_15t_4wd")


class AuthTokenResponse(BaseModel):
    status: str = "ok"
    token: str
    user_id: str
    name: str
    car_id: str
    is_guest: bool
    is_new_user: bool = False


def _build_anonymized_yandex_id(raw_yandex_id: str) -> str:
    digest = hashlib.sha256(f"{SECRET_SALT}:yandex_id:{raw_yandex_id}".encode("utf-8")).hexdigest()[:16]
    return f"ya_{digest}"


def _resolve_redirect_uri(request: Request) -> str:
    custom_uri = os.getenv("YANDEX_REDIRECT_URI", "").strip()
    if custom_uri:
        return custom_uri
    return str(request.url_for("yandex_oauth_callback"))


def _exchange_code_for_yandex_id(code: str, redirect_uri: str) -> str:
    client_id = os.getenv("YANDEX_CLIENT_ID", "").strip()
    client_secret = os.getenv("YANDEX_CLIENT_SECRET", "").strip()

    token_url = "https://oauth.yandex.ru/token"
    token_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }).encode("utf-8")

    req = urllib.request.Request(
        token_url,
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        token_json = json.loads(resp.read().decode("utf-8"))

    access_token = token_json.get("access_token")
    if not access_token:
        raise ValueError("Яндекс не вернул access_token")

    info_req = urllib.request.Request(
        "https://login.yandex.ru/info?format=json",
        headers={"Authorization": f"OAuth {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(info_req, timeout=10) as resp:
        info_json = json.loads(resp.read().decode("utf-8"))

    raw_id = str(info_json.get("psuid") or info_json.get("id") or "")
    if not raw_id:
        raise ValueError("Не удалось получить технический идентификатор от Яндекс ID")
    return raw_id


async def _get_or_merge_yandex_user(
    db: AsyncSession,
    raw_yandex_id: str,
    guest_id: Optional[str] = None,
) -> User:
    ya_user_id = _build_anonymized_yandex_id(raw_yandex_id)
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy import text
        lock_id = int.from_bytes(hashlib.sha256(ya_user_id.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})

    # Serialize merges with trip writes; the same guest can only be transferred once.
    ids = sorted({ya_user_id, guest_id} - {None})
    users = (await db.execute(select(User).where(User.id.in_(ids)).order_by(User.id).with_for_update())).scalars().all()
    by_id = {user.id: user for user in users}
    ya_user = by_id.get(ya_user_id)
    guest_user = by_id.get(guest_id)
    if guest_user is not None and not guest_user.is_guest:
        raise HTTPException(403, "Only an authenticated guest can be merged")
    if ya_user is None:
        ya_user = User(id=ya_user_id, name="Яндекс ID", is_guest=False,
                       car_id=getattr(guest_user, "car_id", None) or "haval_jolion_15t_4wd",
                       car_name=getattr(guest_user, "car_name", None),
                       custom_profile=getattr(guest_user, "custom_profile", None),
                       fuel_price_rub=getattr(guest_user, "fuel_price_rub", None) or 72.40,
                       service_cost_rub=getattr(guest_user, "service_cost_rub", None) or 9500.0)
        db.add(ya_user)
        await db.flush()  # Parent must exist before moving foreign keys.
    if guest_user is not None:
        for field in ("total_savings_rub", "total_fuel_saved_liters", "range_bank_km", "total_distance_km", "total_trips", "current_oil_wear_percent"):
            value = (getattr(ya_user, field) or 0) + (getattr(guest_user, field) or 0)
            setattr(ya_user, field, min(100.0, value) if field == "current_oil_wear_percent" else value)
            setattr(guest_user, field, 0)
        for table in (Trip, TripRecording, CPALead):
            await db.execute(update(table).where(table.user_id == guest_user.id).values(user_id=ya_user_id))
        profile = await db.get(RecordingVehicle, guest_user.id)
        if profile is not None:
            if await db.get(RecordingVehicle, ya_user_id) is None:
                db.add(RecordingVehicle(user_id=ya_user_id, profile=profile.profile))
            await db.delete(profile)
    await db.commit()
    await db.refresh(ya_user)
    return ya_user


def _build_session_bridge_html(user: User, token: str, previous_user_id: Optional[str] = None) -> str:
    payload_js = json.dumps({
        "user_id": user.id,
        "token": token,
        "name": "Яндекс ID",
        "car_id": user.car_id or "haval_jolion_15t_4wd",
        "is_guest": False,
        "previous_user_id": previous_user_id,
    }).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><title>autoXAK — Яндекс ID</title><script src="/static/session.js"></script><script src="/static/offline_store.js"></script></head>
<body style="background:#05070C;color:#FFF;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
    <div style="color:#D2FA58;font-weight:700;">Вход выполнен, загружаем кокпит...</div>
    <script>
    (async function() {{
        const data = {payload_js};
        if (data.previous_user_id && AutoXAKSession.getUserId() === data.previous_user_id) {{
            await AutoXAKOfflineStore.reassignOwner(data.previous_user_id, data.user_id);
        }}
        AutoXAKSession.adoptSession(data);
        localStorage.setItem('autoXAK_user_id', data.user_id);
        localStorage.setItem('autoXAK_jwt_token', data.token);
        localStorage.setItem('autoXAK_is_guest', 'false');
        localStorage.setItem('autoXAK_user_name', data.name);
        localStorage.setItem('autoXAK_welcome_seen', 'true');
        window.location.replace('/');
    }})().catch(error => {{ console.error(error); window.alert('Не удалось перенести офлайн-поездки. Повторите вход.'); window.location.replace('/login'); }});
    </script>
</body>
</html>"""


@router.post("/guest", response_model=AuthTokenResponse)
async def continue_as_guest(
    payload: Optional[GuestPayload] = None,
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
):
    requested_id = payload.guest_id if payload and payload.guest_id else None
    car_id = (payload.car_id if payload and payload.car_id else None) or "haval_jolion_15t_4wd"

    if requested_id and requested_id.startswith(("guest_", "dev_")):
        stmt = select(User).where(User.id == requested_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            owner = await require_user(credentials, db)
            if owner.id != requested_id or not owner.is_guest:
                raise HTTPException(403, "Guest session proof is required")
            token = create_access_token(user_id=existing.id)
            return AuthTokenResponse(
                token=token,
                user_id=existing.id,
                name="Гость",
                car_id=existing.car_id or car_id,
                is_guest=True,
                is_new_user=False,
            )

    guest_id = f"guest_{uuid.uuid4().hex}"
    guest_user = User(
        id=guest_id,
        email=None,
        name="Гость",
        hashed_password=None,
        is_guest=True,
        car_id=car_id,
        fuel_price_rub=72.40,
        service_cost_rub=9500.0,
        total_savings_rub=0.0,
        current_oil_wear_percent=0.0,
    )
    db.add(guest_user)
    await db.commit()
    await db.refresh(guest_user)

    token = create_access_token(user_id=guest_user.id)
    return AuthTokenResponse(
        token=token,
        user_id=guest_user.id,
        name="Гость",
        car_id=guest_user.car_id,
        is_guest=True,
        is_new_user=True,
    )


OAUTH_COOKIE = "autoxak_oauth_state"


def _oauth_claims(request: Request, state: Optional[str]):
    claims = decode_access_token(request.cookies.get(OAUTH_COOKIE, ""))
    if not claims or claims.get("purpose") != "oauth_state" or not state or not hmac.compare_digest(state, claims["sub"]):
        raise HTTPException(400, "Invalid or expired OAuth state")
    return claims


@router.get("/yandex/login")
@router.post("/yandex/login")
async def yandex_oauth_login(request: Request, db: AsyncSession = Depends(get_db),
                             credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    guest_id = None
    if credentials is not None:
        user = await require_user(credentials, db)
        guest_id = user.id if user.is_guest else None
    state_val = secrets.token_urlsafe(32)
    signed_state = create_access_token(state_val, {"purpose": "oauth_state", "guest_id": guest_id}, expires_delta=timedelta(minutes=10))
    client_id = os.getenv("YANDEX_CLIENT_ID", "").strip()
    if not client_id and os.getenv("ENABLE_YANDEX_DEMO", "false").lower() != "true":
        raise HTTPException(503, "Yandex OAuth is not configured")
    if not client_id:
        demo_url = f"/api/v1/auth/yandex/demo?state={urllib.parse.quote(state_val)}"
        html = f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Яндекс ID</title></head>
<body style="background:#05070C;color:#FFF;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:20px;">
    <div style="max-width:360px;width:100%;background:#121417;border:1px solid rgba(255,255,255,0.12);border-radius:24px;padding:26px;text-align:center;">
        <div style="width:48px;height:48px;background:#FC3F1D;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-weight:900;font-size:22px;color:#FFF;margin-bottom:14px;">Я</div>
        <h2 style="margin:0 0 8px;font-size:18px;">Яндекс ID (Демо-вход)</h2>
        <p style="font-size:13px;color:#9CA3AF;line-height:1.45;margin-bottom:18px;">
            Ключ <code>YANDEX_CLIENT_ID</code> не найден в окружении контейнера. Вы можете войти в обезличенный профиль прямо сейчас:
        </p>
        <a href="{demo_url}" style="display:block;width:100%;padding:14px;background:#FC3F1D;color:#FFF;text-decoration:none;border-radius:14px;font-weight:700;font-size:14px;box-sizing:border-box;margin-bottom:10px;">
            Подключить обезличенный Яндекс ID
        </a>
        <a href="/" style="display:block;width:100%;padding:10px;color:#9CA3AF;text-decoration:none;font-size:13px;">Назад</a>
    </div>
</body>
</html>"""
        response = JSONResponse({"url": demo_url}) if request.method == "POST" else HTMLResponse(content=html)
        response.set_cookie(OAUTH_COOKIE, signed_state, max_age=600, httponly=True, secure=request.url.scheme == "https", samesite="lax")
        return response

    redirect_uri = _resolve_redirect_uri(request)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state_val,
    }
    url = f"https://oauth.yandex.ru/authorize?{urllib.parse.urlencode(params)}"
    response = JSONResponse({"url": url}) if request.method == "POST" else RedirectResponse(url=url)
    response.set_cookie(OAUTH_COOKIE, signed_state, max_age=600, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax")
    return response


@router.get("/yandex/demo", response_class=HTMLResponse)
async def yandex_oauth_demo(request: Request, state: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    if os.getenv("ENABLE_YANDEX_DEMO", "false").lower() != "true":
        raise HTTPException(404, "Demo login is disabled")
    claims = _oauth_claims(request, state)
    user = await _get_or_merge_yandex_user(db, raw_yandex_id="demo_" + claims["sub"], guest_id=claims.get("guest_id"))
    response = HTMLResponse(content=_build_session_bridge_html(user, create_access_token(user.id), claims.get("guest_id")))
    response.delete_cookie(OAUTH_COOKIE)
    return response


@router.get("/yandex/callback", response_class=HTMLResponse)
async def yandex_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    claims = _oauth_claims(request, state)
    if not code:
        return RedirectResponse(url="/?auth_error=yandex_cancelled")

    try:
        redirect_uri = _resolve_redirect_uri(request)
        raw_yandex_id = await asyncio.to_thread(_exchange_code_for_yandex_id, code, redirect_uri)
        user = await _get_or_merge_yandex_user(db, raw_yandex_id=raw_yandex_id, guest_id=claims.get("guest_id"))
        token = create_access_token(user_id=user.id)
    except Exception:
        await db.rollback()
        response = RedirectResponse(url="/?auth_error=yandex_failed")
        response.delete_cookie(OAUTH_COOKIE)
        return response

    response = HTMLResponse(content=_build_session_bridge_html(user, token, claims.get("guest_id")))
    response.delete_cookie(OAUTH_COOKIE)
    return response


@router.get("/me", response_model=AuthTokenResponse)
async def get_current_user_profile(user: User = Depends(require_user)):
    return AuthTokenResponse(token=create_access_token(user.id), user_id=user.id,
                             name=user.name or ("Гость" if user.is_guest else "Яндекс ID"),
                             car_id=user.car_id or "haval_jolion_15t_4wd", is_guest=bool(user.is_guest))
