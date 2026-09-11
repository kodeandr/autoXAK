import numpy as np
from typing import Dict

class WearEngine:
    BASE_OIL_LIFETIME_HOURS = 250.0

    def __init__(self, ambient_temp_c: float = 20.0):
        self.ambient_temp = ambient_temp_c

    def _get_temperature_multiplier(self) -> float:
        if self.ambient_temp < 0.0:
            return 1.25
        elif self.ambient_temp > 30.0:
            return 1.15
        return 1.0

    def compute_oil_wear(
        self, 
        speeds_mps: np.ndarray, 
        horizontal_acc: np.ndarray, 
        traffic_score: int = 5,
        dt: float = 0.02
    ) -> Dict[str, float]:
        n_samples = len(speeds_mps)
        if n_samples == 0:
            return {"equivalent_engine_hours": 0.0, "oil_wear_percent": 0.0, "idle_ratio": 0.0}

        total_duration_sec = n_samples * dt
        k_temp = self._get_temperature_multiplier()

        # 1. Режим холостого хода (скорость < 0.5 м/с)
        idle_mask = speeds_mps < 0.5
        idle_samples = np.sum(idle_mask)
        idle_ratio = float(idle_samples / n_samples)
        
        # На холостых обороты ~800 RPM (база 1.0x), но в заторе 2GIS учитывается тепловой застой
        k_idle = 1.0 + (traffic_score / 10.0) * 0.35

        # 2. Режим движения: учет базовой передачи гидротрансформатора/робота и ускорений
        # Базовый множитель включенной передачи при ненулевой скорости: ДВС работает в диапазоне 1400-1800 RPM (множитель ~1.8-2.0)
        # Динамическая составляющая тяги: резкий разгон дополнительно поднимает обороты до 2500+ RPM
        base_cruise_load = 1.75 + (speeds_mps / 15.0) * 0.45
        traction_stress = np.maximum(0.0, horizontal_acc) * 1.15
        k_motion = base_cruise_load + traction_stress

        # 3. Результирующий стресс-фактор
        combined_stress = np.where(idle_mask, k_idle, k_motion)

        # Интегрирование моточасов
        equivalent_seconds_array = dt * combined_stress * k_temp
        total_equivalent_seconds = float(np.sum(equivalent_seconds_array))
        
        equivalent_hours = total_equivalent_seconds / 3600.0
        wear_percent = (equivalent_hours / self.BASE_OIL_LIFETIME_HOURS) * 100.0

        return {
            "duration_hours": round(total_duration_sec / 3600.0, 4),
            "equivalent_engine_hours": round(equivalent_hours, 5),
            "oil_wear_percent": round(wear_percent, 5),
            "idle_ratio": round(idle_ratio, 3)
        }