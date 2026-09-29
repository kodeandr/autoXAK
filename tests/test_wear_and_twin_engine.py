import pytest
import numpy as np
from app.services.wear_engine import WearEngine
from app.services.twin_engine import AggressiveTwinEngine
from app.models.vehicle_profiles import get_default_profile


def test_traffic_jam_accelerates_oil_wear(default_vehicle_profile):
    """Проверка повышенного начисления износа масла на холостом ходу в пробке 10 баллов."""
    engine = WearEngine(profile=default_vehicle_profile)
    n_points = 1000
    zero_speeds = np.zeros(n_points)
    zero_accel = np.zeros(n_points)

    wear_heavy = engine.compute_oil_wear(zero_speeds, zero_accel, traffic_score=10, dt=0.02)
    wear_free = engine.compute_oil_wear(zero_speeds, zero_accel, traffic_score=1, dt=0.02)

    assert wear_heavy["equivalent_engine_hours"] > wear_free["equivalent_engine_hours"]


def test_aggressive_dynamics_increases_wear(default_vehicle_profile):
    """Проверка реакции модели на резкие динамические ускорения."""
    engine = WearEngine(profile=default_vehicle_profile)
    n_points = 1000
    speeds = np.full(n_points, 10.0)

    calm_accel = np.full(n_points, 0.4)
    harsh_accel = np.full(n_points, 2.5)

    calm_res = engine.compute_oil_wear(speeds, calm_accel, traffic_score=3, dt=0.02)
    harsh_res = engine.compute_oil_wear(speeds, harsh_accel, traffic_score=3, dt=0.02)

    assert harsh_res["equivalent_engine_hours"] > calm_res["equivalent_engine_hours"]


def test_twin_engine_savings_and_limits(default_vehicle_profile):
    """Проверка расчета baseline-двойника: положительная экономия."""
    twin = AggressiveTwinEngine(profile=default_vehicle_profile)
    speeds = np.linspace(0.0, 16.6, 1000)

    res = twin.simulate_twin_and_delta(
        speeds_mps=speeds,
        user_wear_percent=0.015,
        dt=0.02,
        fuel_price_rub=62.0,
        service_cost_rub=9500.0
    )

    assert res["fuel_saved_rub"] >= 0.0
    assert res["total_savings_rub"] > 0.0


def test_twin_engine_zero_movement(default_vehicle_profile):
    """При нулевой дистанции финансовая экономия не должна уходить в отрицательные значения."""
    twin = AggressiveTwinEngine(profile=default_vehicle_profile)
    speeds = np.zeros(200)

    res = twin.simulate_twin_and_delta(
        speeds_mps=speeds,
        user_wear_percent=0.001,
        dt=0.02
    )
    assert res["total_savings_rub"] >= 0.0
