import numpy as np
from typing import Dict, Any, Tuple
from app.models.vehicle_profiles import VehiclePhysicalProfile


class TwinEngine:
    def __init__(self, profile: VehiclePhysicalProfile = None, **kwargs):
        self.profile = profile

    def simulate_aggressive_twin_kinematics(self, speed_mps: np.ndarray, dt: float = 0.02) -> Tuple[np.ndarray, np.ndarray]:
        n = len(speed_mps)
        if n == 0:
            return np.array([]), np.array([])
        accel = np.gradient(speed_mps, dt) if n > 1 else np.zeros(n)
        aggressive_accel = np.clip(accel * 1.35 + 0.25 * np.sign(accel), -4.5, 3.5)
        aggressive_speed = np.clip(speed_mps * 1.15, 0.0, 55.0)
        return aggressive_speed, aggressive_accel

    def evaluate_financial_delta(
        self,
        speeds_mps: np.ndarray = None,
        user_wear_percent: float = 0.0,
        dt: float = 0.02,
        fuel_price_rub: float = 68.5,
        service_cost_rub: float = 13500.0,
        distance_km: float = None,
        **kwargs
    ) -> Dict[str, float]:
        if distance_km is None:
            if speeds_mps is not None and len(speeds_mps) > 0:
                distance_km = float(np.sum(speeds_mps) * dt / 1000.0)
            else:
                distance_km = 0.0

        if distance_km < 0.05:
            return {
                'fuel_saved_rub': 0.0,
                'fuel_savings_rub': 0.0,
                'oil_saved_rub': 0.0,
                'oil_savings_rub': 0.0,
                'total_savings_rub': 0.0,
                'twin_wear_percent': float(user_wear_percent)
            }

        liters_saved = distance_km * (1.8 / 100.0)
        fuel_saved_rub = round(float(liters_saved * fuel_price_rub), 2)

        twin_wear_percent = round(float(user_wear_percent * 1.45), 5)
        wear_delta = max(0.0, twin_wear_percent - user_wear_percent)
        oil_saved_rub = round(float((wear_delta / 100.0) * service_cost_rub), 2)

        total_savings_rub = round(fuel_saved_rub + oil_saved_rub, 2)

        return {
            'fuel_saved_rub': fuel_saved_rub,
            'fuel_savings_rub': fuel_saved_rub,
            'oil_saved_rub': oil_saved_rub,
            'oil_savings_rub': oil_saved_rub,
            'total_savings_rub': total_savings_rub,
            'twin_wear_percent': twin_wear_percent
        }

    def simulate_twin_and_delta(
        self,
        speeds_mps: np.ndarray = None,
        user_wear_percent: float = 0.0,
        dt: float = 0.02,
        fuel_price_rub: float = 68.5,
        service_cost_rub: float = 13500.0,
        distance_km: float = None,
        **kwargs
    ) -> Dict[str, float]:
        return self.evaluate_financial_delta(
            speeds_mps=speeds_mps,
            user_wear_percent=user_wear_percent,
            dt=dt,
            fuel_price_rub=fuel_price_rub,
            service_cost_rub=service_cost_rub,
            distance_km=distance_km,
            **kwargs
        )


AggressiveTwinEngine = TwinEngine
