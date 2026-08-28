# Dracut migration transaction design

## Status and scope

Status: implementation and qualification contract. The synchronous
core-package guard, durable GRUB fallback, and hermetic fault injection are
implemented in this tree. One Resolute/ext4/legacy-BIOS PackageKit smoke test
has exercised the real offline service, the subsequent timer migration, and a
verified Dracut reboot. The complete rebooting VM matrix remains a production
release gate; one green smoke test or retry-timer unit test is not a substitute.

This document defines the upgrade contract for moving an existing AnduinOS
desktop from `initramfs-tools` to Dracut. It covers both supported entry paths:

1. a conservative interactive `apt upgrade`, which initially keeps the
   conflicting core packages back; and
2. a GNOME Software / PackageKit offline update. The Resolute APT backend marks
   the conflicting core update as blocked, but can update `anduinos-desktop`
   and install the non-conflicting migration bootstrap in
   `system-update.target`. A backend which does resolve the core transition
   directly is still covered by the synchronous core-package guard.

The migration must remain safe if package configuration fails, the PackageKit
offline service reboots after a failure, or power is lost at any instruction.
It must not depend on a timer running before the next reboot.

The PackageKit and systemd offline-update contracts are documented at:

- <https://github.com/PackageKit/PackageKit/blob/main/docs/offline-updates.txt>
- <https://www.freedesktop.org/software/systemd/man/latest/systemd.offline-updates.html>

Debian maintainer-script ordering and idempotency requirements are documented
at:

- <https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html>
- <https://www.debian.org/doc/debian-policy/ch-relationships.html>
- <https://manpages.debian.org/unstable/dpkg-dev/deb-triggers.5.en.html>
- <https://manpages.debian.org/unstable/dpkg/dpkg-trigger.1.en.html>

## Non-negotiable boot invariant

Once the first destructive package operation can occur, every durable state
must satisfy at least one of these conditions:

- the original boot entry still names an untouched, readable legacy initrd; or
- a separately named migration fallback entry names an untouched kernel and
  legacy initrd; or
- a normal GRUB entry names a newly generated and fully validated Dracut image.

The fallback files must not share the pathname being replaced by Dracut. A
hard link is deliberately forbidden: another maintainer script could still
truncate the active image in place and thereby corrupt both names. The guard
uses a reflink when supported and otherwise a full copy, always producing an
independent inode. No completion marker is proof of bootability by itself.

## Package responsibilities

### `anduinos-desktop`

`anduinos-desktop` depends on `anduinos-dracut-migration`. This is the bootstrap
path for existing desktop installations whose conservative `apt upgrade`
refuses the generator conflict. The dependency deliberately does not live in
`anduinos-apt-config`, `anduinos-apt-config-dev`, `anduinos-container`,
`anduinos-core-system`, or `anduinos-desktop-core`:

- the APT configuration packages are also used by container images;
- the container and generic core layers must not install a host boot migrator;
- `anduinos-core-system` cannot depend on the bootstrap because that would put
  the bootstrap inside the conflicting transaction it is intended to start.

The release pipeline must publish the migration helper before the core, then
publish the complete Dracut-compatible core, snapshots-manager, and Plymouth
set before publishing the desktop version that introduces this dependency.

The Dracut-only snapshots-manager and Plymouth candidates carry a versioned
dependency on the guarded core. CI deliberately publishes those temporarily
uninstallable candidates first and the guarded core last. Consequently no
intermediate repository index exposes an installable unguarded transition.

### `anduinos-dracut-migration`

This package is a one-shot transaction coordinator, not the boot-safety owner.
It has no dependency on Dracut and no conflict with the legacy generator.

Its timer handles only the conservative APT case:

1. inspect already downloaded APT metadata;
2. refresh metadata only if the complete candidate set is not visible;
3. simulate the exact install and reject removal of any `anduinos-*` package;
4. require a complete installed kernel before attempting the package set;
5. execute the package transaction under a shutdown/sleep inhibitor; and
6. call the shared verifier after APT returns.

The timer is an optimization and retry mechanism. PackageKit correctness must
be unchanged if the timer has never started.

The package and its state may remain installed for the full AnduinOS 2 support
window. Removing it after roughly two years is a separate cleanup release and
must not remove a still-present fallback unless the current images pass the
same verifier.

### `anduinos-core-system`

The new core package owns the synchronous safety boundary because both APT and
PackageKit must run its maintainer scripts.

Its `preinst` must be self-contained: the new package payload has not been
unpacked yet and ordinary `Depends` cannot be assumed available. On an upgrade
from the legacy stack it must:

1. select one installed kernel/initrd pair, preferring `uname -r`;
2. verify both files are non-empty and the old initrd is readable with the
   currently installed inspection tool;
3. create separately named fallback kernel and initrd files in `/boot`, using
   reflinks where available and full copies otherwise;
4. verify enough free `/boot` space remains for a staged Dracut image plus a
   growth margin;
5. write and fsync a manifest containing versions, sizes, and SHA-256 digests;
6. install a temporary GRUB generator that emits a first, top-level migration
   fallback entry;
7. generate GRUB into a separate file in `/boot/grub`, validate and sync it,
   atomically rename it over `grub.cfg`, and verify that the first entry
   references the fallback kernel and initrd; and
8. only then write the durable `fallback-ready` state and return success.

If any step fails, `preinst` returns nonzero. The normal legacy boot artifacts
have not been modified, so an offline-update failure reboot remains safe.

Its `postinst configure` must:

1. require Dracut and all declared boot dependencies to be present;
2. write `packages-switched` idempotently;
3. generate images for every kernel that has both `/boot/vmlinuz-*` and a
   matching `/lib/modules/*`, allowing Dracut to use its same-directory
   temporary output and rename;
4. run the shared verifier;
5. regenerate and verify GRUB;
6. write `images-verified` only after all validation succeeds;
7. remove the temporary first-entry GRUB generator and regenerate GRUB, while
   retaining the separately named fallback artifacts; and
8. write `transaction-complete` and disable the retry timer if present.

Any generation, validation, or GRUB failure returns nonzero. PackageKit may
still reboot because that is its failure policy, but the first GRUB entry and
fallback artifacts remain available and bootable.

The scripts must accept every dpkg recovery invocation and be idempotent. A
subsequent `dpkg --configure -a`, timer retry, or package reinstall continues
from inspected disk state rather than trusting the last marker.
Once `fallback-ready` exists, retries verify its manifest and reuse the sealed
copy; they never replace it from an active initrd that may already have changed.

### Initrd consumers

`anduinos-btrfs-snapshots-manager` and `plymouth-anduinos` must not each invent
a different migration policy.

- Their package changes use the shared staged writer instead of silently
  running independent best-effort rebuilds.
- An image-regeneration failure must leave dpkg failed; it must not be hidden with
  `|| true`.
- Disk Snapshots Manager can retain an explicit synchronous rebuild where its
  recovery feature requires it, but it must call the shared verifier and fail
  configuration if the required Btrfs recovery module is absent.
- No package deletes the migration fallback during this release series.

dpkg explicitly does not define trigger execution order, and a package must
not construct a trigger cycle by waiting for itself. The guarded core therefore
does not attempt to become the last interested package. Instead, it uses
`dpkg-divert` to preserve and wrap Ubuntu Dracut's historical
`/usr/sbin/update-initramfs` compatibility entry point. Activation-only calls
still defer normally; the later real Dracut handler cannot return successfully
until the wrapper's shared verification also succeeds. The same lifecycle wraps
`/usr/sbin/update-grub`, replacing its direct write with staged generation,
fsync, and atomic rename. Removal of the core restores both diverted upstream
implementations through an idempotent `prerm`.

## Durable state machine

State lives in `/var/lib/anduinos-dracut-migration/`. Markers are created by
writing a temporary file, syncing it, renaming it, and syncing the directory.
Each transition revalidates its input artifacts before proceeding.

| State | Required durable evidence | Safe reboot result |
|---|---|---|
| `legacy` | Original kernel and legacy initrd | Existing normal entry boots |
| `fallback-ready` | Digest-verified fallback pair and GRUB reference | Migration fallback is the first entry |
| `packages-switched` | `fallback-ready`; Dracut packages unpacked/configuring | Migration fallback boots |
| `images-verified` | `fallback-ready`; at least one validated Dracut pair and normal GRUB reference | Normal Dracut entry or fallback boots |
| `transaction-complete` | Normal entry restored first, temporary `GRUB_DEFAULT=0` retained, fallback retained | The verified first Dracut entry boots; fallback is available manually |
| `boot-confirmed` | A different kernel boot ID reached multi-user target, an embedded Dracut pre-pivot hook left an ephemeral proof in `/run`, the running kernel's image passed the verifier, and the temporary default override was removed with an atomic GRUB rebuild | The user's previous GRUB policy is restored; eligible for later cleanup policy |

Markers never move backwards. If a marker and its required artifacts disagree,
the artifacts win: the helper repairs or fails safely instead of skipping work.

## Shared boot verifier

One packaged helper must be used by the core `postinst`, the migration timer,
Disk Snapshots Manager, and VM qualification. It succeeds only
when all applicable checks pass:

- at least one installed kernel has a matching non-empty initrd;
- `lsinitrd` can fully list every image selected for normal boot;
- every selected image contains the `anduinos-migration-proof` module whose
  pre-pivot hook proves that a later boot actually traversed Dracut;
- installed-system images do not contain `dmsquash-live`,
  `dmsquash-live-autooverlay`, `livenet`, or `anduinos-live-layers`;
- a Btrfs root with Disk Snapshots Manager installed contains the
  `anduinos-btrfs-snapshots-manager` Dracut module and its required recovery
  payloads;
- the selected kernel has its matching `/lib/modules/<version>` tree;
- `/boot/grub/grub.cfg` contains a normal entry referencing a validated pair,
  and migration completion additionally requires that pair in the first/default
  entry;
- while migration is incomplete, GRUB also references the digest-verified
  fallback pair; and
- the separately generated GRUB configuration is non-empty before it is
  atomically renamed over the active configuration.

`lsinitrd` proves image structure, not that firmware, GRUB, storage discovery,
and root mounting work together. Therefore only a VM reboot can qualify a
release.

## Reboot and power-loss behavior

### Interactive/timer migration

The coordinator holds a `systemd-inhibit` block for shutdown and sleep around
the entire APT transaction and final verification. It cannot prevent a kernel
panic, forced power-off, hypervisor reset, or power loss; the fallback invariant
handles those cases.

### PackageKit offline migration

The Resolute PackageKit APT backend reports the core candidate as `blocked`
because it must remove the legacy initramfs packages. It nevertheless accepts
the independently upgradable desktop candidate and installs
`anduinos-dracut-migration` as an automatic dependency in the isolated
`system-update.target`. PackageKit then records success and reboots without
having changed the boot generator. The normal legacy boot remains valid; the
timer performs the guarded APT migration shortly after that boot, and the next
ordinary reboot enters the verified Dracut image.

This two-stage behavior is an availability optimization, not the sole safety
boundary. If another PackageKit/backend version resolves the conflicting core
transition directly, the synchronous core `preinst` seals and publishes the
fallback before dpkg can remove the legacy stack. If that offline transaction
fails and PackageKit reboots, GRUB selects the migration fallback first.

### Recovery after interruption

On the next userspace boot:

- a successful fallback boot leaves the state and packages intact;
- `dpkg --audit` and `dpkg --configure -a` can resume configuration;
- the migration service retries only when no package manager owns the dpkg
  lock; and
- `transaction-complete` is never synthesized merely because
  `initramfs-tools` is absent.

## Space and filesystem rules

Before returning from `preinst`, the transaction checks free space on the
filesystem containing `/boot`. It budgets for:

- a reflinked or copied fallback pair;
- one Dracut temporary image at the observed largest initrd size plus a safety
  margin; and
- GRUB configuration temporary files.

The fallback and generated image must stay on the same filesystem as their
final path so rename is atomic. `/boot` and `/boot/efi` are synced at the
fallback and commit boundaries. Failure to meet the budget aborts while the
legacy boot path is still intact.

## Implementation sequence

The work should land in the following order so no published intermediate state
weakens existing installations:

1. Add the shared fallback/state/verifier helper and unit tests without making
   it active.
2. Add `anduinos-core-system` `preinst` and `postinst`, package the helper where
   `preinst` can use only its own embedded code, and add generated `.deb`
   contract tests using `apkg build` plus `dpkg-deb -I/-e`.
3. Convert Plymouth and Disk Snapshots Manager regeneration to the common
   verifier contract and remove swallowed Dracut errors.
4. Update the migration coordinator to perform preflight, use a shutdown
   inhibitor, invoke the exact package set, and resume interrupted dpkg states.
5. Keep the desktop-only dependency and correct CI release ordering so the
   full compatible set is visible before the bootstrap version.
6. Publish to a non-production repository and pass the VM matrix below.
7. Publish the complete package set atomically, then publish the desktop
   bootstrap version last.

## Required automated tests

### Hermetic script tests

Inject failure after every durable operation:

- before and after each fallback reflink/copy;
- before and after manifest rename and directory sync;
- before, during, and after GRUB regeneration;
- before and after each Dracut temporary image is renamed;
- after the first of multiple kernel images;
- before and after each verifier check;
- before removing the temporary first-entry generator; and
- before every state-marker rename.

After each injected failure, rerun the script and require idempotent completion.
At every checkpoint, a model checker test must find at least one boot entry
whose named kernel and initrd both exist and match the recorded digest.

### Package contract tests

Build the real packages with `apkg build`, extract them with `dpkg-deb`, and
assert:

- core contains executable `preinst` and `postinst` scripts;
- migration has no Dracut dependency or legacy conflict;
- desktop, but not APT-config or container packages, depends on migration;
- no relevant maintainer script hides a Dracut failure;
- the core package contains and lifecycle-tests the diverted Dracut
  compatibility guard; and
- CI publication order includes every runtime and release-only edge.

### Rebooting VM matrix

Each row is tested on ext4 and Btrfs root, UEFI and legacy BIOS where supported,
one and two installed kernels, Secure Boot enabled and disabled, and with a
small separate `/boot` filesystem:

| Upgrade path | Faults | Required result |
|---|---|---|
| `apt upgrade` then timer | none | Dracut boot; transaction complete |
| explicit `apt install anduinos-core-system` | none | Dracut boot; transaction complete |
| GNOME Software / PackageKit offline | none | bootstrap-only offline reboot; timer completes migration; next reboot enters Dracut |
| each path | kill package process at every checkpoint | fallback or original path boots; rerun completes |
| each path | hard reset at every checkpoint | fallback or original path boots; filesystem and dpkg recover |
| each path | Dracut failure, full `/boot`, bad GRUB generation | transaction fails but automatic reboot is bootable |
| Btrfs rows | recovery rollback after migration | recovery module enters and completes the protocol |

The PackageKit test must use the real prepare/trigger API and
`packagekit-offline-update.service`; invoking APT in a rescue shell is not an
equivalent test. On Resolute PackageKit 1.3 this is
`pkgcli update --download-only anduinos-desktop` followed by
`pkgcli offline-update trigger`. Older releases expose the equivalent commands
as `pkcon --only-download update anduinos-desktop` and
`pkcon offline-trigger`. The test must assert that core remains blocked during
the offline bootstrap, that PackageKit records desktop plus migration success,
and that the later timer transaction and Dracut boot are independently
verified.

## Release gates

Production publication is blocked unless all of these are true:

- hermetic and built-package tests pass on both target architectures;
- the complete rebooting VM matrix passes from the last production 2.0.2
  package set;
- a forced reset at every checkpoint still leaves an automatically reachable
  boot path;
- PackageKit either records only the non-destructive desktop/migration
  bootstrap, or, if it applies core directly, records success only after the
  synchronous shared verifier has succeeded;
- a PackageKit-recorded failure reboots into the fallback without manual file
  repair; and
- the repository publication order cannot expose the desktop bootstrap before
  all migration candidates.

## Retirement

Keeping the one-shot package installed is harmless and makes repair tooling
available. A future cleanup release, no earlier than the end of the AnduinOS 2
support window, may remove its timer, state, and fallback only when:

1. `boot-confirmed` exists;
2. the shared verifier succeeds against the then-current kernel and GRUB;
3. no legacy generator package is installed or configured;
4. no dpkg audit error is present; and
5. fallback removal and GRUB regeneration complete successfully.

If any condition fails, cleanup is deferred indefinitely rather than risking a
boot path for cosmetic package removal.
