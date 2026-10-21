from pydantic import BaseModel, Field, AliasChoices


class CustomVehicleInput(BaseModel):
    model_config = {"extra": "allow", "allow_inf_nan": False, "populate_by_name": True}
    brand: str = Field(default="Custom", min_length=1, max_length=100)
    model: str = Field(default="Vehicle", min_length=1, max_length=100)
    custom_name: str | None = Field(default=None, max_length=200)
    body_type: str = Field(default="sedan", max_length=50)
    engine_displacement_l: float = Field(default=1.5, gt=0.5, le=8.0, validation_alias=AliasChoices("engine_displacement_l", "engine_disp_l"))
    engine_power_hp: float = Field(default=122.0, gt=30.0, le=1500.0, validation_alias=AliasChoices("engine_power_hp", "power_hp"))
    drivetrain: str = Field(default="FWD", max_length=20, validation_alias=AliasChoices("drivetrain", "drivetrain_type", "drive_type"))
    transmission: str = Field(default="MT", max_length=20, validation_alias=AliasChoices("transmission", "transmission_type"))
    is_turbo: bool = False
    fuel_type: str = Field(default="gasoline", max_length=20)
    curb_weight_kg: float = Field(default=1280.0, gt=500.0, le=4500.0)

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