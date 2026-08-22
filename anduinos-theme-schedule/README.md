# AnduinOS Dark Style schedule

A GNOME Shell extension that adds **Sunset to Sunrise** to the Quick Settings
Dark Style tile. The scheduler runs in the same GJS process as the rest of
the Shell.

It writes `org.gnome.desktop.interface color-scheme`. Fluent GTK and icon
follow-up stays with the accent theme extensions.

Location comes from GeoClue when available, otherwise the last successful
coordinates, otherwise 07:00 / 19:00 local time.
