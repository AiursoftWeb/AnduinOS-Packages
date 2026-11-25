# Desktop resource policy

`anduinos-uresourced` packages upstream uresourced 0.5.5 with application management
and without cgroupify. `anduinos-desktop` recommends it; Control Panel provides the
settings window and calls this package's Polkit helper.

## Configuration

`src/anduin-config.c` merges INI keys in this order:

1. `/usr/share/anduinos-uresourced/defaults.conf` (required vendor defaults).
2. `/etc/uresourced.conf` (optional legacy administrator configuration).
3. `/etc/uresourced.conf.d/*.conf`, sorted by filename.
4. The user's XDG `uresourced.conf`, for `AppBoost` only.

Control Panel writes only `90-anduinos-control-panel.conf`. A later administrator
file such as `95-local.conf` wins. `[AnduinOS] Enabled` is machine-wide and gates
both units through `ExecCondition`; it cannot be overridden by an ordinary user's
file. `--dump-config` reports the merged values and their source paths. This reader
is shared by both daemon modes and helper validation. Upstream 0.5.5 does not
provide this layering; `patches/desktop-policy.patch` integrates the local reader.

The old packaged `/etc/uresourced.conf` is removed on upgrade only when its hash
still matches dpkg's old conffile entry. Modified files remain overrides. It can
remain registered as an obsolete conffile, so dpkg's normal purge semantics apply
to that legacy path. New administrator drop-ins are not package-owned files.

## Apply and restore

`scripts/helper` accepts only `status`, `apply`, `reset`, `cleanup`, and `set JSON`.
Read-only status needs no authentication. Mutation is root-only, serialized,
validated, and limited to the GUI file and this package's services. Only changed
UI fields are supplied; unrelated native keys remain intact. Failed changes
restore the old GUI file and attempt to restore running policy; a failed rollback
is reported. Persistence is implicit; neither UI nor helper offers a temporary mode.

`src/anduin-resources.c` records the previous systemd runtime resource-property
files before each unit is first managed. The private `RuntimeDirectory` journal
allows `ExecStopPost` to restore after both normal shutdown and a crash. Restoration
only touches the four managed properties and checks that the runtime value still
matches the daemon's last update, preserving later external changes. It never
uses `systemctl revert` or removes administrator `/etc/systemd` drop-ins. The root
cleanup also removes the daemon's generated session drop-in and reloads user
managers. Upstream's fixed shutdown resets are replaced by this restoration.

Remove stops user and system instances and withdraws runtime policy; saved GUI
choices remain. Purge also removes the GUI file, without deleting administrator
drop-ins or personal XDG files. Disabled choices remain disabled on upgrade,
relogin, reboot, and reinstall until changed or reset.

## OOM and workload boundaries

The fixed systemd drop-ins monitor user `app.slice` and `background.slice` at
80% memory PSI for 20 seconds, and cancel base root/user@ monitoring. `auto` is not
recursive exclusion: administrator monitoring on another ancestor must be
considered. These fixed OOM boundaries remain when desktop priority is disabled;
this does not stop oomd, change swap settings, or replace GNOME notifications.

`aos-bg` creates a unique background scope, preserving argv and exit status so
heavy terminal work does not share the interactive shell's OOM group. System
services, containers, and VMs need their own budgets.

Build through the standard `.aosproj` (Resolute/Stonking, amd64/arm64). Runtime
acceptance belongs in isolated graphical VMs: cover the GUI, real focus changes,
configuration migration and overrides, on/off/reset, crash/removal recovery,
relogin/reboot, and CPU/I/O/PSI pressure. A MemoryMax OOM notification test alone
does not validate oomd's victim selection.
