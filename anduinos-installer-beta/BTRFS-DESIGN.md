# AnduinOS Btrfs architecture

## Status

This document defines the intended Btrfs storage and rollback architecture for
AnduinOS. The release-one canonical subvolume layout is a system ABI: changing
it after release affects installers, upgrades, recovery tools, snapshots and
user data. Future custom layouts use an explicit semantic-role manifest and
are enabled only through the milestones in
[`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).

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

## Default filesystem policy

Btrfs is the AnduinOS default because Timeback, snapshots and shared-space
subvolumes are operating-system capabilities, not because every workload is
faster than ext4. ext4 remains an explicit classic alternative and does not
receive Btrfs snapshot or Timeback semantics.

The default does not change solely because a device reports itself as
rotational. Device names and rotational hints may be unreliable behind USB,
virtualization and storage controllers, and silently switching filesystems
would silently change the installed feature set. The UI may offer contextual
performance guidance while keeping the capability trade-off explicit.

An SSD flash translation layer performing out-of-place writes does not make
filesystem CoW free. Filesystem CoW can still add metadata updates, write
amplification and logical fragmentation. Policy changes require representative
workload, snapshot-pressure and failure testing rather than an analogy to NAND
behaviour or a short installer-time benchmark.

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

## Future custom subvolume layouts

Custom names and paths are supported only after the installer and Timeback can
consume a versioned semantic-role manifest. The manifest maps filesystem and
subvolume UUIDs to roles such as system root, user home, persistent logs,
snapshot store, container data and virtual-machine images.

Timeback compatibility is determined by rollback invariants:

- `/`, `/usr`, `/etc`, `/var/lib/dpkg`, `/var/lib/apt`, `/var/cache/apt` and
  `/boot` share the system-root transaction boundary;
- user home, persistent logs, the snapshot store, containers and virtual
  machines remain outside a system rollback;
- every declared subvolume has an explicit mount;
- the snapshot repository cannot be recursively captured by system
  snapshots;
- boot artifacts can be proven compatible with a retained deployment.

A custom layout that is bootable but violates these invariants is labeled
`Custom layout — Timeback unsupported` before installation. Cosmetic names do
not determine support, and canonical names do not override an invalid
boundary.

The release-one literal names remain the compatibility default until the role
manifest and migration path are implemented. Sharing an existing Btrfs
filesystem with another Linux installation is deferred until namespacing,
collision handling and shared filesystem-wide mount options are specified.

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

Btrfs-specific mount options such as compression and nodatacow apply to the
whole filesystem even when subvolumes are mounted separately. The custom UI
must not present them as independent per-subvolume mount options. Per-workload
NOCOW policy is applied to an empty directory before its first data file is
created and explicitly trades away data checksums and compression.

## Future multi-device Btrfs

The first multi-device milestone consumes a healthy filesystem prepared by an
expert and binds it by FSID plus its complete device UUID set. Creation follows
later as curated profiles with separate data and metadata policy.

Candidate profiles are single/DUP where appropriate, RAID0 with a
zero-redundancy warning, RAID1, RAID10, RAID1C3 and RAID1C4. RAID56 is rejected
for an AnduinOS system root while upstream classifies it as unstable. Device
add/remove, balance and profile conversion are administration features, not
initial installer actions.

Btrfs redundancy does not make an EFI System Partition redundant. Each
independently bootable member needs its own ESP and verified loader lifecycle,
as defined in [`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).

## Swap and hibernation

AnduinOS uses a dedicated, dynamically sized swap partition for both Btrfs and
ext4 installs. It is independent of snapshots and avoids Btrfs swapfile
physical-offset and CoW constraints. The policy always reserves at least 2 GiB
swap and 20 GiB root space. It prefers rounded-up RAM plus 1 GiB when that fits;
otherwise it uses rounded-up RAM/2, capped at 64 GiB.

AnduinOS also enables:

- LZ4 zram sized to 50% of RAM;
- zram priority 100;
- disk swap priority 10.

The size calculation can make the layout large enough for hibernation, but
capacity is not a promise that hibernation is enabled. zram cannot be used as a
persistent resume target.

Hibernation must remain disabled or explicitly unsupported until the installer
also configures and verifies the remaining resume path:

- configure a stable resume device;
- generate a matching initramfs;
- verify resume with encryption and Secure Boot enabled;
- handle memory upgrades and runtime insufficient resume capacity.

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
- configure the policy-sized swap partition and zram priorities;
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
- semantic-role manifest schema and migration from release-one literal names;
- supported multi-device profiles and unequal-device capacity presentation.

These require prototypes and destructive VM tests before becoming release
contracts.
