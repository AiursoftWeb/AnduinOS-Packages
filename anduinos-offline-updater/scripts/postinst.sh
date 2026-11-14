#!/bin/sh
set -eu

if [ "${1:-}" = configure ]; then
    # APKG normalizes source timestamps. Equal-size Python edits must not reuse
    # an older timestamp-based cache when the installation worker re-execs.
    py3clean -p anduinos-offline-updater
    py3compile -p anduinos-offline-updater
fi
