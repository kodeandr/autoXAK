import os
os.environ.setdefault("SECRET_KEY", "autoxak-test-only-signing-key-do-not-deploy")
os.environ.setdefault("DGIS_USE_MOCK", "true")
os.environ.setdefault("LOG_DIR", "/tmp/autoxak-test-logs")
import pytest
import pytest_asyncio
import numpy as np
from typing import AsyncGenerator, Dict, Any, List
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from app.main import app
from app.core.database import engine, get_db, initialize_schema
from app.models.db_models import User
from app.core.security import create_access_token
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.vehicle_profiles import get_default_profile, VehiclePhysicalProfile


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


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def prepared_database():
    await initialize_schema()
    await engine.dispose()


@pytest.fixture
async def async_client(prepared_database) -> AsyncGenerator[AsyncClient, None]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as seed:
            if await seed.get(User, "test_user_coverage") is None:
                seed.add(User(id="test_user_coverage", car_id="haval_jolion_15t"))
                await seed.commit()

        async def test_db():
            async with AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://testserver", headers={"Authorization": "Bearer " + create_access_token("test_user_coverage")}) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_db, None)
            await transaction.rollback()
    await engine.dispose()


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


@pytest.fixture
async def committed_client(prepared_database):
    """Independent committed requests for PostgreSQL concurrency/streaming tests."""
    import uuid
    from sqlalchemy import delete
    from app.core.database import async_session_factory
    user_id = 'audit_' + uuid.uuid4().hex
    async with async_session_factory() as session:
        session.add(User(id=user_id, car_id='haval_jolion_15t'))
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver', headers={'Authorization': 'Bearer ' + create_access_token(user_id)}) as client:
            client.audit_user_id = user_id
            yield client
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()
