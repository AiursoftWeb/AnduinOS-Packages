"""Developer-only CLI reference implementation, not installed or used by the service."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

from anduinos_whisper_framework.commands import clean_transcript
from anduinos_whisper_framework.chinese import normalize_chinese_script, whisper_language
from anduinos_whisper_framework.diagnostics import parse_cli_backend, parse_cli_timings
from anduinos_whisper_framework.errors import RecognitionError, RecognitionCancelled, RecognitionTimeout


class WhisperEngine:
    def __init__(self, model: Path, language: str = "auto", threads: int = 0, backend="auto"):
        self.model = model
        self.output_language = language or "auto"
        self.language = whisper_language(self.output_language)
        self.threads = threads or max(1, min(8, (os.cpu_count() or 4) - 1))
        self.last_metrics = {}
        self.backend = backend

    def transcribe(self, pcm: bytes, cancel: threading.Event | None = None) -> str:
        self.last_metrics = {}
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
                "--suppress-nst",
            ]
            if self.backend == "cpu":
                command.append("--no-gpu")
            started = time.monotonic()
            if cancel is None:
                try:
                    result = subprocess.run(
                        command, check=False, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=120,
                    )
                except subprocess.TimeoutExpired:
                    raise RecognitionTimeout("Speech recognition timed out") from None
            else:
                result = self._run_cancellable(command, cancel)
            self.last_metrics = {
                **parse_cli_timings(result.stderr or ""),
                "audio_ms": len(pcm) / 32,
                "inference_ms": (time.monotonic() - started) * 1000,
                "threads": self.threads,
                "backend": "cpu" if self.backend == "cpu" else parse_cli_backend(result.stderr or ""),
                "engine": "cli",
            }
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
                    raise RecognitionTimeout("Speech recognition timed out")
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
