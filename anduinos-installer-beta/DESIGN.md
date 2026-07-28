# AnduinOS Installer Beta architecture

The installer is split across a non-privileged planner and a privileged,
fixed executor. The UI may describe desired state, but it may not supply
commands, step policies, mount paths, or arbitrary hooks.

## Release-one contract

- Architectures: amd64 and arm64.
- Firmware: amd64 UEFI and Legacy BIOS; arm64 standards-based UEFI/ACPI.
- Secure Boot: detected and preserved on UEFI systems. When enabled, the
  installer creates/imports the AnduinOS MOK using the existing one-time
  enrollment password policy (`123456`). The password is an implementation
  secret and is never serialized into an install plan.
- Storage mode: erase one complete disk. Manual partitioning is reserved for
  phase two.
- Filesystems: Btrfs by default, ext4 as an alternative.
- Swap: a fixed 4 GiB disk swap partition plus the installed system's existing
  50%-of-RAM LZ4 zram policy. zram has the higher priority.
- Live system: Casper remains the image/boot transport for release one. Its
  live-session state must not leak into the installed target.

## Safety boundary

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

AnduinOS ISO builds now ship this beta as the default installer so the
destructive VM matrix can run against the real image. Ubiquity,
`anduinos-installer-config`, and `anduinos-bwrap-hack` remain built and
published but are not installed automatically. During the beta period a user
may explicitly install `anduinos-installer-config` to obtain the complete
legacy fallback stack.

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
- Milestone 6: release gate review and only then removal of Ubiquity and
  `anduinos-installer-config`.

Disk encryption, TPM2 unlocking and FIDO2 unlocking are explicitly outside
the release-one scope. Release one supports unencrypted ext4 and unencrypted
Btrfs only.
