import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    total_savings_rub: Mapped[float] = mapped_column(Float, default=0.0)
    current_oil_wear_percent: Mapped[float] = mapped_column(Float, default=0.0)

    trips: Mapped[list["Trip"]] = relationship("Trip", back_populates="user", cascade="all, delete-orphan")


class Car(Base):
    __tablename__ = "cars"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    engine_type: Mapped[str] = mapped_column(String(32), default="TGDI")
    engine_displacement_l: Mapped[float] = mapped_column(Float, default=2.0)
    base_oil_hours: Mapped[float] = mapped_column(Float, default=250.0)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    car_id: Mapped[str] = mapped_column(String(64), index=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    duration_seconds: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float] = mapped_column(Float)
    
    equivalent_engine_hours: Mapped[float] = mapped_column(Float)
    oil_wear_percent: Mapped[float] = mapped_column(Float)
    idle_ratio: Mapped[float] = mapped_column(Float)
    
    fuel_saved_rub: Mapped[float] = mapped_column(Float)
    oil_saved_rub: Mapped[float] = mapped_column(Float)
    total_savings_rub: Mapped[float] = mapped_column(Float)

    user: Mapped["User"] = relationship("User", back_populates="trips")

    __table_args__ = (
        Index("idx_trip_user_created", "user_id", "created_at"),
    )
