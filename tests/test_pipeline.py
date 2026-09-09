import pytest
import numpy as np
from app.core.dsp.filters import SignalFilter
from app.services.wear_engine import WearEngine
from app.services.twin_engine import AggressiveTwinEngine

def test_dsp_gravity_removal():
    filter_mod = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
    # Подаем константный вектор гравитации 9.81 м/с^2 по оси Z на протяжении 2 секунд
    n = 100
    ax = [0.0] * n
    ay = [0.0] * n
    az = [9.81] * n

    _, _, filt_z = filter_mod.isolate_linear_acceleration(ax, ay, az)
    # После вычитания вектора гравитации ускорение по Z должно стремиться к 0
    assert abs(np.mean(filt_z)) < 0.1

def test_wear_engine_traffic_stress():
    engine = WearEngine(ambient_temp_c=20.0)
    n = 1000  # 20 секунд
    # Тест 1: автомобиль стоит на месте (пробка, холостой ход)
    zero_speeds = np.zeros(n)
    acc = np.zeros(n)
    res_idle = engine.compute_oil_wear(zero_speeds, acc, dt=0.02)

    # Тест 2: автомобиль движется со стабильной крейсерской скоростью 15 м/с
    cruise_speeds = np.full(n, 15.0)
    res_cruise = engine.compute_oil_wear(cruise_speeds, acc, dt=0.02)

    # На холостом ходу в пробке эквивалентный износ должен начисляться быстрее из-за штрафа K_traffic
    assert res_idle["equivalent_engine_hours"] > res_cruise["equivalent_engine_hours"]

def test_twin_engine_savings_positive():
    twin = AggressiveTwinEngine()
    # Спокойная поездка с небольшим набором скорости
    speeds = np.linspace(0.0, 15.0, 500)
    res = twin.simulate_twin_and_delta(speeds_mps=speeds, user_wear_percent=0.05, dt=0.02)

    assert res["fuel_saved_rub"] >= 0.0
    assert res["total_savings_rub"] > 0.0