from typing import Literal
from pydantic import BaseModel, Field, AliasChoices


class CustomVehicleInput(BaseModel):
    model_config = {"extra": "allow", "allow_inf_nan": False, "populate_by_name": True}
    brand: str = Field(default="Custom", min_length=1, max_length=100)
    model: str = Field(default="Vehicle", min_length=1, max_length=100)
    custom_name: str | None = Field(default=None, max_length=200)
    body_type: str = Field(default="sedan", max_length=50)
    engine_displacement_l: float | None = Field(default=None, gt=0.5, le=8.0, validation_alias=AliasChoices("engine_displacement_l", "engine_disp_l"))
    engine_power_hp: float | None = Field(default=None, gt=30.0, le=1500.0, validation_alias=AliasChoices("engine_power_hp", "power_hp"))
    drivetrain: str = Field(default="FWD", max_length=20, validation_alias=AliasChoices("drivetrain", "drivetrain_type", "drive_type"))
    transmission: str = Field(default="MT", max_length=20, validation_alias=AliasChoices("transmission", "transmission_type"))
    engine_power_unit: Literal['metric_ps','mechanical_hp'] | None = None
    is_turbo: bool | None = None
    fuel_type: str = Field(default="unknown", max_length=20)
    curb_weight_kg: float | None = Field(default=None, gt=500.0, le=4500.0)

    @property
    def engine_disp_l(self):
        return self.engine_displacement_l

    @property
    def power_hp(self):
        return self.engine_power_hp


class VehicleCatalogItem(BaseModel):
    id: str
    brand: str
    model: str
    body_type: str
    power_hp: float
    oil_capacity_l: float
    oil_grade: str