"""Oil life is not identifiable from smartphone speed/acceleration alone."""

from typing import Any

from app.services.reference_math import VERSION, timeline, series


class WearEngine:
    def __init__(self, profile=None, **kwargs):
        self.profile = profile

    def compute_oil_wear(self, *args, times_s=None, **kwargs) -> dict[str, Any]:
        # Keep the old entry point so saved clients fail closed rather than receive
        # fabricated stress factors or break the independent recording workflow.
        speeds = (
            args[0]
            if args
            else kwargs.get(
                "speed_mps", kwargs.get("speeds_mps", kwargs.get("speeds", []))
            )
        )
        speeds = series(speeds, "speeds", nonnegative=True)
        duration = None
        if times_s is not None:
            times = timeline(times_s)
            series(speeds, "speeds", length=len(times))
            duration = float(times[-1])
        return {
            "status": "unavailable",
            "reason": "oil_life_requires_validated_engine_and_oil_measurements",
            "math_version": VERSION,
            "equivalent_engine_hours": None,
            "equivalent_hours": None,
            "wear_increment_percent": None,
            "oil_wear_percent": None,
            "trip_wear_percent": None,
            "trip_duration_sec": duration,
            "idle_duration_sec": None,
            "idle_duration_seconds": None,
            "missing_inputs": [
                "validated_oil_degradation_model",
                "engine_operating_measurements",
                "oil_condition_reference",
            ],
        }

    evaluate_trip_wear = compute_oil_wear