import unittest

from app.core import measurement_error, sanitize_context, trapezoid_kwh


class MeasurementQualityTests(unittest.TestCase):
    def test_partial_nulls_are_not_rejected(self) -> None:
        row = {"read_ts": 1_750_000_000, "inverter": 1, "pv1power": None, "outw_a": 120, "battsoc": 80}
        self.assertIsNone(measurement_error(row))

    def test_snapshot_without_any_key_channel_is_rejected(self) -> None:
        row = {"read_ts": 1_750_000_000, "inverter": 2, "pv1power": None, "outw_a": None}
        self.assertEqual(measurement_error(row), "empty_snapshot")

    def test_impossible_soc_is_rejected(self) -> None:
        row = {"read_ts": 1_750_000_000, "inverter": 2, "battsoc": 101}
        self.assertEqual(measurement_error(row), "invalid_battery_soc")

    def test_negative_power_is_rejected_but_missing_is_not(self) -> None:
        row = {"read_ts": 1_750_000_000, "inverter": 1, "pv1power": -1}
        self.assertEqual(measurement_error(row), "negative_pv1power")

    def test_zero_battery_voltage_is_rejected(self) -> None:
        row = {"read_ts": 1_750_000_000, "inverter": 2, "outw_a": 50, "battvolt": 0}
        self.assertEqual(measurement_error(row), "zero_battery_voltage")

    def test_unknown_inverter_and_bad_timestamp(self) -> None:
        self.assertEqual(measurement_error({"read_ts": 1_750_000_000, "inverter": 3}), "unsupported_inverter")
        self.assertEqual(measurement_error({"read_ts": 10, "inverter": 1}), "invalid_timestamp")


class EnergyAndPrivacyTests(unittest.TestCase):
    def test_trapezoidal_energy_and_gap_limit(self) -> None:
        self.assertAlmostEqual(trapezoid_kwh(1000, 2000, 3600, 3600) or 0, 1.5)
        self.assertIsNone(trapezoid_kwh(1000, 2000, 3601, 3600))

    def test_context_retains_only_allowlisted_metadata(self) -> None:
        clean = sanitize_context(
            '{"source":"manual","inverter":1,"device":"workstation",'
            '"context_type":"gpu_cnn_training_workload","textual_description":"private free text",'
            '"command":"b2 --j 3 test","directory":"/home/user/project"}'
        )
        self.assertEqual(
            clean,
            {"inverter": 1, "source": "manual", "device": "workstation",
             "context_type": "gpu_cnn_training_workload"},
        )


if __name__ == "__main__":
    unittest.main()
