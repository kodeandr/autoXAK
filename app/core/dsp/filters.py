import numpy as np
from scipy import signal
from typing import Tuple, Union


class SignalFilter:
    def calculate_horizontal_acceleration(self, ax, ay):
        """Вычисление результирующего горизонтального ускорения: sqrt(ax^2 + ay^2)."""
        import numpy as np
        return np.hypot(ax, ay)

    def __init__(self, sample_rate_hz: float = 50.0, cutoff_hz: float = 2.5):
        if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0 or not np.isfinite(cutoff_hz) or cutoff_hz <= 0:
            raise ValueError("Filter frequencies must be positive and finite")
        self.fs = sample_rate_hz
        self.cutoff = cutoff_hz

    def apply_butterworth_lpf(self, data: Union[np.ndarray, list], order: int = 4) -> np.ndarray:
        arr = np.asarray(data, dtype=float)
        if arr.ndim != 1 or not np.isfinite(arr).all():
            raise ValueError("Expected a finite one-dimensional signal")
        if not isinstance(order, int) or order < 1:
            raise ValueError("Filter order must be a positive integer")
        if len(arr) <= 3 * (order + 1):
            return arr
        nyq = 0.5 * self.fs
        normal_cutoff = min(self.cutoff / nyq, 0.99)
        b, a = signal.butter(order, normal_cutoff, btype='low', analog=False)
        return signal.filtfilt(b, a, arr)

    def isolate_linear_acceleration(
        self,
        ax: Union[np.ndarray, list],
        ay: Union[np.ndarray, list],
        az: Union[np.ndarray, list]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Вычитает квазистатический вектор гравитации для выделения динамического ускорения."""
        x = np.asarray(ax, dtype=float)
        y = np.asarray(ay, dtype=float)
        z = np.asarray(az, dtype=float)

        if not (len(x) == len(y) == len(z)):
            raise ValueError("Acceleration axes must have equal lengths")
        if len(x) == 0:
            return x, y, z
        # A fixed gravity estimate preserves driving frequencies. The caller with
        # speed/orientation data should use IMUAlignmentService before low-pass filtering.
        gravity = np.median(np.column_stack((x, y, z)), axis=0)
        norm = np.linalg.norm(gravity)
        gravity = gravity * (9.81 / norm) if norm > 1e-9 else np.zeros(3)
        return tuple(self.apply_butterworth_lpf(axis - bias) for axis, bias in zip((x, y, z), gravity))
