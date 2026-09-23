import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, 
    String, 
    Float, 
    Integer, 
    DateTime, 
    ForeignKey, 
    Index
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class FuelRegionalPrice(Base):
    __tablename__ = "fuel_regional_prices"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    region_code = Column(String(10), nullable=False, index=True)
    region_name = Column(String(100), nullable=False)
    brand = Column(String(50), nullable=False, index=True)
    brand_display_name = Column(String(100), nullable=False)

    price_ai92 = Column(Float, nullable=False)
    price_ai95 = Column(Float, nullable=False)
    price_ai100 = Column(Float, nullable=False)
    price_dt = Column(Float, nullable=False)

    availability_status = Column(String(30), default="NORMAL")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fuel_region_brand", "region_code", "brand", unique=True),
    )


class VehicleMake(Base):
    __tablename__ = "vehicle_makes"

    id = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    country = Column(String(50), nullable=True)

    models = relationship("VehicleModel", back_populates="make", cascade="all, delete-orphan")


class VehicleModel(Base):
    __tablename__ = "vehicle_models"

    id = Column(String(80), primary_key=True)
    make_id = Column(String(50), ForeignKey("vehicle_makes.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    body_type = Column(String(50), nullable=False)

    make = relationship("VehicleMake", back_populates="models")
    trims = relationship("VehicleTrim", back_populates="model", cascade="all, delete-orphan")


class VehicleTrim(Base):
    __tablename__ = "vehicle_trims"

    id = Column(String(100), primary_key=True)
    model_id = Column(String(80), ForeignKey("vehicle_models.id", ondelete="CASCADE"), nullable=False)
    badge_name = Column(String(120), nullable=False)

    curb_weight_kg = Column(Float, nullable=False)
    drag_coefficient_area = Column(Float, nullable=False)
    rolling_resistance_coeff = Column(Float, default=0.012)
    drivetrain_efficiency = Column(Float, default=0.90)

    engine_displacement_l = Column(Float, nullable=False)
    rated_power_kw = Column(Float, nullable=False)
    drivetrain_type = Column(String(20), default="FWD")
    transmission_type = Column(String(20), default="AT")

    oil_capacity_l = Column(Float, nullable=False)
    oil_grade = Column(String(20), nullable=False)
    oil_spec = Column(String(50), nullable=False)
    recommended_oil_brand = Column(String(100), default="LUKOIL GENESIS")
    nominal_service_hours = Column(Float, default=250.0)

    model = relationship("VehicleModel", back_populates="trims")


class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    car_id = Column(String(100), default="haval_jolion_15t_4wd")
    fuel_price_rub = Column(Float, default=72.40)
    service_cost_rub = Column(Float, default=9500.0)
    total_savings_rub = Column(Float, default=0.0)
    current_oil_wear_percent = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    trips = relationship("Trip", back_populates="user", cascade="all, delete-orphan")


class Trip(Base):
    __tablename__ = "trips"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    car_id = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    duration_seconds = Column(Float, nullable=False)
    distance_km = Column(Float, nullable=False)
    equivalent_engine_hours = Column(Float, nullable=False)
    oil_wear_percent = Column(Float, nullable=False)
    idle_ratio = Column(Float, default=0.0)
    fuel_saved_rub = Column(Float, default=0.0)
    oil_saved_rub = Column(Float, default=0.0)
    total_savings_rub = Column(Float, default=0.0)

    user = relationship("User", back_populates="trips")

    __table_args__ = (
        Index("ix_trips_user_created", "user_id", "created_at"),
    )


class CPALead(Base):
    __tablename__ = "cpa_leads"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    car_id = Column(String(100), nullable=False)
    partner_id = Column(String(64), nullable=False)
    promo_code = Column(String(64), nullable=False)
    discount_rub = Column(Float, default=500.0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
