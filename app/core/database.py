import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://autoXAK_user@localhost:5432/autoXAK_db"
)

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
    additions = {
        "trips": {"board_computer_consumption_l100km", "request_hash", "calculation_result", "client_session_id"},
        "users": {"car_name", "custom_profile"},
    }
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            await connection.execute(text("SELECT pg_advisory_xact_lock(173512341)"))
        await connection.run_sync(Base.metadata.create_all)
        for table_name, names in additions.items():
            columns = await connection.run_sync(lambda sync, name=table_name: {col["name"] for col in inspect(sync).get_columns(name)})
            for name in names - columns:
                column = Base.metadata.tables[table_name].c[name]
                ddl = str(CreateColumn(column).compile(dialect=connection.dialect))
                await connection.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {ddl}'))
        index = next(index for index in Base.metadata.tables["trips"].indexes if index.name == "ix_trips_user_client_session")
        await connection.run_sync(lambda sync: index.create(sync, checkfirst=True))
