"""Additive recording tables; legacy trip/vehicle rows are never rewritten."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, JSON, String

from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class RecordingVehicle(Base):
    __tablename__ = "recording_vehicles"

    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    profile = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class TripRecording(Base):
    __tablename__ = "trip_recordings"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    started_at_ms = Column(BigInteger, nullable=False)
    ended_at_ms = Column(BigInteger, nullable=True)
    status = Column(String(20), default="recording", nullable=False)
    end_reason = Column(String(20), nullable=True)
    vehicle = Column(JSON, nullable=False)
    device = Column(JSON, nullable=False)
    control = Column(JSON, nullable=True)
    fuel_estimate = Column(JSON, nullable=True)
    create_hash = Column(String(64), nullable=False)
    complete_hash = Column(String(64), nullable=True)
    chunk_count = Column(Integer, default=0, nullable=False)
    revision = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("ix_trip_recordings_owner_started", "user_id", "started_at_ms"),)


class RecordingChunk(Base):
    __tablename__ = "recording_chunks"

    trip_id = Column(String(64), ForeignKey("trip_recordings.id", ondelete="CASCADE"), primary_key=True)
    sequence = Column(Integer, primary_key=True)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    min_timestamp_ms = Column(BigInteger, nullable=False)
    max_timestamp_ms = Column(BigInteger, nullable=False)
    gps_count = Column(Integer, nullable=False)
    motion_count = Column(Integer, nullable=False)


class TripMathEvidenceRecord(Base):
    """Immutable per-request inputs; never load large maps with history summaries."""
    __tablename__ = 'trip_math_evidence'
    trip_id = Column(String(64),ForeignKey('trips.id',ondelete='CASCADE'),primary_key=True)
    math_version = Column(String(100),nullable=False)
    inputs_sha256 = Column(String(64),nullable=False)
    inputs = Column(JSON,nullable=False)
    result = Column(JSON,nullable=False)
    created_at = Column(DateTime(timezone=True),default=utcnow,nullable=False)
