"""Conservative post-processing for dictation and opt-in voice commands."""

from __future__ import annotations

import re


VOICE_COMMANDS = {
    "new line": "\n",
    "newline": "\n",
    "换行": "\n",
    "新的一行": "\n",
    "new paragraph": "\n\n",
    "新段落": "\n\n",
    "comma": ",",
    "逗号": "，",
    "period": ".",
    "full stop": ".",
    "句号": "。",
    "question mark": "?",
    "问号": "？",
    "exclamation mark": "!",
    "感叹号": "！",
    "tab": "\t",
    "制表符": "\t",
}

STOP_COMMANDS = {
    "stop listening",
    "stop dictation",
    "停止听写",
    "停止语音输入",
}


def clean_transcript(text: str) -> str:
    """Remove common Whisper non-speech annotations and surrounding space."""

    text = re.sub(
        r"\s*[\[(](?:blank[ _-]audio|silence|music|applause|inaudible)[\])]\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"[ \t]+", " ", text).strip()


def apply_voice_command(text: str, enabled: bool) -> tuple[str, str | None]:
    """Return (replacement text, action) for an exact spoken command."""

    cleaned = clean_transcript(text)
    if not enabled:
        return cleaned, None
    normalized = cleaned.casefold().strip(" .,!?:;，。！？：；\"'“”‘’")
    if normalized in STOP_COMMANDS:
        return "", "stop"
    if normalized in VOICE_COMMANDS:
        return VOICE_COMMANDS[normalized], None
    return cleaned, None


def remove_punctuation(text: str) -> str:
    return re.sub(r"[,.!?;:，。！？；：]", "", text)
