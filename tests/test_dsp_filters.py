import pytest
import numpy as np
from app.core.dsp.filters import SignalFilter
from app.core.dsp.simplifier import PathSimplifier


def test_gravity_vector_removal():
    """Проверка работы фильтрации ускорений IMU."""
    filter_mod = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
    n_samples = 200

    ax = np.zeros(n_samples)
    ay = np.zeros(n_samples)
    az = np.full(n_samples, 9.81)

    filt_x, filt_y, filt_z = filter_mod.isolate_linear_acceleration(ax, ay, az)

    assert filt_x is not None
    assert len(filt_z) == n_samples


def test_butterworth_attenuates_high_frequency_noise():
    """Проверка среза паразитных частот подвески фильтром Баттерворта."""
    fs = 50.0
    filter_mod = SignalFilter(sample_rate_hz=fs, cutoff_hz=2.5)
    time = np.linspace(0, 10.0, int(10.0 * fs))

    useful_signal = 1.5 * np.sin(2 * np.pi * 0.2 * time)
    noise = 1.0 * np.sin(2 * np.pi * 15.0 * time)
    raw_signal = useful_signal + noise

    clean_signal = filter_mod.apply_butterworth_lpf(raw_signal)

    residual_noise = np.std(clean_signal - useful_signal)
    assert residual_noise < 0.35


def test_rdp_path_simplification_efficiency():
    """Проверка сжатия трека по алгоритму RDP."""
    simplifier = PathSimplifier()
    points = []

    for i in range(100):
        points.append({"lat": 55.7000 + i * 0.0001, "lon": 37.6000 + i * 0.0001})
    points[50]["lat"] += 0.005

    simplified = simplifier.downsample_gps_stream(points, max_points_for_api=15)

    assert len(simplified) <= 15
    assert len(simplified) >= 3
