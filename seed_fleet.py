import asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.core.database import engine, Base
from app.models.db_models import VehicleMake, VehicleModel, VehicleTrim

# Автономная фабрика сессий, независимая от имен в database.py
SessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    autoflush=False
)

TOP_FLEET_DATA = [
    # 1. HAVAL
    {
        "make": {"id": "haval", "name": "Haval", "country": "China"},
        "models": [
            {
                "id": "haval_jolion", "name": "Jolion", "body_type": "crossover",
                "trims": [
                    {
                        "id": "haval_jolion_15t_fwd",
                        "badge_name": "1.5T 2WD 7DCT (143 л.с.)",
                        "curb_weight_kg": 1420.0,
                        "drag_coefficient_area": 0.81,
                        "rolling_resistance_coeff": 0.0125,
                        "drivetrain_efficiency": 0.91,
                        "engine_displacement_l": 1.5,
                        "rated_power_kw": 105.0,
                        "drivetrain_type": "FWD",
                        "transmission_type": "DCT",
                        "oil_capacity_l": 3.8,
                        "oil_grade": "0W-20",
                        "oil_spec": "ACEA C5",
                        "recommended_oil_brand": "TotalEnergies Quartz 9000",
                        "nominal_service_hours": 250.0
                    },
                    {
                        "id": "haval_jolion_15t_4wd",
                        "badge_name": "1.5T 4WD 7DCT (150 л.с.)",
                        "curb_weight_kg": 1505.0,
                        "drag_coefficient_area": 0.86,
                        "rolling_resistance_coeff": 0.0135,
                        "drivetrain_efficiency": 0.87,
                        "engine_displacement_l": 1.5,
                        "rated_power_kw": 110.0,
                        "drivetrain_type": "AWD",
                        "transmission_type": "DCT",
                        "oil_capacity_l": 3.8,
                        "oil_grade": "0W-20",
                        "oil_spec": "ACEA C5",
                        "recommended_oil_brand": "TotalEnergies Quartz 9000",
                        "nominal_service_hours": 250.0
                    }
                ]
            }
        ]
    },
    # 2. SKODA
    {
        "make": {"id": "skoda", "name": "Skoda", "country": "Czech Republic"},
        "models": [
            {
                "id": "skoda_octavia", "name": "Octavia", "body_type": "liftback",
                "trims": [
                    {
                        "id": "skoda_octavia_16mpi_mt",
                        "badge_name": "1.6 MPI 5-МКПП (110 л.с.)",
                        "curb_weight_kg": 1235.0,
                        "drag_coefficient_area": 0.62,
                        "rolling_resistance_coeff": 0.011,
                        "drivetrain_efficiency": 0.95,
                        "engine_displacement_l": 1.6,
                        "rated_power_kw": 81.0,
                        "drivetrain_type": "FWD",
                        "transmission_type": "MT",
                        "oil_capacity_l": 4.0,
                        "oil_grade": "5W-40",
                        "oil_spec": "VW 502.00",
                        "recommended_oil_brand": "Sintec Platinum 7000",
                        "nominal_service_hours": 250.0
                    },
                    {
                        "id": "skoda_octavia_14tsi_dsg",
                        "badge_name": "1.4 TSI 7-DSG (150 л.с.)",
                        "curb_weight_kg": 1285.0,
                        "drag_coefficient_area": 0.61,
                        "rolling_resistance_coeff": 0.0105,
                        "drivetrain_efficiency": 0.93,
                        "engine_displacement_l": 1.4,
                        "rated_power_kw": 110.0,
                        "drivetrain_type": "FWD",
                        "transmission_type": "DCT",
                        "oil_capacity_l": 4.0,
                        "oil_grade": "0W-30",
                        "oil_spec": "VW 504.00",
                        "recommended_oil_brand": "VAG LongLife III FE",
                        "nominal_service_hours": 250.0
                    },
                    {
                        "id": "skoda_octavia_20tsi_dsg_4wd",
                        "badge_name": "2.0 TSI DSG 4x4 (190 л.с.)",
                        "curb_weight_kg": 1455.0,
                        "drag_coefficient_area": 0.64,
                        "rolling_resistance_coeff": 0.0115,
                        "drivetrain_efficiency": 0.88,
                        "engine_displacement_l": 2.0,
                        "rated_power_kw": 140.0,
                        "drivetrain_type": "AWD",
                        "transmission_type": "DCT",
                        "oil_capacity_l": 5.7,
                        "oil_grade": "0W-20",
                        "oil_spec": "VW 508.00",
                        "recommended_oil_brand": "Mobil 1 ESP x2",
                        "nominal_service_hours": 250.0
                    }
                ]
            }
        ]
    },
    # 3. GEELY
    {
        "make": {"id": "geely", "name": "Geely", "country": "China"},
        "models": [
            {
                "id": "geely_coolray", "name": "Coolray", "body_type": "crossover",
                "trims": [
                    {
                        "id": "geely_coolray_15t_fwd",
                        "badge_name": "1.5T 7DCT (150 л.с.)",
                        "curb_weight_kg": 1340.0,
                        "drag_coefficient_area": 0.72,
                        "rolling_resistance_coeff": 0.012,
                        "drivetrain_efficiency": 0.91,
                        "engine_displacement_l": 1.5,
                        "rated_power_kw": 110.0,
                        "drivetrain_type": "FWD",
                        "transmission_type": "DCT",
                        "oil_capacity_l": 4.0,
                        "oil_grade": "0W-20",
                        "oil_spec": "VCC RBS0-2AE",
                        "recommended_oil_brand": "LUKOIL GENESIS ARMORTECH",
                        "nominal_service_hours": 250.0
                    }
                ]
            }
        ]
    },
    # 4. LADA
    {
        "make": {"id": "lada", "name": "LADA", "country": "Russia"},
        "models": [
            {
                "id": "lada_vesta", "name": "Vesta NG", "body_type": "sedan",
                "trims": [
                    {
                        "id": "lada_vesta_16_mt",
                        "badge_name": "1.6 16V 5-МКПП (106 л.с.)",
                        "curb_weight_kg": 1220.0,
                        "drag_coefficient_area": 0.68,
                        "rolling_resistance_coeff": 0.012,
                        "drivetrain_efficiency": 0.94,
                        "engine_displacement_l": 1.6,
                        "rated_power_kw": 78.0,
                        "drivetrain_type": "FWD",
                        "transmission_type": "MT",
                        "oil_capacity_l": 4.1,
                        "oil_grade": "5W-40",
                        "oil_spec": "API SN / АвтоВАЗ",
                        "recommended_oil_brand": "Rosneft Magnum Ultratec",
                        "nominal_service_hours": 250.0
                    },
                    {
                        "id": "lada_vesta_18_cvt",
                        "badge_name": "1.8 Evo CVT (122 л.с.)",
                        "curb_weight_kg": 1270.0,
                        "drag_coefficient_area": 0.69,
                        "rolling_resistance_coeff": 0.0125,
                        "drivetrain_efficiency": 0.89,
                        "engine_displacement_l": 1.8,
                        "rated_power_kw": 90.0,
                        "drivetrain_type": "FWD",
                        "transmission_type": "CVT",
                        "oil_capacity_l": 4.4,
                        "oil_grade": "5W-30",
                        "oil_spec": "API SP",
                        "recommended_oil_brand": "LUKOIL GENESIS ARMORTECH",
                        "nominal_service_hours": 250.0
                    }
                ]
            }
        ]
    },
    # 5. CHERY
    {
        "make": {"id": "chery", "name": "Chery", "country": "China"},
        "models": [
            {
                "id": "chery_tiggo_7pro", "name": "Tiggo 7 Pro Max", "body_type": "crossover",
                "trims": [
                    {
                        "id": "chery_tiggo_7pro_16t_awd",
                        "badge_name": "1.6T AWD 7DCT (150 л.с.)",
                        "curb_weight_kg": 1540.0,
                        "drag_coefficient_area": 0.84,
                        "rolling_resistance_coeff": 0.0135,
                        "drivetrain_efficiency": 0.88,
                        "engine_displacement_l": 1.6,
                        "rated_power_kw": 110.0,
                        "drivetrain_type": "AWD",
                        "transmission_type": "DCT",
                        "oil_capacity_l": 4.3,
                        "oil_grade": "5W-30",
                        "oil_spec": "API SP / ACEA C2",
                        "recommended_oil_brand": "Chery Motor Oil Synthetic",
                        "nominal_service_hours": 250.0
                    }
                ]
            }
        ]
    }
]


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        for m_data in TOP_FLEET_DATA:
            make_dict = m_data["make"]
            make = await session.get(VehicleMake, make_dict["id"])
            if not make:
                make = VehicleMake(**make_dict)
                session.add(make)
                await session.flush()

            for model_dict in m_data["models"]:
                m_id = model_dict["id"]
                model = await session.get(VehicleModel, m_id)
                if not model:
                    model = VehicleModel(
                        id=m_id,
                        make_id=make.id,
                        name=model_dict["name"],
                        body_type=model_dict["body_type"]
                    )
                    session.add(model)
                    await session.flush()

                for trim_dict in model_dict["trims"]:
                    t_id = trim_dict["id"]
                    trim = await session.get(VehicleTrim, t_id)
                    if not trim:
                        trim = VehicleTrim(model_id=model.id, **trim_dict)
                        session.add(trim)
                    else:
                        for k, v in trim_dict.items():
                            setattr(trim, k, v)

        await session.commit()
        print("База данных Top-Fleet успешно наполнена модификациями.")

if __name__ == "__main__":
    asyncio.run(seed())
