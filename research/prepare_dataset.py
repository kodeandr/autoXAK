"""Audit the public VED snapshot and build a vehicle-disjoint supervised table."""
import json
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
from fuel_model import AFR, DENSITY_G_L, MAX_GAP_S, ROAD, SOURCES, fuel_rate, interval_integral, motion_windows

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data/processed"
REPORT = ROOT / "reports"
SEED = 20261018
COLUMNS = ["VehId", "Trip", "Timestamp(ms)", "Vehicle Speed[km/h]", "MAF[g/sec]", "Fuel Rate[L/hr]"] + [
    f"{term} Term Fuel Trim Bank {bank}[%]" for term in ("Short", "Long") for bank in (1, 2)]


def static_table():
    table = pd.read_excel(ROOT / "data/raw/ved_static_ice_hev.xlsx").replace("NO DATA", np.nan)
    engine = table["Engine Configuration & Displacement"].fillna("").astype(str)
    table["displacement_l"] = pd.to_numeric(engine.str.extract(r"(\d+(?:\.\d+)?)\s*L\b")[0])
    table["mass_kg"] = pd.to_numeric(table["Generalized_Weight"], errors="coerce") * 0.45359237
    table["turbo_tag"] = engine.str.contains("T/C", regex=False).astype(int)
    table["diesel_tag"] = engine.str.contains(r"DSL|DIESEL", case=False, regex=True)
    table["eligible"] = ((table["Vehicle Type"] == "ICE") & ~table["diesel_tag"]
                         & table["mass_kg"].notna() & table["displacement_l"].notna())
    if table.VehId.duplicated().any():
        raise ValueError("Static vehicle IDs must be unique")
    table["split"] = "excluded"
    eligible = table[table.eligible].copy()
    # Fix the split before reading trip labels. Ensure 1.4/2.0 groups are represented.
    eligible["stratum"] = eligible.displacement_l.map(
        lambda x: str(x) if x in (1.4, 2.0) else "small" if x < 2.0 else "medium" if x <= 3.0 else "large")
    rng = np.random.default_rng(SEED)
    for _, group in eligible.groupby("stratum", sort=True):
        indices = rng.permutation(group.index.to_numpy())
        ntest = max(1, round(len(indices) * 0.15))
        nval = max(1, round(len(indices) * 0.15))
        table.loc[indices[:ntest], "split"] = "test"
        table.loc[indices[ntest:ntest+nval], "split"] = "validation"
        table.loc[indices[ntest+nval:], "split"] = "train"
    return table


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(exist_ok=True)
    table = static_table()
    table.to_csv(REPORT / "vehicles.csv", index=False, encoding="utf-8-sig")
    profile_map = table.set_index("VehId").to_dict("index")
    counts = Counter()
    source_seconds = Counter()
    frames, fragments = [], []
    seen_keys = set()
    files = sorted((ROOT / "data/raw/dynamic").glob("*.csv"))
    if len(files) != 54:
        raise ValueError(f"Expected the complete 54-file snapshot; found {len(files)}")
    for file_no, path in enumerate(files, 1):
        df = pd.read_csv(path, usecols=COLUMNS)
        counts["all_dynamic_rows"] += len(df)
        df = df[df.VehId.isin(table.loc[table.eligible, "VehId"])].copy()
        counts["eligible_rows"] += len(df)
        df = df.sort_values(["VehId", "Trip", "Timestamp(ms)"], kind="stable")
        before = len(df)
        df = df.drop_duplicates(["VehId", "Trip", "Timestamp(ms)"], keep="last")
        counts["duplicate_timestamps_removed"] += before - len(df)
        rate, provenance = fuel_rate(df)
        df["label_rate"] = rate
        df["source"] = provenance
        records = []
        for (vehicle, trip), group in df.groupby(["VehId", "Trip"], sort=False):
            counts["recorded_fragments"] += 1
            key = (int(vehicle), int(trip))
            if key in seen_keys:
                # Never silently count the same trip twice or accidentally split it across files.
                raise ValueError(f"Trip appears in more than one week: {key}")
            seen_keys.add(key)
            meta = profile_map[vehicle]
            profile = {name: float(meta[name]) for name in ("mass_kg", "displacement_l", "turbo_tag")}
            time = group["Timestamp(ms)"].to_numpy(float) / 1000
            if len(time) < 2 or not np.isfinite(time).all():
                counts["short_or_invalid_fragments"] += 1
                continue
            speed = group["Vehicle Speed[km/h]"].to_numpy(float) / 3.6
            rows, edges, good_motion = motion_windows(time, speed, profile)
            if not rows:
                counts["short_or_invalid_fragments"] += 1
                continue
            dt = np.diff(time)
            counts["raw_intervals"] += len(dt)
            counts["intervals_over_10s"] += int((dt > MAX_GAP_S).sum())
            counts["intervals_under_1s"] += int((dt < 1).sum())
            label = group.label_rate.to_numpy(float)
            source = group.source.to_numpy(int)
            valid = np.isfinite(label) & np.r_[dt <= MAX_GAP_S, False]
            duration = np.diff(edges)
            label_duration = interval_integral(time, valid.astype(float), edges)
            good = good_motion & np.isclose(label_duration, duration, atol=1e-6)
            litres = interval_integral(time, np.nan_to_num(label) / 3600, edges)
            source_durations = {name: interval_integral(time, (source == code).astype(float), edges)
                                for code, name in SOURCES.items() if code}
            counts["candidate_windows"] += len(rows)
            counts["rejected_motion_windows"] += int((~good_motion).sum())
            counts["rejected_label_windows"] += int((~np.isclose(label_duration, duration, atol=1e-6)).sum())
            kept_duration = 0.0
            for i, row in enumerate(rows):
                if not good[i]:
                    continue
                row.update(vehicle_id=int(vehicle), trip_id=int(trip), split=meta["split"],
                           week=path.stem, fuel_l=float(litres[i]))
                for name, durations in source_durations.items():
                    row[name + "_s"] = float(durations[i])
                    source_seconds[name] += float(durations[i])
                records.append(row)
                kept_duration += row["duration_s"]
            span = float(time[-1] - time[0])
            fragments.append({"vehicle_id": int(vehicle), "trip_id": int(trip), "split": meta["split"],
                              "recorded_span_s": span, "retained_s": kept_duration,
                              "coverage": kept_duration / span if span else 0})
        if records:
            frames.append(pd.DataFrame(records))
        print(f"{file_no}/54 {path.name}: {len(records):,} accepted windows", flush=True)
    result = pd.concat(frames, ignore_index=True)
    result.to_csv(OUT / "windows.csv.gz", index=False, compression="gzip")
    fragment_df = pd.DataFrame(fragments)
    fragment_df.to_csv(OUT / "fragments.csv", index=False)
    coverage = result.groupby(["vehicle_id", "split", "displacement_l"]).agg(
        windows=("fuel_l", "size"), fragments=("trip_id", "nunique"),
        hours=("duration_s", lambda x: x.sum()/3600), litres=("fuel_l", "sum")).reset_index()
    coverage.to_csv(REPORT / "vehicle_coverage.csv", index=False)
    ice = table[table["Vehicle Type"] == "ICE"]
    audit = {
        "seed": SEED, "files": len(files), "counts": dict(counts),
        "static_ice": len(ice), "excluded_diesel_ids": table.loc[table.diesel_tag, "VehId"].tolist(),
        "static_eligible": int(table.eligible.sum()), "retained_windows": len(result),
        "retained_vehicles": int(result.vehicle_id.nunique()),
        "retained_fragments": int(result[["vehicle_id", "trip_id"]].drop_duplicates().shape[0]),
        "retained_hours": float(result.duration_s.sum()/3600),
        "label_source_seconds": dict(source_seconds),
        "split_vehicles": result.groupby("split").vehicle_id.nunique().to_dict(),
        "split_windows": result.groupby("split").size().to_dict(),
        "missing_ice_fields": {col: int(ice[col].isna().sum()) for col in
            ["Vehicle Class", "Transmission", "Drive Wheels", "Generalized_Weight"]},
        "constants": {"afr": AFR, "density_g_l": DENSITY_G_L, "max_gap_s": MAX_GAP_S, **ROAD},
        "target": "OEM estimated fuel rate or MAF-based proxy; not measured tank fuel",
    }
    (REPORT / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2), flush=True)


if __name__ == "__main__":
    main()
