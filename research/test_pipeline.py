import unittest
import numpy as np
import pandas as pd
from fuel_model import FEATURES, AFR, DENSITY_G_L, fuel_rate, interval_integral, motion_windows
from prepare_dataset import static_table


class PipelineTests(unittest.TestCase):
    def test_integrate_asynchronous_rate_and_window_boundaries(self):
        # 1 L/h for 1800 s, then 3 L/h for 1800 s: exactly 2 litres.
        t = [0, 0.2, 1200.1, 1800, 2700.8, 3600]
        y = np.array([1, 1, 1, 3, 3, 999]) / 3600
        np.testing.assert_allclose(interval_integral(t, y, [0, 900, 1800, 3600]), [.25, .25, 1.5])

    def test_maf_units_missing_trim_and_oem_precedence(self):
        columns = {f"{term} Term Fuel Trim Bank {bank}[%]": [np.nan]*3
                   for term in ("Short", "Long") for bank in (1, 2)}
        columns.update({"MAF[g/sec]": [AFR*DENSITY_G_L/3600]*3,
                        "Fuel Rate[L/hr]": [np.nan, 2., np.nan]})
        frame = pd.DataFrame(columns)
        frame.loc[2, "Short Term Fuel Trim Bank 1[%]"] = 10
        frame.loc[2, "Long Term Fuel Trim Bank 1[%]"] = 5
        rate, source = fuel_rate(frame)
        np.testing.assert_allclose(rate, [1, 2, 1.15])
        np.testing.assert_array_equal(source, [4, 1, 2])

    def test_motion_distance_gap_and_engine_on_stop(self):
        profile = dict(mass_kg=1500, displacement_l=2., turbo_tag=0)
        rows, edges, good = motion_windows([0, 10, 20, 30], [10]*4, profile)
        self.assertEqual(rows[0]["distance_m"], 300)
        self.assertEqual(rows[0]["duration_s"], 30)
        self.assertTrue(good[0])
        _, _, good = motion_windows([0, 11, 20, 30], [10]*4, profile)
        self.assertFalse(good[0])
        rows, _, good = motion_windows(np.arange(31), np.zeros(31), profile)
        self.assertEqual(rows[0]["stopped_fraction"], 1)
        self.assertEqual(rows[0]["tractive_energy_mj"], 0)
        self.assertEqual(rows[0]["duration_s"], 30)

    def test_no_extrapolation_short_tail_and_nonmonotonic_rejected(self):
        profile = dict(mass_kg=1500, displacement_l=2., turbo_tag=0)
        rows, edges, _ = motion_windows(np.arange(34), np.ones(34), profile)
        self.assertEqual(len(rows), 1)
        self.assertEqual(edges[-1], 30)
        with self.assertRaises(ValueError):
            motion_windows([0, 2, 1], [0, 1, 2], profile)

    def test_split_vehicle_integrity_and_label_feature_boundary(self):
        table = static_table()
        sets = [set(table.loc[table.split == split, "VehId"]) for split in ("train", "validation", "test")]
        self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
        self.assertTrue((table.loc[table.diesel_tag, "split"] == "excluded").all())
        self.assertNotIn("vehicle_id", FEATURES)
        self.assertFalse(any(any(x in name.lower() for x in ("maf", "fuel", "rpm", "trim", "source", "trip"))
                             for name in FEATURES))


if __name__ == "__main__":
    unittest.main()
