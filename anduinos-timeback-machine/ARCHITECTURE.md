# AnduinOS Timeback Machine architecture

## Product contract

Timeback Machine is the graphical system-deployment and recovery manager for
AnduinOS. The product calls snapshots **recovery points** because a usable
recovery point is more than a Btrfs subvolume snapshot.

Recovery points are not backups. A system rollback intentionally leaves home
directories, logs, containers, and virtual-machine images unchanged.

## Supported storage ABI

Full functionality is available only when all of the following mounts are
present on the same Btrfs filesystem:

| Subvolume | Mount point | Rolled back |
|---|---|---:|
| `@root` | `/` | yes |
| `@home` | `/home` | no |
| `@log` | `/var/log` | no |
| `@snapshots` | `/.snapshots` | no |
| `@containers` | `/var/lib/containers` | no |
| `@libvirt` | `/var/lib/libvirt/images` | no |

An ext4 installation is supported by AnduinOS, but it is not supported by
Timeback Machine. A Btrfs installation with a different topology is reported
as incompatible and is never modified.

The layout detector reads `/proc/self/mountinfo`; it does not infer support from
the root filesystem type alone.

## Components and trust boundary

```text
anduinos-timeback-machine (desktop user)
                  |
                  | typed system D-Bus API
                  v
anduinos-timebackd (root, system service)
                  |
                  +-- Btrfs deployment store
                  +-- package-manager transaction integration
                  +-- GRUB/initramfs recovery preparation
                  +-- retention and space-pressure policy

timebackctl -------+  CLI for tests, diagnostics, and recovery
```

The GTK process never runs as root. Read-only D-Bus methods do not require
authorization. Every mutation is authorized by the daemon with a dedicated
Polkit action.

The daemon accepts deployment identifiers and typed options, never caller-
provided filesystem paths, executable names, or arbitrary command arguments.
It constructs all paths beneath `/.snapshots/anduinos` itself.

TM-0 froze the domain, on-disk, D-Bus, and authorization contracts. TM-1
installed the root system daemon for bounded read-only discovery. TM-2 adds
manual creation, integrity verification, pinning, and idempotent deletion.
Rollback and retention methods remain explicit milestone errors until their
boot-time and package-manager transactions are implemented and tested.

## On-disk format

```text
/.snapshots/anduinos/
├── deployments/
│   └── <deployment-id>/
│       └── root
├── metadata/
│   └── <deployment-id>.json
├── history/
│   ├── system-lineage.json
│   └── system-lineage.lock
├── home/
│   ├── snapshots/
│   │   └── <home-snapshot-id>
│   └── metadata/
│       └── <home-snapshot-id>.json
└── transactions/
    ├── pending-rollback.json
    ├── pending-package.json
    └── package-history/
        └── <package-transaction-id>.json
```

The deployment ID is a lowercase UUID. Metadata is stored outside `@root`, is
written atomically, and is fsynced before a transaction advances. The schema is
versioned and unknown schema versions are never mutated.

`history/system-lineage.json` is the authoritative, versioned relationship
graph used by the 0.4 system-history UI. New recovery points form an exact
parent chain from the active branch head. A confirmed restore moves that head
to its target; an automatic revert moves it to the protected safety point.
Activation events are keyed by rollback UUID, so resumable confirmation cannot
create duplicate branches. Legacy recovery points are imported as unlinked
history rather than assigned guessed relationships. Deleting snapshot data
keeps a bounded tombstone node so descendants never silently change parents.

System recovery points and Home snapshots are independent streams. System
recovery points snapshot `@root` and carry the boot identity required for a
full rollback. Home snapshots capture the independently mounted `@home`
subvolume and are never offered as system rollback targets. Each stream has an
independent automatic schedule and tiered retention policy; users may link the
two policies when they want identical settings.

`/var/log/anduinos-timeback/` contains persistent operation logs. It is useful
diagnostic evidence but is not authoritative transaction state.

## Deployment invariants

- Only a complete deployment with matching Btrfs UUIDs, read-only property,
  dpkg database, kernel, initramfs, boot artifacts, and MOK identity can be
  restored.
- `Current`, `PendingRollback`, `BootedUnconfirmed`, `FallbackProtected`, and
  pinned deployments cannot be deleted.
- An incomplete post-package-transaction snapshot is visible for diagnosis but
  is never bootable.
- A rollback always creates and protects a recovery point for the current
  system before changing `@root`.
- At least one known-good fallback remains protected until the restored system
  has booted and has been confirmed.
- `@home`, `@log`, `@snapshots`, `@containers`, and `@libvirt` are never moved
  by a system rollback.

## Rollback transaction

The installed system mounts `subvol=@root` explicitly. Changing the Btrfs
default subvolume therefore does not perform a rollback.

A restore is scheduled from the running system and completed from a dedicated
initramfs recovery path:

1. Validate the selected deployment and current boot environment.
2. Create and pin a recovery point for the current system.
3. Atomically persist a pending transaction.
4. Install a one-shot GRUB recovery entry.
5. Reboot and mount the Btrfs top level (`subvolid=5`).
6. Replace `@root` with a writable snapshot of the selected deployment.
7. Verify mounts, kernel, initramfs, GRUB, EFI fallback artifacts, and Secure
   Boot identities.
8. Boot the restored deployment.
9. Confirm it with `anduinos-timeback-confirm.service`.
10. Revert to the protected deployment if confirmation fails.

Every transition is idempotent and must survive power loss.

## Secure Boot

Deployment metadata records the kernel, initramfs, boot artifacts, and MOK
certificate identity. The EFI System Partition is outside Btrfs, so a root
snapshot is offered for restore only when a compatible signed boot chain can be
constructed.

The first implementation keeps the installed shim/GRUB/MOK model. Signed
Unified Kernel Images remain the preferred later deployment format.

## Space accounting

TM-4 does not require qgroups. Initial accounting uses `statvfs` availability
for the Btrfs filesystem; it does not claim per-recovery-point exclusive byte
usage.

The current deployment, pinned deployments, pending target, protected fallback,
and only known-good bootable deployment are never automatically deleted.
The fixed Balanced policy retains at least two complete update transactions and
one known-good restorable deployment. It rebuilds the deletion plan and
re-measures free space after every operation. Policy customization remains
outside the ABI until low-space VM qualification has been completed.

## UI principles

- Use “recovery point” in user-facing copy and “deployment” in technical data.
- Explain what is and is not restored before asking for authentication.
- Keep read-only inspection passive and fast.
- Show progress before starting slow or privileged work.
- Permit cancellation only before the transaction commit boundary.
- Use banners for degraded protection, toasts for completed minor actions, and
  explicit dialogs for destructive or reboot-requiring actions.
- Never show a restore action for a deployment that the daemon has not
  classified as restorable.

## Milestones

- **TM-0 (complete):** contracts, model, layout detector, CLI diagnostics, visual shell.
- **TM-1 (complete):** read-only daemon, deployment discovery, overview and timeline.
- **TM-2 (complete):** manual create, pin, delete, and integrity verification.
- **TM-3A (complete):** versioned rollback transaction, atomic store, retry limits.
- **TM-3B (complete):** initramfs replacement engine and verified one-shot GRUB entry.
- **TM-3C (implemented; VM qualification pending):** boot confirmation,
  automatic fallback, D-Bus and GTK restore UX.
- **TM-4A (implemented; VM qualification pending):** fail-open APT/dpkg pre/post
  recovery-point pairs and interruption recovery.
- **TM-4B (implemented; VM qualification pending):** conservative paired
  retention, bounded free-space reserve, D-Bus/CLI inspection, and fail-open
  post-APT cleanup.
- **TM-5A (implemented; VM qualification pending):** hardened, fail-open
  periodic space-pressure maintenance independent of APT transactions.
- **TM-5B (harness implemented; qualification pending):** guarded real Btrfs
  smoke and GRUB/initramfs rollback cycles plus a read-only-fixture QEMU
  controller covering every apply and automatic-revert checkpoint.
- **UX-0.4A (complete):** atomic system lineage, activation history, safe
  legacy migration, and a read-only D-Bus history graph for the visual tree.
- **UX-0.4B (complete):** four task-oriented primary destinations, an explicit
  “You Are Here” current-system card, a lineage-backed System History view, and
  a dedicated read-only Recover Files entry for System and Personal Files
  snapshots. Storage, diagnostics, and advanced settings remain secondary menu
  destinations.
- **UX-0.4C (complete):** bounded, scrollable system branch map with exact
  lineage connectors, native selectable node cards, automatic focus on the
  current system, correct new-lane rendering after returning to an older point,
  and a separate non-speculative presentation for legacy relationships.
- **UX-0.4D (complete):** persistent node selection and a responsive,
  state-aware history action panel. Browse and verify remain non-mutating,
  restore delegates to the explanatory one-time boot flow, pending restores
  become cancellable, and current or history-only nodes never expose misleading
  actions.
- **UX-0.4E (complete):** prominent pending/confirming restore status on
  Overview, direct pre-boot cancellation, and an evidence-backed explanation of
  the one-shot `grub-reboot` flow, preserved normal menu entries, successful
  branch creation, and automatic fallback.
- **UX-0.4F (complete):** real-state first-run protection checklist and global
  Active / Setup Needed / Attention classification across System recovery,
  Personal Files, and Automatic Protection. Empty states provide direct next
  actions, while service errors and unsupported Home layouts are never
  misrepresented as ordinary setup work.
- **UX-0.4G (complete):** reduced Overview duplication, keyboard
  shortcuts and menu accessibility hints, 0.4.0 package metadata, release
  notes, and successful disposable-loopback Btrfs qualification. Destructive
  GRUB/initramfs reboot and power-cut qualification remains gated on a
  disposable AnduinOS VM.
- **UX-0.4H (complete):** every user-triggered snapshot entry opens the same
  explicit System and User Data / System Only / User Data Only selector.
  Manual Home snapshots use schema v2 metadata, are excluded from automatic
  retention, may link to their paired System recovery point, and can be
  deleted independently from the file-recovery page. CLI creation requires an
  explicit target as well.
