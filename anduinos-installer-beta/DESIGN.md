# AnduinOS Installer Beta architecture

The installer is split across a non-privileged planner and a privileged,
fixed executor. The UI may describe desired state, but it may not supply
commands, step policies, arbitrary hooks or unvalidated command arguments.
Release one has no user-supplied mount paths. Future custom storage may carry
declarative mount paths only after the executor normalizes and validates them
and constructs every command itself.

## Release-one contract

- Architectures: amd64 and arm64.
- Firmware: amd64 UEFI and Legacy BIOS; arm64 standards-based UEFI/ACPI.
- Secure Boot: detected and preserved on UEFI systems. When enabled, the
  installer creates/imports the AnduinOS MOK using the existing one-time
  enrollment password policy (`123456`). The password is an implementation
  secret and is never serialized into an install plan.
- Storage mode: erase one complete disk. Guided coexistence, custom layouts
  and RAID are post-release-one work defined in
  [`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).
- Filesystems: Btrfs by default, ext4 as an alternative.
- Swap: a fixed 4 GiB disk swap partition plus the installed system's existing
  50%-of-RAM LZ4 zram policy. zram has the higher priority.
- Live system: Casper remains the image/boot transport for release one. Its
  live-session state must not leak into the installed target.
- Software: refreshing package indexes and installing available updates is
  enabled by default. An offline index-refresh failure is a warning and skips
  the upgrade; after an upgrade transaction starts, any APT/dpkg failure is
  fatal. Recommended third-party drivers are an explicit opt-in and use
  `ubuntu-drivers install --no-oem`.
- Mirrors: before refreshing APT, a warning-policy step concurrently probes a
  maintained HTTP+HTTPS Ubuntu mirror list, bandwidth-tests the five lowest
  latency candidates, and atomically replaces only `URIs:` fields in the
  target's Ubuntu Deb822 source. Current-architecture package indexes are used
  first, with OOBE's `Contents-amd64.gz` probe as fallback. A failed update
  restores the exact original source bytes and mode, retries once, and never
  weakens APT signature or `Valid-Until` verification.
- Accounts: password authentication requires matching password entries. A
  separate visible control chooses whether sudo requires that password.
  Explicit passwordless shared-computer mode configures GDM automatic login
  and necessarily locks that sudo control on. Every enabled passwordless-sudo
  policy uses a mode-0440, `visudo`-validated `NOPASSWD` rule. The UI warns
  that anyone or any program with session access can obtain root; root itself
  remains locked.
- Regional defaults: every officially supported language/region has an
  installer-owned representative timezone. The timezone page preselects it
  (for example US English → New York) while retaining the complete searchable
  system timezone list and allowing the user to override the guess.

## Safety boundary

The GTK process always runs as the desktop user. Ordinary `lsblk` discovery
stays unprivileged. Exact free-space geometry crosses Polkit through
`anduinos-installer-storage-probe`, a read-only helper that accepts exactly one
validated fixed whole-disk path and can execute only `parted ... print free`.
The policy never authorizes `parted` itself, so the UI cannot turn this probe
into a partition-table write. Destructive work remains isolated in the
separate plan-only executor.

Before the first destructive command, the executor:

1. Parses and validates the versioned `InstallPlan`.
2. Re-probes architecture, firmware and Secure Boot.
3. Resolves the selected whole disk and compares stable ID and byte size.
4. Locates and verifies the source image.
5. Runs every step's preflight check.

The executor owns the ordered step list and each step's failure policy. Plans
cannot mark failures optional. Partitioning, formatting, filesystem copying,
fstab, user creation, swap, bootloader and final verification are fatal.
Cosmetic live-session cleanup may be best-effort.

## Deterministic erase-disk layouts

amd64 uses GPT with a 2 MiB BIOS boot partition, 1 GiB EFI System Partition,
4 GiB swap, and the remaining space as root. This supports either UEFI or
Legacy BIOS without repartitioning.

arm64 uses GPT with a 1 GiB EFI System Partition, 4 GiB swap, and the remaining
space as root.

The partition boundary is stable. The legacy `backend.py` single-`@`
implementation is obsolete and must not be shipped; the new executor uses the
multi-subvolume ABI. The release subvolume, rollback, CoW, hibernation and
future encryption contract is defined in
[`BTRFS-DESIGN.md`](BTRFS-DESIGN.md).

The deterministic layout is not intended to be the only long-term product
mode. It is the first proven execution path and remains isolated while the
planner evolves toward a typed storage graph. That graph, Windows coexistence,
custom filesystem/subvolume policy, multi-ESP boot and RAID milestones are
defined in [`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).

AnduinOS ISO builds now ship this beta as the default installer so the
destructive VM matrix can run against the real image. Ubiquity,
`anduinos-installer-config`, and `anduinos-bwrap-hack` remain built and
published but are not installed automatically. During the beta period a user
may explicitly install `anduinos-installer-config` to obtain the complete
legacy fallback stack.

The package owns both its application-menu entry and a GNOME autostart helper.
The helper creates a trusted desktop launcher only in a non-root Casper
session, inside that Live user's runtime home. It never writes to `/etc/skel`,
so no dead installer shortcut can enter the installed user's home. The
launcher package carries its own independent copy of the OOBE box-and-logo
artwork and does not depend on the OOBE package.

## Interface architecture

The GTK interface uses a five-chapter visual model. Several guarded pages may
belong to one chapter, so the chapter indicator communicates progress without
pretending that every storage branch has the same number of screens.

| Chapter | Pages |
| --- | --- |
| Preparation | Language, keyboard and software choices |
| Storage | Target disk, installation method and conditional advanced storage |
| Account | User account and timezone |
| Review | Immutable plan summary and destructive confirmation |
| Install | Execution dashboard and completion state |

Every regular page has a package-owned SVG hero, a constrained content area
and a persistent bottom navigation bar. The default 960 x 680 window must fit
inside a 1024 x 768 live session without hiding navigation. Long or conditional
content scrolls inside the middle region; the hero and navigation do not.

The chapter dots are indicators, not arbitrary navigation controls. Forward
movement continues to use the existing page-specific validation callbacks, and
the navigation view remains the sole owner of the back stack. This prevents a
carousel gesture or a dot click from bypassing disk selection, coexistence
preflight, account validation or final confirmation.

Visual assets are copied into `assets/icons` and shipped by this package. The
runtime never depends on a sibling OOBE/Timeback checkout or a developer's icon
theme source tree. Shared colors, cards, callouts, dots and progress states live
in `assets/style.css`; reusable GTK construction lives in `src/ui.py`. New pages
should extend those two layers rather than defining a page-local visual system.

Storage selection represents each physical disk as a complete selectable card:
model, stable display path, capacity, partition table, current partitions and
known unallocated extents remain visible together. Installation methods use
grouped whole-card toggles with equal icon canvases. Neither workflow relies on
the toolkit's rectangular default list selection, which would escape the
rounded visual boundary and obscure whether the card itself is active.

## Implementation milestones

- Milestone 1 — complete: plan schema, validation, hardware discovery,
  deterministic layouts, command generation and step state machine.
- Milestone 2 — complete: privileged command boundary, hardware revalidation,
  partition/format/mount/copy/unmount lifecycle, persistent fstab, 4 GiB disk
  swap and explicit zram defaults.
- Milestone 3A — complete: target user, encrypted password input, sudo
  membership, root locking, hostname, locale, timezone, keyboard, Rime and
  fresh machine identity.
- Milestone 3B — complete: isolated target `/run`, controlled virtual
  filesystems, temporary DNS, service-start suppression, reversible cleanup
  and manifest-driven removal of live-session packages.
- Milestone 3C — implementation complete: amd64 BIOS+UEFI and arm64 UEFI
  bootloader installation, initramfs generation, fallback EFI loaders and
  architecture-aware artifact verification. Destructive boot testing remains
  part of the VM matrix milestone.
- Milestone 4 — implementation complete: signed shim/GRUB, machine-local MOK
  generation, explicit DKMS signing, idempotent enrollment scheduling and
  signed-chain verification. See
  [`SECURE-BOOT-DESIGN.md`](SECURE-BOOT-DESIGN.md).
- Milestone 5A — implementation complete: GTK state is converted once into an
  immutable, versioned plan; plaintext passwords are erased after hashing; the
  destructive summary and final disk confirmation expose the exact platform,
  disk identity, filesystem, swap and Secure Boot intent; a root-only helper
  streams executor progress while shutdown, sleep and window-close paths are
  inhibited. The obsolete prototype backend is no longer shipped.
- Milestone 5B — test infrastructure complete, execution pending: the
  ten-row release matrix, qcow2-only QEMU runner, exhaustive step failure
  injection tests and pass/fail protocol are defined in
  [`VM-TESTING.md`](VM-TESTING.md). No matrix row may be marked passed until
  it has run from a real AnduinOS ISO and booted the installed virtual disk.
- Milestone 5C — implementation complete: the ISO build installs
  `anduinos-installer-beta`, excludes it from the installed target manifest,
  and rejects accidental inclusion of the retired Ubiquity/bwrap stack. Casper
  remains the live boot transport.
- Milestone 6A — complete: schema v2 carries immutable update and third-party
  driver choices; GTK defaults to updates on and non-free drivers off; summary
  and development simulation expose the resulting fixed pipeline.
- Milestone 6B — complete: the isolated target refreshes APT indexes, tolerates
  an offline refresh, applies upgrades only after a successful refresh, and
  treats an interrupted/invalid upgrade transaction as fatal.
- Milestone 6C — complete: opt-in recommended drivers use Ubuntu's supported
  discovery/install frontend without OEM archives. Secure Boot preparation
  precedes driver installation; DKMS then rebuilds once, and every resulting
  DKMS module must match the new machine-local MOK before boot artifacts and
  MOK enrollment are finalized.
- Milestone 6D — implementation complete: MOK enrollment is visible in both
  the destructive summary and non-destructive development simulation, with
  the documented one-time password `123456`. Unit, lint, package-build and
  installed-GUI checks form the local gate; destructive VM rows remain
  mandatory before release.
- Milestone 7A — complete: the executor emits explicit running, succeeded,
  warning, failed and skipped events for every applicable Step. The GTK4
  execution dashboard renders the exact backend pipeline as an accessible
  five-state light board beside live output, with a fixed overall progress
  area. Unselected optional update/driver steps are visibly skipped rather
  than falsely reported as successful.
- Milestone 7B — complete: the historical seven-page AnduinOS presentation,
  including all 28 supported localizations and six screenshots, is copied as
  installer-owned data and rendered by native GTK4. No WebKit, JavaScript,
  Ubiquity or installer-config dependency is introduced. The dashboard opens
  on an automatically advancing presentation with manual navigation and can
  switch instantly to the live Output view.
- Milestone 7C — complete: warning events accumulate on the Output switcher
  without interrupting the presentation; fatal errors reveal and focus the
  live log with an error banner; successful completion stops the carousel and
  opens a dedicated completion/MOK/reboot card. Output can be copied or saved
  to the live user's home directory, while the presentation and log remain
  available after completion.
- Milestone 8A — complete: read-only storage inventory records stable disk and
  partition identities, exact allocated/free geometry, filesystems, ESPs and
  topology digests. The existing erase-disk executor freezes a typed write set
  beside its command plan during preflight and fails closed if they drift.
  `InstallPlan` v4, GTK choices and destructive commands remain unchanged.
- Milestone 8B — complete: `InstallPlan` v5 carries strict storage graph schema
  v1. The graph contains no commands or authoritative device paths; privileged
  preflight re-probes its stable disk/topology binding, resolves the current
  path and rejects unknown fields, stale topology, non-canonical declarations
  and graph/write-set drift. The erase-disk UI and destructive command policy
  remain unchanged.
- Milestone 8C — complete: the read-only coexistence analyzer classifies
  Windows-shaped GPT layouts, BitLocker, preliminary ESP candidates, exact
  free extents, disposable whole partitions, mounts and unsupported nested
  mappings. Missing space produces explicit shrink-in-Windows, rescan and
  no-force-continue notices; no coexistence control is exposed yet.
- Milestone 8D — complete: `InstallPlan` v6 and storage graph schema v2 model
  every preserved partition, one topology-bound free extent, bounded new
  partitions, reused/new ESP policy and NVRAM intent. Guided graphs reject
  whole-disk replacement, BIOS and shared fallback writes, and privileged
  reconstruction rejects stale topology. Execution remains disabled.
- Milestone 8E — complete: the privileged coexistence compiler produces a
  graph-identical typed write set and bounded free-space-only commands. Shared
  ESP reuse requires a read-only FAT check, matching identities, 64 MiB free,
  vendor-only boot files and an exact verified NVRAM entry. Command or
  declaration drift fails closed; execution and GTK remain disabled.
- Milestone 8F — complete: the coexistence GTK workflow selects an exact free
  extent and ESP policy, surfaces shrink-in-Windows/rescan/no-force guidance,
  and renders its final confirmation from the typed write set. The beta has no
  command-line feature flag: a target-only disk page leads to explicit Btrfs
  erase, ext4 erase or Advanced-preservation choices, and only Advanced opens
  the coexistence controls. Existing partitions suppress automatic strategy
  selection, and target/topology changes invalidate all dependent choices.
- Milestone 8G — in progress: an executor-owned destructive-test policy now
  remains available for passwordless disposable-VM and power-cut campaigns,
  while password-protected guided plans use the normal beta public helper.
  Runtime checks freeze and verify all
  existing partition identities/boundaries and every shared-ESP entry outside
  `EFI/AnduinOS`; new partition results and the exact NVRAM entry are verified
  after writes. A test-only plan generator, strict full-partition/ESP/NVRAM
  evidence manifest, stable destructive-boundary markers and a persistent
  evidence qcow2 support the eight-row campaign. The ISO, Windows disk, OVMF
  CODE and Windows-paired VARS are SHA-256 pinned, every fixed executor step
  has a guided-only power-cut marker and retained artifact hashes are strictly
  verifiable without inferring a pass from QEMU status. Real Windows
  preservation, independent boot, hard-power-cut and partial-target recovery
  runs remain mandatory. See
  [`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).
- Final release gate: complete the VM matrix and only then remove Ubiquity and
  `anduinos-installer-config`.

Disk encryption, TPM2 unlocking and FIDO2 unlocking are explicitly outside
the release-one scope. Release one supports unencrypted ext4 and unencrypted
Btrfs only.

## Post-release-one storage direction

Storage development proceeds in independently gated milestones:

1. refactor discovery and planning into an immutable storage graph while
   preserving erase-disk command parity;
2. add UEFI+GPT guided coexistence using only selected free space or an
   explicit disposable partition;
3. add custom partition, filesystem, mount and Btrfs subvolume mapping;
4. consume healthy LVM volumes and arrays prepared by expert users;
5. add curated redundant-array creation;
6. add LUKS2 and hardware-assisted unlock as separate recovery-driven work.

No mode is exposed merely because its UI exists. Each mode requires its
executor, preservation checks, power-cut campaign and boot matrix to pass.
The complete plan and invariants live in
[`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).
