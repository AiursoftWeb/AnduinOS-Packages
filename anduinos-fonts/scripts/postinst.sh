#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    if command -v ionice >/dev/null 2>&1; then
        ionice -c3 -p "$$" 2>/dev/null || true
    fi
    fc-cache -f
fi
#DEBHELPER#
exit 0
