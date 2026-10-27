"""Research fuel inference; never infer savings or fill unobserved trip time."""
from functools import lru_cache
import math
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.features import FEATURES, ROAD, motion_windows, physical_prediction

MODEL_VERSION = "ved-gasoline-hybrid-2026-10-18-v1"
SELECTED_MODEL = "hybrid"
MAX_TRIP_DURATION_S = 86_400
MAX_GPS_SAMPLES = 200_000
MAX_GPS_ACCURACY_M = 50.0
ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "ml" / "artifacts"
LIMITATIONS = [
    "Research estimate; not validated on smartphone GPS or the pilot Audi vehicles.",
    "Training fuel targets are MAF-derived estimates, not measured tank consumption.",
    "No fuel estimate is made for rejected, missing, or unobserved intervals.",
    "Engine is assumed running from trip start until trip end, including stops.",
    "Level road and generic road resistance; no road gradient, 2GIS or climate inputs yet.",
    "No counterfactual trip or gasoline savings estimate is produced by this module.",
]


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


@lru_cache(maxsize=2)
def _load_artifacts(directory=None):
    # Cache immutable bundles by path, never a mutable "current" directory.
    from app.ml.bundle import load_bundle
    return load_bundle(directory or ARTIFACT_DIR)


def predict_fuel(gps_samples, vehicle_profile, started_at_ms, ended_at_ms):
    """Estimate liters on retained GPS intervals of ONE ended trip.

    Required profile: fuel_type='gasoline', mass_kg, displacement_l, turbo (bool).
    GPS uses event timestamp_ms and speed_mps; missing speed is not a stop. Accuracy
    >50m, missing/invalid accuracy and absent/invalid speed invalidate intervals.
    There is no extrapolation to session boundaries and no scaling for missing data.
    Every result is JSON-safe; unusable input yields null estimates with a reason.
    """
    result = {
        "status": "invalid_trip",
        "model_version": MODEL_VERSION,
        "selected_model": SELECTED_MODEL,
        "engine_on_assumed_until_trip_end": True,
        "session_duration_s": None,
        "retained_duration_s": 0.0,
        "unestimated_duration_s": None,
        "coverage_fraction": 0.0,
        "accepted_windows": 0,
        "rejected_windows": 0,
        "retained_distance_m": 0.0,
        "distance_km": 0.0,
        "gps_accuracy_threshold_m": MAX_GPS_ACCURACY_M,
        "estimated_fuel_l": None,
        "complete_trip_fuel_l": None,
        "fuel_l_on_retained_intervals": {"hybrid": None, "catboost": None, "physics": None},
        "features_outside_training_range": [],
        "missing_profile_fields": [],
        "quality_flags": [],
        "limitations": list(LIMITATIONS),
    }
    flags = result["quality_flags"]
    start, end = _number(started_at_ms), _number(ended_at_ms)
    if (start is None or end is None or start < 0 or end > 253402300799999
            or end <= start or (end - start) / 1000 > MAX_TRIP_DURATION_S):
        flags.append("invalid_session_boundaries")
        return result
    duration = (end - start) / 1000
    result.update(session_duration_s=duration, unestimated_duration_s=duration)
    if not isinstance(gps_samples, (list, tuple)) or len(gps_samples) > MAX_GPS_SAMPLES:
        flags.append("invalid_gps_sample_count")
        return result

    vehicle = vehicle_profile if isinstance(vehicle_profile, dict) else {}
    fuel = str(vehicle.get("fuel_type", "unknown")).lower()
    if fuel not in ("gasoline", "unknown", "none", ""):
        result["status"] = "unsupported_vehicle"
        flags.append("gasoline_only_model")
        return result
    missing = []
    mass, displacement = _number(vehicle.get("mass_kg")), _number(vehicle.get("displacement_l"))
    if fuel != "gasoline":
        missing.append("fuel_type")
    if mass is None or not 300 <= mass <= 5000:
        missing.append("mass_kg")
    if displacement is None or not .5 <= displacement <= 8:
        missing.append("displacement_l")
    if not isinstance(vehicle.get("turbo"), bool):
        missing.append("turbo")
    if missing:
        result.update(status="profile_incomplete", missing_profile_fields=missing)
        flags.append("vehicle_profile_required")
        return result
    profile = {"mass_kg": mass, "displacement_l": displacement,
               "turbo_tag": float(vehicle["turbo"])}

    readings = {}
    counts = {"received": len(gps_samples), "invalid_timestamp": 0, "outside_session": 0,
              "duplicate_timestamp": 0, "missing_or_invalid_speed": 0,
              "missing_or_invalid_accuracy": 0, "low_accuracy": 0,
              "unknown_speed_source": 0}
    for sample in gps_samples:
        if not isinstance(sample, dict):
            counts["invalid_timestamp"] += 1
            continue
        timestamp = _number(sample.get("timestamp_ms"))
        if timestamp is None:
            counts["invalid_timestamp"] += 1
            continue
        if timestamp < start or timestamp > end:
            counts["outside_session"] += 1
            continue
        speed = _number(sample.get("speed_mps"))
        accuracy = _number(sample.get("accuracy_m"))
        valid = True
        if speed is None or not 0 <= speed <= 200 / 3.6:
            valid = False
            counts["missing_or_invalid_speed"] += 1
        if accuracy is None or accuracy < 0:
            valid = False
            counts["missing_or_invalid_accuracy"] += 1
        elif accuracy > MAX_GPS_ACCURACY_M:
            valid = False
            counts["low_accuracy"] += 1
        if sample.get("speed_source") not in ("gps", "derived", "coordinates", "derived_gps"):
            counts["unknown_speed_source"] += 1
            valid = False
        if timestamp in readings:
            counts["duplicate_timestamp"] += 1
            previous = readings[timestamp]
            # Conflicting duplicates invalidate both adjacent intervals. Never pick
            # the more convenient reading or let arrival order affect the estimate.
            if not valid or not math.isfinite(previous) or previous != speed:
                readings[timestamp] = math.nan
        else:
            readings[timestamp] = speed if valid else math.nan
    result["gps_sample_counts"] = counts
    flags.extend(name for name, count in counts.items() if name != "received" and count)
    result["status"] = "no_usable_gps"
    if len(readings) < 2:
        flags.append("insufficient_gps_samples")
        return result
    times = sorted(readings)
    # Normalize before resampling to keep epoch values out of numerical operations.
    time_s = np.array([(timestamp - start) / 1000 for timestamp in times])
    speeds = np.array([readings[timestamp] for timestamp in times])
    rows, _, good = motion_windows(time_s, speeds, profile)
    result["rejected_windows"] = int((~good).sum())
    if np.any(np.diff(time_s) > 10):
        flags.append("gps_gap_over_10_seconds")
    if not len(rows) or not good.any():
        flags.append("no_complete_valid_motion_window")
        return result
    frame = pd.DataFrame(rows).loc[good].copy()
    retained = float(frame.duration_s.sum())
    result.update(
        retained_duration_s=retained,
        unestimated_duration_s=max(0.0, duration - retained),
        coverage_fraction=min(1.0, retained / duration),
        accepted_windows=int(good.sum()),
        retained_distance_m=float(frame.distance_m.sum()),
        distance_km=float(frame.distance_m.sum() / 1000),
    )
    if retained < duration - 1e-6:
        flags.append("partial_coverage_no_extrapolation")
    if result["rejected_windows"]:
        flags.append("invalid_motion_windows_rejected")
    try:
        from app.services.model_registry import active_artifacts, enabled
        if enabled():
            card, models, info = active_artifacts(_load_artifacts)
        else:
            card, models, info = _load_artifacts()
            info = {**info, "model_bundle_id": "builtin"}
        result.update({key: info[key] for key in (
            "model_version", "selected_model", "model_bundle_id",
            "artifact_bundle_sha256", "feature_schema_version",
        )})
        frame["physics"] = physical_prediction(frame, card["physics_coefficients_l_per_mj_l_per_h"])
        direct = models["catboost"].predict(frame[FEATURES], thread_count=1)
        hybrid = models["hybrid"].predict(frame[card["hybrid_features"]], thread_count=1) + frame.physics.to_numpy()
        predictions = {"physics": frame.physics.to_numpy(), "catboost": direct, "hybrid": hybrid}
        if any(not np.isfinite(values).all() for values in predictions.values()):
            raise ValueError("Non-finite model output")
        if any((values < 0).any() for values in predictions.values()):
            flags.append("negative_window_predictions_clipped")
        totals = {key: float(np.maximum(values, 0).sum()) for key, values in predictions.items()}
    except Exception as error:
        # Deliberate service boundary: native CatBoost errors must not prevent saving
        # a trip. Log the technical cause; expose only a stable failure code.
        import logging
        logging.getLogger(__name__).error("Fuel inference unavailable: %s", error)
        result["status"] = "model_unavailable"
        flags.append("model_load_or_prediction_failed")
        return result
    outside = [key for key, (low, high) in card["input_ranges_train"].items()
               if (frame[key] < low - 1e-9).any() or (frame[key] > high + 1e-9).any()]
    if outside:
        flags.append("outside_training_range")
    result.update(status="research_estimate", estimated_fuel_l=totals[result["selected_model"]],
                  fuel_l_on_retained_intervals=totals, features_outside_training_range=outside)
    if math.isclose(retained, duration, abs_tol=1e-6, rel_tol=0):
        result["complete_trip_fuel_l"] = totals[result["selected_model"]]
    return result
