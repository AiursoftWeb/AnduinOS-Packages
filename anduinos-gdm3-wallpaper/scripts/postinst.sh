if [ "$1" = "configure" ]; then
    # Output directory for the dynamically generated gresource
    OUT_DIR="/var/lib/anduinos-gdm3-wallpaper"
    OUT_FILE="${OUT_DIR}/anduinos-theme.gresource"
    SAVED_WALLPAPER="${OUT_DIR}/wallpaper.png"
    DEFAULT_WALLPAPER="/usr/share/anduinos-gdm3-wallpaper/Floating-objects-dark.png"

    mkdir -p "$OUT_DIR"

    # Older releases saved only the compiled theme. Recover its current image
    # before rebuilding, including effects and images whose original was deleted.
    if [ ! -f "$SAVED_WALLPAPER" ] && [ -f "$OUT_FILE" ] &&
        { [ -z "${2:-}" ] || dpkg --compare-versions "${2%%+*}" lt '2.1.0-3'; }; then
        recovered=$(mktemp "$OUT_DIR/.wallpaper-XXXXXX") || exit 1
        if gresource extract "$OUT_FILE" /org/gnome/shell/theme/background.png > "$recovered" && [ -s "$recovered" ]; then
            if ! cmp -s "$recovered" "$DEFAULT_WALLPAPER"; then
                chmod 644 "$recovered"
                mv -f "$recovered" "$SAVED_WALLPAPER" || exit 1
            fi
            rm -f "$recovered"
        else
            rm -f "$recovered"
            echo "Warning: Cannot recover the existing GDM wallpaper. Keeping the current theme."
            exit 0
        fi
    fi

    wallpaper="$DEFAULT_WALLPAPER"
    if [ -f "$SAVED_WALLPAPER" ]; then
        wallpaper="$SAVED_WALLPAPER"
    fi
    if ! /usr/bin/anduinos-gdm-set-wallpaper --wallpaper "$wallpaper" --output "$OUT_FILE"; then
        echo "Warning: Cannot rebuild the GDM theme. Keeping the current theme."
        exit 0
    fi

    # Upgrades retain the current selection, including automatic mode. On a
    # first install, let our priority replace the automatic upstream default,
    # but still respect a manually selected theme.
    if [ -f "$OUT_FILE" ]; then
        alternative=$(update-alternatives --query gdm-theme.gresource 2>/dev/null || true)
        selected=$(printf '%s\n' "$alternative" | sed -n 's/^Value: //p')
        status=$(printf '%s\n' "$alternative" | sed -n 's/^Status: //p')
        update-alternatives --install \
            "/usr/share/gnome-shell/gdm-theme.gresource" \
            gdm-theme.gresource \
            "$OUT_FILE" \
            150
            
        current=$(update-alternatives --query gdm-theme.gresource 2>/dev/null | sed -n 's/^Value: //p')
        if [ -n "$selected" ] && [ "$selected" != "$current" ] &&
            { [ -n "${2:-}" ] || [ "$status" = manual ]; }; then
            update-alternatives --set gdm-theme.gresource "$selected" || true
        fi
    fi
fi
