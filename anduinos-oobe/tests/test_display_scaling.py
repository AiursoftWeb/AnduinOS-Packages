import importlib.util
import importlib.machinery
import pathlib
import time
import unittest
from unittest import mock


MODULE = pathlib.Path(__file__).parents[1] / 'assets' / 'display_scaling.py'
spec = importlib.util.spec_from_file_location('oobe_display_scaling', MODULE)
scaling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scaling)


def monitor(connector='eDP-1', width=2880, height=1800, builtin=True,
            supported=(1, 1.5, 5 / 3, 2, 2.5, 3), properties=None):
    return ((connector, 'vendor', 'model', 'serial'),
            [('mode', width, height, 120.0, 2.0, supported, {'is-current': True})],
            {'is-builtin': builtin, **(properties or {})})


def state(monitors=None, logical=None, properties=None):
    monitors = monitors or [monitor()]
    logical = logical or [(0, 0, 5 / 3, 0, True, [monitors[0][0]], {})]
    return (7, monitors, logical, properties or {})


def info(value):
    return scaling.primary_info(value, size_reader=lambda *_: (302, 189),
                                evidence=('10', True))


class DisplayScalingTests(unittest.TestCase):
    def test_laptop_recommends_166_and_reports_effective_ppi(self):
        result = info(state())
        self.assertEqual(result['target_ppi'], 146)
        self.assertAlmostEqual(result['recommended'], 5 / 3)
        self.assertAlmostEqual(result['effective_ppi'], 145.281, places=3)
        self.assertEqual(scaling.format_scale(result['recommended']), '166.67%')

    def test_external_primary_does_not_inherit_laptop_identity(self):
        panel = monitor()
        external = monitor('DP-1', 3840, 2160, False)
        value = state([panel, external], [(0, 0, 1, 0, False, [panel[0]], {}),
                                         (2880, 0, 2, 0, True, [external[0]], {})])
        result = info(value)
        self.assertEqual(result['key'], (external[0],))
        self.assertEqual(result['target_ppi'], 96)
        self.assertEqual(result['laptop_score'], 0)

    def test_unknown_dimensions_keep_choices_without_recommendation(self):
        result = scaling.primary_info(state(), size_reader=lambda *_: None, evidence=('', False))
        self.assertIsNone(result['recommended'])
        self.assertIsNone(result['effective_ppi'])
        self.assertGreater(len(result['supported']), 1)

    def test_score_needs_more_than_small_builtin_screen(self):
        self.assertLess(scaling.laptop_score(True, (302, 189), '', False), 6)
        self.assertGreaterEqual(scaling.laptop_score(True, (302, 189), '', True), 6)

    def test_edid_detailed_size_and_corrupt_or_missing_size(self):
        data = bytearray(128)
        data[:8] = b'\0\xff\xff\xff\xff\xff\xff\0'
        data[21:23] = bytes((30, 19))
        data[54] = 1
        data[66:69] = bytes((46, 189, 16))
        data[127] = -sum(data) % 256
        self.assertEqual(scaling.edid_size(data), (302, 189))
        data[21] = 31
        self.assertIsNone(scaling.edid_size(data))
        self.assertIsNone(scaling.edid_size(b''))
        self.assertFalse(scaling.valid_size((0, 189)))

    def test_mirror_intersects_choices_and_omits_ambiguous_recommendation(self):
        a, b = monitor(), monitor('DP-1', supported=(1, 2))
        value = state([a, b], [(0, 0, 2, 0, True, [a[0], b[0]], {})])
        result = info(value)
        self.assertEqual(result['supported'], [1, 2])
        self.assertIsNone(result['recommended'])
        with self.assertRaises(ValueError):
            scaling.build_config(value, 5 / 3, result['key'])

    def test_preserves_modes_rotation_colour_and_secondary_scale(self):
        a = monitor(properties={'color-mode': 2, 'rgb-range': 3, 'is-underscanning': True})
        b = monitor('DP-1', 1920, 1080, False)
        value = state([a, b], [(0, 0, 5 / 3, 0, True, [a[0]], {}),
                              (1728, 0, 1, 1, False, [b[0]], {})])
        _, configs, _ = scaling.build_config(value, 2, info(value)['key'])
        self.assertEqual(configs[1][:5], [1440, 0, 1, 1, False])
        self.assertEqual(configs[0][5][0][:2], ('eDP-1', 'mode'))
        self.assertEqual(configs[0][5][0][2]['color-mode'].unpack(), 2)
        self.assertEqual(configs[0][5][0][2]['rgb-range'].unpack(), 3)
        self.assertTrue(configs[0][5][0][2]['underscanning'].unpack())

    def test_vertical_layout_and_primary_on_right(self):
        a, b = monitor(), monitor('DP-1', 1920, 1080, False)
        for location, expected in (((0, 1080), (0, 900)), ((-1920, 0), (0, 0))):
            with self.subTest(location=location):
                value = state([a, b], [(0, 0, 5 / 3, 0, True, [a[0]], {}),
                                      (*location, 1, 0, False, [b[0]], {})])
                _, configs, _ = scaling.build_config(value, 2, info(value)['key'])
                self.assertEqual(tuple(configs[1][:2]), expected)

    def test_physical_layout_does_not_move_neighbours(self):
        a, b = monitor(), monitor('DP-1')
        value = state([a, b], [(0, 0, 2, 0, True, [a[0]], {}),
                              (2880, 0, 1, 0, False, [b[0]], {})],
                      {'layout-mode': 2, 'supports-changing-layout-mode': True})
        _, configs, extra = scaling.build_config(value, 1.5, info(value)['key'])
        self.assertEqual(configs[1][0], 2880)
        self.assertEqual(extra['layout-mode'].unpack(), 2)

    def test_stale_primary_and_unsupported_scale_are_rejected(self):
        for scale, key in ((1.7, info(state())['key']), (2, (('DP-1', '', '', ''),))):
            with self.assertRaises(ValueError):
                scaling.build_config(state(), scale, key)

    def test_global_only_backend_does_not_change_secondary_displays(self):
        a, b = monitor(), monitor('DP-1')
        value = state([a, b], [(0, 0, 2, 0, True, [a[0]], {}),
                              (1440, 0, 2, 0, False, [b[0]], {})],
                      {'global-scale-required': True})
        self.assertEqual(info(value)['supported'], [2])
        with self.assertRaises(ValueError):
            scaling.build_config(value, 1.5, info(value)['key'])

    def test_failed_verification_never_applies(self):
        client = object.__new__(scaling.DisplayClient)
        with mock.patch.object(client, 'state', return_value=state()), \
                mock.patch.object(client, 'call', side_effect=RuntimeError('invalid layout')) as call:
            with self.assertRaises(RuntimeError):
                client.apply(2, info(state())['key'])
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[1].unpack()[1], 0)

    def test_apply_uses_verification_then_persistence_and_reads_back(self):
        client = object.__new__(scaling.DisplayClient)
        value = state()
        after = state(logical=[(0, 0, 2, 0, True, [value[1][0][0]], {})])
        with mock.patch.object(client, 'state', side_effect=[value, after]), \
                mock.patch.object(client, 'call') as call:
            result = client.apply(2, info(value)['key'])
        self.assertEqual([c.args[1].unpack()[1] for c in call.call_args_list], [0, 2])
        self.assertEqual(result['scale'], 2)


class ScalingWidgetTests(unittest.TestCase):
    def test_initial_selection_recommended_label_and_failed_apply(self):
        oobe = importlib.machinery.SourceFileLoader(
            'oobe_scaling_widget', str(MODULE.with_name('anduinos-oobe'))
        ).load_module()
        if not oobe.Gtk.init_check():
            self.skipTest('GTK display unavailable')
        oobe.Adw.init()
        value = info(state())
        client = mock.Mock()

        def apply(scale, key):
            self.assertEqual(key, value['key'])
            value['scale'] = scale
            return dict(value)

        client.apply.side_effect = apply

        def drain_until(predicate):
            deadline = time.monotonic() + 3
            context = oobe.GLib.MainContext.default()
            while not predicate() and time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                time.sleep(.01)
            self.assertTrue(predicate())

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from descendants(child)
                child = child.get_next_sibling()

        with mock.patch.object(oobe._display_scaling, 'DisplayClient', return_value=client), \
                mock.patch.object(oobe._display_scaling, 'primary_info', side_effect=lambda *_: dict(value)), \
                mock.patch.object(oobe, '_', side_effect=lambda s: s):
            group = oobe.create_display_scaling_group()
            window = oobe.Gtk.Window()
            window.set_child(group)
            try:
                window.present()
                dropdown = next(w for w in descendants(group) if isinstance(w, oobe.Gtk.DropDown))
                row = next(w for w in descendants(group) if isinstance(w, oobe.Adw.ActionRow))
                drain_until(dropdown.get_sensitive)
                client.apply.assert_not_called()
                self.assertEqual(dropdown.get_selected_item().get_string(), '166.67% (Recommended)')
                dropdown.set_selected(value['supported'].index(2))
                drain_until(lambda: dropdown.get_sensitive() and value['scale'] == 2)
                self.assertEqual(dropdown.get_selected_item().get_string(), '200%')
                client.apply.side_effect = RuntimeError('test rejection')
                dropdown.set_selected(value['supported'].index(1.5))
                drain_until(dropdown.get_sensitive)
                self.assertEqual(dropdown.get_selected_item().get_string(), '200%')
                self.assertEqual(row.get_subtitle(), 'Could not change display scaling.')
            finally:
                window.destroy()


if __name__ == '__main__':
    unittest.main()
