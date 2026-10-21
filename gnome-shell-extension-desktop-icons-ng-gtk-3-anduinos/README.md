# Desktop Icons NG v85 (GTK 3)

This optional AnduinOS package pins GNOME Extensions release 85 of
`ding@rastersoft.com` for GNOME Shell 46 and 50. The upstream ZIP is verified
by SHA-256 at build time. The package owns the same system extension path and
desktop settings as the regular GTK 4 package, so Debian `Conflicts` and
`Replaces` make APT swap one for the other.

Install the GTK 3 alternative from the AnduinOS package repository:

```sh
sudo apt install gnome-shell-extension-desktop-icons-ng-gtk-3-anduinos
```

Return to the regular GTK 4 package:

```sh
sudo apt install gnome-shell-extension-desktop-icons-ng-anduinos
```

Log out and back in after switching versions. A separately installed copy in
`~/.local/share/gnome-shell/extensions/ding@rastersoft.com` takes precedence
over either system package and must be removed or moved out of that directory
to test the selected package.
