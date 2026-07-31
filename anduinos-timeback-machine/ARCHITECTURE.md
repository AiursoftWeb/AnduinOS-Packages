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

TM-0 freezes the domain, on-disk, D-Bus, and authorization contracts. The
privileged daemon and mutation methods are deliberately not installed until
their implementation and failure-injection tests exist.

## On-disk format

```text
/.snapshots/anduinos/
├── deployments/
│   └── <deployment-id>/
│       └── root
├── metadata/
│   └── <deployment-id>.json
└── transactions/
    └── pending-rollback.json
```

The deployment ID is a lowercase UUID. Metadata is stored outside `@root`, is
written atomically, and is fsynced before a transaction advances. The schema is
versioned and unknown schema versions are never mutated.

`/var/log/anduinos-timeback/` contains persistent operation logs. It is useful
diagnostic evidence but is not authoritative transaction state.

## Deployment invariants

- Only a complete deployment with matching root, dpkg, kernel, initramfs, and
  boot identities can be restored.
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

TM-0 does not require qgroups. Initial accounting uses raw Btrfs filesystem
usage and filesystem-du data and labels per-recovery-point values as estimates.

The current deployment, pinned deployments, pending target, protected fallback,
and only known-good bootable deployment are never automatically deleted.
Retention defaults remain outside the ABI until update and low-space testing
has been completed.

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

- **TM-0:** contracts, model, layout detector, CLI diagnostics, visual shell.
- **TM-1:** read-only daemon, deployment discovery, overview and timeline.
- **TM-2:** manual create, pin, delete, and integrity verification.
- **TM-3:** initramfs rollback, one-shot boot, confirmation, automatic fallback.
- **TM-4:** APT/dpkg pre/post recovery points and retention.
- **TM-5:** space-pressure automation and destructive failure-injection suite.
