"""Shared model and settings metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SETTINGS_SCHEMA = "com.anduinos.voice-typing"
SYSTEM_MODEL_DIR = Path("/usr/share/anduinos-whisper-framework/models")
USER_MODEL_DIR = Path.home() / ".local/share/anduinos-whisper/models"


@dataclass(frozen=True)
class Model:
    key: str
    title: str
    description: str
    size: int
    sha256: str

    @property
    def filename(self) -> str:
        return f"ggml-{self.key}.bin"

    @property
    def url(self) -> str:
        return (
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
            f"{self.filename}?download=true"
        )


MODELS = {
    "tiny": Model(
        "tiny",
        "Whisper Tiny",
        "Fastest — best for low-power computers",
        77_691_713,
        "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
    ),
    "base": Model(
        "base",
        "Whisper Base",
        "Balanced — included and recommended for most computers",
        147_951_465,
        "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
    ),
    "small": Model(
        "small",
        "Whisper Small",
        "High accuracy — needs more memory and processing power",
        487_601_967,
        "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
    ),
}


def model_path(key: str) -> Path:
    """Return an installed model, preferring a per-user copy."""

    model = MODELS.get(key, MODELS["base"])
    user_path = USER_MODEL_DIR / model.filename
    if user_path.is_file():
        return user_path
    return SYSTEM_MODEL_DIR / model.filename


def model_installed(key: str) -> bool:
    path = model_path(key)
    return path.is_file() and path.stat().st_size >= 1_000_000
