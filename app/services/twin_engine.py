"""
Тяговый баланс автомобиля и модель виртуального агрессивного двойника (TwinEngine).
Опирается на:
- Barth et al. / CMEM v3.01 [SRC-06] (Тяговый баланс продольной динамики P_tr);
- Ahn & Rakha / VT-Micro [SRC-07] (Полиномиальный расход топлива);
- Ericsson [SRC-12] & Cabrera et al. [SRC-13] (Нормативы динамического перерасхода топлива).
"""
import numpy as np
from typing import Tuple
from app.models.vehicle_profiles import VehiclePhysicalProfile

class TwinEngine:
    def __init__(self, profile: VehiclePhysicalProfile, fuel_price_rub: float = 61.50, service_cost_rub: float = 9500.0):
        self.profile = profile
        self.fuel_price = fuel_price_rub
        self.service_cost = service_cost_rub

    def calculate_cmem_traction_power(self, speed_mps: np.ndarray, ax_mps2: np.ndarray) -> np.ndarray:
        """
        Физический тяговый баланс CMEM [SRC-06]:
        P_tr = [(m*a + F_roll + F_aero) * v] / eta_trans
        """
        m = self.profile.curb_weight_kg
        g = 9.80665
        f_roll = self.profile.rolling_resistance_coeff
        cd_a = self.profile.drag_coefficient_area
        rho_air = 1.225
        eta = self.profile.drivetrain_efficiency

        f_rolling = m * g * f_roll
        f_aerodynamic = 0.5 * rho_air * cd_a * (speed_mps ** 2)
        f_inertial = m * ax_mps2

        f_traction_total = f_inertial + f_rolling + f_aerodynamic
        
        # Тяговая мощность на валу двигателя (Вт)
        power_watts = np.where(f_traction_total > 0, (f_traction_total * speed_mps) / eta, 0.0)
        return power_watts

    def simulate_aggressive_twin_kinematics(self, speed_mps: np.ndarray, dt: float) -> Tuple[np.ndarray, np.ndarray]:
        """
        Синтез кинематического профиля агрессивного двойника на идентичной траектории:
        - Предельные ускорения разгона a_twin = +2.8 м/с^2;
        - Экстренные торможения a_twin = -3.5 м/с^2;
        - Скорость ограничена скоростным режимом реального трека.
        """
        twin_speed = np.zeros_like(speed_mps)
        twin_ax = np.zeros_like(speed_mps)
        
        current_v = 0.0
        for i in range(1, len(speed_mps)):
            v_target = speed_mps[i]
            if v_target > current_v:
                # Агрессивный старт/разгон
                a = 2.8
                current_v = min(v_target, current_v + a * dt)
            elif v_target < current_v:
                # Позднее жесткое торможение
                a = -3.5
                current_v = max(v_target, current_v + a * dt)
            else:
                a = 0.0
                
            twin_speed[i] = current_v
            twin_ax[i] = a
            
        return twin_speed, twin_ax

    def evaluate_financial_delta(self, distance_km: float, user_equiv_hours: float, 
                                 user_ax: np.ndarray, twin_equiv_hours: float) -> dict:
        """
        Конвертация сохраненных ресурсов в рублевую выгоду Delta Cost.
        """
        if distance_km <= 0.01:
            return {"fuel_savings_rub": 0.0, "oil_savings_rub": 0.0, "total_savings_rub": 0.0}

        # 1. Топливная выгода Delta C_fuel (Ericsson [SRC-12], Cabrera [SRC-13])
        # Доля резких ускорений реального водителя
        n_hard = np.sum(user_ax > 1.8)
        n_total = max(1, len(user_ax))
        k_user_fuel = 1.0 + (n_hard / n_total) * 2.2
        k_twin_fuel = 1.66  # Фиксированный норматив двойника (30% резких стартов)
        
        fuel_multiplier_delta = max(0.0, k_twin_fuel - k_user_fuel)
        saved_liters = (distance_km / 100.0) * self.profile.base_city_fuel_rate_l100km * fuel_multiplier_delta
        delta_c_fuel = saved_liters * self.fuel_price

        # 2. Масляная выгода Delta C_oil (Пальмгрен-Майнер)
        # Разница выработанных эквивалентных моточасов
        delta_equiv_hours = max(0.0, twin_equiv_hours - user_equiv_hours)
        oil_life_fraction_saved = delta_equiv_hours / self.profile.oil_profile.nominal_service_hours
        delta_c_oil = oil_life_fraction_saved * self.service_cost

        return {
            "fuel_savings_rub": round(float(delta_c_fuel), 2),
            "oil_savings_rub": round(float(delta_c_oil), 2),
            "total_savings_rub": round(float(delta_c_fuel + delta_c_oil), 2),
            "saved_fuel_liters": round(float(saved_liters), 2)
        }

AggressiveTwinEngine = TwinEngine

