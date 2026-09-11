import pytest
import numpy as np
from app.core.dsp.filters import SignalFilter
from app.services.wear_engine import WearEngine
from app.services.twin_engine import AggressiveTwinEngine
from app.core.dsp.simplifier import PathSimplifier
from app.services.gis_service import GISService

def test_dsp_gravity_removal():
    filter_mod = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
    n = 100
    ax = [0.0] * n
    ay = [0.0] * n
    az = [9.81] * n

    _, _, filt_z = filter_mod.isolate_linear_acceleration(ax, ay, az)
    assert abs(np.mean(filt_z)) < 0.1

def test_wear_engine_traffic_stress():
    engine = WearEngine(ambient_temp_c=20.0)
    n = 1000  # 20 секунд
    zero_speeds = np.zeros(n)
    acc = np.zeros(n)

    # Холостой ход в заторе 2GIS (10 баллов) vs холостой ход без затора (1 балл)
    res_heavy_traffic = engine.compute_oil_wear(zero_speeds, acc, traffic_score=10, dt=0.02)
    res_free_road = engine.compute_oil_wear(zero_speeds, acc, traffic_score=1, dt=0.02)

    # Термический застой в пробке начисляет больше эквивалентных моточасов
    assert res_heavy_traffic["equivalent_engine_hours"] > res_free_road["equivalent_engine_hours"]

def test_twin_engine_savings_positive():
    twin = AggressiveTwinEngine()
    speeds = np.linspace(0.0, 15.0, 500)
    res = twin.simulate_twin_and_delta(speeds_mps=speeds, user_wear_percent=0.05, dt=0.02)
    assert res["fuel_saved_rub"] >= 0.0
    assert res["total_savings_rub"] > 0.0

@pytest.mark.asyncio
async def test_gis_route_context():
    gis = GISService()
    coords = [{"lat": 55.751244, "lon": 37.618423}]
    context = await gis.get_route_context(coords)
    assert context.traffic_score >= 1
    assert context.traffic_score <= 10
    assert context.road_type is not None

def test_rdp_path_simplification():
    simplifier = PathSimplifier()
    points = []
    for i in range(100):
        lat = 55.75 + (i * 0.0001)
        lon = 37.61 + (i * 0.0001)
        points.append({"lat": lat, "lon": lon})
    points[50]["lat"] += 0.005

    simplified = simplifier.downsample_gps_stream(points, max_points_for_api=10)
    assert len(simplified) < 10
    assert len(simplified) >= 3
