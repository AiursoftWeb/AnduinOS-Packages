if [ "$1" = "configure" ]; then
    # Output directory for the dynamically generated gresource
    OUT_DIR="/var/lib/anduinos-gdm3-wallpaper"
    OUT_FILE="${OUT_DIR}/anduinos-theme.gresource"
    DEFAULT_WALLPAPER="/usr/share/anduinos-gdm3-wallpaper/Floating-objects-dark.png"

    # 1. (Re)generate the custom GDM theme on every install/upgrade
    mkdir -p "$OUT_DIR"

    if [ -f "$DEFAULT_WALLPAPER" ]; then
        echo "Generating default AnduinOS GDM theme..."
        /usr/bin/anduinos-gdm-set-wallpaper --wallpaper "$DEFAULT_WALLPAPER" --output "$OUT_FILE"
    else
        echo "Warning: Default wallpaper $DEFAULT_WALLPAPER not found. Skipping theme generation."
    fi

    # 2. Register with alternatives system (Priority 150)
    # Only register if the file was successfully created
    if [ -f "$OUT_FILE" ]; then
        update-alternatives --install \
            /usr/share/gnome-shell/gdm-theme.gresource \
            gdm-theme.gresource \
            "$OUT_FILE" \
            150
            
        update-alternatives --set \
            gdm-theme.gresource \
            "$OUT_FILE" || true
    fi
fi
