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
- Secure Boot: detected and preserved on UEFI systems. Enabled, disabled and
  explicitly unsupported firmware states are distinct plan values; missing,
  malformed or contradictory probe output remains fatal. When enabled, the
  installer creates/imports the AnduinOS MOK using the existing one-time
  enrollment password policy (`123456`). The password is an implementation
  secret and is never serialized into an install plan.
- Boot package ownership: `anduinos-core-system`, which remains installed on
  the target, owns the architecture-matched GRUB platform modules, signed UEFI
  payload and shim. On amd64 it deliberately retains both `i386-pc` and
  `x86_64-efi` modules for the erase-installation dual-firmware contract. The
  removable Live installer may use these tools but is never their sole owner,
  so orphan cleanup cannot remove boot maintenance from the installed system.
- Storage mode: erase one complete disk. Guided coexistence, custom layouts
  and RAID are post-release-one work defined in
  [`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).
- Filesystems: Btrfs by default and ext4 as the classic alternative in
  automatic and guided modes. Advanced manual mode additionally offers XFS
  and F2FS as conventional single-root filesystems.
- Machine identity: the account page accepts one RFC-style ASCII hostname
  label, including upper-case input, digits and internal hyphens. The planner
  converts it to a lower-case systemd static hostname before constructing the
  immutable plan. Plans are canonical: the privileged executor rejects
  upper-case, non-ASCII, dotted, underscored, edge-hyphen or over-63-byte
  hostnames instead of normalizing untrusted input.
- Secure Shell: the desktop metapackage only suggests `openssh-server`, so an
  existing desktop upgrade never installs or starts a new network listener.
  The Live-only `anduinos-live-settings` composition carries the server for new
  installations, and the installer retains it when removing Live packages.
  The ISO builder disables both SSH units and scrubs every build-time SSH host
  key as machine identity. After copying the Live system, a fatal installer
  step removes any Live-session keys again, generates target-owned keys,
  validates `sshd`, and explicitly disables both units on the newly created
  target after all package transactions. No global systemd preset is shipped,
  so `preset-all` cannot disconnect an existing administrator. When UFW is
  installed, its OpenSSH application rule is prepared without starting a
  listener, so a later explicit GNOME opt-in works with the firewall enabled.
  During offline validation, the installer creates `/run/sshd` inside the
  target's isolated tmpfs because the normal boot-time tmpfiles pass has not
  happened yet. The directory is discarded with the chroot environment. The
  installer never permits empty SSH passwords.
- Account access policy: release installations always create a
  password-protected account. A separate Advanced Options page exposes three
  independent, default-off choices: passwordless sudo, GDM automatic login,
  and enabling `ssh.socket` with account-password authentication. Automatic
  login follows Ubiquity's GDM model and retains the account password. SSH
  opt-in also enforces `PermitEmptyPasswords no` and `PermitRootLogin no`.
  Passwordless sudo is written atomically to
  `/etc/sudoers.d/90-anduinos-passwordless-admin`, validated against the full
  sudoers configuration before and after mutation, and published through the
  root-owned, world-readable
  `/var/lib/anduinos-passwordless-sudo/users` state snapshot. The YubiKey
  Security Center consumes and maintains the same policy contract.
- Swap: a dynamically sized whole-GiB disk swap partition plus the installed
  system's existing 50%-of-RAM LZ4 zram policy. Disk swap is never below 2 GiB
  and never reduces the planned root filesystem below 20 GiB. When space
  permits, the disk partition is rounded-up RAM plus 1 GiB so the layout has
  hibernation capacity; otherwise it falls back to rounded-up RAM/2, capped at
  64 GiB. zram has the higher runtime priority. Partition capacity alone does
  not enable hibernation; resume configuration and platform support remain
  separate requirements.
- Live system: Dracut `dmsquash-live` mounts the single
  `/LiveOS/rootfs.squashfs`, composes the temporary or persistent overlay, and
  `anduinos-live-layers` preserves `/cdrom` plus the installer source under
  `/run/anduinos-live`. `anduinos-live-settings` is a hard dependency of the
  installer and applies the GRUB-selected locale/timezone, creates the Live
  user, and configures automatic login through a systemd oneshot. A dedicated
  installer step purges the fixed Live-only package set from the copied target.
  It retains Disk Snapshots Manager on Btrfs but purges it on every classic
  root filesystem, retains the selected XFS or F2FS userspace tools when
  required, removes VMware guest integration from non-VMware targets, and
  purges orphaned packages. This policy does not use Ubiquity's historical
  dual manifest convention.
- Software: refreshing package indexes and installing available updates is
  enabled by default. An offline index-refresh failure is a warning and skips
  the upgrade; after an upgrade transaction starts, any APT/dpkg failure is
  fatal. Recommended third-party drivers are an explicit opt-in and use
  `ubuntu-drivers install --no-oem`. Celluloid, yt-dlp, FFmpeg, libmpv and the
  GStreamer base/good sets remain in the default system for everyday playback.
  Wider GStreamer, legacy and specialist format support is a separate,
  default-off online choice owned by the `anduinos-multimedia-codecs`
  metapackage. A clean download failure is a visible warning; an inconsistent
  APT/dpkg state remains fatal.
- Wi-Fi: the active Live-session Wi-Fi UUID selects its exact persistent
  `/etc/netplan/90-NM-<UUID>.yaml`, which is migrated after the target image is
  copied. Historical connections, VPNs, hotspots and unsafe files are excluded.
  The executor never overwrites a target Netplan and publishes a new one
  atomically as root with mode 0600, then validates its NetworkManager mapping
  without applying it. Migration failure is a visible warning, not an
  installation failure. The unprivileged GTK frontend is also a native
  libnm client: it scans and refreshes access points, connects open, OWE, WEP,
  WPA Personal and common 802.1X networks, handles hidden SSIDs and WPS-PBC,
  and disconnects without launching a desktop settings application. Secrets
  remain in the installer process and libnm D-Bus payload; they are never
  placed in a child-process argument list.
- Mirrors: before refreshing APT, a warning-policy step concurrently probes a
  maintained HTTP+HTTPS Ubuntu mirror list, bandwidth-tests the five lowest
  latency candidates, and atomically replaces only `URIs:` fields in the
  target's Ubuntu Deb822 source. Current-architecture package indexes are used
  first, with OOBE's `Contents-amd64.gz` probe as fallback. Offline installation
  explicitly skips this step and preserves the source embedded in the image;
  an online probe failure is a warning and preserves that source. A failed update
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
- Regional input policy: `data/languages.json` is the single validated source
  for supported languages, locale aliases, the default language, physical XKB
  layouts, Ubuntu language-pack codes and ordered optional input-method
  recommendations. Each input method declares its label, packages, required
  files and optional desktop source. The first recommendation is checked by
  default; the UI lets the user select any number of maintained methods,
  including none. While offline, every input-method choice is unchecked and
  disabled.
  The UI, planner, validator and privileged executor resolve the same policy;
  none contains a language-, region-, framework- or product-specific branch.
  Physical keyboard configuration remains an independent offline step. The
  keyboard test field feeds GTK raw hardware keycodes into a private
  `libxkbcommon` keymap for the selected layout. It previews changes
  immediately on Wayland and X11 without modifying the Live Session's GNOME
  input sources; Shift, Caps Lock, AltGr, auto-repeat and Compose/dead-key
  sequences remain inside that isolated state machine. The
  language-support step reuses installed packs or installs the exact base and
  GNOME packs derived from the selected JSON code; missing network or package
  failure produces a visible, non-fatal warning. A selected input method can
  likewise reuse a complete payload on the medium or download only its
  declared packages. Desktop defaults are generated from the selected policy.
  Input-method packages own their system defaults; the installer never writes
  input-method files below `/etc/skel`. It has no dependency on Ubuntu Language
  Selector metadata and never modifies its `pkg_depends` database.

## Safety boundary

The GTK process always runs as the desktop user. Ordinary `lsblk` discovery
stays unprivileged. Exact free-space geometry crosses Polkit through
`anduinos-installer-storage-probe`, a read-only helper that accepts exactly one
validated fixed whole-disk path and can execute only `parted ... print free`.
The shared inventory probe forces the C locale for both `lsblk` and `parted`,
so translated machine-output flags cannot change the topology authorization
digest between the desktop process and the root executor.
The policy never authorizes `parted` itself, so the UI cannot turn this probe
into a partition-table write. Destructive work remains isolated in the
separate plan-only executor.
The root executor creates a private mount namespace and recursively disables
mount propagation before it reads or executes a plan. Target and chroot mounts
therefore cannot leak into long-running Live-session services. A target mount
left in another process namespace by an older installer run is detected before
the first disk write and requires a Live-session restart; the installer does
not force-unmount unrelated process namespaces.

Before the first destructive command, the executor:

1. Parses and validates the versioned `InstallPlan`.
2. Re-probes architecture, firmware and Secure Boot.
3. Resolves the selected whole disk and compares stable ID and byte size.
4. Re-reads physical RAM and rejects a stale or forged dynamic swap size.
5. Locates and verifies the source image.
6. Runs every step's preflight check.

The executor owns the ordered step list and each step's failure policy. Plans
cannot mark failures optional. Partitioning, formatting, filesystem copying,
fstab, user creation, swap, bootloader and final verification are fatal.
Cosmetic live-session cleanup may be best-effort.

## Deterministic erase-disk layouts

amd64 uses GPT with a 2 MiB BIOS boot partition, 1 GiB EFI System Partition,
a policy-sized swap partition, and the remaining space as root. This supports
either UEFI or Legacy BIOS without repartitioning.

arm64 uses GPT with a 1 GiB EFI System Partition, the same policy-sized swap,
and the remaining space as root.

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
destructive VM matrix can run against the real image. The legacy Ubiquity
integration package has been retired and is no longer built or published.
`anduinos-bwrap-hack` remains available independently but is not installed
automatically.

The package owns both its application-menu entry and a GNOME autostart helper.
The helper creates a trusted desktop launcher only in a non-root Dracut Live
session, inside that Live user's runtime home. It never writes to `/etc/skel`,
so no dead installer shortcut can enter the installed user's home. The
launcher package carries its own independent copy of the OOBE box-and-logo
artwork and does not depend on the OOBE package.

## Interface architecture

The GTK interface uses an exact planned-page progress model. Every dot in the
persistent navigation bar corresponds one-to-one with a page that the current
installation route will actually show. Power, Secure Boot and network probes
freeze their conditional pages before the welcome footer is drawn. The storage
route is recomputed as soon as the user selects erase or coexistence, which is
the earliest point at which that branch can be known; every already-created
footer is updated from the same controller.

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

The page dots are indicators, not arbitrary navigation controls. Forward
movement continues to use the existing page-specific validation callbacks, and
the navigation view remains the sole owner of the back stack. This prevents a
carousel gesture or a dot click from bypassing disk selection, coexistence
preflight, account validation or final confirmation.

Visual assets are copied into `assets/icons` and shipped by this package. The
runtime never depends on a sibling OOBE/Disk Snapshots Manager checkout or a developer's icon
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
  partition/format/mount/copy/unmount lifecycle, persistent fstab, dynamic disk
  swap and explicit zram defaults.
- Milestone 3A — complete: target user, encrypted password input, sudo
  membership, root locking, hostname, locale, timezone, independent offline
  keyboard configuration, optional online language-support and input-method
  installation, and fresh machine identity.
- Milestone 3B — complete: isolated target `/run`, controlled virtual
  filesystems, temporary DNS, service-start suppression, reversible cleanup
  and manifest-driven removal of live-session packages.
- Milestone 3C — implementation complete: amd64 BIOS+UEFI and arm64 UEFI
  bootloader installation, initramfs generation, fallback EFI loaders and
  architecture-aware artifact verification. On UEFI only, a final independent
  step mounts eligible ESPs on non-target disks read-only, validates canonical
  Windows Boot Manager binaries, and writes UUID-bound chainloader entries
  only to the installed target's GRUB configuration. A detected Windows entry
  makes the GRUB menu visible while leaving the first AnduinOS entry as the
  default. `os-prober`, foreign Windows volumes, foreign ESP writes and
  firmware changes are not involved.
  Destructive boot testing remains part of the VM matrix milestone.
- Milestone 4 — implementation complete: signed shim/GRUB, machine-local MOK
  generation, explicit DKMS signing, idempotent enrollment scheduling and
  signed-chain verification. See
  [`SECURE-BOOT-DESIGN.md`](SECURE-BOOT-DESIGN.md).
- Milestone 5A — implementation complete: GTK state is converted once into an
  immutable, versioned plan before the destructive confirmation is displayed;
  plaintext passwords are erased when that validated plan is accepted and are
  never serialized. The destructive summary and final disk confirmation
  expose the exact platform, disk identity, planned partition layout,
  filesystem, swap and Secure Boot intent. Dynamic swap includes a compact
  expandable formula showing the current RAM, disk budget, hibernation target,
  fallback target and chosen size; a root-only helper
  streams executor progress while shutdown, sleep and window-close paths are
  inhibited. The obsolete prototype backend is no longer shipped.
- Milestone 5B — test infrastructure complete, execution pending: the
  ten-row release matrix, qcow2-only QEMU runner, exhaustive step failure
  injection tests and pass/fail protocol are defined in
  [`VM-TESTING.md`](VM-TESTING.md). No matrix row may be marked passed until
  it has run from a real AnduinOS ISO and booted the installed virtual disk.
- Milestone 5C — implementation complete: the ISO build installs
  `anduinos-installer-beta`, excludes it from the installed target manifest,
  rejects accidental inclusion of the retired Ubiquity/bwrap stack, and boots
  the Live root through the dedicated Dracut image.
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
- Milestone 8H — implementation complete: the bounded manual GPT editor can
  preserve, delete, create and format explicitly declared partitions and can
  shrink plain NTFS without moving its start. The unprivileged UI obtains a
  read-only `ntfs-3g.probe`/`ntfsresize` assessment through its fixed Polkit
  helper. BitLocker, mounted, hibernated/Fast-Startup, dirty or inconsistent
  volumes are hard failures. The executor repeats the complete target-specific
  dry run immediately before writing, never passes a force option, shrinks the
  filesystem before its GPT tail, then re-probes and verifies the unchanged
  PARTUUID, number and start plus the exact declared size before creating any
  new partition. ext4, Btrfs and every other filesystem remain non-resizable.
  The same bounded manual editor offers Btrfs, ext4, XFS and F2FS for a newly
  formatted root. Btrfs alone creates the canonical AnduinOS subvolumes and
  enables Disk Snapshots Manager; ext4, XFS and F2FS use one conventional root
  mount and direct system copy. XFS and F2FS are intentionally not exposed in
  automatic or guided layouts until their broader release matrices exist.
  Unit and GTK development-mode interaction gates pass; real Windows and
  interrupted-resize VM qualification remains mandatory before release.
- Final release gate: complete the VM matrix before promoting and renaming the
  native installer package.

Disk encryption, TPM2 unlocking and FIDO2 unlocking are explicitly outside
the release-one scope. Automatic and guided release-one layouts support
unencrypted ext4 and unencrypted Btrfs only; the bounded Advanced manual mode
also supports unencrypted XFS and F2FS roots.

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
