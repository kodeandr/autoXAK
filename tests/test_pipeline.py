import pytest
import numpy as np
from app.core.dsp.filters import SignalFilter
from app.core.dsp.simplifier import PathSimplifier
from app.services.wear_engine import WearEngine
from app.services.twin_engine import AggressiveTwinEngine
from app.services.gis_service import GISService
from app.models.vehicle_profiles import VehiclePhysicalProfile, OilTribologyProfile

@pytest.fixture
def default_profile():
    return VehiclePhysicalProfile(
        car_id='test_car',
        brand='TestBrand',
        model='TestModel',
        curb_weight_kg=1500.0,
        drag_coefficient_area=0.75,
        rolling_resistance_coeff=0.0135,
        drivetrain_efficiency=0.90,
        engine_displacement_l=1.5,
        rated_power_kw=110.0,
        idle_rpm=800.0,
        oil_capacity_l=4.0,
        idle_fuel_rate_lph=0.85,
        base_city_fuel_rate_l100km=9.0,
        oil_profile=OilTribologyProfile(
            oil_grade='5W-30',
            base_activation_energy_jmol=106170.0,
            aged_activation_energy_jmol=91900.0,
            zddp_activation_energy_jmol=53000.0,
            zddp_activation_volume_m3=1.8e-28,
            nominal_service_hours=250.0
        )
    )

def test_dsp_gravity_removal():
    filter_mod = SignalFilter(sample_rate_hz=50.0, cutoff_hz=2.5)
    n = 100
    ax = [0.0] * n
    ay = [0.0] * n
    az = [9.81] * n
    _, _, filt_z = filter_mod.isolate_linear_acceleration(ax, ay, az)
    assert abs(np.mean(filt_z)) < 0.1

def test_wear_engine_traffic_stress(default_profile):
    engine = WearEngine(profile=default_profile)
    n = 1000
    dt = 0.02
    zero_speeds = np.zeros(n)
    zero_acc = np.zeros(n)
    res_idle = engine.evaluate_trip_wear(dt=dt, speed_mps=zero_speeds, ax_mps2=zero_acc)

    stress_speeds = np.full(n, 15.0)
    stress_acc = np.full(n, 2.5)
    res_stress = engine.evaluate_trip_wear(dt=dt, speed_mps=stress_speeds, ax_mps2=stress_acc)

    assert res_stress['equivalent_hours'] > res_idle['equivalent_hours']
    assert res_idle['oil_wear_percent'] > 0.0

def test_twin_engine_savings_positive(default_profile):
    twin = AggressiveTwinEngine(profile=default_profile)
    n = 500
    dt = 0.02
    user_speeds = np.linspace(0.0, 15.0, n)
    twin_speed, twin_ax = twin.simulate_aggressive_twin_kinematics(user_speeds, dt=dt)
    user_ax = np.gradient(user_speeds, dt)
    res = twin.evaluate_financial_delta(
        distance_km=2.5,
        user_equiv_hours=0.01,
        user_ax=user_ax,
        twin_equiv_hours=0.03
    )
    assert res['fuel_savings_rub'] >= 0.0
    assert res['oil_savings_rub'] >= 0.0
    assert res['total_savings_rub'] > 0.0

@pytest.mark.anyio
async def test_gis_route_context():
    gis = GISService()
    coords = [{'lat': 55.751244, 'lon': 37.618423}, {'lat': 55.755814, 'lon': 37.617635}]
    ctx = await gis.get_route_context(coords)
    assert ctx.traffic_score >= 1.0

def test_rdp_path_simplification():
    simplifier = PathSimplifier()
    # Прямой отрезок из 100 точек
    points = [{'lat': 55.75 + (i * 0.0001), 'lon': 37.61 + (i * 0.0001)} for i in range(100)]
    # Добавляем точку изгиба/поворота (маневра)
    points[50]['lat'] += 0.005

    simplified = simplifier.downsample_gps_stream(points, max_points_for_api=10)
    assert len(simplified) <= 10
    assert len(simplified) >= 3
