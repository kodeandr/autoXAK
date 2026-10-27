"""Export only vehicle catalogue tables in one PostgreSQL read-only snapshot.

Users, trips, credentials and connection URLs are not exported. This is a data
review tool, not a migration, seed or importer. Output must be a NEW directory.
"""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

MAX_ROWS = 200000


class CatalogExportError(ValueError):
    pass


def encode(value):
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode()


def write_private(path, raw):
    # Self-contained so this script can be piped into an existing API container
    # without installing the new normalization package or restarting production.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


async def read_catalog(db_engine):
    from sqlalchemy import select, text

    from app.models.db_models import VehicleMake, VehicleModel, VehicleTrim

    if db_engine.dialect.name != "postgresql":
        raise CatalogExportError("A PostgreSQL read-only transaction is required")
    async with db_engine.connect() as connection, connection.begin():
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        if await connection.scalar(text("SHOW transaction_read_only")) != "on":
            raise CatalogExportError("Database did not confirm read-only mode")
        tables = []
        for model in (VehicleMake, VehicleModel, VehicleTrim):
            records = (
                (
                    await connection.execute(
                        select(model.__table__).order_by(model.id).limit(MAX_ROWS + 1)
                    )
                )
                .mappings()
                .all()
            )
            if len(records) > MAX_ROWS:
                raise CatalogExportError("Catalogue export row limit exceeded")
            tables.append([dict(record) for record in records])
    makes, models, variants = tables
    make_lookup = {m["id"]: {"make": m, "models": []} for m in makes}
    model_lookup = {}
    for model in models:
        if model["make_id"] not in make_lookup:
            raise CatalogExportError("Orphan model found; export cannot hide it")
        entry = {k: v for k, v in model.items() if k != "make_id"}
        entry["trims"] = []
        model_lookup[model["id"]] = entry
        make_lookup[model["make_id"]]["models"].append(entry)
    for variant in variants:
        if variant["model_id"] not in model_lookup:
            raise CatalogExportError("Orphan variant found; export cannot hide it")
        model_lookup[variant["model_id"]]["trims"].append(
            {k: v for k, v in variant.items() if k != "model_id"}
        )
    return list(make_lookup.values()), {
        "makes": len(makes),
        "models": len(models),
        "variants": len(variants),
    }


async def export(db_engine, output_dir):
    data, counts = await read_catalog(db_engine)
    raw = encode(data)
    manifest = {
        "source_id": "server_catalog",
        "format": "legacy_tree",
        "revision": datetime.now(UTC).isoformat(),
        "source_url": "database:vehicle_makes+vehicle_models+vehicle_trims",
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "license_status": "unresolved",
        "license_evidence": "Read-only database snapshot. Original sources and rights have not been established; review only.",
    }
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_private(output_dir / "catalog.json", raw)
    write_private(output_dir / "source.json", encode(manifest))
    write_private(
        output_dir / "COMPLETE.json",
        encode(
            {
                "status": "complete",
                "counts": counts,
                "read_only_transaction_verified": True,
                "source_sha256": manifest["expected_sha256"],
            }
        ),
    )
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    async def run():
        from app.core.database import engine

        try:
            return await export(engine, args.output_dir)
        finally:
            await engine.dispose()

    try:
        counts = asyncio.run(run())
    except Exception as error:  # noqa: BLE001 -- prevent driver details/DSNs in CLI output
        # Driver error strings can contain deployment details; do not dump a DSN.
        parser.exit(
            2,
            f"Catalogue export failed ({type(error).__name__}); check the database and output path locally.\n",
        )
    print(
        json.dumps(
            {"output_dir": str(args.output_dir), "counts": counts, "read_only": True}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
