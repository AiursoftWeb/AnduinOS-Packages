"""Unit tests for adaptive display rotation scaling."""

import unittest
from unittest.mock import MagicMock, patch

from anduinos_appearance.rotation_scale import (
    AVAILABLE_SCALES,
    DEFAULT_LANDSCAPE_SCALE,
    DEFAULT_PORTRAIT_SCALE,
    apply_rotation_scale,
    compute_target_scale,
    is_portrait_transform,
    read_rotation_scale_config,
    write_rotation_scale_config,
)


class RotationScaleTests(unittest.TestCase):
    def test_is_portrait_transform(self):
        self.assertFalse(is_portrait_transform(0))  # normal landscape
        self.assertTrue(is_portrait_transform(1))   # 90 deg portrait
        self.assertFalse(is_portrait_transform(2))  # 180 deg landscape inverted
        self.assertTrue(is_portrait_transform(3))   # 270 deg portrait inverted

    def test_compute_target_scale(self):
        landscape_scale = 1.0
        portrait_scale = 1.5

        self.assertEqual(compute_target_scale(0, landscape_scale, portrait_scale), 1.0)
        self.assertEqual(compute_target_scale(1, landscape_scale, portrait_scale), 1.5)
        self.assertEqual(compute_target_scale(2, landscape_scale, portrait_scale), 1.0)
        self.assertEqual(compute_target_scale(3, landscape_scale, portrait_scale), 1.5)

    @patch("anduinos_appearance.rotation_scale.dconf_read")
    def test_read_config_defaults_when_empty(self, mock_read):
        mock_read.side_effect = lambda key: ""
        cfg = read_rotation_scale_config()
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["landscape"], DEFAULT_LANDSCAPE_SCALE)
        self.assertEqual(cfg["portrait"], DEFAULT_PORTRAIT_SCALE)

    @patch("anduinos_appearance.rotation_scale.dconf_read")
    def test_read_config_custom_values(self, mock_read):
        def _fake_read(key):
            if "auto-rotate-scale-enabled" in key:
                return "true"
            if "auto-rotate-scale-landscape" in key:
                return "1.25"
            if "auto-rotate-scale-portrait" in key:
                return "1.75"
            return ""

        mock_read.side_effect = _fake_read
        cfg = read_rotation_scale_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["landscape"], 1.25)
        self.assertEqual(cfg["portrait"], 1.75)

    @patch("anduinos_appearance.rotation_scale.subprocess.run")
    @patch("anduinos_appearance.rotation_scale.shutil.which", return_value="/bin/systemctl")
    def test_write_config_enables_service(self, mock_which, mock_run):
        write_rotation_scale_config(True, 1.0, 1.5)
        self.assertTrue(mock_run.called)
        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertTrue(any("enable" in str(cmd) for cmd in calls))

    @patch("anduinos_appearance.rotation_scale.subprocess.run")
    @patch("anduinos_appearance.rotation_scale.shutil.which", return_value="/bin/systemctl")
    def test_write_config_disables_service(self, mock_which, mock_run):
        write_rotation_scale_config(False, 1.0, 1.5)
        self.assertTrue(mock_run.called)
        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertTrue(any("disable" in str(cmd) for cmd in calls))

    def test_apply_rotation_scale_updates_when_different(self):
        mock_iface = MagicMock()
        # Mock logical_monitors: [(x, y, scale, transform, primary, linked, props)]
        # Current: transform 1 (portrait), current scale 1.0. Target: 1.5
        mock_logical = [(0, 0, 1.0, 1, True, [("eDP-1", "LEN", "0x123", "0x0")], {})]
        mock_iface.GetCurrentState.return_value = (42, [], mock_logical, {})

        changed = apply_rotation_scale(mock_iface, landscape_scale=1.0, portrait_scale=1.5)
        self.assertTrue(changed)
        self.assertTrue(mock_iface.ApplyMonitorsConfig.called)
        call_args = mock_iface.ApplyMonitorsConfig.call_args[0]
        self.assertEqual(call_args[0], 42)  # serial
        self.assertEqual(call_args[1], 2)   # method TEMPORARY
        new_logical = call_args[2]
        self.assertEqual(new_logical[0][2], 1.5)  # updated scale

    def test_apply_rotation_scale_noop_when_already_matching(self):
        mock_iface = MagicMock()
        # Current: transform 0 (landscape), current scale 1.0. Target: 1.0
        mock_logical = [(0, 0, 1.0, 0, True, [("eDP-1", "LEN", "0x123", "0x0")], {})]
        mock_iface.GetCurrentState.return_value = (42, [], mock_logical, {})

        changed = apply_rotation_scale(mock_iface, landscape_scale=1.0, portrait_scale=1.5)
        self.assertFalse(changed)
        self.assertFalse(mock_iface.ApplyMonitorsConfig.called)


if __name__ == "__main__":
    unittest.main()
