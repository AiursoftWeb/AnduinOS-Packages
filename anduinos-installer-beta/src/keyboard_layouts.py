"""Discover the complete keyboard catalog shipped by ``xkb-data``.

Installer interface languages are a product policy.  Physical keyboard
layouts are not: users must be able to select every layout and variant that
the installed XKB rules support, regardless of the language chosen for the
installer UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import gettext
from pathlib import Path
from xml.etree import ElementTree


XKB_RULES_PATHS = (
    Path("/usr/share/X11/xkb/rules/evdev.xml"),
    Path("/usr/share/X11/xkb/rules/base.xml"),
)


@dataclass(frozen=True)
class XkbVariant:
    id: str
    description: str


@dataclass(frozen=True)
class XkbLayout:
    id: str
    description: str
    variants: tuple[XkbVariant, ...]


def _required_text(parent, path: str, label: str) -> str:
    value = parent.findtext(path)
    if value is None or not value.strip():
        raise RuntimeError(f"XKB rules contain an empty {label}")
    return value.strip()


def parse_xkb_rules(path: Path) -> tuple[XkbLayout, ...]:
    """Parse layouts and variants from one trusted xkeyboard-config file."""

    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise RuntimeError(f"Cannot load XKB keyboard rules: {path}") from error

    layouts: list[XkbLayout] = []
    layout_ids: set[str] = set()
    for node in root.findall("./layoutList/layout"):
        layout_id = _required_text(node, "./configItem/name", "layout id")
        if layout_id in layout_ids:
            raise RuntimeError(f"Duplicate XKB layout id: {layout_id}")
        layout_ids.add(layout_id)
        description = _required_text(
            node, "./configItem/description", f"description for {layout_id}"
        )
        variants: list[XkbVariant] = []
        variant_ids: set[str] = set()
        for variant_node in node.findall("./variantList/variant"):
            variant_id = _required_text(
                variant_node, "./configItem/name", "variant id"
            )
            if variant_id in variant_ids:
                raise RuntimeError(
                    f"Duplicate XKB variant id for {layout_id}: {variant_id}"
                )
            variant_ids.add(variant_id)
            variants.append(
                XkbVariant(
                    variant_id,
                    _required_text(
                        variant_node,
                        "./configItem/description",
                        f"description for {layout_id}+{variant_id}",
                    ),
                )
            )
        layouts.append(XkbLayout(layout_id, description, tuple(variants)))
    if not layouts:
        raise RuntimeError(f"XKB rules contain no keyboard layouts: {path}")
    return tuple(layouts)


@lru_cache(maxsize=1)
def keyboard_layouts() -> tuple[XkbLayout, ...]:
    """Return every self-contained choice provided by host ``xkb-data``."""

    for path in XKB_RULES_PATHS:
        if path.is_file():
            # ``custom`` is only a registry placeholder. It has no packaged
            # symbols and cannot be previewed or reproduced on the target
            # unless a user separately authors and transfers a custom file.
            return tuple(
                layout
                for layout in parse_xkb_rules(path)
                if layout.id != "custom"
            )
    raise RuntimeError("xkb-data is missing its evdev/base keyboard rules")


@lru_cache(maxsize=1)
def _choice_index() -> frozenset[tuple[str, str]]:
    return frozenset(
        (layout.id, variant.id)
        for layout in keyboard_layouts()
        for variant in layout.variants
    ) | frozenset((layout.id, "") for layout in keyboard_layouts())


def is_valid_xkb_choice(layout: str, variant: str = "") -> bool:
    """Return whether *layout* and *variant* are a catalogued XKB pair."""

    return (layout, variant) in _choice_index()


def xkb_choice_id(layout: str, variant: str = "") -> str:
    """Return the identifier format expected by GNOME input sources."""

    return f"{layout}+{variant}" if variant else layout


@lru_cache(maxsize=None)
def _xkb_translation(language: str) -> gettext.NullTranslations:
    return gettext.translation(
        "xkeyboard-config", languages=[language], fallback=True
    )


def translate_xkb_description(description: str, language: str) -> str:
    """Translate an XKB-owned description with its own system catalog."""

    return _xkb_translation(language).gettext(description)
