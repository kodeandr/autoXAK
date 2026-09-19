"""
Модуль физико-технических профилей автомобилей и смазочных материалов.
Опирается на калиброванные данные CMEM (SRC-06), Tripathi & Vinu (SRC-01),
Zhang et al. (SRC-03) и Chen et al. (SRC-04).
"""
from pydantic import BaseModel, Field
from typing import Dict

class OilTribologyProfile(BaseModel):
    oil_grade: str = Field(..., description="Класс вязкости по SAE J300 (напр. 5W-30)")
    base_activation_energy_jmol: float = Field(
        default=106170.0, 
        description="Кажущаяся энергия активации свежего масла E_a (Дж/моль), Tripathi & Vinu [SRC-01]"
    )
    aged_activation_energy_jmol: float = Field(
        default=91900.0, 
        description="Энергия активации сработавшегося масла (Дж/моль) после разрушения антиоксидантов [SRC-01]"
    )
    zddp_activation_energy_jmol: float = Field(
        default=53000.0, 
        description="Внутренняя энергия активации реакции ZDDP E_0 (Дж/моль), Zhang & Spikes [SRC-02]"
    )
    zddp_activation_volume_m3: float = Field(
        default=1.8e-28, 
        description="Активационный объем реакции ZDDP Delta V_act (м^3) [SRC-02]"
    )
    nominal_service_hours: float = Field(
        default=250.0, 
        description="Номинальный ресурс гидрокрекинговой синтетики в городском цикле (моточасы)"
    )

class VehiclePhysicalProfile(BaseModel):
    car_id: str
    brand: str
    model: str
    curb_weight_kg: float = Field(..., description="Снаряженная масса ТС m (кг) [SRC-06]")
    drag_coefficient_area: float = Field(..., description="Фактор лобового сопротивления C_d * A (м^2) [SRC-06]")
    rolling_resistance_coeff: float = Field(default=0.0135, description="Коэффициент сопротивления качению f_roll [SRC-06]")
    drivetrain_efficiency: float = Field(default=0.90, description="Механический КПД трансмиссии eta_trans [SRC-06]")
    engine_displacement_l: float = Field(..., description="Рабочий объем ДВС (л)")
    rated_power_kw: float = Field(..., description="Номинальная мощность ДВС (кВт)")
    idle_rpm: float = Field(default=800.0, description="Обороты холостого хода прогретого ДВС (об/мин)")
    oil_capacity_l: float = Field(..., description="Заправочный объем масляного картера (л)")
    idle_fuel_rate_lph: float = Field(default=0.85, description="Расход топлива на холостом ходу (л/ч)")
    base_city_fuel_rate_l100km: float = Field(default=9.2, description="Паспортный городской расход F_pass (л/100 км)")
    oil_profile: OilTribologyProfile

# Калиброванный реестр фокусного парка (B2C-ядро)
VEHICLE_REGISTRY: Dict[str, VehiclePhysicalProfile] = {
    "haval_jolion_15t": VehiclePhysicalProfile(
        car_id="haval_jolion_15t",
        brand="Haval",
        model="Jolion 1.5T 4WD",
        curb_weight_kg=1505.0,
        drag_coefficient_area=0.32 * 2.38,
        engine_displacement_l=1.5,
        rated_power_kw=110.0,
        oil_capacity_l=3.8,
        oil_profile=OilTribologyProfile(oil_grade="5W-30")
    ),
    "chery_tiggo7_15t": VehiclePhysicalProfile(
        car_id="chery_tiggo7_15t",
        brand="Chery",
        model="Tiggo 7 Pro 1.5T",
        curb_weight_kg=1540.0,
        drag_coefficient_area=0.33 * 2.42,
        engine_displacement_l=1.5,
        rated_power_kw=108.0,
        oil_capacity_l=4.1,
        oil_profile=OilTribologyProfile(oil_grade="5W-30")
    )
}