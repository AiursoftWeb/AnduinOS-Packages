# Disk Snapshots Manager

Disk Snapshots Manager is the native GTK 4 and libadwaita recovery application
for AnduinOS. It has two equal, explicit destinations:

- **System Recovery** manages immutable recovery points of the mandatory
  `@root` Btrfs subvolume.
- **Personal Files Recovery** manages immutable history points of the mandatory
  `@home` Btrfs subvolume.

Both pages use the same snapshot-list model: create now, configure automatic
snapshots, search, enter selection mode for one authenticated batch deletion,
and open the actions available for one point. System points can prepare a safe
rollback; Personal Files are recovered item by item and are never changed by a
system rollback.

## Product behavior

A snapshot may be protected permanently or be eligible for Smart Cleanup.
Manual, scheduled, and package-change snapshots participate in cleanup by
default. The safety point created before a rollback is permanently protected.
Smart Cleanup uses explicit time buckets: keep everything in the recent window,
then one representative per day, week, month, and year. System and Home policies
are independent and use a configurable one-to-24-hour freshness interval.

The systemd timer remains installed and enabled even when both automatic scopes
are off. On every run the scheduler compares the newest point with the configured
freshness target, so a machine that was asleep or powered off creates a catch-up
point on the next timer activation. The default package policy creates a system
point before a real DPKG transaction and no post-transaction point. Both package
boundaries and snapshot notifications are configured in Advanced Settings.

Nautilus adds “View File History…” for one local Home item and “Browse This
Folder’s History…” for a local Home folder. The extension only activates the
unprivileged GApplication action. It never contacts the system helper or puts a
selected path on the process command line.

## Safety boundary

The GTK process never performs privileged Btrfs operations. It consumes the
existing `org.anduinos.BtrfsSnapshotsManager.Helper` D-Bus contract, while the root helper and
Polkit policy remain the authority for creation, deletion, configuration,
system browsing, and rollback preparation.

A system rollback is prepared only after the target passes availability checks.
The recovery engine creates and protects a current-system fallback, prepares a
verified one-shot GRUB transaction, and applies the root change from initramfs.
The confirmation UI always states that Personal Files remain unchanged and that
a restart is required. The GUI does not replace helper-side validation.

Historical files are opened through descriptor-confined helper operations.
System-snapshot browsing requires administrator authorization. Home browsing is
restricted to the authenticated caller's own Home history. The helper never
receives a caller-selected destination path; the unprivileged GTK process writes
ordinary files and directories without following symbolic links or exporting
special files. See [docs/RECOVERY-SCOPE.md](docs/RECOVERY-SCOPE.md).

## Architecture and platform baseline

The release baseline is resolute-addon with GTK 4.10+, libadwaita 1.4+, Rust
`gtk4` 0.9, and `libadwaita` 0.7. Newer Adwaita APIs are intentionally not used.

- `src/btrfs-snapshots-manager/`: typed `adw::Application`, typed
  `adw::ApplicationWindow`, two snapshot pages, automation/settings, and file
  browsing/recovery.
- `src/btrfs-snapshots-manager-helper/`: privileged D-Bus adapter and policy enforcement.
- `src/anduinos-recovery-engine/`: GUI-independent trusted snapshot and safe
  rollback engine.
- `src/btrfs-snapshots-manager-scheduler/`: systemd-timer freshness and Smart Cleanup worker.
- `src/btrfs-snapshots-manager-notifier/`: unprivileged session notification bridge.
- `src/snapshots-manager-common/`: shared automation, retention, layout, and metadata
  types.

Disk Snapshots Manager as a combined AnduinOS work is distributed under
[GPL-3.0-or-later](../LICENSE). Portions derived from the original Waypoint
project retain their original copyright and MIT license notice in
[LICENSE.upstream-MIT](LICENSE.upstream-MIT).

## Development and qualification

Run the non-destructive engineering gates from this package directory:

```bash
cd src
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cd ..
python3 scripts/check-i18n.py
scripts/test-gui-smoke.sh
scripts/prebuild-check.sh
```

`scripts/test-gui-smoke.sh` constructs and destroys the real Adw application on
a headless GTK Broadway display with fatal GTK criticals. The loopback recovery
test uses only a disposable sparse Btrfs image and exits 77 when its prerequisites
are unavailable. Installed-policy qualification uses invalid mutation payloads
and verifies that recovery state is unchanged.

Actual rebooting rollback, cancellation after reboot, fallback boot, and
power-loss qualification must be run only in a disposable VM with the exact
AnduinOS Btrfs layout, following [docs/VM-QUALIFICATION.md](docs/VM-QUALIFICATION.md).
They are deliberately not host-side package acceptance tests.
