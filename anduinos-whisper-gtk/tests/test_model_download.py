"""Optional model installation stays atomic, verified and cancellable."""
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
from dataclasses import replace

from anduinos_whisper_framework.config import MODELS, PARAKEET_FILES, model_language_supported
from anduinos_whisper_gtk import models


class ModelDownloadTests(unittest.TestCase):
    def bundle(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name in PARAKEET_FILES:
                archive.writestr('parakeet-tdt-0.6b-v3-int8/' + name, b'payload')
            archive.writestr('../escape', b'never extracted')
        return output.getvalue()

    def install(self, root, data, *, bad_digest=False, cancel=None):
        model = replace(MODELS['parakeet'], size=len(data), installed_size=7 * len(PARAKEET_FILES),
                        sha256='invalid' if bad_digest else hashlib.sha256(data).hexdigest())
        downloader = models.ModelDownloader()
        completed, errors = [], []
        event = cancel or threading.Event()
        with patch.object(models, 'USER_MODEL_DIR', root), \
             patch.dict(models.MODELS, parakeet=model), \
             patch.object(models.urllib.request, 'urlopen', return_value=io.BytesIO(data)), \
             patch.object(models.GLib, 'idle_add', side_effect=lambda f, *a: f(*a)):
            downloader._worker('parakeet', lambda _: None,
                               lambda: completed.append(True), errors.append, event)
        return completed, errors

    def test_only_verified_complete_bundle_becomes_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            done, errors = self.install(root, self.bundle())
            self.assertEqual((done, errors), ([True], []))
            self.assertEqual({p.name for p in (root/'parakeet-v3').iterdir()}, set(PARAKEET_FILES))
            self.assertFalse((root/'escape').exists())
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_checksum_failure_never_publishes_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            done, errors = self.install(root, self.bundle(), bad_digest=True)
            self.assertFalse(done)
            self.assertIn('SHA-256', errors[0])
            self.assertEqual(list(root.iterdir()), [])

    def test_cancel_cleans_up_and_same_model_can_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cancelled = threading.Event(); cancelled.set()
            self.assertEqual(self.install(root, self.bundle(), cancel=cancelled), ([], ['']))
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(self.install(root, self.bundle()), ([True], []))

    def test_removal_preserves_other_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.install(root, self.bundle())
            (root/'ggml-small.bin').write_bytes(b'other model')
            with patch.object(models, 'USER_MODEL_DIR', root):
                self.assertTrue(models.remove_user_model('parakeet'))
            self.assertEqual((root/'ggml-small.bin').read_bytes(), b'other model')

    def test_supported_language_validation_keeps_whisper_coverage(self):
        for language in ['zh-Hans', 'zh-Hant', 'ja', 'ko']:
            self.assertFalse(model_language_supported('parakeet', language))
            self.assertTrue(model_language_supported('base', language))
        for language in ['auto', 'fr', 'en', 'de', 'es', 'mt']:
            self.assertTrue(model_language_supported('parakeet', language))
