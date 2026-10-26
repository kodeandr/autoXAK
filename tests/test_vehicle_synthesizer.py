from app.services.profile_synthesizer import ProfileSynthesizer
from app.schemas.vehicle import CustomVehicleInput


def test_synthesizer_compact_sedan():
    synthesizer = ProfileSynthesizer()
    input_data = CustomVehicleInput(
        brand="Geely",
        model="Emgrand",
        body_type="sedan",
        engine_displacement_l=1.498,
        engine_power_hp=122,
        drive_type="fwd",
        transmission="MT",
        curb_weight_kg=1280
    )

    profile = synthesizer.synthesize_profile(input_data)
    assert profile.curb_weight_kg == 1280
    assert profile.drag_coefficient_area is None
    assert profile.drivetrain_efficiency is None
    assert profile.parameter_status=='user_declared_incomplete_not_calibrated'


def test_synthesizer_heavy_suv():
    synthesizer = ProfileSynthesizer()
    input_data = CustomVehicleInput(
        brand="Haval",
        model="H9",
        body_type="suv",
        engine_displacement_l=2.0,
        engine_power_hp=218,
        drive_type="4wd",
        transmission="AT",
        curb_weight_kg=2250
    )

    profile = synthesizer.synthesize_profile(input_data)
    assert profile.curb_weight_kg == 2250
    assert profile.drag_coefficient_area is None
    assert profile.oil_capacity_l is None
