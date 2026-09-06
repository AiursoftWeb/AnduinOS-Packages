"""Small subprocess adapter around the distribution's whisper.cpp CLI."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

from .commands import clean_transcript
from .chinese import normalize_chinese_script, whisper_language


class RecognitionError(RuntimeError):
    pass


class RecognitionCancelled(Exception):
    """An obsolete preview or cancelled session is not a user-facing failure."""


class WhisperEngine:
    def __init__(self, model: Path, language: str = "auto", threads: int = 0):
        self.model = model
        self.output_language = language or "auto"
        self.language = whisper_language(self.output_language)
        self.threads = threads or max(1, min(8, (os.cpu_count() or 4) - 1))

    def transcribe(self, pcm: bytes, cancel: threading.Event | None = None) -> str:
        if not self.model.is_file():
            raise RecognitionError(f"Speech model is missing: {self.model}")
        if len(pcm) < 16_000:  # Less than half a second of mono S16LE audio.
            return ""

        with tempfile.TemporaryDirectory(prefix="anduinos-whisper-") as directory:
            audio_path = Path(directory) / "phrase.wav"
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(pcm)

            command = [
                "/usr/bin/whisper-cli",
                "--model",
                str(self.model),
                "--file",
                str(audio_path),
                "--language",
                self.language,
                "--threads",
                str(self.threads),
                "--no-timestamps",
                "--no-prints",
                "--suppress-nst",
            ]
            if cancel is None:
                result = subprocess.run(
                    command, check=False, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=120,
                )
            else:
                result = self._run_cancellable(command, cancel)
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip().splitlines()
            raise RecognitionError(details[-1] if details else "whisper-cli failed")
        transcript = clean_transcript(result.stdout)
        return normalize_chinese_script(transcript, self.output_language)

    @staticmethod
    def _run_cancellable(command, cancel):
        if cancel.is_set():
            raise RecognitionCancelled()
        process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        deadline = time.monotonic() + 120
        try:
            while True:
                if cancel.is_set():
                    raise RecognitionCancelled()
                if time.monotonic() >= deadline:
                    raise RecognitionError("Speech recognition timed out; try a smaller model")
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    if cancel.is_set():
                        raise RecognitionCancelled()
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
