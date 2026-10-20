import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "sqlite+aiosqlite:///./autoxak.db"
)

pool_options = {} if DATABASE_URL.startswith("sqlite") else {"pool_size": 10, "max_overflow": 20}
engine = create_async_engine(DATABASE_URL, echo=False, **pool_options)

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
