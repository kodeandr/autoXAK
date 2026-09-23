from typing import Dict, Any
from pydantic import BaseModel, Field


class SyntheticCarInput(BaseModel):
    body_type: str = Field(..., description="sedan, crossover, suv, hatchback")
    engine_disp_l: float = Field(..., ge=0.8, le=6.0)
    is_turbo: bool = Field(default=True)
    drivetrain: str = Field(default="FWD", description="FWD, AWD, RWD")
    transmission: str = Field(default="AT", description="MT, AT, DCT, CVT")
    curb_weight_kg: float = Field(default=1400.0, ge=800.0, le=3500.0)


class ProfileSynthesizer:
    """
    Параметрический генератор физических констант ТС на основе 
    статистических моделей автомобильного инжиниринга.
    """

    BODY_AERO_DEFAULTS = {
        "sedan": {"cd": 0.28, "frontal_area": 2.18},     # Cd*A ~ 0.61
        "liftback": {"cd": 0.29, "frontal_area": 2.20},  # Cd*A ~ 0.64
        "hatchback": {"cd": 0.31, "frontal_area": 2.15}, # Cd*A ~ 0.67
        "crossover": {"cd": 0.33, "frontal_area": 2.35}, # Cd*A ~ 0.77
        "suv": {"cd": 0.38, "frontal_area": 2.65},       # Cd*A ~ 1.00
    }

    TRANSMISSION_EFFICIENCY = {
        "MT": 0.95,   # Механическая КПП
        "DCT": 0.92,  # Роботизированная с двумя сцеплениями
        "AT": 0.88,   # Классический гидромеханический автомат
        "CVT": 0.89   # Бесступенчатый вариатор
    }

    @classmethod
    def synthesize_profile(cls, input_data: SyntheticCarInput) -> Dict[str, Any]:
        # 1. Аэродинамика
        aero = cls.BODY_AERO_DEFAULTS.get(input_data.body_type.lower(), cls.BODY_AERO_DEFAULTS["crossover"])
        cd_a = round(aero["cd"] * aero["frontal_area"], 3)

        # 2. КПД трансмиссии
        eta = cls.TRANSMISSION_EFFICIENCY.get(input_data.transmission.upper(), 0.90)
        if input_data.drivetrain.upper() == "AWD":
            eta = round(eta - 0.04, 2)  # Дополнительные потери в раздатке и редукторах

        # 3. Сопротивление качению
        f_r = 0.0135 if input_data.body_type.lower() in ["crossover", "suv"] else 0.0115

        # 4. Мощность двигателя (оценка)
        if input_data.is_turbo:
            power_kw = round(input_data.engine_disp_l * 75.0, 1)  # ~100 л.с./литр
        else:
            power_kw = round(input_data.engine_disp_l * 52.0, 1)  # ~70 л.с./литр

        # 5. Объем масляной системы и допуск
        oil_vol = round(input_data.engine_disp_l * 1.2 + 2.1, 1)
        if input_data.is_turbo:
            oil_grade = "0W-20" if input_data.curb_weight_kg < 1600 else "5W-30"
            oil_spec = "API SP / ILSAC GF-6A"
        else:
            oil_grade = "5W-30"
            oil_spec = "API SN / ACEA A3/B4"

        return {
            "car_id": f"synth_{input_data.body_type}_{int(input_data.engine_disp_l*10)}_{input_data.drivetrain.lower()}",
            "curb_weight_kg": input_data.curb_weight_kg,
            "drag_coefficient_area": cd_a,
            "rolling_resistance_coeff": f_r,
            "drivetrain_efficiency": eta,
            "engine_displacement_l": input_data.engine_disp_l,
            "rated_power_kw": power_kw,
            "oil_capacity_l": oil_vol,
            "oil_grade": oil_grade,
            "oil_spec": oil_spec,
            "nominal_service_hours": 250.0
        }
