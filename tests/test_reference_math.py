"""Analytical answers, conservation laws and metamorphic checks, not snapshots."""

import math
from copy import deepcopy

import numpy as np
import pytest

from app.services.reference_math import (
    G,
    compare_fuel,
    fuel_from_map,
    integral_linear,
    integrate_fuel_rate,
    road_energy,
    time_below,
    trajectory_metrics,
    validation_metrics,
)
from app.services.twin_engine import TwinEngine
from app.services.wear_engine import WearEngine


def parameters(**overrides):
    # These are explicitly synthetic test parameters, not production defaults.
    return dict(
        mass_kg=1000,
        rotating_equivalent_mass_kg=0,
        crr=0,
        cda_m2=0,
        air_density_kg_m3=1,
        drivetrain_efficiency=1,
        **overrides,
    )


def test_constant_velocity_matches_analytic_rolling_and_drag_work():
    p = parameters()
    p.update(crr=0.01, cda_m2=0.6, drivetrain_efficiency=0.8)
    r = road_energy([0, 10], [10, 10], [0, 0], [0, 0], p)
    assert r["distance_m"] == 100
    assert r["kinetic_j"] == 0
    assert r["rolling_j"] == pytest.approx(1000 * G * 0.01 * 100)
    assert r["aerodynamic_j"] == pytest.approx(0.5 * 0.6 * 10**2 * 100)
    assert r["positive_shaft_work_j"] == pytest.approx(r["net_wheel_work_j"] / 0.8)
    assert r["balance_residual_j"] == pytest.approx(0, abs=1e-9)
    assert r["fuel_l"] is None


def test_acceleration_and_braking_preserve_kinetic_energy_with_rotating_mass():
    p = parameters()
    p["rotating_equivalent_mass_kg"] = 100
    r = road_energy([0, 10, 20], [0, 10, 0], [0, 0, 0], [0, 0, 0], p)
    assert r["positive_wheel_work_j"] == pytest.approx(55000)
    assert r["negative_wheel_work_j"] == pytest.approx(-55000)
    assert r["kinetic_j"] == pytest.approx(0)
    assert r["net_wheel_work_j"] == pytest.approx(0)


def test_closed_elevation_loop_cancels_potential_energy_not_traction_demand():
    r = road_energy([0, 10, 20], [10, 10, 10], [0, 10, 0], [0, 0, 0], parameters())
    assert r["grade_j"] == pytest.approx(0)
    assert r["positive_wheel_work_j"] == pytest.approx(1000 * G * 10)
    assert r["negative_wheel_work_j"] == pytest.approx(-1000 * G * 10)


def test_air_relative_velocity_sign_crossing_is_integrated_analytically():
    p = parameters()
    p["cda_m2"] = 2
    r = road_energy([0, 2], [1, 1], [0, 0], [0, 2], p)
    assert r["positive_wheel_work_j"] == pytest.approx(1 / 3)
    assert r["negative_wheel_work_j"] == pytest.approx(-1 / 3)
    assert r["net_wheel_work_j"] == pytest.approx(0, abs=1e-12)


def test_wheel_power_sign_crossing_splits_traction_from_braking():
    # Synthetic: v=20-2t, P=-2000v+10v³. The interior root is sqrt(200).
    # Integrating P dt on either side gives +50000 J and -50000 J.
    p = parameters()
    p.update(cda_m2=10, air_density_kg_m3=2)
    r = road_energy([0, 10], [20, 0], [0, 0], [0, 0], p)
    assert r["positive_wheel_work_j"] == pytest.approx(50000, rel=1e-12)
    assert r["negative_wheel_work_j"] == pytest.approx(-50000, rel=1e-12)
    assert r["net_wheel_work_j"] == pytest.approx(0, abs=1e-8)
    assert r["balance_residual_j"] == pytest.approx(0, abs=1e-8)


def test_subdividing_linear_observations_does_not_change_work():
    p = parameters()
    p.update(crr=0.01, cda_m2=0.6)
    original = road_energy([0, 10], [3, 13], [0, 0], [1, 2], p)
    t = np.linspace(0, 10, 101)
    refined = road_energy(t, 3 + t, np.zeros(101), 1 + t / 10, p)
    for key in (
        "kinetic_j",
        "rolling_j",
        "grade_j",
        "aerodynamic_j",
        "positive_wheel_work_j",
        "negative_wheel_work_j",
    ):
        assert refined[key] == pytest.approx(original[key], rel=1e-10, abs=1e-7)


def test_irregular_timestamps_and_threshold_crossings_are_exact():
    assert integral_linear([0, 1, 10], [0, 2, 2]) == 19
    assert time_below([0, 1, 10], [0, 2, 2], 1) == 0.5
    assert time_below([0, 1, 10], [0, 2, 2], 2) == 1
    assert trajectory_metrics([1000, 1001, 1010], [0, 2, 2])["distance_m"] == 19


@pytest.mark.parametrize(
    "times,speeds",
    [
        ([0, 0], [0, 1]),
        ([0, 1], [0, -1]),
        ([0, math.nan], [0, 1]),
        ([0, 1], [0, math.inf]),
    ],
)
def test_invalid_observations_are_rejected(times, speeds):
    with pytest.raises(ValueError):
        trajectory_metrics(times, speeds)


def test_missing_parameters_and_impossible_elevation_are_rejected():
    with pytest.raises(ValueError):
        road_energy([0, 1], [1, 1], [0, 0], [0, 0], {})
    with pytest.raises(ValueError):
        road_energy([0, 1], [1, 1], [0, 2], [0, 0], parameters())


def test_fuel_integration_requires_explicit_time_semantics():
    assert integrate_fuel_rate([0, 3600], [1, 2], interpolation="previous") == 1
    assert integrate_fuel_rate([0, 3600], [1, 2], interpolation="linear") == 1.5
    with pytest.raises(ValueError):
        integrate_fuel_rate([0, 3600], [1, -2], interpolation="linear")
    with pytest.raises(ValueError):
        integrate_fuel_rate([0, 3600], [1, 2], interpolation="unknown")


def test_bilinear_map_crossing_multiple_cells_matches_exact_polynomial_integral():
    x, y = [0, 1000, 1500, 2000], [0, 50, 100]
    calibration = {
        "rpm": x,
        "torque_nm": y,
        "source": "synthetic polynomial test",
        "fuel_rate_lph": [
            [1 + 0.001 * r + 0.01 * q + 0.00001 * r * q for q in y] for r in x
        ],
    }
    r = fuel_from_map([0, 10], [1000, 2000], [0, 100], [True], calibration)
    assert r["fuel_l"] == pytest.approx((2 + 3 / 2 + 1 / 3) * 10 / 3600, rel=1e-12)
    with pytest.raises(ValueError):
        fuel_from_map([0, 10], [1000, 2001], [0, 100], [True], calibration)


def test_no_map_extrapolation_or_unobserved_engine_off_assumption():
    c = {
        "rpm": [800, 2000],
        "torque_nm": [0, 100],
        "fuel_rate_lph": [[1, 2], [2, 3]],
        "source": "synthetic test",
    }
    assert fuel_from_map([0, 10], [0, 0], [0, 0], [False], c)["fuel_l"] == 0
    with pytest.raises(ValueError):
        fuel_from_map([0, 10], [800, 800], [0, 0], [False], c)
    with pytest.raises(ValueError):
        fuel_from_map([0, 10], [0, 0], [0, 0], [True], c)


def test_fuel_comparison_is_signed_and_uses_decimal_money_rounding():
    actual = {
        "vehicle_id": "car",
        "route_id": "route",
        "context_id": "conditions",
        "source": "test observation",
        "distance_m": 1000,
        "duration_s": 100,
        "fuel_l": 0.7,
    }
    reference = dict(actual, fuel_l=0.6)
    r = compare_fuel(actual, reference, price_rub_per_l=70)
    assert r["delta_fuel_l"] == -0.1
    assert r["delta_cost_rub"] == -7
    assert r["causal_savings_verified"] is False
    assert (
        compare_fuel(
            dict(actual, fuel_l=0), dict(actual, fuel_l=0.005), price_rub_per_l=1
        )["delta_cost_rub"]
        == 0.01
    )
    with pytest.raises(ValueError):
        compare_fuel(actual, dict(reference, distance_m=1001), price_rub_per_l=70)
    with pytest.raises(ValueError):
        compare_fuel(actual, dict(reference, route_id="other"), price_rub_per_l=70)
    with pytest.raises(ValueError):
        compare_fuel(actual, dict(reference, duration_s=101), price_rub_per_l=70)


def test_unknown_uncertainty_does_not_turn_into_zero_error():
    actual = {
        "vehicle_id": "car",
        "route_id": "r",
        "context_id": "c",
        "source": "test",
        "distance_m": 1000,
        "duration_s": 100,
        "fuel_l": 0.6,
    }
    reference = dict(actual, fuel_l=0.7)
    assert (
        compare_fuel(actual, reference, price_rub_per_l=70)[
            "declared_uncertainty_bounds"
        ]
        is None
    )
    result = compare_fuel(
        dict(actual, uncertainty_l=0.1),
        dict(reference, uncertainty_l=0.1),
        price_rub_per_l=70,
    )
    assert result["declared_uncertainty_bounds"]["delta_fuel_l"] == pytest.approx(
        [-0.1, 0.3]
    )
    assert result["declared_uncertainty_bounds"]["delta_cost_rub"] == pytest.approx(
        [-7, 21]
    )
    empty = dict(actual, fuel_l=0, uncertainty_l=0.1)
    bounded = compare_fuel(empty, empty, price_rub_per_l=70)
    assert bounded["declared_uncertainty_bounds"]["delta_fuel_l"] == pytest.approx(
        [-0.1, 0.1]
    )
    assert compare_fuel(actual, reference, price_rub_per_l=0)["delta_cost_rub"] == 0


def test_aggregate_error_metrics_do_not_hide_zero_references_or_certify_accuracy():
    result = validation_metrics([0, 1, 3], [0, 2, 2])
    assert result["mae_l"] == pytest.approx(2 / 3)
    assert result["rmse_l"] == pytest.approx(math.sqrt(2 / 3))
    assert result["wape_percent"] == 50
    assert result["zero_reference_count"] == 1
    assert result["accuracy_certified"] is False
    assert validation_metrics([0], [0])["wape_percent"] is None


def test_no_fuel_or_oil_credits_are_invented_from_smartphone_motion():
    for speed in (np.zeros(100), np.full(100, 10)):
        wear = WearEngine().compute_oil_wear(
            speed, np.full(100, 5), traffic_score=10, times_s=np.arange(100)
        )
        assert wear["oil_wear_percent"] is wear["equivalent_hours"] is None
        savings = TwinEngine().evaluate_financial_delta(
            distance_km=10, speeds_mps=speed, user_wear_percent=1
        )
        assert savings["total_savings_rub"] is savings["fuel_saved_liters"] is None


async def test_reference_api_is_authenticated_and_never_credits_account(
    committed_client,
):
    client = committed_client
    before = (
        await client.get(f"/api/v1/users/{client.audit_user_id}/dashboard")
    ).json()
    body = {
        "times_s": [0, 10],
        "speeds_mps": [10, 10],
        "idle_threshold_mps": 1,
        "fuel_rate_lph": [3.6, 3.6],
        "fuel_rate_interpolation": "linear",
        "fuel_rate_source": "synthetic API check",
    }
    response = await client.post("/api/v3/math/evaluate", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["observed_fuel_l"] == pytest.approx(0.01)
    assert response.json()["oil_wear_percent"] is None
    assert (
        await client.post(
            "/api/v3/math/evaluate", json=body, headers={"Authorization": ""}
        )
    ).status_code == 401
    assert (
        await client.get(f"/api/v1/users/{client.audit_user_id}/dashboard")
    ).json() == before
    assert (await client.get(f"/api/v1/users/{client.audit_user_id}/trips")).json()[
        "trips"
    ] == []
    broken = deepcopy(body)
    broken["speeds_mps"] = [10]
    assert (await client.post("/api/v3/math/evaluate", json=broken)).status_code == 422
    broken = deepcopy(body)
    broken["speeds_mps"] = [True, 10]
    assert (await client.post("/api/v3/math/evaluate", json=broken)).status_code == 422


async def test_validation_api_rejects_duplicate_trips_and_training_overlap(
    committed_client,
):
    sample = {
        "trip_id": "a",
        "vehicle_id": "held_out",
        "reference_source": "synthetic",
        "predicted_fuel_l": 1,
        "observed_fuel_l": 2,
    }
    request = {"samples": [sample], "training_vehicle_ids": ["training_vehicle"]}
    response = await committed_client.post("/api/v3/math/validate-fuel", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["overall"]["wape_percent"] == 50
    assert response.json()["accuracy_certified"] is False
    assert response.json()["per_source"]["synthetic"]["count"] == 1
    request["training_vehicle_ids"] = ["held_out"]
    assert (
        await committed_client.post("/api/v3/math/validate-fuel", json=request)
    ).status_code == 422
    request["training_vehicle_ids"] = []
    request["samples"] *= 2
    assert (
        await committed_client.post("/api/v3/math/validate-fuel", json=request)
    ).status_code == 422


async def test_zero_motion_does_not_imply_zero_fuel_or_oil_condition(committed_client):
    request = {
        "times_s": [0, 3600],
        "speeds_mps": [0, 0],
        "idle_threshold_mps": 1,
        "engine_rpm": [800, 800],
        "fuel_rate_lph": [0.8, 0.8],
        "fuel_rate_interpolation": "previous",
        "fuel_rate_source": "synthetic observed stream",
    }
    response = await committed_client.post("/api/v3/math/evaluate", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["observed_fuel_l"] == pytest.approx(0.8)
    assert result["observed_engine_running_hours"] == 1
    assert result["observed_crankshaft_revolutions"] == 48000
    assert result["oil_wear_percent"] is None


async def test_unavailable_oil_and_money_do_not_change_stored_balances(
    committed_client, synthetic_telemetry_batch
):
    client = committed_client
    before = (await client.get(f"/api/v1/users/{client.audit_user_id}/profile")).json()
    payload = dict(synthetic_telemetry_batch, user_id=client.audit_user_id)
    result = await client.post("/api/v1/telemetry/session", json=payload)
    assert result.status_code == 200, result.text
    assert (
        result.json()["oil_wear_percent"] is result.json()["cost_savings_rub"] is None
    )
    dashboard = (
        await client.get(f"/api/v1/users/{client.audit_user_id}/dashboard")
    ).json()
    assert (
        dashboard["total_savings_rub"]
        == dashboard["total_fuel_saved_liters"]
        == dashboard["range_bank_km"]
        == 0
    )
    after = (await client.get(f"/api/v1/users/{client.audit_user_id}/profile")).json()
    assert after["current_oil_wear_percent"] == before["current_oil_wear_percent"]
    history = (
        await client.get(f"/api/v1/users/{client.audit_user_id}/analytics/period")
    ).json()["trips"]
    assert history[0]["total_savings_rub"] is history[0]["oil_wear_percent"] is None
    assert history[0]["fuel_saved_liters"] is None
    assert history[0]["range_bonus_km"] is None
    assert history[0]["rush_cost_rub"] is None
    assert history[0]["math_status"]["monetary_credit_status"] == "not_verified"


def test_unknown_new_gps_metadata_preserves_legacy_canonical_payload():
    from app.schemas.recordings import GPSPoint

    original = {
        "timestamp_ms": 1700000000000,
        "latitude": 55.75,
        "longitude": 37.61,
        "accuracy_m": 5.0,
        "speed_mps": 10.0,
        "speed_source": "gps",
    }
    assert GPSPoint.model_validate(original).model_dump(mode="json") == original
    expanded = dict(
        original,
        altitude_m=None,
        altitude_accuracy_m=None,
        heading_deg=None,
        speed_accuracy_mps=None,
    )
    assert GPSPoint.model_validate(expanded).model_dump(mode="json") == original


async def test_new_gps_metadata_is_retained_without_breaking_old_retries(
    committed_client,
):
    import uuid

    start = 1700000000000
    payload = {
        "session_id": str(uuid.uuid4()),
        "user_id": committed_client.audit_user_id,
        "car_id": "haval_jolion_15t",
        "telemetry_stream": [],
        "raw_recording": {
            "started_at_ms": start,
            "ended_at_ms": start + 10000,
            "gps": [
                {
                    "timestamp_ms": start + i * 1000,
                    "latitude": 55.75,
                    "longitude": 37.61,
                    "accuracy_m": 5,
                    "speed_mps": 10,
                    "speed_source": "gps",
                }
                for i in range(11)
            ],
        },
    }
    for gps in payload["raw_recording"]["gps"]:
        gps.update(
            altitude_m=120.5,
            altitude_accuracy_m=8,
            heading_deg=90,
            speed_accuracy_mps=0.2,
        )
    response = await committed_client.post("/api/v1/telemetry/session", json=payload)
    assert response.status_code == 200, response.text
    exported = await committed_client.get(
        "/api/v2/trips/" + response.json()["session_id"] + "/export"
    )
    point = exported.json()["gps"][0]
    assert point["altitude_m"] == 120.5 and point["altitude_accuracy_m"] == 8
    assert point["heading_deg"] == 90 and point["speed_accuracy_mps"] == 0.2
    assert (
        await committed_client.post("/api/v1/telemetry/session", json=payload)
    ).json() == response.json()


async def test_unknown_vehicle_does_not_inherit_default_car_physics(async_client):
    from app.core.database import get_db
    from app.main import app, resolve_car_profile_async

    async for db in app.dependency_overrides[get_db]():
        profile = await resolve_car_profile_async("unknown-vehicle", db)
        assert profile.car_id == "unknown-vehicle"
        assert (
            profile.curb_weight_kg
            is profile.drag_coefficient_area
            is profile.engine_displacement_l
            is None
        )


def test_vertical_alignment_does_not_claim_an_unidentified_forward_axis():
    from app.core.dsp.alignment import IMUAlignmentService

    z = np.full(100, 9.80665)
    transformed = IMUAlignmentService().calibrate_and_transform(
        np.zeros(100), np.zeros(100), z, np.full(100, 10)
    )
    assert transformed[3]["vertical_aligned"] is True
    assert transformed[3]["forward_axis_identified"] is False
    assert transformed[3]["is_aligned"] is False


async def test_reference_evidence_is_bound_to_recording_and_saved_atomically(
    committed_client,
):
    import uuid

    start = 1700000000000
    payload = {
        "session_id": str(uuid.uuid4()),
        "user_id": committed_client.audit_user_id,
        "car_id": "haval_jolion_15t",
        "telemetry_stream": [],
        "raw_recording": {
            "started_at_ms": start,
            "ended_at_ms": start + 10000,
            "gps": [
                {
                    "timestamp_ms": start + i * 1000,
                    "latitude": 55.75,
                    "longitude": 37.61,
                    "accuracy_m": 5,
                    "speed_mps": 10,
                    "speed_source": "gps",
                }
                for i in range(11)
            ],
        },
    }
    observed = {
        "vehicle_id": payload["car_id"],
        "route_id": "synthetic-route",
        "context_id": "synthetic-context",
        "source": "synthetic test",
        "distance_m": 100,
        "duration_s": 10,
        "fuel_l": 0.01,
    }
    payload["reference_math"] = {
        "run": {
            "times_s": list(range(11)),
            "speeds_mps": [10] * 11,
            "idle_threshold_mps": 1,
            "fuel_rate_lph": [3.6] * 11,
            "fuel_rate_source": "synthetic meter",
            "fuel_rate_interpolation": "linear",
        },
        "fuel_comparison": {
            "actual": observed,
            "reference": dict(observed, fuel_l=0.02),
            "price_rub_per_l": 70,
        },
    }
    response = await committed_client.post("/api/v1/telemetry/session", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    evaluation = data["metrics"]["math_status"]["reference_evaluation"]
    assert evaluation["observed_fuel_l"] == pytest.approx(0.01)
    assert evaluation["fuel_comparison"]["delta_cost_rub"] == 0.7
    assert evaluation["fuel_comparison"]["causal_savings_verified"] is False
    assert data["cost_savings_rub"] is None
    exported = (
        await committed_client.get("/api/v2/trips/" + data["session_id"] + "/export")
    ).json()
    assert exported["fuel_estimate"]["reference_evaluation"] == evaluation
    saved = (
        await committed_client.get(exported["reference_math_evidence"]["url"])
    ).json()
    assert saved["inputs"]["run"]["fuel_rate_lph"] == [3.6] * 11
    assert (
        saved["inputs_sha256"] == exported["reference_math_evidence"]["inputs_sha256"]
    )
    assert saved["result"] == evaluation
    from sqlalchemy import delete

    from app.core.database import async_session_factory
    from app.models.db_models import User

    other = (await committed_client.post("/api/v1/auth/guest", json={})).json()
    try:
        denied = await committed_client.get(
            exported["reference_math_evidence"]["url"],
            headers={"Authorization": "Bearer " + other["token"]},
        )
        assert denied.status_code == 404
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(User).where(User.id == other["user_id"]))
            await db.commit()
    from app.core.security import create_access_token

    assert (
        await committed_client.get(
            exported["reference_math_evidence"]["url"],
            headers={
                "Authorization": "Bearer " + create_access_token("nonexistent-owner")
            },
        )
    ).status_code == 401
    assert (
        await committed_client.post("/api/v1/telemetry/session", json=payload)
    ).json() == data
    dashboard = (
        await committed_client.get(
            f"/api/v1/users/{committed_client.audit_user_id}/dashboard"
        )
    ).json()
    assert dashboard["total_trips"] == 1 and dashboard["total_savings_rub"] == 0
    # Unrelated reference velocities are rejected as analysis, while raw data survives.
    changed = deepcopy(payload)
    changed["session_id"] = str(uuid.uuid4())
    changed["reference_math"]["run"]["speeds_mps"][0] = 9
    result = await committed_client.post("/api/v1/telemetry/session", json=changed)
    assert result.status_code == 200, result.text
    assert (
        result.json()["metrics"]["math_status"]["reference_evaluation"]["status"]
        == "rejected"
    )
    assert (
        await committed_client.get(
            "/api/v2/trips/" + result.json()["session_id"] + "/export"
        )
    ).json()["point_counts"]["gps"] == 11


async def test_pre_reference_request_hash_still_returns_original_receipt(
    committed_client, synthetic_telemetry_batch
):
    import hashlib
    import json
    import uuid

    from app.core.database import async_session_factory
    from app.models.db_models import Trip, User
    from app.models.telemetry import TripSessionPayload

    client = committed_client
    body = dict(synthetic_telemetry_batch, user_id=client.audit_user_id)
    canonical = TripSessionPayload.model_validate(body).model_dump(mode="json")
    # This is the v1 canonical wire contract, before reference_math existed.
    for key in ("user_id", "raw_recording", "reference_math"):
        canonical.pop(key, None)
    digest = hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    identifier = str(uuid.uuid4())
    old_receipt = {
        "session_id": identifier,
        "duration_seconds": 60,
        "distance_km": 1,
        "oil_wear_percent": 0.01,
        "cost_savings_rub": 1.23,
        "status": "PROCESSED",
    }
    async with async_session_factory() as db:
        db.add(
            Trip(
                id=identifier,
                user_id=client.audit_user_id,
                car_id=body["car_id"],
                client_session_id=body["session_id"],
                request_hash=digest,
                calculation_result=old_receipt,
                duration_seconds=60,
                distance_km=1,
                total_savings_rub=1.23,
            )
        )
        user = await db.get(User, client.audit_user_id)
        user.total_savings_rub = 1.23
        user.total_trips = 1
        await db.commit()
    response = await client.post("/api/v1/telemetry/session", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["cost_savings_rub"] == 1.23
    assert (await client.get(f"/api/v1/users/{client.audit_user_id}/dashboard")).json()[
        "total_trips"
    ] == 1
