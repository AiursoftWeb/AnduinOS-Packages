# TODO: LUKS2 Full-Disk Encryption Support

## Status and purpose

This document records the design discussion for adding installer-managed disk
encryption to AnduinOS. It is an implementation brief, not a claim that the
feature is already supported.

The existing storage roadmap calls this **Storage 6**. Encryption must remain
unavailable in release UI until the supported layouts, recovery flow, boot
chain, cleanup behavior, and destructive VM tests described here are complete.

The first milestone should provide reliable encryption at rest. It must not be
blocked on TPM auto-unlock, FIDO2, hibernation, hybrid sleep, arbitrary storage
stacks, or LVM.

## Archived staged conclusion — 2026-08-09

This is the hand-off state at the end of the design and proof-of-concept
session. **No final production boot architecture has been approved.** The
storage mechanism is substantially understood, but the product must still
choose and qualify one of two honest Secure Boot threat models. Do not expose
encryption in the release installer merely because the mechanism tests pass.

### What is proven on disposable amd64/Btrfs VMs

- A LUKS2 root can contain Btrfs `@root`, including `/boot`, and boot with one
  human prompt while Secure Boot remains enabled.
- Keeping `/boot` in `@root` makes kernel, initramfs, modules, configuration,
  package state, and userspace roll back together. G5 booted both sides of a
  real kernel upgrade while the ESP remained byte-identical.
- Ubuntu's stock Microsoft/Canonical-signed shim and GRUB cannot directly open
  the chosen LUKS2 root because the signed GRUB module set lacks `luks2`.
- Stock signed GRUB can use `cryptomount -H` with a detached LUKS1 compatibility
  header that wraps the same random volume key and data area as the main LUKS2
  header. This needs no paid certificate, custom shim, MOK enrollment, or
  disabled Secure Boot.
- An encrypted initramfs can use an internal random machine credential to
  reopen the main LUKS2 mapping silently, so the user is not prompted twice.
- Password rotation, recovery-key rotation at every recorded transaction
  boundary, sanitized rescue-header boot, and boot after complete destruction
  of the main LUKS2 metadata all worked in the proof fixtures.
- G7 proved that the certificate-free path is **not** a complete verified-boot
  chain. An attacker who modifies only the ESP can alter external GRUB policy,
  inject kernel arguments, and append executable unsigned initramfs content to
  a publicly signed kernel while firmware still reports Secure Boot enabled.
- G8 proved that a signed UKI protects its own kernel, initramfs, and embedded
  command line. It does not close G7 by itself because untrusted GRUB policy
  can select a different publicly signed kernel and an attacker-supplied
  initramfs.

### What is rejected

- Do not use a separate unencrypted `/boot`: it reintroduces the exact
  kernel/root rollback mismatch that the design is meant to avoid.
- Do not introduce LVM or replace the dedicated swap partition with a swapfile.
- Do not claim that stock Ubuntu GRUB directly supports this LUKS2 profile.
- Do not claim that a signed UKI alone authenticates an external GRUB policy.
- Do not depend on an AnduinOS public signing certificate, a project-owned
  custom shim, disabled Secure Boot, or removing Btrfs system rollback.
- Do not retain an old detached header containing the previous user password.
  A rescue header must be separately sanitized to recovery and machine
  credentials only.

### The unresolved release fork

**Branch A — encryption at rest with the Ubuntu-classic integrity boundary.**
Use the official signed shim and stock signed GRUB, an external minimal GRUB
configuration, active and sanitized-rescue detached LUKS1 headers on the ESP,
and a LUKS2 root containing `/boot`. This branch already has strong mechanism,
rollback, lifecycle, and disaster-recovery evidence. It is viable only if the
published threat model explicitly excludes an offline evil-maid attacker who
can modify the ESP. “Secure Boot enabled” must not be marketed as full boot
integrity for this branch.

**Branch B — machine-local MOK verified boot.** Reuse the machine MOK and
MokManager enrollment concept already documented in `SECURE-BOOT-DESIGN.md`.
Have the official signed shim trust a constrained, self-contained, MOK-signed
GRUB; that GRUB directly opens LUKS2 and chainloads only a root-owned,
MOK-signed UKI. This needs neither a paid certificate nor a custom shim and may
eliminate the detached LUKS1 compatibility-header lifecycle. It is not yet
proven: enrollment, cancellation, recovery, negative substitution tests, and
update safety remain open.

The last G9 builder did boot with Microsoft-key OVMF Secure Boot enabled and
generated the machine MOK and UKI, but stopped before queueing enrollment
because the reproducer asked `grub-mkstandalone` for module `chainloader`;
Ubuntu provides the command in `chain.mod`. The source has been corrected to
`chain` for the next engineer, but the corrected VM path was deliberately not
rerun during this archival pass. **G9 remains failed/incomplete, not passed.**

### Remaining qualification work

- Complete G9 if full evil-maid integrity is a release requirement, then test
  cancellation, forgotten enrollment password, key loss, unsigned GRUB/UKI
  rejection, updates, rollback, and interrupted writes.
- Qualify ext4 independently; all completed storage proofs used Btrfs.
- Qualify arm64 under AAVMF or supported real hardware; this host lacked both
  `qemu-system-aarch64` and AAVMF.
- Perform torn-write and power-loss injection inside cryptsetup updates and
  FAT/ESP publication; the completed lifecycle tests covered durable logical
  transaction boundaries, not every possible torn sector/write.
- Integrate the selected architecture into the real installer. The work in
  `tests/vm/luks-grub-btrfs/` is proof code, not product implementation.
- Keep TPM auto-unlock, persistent encrypted hibernation, and hybrid sleep as
  later qualification projects. Random-key encrypted swap cannot resume.

### Reproducer and disposable evidence index

The maintained reproducer sources are under
`tests/vm/luks-grub-btrfs/`. The corresponding `/tmp` directories below are
useful on the original workstation but are disposable evidence, not durable
project artifacts:

| Gate | Result | Evidence directory |
|---|---|---|
| G1 | direct LUKS2/Btrfs mechanism passed | `/tmp/anduinos-luks-g1.lKuajN` |
| G2 | stock signed dual-header path passed | `/tmp/anduinos-luks-g2-dual-header.yXvvoa` |
| G3/G4 | signed kernel and silent reopen passed | `/tmp/anduinos-luks-g3-linux-initrd.6mLeM5` |
| G5 | kernel-update rollback passed | `/tmp/anduinos-luks-g5-rollback.l5ZSuK` |
| G6 | sanitized header lifecycle passed | `/tmp/anduinos-luks-g6-header-lifecycle.YMsSo9` |
| G6 | recovery rotation boundaries passed | `/tmp/anduinos-luks-g6-recovery-rotation.4ywfbq` |
| G6 | destroyed main-header recovery passed | `/tmp/anduinos-luks-g6-main-header-recovery.npHrYb` |
| G7 | malicious ESP negative test succeeded | `/tmp/anduinos-luks-g7-esp-tamper.1i5dEk` |
| G8 | signed UKI mechanism passed | `/tmp/anduinos-luks-g8-signed-uki.T6yyRq` |
| G9 | builder failed before MOK enrollment | `/tmp/anduinos-luks-g9-machine-mok.OeozCK` |

There is no hidden third route established by these experiments: if the
project will not enroll a machine-local MOK or otherwise authenticate its
loader policy, it must choose Branch A and honestly accept its integrity
boundary. If that boundary is unacceptable, finish Branch B before shipping.

## Archived architecture checkpoint — 2026-08-09

**Do not implement the separate unencrypted `/boot` design described in the
historical discussion below.** It was selected as a pragmatic first approach,
then challenged because it breaks the natural atomic relationship between a
Btrfs root snapshot, its kernel, initramfs, kernel modules, DKMS state, and
package database.

At this checkpoint, the leading no-MOK candidate was a Windows/BitLocker-like
boot boundary that
does **not** require an AnduinOS signing certificate, a custom shim, a custom
trusted GRUB build, MOK enrollment, or disabled Secure Boot:

```text
unencrypted ESP
├── Microsoft-signed Ubuntu shim
├── Canonical-signed stock Ubuntu GRUB
├── minimal stable GRUB configuration
├── current detached LUKS1 compatibility header
└── detached rescue header (recovery + machine slots only)

encrypted root partition
└── LUKS2
    └── Btrfs or ext4
        └── /boot, signed kernel, and encrypted initramfs live inside root
```

For Btrfs, `/boot` remains inside `@root`, so a root snapshot carries its
matching kernel, initramfs, configuration, and `/usr/lib/modules`. The ESP
contains no per-kernel deployment archive. Stock signed GRUB cannot open the
main LUKS2 header because Ubuntu's signed module set omits `luks2`, so it uses
its trusted built-in LUKS1 support with `cryptomount -H`. The small detached
LUKS1 header and the main LUKS2 header describe the same encrypted data area
and wrap the same random volume key. The compatibility header contains no
plaintext root key.

After the user selects AnduinOS, GRUB asks once for the user password or
recovery key, reads `/boot` from encrypted root, and loads the root-owned boot
artifact. The initramfs silently reopens the main LUKS2 mapping using a random
machine credential stored only inside that encrypted artifact.

This certificate-free chain is sufficient for encryption at rest and keeps
Secure Boot enabled, but G7 proved it does not provide evil-maid integrity:
an offline attacker can replace the external ESP GRUB configuration, inject a
kernel command line, and append an unsigned initramfs to a publicly signed
kernel. A signed UKI protects its own contents and command line, but an
untrusted GRUB policy can choose a different signed kernel instead. Therefore
the final integrity choice is explicit:

- accept the same classic signed-kernel/unsigned-initramfs threat boundary as
  Ubuntu, with no AnduinOS/MOK trust in GRUB; or
- reuse the installer-managed machine MOK already required by
  `SECURE-BOOT-DESIGN.md` to trust a constrained self-contained GRUB and signed
  UKIs. This requires no paid certificate and no custom shim, but its existing
  MokManager enrollment must pass the remaining VM gate.

The storage/encryption mechanism is now strongly evidenced on amd64/Btrfs, but
the trusted-policy fork is still under VM validation. It becomes the selected
release design only after the integrity boundary, enrollment path, ext4 path,
and supported architectures pass their gates. Until then, encryption remains
unavailable in release UI.

## Decisions already made for the Branch A candidate

The following points should be treated as the intended direction unless a
later design review explicitly changes them:

The detached-LUKS1 credential and maintenance decisions in this section apply
to Branch A. If Branch B passes and directly opens LUKS2, remove the
compatibility-header machinery rather than carrying it forward unnecessarily.

- Use LUKS2 as the block-layer encryption boundary.
- Put either Btrfs or ext4 inside the same root-encryption implementation.
- Do not introduce LVM. This is a project invariant, not merely a first-release
  deferral.
- Keep the EFI System Partition (ESP) unencrypted.
- Keep `/boot` inside root. For Btrfs it remains inside `@root` and participates
  in system snapshots.
- Keep only stable pre-boot components on the ESP: distribution-signed shim and
  GRUB, minimal configuration, the active detached LUKS1 compatibility header,
  a sanitized rescue header with no user-password slot, and future TPM-sealed
  metadata.
- The first milestone asks exactly once in GRUB and never prompts again in the
  initramfs. A future TPM path may make the normal boot promptless while
  retaining password and recovery fallback.
- Use a calibrated PBKDF2 keyslot for the human password in the detached LUKS1
  header because current stock signed GRUB has no Argon2 path. Do not mirror
  that password into the main LUKS2 header. Document the resulting offline
  dictionary-attack limitation and enforce a strong password policy.
- Protect the root filesystem with a user passphrase and a separately generated
  recovery key.
- Enroll the user password only in the detached boot header. Enroll the random
  recovery and machine credentials in both the compatibility header and the
  main LUKS2 header.
- Never make TPM2 or FIDO2 the only way to unlock a system.
- Always use a dedicated swap partition. Do not replace it with a swapfile or
  an LVM logical volume.
- Encrypt the dedicated disk-swap partition with a new random key on every
  boot in the first milestone.
- Do not promise hibernation merely because the swap partition is larger than
  RAM. Random-key swap cannot be a persistent resume target.
- Never put a plaintext disk passphrase, recovery key, temporary unlock key, or
  keyfile contents in `InstallPlan`, the storage graph, JSON/YAML evidence,
  command-line arguments, environment variables, logs, crash reports, or a
  regular temporary file.
- Pass secrets out of band through a short-lived anonymous pipe or equivalent
  dedicated IPC channel between the frontend and the privileged executor.
- Use the user's passphrase directly for `luksFormat`. Do not create a
  temporary LUKS keyslot merely to keep the mapping open during installation.
- Keep the mapping open for the installation and close it during final cleanup.
- Btrfs compression is a filesystem option, not an encryption option. ext4
  does not expose the compression control.
- Btrfs keeps its existing subvolume layout and snapshot capabilities inside
  LUKS. ext4 remains a non-snapshot installation.
- Encryption must not silently enable hibernation, TPM enrollment, or LVM.

## Target storage topology

### First milestone: encrypted Btrfs

```text
GPT disk
├── BIOS boot partition (amd64 layout compatibility only, where applicable)
├── EFI System Partition (FAT, unencrypted)
├── swap partition
│   └── volatile random-key dm-crypt mapping
│       └── swap
└── root partition
    └── LUKS2: anduinos-root
        └── Btrfs
            ├── @root          /
            │   └── boot       matching kernel/initramfs or UKI and boot state
            ├── @home          /home
            ├── @log           /var/log
            ├── @snapshots     /.snapshots
            ├── @containers    /var/lib/containers
            └── @libvirt       /var/lib/libvirt/images
```

### First milestone: encrypted ext4

```text
GPT disk
├── BIOS boot partition (amd64 layout compatibility only, where applicable)
├── EFI System Partition (FAT, unencrypted)
├── swap partition
│   └── volatile random-key dm-crypt mapping
│       └── swap
└── root partition
    └── LUKS2: anduinos-root
        └── ext4 mounted at /
            └── /boot contains the selected root-owned boot artifacts
```

The LUKS code path should be shared. Only the formatter, mount policy, Btrfs
subvolume creation, compression options, snapshot-manager installation, and
filesystem-specific verification should branch by filesystem.

Both archived branches deliberately preserve the existing `/boot` placement in
`BTRFS-DESIGN.md`. It avoids a second boot filesystem, avoids an external
per-kernel garbage collector, and makes a Btrfs root snapshot carry its kernel,
initramfs/UKI, package database, and kernel modules together.

This borrows BitLocker's boundary rather than Ubuntu classic encrypted LVM's
exact partition layout: a small trusted pre-boot environment lives outside the
encrypted OS volume, while the real OS boot image lives inside the encrypted
volume. AnduinOS still uses no LVM and no swapfile.

### Why LVM is deliberately absent

Ubuntu commonly places multiple logical volumes inside one encrypted
container. This is useful when root, home, and persistent swap must share one
unlock operation while remaining independently resizable block devices. It is
particularly useful for ext4, server installations, thin provisioning, and
multi-volume administration.

AnduinOS currently has one root filesystem plus a dedicated swap partition.
Btrfs already provides shared free space, subvolumes, and filesystem-level
snapshots. Adding LVM now would add another metadata and recovery layer without
solving a first-milestone requirement.

If AnduinOS later needs independently resizable ext4 volumes or persistent
encrypted hibernation swap, that future design must solve the problem with
typed partitions and encryption mappings instead of silently introducing LVM.

## Installer user experience

### Page order

The existing disk page correctly says that storage settings come next. The
intended flow is:

1. Select the target disk.
2. Select the installation method, such as erase-disk or guided coexistence.
3. Select Btrfs or ext4 where that choice is supported.
4. Show a new **Filesystem Settings** or **Storage Settings** page.
5. Review the final plan, including an explicit encryption and recovery
   summary.
6. Start the privileged executor.

Do not add passphrase fields to the disk-card page. Encryption depends on the
chosen layout and filesystem, so the settings page must appear after those
choices are known.

### Questions and controls on the storage settings page

For Btrfs, show:

- **Enable filesystem compression** — checkbox, checked by default to preserve
  the current `compress=zstd` policy.
- A short explanation that compression usually saves space and can reduce
  physical writes.
- Do not ask for an expert compression level in the first version. Store a
  typed policy such as `zstd` or `none`, not a free-form mount option.
- **Encrypt this AnduinOS installation** — checkbox.

For ext4, show:

- **Encrypt this AnduinOS installation** — checkbox.
- Do not show a nonfunctional compression checkbox.

When encryption is selected, reveal:

- **Disk encryption passphrase**.
- **Confirm disk encryption passphrase**.
- Per-field show/hide controls.
- A passphrase mismatch error before the user can continue.
- Caps Lock state and a warning that the early-boot keyboard environment may
  differ from the graphical Live session unless the selected keyboard layout
  is included in the target initramfs.
- A clear explanation that the login password and disk passphrase are
  different credentials, even if the user chooses the same text.
- A clear explanation that losing both the passphrase and recovery key makes
  the data unrecoverable.
- A statement that swap is also encrypted, but hibernation is not enabled by
  this option.
- A storage summary stating that root, home, `/boot`, kernels, and initramfs are
  inside the encrypted root while the ESP contains only trusted pre-boot
  components and remains unencrypted.

Do not add a separate checkbox for encrypting `/boot`. It follows root and is
inside the same LUKS2 boundary. Likewise, do not ask the user to choose a boot
filesystem, boot mount options, or arbitrary boot size.

The UI should not trim, normalize, silently transliterate, or rewrite the
passphrase. Password-strength feedback may be advisory, but simplistic
composition rules must not encourage short predictable passwords.

The default state of the encryption checkbox is a product-policy decision that
must be made before UI freeze. It should be prominently recommended for mobile
devices, but the installer must never surprise the user by changing an
existing default without showing the choice on the review page.

### Recovery-key experience

The privileged backend, not the Python frontend, should generate a
cryptographically random recovery key. Use a human-transcribable ASCII format
with sufficient entropy; formatting separators are presentation, not entropy.

Before destructive work proceeds, the frontend must:

1. Receive the generated key over a dedicated secret-response channel.
2. Display it in a screen that is never copied into normal event logs.
3. Ask the user to save it offline.
4. Require confirmation, preferably by re-entering selected groups or the full
   key rather than clicking an unchecked acknowledgement.

Saving to removable media may be offered explicitly. Do not automatically
save the key onto the disk being encrypted, into the Live user's home
directory, or into the clipboard. Clipboard copying, if offered, must be an
explicit action accompanied by a warning.

Cancellation before the confirmed destructive boundary must leave the target
unchanged and invalidate the generated key.

## Installation-plan and storage-graph changes

`InstallPlan` is versioned, serialized, validated across a privilege boundary,
and hashed into installation evidence. It may record desired policy, but never
a credential.

A possible typed model is:

```text
StorageSpec
├── filesystem: btrfs | ext4
├── compression: zstd | none
└── encryption
    ├── enabled: bool
    ├── format: luks2
    └── mapper_name: anduinos-root
```

The exact dataclass layout may differ, but it must satisfy these rules:

- Bump and strictly validate the plan schema version.
- Reject `compression != none` for ext4.
- Reject unknown encryption formats and arbitrary mapper names.
- Reject any frontend attempt to declare a separate boot filesystem or supply
  raw GRUB, cryptsetup, UKI, formatter, or mount options.
- The executor owns the canonical mapper name; the UI must not be able to
  inject a path or command fragment.
- No `passphrase`, `password`, `recovery_key`, `keyfile`, secret FD number, or
  secret-channel payload belongs in `InstallPlan`.
- Redact sensitive-looking unknown fields before reporting schema errors.
- Guided-install evidence may safely say that encryption is enabled, but must
  not contain credential-derived data.

The storage graph already reserves `BlockReferenceKind.LUKS`. The encrypted
root filesystem must be represented as a filesystem on a LUKS mapping whose
parent is the root partition. Volatile random-key swap is not a LUKS2
container; either add an explicit typed declaration for volatile dm-crypt swap
or keep it as a narrowly defined swap policy. Do not falsely describe it as a
LUKS block reference.

The graph remains command-free. It must never accept raw cryptsetup options,
mount options, shell fragments, or frontend-selected device-mapper paths.

## Secret transport and executor protocol

### Current limitation

`ExecutorClient` currently writes one JSON line containing `InstallPlan`,
closes stdin, and then reads JSON events from stdout. `executor_cli.py` reads
that single line before starting the pipeline. This one-way protocol cannot
safely support an executor-initiated passphrase request or recovery-key
delivery.

Do not work around this by adding a password field to the plan or by writing a
temporary keyfile under `/tmp`, `/run/user`, or the target filesystem.

### Required protocol shape

The executor needs a small, explicitly framed duplex protocol. Conceptually:

```text
frontend -> executor: InstallPlan (non-secret)
executor -> frontend: secret-request(disk-passphrase, request-id)
frontend -> executor: secret-response(request-id, length, raw bytes)
executor -> frontend: secret-delivery(recovery-key, request-id, raw bytes)
frontend -> executor: secret-confirmed(request-id)
executor -> frontend: ordinary progress and status events
```

A dedicated inherited pipe pair or Unix socketpair for secrets is preferred.
Normal stdout JSON events may continue to carry logs and progress, but must not
carry credentials. The implementation must verify that the selected IPC works
through the actual privilege wrapper (`systemd-inhibit` and, where used,
`sudo`) without leaking inherited descriptors to unrelated child processes.

Protocol requirements:

- Length-frame secret bytes; do not rely on newline termination.
- Bind every response to a unique outstanding request ID and expected secret
  type.
- Permit only one response for a request and close or invalidate it after use.
- Apply timeouts and handle frontend cancellation without deadlock.
- Never echo a secret in an exception, event, debug representation, or command
  trace.
- Pass cryptsetup credentials through subprocess stdin or a dedicated
  anonymous FD, never argv or the environment.
- Ensure unrelated commands and chrooted processes do not inherit the secret
  descriptors.
- Minimize credential lifetime and overwrite mutable buffers where practical.
  Be honest that Python strings and runtime copies cannot provide a perfect
  memory-erasure guarantee.
- Unit-test malformed frames, duplicate responses, wrong request IDs, early
  EOF, cancellation, executor crash, and frontend crash.

### Why no temporary installation keyslot

A path such as `/run/user/1000/temp_install.key` is still a live unlock
credential. An unprivileged path also creates ownership, replacement, symlink,
and time-of-check/time-of-use problems. tmpfs reduces persistence but is not a
substitute for eliminating the credential, and “securely deleting” a file is
not a reliable lifecycle primitive.

The privileged executor runs for the entire installation and can keep the
device-mapper mapping open. Therefore the simpler flow is:

1. Use the user's passphrase directly for `luksFormat` through anonymous IPC.
2. Add the backend-generated recovery key as a second permanent keyslot.
3. Verify both permanent credentials.
4. Open the mapping and keep it open.

If a transactional temporary slot is ever proven necessary, it must be a
separate reviewed design using backend-created anonymous storage such as a
sealed `memfd` or `O_TMPFILE`. It must remove an exact verified slot with
`luksKillSlot`, never remove a key ambiguously by pathname or content.

## Executor pipeline changes

The current `PrepareStorageStep` combines partitioning and formatting. LUKS
requires an explicit boundary between those operations. The all-step preflight
guarantee must be preserved: dependency and plan validation for every step
must complete before the first destructive command.

The following are logical steps. Several may be grouped in the progress UI,
but the internal destructive boundaries and verification must remain visible
to tests.

### 1. `VerifyEncryptionSupportStep` (conditional, non-destructive)

- Require the approved `cryptsetup` version and target packages.
- Verify initramfs integration, bootloader support, architecture, firmware,
  Secure Boot state, and selected filesystem combination.
- Reject unsupported guided/manual layouts before partitioning.
- Confirm that the target is not already mapped, mounted, swap-active, or
  claimed by another device-mapper stack.
- Validate required entropy availability and recovery-key generation support.
- Freeze the expected device identities and write set.

### 2. `PrepareEncryptionCredentialsStep` (conditional, non-destructive)

- Request the passphrase from the frontend over secret IPC.
- Generate the recovery key in the privileged process.
- Deliver it through the protected response channel.
- Wait for the user's recovery confirmation.
- Abort without modifying the disk if the user cancels or confirmation fails.
- Keep secrets only in narrowly scoped mutable buffers needed by the immediate
  provisioning step.

### 3. `PartitionTargetStep` (destructive)

- Split partition-table creation out of `PrepareStorageStep`.
- Create or authorize the ESP, swap, and root partitions according to the
  immutable execution plan. Do not create a separate `/boot` partition.
- Settle udev and re-resolve exact partition devices.
- Do not format root or raw swap here.
- Retain the existing guided-coexistence preservation evidence and power-cut
  boundaries.

### 4. `ProvisionRootEncryptionStep` (conditional, destructive)

- Run `cryptsetup luksFormat` on the exact root partition using the user
  passphrase from anonymous IPC.
- Select explicit, reviewed LUKS2 metadata and KDF policy rather than relying
  forever on changing tool defaults.
- Add the recovery key to a different permanent keyslot.
- Verify the expected LUKS UUID, format, keyslot state, and metadata.
- Test both the user passphrase and recovery key before filesystem creation.
- Open exactly one deterministic mapping, such as
  `/dev/mapper/anduinos-root`.
- Verify that the mapping's backing device is the expected root partition.
- Discard the passphrase from executor memory after immediate verification.
- Record only non-secret facts needed for cleanup, such as mapper name and
  LUKS UUID.

Prefer `cryptsetup open --test-passphrase` or an equivalently non-mounting
credential check. Never infer success merely because `luksAddKey` exited zero.

### 5. `FormatFilesystemsStep` (destructive)

- Format the ESP only when the storage plan authorizes formatting it.
- With encryption enabled, run `mkfs.btrfs` or `mkfs.ext4` on the mapper, never
  on the raw root partition.
- With encryption disabled, retain the existing direct format path.
- For random-key encrypted swap, do not leave a plaintext swap signature on
  the raw partition. The mapped device will be initialized as swap at boot.
- Verify the raw root reports `crypto_LUKS` and the mapper reports the selected
  inner filesystem.
- Verify the raw swap partition is not activated directly.

### 6. Existing mount, copy, and target-configuration steps

- `MountTargetStep` must consume the logical root block device selected by the
  storage layer: mapper when encrypted, raw partition when unencrypted.
- Mount the ESP at `/target/boot/efi`; `/target/boot` itself must remain part of
  the logical root filesystem (`@root` for Btrfs).
- Btrfs subvolumes are created inside the mapper exactly as today.
- Compression mount options must derive from the typed plan instead of the
  currently hard-coded `compress=zstd` property.
- ext4 continues to use its ext4-specific mount options.
- Copy and system configuration should not need to know whether the block
  device below the filesystem is encrypted.

### 7. `ConfigureEncryptedStorageStep` (conditional)

- Write `/etc/crypttab` using the stable LUKS UUID, the canonical mapper name,
  and the explicitly approved UKI-contained machine-credential path. Never
  embed the human passphrase or recovery key.
- Configure volatile random-key dm-crypt swap using a stable partition
  identity such as PARTUUID and a canonical swap mapper name.
- Configure `/etc/fstab` to use the mapped swap device, not the raw partition.
- Keep only the ESP as the nested `/boot/efi` mount. Do not generate an fstab
  entry for a separate `/boot`.
- Ensure boot-time swap setup initializes the mapped device before `swapon`.
- Preserve current zram policy and disk-swap priority.
- Install/retain `cryptsetup-initramfs` or the distribution-approved equivalent
  in the target.
- Generate the initramfs only after crypttab, keyboard, and required hooks are
  complete.
- Verify the generated files contain no passphrase or recovery key.

The exact crypttab options, cipher policy, discard policy, and initramfs hooks
must be constants owned and validated by the backend. They must not be
free-form frontend input.

### 8. `ConfigureEncryptedBootStep` (conditional, release-blocking)

This step must implement exactly one approved branch; the archive does not
authorize silently combining both designs.

For Branch A:

- Install only the official signed shim and stock signed Ubuntu GRUB.
- Publish the minimal external configuration plus the active detached LUKS1
  compatibility header and separately sanitized rescue header.
- Keep the signed kernel and ordinary initramfs inside root. State explicitly
  that external policy and classic initramfs are not authenticated against an
  evil-maid attacker.

For Branch B, only after G9 passes:

- Build a machine-MOK-signed, self-contained GRUB image for the ESP with the
  exact partition, cryptodisk, LUKS2, cipher/KDF, Btrfs/ext4, and
  signature-verification modules required before root is available.
- Embed a constrained configuration that can show the operating-system menu,
  bind the expected LUKS UUID, unlock only after AnduinOS is selected, and load
  only the approved root-owned UKI. Do not trust arbitrary external policy.
- Build a MOK-signed UKI inside `/boot` on root. It must contain the matching
  kernel, initramfs, command line, and narrowly scoped machine credential.

For either branch:

- Enroll the high-entropy machine credential in a dedicated LUKS2 keyslot. A
  future TPM protector may seal that credential for GRUB; the plaintext form
  must never be stored on the ESP.
- Verify that the initramfs/UKI and all cleartext copies of its embedded machine
  credential remain inside encrypted root. Snapshot export and backup paths
  must not leak the credential.
- Rebuild initramfs and the branch-specific boot artifacts in the correct order.
- Verify every installed kernel has a matching usable initramfs and boot entry.
- Verify the exact promised Secure Boot boundary. Branch A must rerun the G7
  negative control and preserve its documented exclusion; Branch B must prove
  that shim trusts GRUB and GRUB admits only the signed UKI.
- Verify the chosen boot path on both amd64 and arm64; arm64 is UEFI-only in
  the current release layout.
- Never claim success from file presence alone. The resulting VM must boot
  from cold power-on and unlock the installed system.

### 9. Unmount and `FinalizeEncryptionStep` (conditional)

- Deactivate only the target's mapped swap if it was activated for testing.
- Unmount the ESP and Btrfs subvolumes/ext4 in reverse dependency order.
- Close the root mapping.
- Verify it is actually absent from device mapper.
- Re-open the container with a permanent credential, verify the inner
  filesystem identity read-only, close it again, and leave no target mapping
  active.
- Verify the raw root cannot be mounted as Btrfs/ext4 and remains identified as
  LUKS2.
- Verify the raw swap contains no persistent plaintext swap signature.
- Produce a LUKS header backup only under an explicitly designed export flow;
  do not silently leave it on the ESP, Live filesystem, or encrypted target.

Final unlock verification creates a credential-lifetime choice. Keeping the
passphrase in frontend or executor memory for the entire installation is
undesirable. The preferred behavior is to request the passphrase again at the
final verification screen, or let the user re-enter the saved recovery key.
This UX must be finalized before implementation; early verification alone
must not be mislabeled as post-install cold-unlock verification.

### 10. Failure cleanup

Every step after `luksFormat` must have idempotent, target-specific cleanup:

- unmount only known target mounts;
- deactivate only the selected target's swap mapping;
- close only the mapper created by this executor;
- do not run broad commands such as `swapoff -a`;
- never remove an unknown pre-existing mapper with the same name;
- preserve enough non-secret state to explain the failure and resume safe
  diagnosis;
- do not print cryptsetup input or include it in command traces;
- distinguish “disk was never modified,” “LUKS header exists but installation
  failed,” and “filesystem was created but system is incomplete.”

Power-cut testing must surround partitioning, `luksFormat`, recovery-key
addition, filesystem formatting, crypttab generation, initramfs generation,
bootloader writes, unmount, and mapper close.

## Boot-chain design history and experiment gates

### Why the classic Ubuntu layout was considered

The first pragmatic proposal copied Ubuntu classic passphrase-encrypted boot
boundaries: unencrypted ESP, separate unencrypted ext4 `/boot`, and an
initramfs prompt that opens encrypted root. This avoids GRUB LUKS/KDF support,
avoids a second prompt, and permits Argon2id without bootloader constraints.

AnduinOS would still have differed below that boundary by using direct
LUKS2 -> Btrfs/ext4, a dedicated swap partition, and no LVM or swapfile.

### Why the separate `/boot` proposal was rejected

AnduinOS promises Btrfs system rollback. A root snapshot taken before a kernel
upgrade contains the old package database, `/usr/lib/modules`, DKMS state, and
userspace. A separate `/boot` would continue to default to the new kernel and
initramfs after root rollback. This can produce missing modules, unsigned or
mismatched DKMS drivers, and package/boot state disagreement.

A deployment manager could version external kernels and map every Btrfs
snapshot to a boot artifact set, but that creates a large cross-filesystem
state machine, reference-aware old-kernel garbage collection, incomplete-update
states, and a permanently accumulating `/boot`. Disabling system rollback is
not acceptable. Therefore the external `/boot` design is retained only as
decision history and must not be implemented.

Traditional Ubuntu does not prove this safe for AnduinOS: classic Ubuntu does
not offer the same atomic, bootable Btrfs root-snapshot rollback contract.

### Windows/BitLocker insight

BitLocker uses an unencrypted system partition for a small trusted pre-boot
environment and keeps the real Windows boot image and operating system inside
the encrypted OS volume. The boot manager obtains the volume key from TPM/PIN,
a startup key, or a long recovery password, then loads the OS from the unlocked
volume. It does not keep every historical Windows kernel on a separate boot
filesystem.

The analogous AnduinOS design keeps only shim and a self-contained GRUB
unlocker on the ESP. GRUB shows the operating-system menu before touching the
AnduinOS LUKS container. Selecting Windows chainloads Windows without an
AnduinOS prompt. Selecting AnduinOS unlocks root, then loads the root-owned
signed UKI.

### Branch B candidate: authenticated Windows-like AnduinOS design

```text
UEFI firmware
  -> verify signed shim on ESP
  -> shim verifies self-contained signed GRUB on ESP
  -> GRUB shows AnduinOS / Windows / other-system menu
  -> user selects AnduinOS
  -> GRUB obtains a LUKS credential:
       normal future path: TPM2 unseals a high-entropy machine key
       recovery path: user enters a high-entropy recovery key
       optional password path: user enters a supported human passphrase
  -> GRUB cryptomounts LUKS2 and reads Btrfs/ext4 root
  -> GRUB verifies and loads the root-owned signed UKI
  -> UKI initramfs uses its encrypted, signed-in machine credential
     to reopen the same LUKS2 root without another prompt
  -> initramfs mounts Btrfs @root or ext4 /
  -> systemd configures random-key encrypted swap
```

The signed GRUB image must contain every module needed before root exists:
partition parsing, cryptodisk, LUKS2, the exact cipher and KDF, Btrfs/ext4,
signature enforcement, and TPM2 key protection when enabled. Its minimal
configuration must be embedded or independently authenticated. The LUKS UUID
and expected root path are data, but an attacker must not be able to replace
the menu/configuration and cause unsigned code to run.

The UKI lives inside `/boot` on root. For Btrfs, it is therefore inside
`@root`. A root snapshot carries the matching signed UKI, initramfs, kernel
command line, `/usr/lib/modules`, DKMS artifacts, package database, and
userspace. Promoting or rolling back `@root` changes these together without an
external per-kernel deployment database.

The ESP still has state, but only stable pre-boot components: shim, GRUB, a
minimal trusted configuration, and an optional TPM-sealed blob. Shim/GRUB
updates require atomic A/B-style handling and backward compatibility, but this
is much smaller than retaining every kernel for every root snapshot.

### One-prompt handoff

GRUB's decrypted device mapping is not inherited by Linux, and there is no
current standard secure handoff of that mapping or the human passphrase. The
candidate design therefore uses a separate high-entropy machine credential:

- enroll it in a dedicated LUKS2 keyslot;
- seal a copy to TPM for GRUB auto-unlock when TPM support is enabled;
- place the clear machine credential only inside the signed UKI stored on the
  encrypted root;
- let the UKI initramfs use it to reopen the same root silently;
- never put the clear machine credential on the ESP;
- treat every exported UKI or root snapshot as sensitive key material and
  require encrypted export.

This yields zero prompts on a successful TPM boot and one prompt on a manual
password/recovery boot. It does not forward or retain the human-entered secret.

### Branch B human-passphrase KDF decision

GNU GRUB 2.14 supports LUKS2 `cryptomount` but documents PBKDF2 only, not
Argon2. This is not important for a random 256-bit machine key or a sufficiently
high-entropy recovery key. It is important for a human-memorable passphrase,
because any GRUB-readable PBKDF2 keyslot becomes the easiest offline attack
target even if another Argon2id slot exists.

Do not resolve this silently. The allowed outcomes are:

- ship TPM + high-entropy recovery unlock first and defer ordinary passwords;
- accept a carefully benchmarked PBKDF2 password profile with an explicit
  security/UX decision;
- add and maintain reviewed Argon2 support in the trusted GRUB build;
- keep password-only encryption unavailable on unsupported hardware.

### Secure Boot requirements

Decrypting Btrfs does not break Secure Boot. The chain passes only when:

- firmware verifies shim;
- shim verifies the exact GRUB image;
- all pre-root GRUB modules are built into that signed image or independently
  verified under lockdown;
- GRUB verifies the UKI signature using the approved trust path;
- the UKI kernel enforces signed kernel modules and target-owned DKMS policy;
- recovery/menu paths cannot disable verification or load an unsigned image.

A classic Ubuntu Secure Boot path does not independently validate an ordinary
initramfs. G7 proved this is exploitable through the unencrypted external GRUB
configuration. A signed UKI is necessary to cover its kernel, initramfs, and
command line, but not sufficient while an untrusted policy can select another
signed kernel. An integrity-qualified design therefore also needs a trusted
constrained GRUB/configuration; otherwise its published threat model must be
limited to Ubuntu-classic encryption at rest and explicitly exclude evil-maid
boot integrity.

### Experiment gates before architecture approval

All experiments use disposable QEMU images. No host block device may be
attached.

- [x] **G1 — GRUB cryptodisk, amd64/Btrfs:** GRUB 2.14 opens the exact
      encrypted-root profile and reads Btrfs `@root`. ext4 parity remains a
      separate filesystem qualification test.
- [x] **G2 — certificate-free Secure Boot, amd64:** OVMF Secure Boot accepts
      Ubuntu's stock signed shim/GRUB, rejects an unsigned substitute, and the
      trusted LUKS1-detached-header plus Btrfs modules work in lockdown.
- [x] **G3 — signed kernel from encrypted root, amd64/Btrfs:** stock GRUB loads
      a Canonical-signed kernel and root-owned initramfs from encrypted
      `@root`. This is mechanism evidence, not an integrity acceptance gate.
- [x] **G4 — silent reopen, amd64/Btrfs:** the encrypted initramfs reopens the
      main LUKS2 container using only its internal machine credential and never
      prompts a second time.
- [x] **G5 — rollback consistency, amd64/Btrfs:** switching/promoting a Btrfs
      root snapshot boots that snapshot's matching kernel, initramfs,
      configuration, and modules across a real kernel upgrade while the ESP is
      byte-identical.
- [x] **G6 — boot-header recovery, amd64/Btrfs:** password rotation is
      transactional; after deliberately corrupting the active compatibility
      header, the sanitized rescue header accepts the recovery key and boots
      with exactly one prompt. Recovery rotation boundaries and total main
      LUKS2 metadata destruction also recover.
- [x] **G7 — untrusted ESP negative control:** an offline ESP-only attacker can
      inject kernel arguments and unsigned initramfs code despite enforcing
      Secure Boot. This rejects any claim that stock external GRUB policy is a
      complete verified-boot chain.
- [x] **G8 — signed UKI mechanism:** GRUB chainloads a trusted UKI from
      encrypted `@root`; systemd-stub uses its signed embedded command line and
      rejects an external override. This does not by itself close G7.
- [ ] **G9 — production machine-MOK policy:** the installer-generated MOK is
      enrolled through the existing MokManager flow, trusts a constrained
      self-contained GRUB and root-owned UKI, rejects unsigned substitutes,
      and has a documented cancellation/recovery path. The first builder
      reached Secure Boot and generated its keys/artifacts, then failed before
      enrollment because `chainloader` was used as a module name instead of
      `chain`. The source correction is archived but has not been rerun.
- [ ] **G10 — TPM2:** GRUB TPM2 key protection under `swtpm` unlocks normally,
      PCR mismatch falls back to recovery, and no clear machine key exists on
      the ESP.
- [ ] **G11 — update safety:** interrupted UKI, shim, GRUB, and TPM-policy updates
      retain at least one verified boot path and never strand all credentials.
- [ ] **G12 — architecture parity:** the approved chain passes on amd64/OVMF and
      arm64/AAVMF or qualified real arm64 hardware.

The checked gates prove the amd64/Btrfs mechanism, not release qualification.
ext4, arm64, torn-write fault injection, production MOK trust, update safety,
and the final integrity threat-model decision remain open. Do not paper over a
failed remaining gate with an external deployment manager, a signing
certificate the project cannot obtain, or disabled rollback.

### Experiment journal

#### 2026-08-09 — G1 amd64 Btrfs path passed

A disposable QEMU/KVM experiment cold-booted a 4 GiB qcow2 disk under OVMF.
The disk contained an unencrypted FAT ESP and a LUKS2 root formatted with the
PBKDF2 profile required by upstream GRUB 2.14. Btrfs and `@root` were created
inside the mapper. A self-contained GRUB 2.14 EFI image on the ESP accepted one
test credential, opened the exact LUKS UUID, and loaded
`/@root/boot/grub/grub.cfg` from the encrypted filesystem.

Observed serial markers, in order:

```text
ANDUINOS_G1_GRUB_REACHED
Enter passphrase for hd0,gpt2 (...)
Slot "0" opened
ANDUINOS_G1_LUKS_BTRFS_OK
```

The official Ubuntu 26.04 builder image had SHA-256
`9dc7c5363c0146a08ba0c9aa834d82c2c6dfbb1c471ad9a2f0aba1189e21be05`.
The successful run retained a serial log and synthetic-disk evidence beneath
`/tmp/anduinos-luks-g1.lKuajN`; `/tmp` is disposable, so the reproducible source
of truth is [`tests/vm/luks-grub-btrfs`](tests/vm/luks-grub-btrfs/).

This result establishes only the amd64/Btrfs half of G1. It does **not** yet
establish ext4, Secure Boot, signed-UKI loading, Linux's silent reopen, rollback,
TPM behavior, update safety, or arm64 parity. Two earlier harness runs failed
before boot testing because an overlong FAT label was rejected and because an
ESP directory was created before the mount hid it. Those were corrected
fixture-construction defects, not negative boot-chain results.

#### 2026-08-09 — G2 mechanism passed; Ubuntu's stock signed GRUB is blocked

The first Secure Boot probe used OVMF with Microsoft keys enrolled. Its
unsigned GRUB control was rejected by firmware with `Access Denied`, while
Microsoft-signed shim and Canonical-signed GRUB reached the experiment's GRUB
configuration. This established that the virtual Secure Boot policy was
actually enforcing signatures rather than merely setting a UI flag.

Ubuntu 26.04's signed GRUB then failed before the password prompt:

```text
ANDUINOS_G2_SIGNED_GRUB_REACHED
error: file `/EFI/ubuntu/x86_64-efi/luks2.mod' not found.
error: no such cryptodisk found, perhaps a needed disk or cryptodisk module is
not loaded.
```

The signed image contains `cryptodisk`, but its trusted built-in module set does
not contain `luks2`. Under shim-lock, copying an ordinary `luks2.mod` to the ESP
is not an acceptable fix: dynamically adding unsigned bootloader code would
defeat the trust chain. Therefore **the current Ubuntu `grub-efi-*-signed`
package cannot implement this design as shipped**.

A second probe used OVMF's disposable enrolled test certificate. The same
firmware policy rejected an unsigned control, accepted a custom self-contained
GRUB 2.14 signed by the enrolled certificate, displayed one passphrase prompt,
opened LUKS2, and read the Btrfs `@root` configuration. The observed positive
markers were:

```text
ANDUINOS_G2_CUSTOM_SIGNED_GRUB_REACHED
Enter passphrase for hd0,gpt2 (...)
Slot "0" opened
ANDUINOS_G1_LUKS_BTRFS_OK
```

This proves the Secure Boot mechanism but does not complete production G2.
AnduinOS must choose and qualify a distributable trust path for a GRUB image
that embeds every required module and immutable early configuration. Plausible
paths are:

1. obtain a distribution-signed GRUB build whose signed module set includes
   the exact cryptodisk, LUKS2, crypto, Btrfs/ext4, TPM, and verification code;
2. ship a custom shim whose embedded vendor certificate trusts an AnduinOS
   signing key and have that shim accepted by ordinary Secure Boot firmware;
3. sign the self-contained GRUB with a machine MOK and design a safe first-boot
   enrollment path that works before encrypted root can be opened.

Option 3 interacts with [`SECURE-BOOT-DESIGN.md`](SECURE-BOOT-DESIGN.md): the
current MOK design uses the machine key for DKMS modules after Canonical-signed
GRUB has started. Using it to authenticate GRUB itself changes first-boot and
recovery ordering and must not be assumed to work without a dedicated VM and
real-hardware experiment.

Reproducers are in
[`tests/vm/luks-grub-btrfs`](tests/vm/luks-grub-btrfs/). The successful custom
run retained disposable evidence at `/tmp/anduinos-luks-g2-custom.w5nZ7Z`.
Production G2 remains unchecked until one of the real distribution trust paths,
not the OVMF snake-oil certificate, passes.

#### 2026-08-09 — certificate-free G2 compatibility path passed

The previous result blocks direct LUKS2 access, not all encrypted-root access.
Ubuntu's signed GRUB does contain its trusted LUKS1 module, and GRUB 2.14
supports detached LUKS headers. A third disposable experiment therefore kept
the real root as LUKS2 but placed a detached LUKS1 compatibility header on the
ESP. Both headers used the same 512-bit random volume key, AES-XTS cipher, and
32,768-sector data offset. The compatibility header contains encrypted keyslot
metadata, not the volume key in plaintext.

Linux first proved that the LUKS1 header opened and mounted the existing LUKS2
data area. OVMF with Microsoft keys then rejected an unsigned control image,
accepted Ubuntu's Microsoft-signed shim and Canonical-signed GRUB, and GRUB used
the compatibility header to read Btrfs `@root`:

```text
BdsDxe: ... Access Denied -- rejected probably by Secure Boot
ANDUINOS_G2_DUAL_HEADER_SIGNED_GRUB_REACHED
Enter passphrase for hd0,gpt2 (...)
Slot 0 opened
ANDUINOS_G1_LUKS_BTRFS_OK
```

This path requires no AnduinOS certificate, custom shim, custom trusted GRUB,
MOK, firmware key enrollment, or disabled Secure Boot. Evidence is retained at
`/tmp/anduinos-luks-g2-dual-header.yXvvoa`, and the reproducer is
`run-g2-dual-header.sh` in the experiment directory.

This is now the leading no-MOK candidate, but a successful boot alone does not
approve the dual-header lifecycle. The key model and transactional update rules
below are mandatory if the candidate survives the remaining audit.

#### 2026-08-09 — G3/G4 signed-kernel and silent-reopen path passed

The next experiment stored Ubuntu's Canonical-signed kernel and generated
initramfs inside encrypted Btrfs `@root`. The initramfs contained a random
64-byte machine key enrolled only in the main LUKS2 header. Under enforcing
Secure Boot, the official GRUB loaded the signed kernel after the single LUKS1
prompt. The kernel reported Secure Boot enabled and lockdown active. The
initramfs then opened the main LUKS2 header, mounted `@root`, and powered off
without another human prompt:

```text
Enter passphrase for hd0,gpt2 (...)
Slot 0 opened
ANDUINOS_G3_SIGNED_KERNEL_LOADING
secureboot: Secure boot enabled
Kernel is locked down from EFI Secure Boot mode
ANDUINOS_G4_SILENT_LUKS2_REOPEN_OK
```

Evidence is retained at `/tmp/anduinos-luks-g3-linux-initrd.6mLeM5`. This proves
the certificate-free one-prompt mechanism. It does not give the initramfs a
separate signature: the kernel is verified by Secure Boot, while the initramfs
is protected by being stored inside the encrypted root. This matches Ubuntu's
traditional signed-kernel/unsigned-initramfs boundary, with the important
improvement that AnduinOS does not expose initramfs on an unencrypted `/boot`.
This result originally made a MOK-signed UKI look optional. G7 later disproved
that interpretation for an evil-maid threat model: the certificate-free path
is still usable for encryption at rest, but a signed UKI plus an authenticated
loader policy is required before claiming full verified-boot integrity.

#### 2026-08-09 — G5 rollback across a real kernel update passed

A state-A snapshot contained signed kernel `7.0.0-28` and its initramfs inside
`@root`. The experiment installed signed kernel `7.0.0-29`, generated a new
matching initramfs, changed the root-owned GRUB configuration, and cold-booted
state B under Secure Boot. It then replaced `@root` with the read-only state-A
snapshot and cold-booted kernel `7.0.0-28` with its old initramfs and
configuration. The complete ESP partition remained byte-identical across the
update and rollback:

```text
state B: ANDUINOS_G5_STATE_B_KERNEL=7.0.0-29-generic
state A after rollback: Linux version 7.0.0-28-generic
ESP SHA-256: ad42832455f7c424624be7ecac40d5278429613b3d25ef53acf5f4861b41d36c
```

Evidence is retained at `/tmp/anduinos-luks-g5-rollback.l5ZSuK`. This directly
disproves the external-`/boot` mismatch for the candidate: the kernel,
initramfs, GRUB configuration, modules, and userspace can share the same
snapshot transaction while the ESP remains stable.

#### 2026-08-09 — G6 password rotation and previous-header recovery passed

The lifecycle experiment first enrolled the same high-entropy recovery
credential in the main LUKS2 header and detached LUKS1 header. It copied the
verified current boot header to `previous` and `next`, changed the human
password only in `next`, verified the new password and recovery key, verified
that the old password failed, fsynced, and atomically renamed `next` to the
active filename. The raw encrypted root partition had the same SHA-256 before
and after the operation:

```text
6f729e04e0fd9bb7d835d905cd1ede81a9f7b4a6dd0fb5b6242064c415613afa
```

A cold boot under enforcing Secure Boot opened keyslot 2 with the new password
and reached `ANDUINOS_G4_SILENT_LUKS2_REOPEN_OK`. The experiment then corrupted
the first 4 KiB of the active header, selected the retained previous header,
and cold-booted it using the recovery key. GRUB reported `Slot 1 opened`, the
kernel reported Secure Boot enabled, and the same silent LUKS2 reopen marker
was reached.

The initial evidence at `/tmp/anduinos-luks-g6-header-lifecycle.KdcHHF`
retained a literal old active generation. The credential-revocation audit
rejected that detail, and the reproducer was changed to construct a sanitized
rescue header that explicitly rejects the user password. The corrected run at
`/tmp/anduinos-luks-g6-header-lifecycle.YMsSo9` proved that the new password
boots normally and, after active-header corruption, the rescue header boots
with only the recovery credential. The encrypted-root SHA-256 remained
unchanged. G6 therefore proves the final password-change and active-header
damage policy, not merely the superseded previous-generation policy.

#### 2026-08-09 — G6 recovery-key transaction boundaries passed

`run-g6-recovery-rotation.sh` exercised every durable phase of the two-header
recovery-key transaction. It verified these invariants before advancing:

```text
boundary 0: old recovery key opens active LUKS1 and main LUKS2
boundary 1: new key is in LUKS2 only; old still opens both paths
boundary 2: new boot header is staged; old active path still opens both
boundary 3: new header is active; old and new both open both paths
boundary 4: new opens both; old opens only LUKS2 and retained fallback
boundary 5: new opens both; old is retired from active LUKS1 and main LUKS2
```

The final new recovery key then cold-booted under enforcing Secure Boot and the
initramfs reopened root without a second prompt. Evidence is retained at
`/tmp/anduinos-luks-g6-recovery-rotation.4ywfbq`.

This proves logical interruption safety after each fsynced phase. It does not
yet simulate a torn cryptsetup metadata write or torn FAT directory update in
the middle of a phase; those remain release-qualification fault-injection
tests.

#### 2026-08-09 — G6 complete main-LUKS2-metadata destruction recovered

The disaster experiment embedded a sanitized-copy candidate header and the
random machine credential in the initramfs stored inside encrypted `@root`.
It then zeroed all 32,768 sectors (16 MiB) before the verified ciphertext data
offset. Both LUKS2 metadata copies and their keyslot area were destroyed;
`cryptsetup luksDump` rejected the partition. A Linux preflight nevertheless
opened and mounted the untouched Btrfs payload using the detached LUKS1 header.

On the subsequent cold boot, stock signed GRUB used the ESP compatibility
header to load the root-owned kernel and initramfs. The signed kernel reported
Secure Boot enabled. The initramfs deliberately failed its ordinary LUKS2
open, then used its encrypted embedded header plus machine key to mount the
same root:

```text
ANDUINOS_G6_MAIN_HEADER_RECOVERY_KERNEL_LOADING
secureboot: Secure boot enabled
ANDUINOS_G6_MAIN_LUKS2_HEADER_UNREADABLE
ANDUINOS_G6_EMBEDDED_HEADER_FALLBACK_OPENED
ANDUINOS_G6_MAIN_HEADER_RECOVERY_ROOT_MOUNTED
```

The first successful evidence at
`/tmp/anduinos-luks-g6-main-header-recovery.KAokag` used an unsanitized embedded
copy. The corrected builder removed the human-password slot and verified that
the embedded header accepts only its random machine credential before
destroying LUKS2 metadata. Its cold-boot recheck passed at
`/tmp/anduinos-luks-g6-main-header-recovery.npHrYb`. Earlier builder attempts
also exposed and fixed missing `initramfs.conf.d` and insufficient builder
temporary-space defects. Production must enter an explicit degraded/recovery
state and must never reconstruct or overwrite main LUKS2 metadata
automatically.

#### 2026-08-09 — G7 untrusted-ESP integrity attack succeeded

An offline builder changed only files on the unencrypted ESP; it never opened
the encrypted root. Its replacement GRUB configuration used the legitimate
detached header and prompted for the normal test password, then loaded the
unchanged Canonical-signed kernel from encrypted `@root` with an injected
kernel argument. Under enforcing Secure Boot the kernel accepted that
argument.

The stronger control added a gzip-compressed unsigned initramfs archive from
the ESP after the legitimate encrypted initramfs. It replaced a known
`local-bottom` script and executed this marker from the injected archive:

```text
ANDUINOS_G7_UNSIGNED_ESP_INITRAMFS_CODE_EXECUTED
```

The first raw-newc attempt did not execute because the test archive format did
not match the multi-initrd path; the corrected compressed archive did. Final
evidence is retained at `/tmp/anduinos-luks-g7-esp-tamper.1i5dEk`.

This is not a LUKS confidentiality failure before unlock. It is a decisive
verified-boot failure after a user follows the malicious prompt: stock signed
GRUB executes untrusted external policy and permits an unsigned classic
initramfs beside a signed kernel. A signed UKI alone cannot prevent that
policy from selecting another publicly signed kernel and appended initramfs.

#### 2026-08-09 — G8 signed-UKI mechanism passed but does not close G7 alone

The next experiment retained the stock distribution GRUB binary and module
set, built a UKI containing the kernel, initramfs, and embedded command line in
encrypted `@root`, and signed it with the disposable OVMF fixture key. GRUB
chainloaded the trusted UKI through the same one-prompt LUKS1/LUKS2 path. The
external GRUB configuration passed a hostile invocation argument, but
systemd-stub ignored it under Secure Boot and used the signed embedded command
line:

```text
ANDUINOS_G8_CHAINLOADING_SIGNED_UKI_WITH_UNTRUSTED_ARGUMENT
secureboot: Secure boot enabled
anduinos.g8-embedded-cmdline=1
ANDUINOS_G4_SILENT_LUKS2_REOPEN_OK
```

Evidence is retained at `/tmp/anduinos-luks-g8-signed-uki.T6yyRq`. This proves
the UKI mechanism and matches upstream systemd-stub semantics. The OVMF
snake-oil key is not a production trust path. More importantly, G8 protects a
selected UKI but does not authenticate the external GRUB policy that selects
it. Closing G7 requires a trusted constrained loader/configuration, most
practically a self-contained GRUB signed by the machine-local MOK that this
installer already creates for Secure Boot and DKMS.

#### 2026-08-09 — G9 machine-MOK builder started but did not reach enrollment

The final experiment began testing the only identified certificate-free route
to authenticate loader policy rather than merely keeping Secure Boot enabled.
It booted the builder under Microsoft-key OVMF, observed `SecureBoot enabled`,
generated a machine-local MOK, and generated a MOK-signed UKI. It then failed
while building the constrained GRUB image because the module list named
`chainloader`; on this Ubuntu image the `chainloader` command is implemented by
`/usr/lib/grub/x86_64-efi/chain.mod`.

Evidence from the incomplete run is retained at
`/tmp/anduinos-luks-g9-machine-mok.OeozCK`. The reproducer now names `chain`,
but no post-correction VM run was performed. There is therefore no evidence yet
that MokManager enrollment was queued or completed, that shim accepted the
MOK-signed GRUB, that the constrained GRUB unlocked LUKS2 and booted the UKI,
or that unsigned substitutes and cancellation/recovery cases behave safely.
G9 must remain unchecked.

### Leading candidate key model and maintenance audit

The final partition topology remains simple: ESP, dedicated volatile-encrypted
swap, and LUKS2 root. The LUKS1 object is a small compatibility metadata file on
the existing ESP, not another partition or filesystem.

The candidate should avoid mirroring the ordinary user password into both
headers:

| Credential | Detached LUKS1 boot header | Main LUKS2 header | Purpose |
|---|---:|---:|---|
| User password | yes, PBKDF2 | no | one manual GRUB prompt |
| Recovery key | yes | yes | boot recovery and live-media root recovery |
| Machine reopen key | yes | yes | initramfs reopen; future TPM input |

Omitting the user password from LUKS2 makes normal password changes a
single-header operation. The main LUKS2 header remains independently
recoverable using the high-entropy recovery key. The machine key in both
headers lets a trusted recovery initramfs use the compatibility header if the
main LUKS2 metadata is damaged; it is never plaintext on the ESP.

PBKDF2 for the human password is an unavoidable weakness of upstream GRUB 2.14,
whether it opens LUKS1 or a PBKDF2 LUKS2 keyslot. Use a high calibrated
iteration count, require a strong password, and state the limitation honestly.
The recovery and machine credentials are random high-entropy values, so their
PBKDF choice does not create a dictionary attack.

Provisioning must generate the volume key and machine key inside the privileged
executor and expose them only through sealed memfd/anonymous descriptors or the
libcryptsetup API. The successful proof dumped a test volume key into guest
tmpfs to modify an already-created fixture; production must instead generate
the volume key once and feed both header-creation operations without a regular
file, argv, environment variable, plan field, or log entry.

Password changes modify a copied next-generation LUKS1 header, verify the new
password plus recovery and machine credentials, fsync it, and only then switch
the active ESP filename. Do **not** retain the old active header because it
retains the old user password. Keep a separately constructed rescue header
containing only recovery and machine slots; do not automatically ask the user
for multiple passwords.

Recovery-key rotation is a two-header transaction:

1. add and verify the new key in the main LUKS2 header;
2. add and verify it in a next-generation LUKS1 header;
3. publish and confirm the new recovery key;
4. activate the new boot-header generation;
5. remove the old key from LUKS1, then LUKS2, verifying after each step.

At every interruption boundary either the old or new recovery key must still
open both paths. A non-secret transaction journal records header generations
and completed phases, never key material. Machine-key rotation similarly keeps
the old key until a cold boot with the new encrypted initramfs succeeds.

Every ordinary initramfs stored in root must contain a copy of the sanitized
rescue header as well as its machine credential. This copy rolls back with the
kernel and initramfs and is used only if the main LUKS2 metadata is unreadable.
It must contain no human-password slot. Main-header recovery is boot-only and
read-only by default; metadata repair requires an explicit recovery workflow.

Like every LUKS header backup, a historical detached header plus its historical
credential can unwrap the unchanged volume key forever. No keyslot deletion
can revoke an attacker who previously copied that header; true revocation
against such an attacker requires rotating the data volume key with full
reencryption. The installer and recovery documentation must state this
standard LUKS limitation. On the live ESP, deleting the old active header and
retaining only the sanitized rescue header prevents an ordinary old user
password from remaining visibly usable after a password change.

The remaining audit must experimentally cover the sanitized rescue-header
policy, torn metadata/FAT writes, malicious or stale ESP configuration, and
the signed-kernel/unsigned-initramfs integrity boundary. Until those pass, the
dual-header design is leading but not approved.

## Swap and hibernation

### First milestone behavior

The dedicated swap partition remains sized by the existing dynamic policy,
which may reserve approximately RAM plus 1 GiB when space allows. That sizing
keeps future hibernation possible without repartitioning, but it does not
enable hibernation.

At each normal boot:

1. The system creates a fresh high-entropy random key.
2. It opens a plain dm-crypt mapping over the swap partition.
3. It initializes and activates swap on the mapping.
4. The key exists only for that boot.

After shutdown, old swap ciphertext may remain on disk, but the old key is
gone. This has low operational overhead and should normally be declarative via
crypttab/systemd integration rather than custom initrd scripts.

### Consequences

- Swap does not ask for another password.
- Root and swap are both protected at rest.
- Resume from hibernation is impossible because the old swap key is not
  available on the next boot.
- Suspend-to-idle is unaffected.
- The UI and release notes must not advertise hibernation or hybrid sleep.

Future hibernation requires a separately approved persistent encrypted resume
target, initramfs resume configuration, Secure Boot/lockdown qualification,
memory-size checks, kernel-update tests, and a decision about key management.
That future design must use explicit partitions and approved key management,
not LVM, and must not weaken first-milestone swap confidentiality.

## Filesystem-specific behavior

### Btrfs

- Preserve the approved subvolume ABI.
- Keep `@home`, logs, snapshots, containers, and VM images outside system-root
  rollback as already documented.
- Apply one typed compression policy consistently across the filesystem;
  Btrfs mount options are not independently configurable per subvolume.
- Ensure Disk Snapshots Manager is installed and works normally after unlock.
- Test system rollback with encrypted root and the chosen boot-artifact design.
- Test home snapshot browsing and file restoration; LUKS should be transparent
  after the system is unlocked.

### ext4

- Format and mount ext4 inside the same root mapper.
- Do not install or advertise Btrfs snapshot/rollback functionality.
- Hide compression controls and reject compression in plan validation.
- Treat ext4 as a first-class encryption target, not as an accidental fallback.

The initial release gate may qualify erase-disk Btrfs first and keep ext4
encryption hidden until its matrix passes. However, the implementation should
share the encryption architecture from day one so ext4 does not require a
second secret protocol or a second boot design.

## TPM2 and FIDO2: future convenience unlock, not a third layout

TPM2/FIDO2 enrollment is deliberately deferred until passphrase and recovery
flows are automated and proven. When added:

- Enrollment adds a convenience unlock path; it never removes the human
  passphrase or offline recovery key.
- Firmware updates, PCR changes, Secure Boot key changes, motherboard
  replacement, and TPM clearing must fall back cleanly to recovery.
- The installer must explain what changes can invalidate automatic unlock.
- TPM enrollment state is not a generic encryption boolean and needs its own
  typed policy and evidence.
- A VM test must deliberately change measured state and prove recovery unlock.

Do not model the product as three unrelated installer storage modes:

```text
A. unencrypted
B. passphrase-encrypted
C. TPM-encrypted
```

Mode C is not a different encryption format or disk topology. It is mode B's
same LUKS2 container with an additional TPM-bound unlock token/keyslot. The
human passphrase and offline recovery key remain enrolled.

The preferred product sequence is:

1. The installer initially offers only unencrypted installation or LUKS2 with
   passphrase and recovery key.
2. The newly installed encrypted system completes at least one proven cold
   boot and recovery check.
3. A separate first-boot or Device Security workflow offers **Enable automatic
   unlock with TPM** only on qualified UEFI, Secure Boot, and TPM2 hardware.
4. TPM enrollment adds convenience unlock to the existing LUKS2 device. It
   does not rewrite the filesystem or create a different storage layout.
5. If measured state changes or the TPM is unavailable, early boot falls back
   to the existing passphrase/recovery path.

Consequently, the first installer schema should contain only encryption policy,
not an `A/B/C` mode enumeration. A future typed auto-unlock policy belongs to
device-security enrollment and its own evidence. This keeps TPM qualification
from multiplying every destructive installer path while retaining the same
security and recovery requirements.

Do not design the first `EncryptionSpec` so narrowly that adding a typed
auto-unlock policy requires inserting a secret into `InstallPlan`.

## Secure Boot considerations

Disk encryption protects data at rest; Secure Boot protects the integrity of
the boot chain. Neither feature substitutes for the other.

The implementation must test:

- Secure Boot enabled, disabled, unsupported, and not applicable where valid;
- target-owned DKMS signing keys and third-party driver installation;
- kernel and initramfs updates after installation;
- recovery after MOK/PCR/firmware changes;
- no reuse of the AnduinOS MOK enrollment password as a disk credential;
- kernel lockdown interactions, especially before any future hibernation
  claim.

The current hard-coded MOK enrollment password is unrelated to LUKS and must
never be offered as a default disk passphrase.

## Test strategy and test farm

Encryption turns existing storage choices into a combinatorial release
matrix. For the first milestone, the visible binary dimensions alone are:

```text
Btrfs / ext4
× amd64 / arm64
× encrypted / unencrypted
× Secure Boot on / off
= 16 primary combinations
```

Future TPM auto-unlock is conditional on encryption. Treating it as a naive
independent binary dimension gives 32 combinations, while the valid state
model is better expressed as:

```text
2 filesystems
× 2 architectures
× 2 Secure Boot states
× (unencrypted / passphrase LUKS2 / LUKS2 with TPM convenience unlock)
= 24 valid primary states
```

The test farm should also respect the architectural boundary: installation of
the common passphrase-protected LUKS2 base is one suite, while post-install TPM
enrollment, automatic unlock, PCR change, and fallback are another. TPM still
requires amd64 and arm64 qualification, but it should not force every
filesystem, compression, guided-layout, and installer fault-injection case to
be rerun as if it were a separate partitioning implementation.

Btrfs compression, UEFI/legacy BIOS where valid, erase/guided modes, updates,
drivers, recovery paths, and fault injection multiply the real job count well
beyond those primary states. Do not equate one primary combination with one
complete test.

### Recommended farm layers

#### Pull-request smoke suite

Run approximately 6–8 representative jobs:

- amd64 Btrfs encrypted, Secure Boot on;
- amd64 ext4 encrypted, Secure Boot on;
- corresponding unencrypted regression paths;
- at least one Secure Boot-off encrypted path;
- arm64 Btrfs encrypted under AAVMF or real hardware;
- one recovery-key unlock and one intentionally wrong-passphrase case.

#### Nightly matrix

Run every supported primary combination plus:

- user passphrase unlock;
- recovery-key unlock;
- wrong passphrase and cancellation;
- kernel/initramfs update followed by cold boot;
- Btrfs rollback where applicable;
- random-key swap identity changes across reboots;
- install cleanup and reinstall on a previously failed image.

#### Release qualification

Add:

- power cuts at every new destructive boundary;
- corrupted or missing boot artifacts;
- LUKS header damage and documented recovery behavior;
- Secure Boot key and firmware state changes;
- TPM PCR changes once TPM support exists;
- real amd64 and arm64 hardware boot tests;
- guided coexistence fixtures and preserved-partition hashes;
- independent boot of preserved operating systems;
- low-disk-space, package-upgrade, DKMS, and rollback cases.

The farm should create disposable QEMU/KVM guests from clean images rather
than maintaining one VM per combination. Use OVMF/AAVMF firmware, `swtpm` for
future virtual TPM tests, native KVM for amd64, and preferably real arm64
workers for performance and firmware coverage. Retain serial logs, screenshots,
partition manifests, non-secret LUKS metadata summaries, boot measurements,
test results, and image hashes.

No destructive test may attach a host block device. A passing unit-test suite
or a single successful VM install is not a release gate.

## Suggested implementation sequence

- [x] Complete mechanism gates G1-G8 and record positive and negative evidence.
- [ ] Make the explicit product threat-model decision between archived Branch
      A and Branch B. Do not mix their claims or ship before choosing.
- [ ] If Branch A is selected, freeze the detached-header format, PBKDF2
      calibration, sanitized rescue-header policy, transaction journal, and
      honest evil-maid exclusion described in this document.
- [ ] If Branch B is selected, complete G9 before product work: prove real
      MokManager enrollment, the constrained self-contained GRUB module and
      configuration set, signed UKI boot, rejection tests, cancellation,
      recovery, key rotation, update safety, and rollback.
- [ ] For either branch, keep `/boot` inside root and define boot-artifact
      creation, verification, update, rollback, and cleanup without an external
      deployment catalogue.
- [ ] Define the high-entropy machine credential used by the root-owned
      initramfs/UKI to reopen root without a second human prompt. Specify
      enrollment, rotation, revocation, recovery, permissions, and artifact
      trust boundaries.
- [ ] Define the typed compression and encryption plan schema with no secrets.
- [ ] Extend strict plan, graph, and execution-policy validation.
- [ ] Build the storage settings page and recovery-key confirmation UX.
- [ ] Replace the one-shot executor protocol with tested duplex secret IPC.
- [ ] Split partitioning from formatting.
- [ ] Implement LUKS2 provisioning, two permanent credentials, and mapper
      ownership checks.
- [ ] Implement random-key encrypted swap configuration.
- [ ] Make mount/configuration code consume a logical root device.
- [ ] Make Btrfs compression plan-driven; reject it for ext4.
- [ ] Generate and verify crypttab, fstab, the branch-specific root boot
      artifacts, and the ESP bootloader.
- [ ] Add target-specific failure cleanup and post-install cold-unlock checks.
- [ ] Add unit tests for schemas, command planning, IPC, redaction, and cleanup.
- [ ] Build disposable amd64/arm64 VM jobs, Secure Boot fixtures, and rollback
      fixtures that cross a kernel update.
- [ ] Qualify erase-disk Btrfs encryption.
- [ ] Qualify erase-disk ext4 encryption.
- [ ] Qualify guided coexistence separately; do not inherit approval from
      erase-disk tests.
- [ ] Keep TPM2/FIDO2 and hibernation disabled until their separate gates pass.

## Minimum release acceptance criteria

An encrypted installation is not complete until all of the following are true:

- The plan and all retained evidence contain no encryption secret.
- The raw root partition is LUKS2 and the inner filesystem is exactly the
  selected Btrfs/ext4 filesystem.
- The ESP contains only the approved stable pre-boot components and contains
  no plaintext root unlock credential.
- `/boot` is part of the selected root filesystem. For Btrfs it is inside
  `@root`, and its kernel/initramfs or signed UKI rolls back atomically with
  modules and userspace.
- The user passphrase and recovery key occupy verified permanent unlock paths.
- The user has confirmed possession of the recovery key.
- The installed system cold-boots through the approved Secure Boot state.
- Manual passphrase or recovery-key boot asks exactly once after AnduinOS is
  selected, and the initramfs does not ask again. A future qualified TPM path
  may make normal boot promptless; TPM is not a first-release requirement.
- GRUB's selected pre-boot keyboard layout can enter every supported manual
  unlock credential.
- If the product claims evil-maid verified-boot integrity, the authenticated
  loader policy rejects unsigned or modified GRUB/UKI substitutes, and the
  signed UKI contains the approved initramfs machine-credential mechanism
  without exposing that credential on the ESP. If Branch A ships instead, the
  release criteria and documentation must explicitly exclude that claim.
- Random-key encrypted swap activates on boot and its key changes after reboot.
- The raw swap partition is never activated directly.
- Kernel/UKI updates remain bootable under Secure Boot and are crash-safe.
- Btrfs snapshot browsing and rollback work when Btrfs is selected, including
  rollback across a kernel update with the matching root-owned UKI.
- ext4 does not expose snapshot or compression features.
- Recovery-key unlock works after intentionally invalidating the normal path.
- Failed and interrupted installations leave no active target mapper or mount.
- Both amd64 and arm64 pass their approved real or virtual hardware gates.

## References

- [`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md), especially Storage 6 and release
  gates.
- [`BTRFS-DESIGN.md`](BTRFS-DESIGN.md), especially `/boot`, swap/hibernation,
  encryption/recovery, and rollback consistency.
- [cryptsetup-luksFormat(8)](https://gitlab.com/cryptsetup/cryptsetup/-/blob/33e26be58be852df80f945f328f5ee408a313563/man/cryptsetup-luksFormat.8.adoc)
- [cryptsetup-luksAddKey(8)](https://gitlab.com/cryptsetup/cryptsetup/-/blob/coverity_scan/man/cryptsetup-luksAddKey.8.adoc)
- [systemd crypttab](https://www.freedesktop.org/software/systemd/man/latest/crypttab.html)
- [GNU GRUB 2.14 `cryptomount`](https://www.gnu.org/software/grub/manual/grub/html_node/cryptomount.html)
- [GNU GRUB 2.14 TPM2 key protector](https://www.gnu.org/software/grub/manual/grub/html_node/TPM2-key-protector.html)
- [GNU GRUB Secure Boot and shim-lock behavior](https://www.gnu.org/software/grub/manual/grub/grub.html)
- [Debian cryptsetup initramfs keyfile guidance](https://cryptsetup-team.pages.debian.net/cryptsetup/README.initramfs.html#storing-keyfiles-directly-in-the-initramfs)
- [Debian encrypted `/boot` and the missing GRUB-to-Linux key handoff](https://cryptsetup-team.pages.debian.net/cryptsetup/encrypted-boot.html)
- [Ubuntu Secure Boot and UKI guidance](https://documentation.ubuntu.com/security/docs/security-features/platform-protections/secure-boot/)
- [Microsoft BitLocker FAQ: system and operating-system partitions](https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/faq)
- [Microsoft BitLocker recovery process](https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/recovery-process)
