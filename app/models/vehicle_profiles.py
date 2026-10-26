from typing import Dict, Optional
from pydantic import BaseModel, Field


class OilProfile(BaseModel):
    nominal_service_hours: Optional[float] = Field(default=None, description="Паспортный ресурс масла, моточасы")
    base_activation_energy_jmol: Optional[float] = Field(default=None, description="Энергия активации базы, Дж/моль")
    aged_activation_energy_jmol: Optional[float] = Field(default=None, description="Энергия активации деструкции, Дж/моль")
    zddp_activation_volume_m3: Optional[float] = Field(default=None, description="Активационный объем ZDDP, м^3")
    zddp_activation_energy_jmol: Optional[float] = Field(default=None, description="Энергия механодеструкции ZDDP, Дж/моль")
    oil_grade: Optional[str] = Field(default=None, description="Класс вязкости по SAE")


# Псевдоним для совместимости со старыми модулями
OilTribologyProfile = OilProfile


class VehiclePhysicalProfile(BaseModel):
    car_id: str
    brand: str
    model: str
    curb_weight_kg: Optional[float] = Field(default=None, description="Снаряженная масса, кг")
    rolling_resistance_coeff: Optional[float] = Field(default=None, description="Коэффициент трения качения шин f_r")
    drag_coefficient_area: Optional[float] = Field(default=None, description="Фактор аэродинамического сопротивления Cd * A, м^2")
    drivetrain_efficiency: Optional[float] = Field(default=None, description="КПД трансмиссии eta")
    base_city_fuel_rate_l100km: Optional[float] = Field(default=None, description="Паспортный городской расход топлива, л/100 км")
    engine_displacement_l: Optional[float] = Field(default=None, description="Рабочий объем ДВС, л")
    rated_power_kw: Optional[float] = Field(default=None, description="Номинальная мощность ДВС, кВт")
    oil_capacity_l: Optional[float] = Field(default=None, description="Объем масляной ванны картера, л")
    oil_profile: OilProfile = Field(default_factory=OilProfile)


VEHICLE_REGISTRY: Dict[str, VehiclePhysicalProfile] = {
    "haval_jolion_15t": VehiclePhysicalProfile(
        car_id="haval_jolion_15t",
        brand="Haval",
        model="Jolion 1.5T 4WD",
        curb_weight_kg=1505.0,
        rolling_resistance_coeff=0.0135,
        drag_coefficient_area=0.36 * 2.38,
        drivetrain_efficiency=0.87,
        base_city_fuel_rate_l100km=9.8,
        engine_displacement_l=1.5,
        rated_power_kw=110.0,
        oil_capacity_l=3.8,
        oil_profile=OilProfile(oil_grade="0W-20", nominal_service_hours=250.0)
    ),
    "skoda_octavia_14tsi": VehiclePhysicalProfile(
        car_id="skoda_octavia_14tsi",
        brand="Skoda",
        model="Octavia 1.4 TSI",
        curb_weight_kg=1265.0,
        rolling_resistance_coeff=0.0105,
        drag_coefficient_area=0.28 * 2.18,
        drivetrain_efficiency=0.93,
        base_city_fuel_rate_l100km=7.0,
        engine_displacement_l=1.4,
        rated_power_kw=110.0,
        oil_capacity_l=4.0,
        oil_profile=OilProfile(oil_grade="0W-30", nominal_service_hours=250.0)
    ),
    "geely_coolray_15t": VehiclePhysicalProfile(
        car_id="geely_coolray_15t",
        brand="Geely",
        model="Coolray 1.5T",
        curb_weight_kg=1340.0,
        rolling_resistance_coeff=0.0120,
        drag_coefficient_area=0.32 * 2.25,
        drivetrain_efficiency=0.91,
        base_city_fuel_rate_l100km=8.1,
        engine_displacement_l=1.5,
        rated_power_kw=130.0,
        oil_capacity_l=4.0,
        oil_profile=OilProfile(oil_grade="0W-20", nominal_service_hours=250.0)
    )
}


def get_default_profile() -> VehiclePhysicalProfile:
    """Извлекает эталонный профиль ТС из реестра или создает резервный B2C-профиль."""
    if VEHICLE_REGISTRY:
        if "haval_jolion_15t" in VEHICLE_REGISTRY:
            return VEHICLE_REGISTRY["haval_jolion_15t"]
        return next(iter(VEHICLE_REGISTRY.values()))

    return VehiclePhysicalProfile(car_id='unknown',brand='Unknown',model='Unknown')


def resolve_car_profile(car_id: Optional[str]) -> VehiclePhysicalProfile:
    """Резолвит профиль ТС по идентификатору."""
    if car_id and car_id in VEHICLE_REGISTRY:
        return VEHICLE_REGISTRY[car_id]
    return VehiclePhysicalProfile(car_id=car_id or "unknown",brand="Unknown",model="Unknown")
