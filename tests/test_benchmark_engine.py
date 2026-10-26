import numpy as np
from app.services.benchmark_engine import BenchmarkEngine, VerificationReport


def test_running_time_does_not_validate_oil_equivalent_hours():
    """Длительность работы двигателя не является эталоном химического состояния масла."""
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
    assert report.mape_percent is None
    assert report.hypothesis_confirmed is False
    assert report.p_value is None
    assert report.reference_validates_oil_life is False


def test_single_same_quantity_match_is_not_a_scientific_attestation():
    """Даже точное совпадение длительности одной записи не подтверждает точность масла."""
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
        dt=0.02, prediction_quantity='engine_running_hours'
    )
    assert report.mape_percent == 0.0
    assert report.hypothesis_confirmed is False
