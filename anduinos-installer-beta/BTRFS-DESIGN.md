# AnduinOS Btrfs architecture

## Status

This document defines the intended Btrfs storage and rollback architecture for
AnduinOS. The subvolume layout is a system ABI: changing it after release
affects installers, upgrades, recovery tools, snapshots and user data.

The legacy beta backend's single `@` subvolume is obsolete. It must not be
treated as the release layout or remain in the production execution path.

## Release-one scope

Release one supports only:

- unencrypted ext4;
- unencrypted Btrfs using the subvolume topology in this document.

Release one does **not** implement LUKS, encrypted swap, TPM2 unlocking, FIDO2
unlocking or recovery-key enrollment. The encryption section below records the
future trust and recovery requirements; it is not part of the first release
contract. The installer UI and plan schema must not expose non-functional
encryption choices.

## Design goals

- A failed system update can be rolled back without rolling back user data.
- A rollback restores a package-database state consistent with `/usr` and
  `/etc`.
- Logs and recovery evidence survive a system rollback.
- VM images, containers and other high-write workloads do not inflate system
  snapshots.
- Every bootable snapshot has a compatible kernel, initramfs and boot entry.
- Snapshot retention remains bounded under disk-space pressure.
- Disk encryption always has a recovery path independent of TPM or FIDO2.
- The ext4 installation path remains simple and does not pretend to offer
  Btrfs snapshot semantics.

## Proposed subvolume layout

| Subvolume | Mount point | System rollback | Snapshotted by default | Purpose |
|---|---|---:|---:|---|
| `@root` | `/` | Yes | Yes | System deployment and package-manager state |
| `@home` | `/home` | No | Separately/opt-in | User data |
| `@log` | `/var/log` | No | No | Persistent diagnostic evidence |
| `@snapshots` | `/.snapshots` | No | No | Snapshot storage and metadata |
| `@containers` | `/var/lib/containers` | No | No | Podman/container storage |
| `@libvirt` | `/var/lib/libvirt/images` | No | No | Virtual-machine images |

`/var` must not be separated as one large subvolume. In particular, these
paths remain inside `@root`:

- `/var/lib/dpkg`
- `/var/lib/apt`
- `/var/cache/apt`

The installed files, dpkg database and APT state therefore share one rollback
boundary. Additional persistent subvolumes may be added only for directories
with a clearly defined lifecycle outside the operating-system deployment.

The installer must create subvolumes before copying data and must generate
explicit mount entries for every subvolume. A subvolume must never be nested
inside the snapshot boundary merely because its mount was forgotten.

## Rollback transaction

A system snapshot is not merely a Btrfs snapshot. It is a deployment record
containing:

- a read-only snapshot of `@root`;
- the snapshot UUID and parent UUID;
- kernel version;
- initramfs identity or digest;
- bootloader and EFI artifact identity;
- dpkg status digest;
- creation time and initiating operation;
- whether the transaction completed successfully;
- whether the user has pinned the snapshot.

APT/dpkg integration should create:

1. A pre-transaction snapshot.
2. A post-transaction snapshot only after dpkg, initramfs and bootloader work
   has completed successfully.

Incomplete post snapshots are not bootable recovery points. Recovery tooling
must reject a deployment whose root, package database, kernel, initramfs and
boot artifacts cannot be shown to match.

User data, logs, containers and VM images do not move when the system is rolled
back.

## `/boot`, kernels and initramfs

The EFI System Partition cannot participate in Btrfs snapshots. This creates a
consistency problem between a root snapshot and its boot artifacts.

### Initial implementation

For the initial implementation:

- `/boot` remains within `@root`.
- `/boot/efi` is the separately mounted FAT EFI System Partition.
- A snapshot may be offered for rollback only while its kernel and initramfs
  are present and a compatible boot entry can be constructed.
- Boot artifact updates must be written atomically where possible.
- Old boot artifacts must not be garbage-collected while a retained deployment
  references them.

Legacy BIOS installations have no ESP dependency for booting, but they still
require GRUB core/modules and the selected root deployment to remain
compatible.

### Long-term direction

Unified Kernel Images are the preferred long-term design:

- each deployment references a signed UKI;
- Secure Boot verifies a single kernel/initramfs/command-line artifact;
- boot selection maps directly to a deployment;
- rollback does not depend on whichever kernel happens to be current.

UKI adoption requires a separate design and migration plan. It is not a reason
to weaken first-release boot verification.

## Copy-on-write policy

CoW must not be disabled globally or across all of `/var`. Doing so would
discard checksums, compression and snapshot benefits for unrelated data.

Dedicated subvolumes are used for known high-write or large-image workloads:

- `/var/lib/libvirt/images`
- `/var/lib/containers`
- Docker storage, if Docker is installed and managed by AnduinOS
- future installer-managed database directories, only after workload testing

For directories where CoW is disabled, the installer or owning package must
set the attribute before the first data file is created. Retrofitting `+C`
after files exist is not sufficient.

Databases are not automatically marked NOCOW. The choice depends on the
database, workload, durability settings and value of checksumming. Application
packages should own those policies rather than the base installer guessing.

## Swap and hibernation

Release one uses a dedicated 4 GiB swap partition for both Btrfs and ext4
installs. It is independent of snapshots and avoids Btrfs swapfile physical
offset and CoW constraints.

AnduinOS also enables:

- LZ4 zram sized to 50% of RAM;
- zram priority 100;
- disk swap priority 10.

The 4 GiB partition is an availability feature, not a promise of hibernation.
zram cannot be used as a persistent resume target.

Hibernation must remain disabled or explicitly unsupported until the installer
can:

- size persistent swap for the machine and expected compression ratio;
- configure a stable resume device;
- generate a matching initramfs;
- verify resume with encryption and Secure Boot enabled;
- handle memory upgrades and insufficient resume capacity.

A later “enable hibernation” option may replace the fixed swap size with a
calculated value. It must be an explicit storage-policy choice.

## Snapshot retention and space pressure

Retention uses time, count and space constraints together. The policy should
distinguish:

- automatic pre/post update snapshots;
- daily and weekly recovery points;
- user-created snapshots;
- pinned snapshots;
- the currently booted deployment.

The currently booted deployment and pinned snapshots are never deleted
automatically. Under pressure, the oldest unpinned automatic snapshots are
removed first. If safe reclamation cannot restore the configured reserve,
snapshot creation stops and the user receives a clear warning.

Plain `df` output is insufficient for Btrfs decisions. The manager must account
for allocated/unallocated space and shared versus exclusive extents. qgroups
may provide useful accounting, but their performance and recovery behaviour
must be validated before they become a default dependency.

No fixed retention counts are part of the storage ABI yet. Defaults require
update simulations and low-disk-space testing.

## Future encryption and recovery

The intended trust hierarchy is:

```text
recovery passphrase or recovery key
                |
              LUKS2
                |
       optional TPM2/FIDO2 unlock
                |
       Btrfs subvolumes/deployments
```

Principles:

- LUKS2 is the encryption boundary; Btrfs lives inside it.
- TPM2 and FIDO2 are convenience unlock methods, never the sole recovery path.
- A human-usable passphrase or offline recovery key must always exist.
- The installer must ask the user to save the recovery key and verify it
  before declaring encrypted installation complete.
- Firmware updates, PCR changes, Secure Boot key changes and motherboard
  replacement must not make offline recovery impossible.
- The MOK enrollment password (`123456`) is unrelated to disk encryption and
  must never be reused as an encryption credential.

When encrypted installation becomes a separately approved milestone, its
first implementation should support LUKS2 with a user passphrase and generated
recovery key. TPM2/FIDO2 enrollment follows only after recovery and
firmware-change tests are automated.

## Installer requirements

Before Btrfs installation is release-ready, the installer must:

- create the complete approved subvolume topology;
- mount every subvolume with its intended options;
- ensure snapshot-excluded paths are separate mounts;
- configure the 4 GiB swap partition and zram priorities;
- copy the live filesystem without importing live-session state;
- generate and validate `fstab`;
- install boot artifacts matching the target deployment;
- verify that the target can be mounted from a clean environment;
- record enough metadata for future snapshot tooling;
- refuse to advertise hibernation unless resume is fully configured;
- pass power-loss and failure-injection tests at every destructive boundary.

## Open decisions and experiments

The following are deliberately not frozen:

- snapshot manager implementation and command-line/API contract;
- retention counts and free-space thresholds;
- whether qgroups are enabled by default;
- UKI layout, naming and signing lifecycle;
- TPM2 PCR policy;
- FIDO2 enrolment UX;
- automatic CoW policy for Docker and specific databases;
- home-directory snapshot and backup integration;
- send/receive-based recovery and remote backup.

These require prototypes and destructive VM tests before becoming release
contracts.
