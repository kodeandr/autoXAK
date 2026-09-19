-- scripts/schema_timescale.sql

-- 1. Таблица нормативно-технических профилей ТС
CREATE TABLE IF NOT EXISTS vehicle_profiles (
    car_id VARCHAR(64) PRIMARY KEY,
    brand VARCHAR(32) NOT NULL,
    model VARCHAR(32) NOT NULL,
    curb_weight_kg FLOAT NOT NULL,
    drag_coefficient_area FLOAT NOT NULL,
    rolling_resistance_coeff FLOAT NOT NULL DEFAULT 0.0135,
    drivetrain_efficiency FLOAT NOT NULL DEFAULT 0.90,
    engine_displacement_l FLOAT NOT NULL,
    rated_power_kw FLOAT NOT NULL,
    idle_rpm FLOAT NOT NULL DEFAULT 800.0,
    oil_capacity_l FLOAT NOT NULL,
    oil_grade VARCHAR(16) NOT NULL DEFAULT '5W-30',
    base_activation_energy_jmol FLOAT NOT NULL DEFAULT 106170.0,
    nominal_service_hours FLOAT NOT NULL DEFAULT 250.0
);

-- 2. Справочник калиброванных эталонов (заполнение базы)
INSERT INTO vehicle_profiles (
    car_id, brand, model, curb_weight_kg, drag_coefficient_area, 
    engine_displacement_l, rated_power_kw, oil_capacity_l
) VALUES 
('haval_jolion_15t', 'Haval', 'Jolion 1.5T 4WD', 1505.0, 0.7616, 1.5, 110.0, 3.8),
('chery_tiggo7_15t', 'Chery', 'Tiggo 7 Pro 1.5T', 1540.0, 0.7986, 1.5, 108.0, 4.1),
('test_car_vag_2.0tsi', 'Volkswagen', 'Tiguan 2.0 TSI', 1650.0, 0.7800, 2.0, 132.0, 5.7)
ON CONFLICT (car_id) DO NOTHING;

-- 3. Таблица для хранения сырых точек телеметрии 50 Гц
CREATE TABLE IF NOT EXISTS telemetry_points (
    id BIGSERIAL,
    time TIMESTAMPTZ NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    ax_mps2 REAL NOT NULL,
    ay_mps2 REAL NOT NULL,
    az_mps2 REAL NOT NULL,
    speed_mps REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telemetry_session_time ON telemetry_points (session_id, time DESC);

-- 4. Таблица протоколов метрологической валидации (BenchmarkEngine)
CREATE TABLE IF NOT EXISTS validation_runs (
    run_id UUID PRIMARY KEY,
    cohort_name VARCHAR(64) NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sample_size INT NOT NULL,
    mape_percent FLOAT NOT NULL,
    rmse_hours FLOAT NOT NULL,
    pearson_r FLOAT NOT NULL,
    p_value FLOAT NOT NULL,
    hypothesis_status VARCHAR(32) NOT NULL
);