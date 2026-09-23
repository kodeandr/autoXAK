from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    car_id = Column(String, default="haval_jolion_15t", nullable=False)
    fuel_price_rub = Column(Float, default=62.0, nullable=False)
    service_cost_rub = Column(Float, default=9500.0, nullable=False)
    total_savings_rub = Column(Float, default=0.0, nullable=False)
    current_oil_wear_percent = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    trips = relationship("Trip", back_populates="user", cascade="all, delete-orphan")
    leads = relationship("CPALead", back_populates="user", cascade="all, delete-orphan")


class Trip(Base):
    __tablename__ = "trips"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    car_id = Column(String, default="haval_jolion_15t", nullable=False)
    duration_seconds = Column(Float, nullable=False)
    distance_km = Column(Float, nullable=False)
    equivalent_engine_hours = Column(Float, nullable=False)
    oil_wear_percent = Column(Float, nullable=False)
    idle_ratio = Column(Float, nullable=False)
    fuel_saved_rub = Column(Float, nullable=False)
    oil_saved_rub = Column(Float, nullable=False)
    total_savings_rub = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="trips")


class CPALead(Base):
    __tablename__ = "cpa_leads"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    car_id = Column(String, nullable=False)
    partner_id = Column(String, default="autodoc_partner_01", nullable=False)
    promo_code = Column(String, nullable=False)
    discount_rub = Column(Float, default=500.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="leads")
