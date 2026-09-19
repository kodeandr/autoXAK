"""
Вычислительный модуль трибологической деградации моторного масла (WearEngine).
Реализует модели:
- Tripathi & Vinu [SRC-01] (Аррениус, энергия активации E_a);
- Zhang & Spikes [SRC-02] (Механохимия ZDDP, Белл-Эйринг);
- Chen, Gu & Tian [SRC-04] (Степенной динамический стресс ЦПГ gamma = 1.85);
- Usman et al. [SRC-05] (Холостой ход и разжижение топлива K_idle = 1.55).
"""
import numpy as np
from app.models.vehicle_profiles import VehiclePhysicalProfile

R_GAS = 8.314462  # Универсальная газовая постоянная, Дж/(моль*К)

class WearEngine:
    def __init__(self, profile: VehiclePhysicalProfile):
        self.profile = profile
        self.oil = profile.oil_profile

    def calculate_effective_temperature(self, speed_mps: np.ndarray, ax_mps2: np.ndarray) -> np.ndarray:
        """
        Оценка квазистатической температуры масляной пленки в зоне поршневых канавок.
        T_base = 95 C (368.15 K). При высоких нагрузках фиксируется перегрев до 125-135 C.
        """
        t_base_k = 368.15
        # Нагрузка пропорциональна кинетической мощности
        power_proxy = np.maximum(0.0, ax_mps2) * speed_mps
        temp_delta = np.clip(power_proxy * 0.45, 0.0, 40.0)
        return t_base_k + temp_delta

    def calculate_arrhenius_factor(self, temp_kelvin: np.ndarray, current_oil_life_ratio: float) -> np.ndarray:
        """
        Температурный множитель окисления базового масла Tripathi & Vinu [SRC-01].
        При выработке ресурса > 50% происходит переключение на E_a сработавшегося масла.
        """
        e_a = self.oil.base_activation_energy_jmol if current_oil_life_ratio > 0.5 else self.oil.aged_activation_energy_jmol
        t_nom_k = 368.15  # Номинальная рабочая температура (95 °C)
        
        # K_arr = exp((E_a / R) * (1/T_nom - 1/T_eff))
        exponent = (e_a / R_GAS) * ((1.0 / t_nom_k) - (1.0 / temp_kelvin))
        return np.exp(np.clip(exponent, -2.0, 5.0))

    def calculate_zddp_mechanochemical_stress(self, ax_mps2: np.ndarray, temp_kelvin: np.ndarray) -> np.ndarray:
        """
        Модель Белла-Эйринга для механохимического срыва трибопленки ZDDP (Zhang & Spikes [SRC-02]).
        При a_x > 1.8 м/с^2 контактные напряжения сдвига tau достигают 140-250 МПа,
        снижая барьер активации E_0 на tau * Delta V_act * N_A.
        """
        # Оценка гидродинамического напряжения сдвига в кольцах через индикаторную нагрузку
        tau_base_pa = 50.0e6  # 50 МПа в номинальном режиме
        acc_excess = np.maximum(0.0, np.abs(ax_mps2) - 1.8)
        tau_shear_pa = tau_base_pa + acc_excess * 65.0e6  # До 245 МПа при a_x = 4.8 м/с^2
        
        # Энергетический сдвиг барьера: work = tau * Delta V_act * N_A (Дж/моль)
        n_a = 6.02214076e23
        mechanical_work_jmol = tau_shear_pa * self.oil.zddp_activation_volume_m3 * n_a
        
        # Эффективный барьер активации
        e_effective = np.maximum(10000.0, self.oil.zddp_activation_energy_jmol - mechanical_work_jmol)
        
        # Относительное ускорение реакции синтеза/разрушения ZDDP
        k_zddp = np.exp((self.oil.zddp_activation_energy_jmol - e_effective) / (R_GAS * temp_kelvin))
        return np.clip(k_zddp, 1.0, 15.0)

    def calculate_dynamic_exponent_wear(self, ax_mps2: np.ndarray) -> np.ndarray:
        """
        Степенная нелинейность контактного износа по Chen, Gu & Tian [SRC-04]:
        gamma = 1.85 при пороге динамического стресса a_norm = 1.2 м/с^2.
        """
        a_norm = 1.2
        gamma = 1.85
        acc_ratio = np.maximum(0.0, np.abs(ax_mps2) / a_norm)
        # Прирост износа выше базовой единицы
        stress_penalty = np.where(np.abs(ax_mps2) > a_norm, (acc_ratio ** gamma) - 1.0, 0.0)
        return stress_penalty

    def evaluate_trip_wear(self, dt: float, speed_mps: np.ndarray, ax_mps2: np.ndarray, 
                           current_life_pct: float = 100.0) -> dict:
        """
        Интегральное накопление повреждений по правилу Пальмгрена-Майнера [SRC-01, SRC-04].
        dt: временной шаг дискретизации (с)
        """
        n_points = len(speed_mps)
        if n_points == 0:
            return {"oil_wear_percent": 0.0, "equivalent_hours": 0.0}

        temp_k = self.calculate_effective_temperature(speed_mps, ax_mps2)
        k_arr = self.calculate_arrhenius_factor(temp_k, current_life_pct / 100.0)
        k_zddp = self.calculate_zddp_mechanochemical_stress(ax_mps2, temp_k)
        dyn_penalty = self.calculate_dynamic_exponent_wear(ax_mps2)
        
        # Штраф затора Usman et al. [SRC-05]: разжижение топливом K_idle = 1.55 при v < 0.5 м/с
        idle_mask = speed_mps < 0.5
        k_idle = np.where(idle_mask, 1.55, 1.0)
        
        # Множитель тяжести условий для каждого отсчета времени
        severity_multiplier = (k_idle + dyn_penalty) * (0.7 * k_arr + 0.3 * k_zddp)
        
        # Эквивалентные моточасы наработки за поездку (часы)
        equiv_seconds = np.sum(severity_multiplier * dt)
        equiv_hours = equiv_seconds / 3600.0
        
        # Процент выработки ресурса масла от базового лимита T_nom
        wear_percent = (equiv_hours / self.oil.nominal_service_hours) * 100.0
        
        return {
            "oil_wear_percent": float(wear_percent),
            "equivalent_hours": float(equiv_hours),
            "physical_hours": float((n_points * dt) / 3600.0),
            "idle_duration_sec": float(np.sum(idle_mask) * dt)
        }