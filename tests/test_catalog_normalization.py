"""Identity, provenance, quarantine and read-only boundaries for catalogue data."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.cli.normalize_vehicle_catalog import run
from app.services.catalog_normalization import (
    CatalogInputError,
    Normalizer,
    Source,
    coverage,
    strict_json,
)


def source_for(raw, fmt="carsbase_taxonomy"):
    return Source(
        source_id="synthetic",
        format=fmt,
        revision="test-only",
        source_url="test:synthetic",
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        license_status="unresolved",
        license_evidence="Synthetic test observations only",
    )


def snapshot(data, fmt="carsbase_taxonomy"):
    raw = json.dumps(data, ensure_ascii=False).encode()
    return Normalizer(source_for(raw, fmt)).normalize(raw)


def taxonomy():
    return [
        {
            "id": "VAZ",
            "name": "Lada (ВАЗ)",
            "country": "Россия",
            "models": [
                {
                    "id": "VAZ_VESTA",
                    "mark_id": "VAZ",
                    "name": "Vesta, I",
                    "year_from": 2015,
                    "year_to": 2026,
                    "class": "B",
                }
            ],
        }
    ]


def legacy():
    return [
        {
            "make": {"id": "make", "name": "Make", "country": None},
            "models": [
                {
                    "id": "old-model-id",
                    "name": "Model",
                    "body_type": "sedan",
                    "trims": [
                        {
                            "id": "old-car-id",
                            "badge_name": "1.5T AT",
                            "curb_weight_kg": 1500,
                            "drivetrain_type": "AWD",
                            "transmission_type": "AT",
                            "nominal_service_hours": 250,
                        }
                    ],
                }
            ],
        }
    ]


def test_normalizes_aliases_and_unicode_without_inventing_generation_or_specs():
    data = taxonomy()
    data[0]["models"][0]["name"] = "  Vesta,\u00a0I  "
    result = snapshot(data)
    assert result["makes"][0]["id"] == "lada"
    assert result["makes"][0]["name"] == "LADA"
    model = result["models"][0]
    assert model["name"] == "Vesta, I"
    assert (
        model["generation"] is model["market"] is model["technical_parameters"] is None
    )
    assert model["years_claim"]["ongoing"] is None
    assert result["variants"] == []
    assert result["publication_status"] == "review_only_not_imported"
    assert not result["generation_or_variant_accuracy_verified"]


def test_ids_do_not_depend_on_revision_or_row_order_and_repeat_has_no_accumulation():
    data = taxonomy()
    data[0]["models"].append(dict(data[0]["models"][0], id="OTHER", name="Other"))
    raw = json.dumps(data).encode()
    normalizer = Normalizer(source_for(raw))
    first = normalizer.normalize(raw)
    assert normalizer.normalize(raw) == first
    data[0]["models"].reverse()
    second = snapshot(data)
    assert {m["source_ref"]["external_id"]: m["id"] for m in first["models"]} == {
        m["source_ref"]["external_id"]: m["id"] for m in second["models"]
    }


def test_does_not_merge_belgee_and_geely_or_solaris_brand_and_model():
    data = [
        {"id": make, "name": make, "models": []}
        for make in ("Geely", "BELGEE", "Solaris", "Hyundai")
    ]
    assert snapshot(data)["accepted_counts"]["makes"] == 4


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b"[NaN]", b"[Infinity]", b"[1e999]"])
def test_bad_json_cannot_silently_override_or_create_nonfinite_values(raw):
    with pytest.raises(CatalogInputError):
        strict_json(raw)


def test_snapshot_mismatch_rejected_before_processing():
    with pytest.raises(CatalogInputError, match="SHA-256"):
        Normalizer(source_for(b"[]")).normalize(b"[{}]")


@pytest.mark.parametrize("bad", ["  ID", "ID\u200b", "<img>", True])
def test_opaque_ids_are_not_rewritten_or_executed(bad):
    data = taxonomy()
    data[0]["models"][0]["id"] = bad
    result = snapshot(data)
    assert result["models"] == []
    assert result["rejected"]


def test_conflicting_duplicate_ids_quarantine_all_and_identical_duplicates_collapse():
    data = taxonomy()
    data[0]["models"] *= 2
    assert snapshot(data)["accepted_counts"]["models"] == 1
    data[0]["models"][1] = dict(data[0]["models"][1], name="Conflicting")
    result = snapshot(data)
    assert result["models"] == []
    assert len(result["rejected"]) == 2
    assert {r["code"] for r in result["rejected"]} == {"conflicting_duplicate_id"}


def test_same_legacy_variant_id_under_different_models_is_not_first_row_winner():
    data = legacy()
    other = deepcopy(data[0]["models"][0])
    other["id"] = "other-model"
    data[0]["models"].append(other)
    result = snapshot(data, "legacy_tree")
    assert len(result["models"]) == 2
    assert result["variants"] == []
    assert len(result["rejected"]) == 2


@pytest.mark.parametrize(
    "edit",
    [
        {"mark_id": "WRONG"},
        {"year_to": 1900},
        {"year_from": True},
        {"name": "<script>bad</script>"},
    ],
)
def test_parent_year_and_unsafe_label_errors_are_quarantined(edit):
    data = taxonomy()
    data[0]["models"][0].update(edit)
    result = snapshot(data)
    assert not result["models"]
    assert result["rejected"][0]["kind"] == "model"


def test_normalized_label_collision_does_not_merge_external_ids():
    data = taxonomy()
    data[0]["models"].append(dict(data[0]["models"][0], id="SECOND", name="VESTA, I"))
    result = snapshot(data)
    assert len(result["models"]) == 2
    assert sum(i["code"] == "normalized_label_collision" for i in result["issues"]) == 2


def test_legacy_parameters_are_claims_and_existing_vehicle_ids_survive():
    result = snapshot(legacy(), "legacy_tree")
    variant = result["variants"][0]
    assert variant["id"] == "old-car-id"
    assert variant["model_id"] == "old-model-id"
    assert variant["parameter_claims"]["curb_weight_kg"]["value"] == 1500
    assert (
        variant["parameter_claims"]["nominal_service_hours"]["status"]
        == "legacy_unverified"
    )
    assert all(value is None for value in variant["reference_parameters"].values())
    assert variant["engine_code"] is None


def test_invalid_parameter_does_not_discard_vehicle_or_turn_into_default():
    data = legacy()
    data[0]["models"][0]["trims"][0]["curb_weight_kg"] = True
    variant = snapshot(data, "legacy_tree")["variants"][0]
    assert variant["parameter_claims"]["curb_weight_kg"]["status"] == "rejected"
    assert variant["reference_parameters"]["curb_weight_kg"] is None


def test_legacy_literal_reader_never_executes_python(tmp_path):
    marker = tmp_path / "executed"
    raw = f"raise RuntimeError('not executed')\nTOP_FLEET_DATA = []\nopen({str(marker)!r}, 'w')\n".encode()
    assert Normalizer(source_for(raw, "legacy_seed")).normalize(raw)["models"] == []
    assert not marker.exists()
    for raw in (b"TOP_FLEET_DATA = list()", b"TOP_FLEET_DATA = [{'id':'a','id':'b'}]"):
        with pytest.raises(CatalogInputError):
            Normalizer(source_for(raw, "legacy_seed")).normalize(raw)


def test_malformed_legacy_container_cannot_be_partly_accepted():
    for data in (
        [{"make": [], "models": []}],
        [{"make": {"id": "m", "name": "M"}, "models": [{"id": "x", "name": "X"}]}],
    ):
        result = snapshot(data, "legacy_tree")
        assert not result["models"]
        assert result["rejected"]


def test_family_candidate_mapping_is_pinned_and_cannot_join_another_make():
    result = snapshot(taxonomy())
    requests = [{"priority": "P0", "make": "LADA", "model_or_family": "Search label"}]
    mapping = {
        "source_sha256": result["source"]["expected_sha256"],
        "relation": "search_candidates_only",
        "requests": [
            {
                "make": "LADA",
                "model_or_family": "Search label",
                "model_external_ids": ["VAZ_VESTA"],
                "rationale": "Synthetic reviewed search label",
            }
        ],
    }
    assert coverage(result, requests)[0]["candidate_count"] == 0
    matched = coverage(result, requests, mapping)[0]
    assert matched["candidate_count"] == 1
    assert not matched["exact_variant_match_verified"]
    assert len(result["models"]) == 1
    for edit in ({"source_sha256": "0" * 64}, {"relation": "merge_entities"}):
        with pytest.raises(CatalogInputError):
            coverage(result, requests, {**mapping, **edit})
    mapping["requests"][0]["make"] = "Other"
    with pytest.raises(CatalogInputError):
        coverage(result, requests, mapping)


def test_private_export_preserves_source_and_refuses_overwrite(tmp_path):
    raw = json.dumps(taxonomy()).encode()
    input_path, manifest_path = tmp_path / "input.json", tmp_path / "manifest.json"
    input_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(source_for(raw).__dict__))
    out = tmp_path / "review"
    result = run(input_path, manifest_path, out)
    assert result["error_count"] == 0
    assert (out / "source.snapshot").read_bytes() == raw
    assert (
        json.loads((out / "COMPLETE.json").read_text())["production_import_performed"]
        is False
    )
    assert out.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in out.iterdir())
    with pytest.raises(FileExistsError):
        run(input_path, manifest_path, out)
    assert (out / "source.snapshot").read_bytes() == raw


def test_repository_manifest_matches_seed_and_preserves_all_legacy_ids():
    raw = Path("seed_fleet.py").read_bytes()
    source = Source.load(Path("catalog/sources/legacy_seed.source.json").read_bytes())
    bundle = Normalizer(source).normalize(raw)
    assert bundle["accepted_counts"] == {"makes": 5, "models": 5, "variants": 9}
    assert not bundle["rejected"]
    assert sum(len(v["parameter_claims"]) for v in bundle["variants"]) == 99
    assert all(
        v["identity_status"] == "needs_year_market_engine_identification"
        for v in bundle["variants"]
    )


async def test_database_export_is_read_only_and_normalizes_without_changing_users_or_trips(
    tmp_path,
):
    from sqlalchemy import func, select

    from app.cli.export_vehicle_catalog import export
    from app.core.database import engine
    from app.models.db_models import Trip, User

    async with engine.connect() as connection:
        before = [
            await connection.scalar(select(func.count()).select_from(table))
            for table in (User, Trip)
        ]
    out = tmp_path / "db-export"
    counts = await export(engine, out)
    assert (
        json.loads((out / "COMPLETE.json").read_text())[
            "read_only_transaction_verified"
        ]
        is True
    )
    manifest = Source.load((out / "source.json").read_bytes())
    bundle = Normalizer(manifest).normalize((out / "catalog.json").read_bytes())
    assert bundle["input_counts"] == counts
    async with engine.connect() as connection:
        after = [
            await connection.scalar(select(func.count()).select_from(table))
            for table in (User, Trip)
        ]
    assert before == after
    await engine.dispose()


def test_frontend_literal_is_read_without_evaluating_javascript():
    raw = b"""const FALLBACK_CATALOG = [{id:'haval',name:'Haval',models:[
      {id:'model',name:'Model',trims:[{id:'car',badge_name:'AT',mass_kg:1500,fuel_rate:8.5}]}]}];
      throw new Error('this code must never run');"""
    bundle = Normalizer(source_for(raw, "frontend_fallback")).normalize(raw)
    variant = bundle["variants"][0]
    assert variant["parameter_claims"]["curb_weight_kg"]["value"] == 1500
    assert variant["parameter_claims"]["base_city_fuel_rate_l100km"]["value"] == 8.5
    assert all(value is None for value in variant["reference_parameters"].values())
    for expression in (
        b"someFunction()",
        b"process.env.SECRET_KEY",
        b"{id:'a',id:'b'}",
    ):
        broken = b"const FALLBACK_CATALOG = [" + expression + b"];"
        with pytest.raises(CatalogInputError):
            Normalizer(source_for(broken, "frontend_fallback")).normalize(broken)


def test_conflict_comparison_never_picks_a_value_or_joins_by_name():
    from app.services.catalog_normalization import compare_reviews

    a, b = legacy(), legacy()
    b[0]["models"][0]["trims"][0]["curb_weight_kg"] = 1600
    left, right = snapshot(a, "legacy_tree"), snapshot(b, "legacy_tree")
    before = deepcopy((left, right))
    result = compare_reviews(left, right)
    assert result["shared_legacy_ids"] == 1
    assert result["conflicts"][0]["field"] == "curb_weight_kg"
    assert result["records_merged"] is False
    assert (left, right) == before
    right["variants"][0]["id"] = "different-existing-id"
    assert compare_reviews(left, right)["conflicts"] == []


def test_optional_unknown_country_and_schema_drift_are_not_fabricated():
    data = taxonomy()
    data[0]["country"] = "   "
    data[0]["models"][0]["new_unknown_property"] = 123
    bundle = snapshot(data)
    assert bundle["makes"][0]["source_refs"][0]["country_claim"] is None
    assert any(issue["code"] == "unmapped_source_fields" for issue in bundle["issues"])
    assert bundle["models"][0]["technical_parameters"] is None


async def test_export_keeps_full_catalogue_rows_and_issues_only_read_statements(
    tmp_path,
):
    import uuid

    from sqlalchemy import delete, event, insert

    from app.cli.export_vehicle_catalog import export
    from app.core.database import engine
    from app.models.db_models import VehicleMake, VehicleModel, VehicleTrim

    identity = "catalog-test-" + uuid.uuid4().hex[:12]
    tables = (VehicleMake.__table__, VehicleModel.__table__, VehicleTrim.__table__)
    async with engine.begin() as conn:
        await conn.execute(
            insert(tables[0]).values(id=identity, name=identity, country=None)
        )
        await conn.execute(
            insert(tables[1]).values(
                id=identity, make_id=identity, name="Synthetic", body_type="sedan"
            )
        )
        await conn.execute(
            insert(tables[2]).values(
                id=identity,
                model_id=identity,
                badge_name="Synthetic variant",
                curb_weight_kg=1234,
                drag_coefficient_area=0.5,
                engine_displacement_l=1.5,
                rated_power_kw=100,
                oil_capacity_l=4,
                oil_grade="test-only",
                oil_spec="not a real specification",
            )
        )
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.split()[0].upper())

    try:
        event.listen(engine.sync_engine, "before_cursor_execute", observe)
        try:
            out = tmp_path / "filled-export"
            await export(engine, out)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe)
        assert set(statements) <= {"SET", "SHOW", "SELECT"}
        rows = json.loads((out / "catalog.json").read_text())
        make = next(r for r in rows if r["make"]["id"] == identity)
        trim = make["models"][0]["trims"][0]
        assert trim["curb_weight_kg"] == 1234
        manifest = Source.load((out / "source.json").read_bytes())
        bundle = Normalizer(manifest).normalize((out / "catalog.json").read_bytes())
        assert (
            next(v for v in bundle["variants"] if v["id"] == identity)[
                "parameter_claims"
            ]["curb_weight_kg"]["status"]
            == "legacy_unverified"
        )
    finally:
        async with engine.begin() as conn:
            for table in reversed(tables):
                await conn.execute(delete(table).where(table.c.id == identity))
        await engine.dispose()
