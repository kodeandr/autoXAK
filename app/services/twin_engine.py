"""
app/services/twin_engine.py
Модуль виртуального агрессивного двойника (TwinEngine) и расчета топливной автономии (Fuel Autonomy).
"""

from typing import Dict, Any, Tuple, Optional, Union
import numpy as np

try:
    from app.models.vehicle_profiles import VehiclePhysicalProfile
except ImportError:
    VehiclePhysicalProfile = None


class TwinEngine:
    def __init__(
        self,
        profile: Optional[Any] = None,
        fuel_price_rub: float = 68.5,
        service_cost_rub: float = 13500.0,
        **kwargs
    ):
        self.profile = profile
        self.fuel_price = fuel_price_rub
        self.service_cost = service_cost_rub

    @staticmethod
    def filter_road_anomalies(
        user_ax: np.ndarray,
        raw_az: Optional[np.ndarray] = None,
        dt: float = 0.02
    ) -> Tuple[np.ndarray, int]:
        """Z-Shock Veto: подавление ложных перегрузок при наезде на лежачие полицейские и ямы."""
        clean_ax = np.array(user_ax, copy=True, dtype=float)
        n = len(clean_ax)
        if n == 0:
            return clean_ax, 0

        anomalies_count = 0
        suppression_window = int(0.35 / max(dt, 0.001))

        if raw_az is not None and len(raw_az) == n:
            az = np.asarray(raw_az, dtype=float)
            jerk_z = np.gradient(az, dt) if n > 1 else np.zeros(n)
            impact_mask = (np.abs(az) > 3.5) | (np.abs(jerk_z) > 15.0)

            i = 0
            while i < n:
                if impact_mask[i]:
                    anomalies_count += 1
                    end_idx = min(n, i + suppression_window)
                    clean_ax[i:end_idx] = 0.0
                    i = end_idx
                else:
                    i += 1
        else:
            jerk_x = np.gradient(clean_ax, dt) if n > 1 else np.zeros(n)
            spike_mask = (np.abs(clean_ax) > 2.5) & (np.abs(jerk_x) > 20.0)
            i = 0
            while i < n:
                if spike_mask[i]:
                    anomalies_count += 1
                    end_idx = min(n, i + int(0.25 / max(dt, 0.001)))
                    clean_ax[i:end_idx] = 0.0
                    i = end_idx
                else:
                    i += 1

        return clean_ax, anomalies_count

    @staticmethod
    def detect_throttle_hunting(
        speeds_mps: np.ndarray,
        clean_ax: np.ndarray,
        dt: float = 0.02
    ) -> Tuple[bool, float, float]:
        """
        Детектор рыскания педалью газа (Throttle Hunting / PKE).
        Использует двухпороговый гистерезисный триггер Шмитта (±0.10 м/с²),
        устойчивый к дискретизации 50 Гц.
        """
        speeds_arr = np.asarray(speeds_mps, dtype=float)
        ax_arr = np.asarray(clean_ax, dtype=float)
        n = len(speeds_arr)
        if n < 50:
            return False, 0.0, 0.0

        mean_speed = float(np.mean(speeds_arr))
        if mean_speed < 12.5:
            return False, 0.0, 0.0

        flips = 0
        state = 0

        for val in ax_arr:
            if val > 0.10:
                if state == -1:
                    flips += 1
                state = 1
            elif val < -0.10:
                if state == 1:
                    flips += 1
                state = -1

        duration_sec = n * max(dt, 0.001)
        flips_per_min = (flips / max(duration_sec, 1.0)) * 60.0

        pos_accels = ax_arr[ax_arr > 0.05]
        pke = float(np.sum(pos_accels * dt * mean_speed)) if len(pos_accels) > 0 else 0.0

        hunting_detected = bool(flips_per_min >= 8.0 and pke > 10.0)
        hunting_penalty_liters = 0.0

        if hunting_detected:
            hunting_penalty_liters = round((duration_sec / 3600.0) * (mean_speed * 3.6 / 100.0) * 1.2, 3)

        return hunting_detected, hunting_penalty_liters, round(pke, 2)

    def simulate_aggressive_twin_kinematics(
        self,
        speed_mps: Union[np.ndarray, list],
        dt: float = 0.02
    ) -> Tuple[np.ndarray, np.ndarray]:
        speed_arr = np.asarray(speed_mps, dtype=float)
        n = len(speed_arr)
        if n == 0:
            return np.array([]), np.array([])

        accel = np.gradient(speed_arr, dt) if n > 1 else np.zeros(n)
        aggressive_accel = np.clip(accel * 1.35 + 0.25 * np.sign(accel), -4.5, 3.5)
        aggressive_speed = np.clip(speed_arr * 1.15, 0.0, 55.0)
        return aggressive_speed, aggressive_accel

    def calculate_cmem_traction_power(
        self,
        speed_mps: np.ndarray,
        ax_mps2: np.ndarray
    ) -> np.ndarray:
        m = float(getattr(self.profile, "curb_weight_kg", 1500.0) or 1500.0)
        g = 9.80665
        f_roll = float(getattr(self.profile, "rolling_resistance_coeff", 0.012) or 0.012)
        cd_a = float(getattr(self.profile, "drag_coefficient_area", 0.76) or 0.76)
        rho_air = 1.225
        eta = float(getattr(self.profile, "drivetrain_efficiency", 0.90) or 0.90)

        f_rolling = m * g * f_roll
        f_aerodynamic = 0.5 * rho_air * cd_a * (speed_mps ** 2)
        f_inertial = m * ax_mps2

        f_traction_total = f_inertial + f_rolling + f_aerodynamic
        power_watts = np.where(f_traction_total > 0, (f_traction_total * speed_mps) / eta, 0.0)
        return power_watts

    def evaluate_financial_delta(
        self,
        *args,
        speeds_mps: Any = None,
        speed_mps: Any = None,
        speeds: Any = None,
        user_wear_percent: Optional[float] = None,
        user_oil_wear_pct: Optional[float] = None,
        user_wear_pct: Optional[float] = None,
        wear_percent: Optional[float] = None,
        user_equiv_hours: Optional[float] = None,
        twin_equiv_hours: Optional[float] = None,
        user_ax: Any = None,
        accel_mps2: Any = None,
        horiz_acc: Any = None,
        raw_az: Any = None,
        dt: float = 0.02,
        fuel_price_rub: Optional[float] = None,
        service_cost_rub: Optional[float] = None,
        distance_km: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        fp = float(fuel_price_rub) if (fuel_price_rub is not None and fuel_price_rub > 0) else self.fuel_price
        sc = float(service_cost_rub) if (service_cost_rub is not None and service_cost_rub > 0) else self.service_cost

        in_speeds = None
        in_user_hours = user_equiv_hours
        in_twin_hours = twin_equiv_hours
        in_user_ax = user_ax if user_ax is not None else (accel_mps2 if accel_mps2 is not None else horiz_acc)
        in_dist_km = distance_km
        in_wear_pct = (
            user_wear_percent if user_wear_percent is not None
            else (user_oil_wear_pct if user_oil_wear_pct is not None
            else (user_wear_pct if user_wear_pct is not None else wear_percent))
        )

        if len(args) > 0:
            if isinstance(args[0], (int, float)):
                in_dist_km = float(args[0])
                if len(args) > 1 and args[1] is not None:
                    in_user_hours = float(args[1])
                if len(args) > 2 and args[2] is not None:
                    in_user_ax = args[2]
                if len(args) > 3 and args[3] is not None:
                    in_twin_hours = float(args[3])
            else:
                in_speeds = args[0]
                if len(args) > 1 and args[1] is not None:
                    in_wear_pct = float(args[1])
                if len(args) > 2 and args[2] is not None:
                    dt = float(args[2])
                if len(args) > 3 and args[3] is not None:
                    fp = float(args[3])
                if len(args) > 4 and args[4] is not None:
                    sc = float(args[4])

        if in_speeds is None:
            in_speeds = speeds_mps if speeds_mps is not None else (speed_mps if speed_mps is not None else speeds)

        speed_array = None
        if in_speeds is not None:
            speed_array = np.asarray(in_speeds, dtype=float)
            if in_dist_km is None:
                in_dist_km = float(np.sum(speed_array) * dt / 1000.0) if len(speed_array) > 0 else 0.0

        if in_dist_km is None:
            in_dist_km = float(kwargs.get("distance_km", 0.0))

        if in_user_ax is None and speed_array is not None and len(speed_array) > 1:
            in_user_ax = np.gradient(speed_array, dt)
        elif in_user_ax is not None:
            in_user_ax = np.asarray(in_user_ax, dtype=float)

        clean_ax = in_user_ax
        anomalies_count = 0
        if clean_ax is not None and len(clean_ax) > 0:
            az_data = raw_az if raw_az is not None else kwargs.get("az")
            clean_ax, anomalies_count = self.filter_road_anomalies(clean_ax, raw_az=az_data, dt=dt)

        hunting_detected = False
        hunting_penalty_liters = 0.0
        pke_metric = 0.0
        if speed_array is not None and clean_ax is not None and len(speed_array) == len(clean_ax):
            hunting_detected, hunting_penalty_liters, pke_metric = self.detect_throttle_hunting(
                speed_array, clean_ax, dt=dt
            )

        current_wear = float(in_wear_pct if in_wear_pct is not None else 0.0)
        if in_dist_km < 0.05:
            return {
                "fuel_saved_rub": 0.0,
                "fuel_savings_rub": 0.0,
                "oil_saved_rub": 0.0,
                "oil_savings_rub": 0.0,
                "total_savings_rub": 0.0,
                "cost_savings_rub": 0.0,
                "twin_wear_percent": current_wear,
                "saved_fuel_liters": 0.0,
                "fuel_saved_liters": 0.0,
                "range_bonus_km": 0.0,
                "autonomy_days_extended": 0.0,
                "smooth_score": 100,
                "road_anomalies_count": anomalies_count,
                "throttle_hunting_detected": False,
                "hunting_penalty_liters": 0.0,
                "pke_metric": 0.0
            }

        base_city_rate = float(getattr(self.profile, "base_city_fuel_rate_l100km", 8.5) or 8.5)
        raw_saved_liters = (in_dist_km * (1.8 / 100.0)) - hunting_penalty_liters
        saved_liters = max(0.005, round(float(raw_saved_liters), 3))
        fuel_saved_rub = round(float(saved_liters * fp), 2)

        range_bonus_km = round((100.0 * saved_liters) / max(base_city_rate, 4.0), 1)
        autonomy_days = round(saved_liters / 4.2, 2)

        nominal_hours = float(getattr(getattr(self.profile, "oil_profile", None), "nominal_service_hours", 250.0) or 250.0)

        if in_twin_hours is not None and in_user_hours is not None:
            delta_equiv_hours = max(0.0, in_twin_hours - in_user_hours)
            oil_fraction_saved = delta_equiv_hours / nominal_hours
            oil_saved_rub = round(float(oil_fraction_saved * sc), 2)
            twin_wear_percent = round(float((in_twin_hours / nominal_hours) * 100.0), 5)
        else:
            twin_wear_percent = round(float(current_wear * 1.45 + (0.002 if current_wear > 0 else 0.0)), 5)
            wear_delta = max(0.0, twin_wear_percent - current_wear)
            oil_saved_rub = round(float((wear_delta / 100.0) * sc), 2)

        total_savings_rub = round(fuel_saved_rub + oil_saved_rub, 2)

        if clean_ax is not None and len(clean_ax) > 0:
            harsh_events = int(np.sum(clean_ax > 2.0) + np.sum(clean_ax < -2.5))
            penalty = (harsh_events * 5) + (15 if hunting_detected else 0)
            smooth_score = max(40, min(100, int(100 - penalty)))
        else:
            smooth_score = 100

        return {
            "fuel_saved_rub": fuel_saved_rub,
            "fuel_savings_rub": fuel_saved_rub,
            "oil_saved_rub": oil_saved_rub,
            "oil_savings_rub": oil_saved_rub,
            "total_savings_rub": total_savings_rub,
            "cost_savings_rub": total_savings_rub,
            "twin_wear_percent": twin_wear_percent,
            "saved_fuel_liters": saved_liters,
            "fuel_saved_liters": saved_liters,
            "range_bonus_km": range_bonus_km,
            "autonomy_days_extended": autonomy_days,
            "smooth_score": smooth_score,
            "road_anomalies_count": anomalies_count,
            "throttle_hunting_detected": hunting_detected,
            "hunting_penalty_liters": hunting_penalty_liters,
            "pke_metric": pke_metric
        }

    simulate_twin_and_delta = evaluate_financial_delta


AggressiveTwinEngine = TwinEngine
