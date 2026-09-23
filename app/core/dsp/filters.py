import numpy as np


class SignalFilter:
    """
    Цифровой фильтр телематических сигналов IMU.
    Подавляет высокочастотные вибрации ДВС и неровностей дорожного полотна.
    """

    def __init__(self, sample_rate_hz: float = 50.0, cutoff_hz: float = 2.5):
        self.fs = sample_rate_hz
        self.cutoff = cutoff_hz

    def apply_butterworth_lpf(self, signal: np.ndarray) -> np.ndarray:
        """
        ФНЧ Баттерворта 2-го порядка (fc = 2.5 Гц).
        """
        if len(signal) < 15:
            return signal

        try:
            from scipy.signal import butter, filtfilt
            b, a = butter(2, self.cutoff / (0.5 * self.fs), btype='low')
            return filtfilt(b, a, signal)
        except Exception:
            # Двухпроходный IIR-фильтр (zero-phase) на чистом NumPy (fallback без scipy)
            alpha = 2 * np.pi * (self.cutoff / self.fs) / (2 * np.pi * (self.cutoff / self.fs) + 1)
            # Прямой проход
            fwd = np.zeros_like(signal, dtype=np.float64)
            fwd[0] = signal[0]
            for i in range(1, len(signal)):
                fwd[i] = alpha * signal[i] + (1 - alpha) * fwd[i - 1]
            # Обратный проход (компенсация задержки по фазе)
            bwd = np.zeros_like(fwd)
            bwd[-1] = fwd[-1]
            for i in range(len(fwd) - 2, -1, -1):
                bwd[i] = alpha * fwd[i] + (1 - alpha) * bwd[i + 1]
            return bwd

    def isolate_linear_acceleration(self, ax: np.ndarray, ay: np.ndarray, az: np.ndarray):
        filt_x = self.apply_butterworth_lpf(ax)
        filt_y = self.apply_butterworth_lpf(ay)
        filt_z = self.apply_butterworth_lpf(az)
        return filt_x, filt_y, filt_z

    def calculate_horizontal_acceleration(self, fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
        return np.sqrt(fx**2 + fy**2)
