#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
test_root="$(mktemp -d -t anduinos-voice-gui.XXXXXXXX)"
trap 'rm -rf -- "$test_root"' EXIT
cp ../anduinos-whisper-framework/data/com.anduinos.voice-typing.gschema.xml "$test_root/"
glib-compile-schemas --strict "$test_root"
mkdir -m 700 "$test_root/runtime"
export XDG_RUNTIME_DIR="$test_root/runtime"
export XDG_CONFIG_HOME="$test_root/config"
export XDG_CACHE_HOME="$test_root/cache"
export XDG_DATA_HOME="$test_root/data"
export GIO_USE_VFS=local GTK_A11Y=none
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:../anduinos-whisper-framework/src
GSETTINGS_SCHEMA_DIR="$test_root" GSETTINGS_BACKEND=memory GDK_BACKEND=x11 \
    ANDUINOS_GTK_SMOKE=1 dbus-run-session -- xvfb-run -a python3 -m unittest discover -s tests/gui -v
