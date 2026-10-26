"""Authenticated numerical workbench. No trip writes or financial credits."""

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_user
from app.core.database import get_db
from app.models.db_models import Trip
from app.models.recordings import TripMathEvidenceRecord
from app.schemas.reference_math import FuelComparison, FuelValidation, ReferenceRun
from app.services.reference_math import (
    VERSION,
    compare_fuel,
    fuel_from_map,
    integral_linear,
    integrate_fuel_rate,
    road_energy,
    time_below,
    trajectory_metrics,
    validation_metrics,
)

router = APIRouter(prefix="/api/v3/math", tags=["Reference mathematics"])


@router.get("/trips/{trip_id}/evidence")
async def evidence(
    trip_id: str, user=Depends(require_user), db: AsyncSession = Depends(get_db)
):
    record = await db.scalar(
        select(TripMathEvidenceRecord)
        .join(Trip, Trip.id == TripMathEvidenceRecord.trip_id)
        .where(Trip.id == trip_id, Trip.user_id == user.id)
    )
    if record is None:
        raise HTTPException(404, "Calculation evidence not found")
    return {
        "trip_id": record.trip_id,
        "math_version": record.math_version,
        "inputs_sha256": record.inputs_sha256,
        "inputs": record.inputs,
        "result": record.result,
    }


def evaluate_run(body):
    result = {
        "math_version": VERSION,
        "kinematics": trajectory_metrics(
            body.times_s, body.speeds_mps, idle_threshold_mps=body.idle_threshold_mps
        ),
        "road_energy": None,
        "observed_fuel_l": None,
        "mapped_fuel": None,
        "oil_wear_percent": None,
        "oil_status": "unavailable_without_validated_degradation_model",
        "observed_engine_running_hours": None,
        "observed_crankshaft_revolutions": None,
        "validation_status": "numerically_evaluated_inputs_not_independently_verified",
    }
    if body.road_parameters is not None:
        result["road_energy"] = road_energy(
            body.times_s,
            body.speeds_mps,
            body.elevations_m,
            body.tailwind_mps,
            body.road_parameters.model_dump(),
        )
    if body.fuel_rate_lph is not None:
        result["observed_fuel_l"] = integrate_fuel_rate(
            body.times_s, body.fuel_rate_lph, interpolation=body.fuel_rate_interpolation
        )
        result["fuel_rate_source"] = body.fuel_rate_source
    if body.fuel_map is not None:
        result["mapped_fuel"] = fuel_from_map(
            body.times_s,
            body.engine_rpm,
            body.torque_nm,
            body.engine_on,
            body.fuel_map.model_dump(),
        )
    if body.engine_rpm is not None:
        rpm = np.asarray(body.engine_rpm, float)
        result["observed_engine_running_hours"] = (
            time_below(body.times_s, -rpm, 0) / 3600
        )
        result["observed_crankshaft_revolutions"] = (
            integral_linear(body.times_s, rpm) / 60
        )
    return result


@router.post("/evaluate")
async def evaluate(body: ReferenceRun, user=Depends(require_user)):
    try:
        return await run_in_threadpool(evaluate_run, body)
    except (
        ValueError,
        OverflowError,
        FloatingPointError,
        np.linalg.LinAlgError,
    ) as error:
        raise HTTPException(422, str(error)) from error


@router.post("/compare-fuel")
async def compare(body: FuelComparison, user=Depends(require_user)):
    try:
        return compare_fuel(
            body.actual.model_dump(),
            body.reference.model_dump(),
            price_rub_per_l=body.price_rub_per_l,
        )
    except (ValueError, OverflowError) as error:
        raise HTTPException(422, str(error)) from error


@router.post("/validate-fuel")
async def validate(body: FuelValidation, user=Depends(require_user)):
    def metrics(rows):
        return validation_metrics(
            [row.predicted_fuel_l for row in rows],
            [row.observed_fuel_l for row in rows],
        )

    per_source = {
        source: metrics([row for row in body.samples if row.reference_source == source])
        for source in sorted({row.reference_source for row in body.samples})
    }
    return {
        "overall": metrics(body.samples),
        "per_source": per_source,
        "independent_vehicle_count": len({row.vehicle_id for row in body.samples}),
        "training_overlap": False,
        "provenance_status": "caller_declared_not_independently_verified",
        "accuracy_certified": False,
        "math_version": VERSION,
    }
