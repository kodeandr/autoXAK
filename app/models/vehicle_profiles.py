from typing import Dict
from pydantic import BaseModel, Field


class OilProfile(BaseModel):
    nominal_service_hours: float = Field(default=250.0, description="Паспортный ресурс масла, моточасы")
    base_activation_energy_jmol: float = Field(default=75000.0, description="Энергия активации базы, Дж/моль")
    aged_activation_energy_jmol: float = Field(default=45000.0, description="Энергия активации деструкции, Дж/моль")
    zddp_activation_volume_m3: float = Field(default=1.2e-29, description="Активационный объем ZDDP, м^3")
    zddp_activation_energy_jmol: float = Field(default=85000.0, description="Энергия механодеструкции ZDDP, Дж/моль")
    oil_grade: str = Field(default="5W-30", description="Класс вязкости по SAE")


class VehiclePhysicalProfile(BaseModel):
    car_id: str
    brand: str
    model: str
    curb_weight_kg: float = Field(..., description="Снаряженная масса, кг")
    rolling_resistance_coeff: float = Field(default=0.012, description="Коэффициент трения качения шин f_r")
    drag_coefficient_area: float = Field(..., description="Фактор аэродинамического сопротивления Cd * A, м^2")
    drivetrain_efficiency: float = Field(default=0.90, description="КПД трансмиссии eta")
    base_city_fuel_rate_l100km: float = Field(default=8.5, description="Паспортный городской расход топлива, л/100 км")
    engine_displacement_l: float = Field(default=1.5, description="Рабочий объем ДВС, л")
    rated_power_kw: float = Field(default=110.0, description="Номинальная мощность ДВС, кВт")
    oil_capacity_l: float = Field(default=4.0, description="Объем масляной ванны картера, л")
    oil_profile: OilProfile = Field(default_factory=OilProfile)


VEHICLE_REGISTRY: Dict[str, VehiclePhysicalProfile] = {
    # 1. Haval Jolion 1.5T 4WD (Тяжелый кроссовер 4WD, высокий мидель)
    "haval_jolion_15t": VehiclePhysicalProfile(
        car_id="haval_jolion_15t",
        brand="Haval",
        model="Jolion 1.5T 4WD",
        curb_weight_kg=1505.0,
        rolling_resistance_coeff=0.0135,
        drag_coefficient_area=0.36 * 2.38,   # 0.857 м^2
        drivetrain_efficiency=0.87,          # Потери в гидромуфте 4WD
        base_city_fuel_rate_l100km=9.8,
        engine_displacement_l=1.5,
        rated_power_kw=110.0,
        oil_capacity_l=3.8,
        oil_profile=OilProfile(oil_grade="0W-20", nominal_service_hours=250.0)
    ),

    # 2. Skoda Octavia 1.4 TSI (Легкий обтекаемый лифтбек FWD)
    "skoda_octavia_14tsi": VehiclePhysicalProfile(
        car_id="skoda_octavia_14tsi",
        brand="Skoda",
        model="Octavia 1.4 TSI",
        curb_weight_kg=1265.0,
        rolling_resistance_coeff=0.0105,
        drag_coefficient_area=0.28 * 2.18,   # 0.610 м^2
        drivetrain_efficiency=0.93,          # Эффективная FWD трансмиссия
        base_city_fuel_rate_l100km=7.0,
        engine_displacement_l=1.4,
        rated_power_kw=110.0,
        oil_capacity_l=4.0,
        oil_profile=OilProfile(oil_grade="0W-30", nominal_service_hours=250.0)
    ),

    # 3. Geely Coolray 1.5T DCT (Городской кроссовер FWD)
    "geely_coolray_15t": VehiclePhysicalProfile(
        car_id="geely_coolray_15t",
        brand="Geely",
        model="Coolray 1.5T",
        curb_weight_kg=1340.0,
        rolling_resistance_coeff=0.0120,
        drag_coefficient_area=0.32 * 2.25,   # 0.720 м^2
        drivetrain_efficiency=0.91,
        base_city_fuel_rate_l100km=8.1,
        engine_displacement_l=1.5,
        rated_power_kw=130.0,
        oil_capacity_l=4.0,
        oil_profile=OilProfile(oil_grade="0W-20", nominal_service_hours=250.0)
    )
}
