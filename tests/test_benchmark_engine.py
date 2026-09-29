import pytest
import numpy as np
from app.services.benchmark_engine import BenchmarkEngine, VerificationReport


def test_benchmark_engine_h1_confirmation():
    """Верификация гипотезы H1: погрешность модели MAPE <= 10%, p-value < 0.05."""
    engine = BenchmarkEngine()

    rpm = [850.0] * 100
    load = [25.0] * 100
    temp = [90.0] * 100
    savings = [15.0, 18.0, 12.0, 20.0, 16.0]

    gt_hours = engine.calculate_obd_ground_truth_hours(
        np.array(rpm), np.array(load), np.array(temp), dt=0.02
    )
    # Расчет с реалистичной погрешностью ~3% (в пределах порога 10%)
    predicted_hours = gt_hours * 1.03

    report: VerificationReport = engine.evaluate_experiment(
        autoxak_engine_hours=predicted_hours,
        obd_rpm=rpm,
        obd_load=load,
        obd_temp=temp,
        user_savings=savings,
        dt=0.02
    )

    assert report.total_points == 100
    assert report.mape_percent <= 10.0
    assert report.hypothesis_confirmed is True
    assert report.p_value < 0.05


def test_benchmark_engine_zero_error_boundary():
    """Граничный случай: идеальное совпадение данных (MAPE = 0)."""
    engine = BenchmarkEngine()
    rpm = [800.0] * 50
    load = [20.0] * 50
    temp = [90.0] * 50
    savings = [10.0] * 5

    gt_hours = engine.calculate_obd_ground_truth_hours(
        np.array(rpm), np.array(load), np.array(temp), dt=0.02
    )
    report = engine.evaluate_experiment(
        autoxak_engine_hours=gt_hours,
        obd_rpm=rpm,
        obd_load=load,
        obd_temp=temp,
        user_savings=savings,
        dt=0.02
    )
    assert report.mape_percent == 0.0
    assert report.hypothesis_confirmed is True
