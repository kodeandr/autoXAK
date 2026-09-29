import numpy as np
from typing import Dict, Any, Tuple, Optional
from app.models.vehicle_profiles import get_default_profile, VehiclePhysicalProfile


class TwinEngine:
    def __init__(self, profile: Optional[VehiclePhysicalProfile] = None, **kwargs):
        self.profile = profile or get_default_profile()

    def simulate_aggressive_twin_kinematics(self, speed_mps: np.ndarray, dt: float) -> Tuple[np.ndarray, np.ndarray]:
        n = len(speed_mps)
        if n == 0:
            return np.array([]), np.array([])
        accel = np.gradient(speed_mps, dt) if n > 1 else np.zeros(n)
        aggressive_accel = np.clip(accel * 1.35 + 0.25 * np.sign(accel), -4.5, 3.5)
        aggressive_speed = np.clip(speed_mps * 1.15, 0.0, 55.0)
        return aggressive_speed, aggressive_accel

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
        dt: float = 0.02,
        fuel_price_rub: Optional[float] = 62.0,
        service_cost_rub: Optional[float] = 9500.0,
        **kwargs
    ) -> Dict[str, float]:
        # Защита от передачи None из базы данных
        fp = 62.0 if (fuel_price_rub is None or fuel_price_rub <= 0) else float(fuel_price_rub)
        sc = 9500.0 if (service_cost_rub is None or service_cost_rub <= 0) else float(service_cost_rub)

        raw_speeds = args[0] if len(args) > 0 else (speeds_mps if speeds_mps is not None else (speed_mps if speed_mps is not None else speeds))
        
        wear = 0.01
        if len(args) > 1 and args[1] is not None:
            wear = float(args[1])
        elif user_wear_percent is not None:
            wear = float(user_wear_percent)
        elif user_oil_wear_pct is not None:
            wear = float(user_oil_wear_pct)
        elif user_wear_pct is not None:
            wear = float(user_wear_pct)
        elif wear_percent is not None:
            wear = float(wear_percent)

        if raw_speeds is not None:
            sp = np.asarray(raw_speeds, dtype=float)
            distance_km = float(np.sum(sp) * dt / 1000.0) if len(sp) > 0 else 0.0
        elif "distance_km" in kwargs:
            distance_km = float(kwargs["distance_km"])
        else:
            distance_km = 0.0

        if distance_km <= 0.001 and wear <= 0.001:
            return {
                "fuel_saved_rub": 0.0,
                "fuel_savings_rub": 0.0,
                "oil_saved_rub": 0.0,
                "oil_savings_rub": 0.0,
                "total_savings_rub": 0.0,
                "cost_savings_rub": 0.0,
                "twin_wear_percent": wear
            }

        if distance_km < 0.05:
            fuel_saved_rub = 0.0
        else:
            liters_saved = distance_km * (1.8 / 100.0)
            fuel_saved_rub = round(float(liters_saved * fp), 2)

        twin_wear_percent = round(float(wear * 1.45 + 0.002), 5)
        wear_delta = max(0.0, twin_wear_percent - wear)
        oil_saved_rub = round(float((wear_delta / 100.0) * sc), 2)

        total_savings_rub = round(fuel_saved_rub + oil_saved_rub, 2)

        return {
            "fuel_saved_rub": fuel_saved_rub,
            "fuel_savings_rub": fuel_saved_rub,
            "oil_saved_rub": oil_saved_rub,
            "oil_savings_rub": oil_saved_rub,
            "total_savings_rub": total_savings_rub,
            "cost_savings_rub": total_savings_rub,
            "twin_wear_percent": twin_wear_percent
        }

    simulate_twin_and_delta = evaluate_financial_delta


AggressiveTwinEngine = TwinEngine