"""
Вычислительный модуль трибологической деградации моторного масла (WearEngine).
Реализует академические модели:
- Tripathi & Vinu [SRC-01] (Аррениус, энергия активации E_a);
- Zhang & Spikes [SRC-02] (Механохимия ZDDP, Белл-Эйринг);
- Chen, Gu & Tian [SRC-04] (Степенной динамический стресс ЦПГ gamma = 1.85);
- Usman et al. [SRC-05] (Холостой ход и разжижение топлива K_idle = 1.55).
"""
from typing import Optional, Dict, Any
import numpy as np

try:
    from app.models.vehicle_profiles import VehiclePhysicalProfile
except ImportError:
    VehiclePhysicalProfile = None

R_GAS = 8.314462  # Универсальная газовая постоянная, Дж/(моль*К)


class DefaultOilProfile:
    """Верифицированные физико-химические константы масел (Tripathi & Vinu, Zhang & Spikes)."""
    base_activation_energy_jmol: float = 106170.0  # E_a свежей синтетики (106.17 кДж/моль)
    aged_activation_energy_jmol: float = 91900.0   # E_a окисленного масла (91.9 кДж/моль)
    zddp_activation_volume_m3: float = 1.8e-28     # Активационный объем ZDDP (0.18 нм^3)
    zddp_activation_energy_jmol: float = 53000.0   # Энергия активации ZDDP (53 кДж/моль)
    nominal_service_hours: float = 250.0          # Базовый ресурс T_nom (моточасы)


class DefaultVehicleProfile:
    oil_profile = DefaultOilProfile()
    mass_kg: float = 1500.0
    drag_coefficient: float = 0.31
    frontal_area_m2: float = 2.2


class WearEngine:
    BASE_OIL_LIFETIME_HOURS = 250.0
    
    def __init__(self, profile: Optional[Any] = None, ambient_temp_c: float = 20.0, **kwargs):
        self.ambient_temp_c = ambient_temp_c
        
        if profile is not None:
            self.profile = profile
            self.oil = getattr(profile, "oil_profile", profile)
        else:
            # Безопасная инициализация профиля по умолчанию
            try:
                from app.models.vehicle_profiles import VehiclePhysicalProfile
                self.profile = VehiclePhysicalProfile()
                self.oil = self.profile.oil_profile
            except Exception:
                self.profile = DefaultVehicleProfile()
                self.oil = self.profile.oil_profile

    def calculate_effective_temperature(self, speed_mps: np.ndarray, ax_mps2: np.ndarray) -> np.ndarray:
        """
        Оценка квазистатической температуры масляной пленки в зоне поршневых канавок.
        T_base = 95 C (368.15 K). При высоких нагрузках фиксируется перегрев до 125-135 C.
        """
        t_base_k = 368.15
        power_proxy = np.maximum(0.0, ax_mps2) * speed_mps
        temp_delta = np.clip(power_proxy * 0.45, 0.0, 40.0)
        return t_base_k + temp_delta

    def calculate_arrhenius_factor(self, temp_kelvin: np.ndarray, current_oil_life_ratio: float) -> np.ndarray:
        """
        Температурный множитель окисления базового масла Tripathi & Vinu [SRC-01].
        """
        e_a = (
            self.oil.base_activation_energy_jmol 
            if current_oil_life_ratio > 0.5 
            else self.oil.aged_activation_energy_jmol
        )
        t_nom_k = 368.15  # Номинальная рабочая температура (95 °C)
        
        exponent = (e_a / R_GAS) * ((1.0 / t_nom_k) - (1.0 / temp_kelvin))
        return np.exp(np.clip(exponent, -2.0, 5.0))

    def calculate_zddp_mechanochemical_stress(self, ax_mps2: np.ndarray, temp_kelvin: np.ndarray) -> np.ndarray:
        """
        Модель Белла-Эйринга для механохимического срыва трибопленки ZDDP (Zhang & Spikes [SRC-02]).
        """
        tau_base_pa = 50.0e6
        acc_excess = np.maximum(0.0, np.abs(ax_mps2) - 1.8)
        tau_shear_pa = tau_base_pa + acc_excess * 65.0e6
        
        n_a = 6.02214076e23
        mechanical_work_jmol = tau_shear_pa * self.oil.zddp_activation_volume_m3 * n_a
        
        e_effective = np.maximum(10000.0, self.oil.zddp_activation_energy_jmol - mechanical_work_jmol)
        k_zddp = np.exp((self.oil.zddp_activation_energy_jmol - e_effective) / (R_GAS * temp_kelvin))
        return np.clip(k_zddp, 1.0, 15.0)

    def calculate_dynamic_exponent_wear(self, ax_mps2: np.ndarray) -> np.ndarray:
        """
        Степенная нелинейность контактного износа по Chen, Gu & Tian [SRC-04]: gamma = 1.85.
        """
        a_norm = 1.2
        gamma = 1.85
        acc_ratio = np.maximum(0.0, np.abs(ax_mps2) / a_norm)
        stress_penalty = np.where(np.abs(ax_mps2) > a_norm, (acc_ratio ** gamma) - 1.0, 0.0)
        return stress_penalty

    def evaluate_trip_wear(
        self, 
        dt: float, 
        speed_mps: np.ndarray, 
        ax_mps2: np.ndarray, 
        current_life_pct: float = 100.0
    ) -> dict:
        """
        Интегральное накопление повреждений по правилу Пальмгрена-Майнера [SRC-01, SRC-04].
        """
        n_points = len(speed_mps)
        if n_points == 0:
            return {
                "oil_wear_percent": 0.0, 
                "equivalent_hours": 0.0, 
                "physical_hours": 0.0, 
                "idle_duration_sec": 0.0
            }

        temp_k = self.calculate_effective_temperature(speed_mps, ax_mps2)
        k_arr = self.calculate_arrhenius_factor(temp_k, current_life_pct / 100.0)
        k_zddp = self.calculate_zddp_mechanochemical_stress(ax_mps2, temp_k)
        dyn_penalty = self.calculate_dynamic_exponent_wear(ax_mps2)
        
        # Штраф затора Usman et al. [SRC-05]: K_idle = 1.55 при v < 0.5 м/с
        idle_mask = speed_mps < 0.5
        k_idle = np.where(idle_mask, 1.55, 1.0)
        
        severity_multiplier = (k_idle + dyn_penalty) * (0.7 * k_arr + 0.3 * k_zddp)
        
        equiv_seconds = np.sum(severity_multiplier * dt)
        equiv_hours = equiv_seconds / 3600.0
        wear_percent = (equiv_hours / self.oil.nominal_service_hours) * 100.0
        
        return {
            "oil_wear_percent": float(wear_percent),
            "equivalent_hours": float(equiv_hours),
            "physical_hours": float((n_points * dt) / 3600.0),
            "idle_duration_sec": float(np.sum(idle_mask) * dt)
        }

    def compute_oil_wear(
        self, 
        speeds_mps: np.ndarray, 
        horizontal_acc: np.ndarray, 
        traffic_score: int = 5,
        dt: float = 0.02,
        current_life_pct: float = 100.0
    ) -> Dict[str, float]:
        """
        Совместимый интерфейс контроллеров autoXAK.
        Транслирует вызовы из API в физико-химическую модель evaluate_trip_wear.
        """
        n_points = len(speeds_mps)
        if n_points == 0:
            return {
                "duration_hours": 0.0,
                "equivalent_engine_hours": 0.0,
                "equivalent_hours": 0.0,
                "oil_wear_percent": 0.0,
                "idle_ratio": 0.0
            }

        res = self.evaluate_trip_wear(
            dt=dt,
            speed_mps=speeds_mps,
            ax_mps2=horizontal_acc,
            current_life_pct=current_life_pct
        )

        total_time_sec = n_points * dt
        idle_ratio = float(res["idle_duration_sec"] / total_time_sec) if total_time_sec > 0 else 0.0

        return {
            "duration_hours": round(res["physical_hours"], 4),
            "equivalent_engine_hours": round(res["equivalent_hours"], 5),
            "equivalent_hours": round(res["equivalent_hours"], 5),
            "oil_wear_percent": round(res["oil_wear_percent"], 5),
            "idle_ratio": round(idle_ratio, 3)
        }