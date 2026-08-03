# AnduinOS Timeback Machine

Rust, GTK4, and Libadwaita system-recovery frontend for the AnduinOS Btrfs
storage ABI.

The current TM-3 implementation provides:

- the versioned deployment model and state machine;
- exact, read-only detection of the AnduinOS Btrfs subvolume layout;
- the `timebackctl inspect` diagnostic command;
- the system D-Bus and Polkit contracts;
- a hardened, D-Bus-activated system service with a narrow Btrfs capability;
- bounded deployment discovery that isolates malformed metadata;
- real recovery-point data in the responsive overview and timeline;
- atomic, power-loss-aware manual recovery-point creation;
- integrity verification for the snapshot, dpkg database, kernel, initramfs,
  boot artifacts, and optional MOK certificate;
- guarded pin, unpin, and idempotent delete operations;
- asynchronous progress in both the GTK interface and CLI;
- Polkit authorization for every mutation.

TM-3 also provides the atomic rollback transaction, a one-shot verified
GRUB entry, an idempotent initramfs root replacement, protected fallback,
userspace boot confirmation, D-Bus scheduling and cancellation, and the GTK
restore workflow. These paths are unit-tested but still require destructive VM
qualification before release. TM-4A adds fail-open, paired recovery points
around APT-managed dpkg changes. TM-4B adds the conservative Balanced retention
policy, live free-space planning, and re-measured post-APT cleanup. Its
destructive Btrfs path still requires VM qualification before release.
TM-5A adds a hardened, fail-open periodic maintenance service so space pressure
is detected even when no package transaction has recently completed.

Version 0.2 introduces the target and policy foundations for independently
mounted `@root` and `@home` snapshot streams. Suggested schedules are opt-in:
two-hourly for System and hourly for Home. Tiered retention uses local civil
days, ISO weeks, and months; manual/protected points are never selected for
automatic deletion. Snapshot browsing paths are constrained to their
read-only roots, and space reporting distinguishes referenced bytes from
exclusive (estimated reclaimable) bytes. A Home directory that is not an
independent compatible Btrfs subvolume is reported as unavailable and is never
silently snapshotted as part of root.

The Automatic Snapshots page exposes the complete schedule and tiered-retention
policy. System and User Data may use independent policies, or users may link
them and edit one shared policy. The overview reports the last successful and
next planned snapshot for each active stream. Home snapshots are stored and
retained independently from bootable System recovery points.

The read-only snapshot file browser provides list and grid views, paging,
preview, recursive copy-out, a Places sidebar, and item Properties. Properties
show the historical path, local-time modification date, size, Unix permissions,
visibility, and whether the item can be copied safely. Folder Properties
calculate total size and child counts asynchronously with cancellation and a
100,000-item safety ceiling; files and folders can be copied out directly from
the same dialog. User Data places come from the selected snapshot's own XDG
user-directory configuration, so
customized and localized Desktop, Documents, and media paths remain accurate
historically. Common file classes use content-aware icons, while bounded raster
images receive asynchronously loaded thumbnails with a small in-memory cache;
failed or unsupported image decoding falls back to the safe file-type icon.
The browser remembers list or grid view, hidden-file visibility, sorting, and
copy conflict policy in a bounded, versioned user preference file. Preferences
are replaced atomically and never stored in or applied to a snapshot.
Successful single-file and recursive exports report files, directories, skipped
items, and bytes, with an action that opens the destination in the desktop's
default file manager.
Search in the browser is recursive from the current snapshot location and runs
asynchronously. Results retain their full historical path and support opening
folders, previewing files, viewing Properties, and copying files or whole
folders out. A new query cancels the previous scan; hidden folders follow the
browser's visibility setting, and 100,000 scanned items or 1,000 matches form a
clear safety ceiling. File results can open their containing snapshot folder;
the matching row is focused and kept visible even when it originally fell
outside that directory's first 1,000-item page.

## Local development

```bash
cargo test --all-targets
cargo run --bin anduinos-timeback-machine
```

The current machine's real storage layout is shown by default. A fully
populated visual-design preview is available without requiring a Btrfs system:

```bash
ANDUINOS_TIMEBACK_DEMO=1 cargo run --bin anduinos-timeback-machine
```

Inspect the machine-readable layout report with:

```bash
cargo run --bin timebackctl -- inspect --json
```

On an installed package, query or manage the D-Bus-activated service with:

```bash
timebackctl list --json
timebackctl create --pin "Before a risky change"
timebackctl verify DEPLOYMENT_ID
timebackctl pin DEPLOYMENT_ID
timebackctl unpin DEPLOYMENT_ID
timebackctl delete DEPLOYMENT_ID
timebackctl restore DEPLOYMENT_ID
timebackctl restore --cancel
timebackctl retention --json
timebackctl retention --apply
```

See `ARCHITECTURE.md` before implementing any privileged operation. The
subvolume layout, deployment schema, D-Bus API, and state transitions are
safety boundaries rather than presentation details.

The detailed rollback transaction and power-loss protocol is documented in
`TM3-DESIGN.md`.
The package hook and retention safety boundary is documented in
`TM4-DESIGN.md`.
Automatic maintenance and destructive qualification are documented in
`TM5-DESIGN.md`.
The guarded guest-side qualification entry points live under `tests/vm/` and
must run only on a disposable Btrfs VM.
