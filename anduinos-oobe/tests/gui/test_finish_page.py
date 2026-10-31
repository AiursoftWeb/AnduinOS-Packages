"""Test finish page rating and feedback functionality."""

import importlib.machinery
import json
from pathlib import Path
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
oobe = importlib.machinery.SourceFileLoader(
    'oobe_finish_page_tests', str(ROOT / 'assets/anduinos-oobe')
).load_module()


class FinishPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not oobe.Gtk.init_check():
            raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
        oobe.Adw.init()

    def wait_for(self, condition, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while oobe.GLib.MainContext.default().pending():
                oobe.GLib.MainContext.default().iteration(False)
            if condition():
                return
            time.sleep(0.01)
        self.fail('Timed out waiting for expected state')

    def test_initial_state(self):
        page = oobe.create_finish_page(lambda: None)
        self.assertEqual(len(page._star_buttons), 5)
        self.assertEqual(len(page._star_images), 5)
        self.assertEqual(page._get_rating(), 0)

        for img in page._star_images:
            self.assertEqual(img.get_icon_name(), 'non-starred-symbolic')

        self.assertFalse(page._feedback_box.get_visible())
        self.assertFalse(page._submit_btn.get_visible())
        self.assertFalse(page._status_label.get_visible())

    def test_five_star_selection(self):
        page = oobe.create_finish_page(lambda: None)
        page._star_buttons[4].emit('clicked')

        self.assertEqual(page._get_rating(), 5)
        for img in page._star_images:
            self.assertEqual(img.get_icon_name(), 'starred-symbolic')

        self.assertFalse(page._feedback_box.get_visible())
        self.assertTrue(page._submit_btn.get_visible())

    def test_less_than_five_star_selection(self):
        page = oobe.create_finish_page(lambda: None)
        page._star_buttons[2].emit('clicked')  # 3 stars

        self.assertEqual(page._get_rating(), 3)
        for idx in range(3):
            self.assertEqual(page._star_images[idx].get_icon_name(), 'starred-symbolic')
        for idx in range(3, 5):
            self.assertEqual(page._star_images[idx].get_icon_name(), 'non-starred-symbolic')

        self.assertTrue(page._feedback_box.get_visible())
        self.assertTrue(page._submit_btn.get_visible())

    def test_switch_between_five_and_lower_stars(self):
        page = oobe.create_finish_page(lambda: None)

        # First choose 3 stars -> feedback visible
        page._star_buttons[2].emit('clicked')
        self.assertTrue(page._feedback_box.get_visible())

        # Then choose 5 stars -> feedback hidden
        page._star_buttons[4].emit('clicked')
        self.assertFalse(page._feedback_box.get_visible())
        self.assertTrue(page._submit_btn.get_visible())

        # Switch back to 4 stars -> feedback visible
        page._star_buttons[3].emit('clicked')
        self.assertTrue(page._feedback_box.get_visible())
        self.assertTrue(page._submit_btn.get_visible())

    def test_submit_success_five_stars(self):
        page = oobe.create_finish_page(lambda: None)
        page._star_buttons[4].emit('clicked')

        recorded_requests = []

        class DummyResponse:
            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def mock_urlopen(req, timeout=None):
            recorded_requests.append(req)
            return DummyResponse()

        with patch('urllib.request.urlopen', side_effect=mock_urlopen):
            page._submit_btn.emit('clicked')
            self.wait_for(lambda: page._status_label.get_visible() and not page._submit_btn.get_visible())

        self.assertEqual(len(recorded_requests), 1)
        sent_req = recorded_requests[0]
        self.assertEqual(sent_req.get_full_url(), 'https://www.anduinos.com/api/rating')
        payload = json.loads(sent_req.data.decode('utf-8'))
        self.assertEqual(payload, {'stars': 5, 'comment': None})
        self.assertIn('Thank you for your feedback!', page._status_label.get_text())

    def test_submit_with_comment_four_stars(self):
        page = oobe.create_finish_page(lambda: None)
        page._star_buttons[3].emit('clicked')  # 4 stars
        page._improve_entry.set_text('Add more themes')

        recorded_requests = []

        class DummyResponse:
            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def mock_urlopen(req, timeout=None):
            recorded_requests.append(req)
            return DummyResponse()

        with patch('urllib.request.urlopen', side_effect=mock_urlopen):
            page._submit_btn.emit('clicked')
            self.wait_for(lambda: page._status_label.get_visible() and not page._submit_btn.get_visible())

        self.assertEqual(len(recorded_requests), 1)
        payload = json.loads(recorded_requests[0].data.decode('utf-8'))
        self.assertEqual(payload, {'stars': 4, 'comment': 'Add more themes'})
        self.assertIn('Thank you for your feedback!', page._status_label.get_text())

    def test_submit_rate_limit_error(self):
        page = oobe.create_finish_page(lambda: None)
        page._star_buttons[3].emit('clicked')

        def mock_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                'https://www.anduinos.com/api/rating', 429, 'Too Many Requests', None, None
            )

        with patch('urllib.request.urlopen', side_effect=mock_urlopen):
            page._submit_btn.emit('clicked')
            self.wait_for(lambda: page._status_label.get_visible() and page._submit_btn.get_sensitive())

        self.assertIn('Submission limit reached', page._status_label.get_text())
        self.assertTrue(page._submit_btn.get_sensitive())

    def test_submit_network_error(self):
        page = oobe.create_finish_page(lambda: None)
        page._star_buttons[2].emit('clicked')

        def mock_urlopen(req, timeout=None):
            raise urllib.error.URLError('Network unreachable')

        with patch('urllib.request.urlopen', side_effect=mock_urlopen):
            page._submit_btn.emit('clicked')
            self.wait_for(lambda: page._status_label.get_visible() and page._submit_btn.get_sensitive())

        self.assertIn('Network error', page._status_label.get_text())
        self.assertTrue(page._submit_btn.get_sensitive())


if __name__ == '__main__':
    unittest.main()
