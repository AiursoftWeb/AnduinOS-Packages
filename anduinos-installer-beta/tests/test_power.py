import tempfile
import unittest
from pathlib import Path

from installer_core.power import (
    PowerDecision,
    probe_power_supply,
    requires_low_battery_warning,
)


class PowerSupplyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def supply(self, name, **fields):
        device = self.root / name
        device.mkdir()
        for field, value in fields.items():
            (device / field).write_text(str(value), encoding="utf-8")
        return device

    def battery(self, name="BAT0", capacity=50, **fields):
        return self.supply(
            name,
            type="Battery",
            capacity=capacity,
            **fields,
        )

    def test_missing_root_and_no_battery_do_not_block(self):
        result = probe_power_supply(self.root / "missing")
        self.assertEqual(result.decision, PowerDecision.NO_VALID_BATTERY)
        self.assertFalse(result.requires_warning)

    def test_peripheral_and_zero_percent_phantom_batteries_are_ignored(self):
        self.supply(
            "hidpp_battery_0",
            type="Battery",
            scope="Device",
            capacity=10,
        )
        self.battery(capacity=0, status="Discharging")
        result = probe_power_supply(self.root)
        self.assertEqual(result.battery_count, 0)
        self.assertIsNone(result.capacity_percent)
        self.assertEqual(result.decision, PowerDecision.NO_VALID_BATTERY)

    def test_named_and_explicit_system_batteries_are_accepted(self):
        self.battery("CMB0", capacity=70)
        self.supply(
            "vendor_cell",
            type="Battery",
            scope="System",
            capacity=60,
        )
        result = probe_power_supply(self.root)
        self.assertEqual(result.battery_count, 2)
        self.assertEqual(result.capacity_percent, 60)

    def test_unplugged_boundaries_are_exact(self):
        for capacity, expected in ((1, True), (45, True), (46, False)):
            with self.subTest(capacity=capacity):
                self.assertEqual(
                    requires_low_battery_warning(capacity, False), expected
                )

    def test_plugged_in_boundaries_are_exact(self):
        for capacity, expected in ((1, True), (25, True), (26, False)):
            with self.subTest(capacity=capacity):
                self.assertEqual(
                    requires_low_battery_warning(capacity, True), expected
                )

    def test_online_external_supply_uses_plugged_in_threshold(self):
        self.battery(capacity=26, status="Discharging")
        self.supply("AC", type="Mains", online=1)
        result = probe_power_supply(self.root)
        self.assertTrue(result.external_power)
        self.assertEqual(result.decision, PowerDecision.ALLOW_INSTALLATION)

    def test_charging_status_is_external_power_fallback(self):
        self.battery(capacity=25, status="Charging")
        result = probe_power_supply(self.root)
        self.assertTrue(result.external_power)
        self.assertEqual(result.decision, PowerDecision.REQUIRE_WARNING)

    def test_malformed_missing_and_out_of_range_capacity_do_not_block(self):
        self.battery("BAT0", capacity="unknown")
        self.battery("BAT1", capacity=101)
        self.supply("BAT2", type="Battery")
        result = probe_power_supply(self.root)
        self.assertEqual(result.decision, PowerDecision.NO_VALID_BATTERY)

    def test_energy_totals_weight_multiple_batteries(self):
        self.battery(
            "BAT0", capacity=20, energy_now=10, energy_full=20
        )
        self.battery(
            "BAT1", capacity=80, energy_now=80, energy_full=80
        )
        result = probe_power_supply(self.root)
        self.assertEqual(result.capacity_percent, 90)

    def test_charge_totals_are_used_when_energy_is_unavailable(self):
        self.battery(
            "BAT0", capacity=20, charge_now=10, charge_full=20
        )
        self.battery(
            "BAT1", capacity=80, charge_now=40, charge_full=80
        )
        result = probe_power_supply(self.root)
        self.assertEqual(result.capacity_percent, 50)

    def test_mixed_or_incomplete_totals_fall_back_to_lowest_capacity(self):
        self.battery(
            "BAT0", capacity=40, energy_now=40, energy_full=100
        )
        self.battery("BAT1", capacity=30)
        result = probe_power_supply(self.root)
        self.assertEqual(result.capacity_percent, 30)
        self.assertEqual(result.decision, PowerDecision.REQUIRE_WARNING)

    def test_energy_only_system_battery_can_be_measured(self):
        self.supply(
            "BAT0",
            type="Battery",
            energy_now=26,
            energy_full=100,
        )
        result = probe_power_supply(self.root)
        self.assertEqual(result.capacity_percent, 26)
        self.assertEqual(result.decision, PowerDecision.REQUIRE_WARNING)


if __name__ == "__main__":
    unittest.main()
