import pytest
import uuid
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta


async def test_health_check_endpoint(async_client: AsyncClient):
    """GET /health: проверка жизнеспособности сервиса."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["ok", "healthy"]


async def test_process_telemetry_session(async_client: AsyncClient, synthetic_telemetry_batch: dict):
    """POST /api/v1/telemetry/session: сквозной прием, расчет и сохранение сессии."""
    response = await async_client.post(
        "/api/v1/telemetry/session",
        json=synthetic_telemetry_batch
    )
    assert response.status_code == 200
    data = response.json()

    assert "session_id" in data
    assert uuid.UUID(data["session_id"])
    assert data["status"] == "PROCESSED"
    assert data["distance_km"] > 0.0
    assert data["duration_seconds"] == 60.0


async def test_user_dashboard_aggregates(async_client: AsyncClient, synthetic_telemetry_batch: dict):
    """GET /api/v1/users/{user_id}/dashboard: виртуальная копилка и ресурс масла."""
    await async_client.post("/api/v1/telemetry/session", json=synthetic_telemetry_batch)
    response = await async_client.get("/api/v1/users/test_user_coverage/dashboard")
    assert response.status_code == 200
    data = response.json()

    assert "month_savings_rub" in data
    assert "oil_remaining_percent" in data
    assert 0.0 <= data["oil_remaining_percent"] <= 100.0


async def test_user_trips_history(async_client: AsyncClient, synthetic_telemetry_batch: dict):
    """GET /api/v1/users/{user_id}/trips: получение списка заездов пользователя."""
    await async_client.post("/api/v1/telemetry/session", json=synthetic_telemetry_batch)
    response = await async_client.get("/api/v1/users/test_user_coverage/trips")
    assert response.status_code == 200
    data = response.json()

    assert "trips" in data
    assert len(data["trips"]) > 0


async def test_period_analytics_empty_and_valid_ranges(async_client: AsyncClient, synthetic_telemetry_batch: dict):
    """GET /api/v1/users/{user_id}/analytics/period: фильтрация и устойчивость к 0."""
    await async_client.post("/api/v1/telemetry/session", json=synthetic_telemetry_batch)
    
    now = datetime.now(timezone.utc)
    start_str = (now - timedelta(days=7)).isoformat()
    end_str = (now + timedelta(minutes=10)).isoformat()

    # 1. Запрос диапазона с поездками
    response = await async_client.get(
        "/api/v1/users/test_user_coverage/analytics/period",
        params={"start_date": start_str, "end_date": end_str}
    )
    assert response.status_code == 200
    payload = response.json()
    assert "summary" in payload
    assert "trips" in payload
    assert payload["summary"]["total_trips"] > 0
    assert payload["summary"]["avg_speed_kmh"] >= 0.0

    # 2. Пустой исторический диапазон (проверка деления на 0 при расчете скорости)
    past_start = (now - timedelta(days=365)).isoformat()
    past_end = (now - timedelta(days=360)).isoformat()
    empty_resp = await async_client.get(
        "/api/v1/users/test_user_coverage/analytics/period",
        params={"start_date": past_start, "end_date": past_end}
    )
    assert empty_resp.status_code == 200
    empty_summary = empty_resp.json()["summary"]
    assert empty_summary["total_trips"] == 0
    assert empty_summary["avg_speed_kmh"] == 0.0


async def test_static_pages_availability(async_client: AsyncClient):
    """Проверка отдачи HTML-страниц интерфейса."""
    for path in ["/", "/setup", "/history"]:
        res = await async_client.get(path)
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
