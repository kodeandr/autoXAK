import numpy as np
from typing import Dict

class AggressiveTwinEngine:
    # Константы для типового бензинового турбо-автомобиля 2.0 TSI (масса ~1600 кг)
    CAR_MASS_KG = 1600.0
    ROLLING_RESISTANCE = 0.015
    DRAG_COEFFICIENT = 0.32
    FRONTAL_AREA_M2 = 2.4
    AIR_DENSITY = 1.225  # кг/м^3
    FUEL_ENERGY_DENSITY_MJ_PER_LITER = 32.0  # Энергия 1 литра бензина АИ-95
    ENGINE_EFFICIENCY = 0.30  # КПД современного бензинового ДВС
    FUEL_PRICE_RUB = 62.0  # Цена за 1 литр топлива (рубли)
    OIL_SERVICE_COST_RUB = 9500.0  # Стоимость регламентного ТО (масло + фильтр + работа)

    def simulate_twin_and_delta(
        self, 
        speeds_mps: np.ndarray, 
        user_wear_percent: float, 
        dt: float = 0.02
    ) -> Dict[str, float]:
        """
        Расчет расхода топлива реального пользователя vs синтетического агрессивного двойника.
        """
        n_samples = len(speeds_mps)
        if n_samples < 2:
            return {"fuel_saved_rub": 0.0, "oil_saved_rub": 0.0, "total_savings_rub": 0.0}

        # 1. Расчет ускорений реального пользователя
        user_acc = np.diff(speeds_mps, prepend=speeds_mps[0]) / dt

        # 2. Синтез агрессивного двойника на том же скоростном треке:
        # Двойник пытается разогнаться резче (до 2.8 м/с^2) и тормозит резче (-3.5 м/с^2)
        twin_acc = np.where(user_acc > 0, np.minimum(2.8, user_acc * 1.5 + 0.5), user_acc)
        twin_acc = np.where(user_acc < 0, np.maximum(-3.5, user_acc * 1.6 - 0.5), twin_acc)

        # 3. Физическая модель тяговой мощности P = (m*a + F_roll + F_aero) * v
        def calculate_fuel_liters(acc_profile: np.ndarray) -> float:
            f_roll = self.CAR_MASS_KG * 9.81 * self.ROLLING_RESISTANCE
            f_aero = 0.5 * self.AIR_DENSITY * self.DRAG_COEFFICIENT * self.FRONTAL_AREA_M2 * (speeds_mps**2)
            f_traction = np.maximum(0.0, self.CAR_MASS_KG * acc_profile + f_roll + f_aero)
            
            power_watts = f_traction * speeds_mps
            # Учет работы двигателя на холостом ходу (P_idle ~ 2.5 кВт на работу вспомогательных систем)
            power_watts = np.where(speeds_mps < 0.5, 2500.0, power_watts)
            
            total_energy_joules = np.sum(power_watts * dt)
            effective_energy_joules = total_energy_joules / self.ENGINE_EFFICIENCY
            liters = (effective_energy_joules / 1e6) / self.FUEL_ENERGY_DENSITY_MJ_PER_LITER
            return float(liters)

        user_fuel_liters = calculate_fuel_liters(user_acc)
        twin_fuel_liters = calculate_fuel_liters(twin_acc)

        fuel_delta_liters = max(0.0, twin_fuel_liters - user_fuel_liters)
        fuel_savings_rub = fuel_delta_liters * self.FUEL_PRICE_RUB

        # 4. Расчет разницы в износе масла:
        # Двойник изнашивает масло ориентировочно в 1.6 раза быстрее за счет предельных оборотов и температур
        twin_wear_percent = user_wear_percent * 1.6
        oil_wear_delta = max(0.0, twin_wear_percent - user_wear_percent)
        oil_savings_rub = (oil_wear_delta / 100.0) * self.OIL_SERVICE_COST_RUB

        total_savings = fuel_savings_rub + oil_savings_rub

        return {
            "user_fuel_liters": round(user_fuel_liters, 3),
            "twin_fuel_liters": round(twin_fuel_liters, 3),
            "fuel_saved_liters": round(fuel_delta_liters, 3),
            "fuel_saved_rub": round(fuel_savings_rub, 2),
            "oil_saved_rub": round(oil_savings_rub, 2),
            "total_savings_rub": round(total_savings, 2)
        }