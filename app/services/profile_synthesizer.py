import hashlib
import json
from typing import Any
from app.schemas.vehicle import CustomVehicleInput
from app.models.vehicle_profiles import OilProfile

# Экспорт псевдонима для обратной совместимости с app/main.py
SyntheticCarInput = CustomVehicleInput


class SynthesizedProfile(dict):
    """
    Гибридный профиль ТС: поддерживает доступ по ключу d['key'],
    через точку d.attr и сериализуется FastAPI в JSON.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(
                f"'SynthesizedProfile' object has no attribute '{name}'"
            )

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class ProfileSynthesizer:
    @classmethod
    def synthesize_profile(cls, input_data: CustomVehicleInput) -> SynthesizedProfile:
        """Preserve declared attributes; body type cannot identify CdA or oil capacity."""
        data = input_data.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[
            :24
        ]
        conversion = {"metric_ps": 0.73549875, "mechanical_hp": 0.7456998715822702}.get(
            input_data.engine_power_unit
        )
        power_kw = (
            input_data.engine_power_hp * conversion
            if input_data.engine_power_hp is not None and conversion is not None
            else None
        )
        return SynthesizedProfile(
            car_id="custom_" + digest,
            car_name=input_data.custom_name or f"{input_data.brand} {input_data.model}",
            brand=input_data.brand,
            model=input_data.model,
            curb_weight_kg=input_data.curb_weight_kg,
            engine_displacement_l=input_data.engine_displacement_l,
            engine_disp_l=input_data.engine_displacement_l,
            power_hp=input_data.engine_power_hp,
            rated_power_kw=power_kw,
            drivetrain_efficiency=None,
            transmission_efficiency=None,
            drag_coefficient_area=None,
            cd_area=None,
            rolling_resistance_coeff=None,
            base_city_fuel_rate_l100km=None,
            oil_capacity_l=None,
            fuel_type=input_data.fuel_type,
            turbo=input_data.is_turbo,
            oil_profile=OilProfile(),
            parameter_status="user_declared_incomplete_not_calibrated",
        )