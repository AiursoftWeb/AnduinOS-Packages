# Recovery scope and trust boundaries

Disk Snapshots Manager has two independent local snapshot streams and no combined rollback:
System Recovery owns `@root`; Personal Files Recovery owns `@home`. A system
rollback never changes `@home`, and a Home snapshot never becomes a boot target.

## Personal Files are read by descriptor, never written by root

The recovery store is root-owned and mode `0700`. The desktop never receives a
store path and cannot browse another local user's Home history.

1. The GUI supplies a verified Home snapshot ID and bounded relative source path.
   It never supplies a destination path to the helper.
2. The helper anchors lookup at the verified snapshot root and resolves every
   component with `openat2(2)` using `RESOLVE_BENEATH`,
   `RESOLVE_NO_MAGICLINKS`, and a no-symlink policy. Absolute paths, `..`, control
   bytes, devices, sockets, FIFOs, and mount crossings are rejected.
3. Directory listing is a separate bounded metadata operation. A snapshot read
   lock prevents deletion while a listing or descriptor export is active.
4. A regular file is returned as a read-only Unix file descriptor after the
   caller authorization required by policy. No shared temporary tree or
   persistent mount is created.
5. The unprivileged GTK process chooses and writes the destination. Single-file
   recovery writes a private sibling temporary file, synchronizes it, and
   atomically replaces the selected name, so a failed stream does not truncate
   an existing destination.
6. Folder recovery creates a fresh destination tree as the desktop user. It does
   not merge with or overwrite an existing folder, follow links, or recreate
   special files.

## System snapshot browsing is administrator-only

Browsing a System snapshot begins with administrator authorization and an
opaque, caller-bound browse lease. After the user chooses a destination, every
explicit copy operation obtains a separate administrator-authorized export
lease; one folder copy uses one export lease for its bounded traversal. The
helper validates every listing and file descriptor. A second user cannot reuse
either lease. Closing the browser or completing an export releases its lease
asynchronously.

The browser lists names after one authorization, but every exported file remains
constrained to a regular file below the immutable snapshot root. Symbolic links
are not followed; devices, sockets, and FIFOs are not exported. The helper only
provides read-only descriptors. Destination selection and writing remain in the
unprivileged GTK process.

## Nautilus only activates the unprivileged application

The Nautilus 4 extension exposes File History for one native local Home item or
a local Home folder background. It hides the action for remote/GVfs locations,
Trash, special files, multiple selections, paths outside Home, and paths that
contain symbolic links.

Menu creation performs no snapshot I/O. Activation sends `(mode, URI)` to the
session `file-history` GApplication action. Disk Snapshots Manager canonicalizes and validates
the request again before converting it to a relative source. The extension never
contacts the system bus helper and the URI is not placed in process arguments.

## Rollback boundary

The GUI may request verification, display helper-owned facts, and request a
rollback transaction. It cannot declare a snapshot safe, create the fallback,
modify GRUB, or perform the root switch. Those decisions remain in the trusted
recovery engine and helper. If target verification or fallback preparation
fails, no restart is scheduled. Until restart, the pending transaction can be
cancelled through the helper.

The recovery kernel, initramfs, and userspace confirmation engine are copied from
the running system into the snapshot-external recovery store before GRUB is armed.
The initramfs must contain the matching recovery script, engine, confirmation
binary, and protocol marker, and all three executable artifacts are hash-bound to
the transaction. Recovery does not execute the possibly old kernel, initramfs, or
confirmation engine found inside the selected snapshot. The EFI selector acts as a lease:
the recovery menu entry rearms it before starting the kernel, and only terminal
userspace reconciliation clears it.

Before changing the root subvolume, the trusted initramfs verifies its confirmation
binary against the transaction and atomically stages it in the external
`recovery-boot` directory. A hardened transient unit is carried in `/run`, but its
`ExecStart` points to that protected external artifact; no executable payload is
run from `/run`, which may legitimately be mounted `noexec`. This shadows an older
installed confirmation service for one boot, so restoring a snapshot made by an
older package cannot silently downgrade the code that confirms, reverts, or fails
the new transaction. Nothing is written into the immutable snapshot itself.

Initramfs detects the root filesystem itself and never depends on a shell variable
computed—but not exported—by another initramfs-tools process. An explicit recovery
request that cannot enter the matching recovery protocol fails visibly. The engine
persists its boot ID, attempt counter, and last synchronized filesystem checkpoint;
userspace treats a requested boot that reached userspace without initramfs entry as
a failed transaction, restores safe deployment states, and archives the diagnostic.
An armed transaction from an earlier transaction schema or recovery protocol
encountered during a package upgrade is likewise cancelled and archived instead of
being interpreted by incompatible recovery code or left as a stale GRUB loop.
Already-applied legacy transactions remain readable only for identity-checked
confirmation, reversion, and terminal cleanup.

Release claims for this boundary require numbered evidence rather than an
informal successful boot. The release ledger in
[ROLLBACK-RELEASE-TEST-PLAN.md](ROLLBACK-RELEASE-TEST-PLAN.md) records normal
protocol-2 confirmation, legacy reconciliation, automatic fallback, Secure Boot,
and every hard-power-loss checkpoint. A restored package or executable proves
only that initramfs activated the target root; terminal `confirmed` history,
userspace service success, an empty one-shot selector, and cleanup of staging
roots are separate pass conditions.

Snapshot list and refresh operations only read a bounded, non-authoritative size
cache. They never recursively scan Btrfs extents. An explicit Properties request
may read an already available level-zero qgroup; it does not enable quotas or force
a quota rescan, and an unavailable qgroup is shown as unknown.

Notification signals are a separate metadata-minimal channel. They contain only
the fixed scope and aggregate outcome/count information; snapshot titles,
caller identities, paths, and filenames are not sent to the desktop notifier.
