from typing import Any, Dict
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
            raise AttributeError(f"'SynthesizedProfile' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class ProfileSynthesizer:
    AERODYNAMIC_MAP = {
        "SEDAN": 0.65,
        "HATCHBACK": 0.68,
        "LIFTBACK": 0.66,
        "CROSSOVER": 0.78,
        "SUV": 0.92,
    }

    TRANSMISSION_EFFICIENCY = {
        "MT": 0.94,
        "MANUAL": 0.94,
        "AT": 0.89,
        "AUTOMATIC": 0.89,
        "ROBOT": 0.92,
        "DCT": 0.92,
        "CVT": 0.88,
    }

    @classmethod
    def synthesize_profile(cls, input_data: CustomVehicleInput) -> SynthesizedProfile:
        body = str(getattr(input_data, "body_type", "sedan")).upper()
        cd_area = cls.AERODYNAMIC_MAP.get(body, 0.70)

        trans = str(getattr(input_data, "transmission", "MT")).upper()
        eta = cls.TRANSMISSION_EFFICIENCY.get(trans, 0.90)

        drivetrain = str(getattr(input_data, "drivetrain", getattr(input_data, "drive_type", "fwd"))).upper()
        if drivetrain in ["AWD", "4WD"]:
            eta -= 0.02

        disp = float(getattr(input_data, "engine_disp_l", getattr(input_data, "engine_displacement_l", 1.5)))
        power_hp = float(getattr(input_data, "power_hp", getattr(input_data, "engine_power_hp", 120.0)))
        power_kw = round(power_hp * 0.7355, 1)

        weight = float(getattr(input_data, "curb_weight_kg", 1350.0))

        oil_cap = round(max(3.8, 3.2 + disp * 0.7), 1)
        if body == "SUV" or weight > 1800.0:
            oil_cap = max(oil_cap, 4.8)

        brand = str(input_data.brand)
        model = str(input_data.model)
        car_id = f"{brand.lower()}_{model.lower()}_custom"

        return SynthesizedProfile(
            car_id=car_id,
            brand=brand,
            model=model,
            curb_weight_kg=weight,
            drag_coefficient_area=cd_area,
            cd_area=cd_area,
            drivetrain_efficiency=eta,
            transmission_efficiency=eta,
            engine_displacement_l=disp,
            engine_disp_l=disp,
            rated_power_kw=power_kw,
            power_hp=power_hp,
            oil_capacity_l=oil_cap,
            rolling_resistance_coeff=0.012,
            base_city_fuel_rate_l100km=round(7.0 + (weight - 1200) * 0.003, 1),
            oil_profile=OilProfile(
                oil_grade="5W-30" if disp >= 1.6 else "0W-20",
                nominal_service_hours=250.0
            )
        )