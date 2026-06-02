#!/bin/sh
set -e
for theme in /usr/share/icons/Fluent*; do
    if [ -d "$theme" ]; then
        gtk-update-icon-cache -f -t "$theme" 2>/dev/null || true
    fi
done
