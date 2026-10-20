"""OAuth boundaries with mocked provider, real SQL and no external requests."""
import asyncio
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import router
from app.api import yandex_auth as oauth
from app.core.database import Base, get_db
from app.core.security import SECRET_KEY, create_access_token
from app.models.db_models import User
from app.models.recordings import TripRecording


@pytest_asyncio.fixture
async def oauth_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("YANDEX_CLIENT_ID", "configured-client")
    monkeypatch.setenv("YANDEX_CLIENT_SECRET", "configured-secret")
    monkeypatch.setenv("YANDEX_REDIRECT_URI", "https://testserver/api/v1/auth/yandex/callback")
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "oauth.sqlite"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        db.add_all([User(id="guest_owner", name="Гость", is_guest=True, total_savings_rub=999),
                    User(id="guest_other", name="Другой", is_guest=True)])
        await db.commit()
        db.add(TripRecording(id="guest-trip", user_id="guest_owner", started_at_ms=1800000000000,
                             vehicle={"vehicle_id": "own-car"}, device={}, create_hash="a" * 64))
        await db.commit()
    app = FastAPI()
    app.include_router(router)

    async def database():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    calls = []

    async def exchange(code, config, verifier):
        calls.append((code, config, verifier))
        return "verified-provider-user"

    monkeypatch.setattr(oauth, "_exchange_code_for_yandex_id", exchange)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
        yield client, sessions, calls
    await engine.dispose()


def bearer(user="guest_owner", expired=False):
    token = create_access_token(user, expires_delta=timedelta(seconds=-10) if expired else None)
    return {"Authorization": "Bearer " + token}


async def begin(client):
    response = await client.post("/api/v1/auth/yandex/start", headers=bearer())
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return query["state"][0], client.cookies.get(oauth.NONCE_COOKIE), query


async def callback(client, state, cookie=None, **params):
    headers = {"Cookie": f"{oauth.NONCE_COOKIE}={cookie}"} if cookie else {}
    return await client.get("/api/v1/auth/yandex/callback", params={"state": state, "code": "provider-code", **params}, headers=headers)


async def test_authenticated_start_cookie_and_pkce(oauth_app):
    client, sessions, calls = oauth_app
    assert (await client.post("/api/v1/auth/yandex/start")).status_code == 401
    state, cookie, query = await begin(client)
    assert query["redirect_uri"] == ["https://testserver/api/v1/auth/yandex/callback"]
    assert query["force_confirm"] == ["yes"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(state) <= 1024
    async with sessions() as db:
        row = (await db.execute(select(oauth.OAuthLoginAttempt))).scalar_one()
        assert row.nonce_hash == hashlib.sha256(cookie.encode()).hexdigest()
        assert query["code_challenge"][0] == oauth._b64(hashlib.sha256(row.code_verifier.encode()).digest())
    response = await client.post("/api/v1/auth/yandex/start", headers=bearer())
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert not calls


async def test_successful_callback_preserves_guest_trip_and_existing_yandex_user(oauth_app):
    client, sessions, calls = oauth_app
    expected_id = "ya_" + hashlib.sha256(f"{SECRET_KEY}:yandex_id:verified-provider-user".encode()).hexdigest()[:16]
    async with sessions() as db:
        db.add(User(id=expected_id, name="Яндекс ID", is_guest=False, car_id="existing-car"))
        await db.commit()
    state, cookie, _ = await begin(client)
    response = await callback(client, state, cookie)
    assert response.status_code == 200
    assert "AutoXAKSession.acceptOAuthSession" in response.text
    assert expected_id in response.text
    assert '"auth_provider": "yandex"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert len(calls) == 1
    async with sessions() as db:
        assert (await db.get(TripRecording, "guest-trip")).user_id == "guest_owner"
        assert (await db.get(User, "guest_owner")).total_savings_rub == 999
        assert (await db.get(User, expected_id)).car_id == "existing-car"
        assert len((await db.execute(select(User).where(User.id.startswith("ya_")))).scalars().all()) == 1


async def test_missing_tampered_and_wrong_browser_state_never_exchange(oauth_app):
    client, _, calls = oauth_app
    state, cookie, _ = await begin(client)
    client.cookies.clear()
    assert (await callback(client, state)).status_code == 400
    assert (await callback(client, state, "wrong-cookie")).status_code == 400
    assert (await callback(client, state + "x", cookie)).status_code == 400
    assert (await client.get("/api/v1/auth/yandex/callback", params={"code": "c"})).status_code == 400
    assert not calls


async def test_expired_and_replayed_state_are_rejected(oauth_app, monkeypatch):
    client, _, calls = oauth_app
    state, cookie, _ = await begin(client)
    now = oauth.time.time()
    with monkeypatch.context() as context:
        context.setattr(oauth.time, "time", lambda: now + oauth.STATE_TTL_SECONDS + 1)
        assert (await callback(client, state, cookie)).status_code == 400
    assert not calls
    state, cookie, _ = await begin(client)
    assert (await callback(client, state, cookie)).status_code == 200
    assert (await callback(client, state, cookie)).status_code == 400
    assert len(calls) == 1


async def test_concurrent_callback_consumes_nonce_once(oauth_app):
    client, _, calls = oauth_app
    state, cookie, _ = await begin(client)
    results = await asyncio.gather(callback(client, state, cookie), callback(client, state, cookie))
    assert sorted(response.status_code for response in results) == [200, 400]
    assert len(calls) == 1


async def test_denial_and_provider_failure_cannot_leak_or_replay(oauth_app, monkeypatch):
    client, _, calls = oauth_app
    state, cookie, _ = await begin(client)
    assert (await callback(client, state, cookie, error="access_denied")).status_code == 400
    assert not calls
    assert (await callback(client, state, cookie)).status_code == 400

    async def failure(*args):
        raise ValueError("private-provider-token-do-not-leak")

    monkeypatch.setattr(oauth, "_exchange_code_for_yandex_id", failure)
    state, cookie, _ = await begin(client)
    response = await callback(client, state, cookie)
    assert response.status_code == 502
    assert "private-provider-token-do-not-leak" not in response.text
    assert (await callback(client, state, cookie)).status_code == 400


async def test_unconfigured_oauth_has_no_demo_or_identity_fallback(oauth_app, monkeypatch):
    client, _, calls = oauth_app
    monkeypatch.delenv("YANDEX_CLIENT_SECRET")
    assert (await client.get("/api/v1/auth/yandex/login")).status_code == 503
    assert (await client.get("/api/v1/auth/yandex/demo?guest_id=guest_owner")).status_code == 404
    assert (await client.get("/api/v1/auth/me?user_id=guest_owner")).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=bearer(expired=True))).status_code == 401
    assert not calls


async def test_login_relogin_uses_configured_redirect_not_forwarded_host(oauth_app):
    client, _, _ = oauth_app
    response = await client.get("/api/v1/auth/yandex/login", headers={"X-Forwarded-Host": "attacker.invalid"})
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["redirect_uri"] == ["https://testserver/api/v1/auth/yandex/callback"]
    assert (await client.get("/api/v1/auth/yandex/login?guest_id=guest_other")).status_code == 400


async def test_strict_me_renews_own_token_without_synthetic_savings(oauth_app):
    client, _, _ = oauth_app
    response = await client.get("/api/v1/auth/me", headers=bearer())
    assert response.status_code == 200
    assert response.json()["user_id"] == "guest_owner"
    assert response.json()["token"]
    assert "total_savings_rub" not in response.json()
    assert (await client.get("/api/v1/auth/me?user_id=guest_other", headers=bearer())).status_code == 403


@pytest.mark.parametrize("uri", ["http://public.example/api/v1/auth/yandex/callback", "https://testserver/wrong",
                                "https://user:password@testserver/api/v1/auth/yandex/callback",
                                "https://testserver/api/v1/auth/yandex/callback?next=bad"])
async def test_invalid_configured_redirect_is_rejected(oauth_app, monkeypatch, uri):
    client, _, _ = oauth_app
    monkeypatch.setenv("YANDEX_REDIRECT_URI", uri)
    assert (await client.get("/api/v1/auth/yandex/login")).status_code == 503


async def test_provider_exchange_uses_fixed_endpoints_and_pkce(monkeypatch):
    original_client = httpx.AsyncClient
    seen = []

    def provider(request):
        seen.append(request)
        if request.url.host == "oauth.yandex.ru":
            form = parse_qs(request.content.decode())
            assert form["code_verifier"] == ["test-verifier"]
            assert form["client_secret"] == ["configured-secret"]
            return httpx.Response(200, json={"access_token": "provider-token"})
        assert request.url.host == "login.yandex.ru"
        assert request.headers["Authorization"] == "OAuth provider-token"
        return httpx.Response(200, json={"psuid": "pairwise-identity", "id": "fallback-identity"})

    monkeypatch.setattr(oauth.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(provider), **kwargs))
    config = oauth.OAuthConfig("configured-client", "configured-secret", "https://testserver/api/v1/auth/yandex/callback")
    result = await oauth._exchange_code_for_yandex_id("test-code", config, "test-verifier")
    assert result == "pairwise-identity"
    assert len(seen) == 2
