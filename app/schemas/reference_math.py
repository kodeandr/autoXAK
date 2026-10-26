"""Explicit inputs; unknown coefficients have no numerical defaults."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MathInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, strict=True, str_strip_whitespace=True
    )


Time = Annotated[float, Field(ge=-253402300800, le=253402300800)]
Speed = Annotated[float, Field(ge=0, le=150)]
Elevation = Annotated[float, Field(ge=-100000, le=100000)]
Wind = Annotated[float, Field(ge=-200, le=200)]
FuelRate = Annotated[float, Field(ge=0, le=100000)]
Rpm = Annotated[float, Field(ge=0, le=20000)]
Torque = Annotated[float, Field(ge=-100000, le=100000)]


class RoadParameters(MathInput):
    mass_kg: float = Field(gt=0, le=100000)
    rotating_equivalent_mass_kg: float = Field(ge=0, le=100000)
    crr: float = Field(ge=0, le=1)
    cda_m2: float = Field(ge=0, le=100)
    air_density_kg_m3: float = Field(gt=0, le=10)
    drivetrain_efficiency: float = Field(gt=0, le=1)
    source: str = Field(min_length=1, max_length=1000)


class FuelMap(MathInput):
    rpm: list[Rpm] = Field(min_length=2, max_length=100)
    torque_nm: list[Torque] = Field(min_length=2, max_length=100)
    fuel_rate_lph: list[
        Annotated[list[FuelRate], Field(min_length=2, max_length=100)]
    ] = Field(min_length=2, max_length=100)
    source: str = Field(min_length=1, max_length=1000)


class ReferenceRun(MathInput):
    times_s: list[Time] = Field(min_length=2, max_length=10000)
    speeds_mps: list[Speed] = Field(min_length=2, max_length=10000)
    idle_threshold_mps: float = Field(ge=0)
    elevations_m: list[Elevation] | None = Field(
        default=None, min_length=2, max_length=10000
    )
    tailwind_mps: list[Wind] | None = Field(
        default=None, min_length=2, max_length=10000
    )
    road_parameters: RoadParameters | None = None
    fuel_rate_lph: list[FuelRate] | None = Field(
        default=None, min_length=2, max_length=10000
    )
    fuel_rate_source: str | None = Field(default=None, min_length=1, max_length=1000)
    fuel_rate_interpolation: Literal["linear", "previous"] | None = None
    engine_rpm: list[Rpm] | None = Field(default=None, min_length=2, max_length=10000)
    torque_nm: list[Torque] | None = Field(default=None, min_length=2, max_length=10000)
    engine_on: list[bool] | None = Field(default=None, min_length=1, max_length=9999)
    fuel_map: FuelMap | None = None

    @model_validator(mode="after")
    def complete_inputs(self):
        n = len(self.times_s)
        for name in (
            "speeds_mps",
            "elevations_m",
            "tailwind_mps",
            "fuel_rate_lph",
            "engine_rpm",
            "torque_nm",
        ):
            value = getattr(self, name)
            if value is not None and len(value) != n:
                raise ValueError(f"{name} must align with timestamps")
        if self.engine_on is not None and len(self.engine_on) != n - 1:
            raise ValueError("engine_on must describe every interval")
        if any(
            value is not None
            for value in (self.road_parameters, self.elevations_m, self.tailwind_mps)
        ) and any(
            value is None
            for value in (self.road_parameters, self.elevations_m, self.tailwind_mps)
        ):
            raise ValueError(
                "Road energy requires parameters, elevations and wind together"
            )
        if self.fuel_rate_lph is not None and (
            self.fuel_rate_source is None or self.fuel_rate_interpolation is None
        ):
            raise ValueError("Fuel-rate source and interpolation are required")
        if self.fuel_map is not None and any(
            value is None for value in (self.engine_rpm, self.torque_nm, self.engine_on)
        ):
            raise ValueError(
                "Fuel map requires measured RPM, torque and interval engine state"
            )
        return self


class FuelObservation(MathInput):
    fuel_l: float = Field(ge=0, le=100000)
    distance_m: float = Field(ge=0, le=20000000)
    duration_s: float = Field(gt=0, le=86400)
    uncertainty_l: float | None = Field(default=None, ge=0, le=100000)
    vehicle_id: str = Field(min_length=1, max_length=100)
    route_id: str = Field(min_length=1, max_length=200)
    context_id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=1000)


class FuelComparison(MathInput):
    actual: FuelObservation
    reference: FuelObservation
    price_rub_per_l: float = Field(ge=0, le=10000)


class TripMathEvidence(MathInput):
    run: ReferenceRun
    fuel_comparison: FuelComparison | None = None


class ValidationSample(MathInput):
    trip_id: str = Field(min_length=1, max_length=200)
    vehicle_id: str = Field(min_length=1, max_length=100)
    reference_source: Literal[
        "calibrated_meter",
        "ecu_fuel_rate",
        "trip_computer_report",
        "maf_derived",
        "synthetic",
    ]
    predicted_fuel_l: float = Field(ge=0, le=100000)
    observed_fuel_l: float = Field(ge=0, le=100000)


class FuelValidation(MathInput):
    samples: list[ValidationSample] = Field(min_length=1, max_length=10000)
    training_vehicle_ids: list[str] = Field(max_length=10000)

    @model_validator(mode="after")
    def reject_leakage(self):
        if len({sample.trip_id for sample in self.samples}) != len(self.samples):
            raise ValueError("Duplicate trips cannot inflate validation sample count")
        if {sample.vehicle_id for sample in self.samples}.intersection(
            self.training_vehicle_ids
        ):
            raise ValueError("Validation vehicles overlap declared training vehicles")
        return self
