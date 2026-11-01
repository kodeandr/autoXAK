"""
app/services/twin_engine.py
Модуль виртуального агрессивного двойника (TwinEngine) и расчета топливной автономии (Fuel Autonomy).
"""

from typing import Any, Tuple, Optional
import numpy as np

try:
    from app.models.vehicle_profiles import VehiclePhysicalProfile
except ImportError:
    VehiclePhysicalProfile = None


class TwinEngine:
    def __init__(
        self,
        profile: Optional[Any] = None,
        fuel_price_rub: Optional[float] = None,
        service_cost_rub: Optional[float] = None,
        **kwargs
    ):
        self.profile = profile
        self.fuel_price = fuel_price_rub
        self.service_cost = service_cost_rub

    @staticmethod
    def filter_road_anomalies(
        user_ax: np.ndarray,
        raw_az: Optional[np.ndarray] = None,
        dt: float = 0.02,
        eligible_mask=None
    ) -> Tuple[np.ndarray, int]:
        """Z-Shock Veto: подавление ложных перегрузок при наезде на лежачие полицейские и ямы."""
        if not np.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be positive and finite")
        clean_ax = np.array(user_ax, copy=True, dtype=float)
        n = len(clean_ax)
        if n == 0:
            return clean_ax, 0

        anomalies_count = 0
        suppression_window = max(1, int(0.35 / dt))

        if raw_az is not None and len(raw_az) == n:
            az = np.asarray(raw_az, dtype=float)
            az = az - np.median(az)  # Accept gravity-inclusive and linear vertical inputs.
            jerk_z = np.gradient(az, dt) if n > 1 else np.zeros(n)
            impact_mask = (np.abs(az) > 3.5) | ((np.abs(az) > .8) & (np.abs(jerk_z) > 15.0))
            if eligible_mask is not None:
                eligible = np.asarray(eligible_mask, dtype=bool)
                if eligible.shape != az.shape:
                    raise ValueError('Shock eligibility must align with acceleration')
                impact_mask &= eligible
            armed, quiet, last = True, 0.0, -1.0
            for i in range(n):
                quiet = quiet + dt if abs(az[i]) < .5 else 0.0
                if quiet >= .12:
                    armed = True
                if impact_mask[i]:
                    clean_ax[max(0, i-int(.1/dt)):min(n, i+suppression_window)] = 0.0
                    if armed and i*dt-last >= .7:
                        anomalies_count += 1
                        armed, last = False, i*dt
        else:
            jerk_x = np.gradient(clean_ax, dt) if n > 1 else np.zeros(n)
            spike_mask = (np.abs(clean_ax) > 2.5) & (np.abs(jerk_x) > 20.0)
            i = 0
            while i < n:
                if spike_mask[i]:
                    anomalies_count += 1
                    end_idx = min(n, i + max(1, int(0.25 / dt)))
                    clean_ax[i:end_idx] = 0.0
                    i = end_idx
                else:
                    i += 1

        return clean_ax, anomalies_count

    @staticmethod
    def detect_throttle_hunting(speeds_mps, clean_ax, dt=0.02):
        """Phone motion cannot establish throttle position or fuel penalty."""
        from app.services.reference_math import trajectory_metrics
        if not np.isfinite(dt) or dt<=0:
            raise ValueError('Positive finite dt is required')
        speeds=np.asarray(speeds_mps,float)
        if len(speeds)<2:
            return None,None,None
        metrics=trajectory_metrics(np.arange(len(speeds))*dt,speeds)
        return None,None,metrics['pke_m_s2']

    def simulate_aggressive_twin_kinematics(self, speed_mps, dt=0.02, *, reference_speed_mps=None):
        """Compare an explicit reference, never invent a 15% faster journey."""
        from app.services.reference_math import series, integral_linear
        actual = series(speed_mps, 'actual_speed', nonnegative=True)
        if reference_speed_mps is None:
            raise ValueError('An observed or independently justified reference trajectory is required')
        reference = series(reference_speed_mps, 'reference_speed', length=len(actual), nonnegative=True)
        if not np.isfinite(dt) or dt <= 0 or len(actual)<2:
            raise ValueError('Positive sample period and at least two samples are required')
        times = np.arange(len(actual))*dt
        if not np.isclose(integral_linear(times, actual), integral_linear(times, reference), rtol=1e-9, atol=1e-6):
            raise ValueError('Reference and actual trajectory must cover the same distance')
        return reference, np.gradient(reference, times)

    def calculate_cmem_traction_power(self, speed_mps, ax_mps2):
        raise ValueError('Use road_energy with explicit slope, wind, rotating mass and road-load parameters')

    def evaluate_financial_delta(self, *args, comparison=None, fuel_price_rub=None,
                                 distance_km=None, user_ax=None, **kwargs):
        """Evaluate the approved MVP fuel multiplier policy on cleaned user_ax.

        K_user = 1 + mean(user_ax > 1.8) * 2.2; K_twin = 1.66.
        Distance <= 10 m earns exactly zero. Matched observations remain an
        independent explicit comparison path for research callers.
        """
        from app.services.reference_math import VERSION, compare_fuel
        result = {'status':'unavailable','reason':'matched_reference_fuel_required','math_version':VERSION,
                  'fuel_saved_rub':None,'fuel_savings_rub':None,'oil_saved_rub':None,'oil_savings_rub':None,
                  'total_savings_rub':None,'cost_savings_rub':None,'saved_fuel_liters':None,'fuel_saved_liters':None,
                  'range_bonus_km':None,'autonomy_days_extended':None,'twin_wear_percent':None,
                  'smooth_score':None,'hunting_penalty_liters':None,'throttle_hunting_detected':None}
        if comparison is not None:
            compared = compare_fuel(comparison['actual'],comparison['reference'],price_rub_per_l=fuel_price_rub)
            result.update(compared, fuel_saved_rub=compared['delta_cost_rub'],fuel_savings_rub=compared['delta_cost_rub'],
                          total_savings_rub=compared['delta_cost_rub'],cost_savings_rub=compared['delta_cost_rub'],
                          saved_fuel_liters=compared['delta_fuel_l'],fuel_saved_liters=compared['delta_fuel_l'],reason=None)
        elif distance_km is not None and user_ax is not None:
            distance = float(distance_km)
            ax = np.asarray(user_ax, dtype=float)
            if not np.isfinite(distance) or distance < 0 or ax.ndim != 1 or not np.all(np.isfinite(ax)):
                raise ValueError('Finite nonnegative distance and finite acceleration samples are required')
            rate = getattr(self.profile, 'base_city_fuel_rate_l100km', None)
            price = fuel_price_rub if fuel_price_rub is not None else (self.fuel_price if self.fuel_price is not None else 72.40)
            if distance > .01 and (not len(ax) or rate is None or price is None):
                result.update(reason='declared_fuel_rate_price_and_acceleration_required')
                return result
            if rate is not None and (not np.isfinite(rate) or not 0 < rate <= 100):
                raise ValueError('Fuel rate must be in (0, 100] l/100km')
            if price is not None and (not np.isfinite(price) or not 0 < price <= 10000):
                raise ValueError('Fuel price must be in (0, 10000] rub/l')
            hard = int(np.count_nonzero(ax > 1.8))
            fraction = hard / max(1, len(ax))
            multiplier = 1.0 + fraction * 2.2
            delta = max(0.0, 1.66 - multiplier)
            liters = 0.0 if distance <= .01 else distance / 100 * rate * delta
            rubles = round(liters * price, 2)
            result.update(status='model_estimate', reason=None, policy_version='twin-fuel-mvp-1',
                          n_hard=hard, n_total=max(1, len(ax)), stress_fraction=fraction,
                          k_user=multiplier, k_twin=1.66, distance_km=distance,
                          base_city_fuel_rate_l100km=rate, price_rub_per_l=price,
                          fuel_saved_rub=rubles, fuel_savings_rub=rubles, total_savings_rub=rubles,
                          cost_savings_rub=rubles, saved_fuel_liters=liters, fuel_saved_liters=liters,
                          causal_savings_verified=False, validation_status='not_established_by_real_obd_logs')
        return result

    simulate_twin_and_delta = evaluate_financial_delta


AggressiveTwinEngine = TwinEngine

def calculate_trip_smoothness_score(*args, **kwargs):
    """Product indicator: percent of valid samples within maneuver thresholds.

    This is an explicit descriptive policy, not a safety or fuel certification.
    Legacy count-only calls cannot determine the fraction of smooth samples.
    """
    signal = kwargs.get('acceleration')
    if signal is None:
        return None
    values = np.asarray(signal, dtype=float)
    if not len(values) or not np.all(np.isfinite(values)):
        return None
    return int(np.floor(100 * np.mean((values <= 2.0) & (values >= -2.3)) + .5))
