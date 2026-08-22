# AnduinOS Theme Schedule

A user-session Rust service that switches GNOME Dark Style at sunrise and
sunset, plus a GNOME Shell extension that adds **Sunset to Sunrise** to the
Quick Settings Dark Style tile.

The daemon writes `org.gnome.desktop.interface color-scheme`. Fluent GTK and
icon follow-up is left to the existing accent theme extensions.
`anduinos-theme-sync` continues to publish the host GTK3 theme to Flatpak.

Location comes from GeoClue when available, otherwise the last successful
coordinates, otherwise 07:00 / 19:00 local time.
