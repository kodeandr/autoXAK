import numpy as np
from scipy import signal
from typing import Tuple, Union


class SignalFilter:
    def __init__(self, sample_rate_hz: float = 50.0, cutoff_hz: float = 2.5):
        self.fs = sample_rate_hz
        self.cutoff = cutoff_hz

    def apply_butterworth_lpf(self, data: Union[np.ndarray, list], order: int = 4) -> np.ndarray:
        arr = np.asarray(data, dtype=float)
        if len(arr) <= 15:
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

        # Низкочастотная оценка гравитационного вектора
        grav_x = self.apply_butterworth_lpf(x) if len(x) > 15 else np.full_like(x, np.mean(x))
        grav_y = self.apply_butterworth_lpf(y) if len(y) > 15 else np.full_like(y, np.mean(y))
        grav_z = self.apply_butterworth_lpf(z) if len(z) > 15 else np.full_like(z, np.mean(z))

        # Линейное динамическое ускорение ТС
        lin_x = x - grav_x
        lin_y = y - grav_y
        lin_z = z - grav_z

        return lin_x, lin_y, lin_z
