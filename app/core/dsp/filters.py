import numpy as np
from scipy.signal import butter, filtfilt
from typing import List, Tuple

class SignalFilter:
    def __init__(self, sample_rate_hz: float = 50.0, cutoff_hz: float = 2.5):
        """
        Пайплайн цифровой фильтрации сырой мобильной телеметрии.
        :param sample_rate_hz: Частота дискретизации сенсоров смартфона (норматив 50 Гц)
        :param cutoff_hz: Частота среза паразитных вибраций подвески (2.5 Гц)
        """
        self.sample_rate = sample_rate_hz
        self.cutoff = cutoff_hz
        self.nyquist = 0.5 * sample_rate_hz
        
        # Расчет коэффициентов низкочастотного фильтра Баттерворта 4-го порядка
        normal_cutoff = self.cutoff / self.nyquist
        self.b, self.a = butter(4, normal_cutoff, btype='low', analog=False)

    def apply_butterworth(self, raw_signal: np.ndarray) -> np.ndarray:
        """
        Двунаправленная фильтрация filtfilt (нулевой фазовый сдвиг).
        """
        if len(raw_signal) < 15:
            # Для коротких последовательностей возвращаем как есть (защита от падения filtfilt)
            return raw_signal
        return filtfilt(self.b, self.a, raw_signal)

    def isolate_linear_acceleration(
        self, 
        ax: List[float], 
        ay: List[float], 
        az: List[float]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Удаление гравитационного вектора через фильтр низких частот и 
        вычисление чистого вектора ускорения движения автомобиля.
        """
        x = np.array(ax, dtype=np.float64)
        y = np.array(ay, dtype=np.float64)
        z = np.array(az, dtype=np.float64)

        # 1. Выделяем гравитацию (медленно меняющийся вектор)
        # Фильтр с экстремально низкой частотой среза (0.3 Гц)
        b_grav, a_grav = butter(2, 0.3 / self.nyquist, btype='low', analog=False)
        
        if len(x) >= 15:
            grav_x = filtfilt(b_grav, a_grav, x)
            grav_y = filtfilt(b_grav, a_grav, y)
            grav_z = filtfilt(b_grav, a_grav, z)
        else:
            grav_x, grav_y, grav_z = 0.0, 0.0, 9.81

        # 2. Чистое линейное ускорение = Сырой сигнал - Гравитация
        linear_x = x - grav_x
        linear_y = y - grav_y
        linear_z = z - grav_z

        # 3. Фильтрация оставшихся высокочастотных шумов дороги (срез 2.5 Гц)
        filtered_x = self.apply_butterworth(linear_x)
        filtered_y = self.apply_butterworth(linear_y)
        filtered_z = self.apply_butterworth(linear_z)

        return filtered_x, filtered_y, filtered_z

    def calculate_horizontal_acceleration(
        self, 
        filtered_x: np.ndarray, 
        filtered_y: np.ndarray
    ) -> np.ndarray:
        """
        Расчет интегрального горизонтального ускорения (тяга/торможение + боковые перегрузки)
        """
        return np.sqrt(filtered_x**2 + filtered_y**2)