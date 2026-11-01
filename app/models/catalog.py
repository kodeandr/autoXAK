"""Additive, immutable taxonomy snapshots; no FK changes to existing vehicles/trips."""
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index, JSON
from app.core.database import Base


class CatalogImport(Base):
    __tablename__ = 'catalog_imports'
    id = Column(String(64), primary_key=True)
    source = Column(JSON, nullable=False)
    makes = Column(JSON, nullable=False)
    model_count = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CatalogModel(Base):
    __tablename__ = 'catalog_models'
    import_id = Column(String(64), ForeignKey('catalog_imports.id'), primary_key=True)
    id = Column(String(100), primary_key=True)
    make_id = Column(String(100), nullable=False)
    make_name = Column(String(300), nullable=False)
    name = Column(String(300), nullable=False)
    details = Column(JSON, nullable=False)
    __table_args__ = (Index('ix_catalog_models_import_make', 'import_id', 'make_id'),
                      Index('ix_catalog_models_vehicle', 'id'))


class CatalogState(Base):
    __tablename__ = 'catalog_state'
    id = Column(String(20), primary_key=True)
    active_import_id = Column(String(64), ForeignKey('catalog_imports.id'), nullable=True)
