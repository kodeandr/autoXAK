import asyncio
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.core.database import engine, Base
from app.models.db_models import FuelRegionalPrice

SessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    autoflush=False
)

FUEL_DATA = [
    # МОСКВА (77)
    {
        "region_code": "77", "region_name": "Москва", "brand": "rosneft",
        "brand_display_name": "Роснефть",
        "price_ai92": 65.20, "price_ai95": 72.40, "price_ai100": 101.50, "price_dt": 80.60,
        "availability_status": "NORMAL"
    },
    {
        "region_code": "77", "region_name": "Москва", "brand": "lukoil",
        "brand_display_name": "Лукойл",
        "price_ai92": 66.10, "price_ai95": 74.20, "price_ai100": 104.90, "price_dt": 82.10,
        "availability_status": "NORMAL"
    },
    {
        "region_code": "77", "region_name": "Москва", "brand": "gpn",
        "brand_display_name": "Газпромнефть",
        "price_ai92": 65.40, "price_ai95": 72.90, "price_ai100": 103.20, "price_dt": 81.30,
        "availability_status": "NORMAL"
    },
    {
        "region_code": "77", "region_name": "Москва", "brand": "independent",
        "brand_display_name": "Частные АЗС",
        "price_ai92": 69.50, "price_ai95": 78.50, "price_ai100": 108.00, "price_dt": 85.00,
        "availability_status": "SHORTAGE"
    },

    # САНКТ-ПЕТЕРБУРГ (78)
    {
        "region_code": "78", "region_name": "Санкт-Петербург", "brand": "rosneft",
        "brand_display_name": "Роснефть",
        "price_ai92": 65.10, "price_ai95": 72.10, "price_ai100": 100.80, "price_dt": 80.20,
        "availability_status": "NORMAL"
    },
    {
        "region_code": "78", "region_name": "Санкт-Петербург", "brand": "lukoil",
        "brand_display_name": "Лукойл",
        "price_ai92": 66.30, "price_ai95": 73.90, "price_ai100": 104.10, "price_dt": 81.90,
        "availability_status": "NORMAL"
    },
    {
        "region_code": "78", "region_name": "Санкт-Петербург", "brand": "gpn",
        "brand_display_name": "Газпромнефть",
        "price_ai92": 65.50, "price_ai95": 72.80, "price_ai100": 102.70, "price_dt": 81.10,
        "availability_status": "NORMAL"
    },

    # КРАСНОДАРСКИЙ КРАЙ (23) — логистическая наценка и дефицит
    {
        "region_code": "23", "region_name": "Краснодарский край", "brand": "rosneft",
        "brand_display_name": "Роснефть",
        "price_ai92": 67.90, "price_ai95": 75.80, "price_ai100": 106.50, "price_dt": 83.40,
        "availability_status": "SHORTAGE"
    },
    {
        "region_code": "23", "region_name": "Краснодарский край", "brand": "lukoil",
        "brand_display_name": "Лукойл",
        "price_ai92": 68.80, "price_ai95": 77.40, "price_ai100": 108.90, "price_dt": 84.80,
        "availability_status": "SHORTAGE"
    },
    {
        "region_code": "23", "region_name": "Краснодарский край", "brand": "independent",
        "brand_display_name": "Частные АЗС",
        "price_ai92": 74.50, "price_ai95": 84.00, "price_ai100": 114.00, "price_dt": 89.50,
        "availability_status": "CRITICAL"
    },

    # СВЕРДЛОВСКАЯ ОБЛАСТЬ (66)
    {
        "region_code": "66", "region_name": "Свердловская область", "brand": "gpn",
        "brand_display_name": "Газпромнефть",
        "price_ai92": 63.80, "price_ai95": 70.90, "price_ai100": 98.50, "price_dt": 79.20,
        "availability_status": "NORMAL"
    },
    {
        "region_code": "66", "region_name": "Свердловская область", "brand": "lukoil",
        "brand_display_name": "Лукойл",
        "price_ai92": 64.90, "price_ai95": 72.10, "price_ai100": 101.40, "price_dt": 80.60,
        "availability_status": "NORMAL"
    }
]


async def seed():
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS fuel_regional_prices CASCADE"))
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        for f in FUEL_DATA:
            entry = FuelRegionalPrice(**f)
            session.add(entry)

        await session.commit()
        print("Таблица региональных цен на топливо успешно создана и наполнена!")

if __name__ == "__main__":
    asyncio.run(seed())
