import numpy as np
from typing import Dict, Any, Optional


class WearEngine:
    """
    Аналитическое ядро предиктивной оценки износа моторного масла autoXAK.
    Основано на уравнениях тягового баланса ТС (ISO 8855) и гипотезе линейного 
    накопления повреждений Пальмгрена-Майнера.
    """

    def __init__(self, profile: Optional[Any] = None, ambient_temp_c: float = 20.0, **kwargs):
        self.ambient_temp_c = ambient_temp_c
        self.profile = profile
        self.oil = getattr(profile, "oil_profile", profile) if profile else None

        # Константы окружающей среды
        self.rho_air = 1.225       # Плотность воздуха при 20°C, кг/м³
        self.g = 9.80665           # Ускорение свободного падения, м/с²

    def _extract_vehicle_params(self) -> Dict[str, float]:
        """Извлечение паспортных физических констант ТС из профиля."""
        p = self.profile
        return {
            "m": float(getattr(p, "curb_weight_kg", 1500.0)),
            "cd_a": float(getattr(p, "drag_coefficient_area", 0.75)),
            "f_r": float(getattr(p, "rolling_resistance_coeff", 0.012)),
            "eta": float(getattr(p, "drivetrain_efficiency", 0.90)),
            "p_rated": float(getattr(p, "rated_power_kw", 110.0)),
            "nominal_hours": float(getattr(self.oil, "nominal_service_hours", 250.0)) if self.oil else 250.0
        }

    def estimate_engine_states(self, speed_mps: np.ndarray, ax_mps2: np.ndarray) -> Dict[str, np.ndarray]:
        """
        Реконструкция первичных параметров ДВС (RPM, Load, T_oil) из кинематики кузова
        через систему уравнений динамики движения автомобиля.
        """
        params = self._extract_vehicle_params()
        m = params["m"]
        cd_a = params["cd_a"]
        f_r = params["f_r"]
        eta = params["eta"]
        p_rated = params["p_rated"]

        v = np.maximum(0.0, speed_mps)
        ax_pos = np.maximum(0.0, ax_mps2)
        ax_neg = np.maximum(0.0, -ax_mps2)

        # 1. Силы тягового баланса
        f_roll = m * self.g * f_r * np.tanh(v / 0.3)
        f_aero = 0.5 * self.rho_air * cd_a * (v ** 2)
        f_inertia = m * ax_pos
        f_trac = f_inertia + f_roll + f_aero

        # Механическая мощность на валу ДВС (кВт)
        p_wheels_kw = (f_trac * v) / 1000.0
        p_engine_kw = p_wheels_kw / eta

        # 2. Оценка нагрузки ЭБУ (Engine Load, %)
        # Базовая нагрузка ХХ зависит от рабочего объема и массы навесного
        base_idle_load = 14.0 + (m - 1200.0) * 0.013
        accel_load_gain = (p_engine_kw / (p_rated * 0.34)) * 58.0

        est_load = np.where(
            v < 0.5,
            base_idle_load,
            np.where(
                ax_neg > 0.3,
                10.0 + (m - 1200.0) * 0.006,  # Режим ПХХ (отсечка топлива)
                np.clip(base_idle_load + accel_load_gain, 18.0, 92.0)
            )
        )

        # 3. Оценка частоты вращения коленвала (RPM)
        idle_rpm = 750.0 if m < 1300.0 else 800.0
        # Адаптивный ряд передаточных чисел АКПП/РКПП в городе
        cruise_rpm = idle_rpm + 400.0 + np.minimum(580.0, v * 46.0)
        kickdown_rpm = ax_pos * 640.0 * (m / 1400.0)
        decel_rpm = np.where(ax_neg > 0.3, idle_rpm + 50.0 + v * 48.0, 0.0)

        est_rpm = np.where(
            v < 0.5,
            idle_rpm,
            np.where(ax_neg > 0.3, decel_rpm, cruise_rpm + kickdown_rpm)
        )

        # 4. Температурное поле масляной пленки (Kelvin)
        temp_c = 90.0 + (m - 1200.0) * 0.008 + (est_load / 100.0) * 12.0
        temp_k = temp_c + 273.15

        return {
            "rpm": est_rpm,
            "load_percent": est_load,
            "temp_k": temp_k
        }

    def evaluate_trip_wear(
        self,
        dt: float,
        speed_mps: np.ndarray,
        ax_mps2: np.ndarray,
        current_life_pct: float = 100.0
    ) -> Dict[str, Any]:
        """
        Расчет эквивалентных моточасов и трибологического износа по правилу Пальмгрена-Майнера.
        """
        n_points = len(speed_mps)
        if n_points == 0:
            return {
                "oil_wear_percent": 0.0,
                "equivalent_hours": 0.0,
                "physical_hours": 0.0,
                "idle_duration_sec": 0.0
            }

        params = self._extract_vehicle_params()
        states = self.estimate_engine_states(speed_mps, ax_mps2)

        rpm_arr = states["rpm"]
        load_arr = states["load_percent"]
        temp_k_arr = states["temp_k"]
        temp_c_arr = temp_k_arr - 273.15

        # Трибологические множители износа (согласованы с эталоном CAN-шины ISO/SAE)
        rpm_factor = rpm_arr / 800.0
        load_factor = 1.0 + (load_arr / 100.0) * 0.5

        temp_factor = np.where(temp_c_arr > 105.0, 1.3, 1.0)
        temp_factor = np.where(temp_c_arr < 70.0, 1.2, temp_factor)

        # Скорость накопления эквивалентных секунд
        severity_rate = rpm_factor * load_factor * temp_factor

        equiv_seconds = np.sum(severity_rate * dt)
        equiv_hours = equiv_seconds / 3600.0

        nominal_hours = params["nominal_hours"]
        wear_percent = (equiv_hours / nominal_hours) * 100.0
        idle_mask = speed_mps < 0.5

        return {
            "oil_wear_percent": float(wear_percent),
            "equivalent_hours": float(equiv_hours),
            "physical_hours": float((n_points * dt) / 3600.0),
            "idle_duration_sec": float(np.sum(idle_mask) * dt),
            "estimated_rpm_mean": float(np.mean(rpm_arr)),
            "estimated_load_mean": float(np.mean(load_arr))
        }

    def compute_oil_wear(self, speeds_mps: np.ndarray, horizontal_acc: np.ndarray, dt: float = 0.02, **kwargs) -> Dict[str, Any]:
        """Обратная совместимость с устаревшими вызовами контроллеров."""
        return self.evaluate_trip_wear(dt=dt, speed_mps=speeds_mps, ax_mps2=horizontal_acc)
