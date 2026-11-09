import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    # Only set default if explicitly testing; otherwise raise to avoid silent misconfiguration
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING"):
        DATABASE_URL = "postgresql+asyncpg://autoxak:ci_only@127.0.0.1:5432/autoxak_test"
    else:
        raise RuntimeError("DATABASE_URL environment variable is required but not set")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

# Псевдонимы для совместимости
async_session_maker = async_session_factory
SessionLocal = async_session_factory

Base = declarative_base()


async def get_db():
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def initialize_schema():
    """Add supported nullable columns to existing installations; never hide DDL errors."""
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateColumn

    ALLOWED_TABLES = {"vehicle_trims", "trips", "users", "recording_vehicles", "login_attempts"}
    ALLOWED_COLUMNS = {
        "base_city_fuel_rate_l100km", "fuel_rate_source",
        "board_computer_consumption_l100km", "request_hash", "calculation_result", "client_session_id",
        "car_name", "custom_profile", "login", "merged_into",
    }
    ALLOWED_INDEXES = {"ix_trips_user_client_session", "ix_users_login", "ix_trips_user_car", "ix_cpa_leads_user_partner", "ix_login_attempts_blocked_until"}

    additions = {
        "vehicle_trims": {"base_city_fuel_rate_l100km", "fuel_rate_source"},
        "trips": {"board_computer_consumption_l100km", "request_hash", "calculation_result", "client_session_id"},
        "users": {"car_name", "custom_profile", "login", "merged_into"},
    }
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            await connection.execute(text("SELECT pg_advisory_xact_lock(173512341)"))
        await connection.run_sync(Base.metadata.create_all)
        for table_name, names in additions.items():
            if table_name not in ALLOWED_TABLES:
                raise ValueError(f"Table {table_name} is not in allowed DDL whitelist")
            safe_names = names & ALLOWED_COLUMNS
            if not safe_names:
                continue
            columns = await connection.run_sync(lambda sync, name=table_name: {col["name"] for col in inspect(sync).get_columns(name)})
            for name in safe_names - columns:
                if name not in Base.metadata.tables[table_name].c:
                    continue
                column = Base.metadata.tables[table_name].c[name]
                ddl = str(CreateColumn(column).compile(dialect=connection.dialect))
                await connection.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {ddl}'))
        trips_table = Base.metadata.tables.get("trips")
        if trips_table is not None:
            index = next((index for index in trips_table.indexes if index.name == "ix_trips_user_client_session"), None)
            if index:
                await connection.run_sync(lambda sync: index.create(sync, checkfirst=True))

        users_table = Base.metadata.tables.get("users")
        if users_table is not None:
            user_index = next((index for index in users_table.indexes if index.name == "ix_users_login"), None)
            if user_index:
                await connection.run_sync(lambda sync: user_index.create(sync, checkfirst=True))
        # Preserve the three pre-existing backend rates with explicit provenance.
        # Do not infer consumption for other modifications from mass/body/engine size.
        from app.models.vehicle_profiles import VEHICLE_REGISTRY
        for old, current in {"haval_jolion_15t": "haval_jolion_15t_4wd", "skoda_octavia_14tsi": "skoda_octavia_14tsi_dsg", "geely_coolray_15t": "geely_coolray_15t_fwd"}.items():
            await connection.execute(text("UPDATE vehicle_trims SET base_city_fuel_rate_l100km=:rate, fuel_rate_source=:source WHERE id=:id AND base_city_fuel_rate_l100km IS NULL AND fuel_rate_source IS NULL"),
                                     {"rate": VEHICLE_REGISTRY[old].base_city_fuel_rate_l100km, "source": "legacy_backend_declared_not_oem_verified:" + old, "id": current})
        # Explicit product default approved for Haval Jolion. Do not replace
        # consumption explicitly supplied by a user or a verified catalog import.
        await connection.execute(text("UPDATE vehicle_trims SET base_city_fuel_rate_l100km=8.5, fuel_rate_source='product_default:twin-fuel-mvp-1' WHERE id IN ('haval_jolion_15t_4wd','haval_jolion_15t_fwd') AND (base_city_fuel_rate_l100km IS NULL OR fuel_rate_source='legacy_backend_declared_not_oem_verified:haval_jolion_15t')"))
