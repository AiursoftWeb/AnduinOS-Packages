import unittest
from unittest.mock import Mock, patch

from pages import Adw, _confirm_storage_capacity, storage_capacity_warning


class StorageCapacityTests(unittest.TestCase):
    def test_sufficient_capacity_proceeds_without_a_dialog(self):
        confirmed = Mock()
        with patch("pages.Adw.MessageDialog") as dialog:
            _confirm_storage_capacity(Mock(), Mock(), "en_US", 50 * 1024**3, confirmed)
        dialog.assert_not_called()
        confirmed.assert_called_once_with()

    def test_warning_requires_confirmation_and_defaults_to_cancel(self):
        for size, severity in ((23, "error"), (25, "warning"), (49, "warning")):
            with self.subTest(size=size), patch("pages.Adw.MessageDialog") as factory, \
                    patch("pages.Gtk.Image.new_from_icon_name") as image:
                page = Mock()
                page.get_mapped.return_value = True
                confirmed = Mock()
                _confirm_storage_capacity(page, Mock(), "en_US", size * 1024**3, confirmed)
                dialog = factory.return_value
                image.return_value.add_css_class.assert_called_once_with(severity)
                dialog.set_default_response.assert_called_once_with("cancel")
                dialog.set_close_response.assert_called_once_with("cancel")
                if severity == "error":
                    dialog.set_response_appearance.assert_called_once_with(
                        "continue", Adw.ResponseAppearance.DESTRUCTIVE)
                else:
                    dialog.set_response_appearance.assert_not_called()
                confirmed.assert_not_called()
                response = dialog.connect.call_args.args[1]
                response(dialog, "cancel")
                confirmed.assert_not_called()
                page.get_mapped.return_value = False
                response(dialog, "continue")
                confirmed.assert_not_called()
                page.get_mapped.return_value = True
                response(dialog, "continue")
                confirmed.assert_called_once_with()

    def test_exact_gib_boundaries(self):
        gib = 1024**3
        for size, expected in (
            (1, "error"), (23 * gib, "error"), (25 * gib - 1, "error"),
            (25 * gib, "warning"), (50 * gib - 1, "warning"),
            (50 * gib, None), (100 * gib, None),
        ):
            with self.subTest(size=size):
                self.assertEqual(storage_capacity_warning(size), expected)
