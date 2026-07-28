"""Native slideshow data loaded from the historical AnduinOS presentation."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


SLIDE_ORDER = ("welcome", "build", "apps", "gaming", "privacy", "root", "support")
SLIDE_IMAGES = {
    "welcome": "welcome.png",
    "build": "jb.png",
    "apps": "st.png",
    "gaming": "gaming.png",
    "privacy": "pv.png",
    "root": "sc.png",
    "support": "welcome.png",
}


@dataclass(frozen=True)
class Slide:
    key: str
    title: str
    body: str
    image: Path


class _SlideParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.capture: str | None = None
        self.buffer: list[str] = []
        self.title = ""
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag, _attrs):
        if tag in {"h1", "p"}:
            self.capture = tag
            self.buffer = []

    def handle_endtag(self, tag):
        if tag != self.capture:
            return
        text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        if tag == "h1":
            self.title = text
        elif text:
            self.paragraphs.append(text)
        self.capture = None
        self.buffer = []

    def handle_data(self, data):
        if self.capture:
            self.buffer.append(data)


def slideshow_root() -> Path:
    installed = Path("/usr/share/anduinos-installer-beta/slideshow")
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parent.parent / "assets/slideshow"


def load_slides(language: str, root: Path | None = None) -> tuple[Slide, ...]:
    root = root or slideshow_root()
    localized = root / "l10n" / language
    slides = []
    for key in SLIDE_ORDER:
        source = localized / f"{key}.html"
        if not source.is_file():
            source = root / f"{key}.html"
        parser = _SlideParser()
        parser.feed(source.read_text(encoding="utf-8"))
        title = html.unescape(parser.title).strip()
        body = "\n\n".join(
            html.unescape(paragraph).strip()
            for paragraph in parser.paragraphs
            if paragraph.strip()
        )
        if not title or not body:
            raise RuntimeError(f"Invalid slideshow content: {source}")
        slides.append(
            Slide(
                key=key,
                title=title,
                body=body,
                image=root / "screenshots" / SLIDE_IMAGES[key],
            )
        )
    return tuple(slides)
