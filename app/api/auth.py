import os
import json
import uuid
import hashlib
import asyncio
import urllib.request
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.core.database import get_db
from app.models.db_models import User, Trip
from app.core.security import create_access_token, decode_access_token

router = APIRouter(prefix="/api/v1/auth", tags=["Аутентификация (Яндекс ID + Гость)"])

SECRET_SALT = os.getenv("SECRET_KEY", "autoxak_super_secret_jwt_key_mipt_2026")


class GuestPayload(BaseModel):
    guest_id: Optional[str] = None
    car_id: Optional[str] = "haval_jolion_15t_4wd"


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
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8000"))
    return f"{proto}://{host}/api/v1/auth/yandex/callback"


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

    stmt = select(User).where(User.id == ya_user_id)
    ya_user = (await db.execute(stmt)).scalar_one_or_none()

    guest_user = None
    if guest_id and guest_id != ya_user_id and guest_id.startswith(("guest_", "dev_")):
        g_stmt = select(User).where(User.id == guest_id)
        guest_user = (await db.execute(g_stmt)).scalar_one_or_none()

    if not ya_user:
        ya_user = User(
            id=ya_user_id,
            email=None,
            name="Яндекс ID",
            hashed_password=None,
            is_guest=False,
            car_id=getattr(guest_user, "car_id", None) or "haval_jolion_15t_4wd",
            fuel_price_rub=getattr(guest_user, "fuel_price_rub", None) or 72.40,
            service_cost_rub=getattr(guest_user, "service_cost_rub", None) or 9500.0,
            total_savings_rub=getattr(guest_user, "total_savings_rub", 0.0) or 0.0,
            current_oil_wear_percent=getattr(guest_user, "current_oil_wear_percent", 0.0) or 0.0,
        )
        for field in ("total_fuel_saved_liters", "range_bank_km", "total_distance_km", "total_trips"):
            if hasattr(ya_user, field) and guest_user is not None:
                setattr(ya_user, field, getattr(guest_user, field, 0.0) or 0)
        db.add(ya_user)
    else:
        if guest_user and (getattr(guest_user, "total_savings_rub", 0.0) or 0.0) > 0:
            ya_user.total_savings_rub = (ya_user.total_savings_rub or 0.0) + (guest_user.total_savings_rub or 0.0)
            guest_user.total_savings_rub = 0.0

    if guest_user:
        await db.execute(
            update(Trip).where(Trip.user_id == guest_user.id).values(user_id=ya_user_id)
        )

    await db.commit()
    await db.refresh(ya_user)
    return ya_user


def _build_session_bridge_html(user: User, token: str) -> str:
    payload_js = json.dumps({
        "user_id": user.id,
        "token": token,
        "name": "Яндекс ID",
        "car_id": user.car_id or "haval_jolion_15t_4wd",
        "is_guest": False,
    })
    return f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><title>autoXAK — Яндекс ID</title></head>
<body style="background:#05070C;color:#FFF;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
    <div style="color:#D2FA58;font-weight:700;">Вход выполнен, загружаем кокпит...</div>
    <script>
    (function() {{
        const data = {payload_js};
        localStorage.setItem('autoXAK_user_id', data.user_id);
        localStorage.setItem('autoXAK_jwt_token', data.token);
        localStorage.setItem('autoXAK_is_guest', 'false');
        localStorage.setItem('autoXAK_user_name', data.name);
        localStorage.setItem('autoXAK_welcome_seen', 'true');
        window.location.replace('/');
    }})();
    </script>
</body>
</html>"""


@router.post("/guest", response_model=AuthTokenResponse)
async def continue_as_guest(
    payload: Optional[GuestPayload] = None,
    db: AsyncSession = Depends(get_db),
):
    requested_id = payload.guest_id if payload and payload.guest_id else None
    car_id = (payload.car_id if payload and payload.car_id else None) or "haval_jolion_15t_4wd"

    if requested_id and requested_id.startswith(("guest_", "dev_")):
        stmt = select(User).where(User.id == requested_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            token = create_access_token(user_id=existing.id)
            return AuthTokenResponse(
                token=token,
                user_id=existing.id,
                name="Гость",
                car_id=existing.car_id or car_id,
                is_guest=True,
                is_new_user=False,
            )

    guest_id = f"guest_{uuid.uuid4().hex[:10]}"
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


@router.get("/yandex/login")
async def yandex_oauth_login(request: Request, guest_id: Optional[str] = None):
    state_val = (guest_id or "").strip()
    client_id = os.getenv("YANDEX_CLIENT_ID", "").strip()

    if not client_id:
        demo_url = f"/api/v1/auth/yandex/demo?guest_id={urllib.parse.quote(state_val)}"
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
        return HTMLResponse(content=html)

    redirect_uri = _resolve_redirect_uri(request)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state_val,
    }
    url = f"https://oauth.yandex.ru/authorize?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url)


@router.get("/yandex/demo", response_class=HTMLResponse)
async def yandex_oauth_demo(
    guest_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    seed_source = guest_id if guest_id else "demo_driver_mipt"
    user = await _get_or_merge_yandex_user(db, raw_yandex_id=seed_source, guest_id=guest_id)
    token = create_access_token(user_id=user.id)
    return HTMLResponse(content=_build_session_bridge_html(user, token))


@router.get("/yandex/callback", response_class=HTMLResponse)
async def yandex_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    if not code:
        return RedirectResponse(url="/?auth_error=yandex_cancelled")

    try:
        redirect_uri = _resolve_redirect_uri(request)
        raw_yandex_id = await asyncio.to_thread(_exchange_code_for_yandex_id, code, redirect_uri)
        user = await _get_or_merge_yandex_user(db, raw_yandex_id=raw_yandex_id, guest_id=state)
        token = create_access_token(user_id=user.id)
    except Exception as exc:
        err_msg = urllib.parse.quote(str(exc))
        return RedirectResponse(url=f"/?auth_error={err_msg}")

    return HTMLResponse(content=_build_session_bridge_html(user, token))


@router.get("/me", response_model=AuthTokenResponse)
async def get_current_user_profile(
    user_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    resolved_id = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization.split(" ", 1)[1].strip()
        decoded = decode_access_token(raw_token)
        if decoded:
            resolved_id = decoded if isinstance(decoded, str) else decoded.get("sub")

    if not resolved_id:
        resolved_id = user_id

    if not resolved_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия не найдена")

    stmt = select(User).where(User.id == resolved_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Профиль не найден")

    token = create_access_token(user_id=user.id)
    return AuthTokenResponse(
        token=token,
        user_id=user.id,
        name=user.name or ("Яндекс ID" if not user.is_guest else "Гость"),
        car_id=user.car_id or "haval_jolion_15t_4wd",
        is_guest=bool(user.is_guest),
        is_new_user=False,
    )
