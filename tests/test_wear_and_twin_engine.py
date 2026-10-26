import numpy as np
from app.services.wear_engine import WearEngine
from app.services.twin_engine import TwinEngine


def test_phone_cannot_measure_oil_condition_from_traffic(default_vehicle_profile):
    engine=WearEngine(profile=default_vehicle_profile)
    for traffic in (1,10):
        result=engine.compute_oil_wear(np.zeros(1000),np.zeros(1000),traffic_score=traffic,dt=.02)
        assert result['oil_wear_percent'] is result['equivalent_hours'] is None
        assert result['status']=='unavailable'


def test_phone_acceleration_cannot_supply_missing_oil_chemistry(default_vehicle_profile):
    engine=WearEngine(profile=default_vehicle_profile)
    for acceleration in (.4,2.5):
        result=engine.compute_oil_wear(np.full(1000,10),np.full(1000,acceleration),traffic_score=3,dt=.02)
        assert result['oil_wear_percent'] is None
        assert 'oil_condition_reference' in result['missing_inputs']


def test_distance_and_user_wear_cannot_create_a_fuel_reference(default_vehicle_profile):
    result=TwinEngine(profile=default_vehicle_profile).simulate_twin_and_delta(
        speeds_mps=np.linspace(0,16.6,1000),distance_km=10,user_wear_percent=.015,dt=.02,fuel_price_rub=62)
    assert result['fuel_saved_liters'] is result['total_savings_rub'] is None
    assert result['reason']=='matched_reference_fuel_required'


def test_stationary_record_does_not_fabricate_financial_values(default_vehicle_profile):
    result=TwinEngine(profile=default_vehicle_profile).simulate_twin_and_delta(speeds_mps=np.zeros(200),dt=.02)
    assert result['total_savings_rub'] is None
    assert result['oil_saved_rub'] is None
