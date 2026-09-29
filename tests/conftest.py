import asyncio
import pytest
import numpy as np
from typing import AsyncGenerator, Dict, Any, List
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from app.main import app
from app.models.vehicle_profiles import get_default_profile, VehiclePhysicalProfile


@pytest.fixture(scope="session")
def event_loop():
    """Единый глобальный event loop для всех тестов сессии."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def default_vehicle_profile() -> VehiclePhysicalProfile:
    return get_default_profile()


@pytest.fixture
def synthetic_telemetry_batch() -> Dict[str, Any]:
    fs = 50.0
    duration_s = 60.0
    n_samples = int(fs * duration_s)
    time_series = np.linspace(0.0, duration_s, n_samples)

    speed_mps = np.clip(15.0 * np.sin(np.pi * time_series / duration_s), 0.0, 15.0)
    accel_long = np.gradient(speed_mps, 1.0 / fs)
    suspension_noise = 0.2 * np.random.normal(0, 1, n_samples)

    stream: List[Dict[str, float]] = []
    base_lat, base_lon = 55.751244, 37.618423

    for i in range(n_samples):
        stream.append({
            "t": round(float(time_series[i]), 3),
            "ax": round(float(accel_long[i] + suspension_noise[i]), 4),
            "ay": round(float(0.05 * np.sin(0.1 * time_series[i])), 4),
            "az": round(float(9.81 + suspension_noise[i]), 4),
            "gx": 0.01,
            "gy": 0.01,
            "gz": 0.02,
            "speed": round(float(speed_mps[i]), 2),
            "lat": round(base_lat + (i * 0.00001), 6),
            "lon": round(base_lon + (i * 0.00001), 6)
        })

    return {
        "session_id": "test-session-coverage-001",
        "user_id": "test_user_coverage",
        "car_id": "haval_jolion_15t",
        "telemetry_stream": stream
    }


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture(autouse=True)
def mock_dgis_service():
    class MockRouteContext:
        traffic_score = 3
        road_type = "city_arterial"
        elevation_gain_m = 12.5
        avg_speed_limit_kmh = 60.0

    with patch("app.services.gis_service.GISService.get_route_context", new_callable=AsyncMock) as mock_gis:
        mock_gis.return_value = MockRouteContext()
        yield mock_gis
