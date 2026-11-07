import os
import json
import uuid
import hashlib
import secrets
import hmac
import time
from datetime import datetime, timedelta, timezone
import asyncio
import urllib.request
import urllib.parse
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text

from app.core.database import get_db
from app.models.db_models import User, Trip, LoginAttempt
from app.core.security import create_access_token, decode_access_token, SECRET_KEY, hash_password, verify_password
from app.api.dependencies import require_user, bearer
from fastapi.security import HTTPAuthorizationCredentials
from app.models.recordings import TripRecording, RecordingVehicle
from app.models.db_models import CPALead

router = APIRouter(prefix="/api/v1/auth", tags=["Аутентификация (Яндекс ID + Гость)"])

SECRET_SALT = os.getenv("YANDEX_ID_SALT", "").strip() or SECRET_KEY

# Simple in-memory rate limiter for sensitive endpoints (guest creation, vehicle synthesis)
# In production, use Redis-backed limiter. Disabled for test environments via PYTEST_CURRENT_TEST.
_rate_limit_store: Dict[str, tuple[int, float]] = {}  # key -> (count, window_start)


def _check_rate_limit(key: str, max_requests: int = 30, window_seconds: int = 60) -> bool:
    """Check rate limit for a given key. Returns True if allowed, False if limited."""
    # Skip rate limiting in test environments
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    now = time.time()
    count, window_start = _rate_limit_store.get(key, (0, now))
    if now - window_start > window_seconds:
        count, window_start = 0, now
    if count >= max_requests:
        return False
    _rate_limit_store[key] = (count + 1, window_start)
    return True


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


class CredentialsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(min_length=5, max_length=15, pattern=r"^[a-zA-Z]+$")
    password: str = Field(min_length=8, max_length=15)
    guest_id: Optional[str] = Field(default=None, max_length=64)


async def _credential_lock(db, key):
    if db.bind.dialect.name == "postgresql":
        lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})


async def _merge_guest(db, guest, target):
    """Caller locks both rows; transfer once, in the same transaction as login."""
    if guest is None or guest.id == target.id:
        return
    if guest.merged_into == target.id:
        return  # Retry after a lost HTTP response.
    if not guest.is_guest or guest.merged_into:
        raise HTTPException(403, "Guest session proof is required")
    for field in ("total_savings_rub", "total_fuel_saved_liters", "range_bank_km", "total_distance_km", "total_trips", "current_oil_wear_percent"):
        value = (getattr(target, field) or 0) + (getattr(guest, field) or 0)
        setattr(target, field, min(100.0, value) if field == "current_oil_wear_percent" else value)
        setattr(guest, field, 0)
    for table in (Trip, TripRecording, CPALead):
        await db.execute(update(table).where(table.user_id == guest.id).values(user_id=target.id))
    profile = await db.get(RecordingVehicle, guest.id)
    if profile:
        if await db.get(RecordingVehicle, target.id) is None:
            db.add(RecordingVehicle(user_id=target.id, profile=profile.profile))
        await db.delete(profile)
    guest.merged_into = target.id


def _guest_proof(payload, credentials):
    if not payload.guest_id:
        return None
    claims = decode_access_token(credentials.credentials) if credentials and credentials.scheme.lower() == "bearer" else None
    if not claims or claims.get("purpose", "access") != "access" or claims.get("sub") != payload.guest_id:
        raise HTTPException(403, "Guest session proof is required")
    return payload.guest_id


def _session_response(user, new=False):
    return AuthTokenResponse(token=create_access_token(user.id), user_id=user.id,
                             name=user.name or user.login or "Гость", car_id=user.car_id,
                             is_guest=user.is_guest, is_new_user=new)


@router.post("/register", response_model=AuthTokenResponse)
async def register(payload: CredentialsPayload, db: AsyncSession = Depends(get_db),
                   credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    guest_id = _guest_proof(payload, credentials)
    login = payload.login.lower()
    await _credential_lock(db, "login:" + login)
    existing = await db.scalar(select(User).where(User.login == login))
    if existing:
        # Idempotent recovery only with proof of the same guest and password.
        if existing.id == guest_id and await asyncio.to_thread(verify_password, payload.password, existing.hashed_password):
            return _session_response(existing)
        raise HTTPException(409, "Этот логин уже занят")
    guest = await db.scalar(select(User).where(User.id == guest_id).with_for_update()) if guest_id else None
    if guest_id and (not guest or not guest.is_guest or guest.merged_into):
        raise HTTPException(403, "Guest session proof is required")
    user = guest or User(id="account_" + uuid.uuid4().hex, car_id="haval_jolion_15t_4wd")
    user.login = login
    user.name = payload.login
    user.hashed_password = await asyncio.to_thread(hash_password, payload.password)
    user.is_guest = False
    db.add(user)
    await db.commit()
    return _session_response(user, True)


@router.post("/login", response_model=AuthTokenResponse)
async def login(payload: CredentialsPayload, db: AsyncSession = Depends(get_db),
                credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    guest_id = _guest_proof(payload, credentials)
    login_name = payload.login.lower()
    await _credential_lock(db, "login:" + login_name)
    now = datetime.now(timezone.utc)
    key = hashlib.sha256(login_name.encode()).hexdigest()
    attempt = await db.get(LoginAttempt, key)
    if attempt and attempt.blocked_until and attempt.blocked_until > now:
        seconds = max(1, int((attempt.blocked_until - now).total_seconds()) + 1)
        raise HTTPException(429, "Слишком много попыток. Попробуйте позже", headers={"Retry-After": str(seconds)})
    target = await db.scalar(select(User).where(User.login == login_name))
    # Perform the same password work for unknown logins to avoid a timing oracle.
    dummy = "0" * 32 + "$" + "0" * 64
    valid = await asyncio.to_thread(verify_password, payload.password, target.hashed_password if target else dummy)
    if not valid or target is None:
        if attempt is None:
            attempt = LoginAttempt(key=key, failures=0, window_started_at=now)
            db.add(attempt)
        if now - attempt.window_started_at >= timedelta(minutes=5):
            attempt.failures = 0
            attempt.window_started_at = now
        attempt.failures += 1
        if attempt.failures >= 4:
            attempt.blocked_until = now + timedelta(minutes=5)
        await db.commit()  # Failed attempts must survive the error response.
        if attempt.failures >= 4:
            raise HTTPException(429, "Слишком много попыток. Попробуйте позже", headers={"Retry-After": "300"})
        raise HTTPException(401, "Неверный логин или пароль")
    users = (await db.execute(select(User).where(User.id.in_(sorted({target.id, guest_id} - {None})))
                             .order_by(User.id).with_for_update().execution_options(populate_existing=True))).scalars().all()
    by_id = {user.id: user for user in users}
    if guest_id and guest_id not in by_id:
        raise HTTPException(403, "Guest session proof is required")
    target = by_id[target.id]
    await _merge_guest(db, by_id.get(guest_id), target)
    if attempt:
        await db.delete(attempt)
    await db.commit()
    return _session_response(target)


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
    await _merge_guest(db, guest_user, ya_user)
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
    # Rate limit guest session creation
    if not _check_rate_limit("guest_create", max_requests=30, window_seconds=60):
        raise HTTPException(429, "Too many guest sessions created. Please wait a minute.")

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
