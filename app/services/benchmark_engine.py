"""Observed engine time is a measurement; an oil-aging multiplier is not ground truth."""

import numpy as np
from pydantic import BaseModel
from app.services.reference_math import timeline, time_below


class VerificationReport(BaseModel):
    total_points: int
    duration_minutes: float
    obd_ground_truth_hours: float
    autoxak_predicted_hours: float | None
    mape_percent: float | None
    hypothesis_confirmed: bool = False
    t_statistic: float | None = None
    p_value: float | None = None
    scientific_conclusion: str
    reference_quantity: str = "engine_running_hours"
    prediction_quantity: str = "oil_equivalent_hours"
    reference_validates_oil_life: bool = False


class BenchmarkEngine:
    @staticmethod
    def calculate_obd_ground_truth_hours(
        engine_rpm, engine_load_percent, oil_temp_c, dt=None, *, times_s=None
    ):
        arrays = [
            np.asarray(v, dtype=float)
            for v in (engine_rpm, engine_load_percent, oil_temp_c)
        ]
        if (
            (times_s is None and (dt is None or not np.isfinite(dt) or dt <= 0))
            or len(arrays[0]) < 2
            or any(
                a.ndim != 1 or len(a) != len(arrays[0]) or not np.isfinite(a).all()
                for a in arrays
            )
        ):
            raise ValueError(
                "Finite aligned OBD observations and positive sample period are required"
            )
        rpm, load, temp = arrays
        if (
            (rpm < 0).any()
            or (rpm > 20000).any()
            or (load < 0).any()
            or (load > 100).any()
            or (temp < -50).any()
            or (temp > 250).any()
        ):
            raise ValueError("OBD values are outside their physical ranges")
        times = timeline(times_s if times_s is not None else np.arange(len(rpm)) * dt)
        if len(times) != len(rpm):
            raise ValueError("OBD timestamps must match observations")
        # No RPM/800, traffic factor or arbitrary thermal multiplier. This is only
        # running duration inferred from measured RPM, not laboratory oil condition.
        running = time_below(times, -rpm, 0)
        return float(running / 3600)

    def evaluate_experiment(
        self,
        autoxak_engine_hours,
        obd_rpm,
        obd_load,
        obd_temp,
        user_savings,
        dt=None,
        *,
        times_s=None,
        prediction_quantity="oil_equivalent_hours",
    ):
        if autoxak_engine_hours is not None and (
            not np.isfinite(autoxak_engine_hours) or autoxak_engine_hours < 0
        ):
            raise ValueError("Predicted hours must be finite and nonnegative")
        observed = self.calculate_obd_ground_truth_hours(
            obd_rpm, obd_load, obd_temp, dt, times_s=times_s
        )
        same_quantity = (
            prediction_quantity == "engine_running_hours"
            and autoxak_engine_hours is not None
        )
        error = (
            abs(observed - autoxak_engine_hours) / observed * 100
            if same_quantity and observed > 0
            else None
        )
        times = timeline(
            times_s if times_s is not None else np.arange(len(obd_rpm)) * dt
        )
        return VerificationReport(
            total_points=len(obd_rpm),
            duration_minutes=float(times[-1] / 60),
            obd_ground_truth_hours=observed,
            autoxak_predicted_hours=autoxak_engine_hours,
            mape_percent=error,
            prediction_quantity=prediction_quantity,
            scientific_conclusion=(
                "Сравниваются одинаковые величины: длительность работы двигателя. "
                "Одна запись не подтверждает точность модели масла или причинную экономию."
                if same_quantity
                else "Эквивалентный износ масла и длительность работы двигателя — разные величины; "
                "погрешность и подтверждение гипотезы не определяются. Нужен независимый эталон состояния масла."
            ),
        )