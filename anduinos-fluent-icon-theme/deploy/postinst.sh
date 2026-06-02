#!/bin/sh
set -e
# Update icon caches for Fluent icon themes
for theme in /usr/share/icons/Fluent*; do
    if [ -d "$theme" ]; then
        gtk-update-icon-cache -f -t "$theme" 2>/dev/null || true
    fi
done
