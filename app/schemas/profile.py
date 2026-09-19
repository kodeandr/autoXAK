# app/schemas/profile.py
from pydantic import BaseModel, Field


class UserProfileUpdatePayload(BaseModel):
    user_id: str
    car_id: str = Field(default="haval_jolion_15t", description="Идентификатор ТС в реестре")
    current_oil_wear_percent: float = Field(default=0.0, ge=0.0, le=100.0, description="Текущий накопленный износ масла, %")
    fuel_price_rub: float = Field(default=62.00, gt=0.0, description="Цена литра топлива, ₽")
    service_cost_rub: float = Field(default=9500.0, gt=0.0, description="Стоимость планового ТО, ₽")