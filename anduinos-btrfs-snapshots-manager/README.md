# Disk Snapshots Manager

Disk Snapshots Manager is the native GTK 4 and libadwaita recovery application
for AnduinOS. It has two equal, explicit destinations:

- **System Recovery** manages immutable system snapshots of the mandatory
  `@root` Btrfs subvolume.
- **Personal Files Recovery** manages immutable snapshots of the mandatory
  `@home` Btrfs subvolume.

Both pages use the same snapshot-list model: create now, configure automatic
snapshots, search, enter selection mode for one authenticated batch deletion,
and open the actions available for one snapshot. System snapshots can prepare a safe
rollback; Personal Files are recovered item by item and are never changed by a
system rollback.

## Product behavior

A snapshot may be protected permanently or be eligible for automatic cleanup.
Manual, scheduled, and package-change snapshots participate in cleanup by
default. A safety snapshot created before a rollback is protected while its
transaction is pending; afterward it can be deleted manually or by automatic cleanup.
Automatic cleanup uses explicit time buckets: keep everything in the recent window,
then one representative per day, week, month, and year. System and Home policies
are independent and use a configurable one-to-24-hour freshness interval.

The systemd timer remains installed and enabled even when both automatic scopes
are off. On every run the privileged helper compares the newest snapshot with the
configured freshness target under the same storage lock used for creation, so a
machine that was asleep or powered off creates at most one catch-up snapshot on the
next timer activation. The default package policy creates a system
snapshot before a real DPKG transaction and no post-transaction snapshot. Both package
boundaries and snapshot notifications are configured in Advanced Settings.
The unprivileged desktop notification listener runs as a supervised user
service. GNOME starts it at login, and opening the application also ensures it
is running, so installing or upgrading the package during an existing session
does not require signing out first.

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
Every complete healthy system snapshot stays `ready` before, during, and after a
rollback, so the same golden image can be restored repeatedly by kiosks, labs,
and shared-machine fleets. Deployment metadata records only storage lifecycle;
rollback progress and outcomes live exclusively in the transaction ledger. Old
transaction-shaped deployment states are accepted as `ready` when upgrading and
never disable the Restore action.
The recovery engine creates and protects a current-system fallback, copies the
currently running kernel, a protocol-verified initramfs, and the matching userspace
confirmation engine into the snapshot-external recovery store, and binds all three
hashes to the transaction. GRUB keeps selecting this
trusted recovery image until initramfs or userspace durably completes or fails the
transaction. Every synchronized root switch is recorded as a persistent checkpoint;
completed and failed transactions are retained in `rollback-history` for diagnosis.
The confirmation UI always states that Personal Files remain unchanged and that
preparing a rollback arms an automatic 60-second restart countdown. Once armed,
the application offers only an immediate restart; it never presents a defer option
that could invite new writes to the soon-to-be-replaced system root. The GUI does
not replace helper-side validation.

Snapshot list refreshes never run Btrfs extent accounting. Cached size information
is non-authoritative; an explicit Properties request reads an existing level-zero
qgroup or, when quotas are off, measures only the selected snapshot with
`btrfs filesystem du`. Each available field is shown independently.

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
- `src/btrfs-snapshots-manager-scheduler/`: systemd-timer freshness and automatic cleanup worker.
- `src/btrfs-snapshots-manager-notifier/`: unprivileged session notification bridge.
- `src/snapshots-manager-common/`: shared automation, retention, layout, and metadata
  types.

Disk Snapshots Manager is distributed under [GPL-3.0-or-later](../LICENSE).

## Development and qualification

Run the non-destructive engineering gates from this package directory:

```bash
cd src
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cd ..
python3 scripts/check-i18n.py
scripts/test-initramfs-integration.sh
scripts/test-recovery-artifacts.sh
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

The numbered release ledger, current pass/pending state, host acceptance lane,
evidence template, incident roots, and remaining TODO are maintained in
[docs/ROLLBACK-RELEASE-TEST-PLAN.md](docs/ROLLBACK-RELEASE-TEST-PLAN.md). Stop
after every rebooting lane and collect its evidence before arming another rollback.
