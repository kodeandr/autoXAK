"""Yandex authorization-code login with PKCE and single-use browser-bound state.

Provider contract: https://yandex.ru/dev/id/doc/ru/codes/code-url and
https://yandex.ru/dev/id/doc/ru/user-information . No account/recording merge.
"""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import Column, Integer, String, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_user
from app.core.database import Base, get_db
from app.core.security import SECRET_KEY, create_access_token
from app.models.db_models import User


router = APIRouter(prefix="/yandex", tags=["Yandex ID"])
STATE_TTL_SECONDS = 600
NONCE_COOKIE = "autoxak_yandex_nonce"
COOKIE_PATH = "/api/v1/auth/yandex"
logger = logging.getLogger(__name__)


class OAuthLoginAttempt(Base):
    """Only ephemeral nonces/PKCE verifiers, not provider tokens or personal data."""
    __tablename__ = "oauth_login_attempts"

    nonce_hash = Column(String(64), primary_key=True)
    code_verifier = Column(String(128), nullable=False)
    expires_at = Column(Integer, nullable=False, index=True)


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def secure_cookie(self):
        return urlsplit(self.redirect_uri).scheme == "https"

    @property
    def binding(self):
        return hashlib.sha256(f"{self.client_id}\n{self.redirect_uri}".encode()).hexdigest()


def _config():
    config = OAuthConfig(*(os.getenv(name, "").strip() for name in
                           ("YANDEX_CLIENT_ID", "YANDEX_CLIENT_SECRET", "YANDEX_REDIRECT_URI")))
    if not all((config.client_id, config.client_secret, config.redirect_uri)):
        raise HTTPException(503, "Вход через Яндекс не настроен на сервере")
    # Stable salt is required to keep the same ya_ user across processes/restarts.
    configured_secret = os.getenv("SECRET_KEY", "")
    if len(configured_secret) < 32 or not hmac.compare_digest(configured_secret.encode(), SECRET_KEY.encode()):
        raise HTTPException(503, "Для входа через Яндекс нужен постоянный SECRET_KEY сервера")
    try:
        uri = urlsplit(config.redirect_uri)
        valid = (bool(uri.hostname) and not uri.username and not uri.password
                 and not uri.query and not uri.fragment
                 and uri.path == "/api/v1/auth/yandex/callback"
                 and (uri.scheme == "https" or (uri.scheme == "http" and uri.hostname in
                      ("localhost", "127.0.0.1", "::1"))))
        _ = uri.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "YANDEX_REDIRECT_URI должен указывать на настроенный HTTPS callback")
    return config


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sign_state(nonce, expires_at, config):
    body = _b64(json.dumps({"nonce": nonce, "exp": expires_at, "binding": config.binding},
                           separators=(",", ":")).encode())
    signature = hmac.new(SECRET_KEY.encode(), b"autoxak-yandex-state-v1:" + body.encode(), hashlib.sha256).digest()
    return body + "." + _b64(signature)


def _validate_state(state, cookie, config):
    """State is purpose-separated from access JWTs and bound to this browser."""
    if not isinstance(state, str) or not isinstance(cookie, str) or len(state) > 1024:
        return None
    try:
        body, signature = state.split(".")
        expected = hmac.new(SECRET_KEY.encode(), b"autoxak-yandex-state-v1:" + body.encode(), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(actual, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        nonce, expiry = payload["nonce"], payload["exp"]
        if (not isinstance(nonce, str) or not 32 <= len(nonce) <= 128
                or type(expiry) is not int or not time.time() < expiry <= time.time() + STATE_TTL_SECONDS + 1
                or payload["binding"] != config.binding or not hmac.compare_digest(nonce, cookie)):
            return None
        return nonce
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def _set_nonce(response, nonce, config):
    response.set_cookie(NONCE_COOKIE, nonce, max_age=STATE_TTL_SECONDS, httponly=True,
                        secure=config.secure_cookie, samesite="lax", path=COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _clear_nonce(response, config):
    response.delete_cookie(NONCE_COOKIE, path=COOKIE_PATH, httponly=True,
                           secure=config.secure_cookie, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def _new_login(db, config):
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    now = int(time.time())
    await db.execute(delete(OAuthLoginAttempt).where(OAuthLoginAttempt.expires_at <= now))
    db.add(OAuthLoginAttempt(nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                            code_verifier=verifier, expires_at=now + STATE_TTL_SECONDS))
    await db.commit()
    query = urlencode({"response_type": "code", "client_id": config.client_id,
                       "redirect_uri": config.redirect_uri, "force_confirm": "yes",
                       "state": _sign_state(nonce, now + STATE_TTL_SECONDS, config),
                       "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
                       "code_challenge_method": "S256"})
    return "https://oauth.yandex.ru/authorize?" + query, nonce


@router.post("/start")
async def start_yandex_login(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    config = _config()
    url, nonce = await _new_login(db, config)
    return _set_nonce(JSONResponse({"authorization_url": url}), nonce, config)


@router.get("/login")
async def yandex_login(request: Request, db: AsyncSession = Depends(get_db)):
    # Login/relogin does not accept somebody's user ID as authorization evidence.
    if request.query_params:
        raise HTTPException(400, "Вход через Яндекс не принимает идентификаторы пользователей")
    config = _config()
    url, nonce = await _new_login(db, config)
    return _set_nonce(RedirectResponse(url, status_code=303), nonce, config)


async def _exchange_code_for_yandex_id(code, config, verifier):
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.post("https://oauth.yandex.ru/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": config.client_id, "client_secret": config.client_secret,
            "code_verifier": verifier,
        })
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Missing provider token")
        info = await client.get("https://login.yandex.ru/info", params={"format": "json"},
                                headers={"Authorization": "OAuth " + token})
        info.raise_for_status()
        data = info.json()
        raw_id = data.get("psuid") or data.get("id")
        if not isinstance(raw_id, (str, int)) or isinstance(raw_id, bool) or not str(raw_id).strip():
            raise ValueError("Missing verified provider identity")
        return str(raw_id)


def _build_anonymized_yandex_id(raw_yandex_id):
    # Preserve upstream account IDs when the operator keeps the same SECRET_KEY.
    digest = hashlib.sha256(f"{SECRET_KEY}:yandex_id:{raw_yandex_id}".encode()).hexdigest()[:16]
    return "ya_" + digest


async def _get_yandex_user(db, raw_yandex_id):
    user_id = _build_anonymized_yandex_id(raw_yandex_id)
    user = await db.get(User, user_id)
    if user is None:
        user = User(id=user_id, name="Яндекс ID", is_guest=False, car_id="unconfigured")
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            user = await db.get(User, user_id)
            if user is None:
                raise
    return user


def _session_bridge(user, token):
    payload = json.dumps({"user_id": user.id, "token": token, "name": "Яндекс ID",
                          "is_guest": False, "auth_provider": "yandex"}, ensure_ascii=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    script_nonce = secrets.token_urlsafe(24)
    html = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>АвтоХАК — Яндекс ID</title>
<link rel="stylesheet" href="/static/app.css"></head><body><main>
<h1>Вход через Яндекс</h1><p id="auth-result">Проверяем локальные поездки и открываем профиль…</p>
<a href="/">Вернуться в АвтоХАК</a></main>
<script src="/static/session.js"></script><script src="/static/offline_store.js"></script>
<script nonce="{script_nonce}">(async () => {{
try {{
  await AutoXAKSession.acceptOAuthSession({payload});
  window.location.replace('/');
}} catch (error) {{
  document.getElementById('auth-result').textContent = error.message || 'Не удалось переключить профиль. Ваши поездки сохранены.';
}}
}})();</script></body></html>'''
    return HTMLResponse(html, headers={
        "Content-Security-Policy": "default-src 'none'; script-src 'self' 'nonce-" + script_nonce
            + "'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/callback", response_class=HTMLResponse)
async def yandex_callback(request: Request, db: AsyncSession = Depends(get_db)):
    config = _config()
    nonce = _validate_state(request.query_params.get("state"), request.cookies.get(NONCE_COOKIE), config)
    if nonce is None:
        return _clear_nonce(JSONResponse({"detail": "Запрос входа устарел или не принадлежит этому браузеру"}, status_code=400), config)
    # Atomic consumption protects against concurrent callbacks and replay in any worker.
    verifier = (await db.execute(delete(OAuthLoginAttempt).where(
        OAuthLoginAttempt.nonce_hash == hashlib.sha256(nonce.encode()).hexdigest(),
        OAuthLoginAttempt.expires_at > int(time.time()),
    ).returning(OAuthLoginAttempt.code_verifier))).scalar_one_or_none()
    await db.commit()
    if verifier is None:
        return _clear_nonce(JSONResponse({"detail": "Запрос входа уже использован или истёк"}, status_code=400), config)
    code = request.query_params.get("code")
    if request.query_params.get("error") or not code or len(code) > 4096:
        return _clear_nonce(JSONResponse({"detail": "Вход через Яндекс отменён. Повторите вход при необходимости."}, status_code=400), config)
    try:
        raw_id = await _exchange_code_for_yandex_id(code, config, verifier)
        user = await _get_yandex_user(db, raw_id)
        response = _session_bridge(user, create_access_token(user.id))
    except Exception as error:
        # Provider errors may contain a secret/token. Never reflect or log their text.
        logger.warning("Yandex sign-in failed (%s)", type(error).__name__)
        response = JSONResponse({"detail": "Яндекс не подтвердил вход. Повторите попытку позже."}, status_code=502)
    return _clear_nonce(response, config)
