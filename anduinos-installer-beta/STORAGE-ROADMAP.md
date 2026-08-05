# AnduinOS installer storage roadmap

## Status and intent

This document defines the post-release-one development plan for storage. The
beta now exposes both deterministic whole-disk erase and guided coexistence in
already-unallocated space, with Btrfs or ext4, without a feature flag. Custom
layouts, encryption and RAID are not currently implemented. Guided mode does
not shrink, move or repair Windows filesystems and remains subject to the
unfinished destructive ISO/VM qualification described below.

The long-term product has three user-facing storage modes:

1. **Automatic installation** — give AnduinOS a complete disk.
2. **Install alongside** — consume selected unallocated space or an explicit
   disposable partition while preserving other operating systems.
3. **Custom storage** — assign partitions, filesystems, Btrfs subvolumes,
   mount points, ESPs and already-prepared arrays.

The installer will not become a general-purpose filesystem repair, partition
move or partition shrink tool. Those operations remain the responsibility of
specialist tools and the operating system that owns the data.

## Implementation status

| Milestone | Status | Current boundary |
|---|---|---|
| Storage 0 | complete | contracts, terminology and release gates frozen |
| Storage 1A | complete | immutable inventory, topology binding, erase-disk write set and command-parity gate |
| Storage 1B | complete | strict graph schema v1, `InstallPlan` v5 and privileged stable-identity resolution |
| Storage 2A | complete | read-only eligibility, Windows/BitLocker classification and no-force guidance |
| Storage 2B | complete | graph schema v2, `InstallPlan` v6 and canonical coexistence declarations |
| Storage 2C | complete | free-space compiler, preserved-ESP checks and shared boot policy |
| Storage 2D | complete | GTK free-extent/ESP selection and typed write-set confirmation |
| Storage 2E | in progress | beta execution enabled by default; runtime preservation proofs and isolated VM qualification remain |
| Storage 3 | pending | custom partitions, mounts and Btrfs roles |
| Storage 4 | pending | existing LVM containers and arrays |
| Storage 5 | pending | curated array creation |
| Storage 6 | pending | LUKS2 and recovery-led encryption |

Storage 1A deliberately does not change `InstallPlan` schema version, GTK
storage choices or the set of destructive commands. During all-step preflight,
the existing erase-disk executor now freezes one immutable composition of its
layout, command plan and typed write set, and fails before disk writes if the
execution and declaration drift.

Storage 1B upgrades the privilege-boundary document to `InstallPlan` v5 and
adds storage graph schema v1. The graph contains stable references, expected
geometry, typed declarations and enum operations, but no device paths,
commands, hooks or raw formatter/mount arguments. The legacy path in
`StorageSpec.disk` is a display hint: the privileged preflight re-probes the
inventory, verifies the selected topology digest, resolves the current device
path and only then freezes the erase-disk execution plan. Unknown fields,
non-canonical graphs and graph/write-set drift fail closed.

Storage 2B upgrades the document to `InstallPlan` v6 and storage graph schema
v2. Guided graphs can now reference every preserved partition by PARTUUID,
one exact free extent by its parent topology digest and the new partitions
allocated wholly inside it. They declare either a preserved ESP or a new
dedicated ESP, omit BIOS and shared fallback writes, and require an AnduinOS
NVRAM operation. At the Storage 2B boundary the executor still rejected guided
mode; that historical gate was opened later by the beta Storage 2E work.

Storage 2C adds a privileged coexistence compiler without enabling execution.
It revalidates the current topology, compiles every graph operation into a
matching typed write set and emits only bounded `mkpart` commands plus
formatters for newly declared partitions. Its parity gate rejects partition
table replacement/removal/resizing, undeclared formatters, shared-ESP
formatting, geometry drift and graph/write-set drift.

An existing ESP now requires a fresh read-only `fsck.fat -n` result, stable
PARTUUID/filesystem UUID and at least 64 MiB free. Capacity is measured from a
temporary read-only `vfat` mount with `nosuid,nodev,noexec`. The guided boot
plan writes only `EFI/AnduinOS`, passes `--no-extra-removable`, then creates
and verifies an exact AnduinOS NVRAM entry. Unavailable EFI variables fail
before the compiler authorizes disk writes. These compiler invariants remain
mandatory now that guided execution is available in the beta.

Storage 2D added the GTK planning and confirmation workflow. It binds a disk
by stable identity and byte size, selects one exact
topology-bound unallocated extent, and lets the user choose Btrfs or ext4 and
either a preliminary shared ESP or a new dedicated ESP when geometry permits.
The final page is reduced from the canonical typed write set: it lists every
preserved partition, every new boundary and formatter, shared-ESP policy,
vendor-only boot writes and NVRAM intent.

When no eligible extent exists, the page explains that Windows users must
shrink a Windows volume in Windows Disk Management, reboot if requested,
return to the live installer and rescan. Rescanning deliberately clears the
target selection so the user must inspect and select it again. There is no
force-continue action and no installer-side shrink. Immediately before
submission, the frontend re-probes and recompiles the selected disk, extent
and ESP; topology drift fails closed and requires a new scan and selection.

Storage 2E is in progress. Password-protected guided plans now use the normal
beta GTK and public executor path by default. The separate passwordless VM
plan, power-cut markers and disposable-guest policy remain test-only: the
internal Python executor requires both `--guided-destructive-test` and the
`ANDUINOS_INSTALLER_DESTRUCTIVE_TEST=1` environment marker. The installed
public executor wrapper rejects arguments and therefore cannot select that
test policy.

Before the first write, the executor freezes every existing partition's GPT
identity, number, boundary, type and filesystem UUID. Reused ESP inspection
also hashes every directory and file outside installer-owned `EFI/AnduinOS`.
After formatting it verifies the complete declared partition result and every
preserved partition; after GRUB it verifies the ESP tree, vendor loader and
exact AnduinOS NVRAM entry. Stable before/after markers now surround every
guided partition command, formatter, vendor boot-file write and NVRAM write.
Every fixed executor step also has a guided-only boundary, covering mounts,
system extraction, configuration, chroot transitions and unmount. Unit tests
inject failure at each new partition and formatter boundary.

The separate coexistence VM matrix covers Btrfs/ext4, Secure Boot on/off and
shared/new ESP policy. Its runner accepts only a SHA-256-pinned regular qcow2
fixture, clones it into a new output directory and never accepts a host block
device. A test-only planner binds a passwordless plan to a fresh `/dev/vda`
inventory. A strict before/after verifier records full raw hashes for every
non-shared preserved partition, per-file shared-ESP hashes and existing UEFI
variables. A separate fresh qcow2 evidence disk preserves these artifacts
across a serial-marker-triggered hard power cut; recovery boot is accepted
only for a matching recorded campaign. The ISO, Windows disk, OVMF CODE and
Windows-paired VARS are all SHA-256 pinned. Host-side run results retain final
artifact hashes but deliberately cannot claim an automatic test pass. Before
evidence capture, the Windows Boot Manager entry must bind that VARS state to
an existing target-disk ESP and the Microsoft loader.

Storage 2E remains incomplete until a real Windows-shaped fixture has passed
every row, independent Windows/AnduinOS boot tests, failure injection and
hard-power-cut recovery. Safe automatic adoption or retry of partially-created
guided targets is also unresolved. The beta UI is intentionally enabled while
that qualification is incomplete; a stable-release claim remains blocked on
those artifacts.

## Product decisions

### Filesystem default

Btrfs remains the default on solid-state and rotational media because
Waypoint, system snapshots and the shared-space subvolume model are
AnduinOS capabilities. ext4 remains an explicit classic alternative without
Waypoint or transactional system rollback.

The installer must not infer filesystem policy from a device path such as
`nvme` or `sd`. NVMe is a transport, SATA devices may be SSDs, and rotational
hints may be unreliable behind USB bridges, virtual machines and RAID
controllers. Hardware hints may produce contextual guidance, but must not
silently remove operating-system capabilities.

The installer must not run a short synthetic benchmark to select a
filesystem. Such a benchmark cannot represent future workloads, snapshot
count, free-space pressure or failure behaviour.

### Supported layout versus installable layout

The UI and final summary distinguish three outcomes:

- **AnduinOS-managed** — canonical, fully supported and eligible for
  Waypoint.
- **Validated custom** — bootable and updateable, but some capabilities are
  disabled because the topology does not satisfy their invariants.
- **Invalid** — the executor refuses the layout; there is no “continue
  anyway” path around boot or data-safety requirements.

Custom Btrfs names are not inherently unsupported. Waypoint eligibility is
decided by semantic roles and rollback boundaries, not cosmetic names. A
layout that moves package-manager state, boot artifacts or required
persistent data across incompatible boundaries cannot be certified merely
because its subvolumes happen to use canonical names.

### RAID policy

The first RAID milestone consumes arrays prepared by an expert. Array
creation follows later as a small number of curated presets. The installer
does not initially attempt to replace mdadm, storage-controller tooling or
the Btrfs administration interface.

Btrfs RAID56 is not accepted for an AnduinOS system root while upstream marks
it unstable. RAID0 may be offered only with an explicit no-redundancy warning.
Supported redundant Btrfs profiles are introduced separately and only after
degraded-boot and device-loss testing.

## Storage terminology and layer model

The UI must use precise terms instead of calling every object a “volume”:

- **disk** — a physical disk or a hardware-controller logical disk;
- **partition/free extent** — a GPT/MBR region;
- **container** — mdraid, LUKS, LVM or a multi-device Btrfs filesystem;
- **filesystem** — Btrfs, ext4 or another explicitly supported root driver;
- **subvolume** — a Btrfs filesystem object sharing the parent filesystem's
  free space;
- **mount** — the association between a filesystem/subvolume and a target
  path;
- **ESP** — a FAT EFI System Partition containing firmware-readable boot
  artifacts.

The planner represents these as a graph:

```text
physical or controller device
        |
partition or unallocated extent
        |
mdraid / LUKS / LVM / Btrfs multi-device container
        |
filesystem
        |
Btrfs subvolume, when applicable
        |
mount point and semantic role
```

Release milestones add graph node types without weakening executor
validation for existing modes.

## Planner and executor architecture

### Read-only inventory

Storage selection operates on an immutable inventory captured by the
unprivileged planner. Each object records its display path and stronger
identity:

| Object | Required identity and geometry |
|---|---|
| Disk | WWN, serial or persistent by-id; exact byte size |
| GPT | disk GUID and complete partition-map digest |
| Partition | PARTUUID, parent identity, start sector and length |
| Free extent | parent map digest, start sector and length |
| mdraid | MD UUID, level, state and complete member set |
| LUKS | LUKS UUID and backing-object identity |
| Filesystem | filesystem UUID and type |
| Btrfs filesystem | FSID and complete device UUID set |
| Btrfs subvolume | subvolume UUID, parent UUID and path |

Kernel device paths are display and resolution hints, not authorization. The
privileged executor re-probes identities and geometry immediately before the
first write. Any changed partition boundary, new array member, changed size,
new mount or active mapping invalidates the plan.

### Declarative plan

The next storage schema replaces the current single-disk `StorageSpec` with
fixed, typed declarations equivalent to:

```text
StoragePlan
  mode
  inventory_digest
  block_references[]
  operations[]
  filesystems[]
  subvolumes[]
  mounts[]
  boot_targets[]
  requested_capabilities[]
```

Operations are trusted enum variants such as preserve, create partition,
format, create subvolume, write AnduinOS boot files and update NVRAM. The plan
contains no commands, arbitrary hooks or raw `mkfs` arguments.

Future custom mode may submit declarative mount paths. The executor normalizes
and validates them, rejects traversal and pseudo-filesystem targets, and
constructs every mount command itself.

### Write set and confirmation

Every plan produces a deterministic write set before execution:

| State | Meaning |
|---|---|
| Preserve | no installer write is permitted |
| Create | allocate a new partition, container or subvolume |
| Format | destroy the selected object's existing filesystem |
| Configure | write `fstab`, initramfs or array configuration |
| Boot files | write only named AnduinOS paths on a selected ESP |
| Firmware | create or update an AnduinOS NVRAM boot entry |

The final UI presents the write set per object. A generic “the disk may be
modified” warning is insufficient for coexistence or RAID.

The executor owns operation order, failure policy and cleanup. Plans cannot
mark a fatal storage action optional.

## Mode 1: automatic installation

This is the release-one path and remains the recommended simple mode.

- The user selects one complete disk.
- The final confirmation names its stable identity and states that every
  partition will be destroyed.
- The installer creates the deterministic architecture-specific partition
  table, ESP, swap and root filesystem.
- Btrfs uses the canonical subvolume topology; ext4 uses a single root.
- Because the installer owns the ESP, it may install the architecture's UEFI
  fallback loader.

This path remains isolated from the more permissive custom graph. Its existing
safety and retry properties must not regress while the plan schema evolves.

## Mode 2: install alongside

### Initial support boundary

The first coexistence release supports:

- UEFI firmware and GPT only;
- one physical disk without mdraid, device mapper or multi-device Btrfs;
- a healthy existing FAT ESP or space for a new dedicated ESP;
- one sufficiently large unallocated extent or one explicit disposable
  target partition;
- Btrfs or ext4 root;
- optional swap created entirely inside the selected free extent;
- no hibernation promise.

It does not support:

- shrinking or moving NTFS, ext4, Btrfs or another filesystem;
- manipulating BitLocker;
- writing to Windows data or recovery partitions;
- Legacy BIOS/MBR coexistence;
- RAID or LVM;
- recovery from a filesystem that is already inconsistent.

Users reclaiming Windows space should shrink Windows from Windows and leave
the result unallocated. The installer then allocates only inside that extent.

### Windows-shaped topology

```text
GPT disk
├── EFI System Partition       preserve; add only EFI/AnduinOS when reused
├── Microsoft Reserved         preserve
├── Windows NTFS               preserve
├── Windows recovery           preserve
├── selected unallocated area  create AnduinOS partitions here
│   ├── optional swap
│   └── Btrfs or ext4 root
└── any other partition        preserve
```

The planner rejects a free extent if its parent partition-map digest or exact
boundaries change between selection and execution. Creating partitions must
never renumber or rewrite unrelated partitions as an incidental shortcut.

### Shared ESP policy

An existing ESP is reusable only when its partition type, FAT filesystem,
health and free-space reserve pass validation. Reuse means:

- never format the ESP;
- never delete or rename another vendor's directory;
- write only the AnduinOS-owned vendor directory;
- do not overwrite `EFI/BOOT/BOOTX64.EFI` or
  `EFI/BOOT/BOOTAA64.EFI` on the shared ESP;
- create and verify an AnduinOS UEFI NVRAM entry;
- fail with recovery instructions if firmware variables cannot be updated,
  rather than silently taking over the shared fallback path.

If the existing ESP is unsuitable, guided mode refuses it. Custom mode may
allocate a dedicated AnduinOS ESP inside explicitly selected free space.

Windows discovery and an optional GRUB chainloader entry are usability
features. Firmware boot entries remain independently usable; detection must
not authorize writes to Windows partitions.

### Swap policy

When guided mode owns a free extent, it may create the release swap partition
inside that extent. When replacing only one pre-created root partition, the
initial implementation uses zram without persistent swap until a tested
installer-managed swapfile policy exists. Neither variant advertises
hibernation.

## Mode 3: custom storage

Custom storage is a topology mapper rather than a general repair tool. Users
may prepare storage in GNOME Disks, GParted, mdadm or another specialist tool,
return to the installer, request a fresh probe and assign roles.

### Partition and mount mapping

The initial custom mode supports:

- format or preserve for explicitly selected objects;
- required `/`;
- one UEFI ESP;
- optional swap;
- optional separate `/home`, `/boot`, `/var`, `/srv` and `/opt` where the
  selected filesystem driver supports them;
- Btrfs subvolume creation or selection;
- existing assembled arrays added by a later RAID milestone.

Mount validation requires normalized absolute paths, rejects `..`, rejects
duplicates and prevents target escape through symlinks. `/proc`, `/sys`,
`/dev`, `/run` and installer staging paths are never user mount targets.

Some layouts may be bootable but incompatible with Waypoint. A separate
`/var` is not Waypoint-compatible if dpkg or APT state leaves the system-root
rollback boundary. Splitting `/usr` or `/etc` is initially rejected.

The installer never overlays a new operating system onto an arbitrary
populated root. A non-format install target must be a verified-empty
filesystem or a newly created empty Btrfs subvolume.

### Filesystem driver contract

Filesystem support is internal and capability-based. Each trusted driver owns
format command construction, mount options, `fstab` generation, verification,
minimum size, boot constraints and recovery tooling.

| Driver | Initial status | Waypoint | Notes |
|---|---|---:|---|
| Btrfs | supported | conditional | canonical or role-compatible layout |
| ext4 | supported | no | simple root and conventional split mounts |
| XFS | deferred | no | requires separate boot/recovery matrix |
| F2FS | deferred | no | requires device and boot/recovery policy |
| ZFS root | out of scope | no | separate packaging, boot and licensing work |

The UI never accepts an executable name, shell fragment or raw formatter
options as a custom filesystem.

### Btrfs layout policies

Custom mode presents two explicit policies:

1. **AnduinOS-managed Btrfs** creates the canonical roles and remains
   Waypoint-compatible.
2. **Custom Btrfs** lets the user select or create subvolumes and assign mount
   points. The base installation is supported only if boot invariants pass;
   Waypoint is enabled only if the semantic-role validator approves the
   rollback boundaries.

The canonical release-one names remain the compatibility default. A future
role manifest records at least:

```text
layout schema version
filesystem UUID
role -> subvolume UUID and current path
mount point
rollback membership
CoW/workload policy
Waypoint compatibility result and reason
```

This permits custom names without making Waypoint guess. If a role or
boundary is missing, the UI says `Custom layout — Waypoint unsupported` before
installation.

Btrfs-specific options such as compression and nodatacow affect the whole
filesystem when supplied as mount options, even when individual subvolumes
are mounted separately. Workload-specific NOCOW is therefore applied as an
installer/package-owned directory policy before the first data file is
created, not represented as a fictional independent subvolume mount option.

Sharing an already populated Btrfs filesystem with another Linux installation
is deferred until namespaced subvolume roles, collision handling, shared
mount-option policy and Waypoint discovery are specified and tested.

## RAID and multi-device plan

### Capability classes

| Storage class | First supported behaviour |
|---|---|
| Hardware RAID logical disk | treat as one disk; controller owns the array |
| Existing mdraid | use a healthy, assembled array by MD UUID |
| New mdraid | deferred; curated RAID1 before a general builder |
| Existing LVM logical volume | use a healthy LV by VG/LV UUID in custom mode |
| New or thin-provisioned LVM | deferred |
| Existing Btrfs multi-device | use by FSID and complete device UUID set |
| New Btrfs multi-device | curated profiles after existing-array support |
| Firmware/fake RAID | deferred |
| Btrfs RAID56 | reject as a supported system root |

Existing arrays must be healthy, non-degraded and inactive outside installer
ownership. The plan records level, UUID, member identities and geometry.
`/dev/md127` or whichever path happens to appear in one boot is not an
identity.

An existing LVM target is similarly bound to its PV, VG and LV UUID graph,
not `/dev/mapper` display names alone. Initial support formats or mounts one
explicit LV; it does not resize a PV, VG or LV, create thin pools, or infer an
arbitrary mdraid/LUKS/LVM stack.

### Btrfs profiles

Data, metadata and system block-group profiles are separate decisions. The UI
must show copies, failure tolerance and estimated usable capacity instead of
presenting a single ambiguous “RAID level” field.

Candidate profiles, subject to the running kernel and btrfs-progs release,
are:

- `single` and `DUP` where appropriate;
- RAID0 with a prominent zero-redundancy warning;
- RAID1;
- RAID10;
- RAID1C3 and RAID1C4.

RAID56 remains rejected until upstream changes its stability classification
and AnduinOS completes a new destructive test campaign. Device add/remove,
profile conversion and repair are post-install administration features, not
part of the initial installer UI.

### Boot redundancy

Neither mdraid nor Btrfs RAID makes an ESP firmware-readable through the
array. A redundant root requires an explicit boot topology:

- one ESP on each independently bootable physical disk;
- an AnduinOS loader and verified signed chain on every ESP;
- one verified NVRAM entry per boot target where firmware permits it;
- kernel/initramfs/GRUB or future UKI updates synchronized across all ESPs;
- initramfs configuration containing every required mdraid/Btrfs member;
- a tested degraded-boot policy.

A RAID installation is not release-ready until the machine boots after each
individual member is removed in turn. RAID0 is tested for normal boot but
cannot promise device-loss boot.

### Encryption layering

The graph reserves a container layer for future LUKS2 support, but the first
custom and RAID milestones remain unencrypted. Supported encrypted layouts
must later define whether encryption is above or below an array, how every
member is unlocked, how recovery keys work and how initramfs discovers the
root. No arbitrary stacking enters through custom mode before that contract
exists.

## User interface plan

### Page 1: target disk

This page selects exactly one stable disk identity. It shows model, capacity
and read-only Windows/BitLocker/existing-partition badges, but no filesystem,
erase or coexistence controls. A rescan clears the target and every
topology-dependent choice. Partition and filesystem writes are confined to
this disk; the UI separately discloses that EFI NVRAM is machine-wide state.
Every non-live disk remains selectable here. Capacity and topology
eligibility belong to the later installation-method pages.

### Page 2: installation method

Three complete, goal-oriented choices are shown instead of presenting
coexistence as a filesystem:

- erase the selected disk with Btrfs (recommended);
- erase the selected disk with ext4 (classic);
- Advanced — preserve existing systems in already-unallocated space.

An empty disk may default to Btrfs. If any existing partition is detected,
nothing is preselected: the two erase choices explicitly say that every
partition will be deleted, while Advanced is the only preservation path.
Whole-disk choices are disabled below their capacity requirement without
preventing Advanced from explaining its own exact eligibility result.

### Page 3: conditional advanced coexistence

Only the Advanced choice opens this page. It shows Windows, Fast Startup,
hibernation, BitLocker/TPM, Secure Boot and EFI warnings, then selects one
exact free extent, Btrfs or ext4, and a reused or dedicated ESP policy.
Missing free space directs users to shrink in Windows and rescan; rescanning
returns to the target-disk page and requires a new explicit selection.

Future custom storage extends this conditional advanced branch rather than
adding raw topology controls to the ordinary target-disk page. Every selected
object will carry an action badge: Preserve, Create, Format, AnduinOS-owned
boot files or Firmware entry.

### Final confirmation

The confirmation identifies every physical disk and array member, then lists
the exact write set. Formatting actions require stronger destructive styling
than additive ESP writes. Preserved Windows, recovery and data partitions are
listed explicitly rather than omitted.

## Safety invariants

All future modes preserve the current planner/executor trust split and add:

1. Re-probe every graph identity and geometry before writes.
2. Reject mounted targets and unexpected active mappings.
3. Reject a changed partition-map or array-member digest.
4. Keep preservation and formatting mutually exclusive per object.
5. Verify every mounted source against the planned UUID and parent graph.
6. Never format a reused ESP.
7. Never write another vendor's ESP directory.
8. Never accept commands, hooks or raw mount/formatter arguments from a plan.
9. Generate `fstab`, crypttab, mdraid and initramfs configuration only from
   validated typed objects.
10. Verify the installed topology from a clean discovery environment before
    reporting success.

Partition changes are not transactionally rollbackable. Partition-table
backups are diagnostic/recovery material, not a promise that an interrupted
resize or move can be reversed. This is why guided coexistence initially adds
partitions only inside selected free space and never moves existing data.

## Development milestones

### Storage 0 — freeze contracts

- Keep the release-one erase-disk matrix green.
- Freeze canonical Btrfs role invariants and capability vocabulary.
- Approve the new storage graph and write-set schemas before adding UI.

### Storage 1A — read-only inventory and erase-disk parity

- Discover partitions, free extents, ESPs, filesystems and parent relations.
- Add stable partition/GPT identities and inventory digests.
- Implement typed operations and deterministic write-set rendering.
- Execute only the existing erase-disk plan through the new model.
- Prove command parity with the current implementation.

This milestone is complete. It has no new user-visible storage mode.

### Storage 1B — serializable graph and planner migration

- Freeze the versioned graph node, reference, operation and capability schema.
- Serialize only stable identities and geometry authorization; keep display
  paths non-authoritative.
- Convert the GTK erase-disk choice into the new graph without exposing new
  controls.
- Re-probe and validate the selected graph in the privileged executor.
- Preserve exact erase-disk command and confirmation behaviour.
- Add schema round-trip, unknown-field, stale-binding and privilege-boundary
  tests before advancing to coexistence.

This milestone is complete. It preserves the existing erase-disk UI and exact
destructive command policy; no coexistence or custom graph is accepted yet.

### Storage 2 — guided UEFI/GPT coexistence

- Select an existing free extent or disposable partition.
- Preserve Windows-shaped partitions and reuse a validated ESP without
  formatting it.
- Add coexistence-specific NVRAM boot policy without a shared fallback write.
- Support Btrfs and ext4 roots.
- Pass preservation, failure-injection and power-cut testing before enabling
  an installable production UI mode.

Storage 2A is complete. Its read-only eligibility analyzer never mounts,
repairs, moves or shrinks a filesystem. It recognizes Windows-shaped GPT
partitions, BitLocker, preliminary ESP candidates, exact free extents,
explicitly disposable whole partitions, mounted volumes and unsupported
nested mappings. A disk is eligible only when an already-unallocated extent
meets the root/swap/ESP budget and every UEFI/GPT/topology gate passes.

When space is missing, the structured UI notices direct Windows users to
create unallocated space with Windows Disk Management, reboot the installer
and rescan. They also explain the separate whole-partition erase option and
state that it is never preselected. There is no force-continue result. These
notices are visible in the default beta coexistence flow.

Storage 2B is complete. Graph schema v2 requires an explicit `preserve`
operation for every existing partition, a stable reference to exactly one
selected free extent, and non-overlapping new partition declarations bounded
by that extent. Reused ESPs are never formatted; coexistence graphs reject
partition-table replacement, BIOS installation and shared fallback writes.
The privileged validator reconstructs the graph from a fresh inventory and
rejects topology, extent, ESP or declaration drift.

Storage 2C is complete. The privileged compiler produces one frozen typed
write set, free-space-only partition/format plan and vendor-only UEFI/NVRAM
plan from the reconstructed graph. Shared ESP reuse additionally requires a
healthy read-only FAT check, matching identities and 64 MiB free. It never
formats a reused ESP or writes the shared fallback path. Those invariants are
also enforced by the now-enabled beta execution path.

Storage 2D is complete. The coexistence controls are visible by default in
the beta, but live behind the Advanced installation-method choice, and support
exact unallocated extents only. They render analyzer notices,
including shrink-in-Windows, rescan and no-force-continue guidance; rescanning
requires a fresh disk selection. Its final confirmation is derived from the
typed write set rather than independently assembled storage promises.

The frontend now submits password-protected guided plans. It clears plaintext
credentials, re-probes the complete selection and rebuilds the immutable graph
before invoking the public executor. The development environment mode still
simulates the same plan without starting any privileged process.

Storage 2E is in progress. The normal beta policy connects password-protected
guided plans to the real storage and vendor-only boot steps. A separate
executor-owned policy for passwordless destructive VM testing requires two
internal opt-ins that the public executor wrapper cannot forward. Runtime
proofs freeze and later verify every preserved
partition and every non-AnduinOS ESP entry; the post-write check also requires
the exact set, geometry, filesystem type and fresh identities of all declared
new partitions. Stable markers cover partition, formatter, boot-file and
NVRAM boundaries, and guided-only step markers cover the complete fixed
pipeline. Every partition/formatter boundary has unit-level failure-injection
coverage.

The isolated qcow2 campaign runner and eight-row coexistence matrix are now
defined. The test-only plan generator and strict evidence CLI bind all work to
a root QEMU/KVM guest and exact `/dev/vda` target. Full raw preserved-partition
hashes, shared-ESP hashes and existing NVRAM state persist on a fresh campaign
evidence qcow2. The runner can kill only on an exact serial marker and resume
only a matching recorded power-cut campaign. All four immutable inputs are
hash-pinned; a strict host verifier detects retained input or artifact drift
without treating QEMU exit status as a pass.

This milestone is not complete: real Windows fixture runs, independent
Windows/AnduinOS boot, NVRAM/boot-order comparison, hard-power-cut cases and
safe handling of partially-created guided targets still have to pass before a
stable release can claim coexistence qualification.

### Storage 3 — custom partition and mount mapping

- Add format/preserve decisions and supported mount roles.
- Add filesystem-driver capability validation.
- Add custom Btrfs subvolume selection/creation and the semantic role
  manifest.
- Expose clear Waypoint compatibility reasons.
- Keep resizing, moving and repair external.

### Storage 4 — consume existing containers and arrays

- Discover hardware-controller disks, LVM, mdraid and Btrfs multi-device
  filesystems.
- Bind plans to array/filesystem UUIDs and exact member sets.
- Generate required initramfs configuration.
- Add multi-ESP boot targets and member-loss boot tests.

### Storage 5 — curated array creation

- Add a two-disk redundant-system preset first.
- Add explicitly approved Btrfs RAID1/10/1C3/1C4 profiles as hardware and VM
  matrices become available.
- Keep RAID0 expert-only and clearly non-redundant.
- Continue rejecting RAID56.

### Storage 6 — encryption

- Add LUKS2 passphrase and recovery-key support on approved single-disk
  layouts.
- Extend to TPM2/FIDO2 convenience unlock only after offline recovery is
  proven.
- Specify and test RAID/encryption ordering as separate milestones.

## Release gates and destructive tests

Unit tests cover parsing, graph validation, command planning and failure
cleanup, but never authorize release by themselves.

Coexistence tests use a Windows-shaped GPT image with sentinel content in the
ESP, NTFS and recovery partitions. Before and after installation they compare:

- the complete partition map;
- preserved partition boundaries and identifiers;
- hashes of pre-existing ESP files;
- hashes or immutable test fixtures in every preserved partition;
- UEFI entries and boot order;
- independent boot of AnduinOS and the preserved system.

Failure and hard-power-cut injection surrounds every new destructive boundary:
partition creation, formatting, ESP writes, NVRAM writes, array assembly,
subvolume creation, copy, configuration and bootloader installation.

RAID tests additionally:

- boot with all members;
- remove each redundant member in turn and boot;
- reject unexpected or substituted members;
- verify every ESP's signed chain;
- exercise interrupted array discovery and initramfs assembly;
- confirm RAID0 is never described as redundant.

No test attaches a host block device. All destructive tests use newly created
VM images and retain topology manifests, logs, screenshots and image hashes.

## Deliberately deferred work

- automatic NTFS shrinking or moving;
- Legacy BIOS/MBR coexistence;
- arbitrary filesystem plugins or raw command entry;
- ZFS root;
- firmware/fake RAID management;
- Btrfs RAID56;
- automatic repair of inconsistent user filesystems;
- arbitrary RAID/LUKS/LVM nesting;
- hibernation until resume storage is fully configured and verified.

## Upstream references

- [Btrfs feature and RAID status](https://btrfs.readthedocs.io/en/latest/Status.html)
- [mkfs.btrfs multi-device profiles](https://btrfs.readthedocs.io/en/latest/mkfs.btrfs.html)
- [Btrfs subvolume mount-option scope](https://btrfs.readthedocs.io/en/latest/btrfs-subvolume.html)
- [Linux MD boot-time assembly](https://www.kernel.org/doc/html/latest/admin-guide/md.html)
- [GNU GRUB installation manual](https://www.gnu.org/software/grub/manual/grub/html_node/Installing-GRUB-using-grub_002dinstall.html)
