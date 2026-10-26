"""Validate declared paired fuel observations; never generate pseudo-real evidence.

Run from the repository root: python -m scripts.benchmark_suite dataset.json
Wire format is the same as POST /api/v3/math/validate-fuel.
"""

import argparse
import json
from pathlib import Path

from app.schemas.reference_math import FuelValidation
from app.services.reference_math import VERSION, validation_metrics


def evaluate_dataset(data):
    body = FuelValidation.model_validate(data)

    def metrics(rows):
        return validation_metrics(
            [row.predicted_fuel_l for row in rows],
            [row.observed_fuel_l for row in rows],
        )

    return {
        "overall": metrics(body.samples),
        "per_source": {
            source: metrics(
                [row for row in body.samples if row.reference_source == source]
            )
            for source in sorted({row.reference_source for row in body.samples})
        },
        "independent_vehicle_count": len({row.vehicle_id for row in body.samples}),
        "training_overlap": False,
        "accuracy_certified": False,
        "math_version": VERSION,
        "provenance_status": "caller_declared_not_independently_verified",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset",
        type=Path,
        help="JSON with paired fuel observations, sources and training vehicle IDs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional NEW private JSON report; never overwrites existing evidence",
    )
    args = parser.parse_args()
    try:
        report = evaluate_dataset(
            json.loads(args.dataset.read_text(encoding="utf-8-sig"))
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
    if args.output:
        import os

        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
