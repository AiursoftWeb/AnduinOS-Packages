"""Verified per-user downloads for optional whisper.cpp models."""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from typing import Callable

from gi.repository import GLib

from anduinos_whisper_framework.config import MODELS, USER_MODEL_DIR, model_path


class ModelDownloader:
    def download(
        self,
        key: str,
        progress: Callable[[float], None],
        completed: Callable[[], None],
        failed: Callable[[str], None],
    ) -> None:
        threading.Thread(
            target=self._worker,
            args=(key, progress, completed, failed),
            daemon=True,
        ).start()

    @staticmethod
    def _worker(key, progress, completed, failed) -> None:
        model = MODELS[key]
        USER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        destination = USER_MODEL_DIR / model.filename
        partial = destination.with_suffix(".bin.part")
        digest = hashlib.sha256()
        received = 0
        try:
            request = urllib.request.Request(
                model.url, headers={"User-Agent": "AnduinOS-Voice-Typing/2.0.2"}
            )
            with (
                urllib.request.urlopen(request, timeout=30) as response,
                partial.open("wb") as output,
            ):
                total = int(response.headers.get("Content-Length", model.size))
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    GLib.idle_add(progress, min(1.0, received / max(1, total)))
            if received != model.size:
                raise ValueError(f"Expected {model.size} bytes, downloaded {received}")
            if digest.hexdigest() != model.sha256:
                raise ValueError("The downloaded model failed its SHA-256 check")
            os.replace(partial, destination)
            GLib.idle_add(completed)
        except Exception as error:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            GLib.idle_add(failed, str(error))


def remove_user_model(key: str) -> bool:
    model = MODELS[key]
    path = USER_MODEL_DIR / model.filename
    if path.is_file():
        path.unlink()
        return True
    return False


def is_user_model(key: str) -> bool:
    return model_path(key).parent == USER_MODEL_DIR
