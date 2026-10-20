"""Shared, offline feature and fuel calculations. Units are SI unless named otherwise."""
import numpy as np

WINDOW_S = 30
MAX_GAP_S = 10.0
AFR = 14.08  # E10 stoichiometric mass ratio, VED paper; NOT measured per vehicle.
DENSITY_G_L = 745.0  # Explicit nominal gasoline assumption, not a VED measurement.
ROAD = {"crr": 0.012, "cda_m2": 0.65, "rho_air": 1.225, "gravity": 9.80665}
FEATURES = [
    "duration_s", "distance_m", "speed_mean_mps", "speed_std_mps", "speed_max_mps",
    "stopped_fraction", "above_80_fraction", "accel_positive_mean", "decel_mean",
    "accel_rms", "accel_max", "decel_max", "positive_speed_change",
    "tractive_energy_mj", "mass_kg", "displacement_l", "turbo_tag",
]
SOURCES = {0: "missing", 1: "oem_fuel_rate", 2: "maf_full_trim",
           3: "maf_partial_trim", 4: "maf_no_trim"}


def fuel_rate(frame):
    """Return L/h and provenance. Engine signals are labels, NEVER ML inputs."""
    def arr(name):
        return frame[name].to_numpy(dtype=float)

    def bank_mean(kind):
        values = np.column_stack([arr(f"{kind} Term Fuel Trim Bank {i}[%]") for i in (1, 2)])
        valid = np.isfinite(values) & (values >= -100) & (values <= 100)
        count = valid.sum(axis=1)
        return np.where(valid, values, 0).sum(axis=1) / np.maximum(count, 1), count > 0

    st, has_st = bank_mean("Short")
    lt, has_lt = bank_mean("Long")
    factor = 1 + (st + lt) / 100
    maf = arr("MAF[g/sec]")
    calculated = maf * factor / AFR / DENSITY_G_L * 3600
    valid = np.isfinite(maf) & (maf >= 0) & (maf <= 800) & (factor >= 0) & (factor <= 3)
    valid &= np.isfinite(calculated) & (calculated <= 200)
    result = np.where(valid, calculated, np.nan)
    source = np.where(valid, np.where(has_st & has_lt, 2, np.where(has_st | has_lt, 3, 4)), 0)
    direct = arr("Fuel Rate[L/hr]")
    direct_ok = np.isfinite(direct) & (direct >= 0) & (direct <= 200)
    result[direct_ok] = direct[direct_ok]
    source[direct_ok] = 1
    return result, source


def interval_integral(time_s, values, edges):
    """Integrate left-held asynchronous signals exactly between arbitrary edges.

    values[i] applies on [time_s[i], time_s[i+1]); final value has no duration.
    Callers must separately reject missing intervals and long gaps.
    """
    time_s, values, edges = map(lambda x: np.asarray(x, dtype=float), (time_s, values, edges))
    if np.any(np.diff(time_s) <= 0) or edges[0] < time_s[0] or edges[-1] > time_s[-1]:
        raise ValueError("Integration needs increasing timestamps and in-range edges")
    cumulative = np.r_[0.0, np.cumsum(values[:-1] * np.diff(time_s))]
    return np.diff(np.interp(edges, time_s, cumulative))


def motion_windows(time_s, speed_mps, profile):
    """Interpolate speed at 1 s, then compute complete 30 s windows / >=5 s tail.

    A regular grid avoids treating every asynchronous OBD row as a new speed sample.
    Does not extrapolate, fill gaps, assume GPS absence is zero, or smooth away stops.
    """
    time_s, speed_mps = np.asarray(time_s, float), np.asarray(speed_mps, float)
    if len(time_s) < 2 or np.any(np.diff(time_s) <= 0):
        raise ValueError("Need at least two strictly increasing timestamps")
    seconds = int(np.floor(time_s[-1] - time_s[0]))
    if seconds < 5:
        return [], np.array([]), np.array([], bool)
    grid = time_s[0] + np.arange(seconds + 1)
    speed = np.interp(grid, time_s, speed_mps)
    starts = np.arange(0, seconds, WINDOW_S)
    ends = np.minimum(starts + WINDOW_S, seconds)
    if ends[-1] - starts[-1] < 5:
        starts, ends = starts[:-1], ends[:-1]
    if not len(starts):
        return [], np.array([]), np.array([], bool)
    edges = np.r_[grid[starts], grid[ends[-1]]]
    # Reject an entire window if any underlying raw interval is invalid.
    raw_good = (np.isfinite(speed_mps[:-1]) & np.isfinite(speed_mps[1:])
                & (speed_mps[:-1] >= 0) & (speed_mps[1:] >= 0)
                & (speed_mps[:-1] <= 200 / 3.6) & (speed_mps[1:] <= 200 / 3.6)
                & (np.diff(time_s) <= MAX_GAP_S))
    covered = interval_integral(time_s, np.r_[raw_good, False].astype(float), edges)
    good = np.isclose(covered, ends - starts, atol=1e-6)
    acceleration = np.diff(speed)
    midpoint = (speed[:-1] + speed[1:]) / 2
    mass = profile["mass_kg"]
    # Change in kinetic energy + rolling and aerodynamic work; level road.
    kinetic = 0.5 * mass * (speed[1:] ** 2 - speed[:-1] ** 2)
    resistance = mass * ROAD["gravity"] * ROAD["crr"] + 0.5 * ROAD["rho_air"] * ROAD["cda_m2"] * midpoint ** 2
    energy = np.maximum(kinetic + resistance * midpoint, 0) / 1e6
    rows = []
    for i, (start, end) in enumerate(zip(starts, ends)):
        v, a = midpoint[start:end], acceleration[start:end]
        if not np.all(np.isfinite(a)) or np.max(np.abs(a)) > 6:
            good[i] = False
        rows.append({
            "window_start_s": float(edges[i]), "duration_s": float(end - start),
            "distance_m": float(v.sum()), "speed_mean_mps": float(v.mean()),
            "speed_std_mps": float(v.std()), "speed_max_mps": float(speed[start:end+1].max()),
            "stopped_fraction": float((v < 0.5).mean()),
            "above_80_fraction": float((v > 80 / 3.6).mean()),
            "accel_positive_mean": float(np.maximum(a, 0).mean()),
            "decel_mean": float(np.maximum(-a, 0).mean()), "accel_rms": float(np.sqrt(np.mean(a*a))),
            "accel_max": float(a.max()), "decel_max": float((-a).max()),
            "positive_speed_change": float(np.maximum(a, 0).sum()),
            "tractive_energy_mj": float(energy[start:end].sum()), **profile,
        })
    return rows, edges, good


def physics_matrix(frame):
    # Coefficients have units L/MJ and L/h respectively. Engine assumed on throughout.
    return np.column_stack([frame["tractive_energy_mj"], frame["duration_s"] / 3600])


def physical_prediction(frame, coefficients):
    return physics_matrix(frame) @ np.asarray(coefficients)
