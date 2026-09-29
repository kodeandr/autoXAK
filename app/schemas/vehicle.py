from pydantic import BaseModel, Field
from typing import Optional, Any


class CustomVehicleInput(BaseModel):
    brand: str = Field(default="Geely")
    model: str = Field(default="Emgrand")
    body_type: str = Field(default="sedan")
    engine_displacement_l: float = Field(default=1.5, gt=0.5, le=8.0)
    engine_disp_l: float = Field(default=1.5)
    engine_power_hp: float = Field(default=122.0, gt=30.0, le=1500.0)
    power_hp: float = Field(default=122.0)
    drive_type: str = Field(default="fwd")
    drivetrain: str = Field(default="FWD")
    transmission: str = Field(default="MT")
    is_turbo: bool = Field(default=False)
    fuel_type: str = Field(default="gasoline")
    curb_weight_kg: float = Field(default=1280.0, gt=500.0, le=4500.0)

    class Config:
        extra = "allow"

    def __init__(self, **data: Any):
        disp = data.get("engine_displacement_l") or data.get("engine_disp_l") or 1.5
        data["engine_displacement_l"] = float(disp)
        data["engine_disp_l"] = float(disp)

        pwr = data.get("engine_power_hp") or data.get("power_hp") or 120.0
        data["engine_power_hp"] = float(pwr)
        data["power_hp"] = float(pwr)

        dt = data.get("drivetrain") or data.get("drive_type") or "fwd"
        data["drivetrain"] = str(dt).upper()
        data["drive_type"] = str(dt).lower()

        if "transmission" not in data:
            data["transmission"] = "MT"
        if "is_turbo" not in data:
            data["is_turbo"] = bool((float(pwr) / max(float(disp), 0.1)) > 95.0 or "turbo" in str(data.get("model", "")).lower())

        super().__init__(**data)


class VehicleCatalogItem(BaseModel):
    id: str
    brand: str
    model: str
    body_type: str
    power_hp: float
    oil_capacity_l: float
    oil_grade: str