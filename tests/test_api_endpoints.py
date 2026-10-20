"""Public app contracts and explicit retirement of the synthetic savings pipeline."""
import pytest


async def test_health_and_pages(async_client):
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "2.0.0"
    for path in ("/", "/setup", "/history"):
        response = await async_client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    response = await async_client.get("/sw.js")
    assert response.headers["service-worker-allowed"] == "/"


async def test_handshake_idempotency_and_owner_isolation(async_client):
    payload = {"device_id": "a"*32}
    first = (await async_client.post("/api/v1/auth/handshake", json=payload)).json()
    repeated = (await async_client.post("/api/v1/auth/handshake", json=payload)).json()
    # Identical prefix must not allow impersonation, unlike the previous truncated ID.
    other = (await async_client.post("/api/v1/auth/handshake", json={"device_id": "a"*16+"b"*16})).json()
    assert first["user_id"] == repeated["user_id"] != other["user_id"]
    assert first["is_new_user"] is True and repeated["is_new_user"] is False
    assert (await async_client.get("/api/v1/users/me")).status_code == 401
    me = await async_client.get("/api/v1/users/me", headers={"Authorization": "Bearer "+first["token"]})
    assert me.json()["user_id"] == first["user_id"]
    assert me.headers["cache-control"] == "no-store"


async def test_auth_returns_token_and_guest_upgrade_checks_owner(async_client):
    one = (await async_client.post("/api/v1/auth/guest")).json()
    two = (await async_client.post("/api/v1/auth/guest")).json()
    headers = {"Authorization": "Bearer "+one["token"]}
    response = await async_client.post("/api/v1/auth/upgrade-guest", headers=headers,
        json={"guest_user_id": two["user_id"], "email": "person@example.test", "password": "testpass123"})
    assert response.status_code == 403
    assert (await async_client.get("/api/v1/auth/me", params={"user_id": two["user_id"]}, headers=headers)).status_code == 403


@pytest.mark.parametrize("path", ["/api/v1/telemetry/session", "/api/v1/trips/complete", "/api/v1/analytics/verify-run"])
async def test_legacy_calculation_cannot_record_fake_savings(async_client, path):
    response = await async_client.post(path, json={"total_savings_rub": 1000})
    assert response.status_code == 410
    assert response.json()["status"] == "legacy_endpoint_retired"


async def test_legacy_dashboard_does_not_expose_old_oil_totals(async_client):
    response = await async_client.get("/api/v1/users/someone/dashboard")
    assert response.status_code == 410
    assert "total_savings_rub" not in response.json()


async def test_email_constraint_race_returns_conflict(async_client):
    from unittest.mock import AsyncMock, patch
    from sqlalchemy.exc import IntegrityError
    guest = (await async_client.post('/api/v1/auth/guest')).json()
    collision = IntegrityError('insert/update', {}, Exception('unique email'))
    with patch('app.api.auth.AsyncSession.commit', new=AsyncMock(side_effect=collision)):
        response = await async_client.post('/api/v1/auth/register', json={
            'email': 'concurrent@example.test', 'password': 'testpass123'})
        assert response.status_code == 409
        response = await async_client.post('/api/v1/auth/upgrade-guest',
            headers={'Authorization': 'Bearer ' + guest['token']}, json={
                'guest_user_id': guest['user_id'], 'email': 'concurrent@example.test', 'password': 'testpass123'})
        assert response.status_code == 409
