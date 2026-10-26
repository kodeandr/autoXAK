"""Numerical reference kernels, SI units, explicit interpolation and no fitted defaults.

These functions verify the mathematics of supplied observations/parameters. They
do not certify that a caller's engine map, measurements or road parameters are
correct for a particular vehicle. No phone signal is converted into oil life.
"""

import hashlib
import json
import math
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from itertools import pairwise

import numpy as np
from numpy.polynomial import Polynomial

VERSION = "reference-math-1"
G = 9.80665  # Conventional standard gravity, m/s²; not a fitted vehicle coefficient.


def fingerprint(value):
    def encode(item):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, np.generic):
            return item.item()
        raise TypeError("Unsupported parameter type")

    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=encode
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def series(values, name, *, length=None, nonnegative=False):
    if any(isinstance(value, (bool, np.bool_)) for value in values):
        raise ValueError(f"{name} must contain numbers, not booleans")
    result = np.asarray(values, dtype=np.float64)
    if (
        result.ndim != 1
        or not np.isfinite(result).all()
        or (length is not None and len(result) != length)
    ):
        raise ValueError(f"{name} must be a finite aligned one-dimensional series")
    if nonnegative and (result < 0).any():
        raise ValueError(f"{name} must be nonnegative")
    return result


def timeline(values):
    times = series(values, "times_s")
    if len(times) < 2 or (np.diff(times) <= 0).any():
        raise ValueError("At least two strictly increasing timestamps are required")
    times = times - times[0]
    if times[-1] > 86400:
        raise ValueError("A trajectory cannot exceed 24 hours")
    if np.min(np.diff(times)) < 64 * np.finfo(float).eps * max(1, float(times[-1])):
        raise ValueError(
            "Timestamp spacing is below numerical resolution for this trajectory"
        )
    return times


def integral_linear(times, values):
    """Exact integral of the piecewise-linear interpolant, with actual Δt."""
    t = timeline(times)
    v = series(values, "values", length=len(t))
    return float(math.fsum(((v[:-1] + v[1:]) * 0.5 * np.diff(t)).tolist()))


def time_below(times, values, threshold):
    """Exact duration below a threshold for piecewise-linear observations."""
    t = timeline(times)
    v = series(values, "values", length=len(t))
    if not math.isfinite(threshold):
        raise ValueError("Threshold must be finite")
    lo, hi = v[:-1], v[1:]
    fractions = ((lo < threshold) & (hi < threshold)).astype(float)
    crossing = (lo < threshold) != (hi < threshold)
    moving = hi != lo
    indices = crossing & moving
    at = np.zeros_like(lo)
    at[indices] = (threshold - lo[indices]) / (hi[indices] - lo[indices])
    fractions[indices] = np.where(lo[indices] < threshold, at[indices], 1 - at[indices])
    return float(math.fsum((fractions * np.diff(t)).tolist()))


def trajectory_metrics(times, speeds, *, idle_threshold_mps=1.0):
    t = timeline(times)
    v = series(speeds, "speed_mps", length=len(t), nonnegative=True)
    distance = integral_linear(t, v)
    duration = float(t[-1])
    positive_specific_energy = float(np.maximum(np.diff(v * v), 0).sum() * 0.5)
    return {
        "duration_s": duration,
        "distance_m": distance,
        "average_speed_mps": distance / duration,
        "below_threshold_s": time_below(t, v, idle_threshold_mps),
        "idle_threshold_mps": idle_threshold_mps,
        "positive_specific_kinetic_energy_m2_s2": positive_specific_energy,
        "pke_m_s2": positive_specific_energy / distance if distance > 0 else None,
        "interpolation": "piecewise_linear",
        "math_version": VERSION,
    }


def threshold_events(times, values, *, threshold, above, min_duration_s, refractory_s):
    """Threshold crossings and durations of the explicit linear interpolant."""
    t = timeline(times)
    v = series(values, "signal", length=len(t))
    if (
        not all(math.isfinite(x) for x in (threshold, min_duration_s, refractory_s))
        or min_duration_s < 0
        or refractory_s < 0
    ):
        raise ValueError(
            "Finite nonnegative duration/refractory and finite threshold are required"
        )
    signed = (v - threshold) if above else (threshold - v)
    intervals = []
    for i, dt in enumerate(np.diff(t)):
        left, right = signed[i], signed[i + 1]
        if left <= 0 and right <= 0:
            continue
        start, end = float(t[i]), float(t[i + 1])
        if left <= 0:
            start += float(-left / (right - left) * dt)
        if right <= 0:
            end = float(t[i] + left / (left - right) * dt)
        if intervals and math.isclose(
            intervals[-1][1],
            start,
            rel_tol=0,
            abs_tol=64 * np.finfo(float).eps * max(1, t[-1]),
        ):
            intervals[-1][1] = end
        else:
            intervals.append([start, end])
    accepted = []
    tolerance = 64 * np.finfo(float).eps * max(1, t[-1])
    for start, end in intervals:
        if end - start + tolerance >= min_duration_s and (
            not accepted or start - accepted[-1][0] + tolerance >= refractory_s
        ):
            accepted.append([start, end])
    return {
        "count": len(accepted),
        "intervals_s": accepted,
        "interpolation": "piecewise_linear",
    }


def _roots_between(poly, lower, upper):
    roots = poly.roots()
    eps = 128 * np.finfo(float).eps
    return sorted(
        float(root.real)
        for root in roots
        if abs(root.imag) <= eps * max(1, abs(root.real)) and lower < root.real < upper
    )


def _positive_negative_integrals(poly, lower, upper):
    edges = [lower, *_roots_between(poly, lower, upper), upper]
    antiderivative = poly.integ()
    positive, negative = [], []
    for start, end in pairwise(edges):
        work = float(antiderivative(end) - antiderivative(start))
        (positive if poly((start + end) * 0.5) >= 0 else negative).append(work)
    return math.fsum(positive), math.fsum(negative)


def road_energy(times, speeds, elevations, winds, parameters):
    """Balance wheel work, including slope, wind and rotating inertia.

    Speed/wind interpolate linearly in time. Elevation interpolates linearly in
    travelled path distance on each observed segment. Drag power is cubic on
    subsegments; roots split changes of air-relative speed and wheel power.
    Integrals are analytic for these interpolants. No flat-road, still-air,
    nominal mass, generic CdA or efficiency is silently substituted.
    """
    t = timeline(times)
    v = series(speeds, "speed_mps", length=len(t), nonnegative=True)
    h = series(elevations, "elevation_m", length=len(t))
    wind = series(winds, "tailwind_mps", length=len(t))
    required = (
        "mass_kg",
        "rotating_equivalent_mass_kg",
        "crr",
        "cda_m2",
        "air_density_kg_m3",
        "drivetrain_efficiency",
    )
    p = {}
    for key in required:
        value = parameters.get(key)
        if isinstance(value, bool) or value is None or not math.isfinite(float(value)):
            raise ValueError(f"An explicit finite {key} is required")
        p[key] = float(value)
    if (
        p["mass_kg"] <= 0
        or p["rotating_equivalent_mass_kg"] < 0
        or p["crr"] < 0
        or p["cda_m2"] < 0
        or p["air_density_kg_m3"] <= 0
        or not 0 < p["drivetrain_efficiency"] <= 1
    ):
        raise ValueError("Road-load parameters are outside their physical domain")
    mass, effective = p["mass_kg"], p["mass_kg"] + p["rotating_equivalent_mass_kg"]
    kinetic, rolling, grade, drag, positive, negative = [], [], [], [], [], []
    for index, dt in enumerate(np.diff(t)):
        distance = (v[index] + v[index + 1]) * 0.5 * dt
        height = h[index + 1] - h[index]
        if abs(height) > distance + 64 * np.finfo(float).eps * max(1, distance):
            raise ValueError("Elevation change cannot exceed travelled path distance")
        sin_grade = height / distance if distance > 0 else 0.0
        cos_grade = math.sqrt(max(0, 1 - sin_grade * sin_grade))
        velocity = Polynomial([v[index], (v[index + 1] - v[index]) / dt])
        relative = velocity - Polynomial(
            [wind[index], (wind[index + 1] - wind[index]) / dt]
        )
        accel = (v[index + 1] - v[index]) / dt
        f_roll = mass * G * p["crr"] * cos_grade
        f_grade = mass * G * sin_grade
        constant_force = effective * accel + f_roll + f_grade
        kinetic.append(0.5 * effective * (v[index + 1] ** 2 - v[index] ** 2))
        rolling.append(f_roll * distance)
        grade.append(mass * G * height)
        edges = [0.0, *_roots_between(relative, 0.0, float(dt)), float(dt)]
        for left, right in pairwise(edges):
            sign = np.sign(relative((left + right) * 0.5))
            drag_power = (
                0.5
                * p["air_density_kg_m3"]
                * p["cda_m2"]
                * sign
                * relative
                * relative
                * velocity
            )
            power = constant_force * velocity + drag_power
            primitive = drag_power.integ()
            drag.append(float(primitive(right) - primitive(left)))
            pos, neg = _positive_negative_integrals(power, left, right)
            positive.append(pos)
            negative.append(neg)
    components = {
        "kinetic_j": math.fsum(kinetic),
        "rolling_j": math.fsum(rolling),
        "grade_j": math.fsum(grade),
        "aerodynamic_j": math.fsum(drag),
    }
    net = math.fsum(components.values())
    traction, negative_work = math.fsum(positive), math.fsum(negative)
    if not all(
        math.isfinite(value)
        for value in (*components.values(), net, traction, negative_work)
    ):
        raise ValueError("Non-finite mechanical work from supplied inputs")
    return {
        **components,
        "net_wheel_work_j": net,
        "positive_wheel_work_j": traction,
        "negative_wheel_work_j": negative_work,
        "positive_shaft_work_j": traction / p["drivetrain_efficiency"],
        "balance_residual_j": traction + negative_work - net,
        "distance_m": integral_linear(t, v),
        "math_version": VERSION,
        "parameters_sha256": fingerprint(parameters),
        "fuel_l": None,
        "fuel_status": "energy_is_not_fuel",
        "parameter_status": "caller_supplied_not_independently_validated",
    }


def integrate_fuel_rate(times, rate_lph, *, interpolation):
    t = timeline(times)
    rates = series(rate_lph, "fuel_rate_lph", length=len(t), nonnegative=True)
    if interpolation == "linear":
        return integral_linear(t, rates) / 3600
    if interpolation == "previous":
        return float(math.fsum((rates[:-1] * np.diff(t)).tolist()) / 3600)
    raise ValueError("Fuel rate interpolation must explicitly be linear or previous")


def fuel_from_map(times, rpm, torque_nm, engine_on, calibration):
    """Integrate a measured/calibrated rectangular fuel-flow map, without extrapolation.

    RPM/torque interpolate linearly; map interpolates bilinearly. Every crossed
    map boundary is split and two-point Gauss quadrature integrates the quadratic
    resulting flow exactly. engine_on describes each interval, not an inferred
    state from GPS. The supplied map must include its actual overrun fuel behavior.
    """
    t = timeline(times)
    rpm = series(rpm, "engine_rpm", length=len(t), nonnegative=True)
    torque = series(torque_nm, "torque_nm", length=len(t))
    if len(engine_on) != len(t) - 1 or any(
        not isinstance(x, (bool, np.bool_)) for x in engine_on
    ):
        raise ValueError("Explicit boolean engine_on is required for each interval")
    if not calibration.get("source"):
        raise ValueError("Engine map source is required")
    x, y = (
        series(calibration["rpm"], "map_rpm", nonnegative=True),
        series(calibration["torque_nm"], "map_torque"),
    )
    z = np.asarray(calibration["fuel_rate_lph"], float)
    if (
        len(x) < 2
        or len(y) < 2
        or (np.diff(x) <= 0).any()
        or (np.diff(y) <= 0).any()
        or z.shape != (len(x), len(y))
        or not np.isfinite(z).all()
        or (z < 0).any()
    ):
        raise ValueError(
            "Fuel map must be a finite, nonnegative, ordered rectangular grid"
        )

    def lookup(r, q):
        i = min(len(x) - 2, max(0, np.searchsorted(x, r, side="right") - 1))
        j = min(len(y) - 2, max(0, np.searchsorted(y, q, side="right") - 1))
        u, v = (r - x[i]) / (x[i + 1] - x[i]), (q - y[j]) / (y[j + 1] - y[j])
        return (
            (1 - u) * (1 - v) * z[i, j]
            + u * (1 - v) * z[i + 1, j]
            + (1 - u) * v * z[i, j + 1]
            + u * v * z[i + 1, j + 1]
        )

    liters = []
    node = 1 / math.sqrt(3)
    for i, dt in enumerate(np.diff(t)):
        if not engine_on[i]:
            if rpm[i] != 0 or rpm[i + 1] != 0:
                raise ValueError("Engine-off interval has nonzero measured RPM")
            continue
        if (
            min(rpm[i : i + 2]) < x[0]
            or max(rpm[i : i + 2]) > x[-1]
            or min(torque[i : i + 2]) < y[0]
            or max(torque[i : i + 2]) > y[-1]
        ):
            raise ValueError("Engine operation is outside the supplied calibration map")
        edges = {0.0, 1.0}
        for values, grid in ((rpm, x), (torque, y)):
            change = values[i + 1] - values[i]
            if change:
                edges.update(float(s) for s in (grid - values[i]) / change if 0 < s < 1)
        edges = sorted(edges)
        for left, right in pairwise(edges):
            mid, half = (left + right) * 0.5, (right - left) * 0.5
            samples = [mid - half * node, mid + half * node]
            flow = (
                sum(
                    lookup(
                        rpm[i] + s * (rpm[i + 1] - rpm[i]),
                        torque[i] + s * (torque[i + 1] - torque[i]),
                    )
                    for s in samples
                )
                * half
            )
            liters.append(float(flow * dt / 3600))
    return {
        "fuel_l": math.fsum(liters),
        "model": "bilinear_fuel_flow_map",
        "calibration_source": calibration["source"],
        "math_version": VERSION,
        "calibration_sha256": fingerprint(calibration),
        "validation_status": "caller_supplied_map_not_independently_validated",
    }


def compare_fuel(actual, reference, *, price_rub_per_l):
    """Signed difference of explicit fuel observations on a declared matched route.

    This is arithmetic, not proof of causal driver savings. Source declarations
    and matching route/context IDs must be independently checked by the caller.
    """
    for run in (actual, reference):
        for key in ("vehicle_id", "route_id", "context_id", "source"):
            if not run.get(key):
                raise ValueError(f"Explicit {key} is required for a fuel comparison")
        for key in ("fuel_l", "distance_m"):
            if (
                isinstance(run.get(key), bool)
                or not math.isfinite(float(run[key]))
                or float(run[key]) < 0
            ):
                raise ValueError(f"{key} must be finite and nonnegative")
        if run["fuel_l"] > 100000 or run["distance_m"] > 20000000:
            raise ValueError("Fuel observation exceeds supported numerical limits")
        duration = run.get("duration_s")
        if (
            isinstance(duration, bool)
            or duration is None
            or not math.isfinite(float(duration))
            or not 0 < float(duration) <= 86400
        ):
            raise ValueError("Explicit positive duration_s is required")
        uncertainty = run.get("uncertainty_l")
        if uncertainty is not None and (
            isinstance(uncertainty, bool)
            or not math.isfinite(float(uncertainty))
            or not 0 <= uncertainty <= 100000
        ):
            raise ValueError("Declared fuel uncertainty must be finite and nonnegative")
    if any(
        actual[key] != reference[key]
        for key in ("vehicle_id", "route_id", "context_id")
    ):
        raise ValueError("Vehicle, route and comparison context must match")
    if not math.isclose(
        actual["distance_m"], reference["distance_m"], rel_tol=1e-9, abs_tol=1e-6
    ):
        raise ValueError(
            "Comparison distances must match; fuel is not scaled to hide a mismatch"
        )
    if not math.isclose(
        actual["duration_s"], reference["duration_s"], rel_tol=1e-9, abs_tol=1e-6
    ):
        raise ValueError(
            "Comparison durations must match; idle time cannot be silently changed"
        )
    if (
        isinstance(price_rub_per_l, bool)
        or price_rub_per_l is None
        or not math.isfinite(float(price_rub_per_l))
    ):
        raise ValueError("Explicit nonnegative fuel price is required")
    price = Decimal(str(price_rub_per_l))
    if not price.is_finite() or not 0 <= price <= 10000:
        raise ValueError("Explicit nonnegative fuel price is required")
    delta = Decimal(str(reference["fuel_l"])) - Decimal(str(actual["fuel_l"]))
    bounds = None
    if (
        actual.get("uncertainty_l") is not None
        and reference.get("uncertainty_l") is not None
    ):
        actual_fuel = Decimal(str(actual["fuel_l"]))
        reference_fuel = Decimal(str(reference["fuel_l"]))
        actual_uncertainty = Decimal(str(actual["uncertainty_l"]))
        reference_uncertainty = Decimal(str(reference["uncertainty_l"]))
        lower = max(Decimal(0), reference_fuel - reference_uncertainty) - (
            actual_fuel + actual_uncertainty
        )
        upper = (
            reference_fuel
            + reference_uncertainty
            - max(Decimal(0), actual_fuel - actual_uncertainty)
        )
        bounds = {
            "delta_fuel_l": [float(lower), float(upper)],
            "delta_cost_rub": [
                float((lower * price).quantize(Decimal(".01"), rounding=ROUND_FLOOR)),
                float((upper * price).quantize(Decimal(".01"), rounding=ROUND_CEILING)),
            ],
            "method": "worst_case_declared_absolute_bounds_not_a_statistical_confidence_interval",
        }
    return {
        "delta_fuel_l": float(delta),
        "delta_cost_rub": float(
            (delta * price).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        ),
        "price_rub_per_l": float(price),
        "math_version": VERSION,
        "declared_uncertainty_bounds": bounds,
        "status": "declared_matched_comparison",
        "causal_savings_verified": False,
    }


def validation_metrics(predicted, observed):
    """Paired numerical errors; no p-values or attestation from modelled rubles."""
    actual = series(observed, "observed_fuel_l", nonnegative=True)
    estimate = series(
        predicted, "predicted_fuel_l", length=len(actual), nonnegative=True
    )
    if not len(actual):
        raise ValueError("Nonempty paired observations are required")
    residual = estimate - actual
    absolute = np.abs(residual)
    total = float(math.fsum(actual.tolist()))
    positive = actual > 0
    return {
        "count": len(actual),
        "mae_l": float(absolute.mean()),
        "rmse_l": float(np.sqrt(np.mean(residual * residual))),
        "bias_l": float(residual.mean()),
        "wape_percent": float(100 * math.fsum(absolute.tolist()) / total)
        if total > 0
        else None,
        "mape_positive_references_percent": float(
            np.mean(absolute[positive] / actual[positive]) * 100
        )
        if positive.any()
        else None,
        "zero_reference_count": int((~positive).sum()),
        "accuracy_certified": False,
        "math_version": VERSION,
    }
