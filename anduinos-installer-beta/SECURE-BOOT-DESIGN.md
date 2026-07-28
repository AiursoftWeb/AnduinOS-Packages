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

```text
Secure Boot disabled or Legacy BIOS
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
        +-- verify signed named and fallback EFI chains
        |
        +-- if already enrolled: complete
        |
        +-- if the same certificate is pending: do not import again
        |
        +-- otherwise mokutil --import via stdin password
        |
        +-- mokutil --timeout -1
```

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

- `EFI/BOOT/BOOTX64.EFI`;
- `EFI/AnduinOS/shimx64.efi`;
- `EFI/AnduinOS/grubx64.efi`.

arm64 requires signed:

- `EFI/BOOT/BOOTAA64.EFI`;
- `EFI/AnduinOS/shimaa64.efi`;
- `EFI/AnduinOS/grubaa64.efi`.

Each file is checked with `sbverify`. The PE machine field is also checked by
the bootloader verifier to prevent an amd64/arm64 mismatch.

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

