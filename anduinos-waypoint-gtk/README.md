# AnduinOS Waypoint

AnduinOS Waypoint is the next-generation Btrfs recovery experience for
AnduinOS. It adopts the clear GTK4/libadwaita interface, information
architecture, scheduling model, retention controls, read-only storage views, and external
backup design direction from the MIT-licensed Waypoint project, while replacing its
Void-specific and unsafe system integration with an AnduinOS recovery engine.

This directory is a new product with no legacy migration contract. Earlier
unreleased recovery experiments remain available from repository history.

## Safety boundary

The upstream rollback implementation is not an acceptable AnduinOS recovery
mechanism. AnduinOS must never boot a read-only snapshot directly or rely only
on `btrfs subvolume set-default`.

A system rollback is releasable only when it:

1. validates the selected recovery point and the installed boot artifacts;
2. creates a new writable deployment from the immutable snapshot;
3. preserves the currently bootable deployment as a fallback;
4. installs a verified one-shot GRUB entry without replacing normal entries;
5. performs the root switch from initramfs;
6. confirms a successful userspace boot; and
7. remains recoverable after interruption or power loss at every transaction
   boundary.

The package now contains that engine as an isolated recovery crate, a
privileged D-Bus adapter, a verified one-shot GRUB generator, an initramfs
root-switch binary, and a boot-confirmation service. These paths remain
release-gated until destructive VM and power-loss qualification is complete.
The package pre-provisions GRUB's external writable environment when an ESP is
mounted, and the trusted scheduling path creates and verifies it again at
runtime. This second path is required for ISO installations, whose target ESP
was not mounted when the package was configured in the image chroot.

Waypoint's package lifecycle and recovery transactions refresh GRUB with a
private no-op `os-prober` path. Waypoint entries are generated entirely from
trusted deployment metadata, so installing, upgrading, removing, scheduling,
or confirming a recovery must not inspect, mount, or add operating systems from
unrelated disks. The helper is stopped explicitly during package replacement;
matching its long executable name with `pkill -x` is not reliable because the
Linux process name is truncated.

The imported path-based external-backup API is not shipped. Its replacement accepts
only trusted deployment IDs, canonical backup UUIDs, and mounted destination
filesystem UUIDs. The helper resolves `/dev/disk/by-uuid` itself, rejects system and
untrusted mounts, and never accepts a destination path from the GUI. Exports are full
Btrfs send streams committed by `fsync` plus atomic rename. Imports verify the bounded
versioned manifest, exact stream size and SHA-256, pre-parse the stream, receive it into
unique internal staging, and recompute the kernel, initramfs, dpkg, MOK, read-only
subvolume, and local snapshot identities before registering a fresh deployment UUID.

External backup is deliberately manual in this first trusted version. Incremental
chains, automatic mount workers, caller-selected paths, and rsync backup formats are
not supported. The former helper methods were removed entirely after Deb introspection
showed that method-level feature gates could still be observed by the D-Bus macro.
Backup media is not encrypted by Waypoint; the UI tells users to use an encrypted
external filesystem when the system contains sensitive data.

Caller-selected in-place file restoration is also not shipped. The imported GUI,
CLI command, D-Bus method, root implementation, and now-unused `rsync` dependency
were removed together: checking only a final pathname cannot close intermediate-
symlink and time-of-check/time-of-use races in a privileged helper. A future file
recovery design must export through a descriptor-confined, non-privileged channel;
until then, Waypoint exposes only the transaction-safe whole-system restore. The
apparently read-only upstream file-browser action is also absent: the recovery
store is root-private and exposing its path would either fail for desktop users or
leak historical sensitive files. The reviewed export boundary is recorded in
[`docs/RECOVERY-SCOPE.md`](docs/RECOVERY-SCOPE.md).

## Source layout

The layout follows the repository-wide package convention:

- `src/`: Rust workspace and the CLI source;
  - `anduinos-recovery-engine/`: GUI-independent, D-Bus-independent trusted
    deployment metadata and early-boot rollback state machine;
  - `waypoint-common/`: shared UI/helper configuration and platform types;
  - `waypoint/`, `waypoint-helper/`, and `waypoint-scheduler/`: the adapted
    upstream product experience and its system integration;
- `assets/`: packaged configuration and service defaults;
- `data/`: desktop, D-Bus, Polkit, systemd, and icon resources;
- `scripts/`: Debian maintainer and validation scripts;
- `screenshots/`: AppStream screenshots;
- `upstream/`: original license, author attribution, documentation, and import
  provenance;
- `obj/`: generated release binaries consumed by APKG (gitignored).

## Development

```bash
cd src
cargo fmt --all -- --check
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

The full-send receive assumptions can be checked without touching a real disk:

```bash
scripts/test-external-backup-loopback.sh
scripts/test-recovery-operations-loopback.sh
```

These tests create, mount, exercise, unmount, and remove sparse Btrfs images
below `/tmp`. The recovery-operation test uses the real `btrfs` executable to
qualify immutable point creation, verification, pin/delete protection,
automatic-retention floors, and failed-creation cleanup. They never accept the
host root or a real block-device path. Both require passwordless `sudo` and exit
with status 77 when that test environment is unavailable.

After installing an APKG-built Deb, the non-destructive caller boundary and all
five Polkit actions can be qualified with:

```bash
scripts/test-installed-policy.sh
```

It uses only invalid mutation payloads, verifies that recovery state is
unchanged, and proves that a non-administrator cannot reach the system helper.

Build the package payload with:

```bash
bash build.sh amd64
```

The authoritative implementation plan and release gates are tracked in
[`TODO.md`](TODO.md). Rebooting rollback, cancellation, fallback, and hard
power-loss qualification are defined in
[`docs/VM-QUALIFICATION.md`](docs/VM-QUALIFICATION.md); the included helper
refuses to run outside a disposable VM with the exact AnduinOS Btrfs layout.

APKG-built amd64 and arm64 Debs have been unpacked and architecture-audited.
The amd64 package has also been repeatedly installed on a real Secure Boot
machine with an ext4 root. That unsupported layout is reported read-only as
`available=false`, package verification stays clean, D-Bus reactivation starts
the newly installed helper, Secure Boot remains enabled, and GRUB refreshes do
not probe unrelated operating systems. Clean install, replacement, purge, and
reinstall have also been exercised: purge removes only generated configuration
and the external GRUB environment, preserves unknown administrator/runtime
files and recovery data, disables the confirmation unit before its payload is
removed, and never calls an already removed APT hook. This is a useful packaging
and negative-layout gate; it does not replace destructive qualification on the
exact AnduinOS Btrfs layout.

The recovery engine uses its own Waypoint namespace and on-disk root
(`/.snapshots/anduinos-waypoint`). The GTK application and CLI use the deployment model throughout,
and the imported automatic/path-based backup implementation has been removed
from the build. The destructive qualification matrix is still required before
release.

## Licensing and attribution

AnduinOS Waypoint as a combined work is distributed under
GPL-3.0-or-later; see [`LICENSE`](LICENSE). The imported Waypoint source remains
attributed under its original MIT terms in [`upstream/`](upstream/README.md).
The MIT notice is retained in every source distribution and installed package.
