# TM-3 rollback and boot protocol

## Scope

TM-3 restores an AnduinOS `@root` deployment without rolling back the
persistent `@home`, `@log`, `@snapshots`, `@containers`, or `@libvirt`
subvolumes. This document narrows the architecture contract into an executable
and failure-testable protocol.

TM-3A implements the versioned transaction model and atomic persistent store.
TM-3B adds the idempotent initramfs replacement engine, package hooks, recovery
GRUB entry generation, and external environment-block verification. TM-3C adds
transactional scheduling and cancellation, the graphical restore workflow, and
boot confirmation or reverted-state reconciliation. Destructive VM
qualification remains required before the restore workflow is released.

## Why the operation runs in initramfs

The running `@root` is mounted and cannot safely replace itself. The recovery
executor therefore runs in initramfs `local-premount`, after the root device is
available and before initramfs-tools mounts the real root filesystem. It mounts
the Btrfs top level with `subvolid=5` and operates on literal top-level
subvolume names.

The release-one protocol is deliberately limited to the unencrypted,
single-device Btrfs layout installed by AnduinOS. LUKS, mdraid, multi-device
Btrfs, and custom role manifests require separate discovery and recovery
contracts.

## GRUB one-shot entry

Resolute's GRUB 2.14 supports an external environment block in the reserved
Btrfs header area. This permits GRUB to clear `next_entry` safely even though a
normal `grubenv` file on a CoW filesystem is not writable at boot.

Scheduling must verify the capability on the installed filesystem; a version
string is not sufficient. The transaction is not armed unless:

1. `grub-editenv` creates or opens the environment block;
2. its output contains a valid external `env_block` location;
3. `update-grub` emits the expected transaction-specific menu entry;
4. `grub-reboot` records that exact menu-entry ID;
5. the generated entry loads the target recovery point's recorded, signed
   kernel and matching initramfs.

The initramfs protocol never relies solely on `next_entry`. The environment
entry is consumed before the kernel starts, so a power loss during replacement
must be recovered from the persistent transaction state on the next boot.

## Persistent state

The one active transaction lives at:

```text
/.snapshots/anduinos/transactions/pending-rollback.json
```

It conforms to `docs/rollback-v1.schema.json`, is limited to one MiB, is never
read through a symlink, and is committed using a synced temporary file plus an
atomic directory update. Only one transaction may exist.

The transaction references deployment UUIDs rather than caller-provided paths.
All temporary subvolume names are derived from its canonical UUID:

```text
@root.timeback-new-<transaction-id>
@root.timeback-old-<transaction-id>
```

## State machine

```text
Preparing -> Armed -> Applying -> BootedUnconfirmed -> Confirmed
                         |                 |
                         v                 v
                     Reverting --------> Reverted

Preparing/Armed/Applying/Reverting -> Failed (only where no safe automatic
                                      root restoration remains possible)
```

An apply attempt records the initramfs boot ID before the first subvolume
mutation. At most three attempts are permitted. `Preparing` and `Armed` have no
attempts; every applied, confirming, or reverting phase has at least one.

## Idempotent replacement

The initramfs executor reconciles observed subvolume existence with the
transaction rather than blindly replaying commands:

1. Create writable `@root.timeback-new-*` from the selected read-only recovery
   point.
2. Sync the Btrfs filesystem.
3. Rename current `@root` to `@root.timeback-old-*`.
4. Rename the new subvolume to `@root`.
5. Persist `BootedUnconfirmed` and continue the same boot.

A loss between either rename leaves enough deterministic state to finish or
revert. The original writable root is retained until userspace confirmation.

If another boot begins while the transaction is unconfirmed, initramfs first
restores `@root.timeback-old-*`, records `Reverted`, and boots the known prior
system. Confirmation deletes the old writable root only after the restored
system reaches the required systemd target and verifies its deployment IDs.

The confirmation service runs only after `multi-user.target`. It requires the
same initramfs boot ID, kernel release, and Btrfs parent UUID recorded by the
transaction. It commits `Confirmed` before deleting the old writable root, so
cleanup is resumable without accidentally triggering fallback. A `Reverted`
transaction instead marks the target failed, promotes the protected fallback,
and removes the now-inert GRUB recovery entry.

The installed initramfs binary links only `libc` and `libgcc_s`; GTK, GLib, and
D-Bus are not copied into early userspace. Its hook includes `btrfs`, required
shared libraries, and the Btrfs kernel module. The `local-premount` script is a
no-op on non-Btrfs roots and mounts the top level only when inspecting a
pending transaction.

## Failure rules

- A missing or malformed transaction never triggers storage mutation.
- An unknown schema is diagnostic-only and is never upgraded in initramfs.
- Target and fallback deployment IDs must differ.
- The target snapshot UUID, read-only property, kernel, initramfs, dpkg, boot,
  and Secure Boot identities are verified before arming.
- Persistent subvolumes are never renamed, snapshotted, or deleted.
- A terminal transaction cannot transition again.
- Failure diagnostics are bounded and contain no caller-selected paths or
  command output beyond the established sanitizer limits.
