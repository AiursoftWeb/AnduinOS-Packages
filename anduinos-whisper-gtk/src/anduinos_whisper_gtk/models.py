"""Verified, cancellable per-user speech model downloads."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import threading
import urllib.request
import zipfile
from typing import Callable

from gi.repository import GLib
from anduinos_whisper_framework.config import MODELS, USER_MODEL_DIR, PARAKEET_FILES, model_path


class DownloadCancelled(Exception):
    pass


class ModelDownloader:
    def __init__(self):
        self._downloads = {}
        self._lock = threading.Lock()

    def cancel(self, key):
        with self._lock:
            event = self._downloads.get(key)
            if event is not None:
                event.set()

    def download(self, key: str, progress: Callable[[float], None],
                 completed: Callable[[], None], failed: Callable[[str], None]) -> None:
        with self._lock:
            if key in self._downloads:
                return
            cancel = threading.Event()
            self._downloads[key] = cancel
        threading.Thread(target=self._worker, args=(key, progress, completed, failed, cancel),
                         daemon=True).start()

    def _worker(self, key, progress, completed, failed, cancel) -> None:
        model = MODELS[key]
        USER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        destination = USER_MODEL_DIR / model.filename
        partial = USER_MODEL_DIR / (model.filename + '.part')
        staging = USER_MODEL_DIR / (model.filename + '.extracting')
        digest = hashlib.sha256()
        received = 0
        def check():
            if cancel.is_set():
                raise DownloadCancelled()
        callback = None
        try:
            shutil.rmtree(staging, ignore_errors=True)
            required = model.size + model.installed_size
            if shutil.disk_usage(USER_MODEL_DIR).free < required:
                raise OSError('Not enough free space to download and install the model')
            request = urllib.request.Request(model.url, headers={'User-Agent': 'AnduinOS-Voice-Typing'})
            with urllib.request.urlopen(request, timeout=5) as response, partial.open('wb') as output:
                while True:
                    check()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if received > model.size:
                        raise ValueError('The download exceeds its expected size')
                    GLib.idle_add(progress, min(1.0, received / max(1, model.size)))
            check()
            if received != model.size or digest.hexdigest() != model.sha256:
                raise ValueError('The downloaded model failed its size or SHA-256 check')
            if key == 'parakeet':
                staging.mkdir(mode=0o700)
                with zipfile.ZipFile(partial) as archive:
                    for name in PARAKEET_FILES:
                        check()
                        member = archive.getinfo('parakeet-tdt-0.6b-v3-int8/' + name)
                        if not 0 < member.file_size <= model.installed_size:
                            raise ValueError('Invalid model archive size')
                        # Copy only known regular payloads; never extract archive paths or symlinks.
                        with archive.open(member) as source, (staging / name).open('wb') as output:
                            remaining = member.file_size
                            while remaining:
                                check()
                                block = source.read(min(1024 * 1024, remaining))
                                if not block:
                                    raise ValueError('Incomplete model archive')
                                output.write(block)
                                remaining -= len(block)
                if sum((staging / name).stat().st_size for name in PARAKEET_FILES) != model.installed_size:
                    raise ValueError('Invalid installed model size')
                check()
                os.replace(staging, destination)
                partial.unlink()
            else:
                os.replace(partial, destination)
            callback = (completed,)
        except DownloadCancelled:
            callback = (failed, '')
        except Exception as error:
            callback = (failed, str(error))
        finally:
            partial.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            with self._lock:
                self._downloads.pop(key, None)
        if callback:
            GLib.idle_add(*callback)


def remove_user_model(key: str) -> bool:
    path = USER_MODEL_DIR / MODELS[key].filename
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return True
    if path.is_file() or path.is_symlink():
        path.unlink()
        return True
    return False


def is_user_model(key: str) -> bool:
    return model_path(key).parent == USER_MODEL_DIR
