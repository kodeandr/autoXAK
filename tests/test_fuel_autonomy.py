import numpy as np
import pytest
from app.services.twin_engine import TwinEngine
from app.models.vehicle_profiles import VEHICLE_REGISTRY


def test_z_shock_veto_suppresses_road_bumps():
    engine = TwinEngine()
    dt = 0.02
    n = 100
    raw_ax = np.zeros(n)
    raw_ax[30:45] = 4.0
    raw_az = np.full(n, 9.81)
    raw_az[30:45] = 16.0

    clean_ax, bumps_count = engine.filter_road_anomalies(raw_ax, raw_az=raw_az, dt=dt)
    assert bumps_count >= 1
    assert np.max(clean_ax) < 1.0


def test_throttle_hunting_detector_and_penalty():
    engine = TwinEngine()
    dt = 0.02
    n = 1500
    time_arr = np.linspace(0, 30, n)
    speeds = 19.4 + 1.2 * np.sin(2 * np.pi * 0.4 * time_arr)
    ax = np.gradient(speeds, dt)

    hunting_detected, penalty_liters, pke = engine.detect_throttle_hunting(speeds, ax, dt=dt)
    assert hunting_detected is True
    assert pke > 10.0
    assert penalty_liters > 0.0


def test_fuel_autonomy_range_calculation():
    profile = VEHICLE_REGISTRY.get("haval_jolion_15t")
    engine = TwinEngine(profile=profile)
    speeds = np.full(1000, 13.88)
    res = engine.evaluate_financial_delta(
        distance_km=10.0,
        speeds_mps=speeds,
        user_wear_percent=0.02,
        fuel_price_rub=72.0,
        service_cost_rub=9500.0
    )
    assert 1.5 <= res["range_bonus_km"] <= 3.0
    assert res["saved_fuel_liters"] > 0.0
    assert res["autonomy_days_extended"] > 0.0
    assert res["smooth_score"] == 100
