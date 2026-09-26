# Secure Boot Signing Architecture

This reference explains how AnduinOS 2.0.2 keeps UEFI Secure Boot enabled while allowing packaged third-party kernel modules, such as NVIDIA, VirtualBox, or the optional Xbox controller driver, to load.

!!! note "For end users"

    For normal setup and recovery, use [Driver Center](../Applications/System/Driver-Center/Driver-Center.md) and follow the [Secure Boot Guide](./First-Boot-For-Secure-Boot.md). The details below are intended for troubleshooting and development.

## The trust chain

```text
UEFI firmware
  trusts the signed shim bootloader
        ↓
shim
  verifies the signed boot chain and maintains the Machine Owner Key list
        ↓
Linux kernel
  imports enrolled MOK certificates into its trusted keyrings
        ↓
third-party kernel module
  loads when its signature matches an enrolled certificate
```

The MOK certificate is enrolled in shim's machine-specific trust database. It is not normally added directly to the motherboard's platform-key database.

Ubuntu provides the signed shim, GRUB and kernel packages, `mokutil`, `update-secureboot-policy`, and the optional DKMS framework. AnduinOS adds a shared toolkit and an installer policy that make the certificate state, signing configuration, and repair flow consistent across the installer, Welcome Center, and Driver Center.

## Fresh installation

The native AnduinOS installer probes the boot mode and Secure Boot state before it creates the installation plan.

When Secure Boot is enabled, it:

1. verifies the architecture-matched signed shim and GRUB packages;
2. creates a new MOK private key and certificate inside the target system;
3. writes the persistent DKMS signing paths;
4. processes installed DKMS modules when DKMS is present and verifies their signer;
5. queues the new certificate for MOK enrollment; and
6. verifies that the enrollment request exists before installation completes.

The first restart enters MOKManager. The one-time enrollment code is `123456`.

When Secure Boot is disabled, unsupported, or not applicable to an AMD64 Legacy BIOS installation, the installer does not queue MOK enrollment. ARM64 installations use standards-based UEFI only.

## Shared AnduinOS toolkit

The `anduinos-secureboot-toolkit` package owns the common inspection and repair implementation used by Driver Center and Welcome Center. It distinguishes these states instead of reducing them to one green or red result:

- Secure Boot enabled, disabled, unsupported, or unknown;
- local MOK key pair present or missing;
- certificate enrolled, pending enrollment, or not enrolled;
- persistent DKMS signing configuration present or missing; and
- installed DKMS modules signed correctly, signed by another key, or unsigned.

The toolkit directly depends on the small Secure Boot inspection utilities. It does **not** depend on DKMS, a compiler, or `build-essential`; DKMS is only a suggestion. A computer without third-party source-built modules can therefore manage its MOK certificate without pulling in a development toolchain.

When DKMS is already installed, repair targets only module/version pairs that `dkms status` reports as installed for the running kernel. It rebuilds those explicit targets with the configured key. It does not run an unconditional `dkms autoinstall` and does not install DKMS merely to perform certificate enrollment.

## Persistent signing configuration

The toolkit and installer write:

```ini title="/etc/dkms/framework.conf.d/anduinos-sb-sign.conf"
mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"
mok_certificate="/var/lib/shim-signed/mok/MOK.der"
```

DKMS selects the correct kernel `sign-file` implementation for each kernel version. Keeping only the key paths in this configuration allows later kernel and driver updates to use the same enrolled machine certificate.

The key files are:

| Path | Purpose |
|---|---|
| `/var/lib/shim-signed/mok/MOK.priv` | Private module-signing key; must remain private to the machine |
| `/var/lib/shim-signed/mok/MOK.der` | Public certificate queued or enrolled through MOKManager |
| `/etc/dkms/framework.conf.d/anduinos-sb-sign.conf` | Persistent DKMS signing-key selection |

The certificate may be inspected or shared for diagnosis. The `.priv` file must never be published or copied to another computer.

## Driver Center recovery

Driver Center's **Secure Boot** page uses the shared toolkit. Depending on the detected state, it can:

- create the missing machine-local certificate;
- queue an existing certificate for enrollment;
- report that MOKManager enrollment is already pending;
- restore the persistent DKMS signing configuration; or
- rebuild explicitly installed DKMS modules whose signatures do not match.

Certificate enrollment still requires a restart and physical confirmation in MOKManager. A graphical application running inside the operating system cannot silently add a new MOK.

## Inspect the state

Check whether signature enforcement is active:

```bash title="Check Secure Boot"
sudo mokutil --sb-state
```

Check whether the AnduinOS certificate is enrolled:

```bash title="Check the local MOK certificate"
sudo mokutil --test-key /var/lib/shim-signed/mok/MOK.der
```

Check whether a request is waiting for MOKManager:

```bash title="List pending MOK enrollment"
sudo mokutil --list-new
```

Inspect the complete machine-readable state used by the graphical applications:

```bash title="Inspect AnduinOS Secure Boot state"
anduinos-securebootctl
```

For an installed module, compare its reported signing key with the local certificate:

```bash title="Inspect a module signature"
modinfo hid-xpadneo | grep -E 'signer|sig_key'
openssl x509 \
    -in /var/lib/shim-signed/mok/MOK.der \
    -inform DER \
    -noout -subject -serial -fingerprint
```

Do not infer trust from a single command failure. Driver Center also checks the enrolled and pending certificate lists because different `mokutil` versions do not use every exit status in the same way.

## Common state transitions

| Initial state | Action | Result |
|---|---|---|
| Secure Boot disabled | Enable it in UEFI settings | Driver Center may then request certificate creation or enrollment |
| Certificate missing | Create and enroll from Driver Center | Key pair is created and enrollment is queued |
| Enrollment pending | Restart and enter `123456` in MOKManager | Certificate becomes trusted by shim and the kernel on the following boot |
| Certificate enrolled, signing config missing | Repair automatic signing | DKMS key paths are restored without changing firmware state |
| Installed DKMS module signed by another key | Repair modules | Explicit installed targets are rebuilt and signed with the enrolled key |
| No DKMS installed | Complete certificate setup only | No compiler toolchain or DKMS package is installed automatically |

This separation is intentional: boot trust, certificate enrollment, persistent signing configuration, and the presence of third-party modules are related but independent states.
