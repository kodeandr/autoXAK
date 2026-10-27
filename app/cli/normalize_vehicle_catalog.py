"""Normalize pinned catalogue snapshots into private, review-only files.

No data is fetched, published or imported into the running application. All source
bytes and the manifest are preserved. Existing output directories are never reused.
"""

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from app.services.catalog_normalization import (
    MAX_BYTES,
    CatalogInputError,
    Normalizer,
    Source,
    compare_reviews,
    coverage,
    strict_json,
)


def read_bounded(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise CatalogInputError("Input exceeds 16 MiB")
    return data


def summary(bundle, candidates):
    matched = sum(bool(row["candidate_count"]) for row in candidates)
    return {
        "normalization_version": bundle["normalization_version"],
        "source": bundle["source"],
        "publication_status": bundle["publication_status"],
        "input_counts": bundle["input_counts"],
        "accepted_counts": bundle["accepted_counts"],
        "rejected_counts": dict(Counter(r["kind"] for r in bundle["rejected"])),
        "issues": dict(Counter(r["code"] for r in bundle["issues"])),
        "error_count": sum(r["severity"] == "error" for r in bundle["issues"]),
        "legacy_parameter_claims": sum(
            len(v["parameter_claims"]) for v in bundle["variants"]
        ),
        "usable_reference_parameters": sum(
            value is not None
            for variant in bundle["variants"]
            for value in variant["reference_parameters"].values()
        ),
        "generation_or_variant_accuracy_verified": False,
        "requested_families": len(candidates),
        "requested_families_with_candidates": matched,
        "requested_families_without_candidates": len(candidates) - matched,
        "candidate_matching_is_not_market_or_exact_variant_coverage": True,
    }


def write_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def encode(value):
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode()


def run(
    input_path,
    manifest_path,
    output_dir,
    requests_path=None,
    aliases_path=None,
    compare_path=None,
):
    manifest_bytes = read_bounded(manifest_path)
    source = Source.load(manifest_bytes)
    raw = read_bounded(input_path)
    bundle = Normalizer(source).normalize(raw)
    candidates = []
    requests_bytes = None
    aliases_bytes = read_bounded(aliases_path) if aliases_path else None
    if aliases_path is not None and requests_path is None:
        raise CatalogInputError("Candidate mappings require a coverage request CSV")
    if requests_path is not None:
        requests_bytes = read_bounded(requests_path)
        requests = list(csv.DictReader(requests_bytes.decode("utf-8-sig").splitlines()))
        if len(requests) > 10000 or any(
            not {"make", "model_or_family", "priority"}.issubset(row)
            for row in requests
        ):
            raise CatalogInputError("Invalid coverage request CSV")
        candidates = coverage(
            bundle, requests, strict_json(aliases_bytes) if aliases_bytes else None
        )
    result = summary(bundle, candidates)
    comparison_bytes = read_bounded(compare_path) if compare_path else None
    comparison = (
        compare_reviews(bundle, strict_json(comparison_bytes))
        if comparison_bytes
        else None
    )
    if comparison is not None:
        result["comparison_shared_legacy_ids"] = comparison["shared_legacy_ids"]
        result["comparison_conflicts"] = len(comparison["conflicts"])
    # Creating exclusively and writing completion last keeps interrupted output
    # distinguishable from a finished review. Never overwrite previous evidence.
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_private(output_dir / "source.snapshot", raw)
    write_private(output_dir / "source.manifest.json", manifest_bytes)
    if requests_bytes is not None:
        write_private(output_dir / "requests.csv", requests_bytes)
    if aliases_bytes is not None:
        write_private(output_dir / "candidate-mappings.json", aliases_bytes)
    write_private(output_dir / "normalized.json", encode(bundle))
    write_private(output_dir / "coverage.json", encode(candidates))
    write_private(output_dir / "report.json", encode(result))
    if comparison is not None:
        write_private(output_dir / "comparison-input.json", comparison_bytes)
        write_private(output_dir / "conflicts.json", encode(comparison))
    fingerprints = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(output_dir.iterdir())
    }
    write_private(
        output_dir / "COMPLETE.json",
        encode(
            {
                "status": "complete",
                "source_sha256": source.expected_sha256,
                "normalization_version": bundle["normalization_version"],
                "production_import_performed": False,
                "files_sha256": fingerprints,
            }
        ),
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--aliases", type=Path)
    parser.add_argument("--compare-review", type=Path)
    args = parser.parse_args()
    try:
        result = run(
            args.input,
            args.manifest,
            args.output_dir,
            args.coverage,
            args.aliases,
            args.compare_review,
        )
    except (
        OSError,
        CatalogInputError,
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
    ) as error:
        parser.exit(2, f"Normalization failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 1 if result["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
