#!/bin/bash
set -euo pipefail

if find src tests \
    \( -type d -name '__pycache__' -o \
       -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
    -print -quit | grep -q .; then
    echo "Python cache files must never enter the installer package." >&2
    exit 1
fi

cache_root="$(mktemp -d)"
trap 'rm -rf "$cache_root"' EXIT

PYTHONPYCACHEPREFIX="$cache_root" \
    python3 -m compileall -q src tests scripts/verify-built-package.py
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:tests \
    python3 -m unittest discover -s tests -v

if find src tests \
    \( -type d -name '__pycache__' -o \
       -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
    -print -quit | grep -q .; then
    echo "Prebuild checks polluted the package source tree." >&2
    exit 1
fi
