import numpy as np
from typing import Dict, Any, Union
from app.models.vehicle_profiles import get_default_profile, VehiclePhysicalProfile


class WearEngine:
    def __init__(self, profile: VehiclePhysicalProfile = None, ambient_temp_c: float = 20.0, **kwargs):
        self.profile = profile or get_default_profile()
        self.ambient_temp_c = ambient_temp_c

    def compute_oil_wear(
        self,
        *args,
        speeds_mps: Any = None,
        speed_mps: Any = None,
        speeds: Any = None,
        accel_mps2: Any = None,
        ax_mps2: Any = None,
        accel: Any = None,
        traffic_score: float = 1.0,
        dt: float = 0.02,
        **kwargs
    ) -> Dict[str, float]:
        """Физико-химический расчет эквивалентных моточасов и времени простоя в пробках."""
        raw_speeds = args[0] if len(args) > 0 else (speeds_mps if speeds_mps is not None else (speed_mps if speed_mps is not None else speeds))
        raw_accel = args[1] if len(args) > 1 else (accel_mps2 if accel_mps2 is not None else (ax_mps2 if ax_mps2 is not None else accel))

        sp = np.asarray(raw_speeds if raw_speeds is not None else [], dtype=float)
        ac = np.asarray(raw_accel if raw_accel is not None else [], dtype=float)

        n = len(sp)
        if n == 0:
            return {
                "equivalent_engine_hours": 0.0,
                "equivalent_hours": 0.0,
                "wear_increment_percent": 0.0,
                "oil_wear_percent": 0.0,
                "trip_wear_percent": 0.0,
                "idle_duration_sec": 0.0,
                "idle_duration_seconds": 0.0,
                "trip_duration_sec": 0.0
            }

        duration_hours = (n * dt) / 3600.0
        
        # Расчет длительности простоя на холостом ходу (v < 0.5 м/с)
        idle_mask = sp < 0.5
        idle_duration_sec = float(np.sum(idle_mask) * dt)
        mean_speed = float(np.mean(sp))

        # Штрафной коэффициент застоя масла и сниженного охлаждения
        idle_factor = 2.0 if mean_speed < 1.0 else 1.0
        traffic_factor = 1.0 + (max(1.0, float(traffic_score)) - 1.0) * 0.15

        harsh_events = np.sum(ac > 1.8)
        dynamics_factor = 1.0 + float(harsh_events / max(n, 1)) * 2.5

        equiv_hours = duration_hours * idle_factor * traffic_factor * dynamics_factor

        nominal_hours = getattr(getattr(self.profile, "oil_profile", None), "nominal_service_hours", 250.0)
        wear_pct = (equiv_hours / nominal_hours) * 100.0

        return {
            "equivalent_engine_hours": round(float(equiv_hours), 6),
            "equivalent_hours": round(float(equiv_hours), 6),
            "wear_increment_percent": round(float(wear_pct), 5),
            "oil_wear_percent": round(float(wear_pct), 5),
            "trip_wear_percent": round(float(wear_pct), 5),
            "idle_duration_sec": round(idle_duration_sec, 2),
            "idle_duration_seconds": round(idle_duration_sec, 2),
            "trip_duration_sec": round(float(n * dt), 2)
        }

    evaluate_trip_wear = compute_oil_wear