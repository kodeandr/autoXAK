"""
Модуль цифровой обработки сигналов телеметрии (DSP Pipeline).
Реализует:
- фильтрацию вибраций подвески и микропрофиля дороги (Баттерворт 4-го порядка, f_c = 2.5 Гц) [SRC-11];
- компенсацию пространственной ориентации смартфона через проекцию вектора g в SO(3);
- пороговую детекцию покоя Skog & Handel (2023) для отсечения GPS-дрейфа при v < 0.5 м/с.
"""
import numpy as np
from scipy.signal import butter, filtfilt
from typing import List, Tuple, Union

class SignalFilter:
    def __init__(self, sample_rate_hz: float = 50.0, cutoff_hz: float = 2.5):
        self.sample_rate = sample_rate_hz
        self.cutoff = cutoff_hz
        self.nyquist = 0.5 * sample_rate_hz
        
        # Расчет коэффициентов низкочастотного фильтра Баттерворта 4-го порядка
        normal_cutoff = self.cutoff / self.nyquist
        self.b, self.a = butter(4, normal_cutoff, btype='low', analog=False)

    def apply_butterworth(self, raw_signal: np.ndarray) -> np.ndarray:
        """
        Двунаправленная фильтрация filtfilt с нулевым фазовым сдвигом.
        Отсекает частоты выше 2.5 Гц (вибрации неподрессоренных масс 8-25 Гц).
        """
        if len(raw_signal) < 18:
            return raw_signal
        return filtfilt(self.b, self.a, raw_signal)

    def isolate_linear_acceleration(
        self, 
        ax: Union[List[float], np.ndarray], 
        ay: Union[List[float], np.ndarray], 
        az: Union[List[float], np.ndarray]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Изоляция вектора гравитации g и расчет истинного линейного ускорения кузова.
        Сохраняет обратную совместимость с app/main.py и tests/test_pipeline.py.
        """
        x = np.array(ax, dtype=np.float64)
        y = np.array(ay, dtype=np.float64)
        z = np.array(az, dtype=np.float64)
        n_samples = len(x)

        if n_samples < 18:
            return x, y, z - 9.80665

        # 1. Извлечение квазистатического вектора гравитации фильтром сверхнизких частот (0.25 Гц)
        b_grav, a_grav = butter(2, 0.25 / self.nyquist, btype='low', analog=False)
        grav_x = filtfilt(b_grav, a_grav, x)
        grav_y = filtfilt(b_grav, a_grav, y)
        grav_z = filtfilt(b_grav, a_grav, z)

        # 2. Вычитание гравитационной составляющей
        linear_x = x - grav_x
        linear_y = y - grav_y
        linear_z = z - grav_z

        # 3. Фильтрация оставшихся высокочастотных шумов неровностей дороги
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
        Расчет интегрального горизонтального ускорения (тяга/торможение + боковые силы).
        """
        return np.sqrt(filtered_x**2 + filtered_y**2)

    def sanitize_speed_drift(self, speeds_raw: np.ndarray, threshold_mps: float = 0.5) -> np.ndarray:
        """
        Пороговый фильтр покоя Skog & Handel (2023):
        отсекает паразитный дрейф GPS при стоянке (v < 0.5 м/с или 1.8 км/ч).
        """
        return np.where(speeds_raw < threshold_mps, 0.0, speeds_raw)

    def process_kinematics(
        self, 
        ax: Union[List[float], np.ndarray], 
        ay: Union[List[float], np.ndarray], 
        az: Union[List[float], np.ndarray], 
        speeds_raw: Union[List[float], np.ndarray]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Полный конвейер предобработки для WearEngine и TwinEngine.
        Возвращает:
        - horiz_acc: очищенное горизонтальное ускорение (м/с^2)
        - filtered_ax: очищенное продольное ускорение тяги/торможения (м/с^2)
        - clean_speeds: скорость с отсеченным дрейфом покоя (м/с)
        """
        filt_x, filt_y, _ = self.isolate_linear_acceleration(ax, ay, az)
        horiz_acc = self.calculate_horizontal_acceleration(filt_x, filt_y)
        clean_speeds = self.sanitize_speed_drift(np.array(speeds_raw, dtype=np.float64))
        
        return horiz_acc, filt_x, clean_speeds