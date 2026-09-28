# AnduinOS installer Secure Boot design

## Scope

Release one preserves Secure Boot on amd64 and arm64 UEFI systems. Legacy BIOS
has no Secure Boot state. Secure Boot support is a release blocker, not an
optional best-effort feature.

The implementation follows Ubuntu's Ubiquity/shim workflow:

- detect Secure Boot from the running firmware;
- generate a local MOK with `update-secureboot-policy --new-key`;
- install signed shim and signed GRUB;
- configure DKMS to use the same key;
- queue the certificate with `mokutil --import`;
- show MOKManager on reboot for physical-presence confirmation.

AnduinOS uses the documented one-time enrollment password `123456`.

## Differences from Ubiquity

Ubiquity generates a key in the live environment, queues it, and later copies
the MOK directory into `/target` without overwriting existing files.

The AnduinOS installer instead:

1. Copies the target filesystem.
2. Rejects any unmarked key pair inherited from the squashfs.
3. Generates a machine-local key directly inside the target.
4. Records the certificate digest so an interrupted installation reuses the
   same key rather than generating another pending enrollment.
5. Configures DKMS and rebuilds third-party modules.
6. Generates initramfs and installs signed boot artifacts.
7. Verifies every signed EFI executable.
8. Only then mutates firmware state by scheduling MOK enrollment.

This ordering prevents an incomplete installation from requesting trust for a
key whose target system or boot chain was never completed.

## State machine

Every UEFI installation (erase, coexistence and manual; amd64 and arm64)
installs the signed chain with `--uefi-secure-boot` and points the AnduinOS
NVRAM entry at shim, regardless of the current enforcement state. The MOK
state machine below is separate: when installed with enforcement off, users
can later prepare/enroll MOK in Driver Center before enabling Secure Boot.
OOBE no longer manages Secure Boot. A whole-disk erase of an externally
connected target additionally receives the direct portable fallback described
below; shared ESPs never do.

```text
Secure Boot disabled, unsupported by UEFI firmware, or Legacy BIOS
        |
        +-- no key generation, no MOK mutation

Secure Boot enabled
        |
        +-- verify shim-signed, signed GRUB, mokutil and OpenSSL payloads
        |
        +-- reuse installer-marked key, otherwise discard inherited key
        |
        +-- generate MOK.priv (0600) and MOK.der (0644)
        |
        +-- verify certificate public key matches private key
        |
        +-- write explicit DKMS signing configuration
        |
        +-- run dkms autoinstall when DKMS is installed
        |
        +-- update initramfs
        |
        +-- grub-install --uefi-secure-boot
        |
        +-- verify the signed AnduinOS vendor EFI chain
        |
        +-- if already enrolled: complete
        |
        +-- if the same certificate is pending: do not import again
        |
        +-- otherwise mokutil --import via stdin password
        |
        +-- mokutil --timeout -1
```

`mokutil --sb-state` is parsed in the C locale. `SecureBoot enabled`,
`SecureBoot disabled`, and `This system doesn't support Secure Boot` are three
distinct explicit outcomes. Missing, malformed, or contradictory output is
indeterminate and stops the plan before destructive work.

Enrollment and pending state use an exact SHA-1 fingerprint match against the
full `mokutil --list-enrolled` and `--list-new` output, preserving all 40 hex
digits including leading zeroes. The installer must not
treat `mokutil --test-key` as a boolean exit status: upstream 0.7.2 returns
zero for an unenrolled key and one for an already enrolled key, while some
distributions patch that convention.

## Secret handling

The MOK enrollment password is executor policy and never appears in
`InstallPlan`. It is passed only through stdin and is absent from argv and
command logs.

`MOK.priv` remains inside the installed system with mode `0600`. It is required
for future DKMS module signing. The certificate is public and uses mode `0644`.

The private key must never be copied from the ISO build environment. A marker
containing the generated certificate's SHA-256 digest distinguishes an
installer-created target key from squashfs residue and makes retries
idempotent.

## DKMS

The installer writes:

```text
/etc/dkms/framework.conf.d/anduinos-sb-sign.conf
```

with:

```text
mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"
mok_certificate="/var/lib/shim-signed/mok/MOK.der"
```

This matches AnduinOS OOBE policy and avoids relying on DKMS's unreliable
implicit key discovery. If DKMS is present, `dkms autoinstall` is fatal on
failure. The following initramfs rebuild includes the resulting modules.

## Signed EFI artifacts

amd64 requires signed:

- `EFI/AnduinOS/shimx64.efi`;
- `EFI/AnduinOS/grubx64.efi`.

arm64 requires signed:

- `EFI/AnduinOS/shimaa64.efi`;
- `EFI/AnduinOS/grubaa64.efi`.

Each file is checked with `sbverify`. The PE machine field is also checked by
the bootloader verifier to prevent an amd64/arm64 mismatch.

Ordinary UEFI installations do not require or validate
`EFI/BOOT/BOOTX64.EFI` or `EFI/BOOT/BOOTAA64.EFI`. They use the vendor path and
an explicitly verified AnduinOS NVRAM entry. The immutable plan records whether
the selected disk was external, and privileged preflight rejects any change in
that property before storage writes.

For an external target in erase-disk mode, the newly formatted, installer-owned
ESP also receives a direct portable chain:

- `EFI/BOOT/BOOTX64.EFI` or `BOOTAA64.EFI`: the signed shim payload;
- `EFI/BOOT/grubx64.efi` or `grubaa64.efi`: signed GRUB;
- `EFI/BOOT/mmx64.efi` or `mmaa64.efi`: signed MokManager;
- `EFI/BOOT/grub.cfg`: an exact copy of the verified vendor configuration.

GRUB installation still uses `--no-extra-removable`. The installer stages each
file, checks PE architecture, cryptographically verifies each EFI signature,
and publishes the shim entry last. It rejects symlinks and confirms every
portable file is byte-for-byte identical to the corresponding vendor payload.
It never installs `fbx64.efi` or `fbaa64.efi`, so portable boot does not enter
shim's NVRAM-registration-and-ResetSystem path that caused #422. The normal
AnduinOS NVRAM entry is still created and points to the vendor shim.

This portable policy is forbidden for coexistence and manual layouts because
their ESP may be shared with Windows or another operating system.

When an amd64 erase-disk installation is launched through Legacy BIOS, the
installer still writes a removable-media EFI path so the resulting disk can
also boot on UEFI machines without relying on NVRAM services that were not
available during installation. That BIOS portability path is separate from
the Secure Boot MOK enrollment chain described here.

## Future coexistence and redundant boot targets

Guided coexistence may reuse an existing ESP but never formats it. On a shared
ESP the installer:

- writes only the `EFI/AnduinOS` vendor directory;
- never deletes or renames another vendor's files;
- does not replace `EFI/BOOT/BOOTX64.EFI` or
  `EFI/BOOT/BOOTAA64.EFI`;
- creates and verifies an AnduinOS UEFI NVRAM entry;
- fails with recovery instructions when NVRAM cannot be updated safely rather
  than taking ownership of the shared fallback path.

An existing ESP is accepted only after its identity, FAT filesystem, health
and free-space reserve are validated and bound into the immutable plan.

RAID and other redundant-root layouts require an ESP on every independently
bootable physical disk. Every ESP receives an architecture-matched signed
AnduinOS chain, and future kernel/initramfs/GRUB or UKI transactions update and
verify all copies before completion. A redundant-root milestone does not pass
until removal of each member in turn still reaches a verified boot chain.

These modes are not part of release one. Their storage identities, write-set
confirmation and delivery sequence are defined in
[`STORAGE-ROADMAP.md`](STORAGE-ROADMAP.md).

## Enrollment and recovery

MOK enrollment is a two-phase operation:

1. The installer writes a pending request to UEFI variables.
2. On reboot, shim's MOKManager requires physical confirmation and password
   `123456`.

The installer must clearly tell the user to select:

```text
Enroll MOK -> Continue -> Yes
```

and enter `123456`.

The installer does not call `mokutil --revoke-import` during cleanup because
that command can affect unrelated enrollment requests already owned by the
user. Enrollment is deliberately scheduled only after all boot-chain checks
pass.

## Required packages

- `shim-signed`
- `mokutil`
- architecture-matched `grub-efi-*-signed`
- architecture-matched `grub-efi-*-bin`
- `openssl`
- `sbsigntool`

amd64 additionally retains `grub-pc-bin` for Legacy BIOS support.

## Release validation

Unit tests cover ordering, password secrecy, key replacement, key-pair
matching, retry idempotency, signed-chain rejection and architecture
selection. Release still requires real UEFI tests for:

- amd64 Secure Boot enabled;
- arm64 Secure Boot enabled;
- enrollment completion in MOKManager;
- boot before and after enrollment;
- DKMS module loading after enrollment;
- canceled or mistyped MOKManager password;
- firmware that rejects EFI-variable writes;
- interrupted installation before and after enrollment scheduling.
- coexistence without changing pre-existing ESP files or fallback loaders;
- external erase-disk boot with empty firmware variables, with Secure Boot
  both disabled and enabled, without a ResetSystem loop;
- multi-ESP synchronization and member-loss boot after RAID support exists.

On 2026-09-22 the amd64 portable layout was exercised with QEMU q35/KVM,
OVMF Secure Boot firmware and a fresh OVMF variable store containing no
AnduinOS entry. The same disk reached the signed kernel and guest userspace
once with Secure Boot disabled and once with Microsoft UEFI certificates and
Secure Boot enabled. In both cases `BootCurrent` was OVMF's auto-created
`UEFI Misc Device`, not an AnduinOS NVRAM entry, and only one kernel boot was
observed. The ESP contained shim, signed GRUB, MokManager and `grub.cfg` under
`EFI/BOOT`, with no `fbx64.efi`.
