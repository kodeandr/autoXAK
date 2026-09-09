import numpy as np
from typing import List, Dict

class WearEngine:
    BASE_OIL_LIFETIME_HOURS = 250.0  # Эталонный ресурс масла по ТЗ (моточасы)

    def __init__(self, ambient_temp_c: float = 20.0):
        self.ambient_temp = ambient_temp_c

    def _get_temperature_multiplier(self) -> float:
        """
        Штрафной коэффициент температуры: зимние прогревы или летний перегрев
        """
        if self.ambient_temp < 0.0:
            # Холодный пуск и конденсат
            return 1.25
        elif self.ambient_temp > 30.0:
            # Риск термической деструкции полимерного загустителя
            return 1.15
        return 1.0

    def compute_oil_wear(
        self, 
        speeds_mps: np.ndarray, 
        horizontal_acc: np.ndarray, 
        dt: float = 0.02
    ) -> Dict[str, float]:
        """
        Расчет эквивалентных моточасов и процента износа масла за поездку.
        :param speeds_mps: Массив мгновенной скорости в м/с
        :param horizontal_acc: Массив очищенных горизонтальных перегрузок в м/с^2
        :param dt: Временной шаг сэмплирования (0.02 с для 50 Гц)
        """
        n_samples = len(speeds_mps)
        if n_samples == 0:
            return {"equivalent_engine_hours": 0.0, "oil_wear_percent": 0.0, "idle_ratio": 0.0}

        total_duration_sec = n_samples * dt
        k_temp = self._get_temperature_multiplier()

        # 1. Детекция холостого хода (пробок): скорость < 0.5 м/с (~1.8 км/ч)
        idle_mask = speeds_mps < 0.5
        idle_samples = np.sum(idle_mask)
        idle_ratio = float(idle_samples / n_samples)
        
        # На холостом ходу в пробке охлаждение картера минимально: штраф 1.4x
        k_traffic = np.where(idle_mask, 1.4, 1.0)

        # 2. Детекция динамических рывков (высокие нагрузки на масляную пленку)
        # Порог комфортного ускорения: 1.2 м/с^2. Свыше 1.8 м/с^2 — динамический стресс
        stress_mask = horizontal_acc > 1.8
        # Чем выше перегрузка, тем сильнее локальный сдвиговый стресс
        k_stress = np.where(stress_mask, 1.0 + (horizontal_acc - 1.8) * 0.5, 1.0)

        # 3. Интеграл эквивалентных секунд работы
        equivalent_seconds_array = dt * k_traffic * k_stress * k_temp
        total_equivalent_seconds = float(np.sum(equivalent_seconds_array))
        
        equivalent_hours = total_equivalent_seconds / 3600.0
        wear_percent = (equivalent_hours / self.BASE_OIL_LIFETIME_HOURS) * 100.0

        return {
            "duration_hours": round(total_duration_sec / 3600.0, 4),
            "equivalent_engine_hours": round(equivalent_hours, 4),
            "oil_wear_percent": round(wear_percent, 5),
            "idle_ratio": round(idle_ratio, 3)
        }