"""Check configured signing material before DKMS can auto-generate a key."""
import os
from pathlib import Path
import re

from .defaults import CONFIG, configuration_conflict, safe_path
from .identity import check_pair
from .inspect import SubprocessRunner

ORIGINAL = "/usr/libexec/anduinos-dkms-original"
MUTATING = {"build", "install", "autoinstall", "generate_mok"}


def validate_signing_defaults(arguments, runner=None, config=CONFIG):
    if not config.exists() or not MUTATING.intersection(arguments):
        return
    if "generate_mok" in arguments:
        raise ValueError("Use Driver Center to create or repair the signing certificate")
    match = re.fullmatch(r'mok_signing_key="([^"\n]+)"\nmok_certificate="([^"\n]+)"\n', config.read_text())
    if not match:
        raise ValueError("Automatic signing configuration needs repair in Driver Center")
    if configuration_conflict(config):
        raise ValueError("Conflicting DKMS signing configuration needs repair")
    key, certificate = (Path(value) for value in match.groups())
    for path in (config, key, certificate):
        safe_path(path)
    # Matching is checked before the real DKMS script reads its configuration
    # or reaches prepare_mok. A missing key must not replace an enrolled cert.
    check_pair(key, certificate, (runner or SubprocessRunner()).run)


def main(arguments):
    validate_signing_defaults(arguments)
    os.execv(ORIGINAL, ["dkms", *arguments])
