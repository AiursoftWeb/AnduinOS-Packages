# Desktop resource policy

The package builds unmodified upstream uresourced 0.5.5 with application management
and without cgroupify. `anduinos-desktop` recommends it. Control Panel provides a
switch and two adjustments through a small Polkit helper.

## One configuration file

`/etc/uresourced.conf` is the upstream INI file and a normal dpkg conffile.
Control Panel and administrators edit this same file. There are no configuration
layers, generated effective configuration, or local daemon patches.

The helper adds `[AnduinOS] Enabled=true/false` to this file when the switch is
saved. Upstream ignores that section; systemd `ExecCondition` uses the helper to
read it. Missing `Enabled` means enabled. Both daemon instances use the same
machine switch, including after reboot and package upgrades.

Upstream can read a personal XDG `uresourced.conf` for application policy. When
present it replaces the system application configuration; missing application
keys use upstream's built-in defaults, not values merged from the system file.
Control Panel reports this existing personal override and does not modify it.

## Apply and lifecycle

The helper reads status without authentication. Changes require root, accept only
the switch, memory amount and foreground-priority values, lock concurrent changes,
and replace the configuration atomically. Other INI keys remain; saving formats
the file using Python's standard INI writer. Restore Recommended Settings resets
only these controls to enabled, 250 MiB and priority 300.

At boot the normal upstream services read the file. Apply stops/restarts these
services and reports their actual state. If saving succeeds but a service fails,
the saved file remains and the error is shown. There is no state snapshot or
rollback journal. Shutdown uses upstream's normal inactive/default resource
settings, not restoration of arbitrary pre-existing runtime properties. Standard
systemd restart-on-failure handles daemon failures.

The system service also removes upstream's generated session drop-in on stop and
reloads user managers. This is a computed `/run` file, not saved preferences.
Remove preserves the conffile; purge follows dpkg's normal conffile removal.
Removing Control Panel does not remove or disable the backend.

## OOM and jobs

Standard systemd drop-ins monitor user `app.slice` and `background.slice` at 80%
memory PSI for 20 seconds, and cancel base root/user@ monitoring. Administrator
monitoring on other ancestors must still be considered. The switch does not
change these boundaries, swap settings or existing GNOME notifications.

`aos-bg` creates a unique background scope, preserves literal arguments and exit
status, and keeps heavy work outside the interactive shell's OOM group.

## Build

Use `apkg build --all`. The existing build script downloads the upstream archive,
runs standard Meson setup/compile/install and stages the package. Build inputs are
declared in `.aosproj`; architecture-specific compilers and fetched development
files provide the normal amd64/arm64 cross-build paths. No C injection or patch
application is performed.
