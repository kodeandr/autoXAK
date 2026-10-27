"""Bounded subprocess entry point. Never receives application credentials."""

import json
from pathlib import Path
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import numpy as np
    from app.ml.bundle import load_bundle
    from app.ml.features import FEATURES

    card, models, _ = load_bundle(sys.argv[1])
    # Probe lower, midpoint and upper training ranges with the exact feature order.
    bounds = np.array([card["input_ranges_train"][key] for key in FEATURES], dtype=float)
    rows = np.array([bounds[:, 0], bounds.mean(axis=1), bounds[:, 1]])
    a, b = card["physics_coefficients_l_per_mj_l_per_h"]
    physics = rows[:, FEATURES.index("tractive_energy_mj")] * a + rows[:, 0] / 3600 * b
    outputs = [models["catboost"].predict(rows, thread_count=1),
               models["hybrid"].predict(np.column_stack([rows, physics]), thread_count=1) + physics]
    if not all(np.isfinite(output).all() for output in outputs):
        raise ValueError("Non-finite probe output")
    print(json.dumps({"probe_rows": 3, "finite_predictions": True}))


if __name__ == "__main__":
    main()
