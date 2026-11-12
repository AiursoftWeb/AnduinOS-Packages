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
    download_url: str = ""
    installed_size: int = 0

    @property
    def filename(self) -> str:
        return "parakeet-v3" if self.key == "parakeet" else f"ggml-{self.key}.bin"

    @property
    def url(self) -> str:
        if self.download_url:
            return self.download_url
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
    "parakeet": Model(
        "parakeet", "Parakeet v3", "25 European languages · CPU · about 1.5 GiB RAM",
        630_717_184,
        "2adb3e2e6feaace71119eed506cb18401ac41b8daef1b6411a9e0ca5f12cacfe",
        "https://github.com/Kieirra/murmure-model/releases/download/1.2.0/parakeet-tdt-0.6b-v3-int8.zip",
        812_280_365,
    ),
}

PARAKEET_FILES = ("encoder-model.int8.onnx", "decoder_joint-model.int8.onnx", "nemo128.onnx", "vocab.txt")
PARAKEET_LANGUAGES = frozenset("auto en de fr es it pt ru pl uk nl ro cs el hu sv bg da fi sk hr lt sl lv et mt".split())

def model_language_supported(key: str, language: str) -> bool:
    return key != "parakeet" or language in PARAKEET_LANGUAGES


def model_path(key: str) -> Path:
    """Return an installed model, preferring a per-user copy."""

    model = MODELS.get(key, MODELS["base"])
    user_path = USER_MODEL_DIR / model.filename
    if user_path.is_file() or (key == "parakeet" and user_path.is_dir()):
        return user_path
    return SYSTEM_MODEL_DIR / model.filename


def model_installed(key: str) -> bool:
    path = model_path(key)
    if key == "parakeet":
        return path.is_dir() and all((path / name).is_file() and (path / name).stat().st_size > 0 for name in PARAKEET_FILES)
    return path.is_file() and path.stat().st_size >= 1_000_000
