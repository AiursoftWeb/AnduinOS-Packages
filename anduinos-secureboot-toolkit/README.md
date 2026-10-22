# AnduinOS Secure Boot Toolkit

`anduinos-secureboot-toolkit` is the single shared implementation of the
Secure Boot trust experience used by AnduinOS Driver Center. OOBE only links
to Driver Center and does not inspect or configure Secure Boot.
It owns Secure Boot inspection, Machine Owner Key (MOK) enrollment, DKMS
signing configuration, DKMS signature health, repair operations, and the
shared GTK/libadwaita trust panel.

## Unified certificate migration (stage 1)

Driver Center now distinguishes firmware enforcement, certificate existence,
certificate signing permissions, and actual MOK enrollment. Module-only MOKs
(EKU `1.3.6.1.4.1.2312.16.1.2`) remain valid for existing drivers but cannot
authorize a GRUB EFI image. New identities use RSA-3072, digitalSignature and
codeSigning, without the module-only restriction. This is a prerequisite for
future GRUB signing, not a claim that a GRUB package is installed or validated.

The explicit, authenticated migration actions are:

1. `prepare-identity`: generate a candidate (or reuse an existing suitable
   identity), check its key pair, then submit a MOK enrollment request.
2. Restart and confirm enrollment in MOKManager with code `123456`.
3. Once enrolled, the suitable certificate is used automatically. There is no
   activation action, second authorization dialog or "Use" button. Inspection
   remains read-only; pending or unreadable enrollment never counts as trust.
   A root oneshot service runs at boot before the display manager and APT
   services; package configuration also synchronizes already-enrolled identities.
4. `regenerate-identity`: explicitly create a replacement if recovery is
   necessary. Old identities and enrolled MOKs remain intact. An unrelated
   pending enrollment request blocks replacement rather than being discarded.

Public identity metadata lives in `/var/lib/anduinos-secureboot`; private keys
are root-owned mode 0600. A private key is never placed in the ESP. Local
root access can use these unencrypted signing keys: this design does not
provide protection against a compromised root account.

`current.json` records the configured identity; `previous.json` preserves a
working predecessor while a replacement awaits enrollment. Earlier local-preview
`candidate.json` / `active.json` files are recognized without requiring a
privileged migration or another enrollment. Signing operations must still
validate the private key pair immediately before use.

**Both identity actions preserve the current GRUB, shim, ESP, boot order,
and existing module signatures.** Old enrolled certificates
continue to authorize existing modules, including Xbox drivers. Canonical's
pre-signed NVIDIA modules are not touched. Once enrollment is verified, the
standard `/var/lib/shim-signed/mok/MOK.{priv,der}` files are replaced with the
new pair. DKMS is first pointed directly at the validated immutable identity
pair, so compatibility file replacement does not give DKMS a half-written pair.
Original files/configuration are retained under root-only
`/var/lib/anduinos-secureboot/backups/signing-*`. Writes are atomic per file,
fsynced, retryable after interruption, and rolled back on ordinary failures.
This is not an atomic filesystem-wide transaction: do not run another signing
tool concurrently with migration. Existing or interrupted backups are never
overwritten. Unknown enrollment, unsafe paths, mismatched keys and competing
DKMS configuration are rejected. No MOK is deleted; no module is rebuilt.
The UI detects unsynchronized defaults and offers repair only if needed;
there is no normal manual activation step. Do not delete the old MOK.

Automatic synchronization leaves an unrecognized signing configuration alone.
After enrollment, the explicit **Repair Automatic Signing** action confirms a
private backup and reset of `framework.conf.d/anduinos-sb-sign.conf`, restoring
the compatibility key pair from the verified identity. Other custom DKMS
configuration files or signing hooks still require review and are not replaced.
Driver signature repair waits until these unified signing defaults are verified;
it does not create another certificate or request another enrollment.

Synchronization also retires the exact `framework.conf.d/mok.conf` template
written by the July 2026 OOBE Xbox installation flow, provided its obsolete
`sign_helper.sh` is absent. The file joins the same private backup/rollback
transaction before removal. This works through the existing repair button,
boot service and package upgrade; no additional user decision or enrollment
is needed. Modified configurations, redirected files and real custom helpers
are not silently removed.

The roadmap is certificate migration on Resolute first, custom GRUB/shim
packages second, encrypted installation third, and Stonking integration last.

## AMD64 and ARM64 bootloader deployment

Both architectures use the same signing, status inspection and ESP transaction.
The native architecture selects `grubx64.efi` or `grubaa64.efi`, its official
shim/MokManager companions, and the matching GRUB compatibility directory.
Package replacement considers only the native EFI packages; BIOS packages
belong to AMD64 only. No release codename selects a separate implementation.

The unsigned package image is signed with the unified MOK and verified against
that certificate and the native PE architecture. Updated templates or signing
certificates invalidate the prepared-image cache. Package hooks and the
interactive repair use the same path, preserving prepared artifacts on signing
failure and the existing ESP rollback/backup protections on deployment failure.
The local `grub-multi-install` hook accepts only its native EFI target.

Source regression tests exercise real signing and verification for both PE
architectures on either host. ESP/NVRAM tests cover both filenames, updates,
foreign-loader preservation and rollback. These tests isolate package and
firmware commands; ARM64 guest boot, enrollment, package reinstall and power
loss qualification remain release gates. Installer and ISO selection of the
new ARM64 stack are the next integration phase.

Phase-two checks on 2026-10-16, on the AMD64 development host:

- All 166 toolkit tests passed, including rejection of a boot entry pointing
  to the other architecture even when the native signed files are present.
- Toolkit packages for both declared suites and GRUB packages for both
  architectures built successfully; both GRUB/shim artifact checks passed.
- The real packaged AMD64 and ARM64 GRUB images passed signing, verification,
  stable retry, wrong-certificate/architecture rejection and signing-failure
  preservation checks using the existing test fixtures with temporary keys.
- Both official shim/MokManager pairs passed signature and PE architecture
  verification. Firmware enrollment and package ownership were isolated in
  these tests; no host package, certificate or ESP was changed.

## Scope contract

### Fresh installation API

The installer uses `identity.generate_identity` and `identity.check_unified_pair`
with explicit target paths. These functions do not read firmware or enroll a
certificate. They share the purpose policy used by Driver Center, but do not
invoke existing-machine migration or wait for enrollment before signing the
new target's modules. A fresh installation can use the standard MOK paths
directly; it does not need a managed migration identity or a second "Use" step.

The toolkit is deliberately narrow. It may:

- inspect Secure Boot, MOK enrollment, matching kernel headers, and DKMS;
- prepare the current AnduinOS vendor EFI chain using installed signed payloads;
- create the standard Ubuntu MOK key pair through
  `update-secureboot-policy`;
- queue the MOK certificate for enrollment with the product enrollment code
  `123456`;
- write `/etc/dkms/framework.conf.d/anduinos-sb-sign.conf` atomically;
- rebuild installed DKMS modules and report partial success accurately;
- install the fixed AnduinOS GRUB/shim package pair after trust and APT
  replacement checks, and sign/deploy its native boot chain;
- expose a versioned state model, a restricted privileged helper, and a
  shared GTK/libadwaita trust panel.

It must not:

- detect NVIDIA hardware or choose a package through `ubuntu-drivers`;
- install NVIDIA, Xbox, audio, printing, or arbitrary APT packages;
- accept arbitrary commands, shell fragments, paths, or package names;
- own OOBE navigation or the Driver Center device list;
- grow into a second driver manager.

Those boundaries are a compatibility contract. Changes that expand the scope
must update this README and receive an explicit architecture review.

## Directory structure

The source tree follows this layout:

```text
anduinos-secureboot-toolkit/
├── README.md
├── anduinos-secureboot-toolkit.aosproj
├── data/
│   └── com.anduinos.SecureBootToolkit.policy
├── scripts/
│   ├── anduinos-secureboot-helper
│   └── anduinos-securebootctl
├── src/
│   └── anduinos_secureboot/
│       ├── __init__.py
│       ├── client.py
│       ├── inspect.py
│       ├── model.py
│       ├── operations.py
│       └── ui.py
└── tests/
    ├── test_contract.py
    ├── test_inspect.py
    └── test_operations.py
```

Keep implementation files in the documented layer. Hardware selection and
general package installation remain outside this toolkit; `grub.py` owns only
the fixed bootloader-pair replacement described above.

## Architecture

`inspect.py` is read-only and independent from GTK. It returns immutable
objects from `model.py`. `operations.py` contains the fixed privileged action
implementation. The installed helper exposes `prepare`, `repair-dkms`, and
the two fixed identity actions above, and `sync-defaults` for automatic
convergence or failure recovery; it never evaluates a shell command.

Enrollment is determined by matching the local DER certificate's complete
SHA-1 fingerprint against `mokutil --list-enrolled`. Pending enrollment uses
the same exact match against `mokutil --list-new`. Never interpret
`mokutil --test-key` as a boolean exit status: upstream 0.7.2 returns `0` for
"not enrolled" and `1` for "already enrolled", while some distributions patch
that convention. Its C-locale message is only a compatibility fallback.

Firmware trust and the persistent DKMS signing configuration are separate
states. A missing DKMS configuration must offer signing repair; it must never
make an enrolled certificate appear unenrolled or offer enrollment again.

Firmware detection has four states and must never collapse command failure into
a disabled boolean:

- `enabled`: firmware enforces Secure Boot and the complete MOK chain applies;
- `disabled`: firmware supports Secure Boot but enforcement is off;
- `unsupported`: BIOS, or an accessible mounted efivarfs with no SecureBoot variable;
- `unknown`: the probe failed, timed out, returned malformed output, or reported
  contradictory states.

`mokutil` exit 255 and “doesn't support Secure Boot” are not sufficient
proof of unsupported firmware: failed reads can mean absent variables,
permissions, or firmware I/O errors. The shared `firmware.py` probe records
stdout, stderr, exit status, and its evidence reason. EFI fallback validates
the four-byte attribute prefix plus one-byte boolean and distinguishes an
unmounted interface from an absent variable.

The fixed `/usr/libexec/anduinos-firmware-probe` helper accepts no arguments,
returns schema-1 JSON, and never creates keys or requests MOK enrollment.
It can prepare the fixed efivarfs mount only in an AnduinOS Live environment.
Interactive clients may request this helper for access failures; the status
CLI remains non-interactive. Live settings prepare the interface at startup
and permit this inspection action for the active local Live administrator.
Other firmware errors remain unknown and retain their diagnostic details.

Disabled and unsupported are known non-enforcing states. NVIDIA, Xbox, and
other driver workflows remain available. Driver Center can prepare and enroll
MOK while Secure Boot is disabled. DKMS inspection still reports unsigned
modules so enabling enforcement is not offered before signing is ready.
Unknown fails closed: trust readiness is false, driver trust cannot
be asserted, and applications surface the detection failure instead of treating
it as disabled. The read-only status CLI exposes this contract as schema 2 so
older boolean-only recovery consumers reject it safely.

`client.py` is the unprivileged boundary used by applications. `ui.py` owns
the common trust rows, product wording, fixed enrollment-code instructions,
progress, refresh, and reboot prompts. Applications inject their gettext
function and icon factory so existing translations and visual assets remain
compatible.

The helper returns a versioned JSON result. MOK creation, configuration,
enrollment, and DKMS rebuild are reported separately because enrollment may
already be queued even when a module rebuild fails.

## Dependency ownership

The toolkit directly depends on `mokutil`, `openssl`, `shim-signed`, `kmod`,
`efibootmgr`, `util-linux`, `sbsigntool`, and `pkexec`. DKMS is suggested rather than required: MOK enrollment and the
persistent signing configuration remain useful before any third-party module
is installed. Applications depend on the toolkit instead of invoking those
tools themselves. Hardware-facing applications retain their own direct
dependencies on `ubuntu-drivers-common` and `pciutils`.

When DKMS is present, the toolkit rebuilds and reinstalls each registered
module for the running kernel so the new build is signed by the configured
MOK. When DKMS is absent or has no installed module for that kernel, the module
step is reported as skipped without weakening the firmware trust operation.

## Preparing an existing UEFI installation

The existing `prepare` helper action first checks the boot chain, then prepares
MOK and DKMS. It works with enforcement enabled or disabled. Its CLI accepts no
disk, path, package, or command arguments. The sequence is:

1. Identify the writable FAT ESP mounted at `/boot/efi` and any unique AnduinOS
   GPT entry in BootOrder by partition UUID/number. Multiple ESPs are allowed;
   duplicate identities, ambiguous entries, BootNext and redirected paths stop
   repair. Without a recognized current entry (including BIOS or generic USB
   boot), with multiple ESPs, or with an AnduinOS chainloaded boot, additionally
   require the evaluated fstab ESP to match the mount and the standard Ubuntu
   EFI `grub.cfg` stub to match `grub-probe --target=fs_uuid /boot/grub` and
   `grub-mkrelpath /boot/grub`. Nonstandard stubs require manual review. Never
   select a foreign ESP just because BootCurrent still names that first-stage
   loader. Existing single-ESP direct-boot behavior is retained.
2. Hold APT/dpkg locks and verify installed-package integrity, PE architecture,
   and cryptographic signatures of shim, signed GRUB and MokManager. Signature
   verification does not claim that a particular firmware db/dbx or shim SBAT
   policy accepts the payload; that still requires a firmware boot test.
3. Stage and verify files before replacing the vendor chain and any recognized
   installer-owned fallback. Preserve GRUB configuration. No APT installation
   is performed.
4. If needed, create a shim entry without changing BootOrder, verify it, then
   replace the old AnduinOS entry's position in BootOrder. Retain the old entry
   outside BootOrder as a recovery option. Foreign entries and their order remain
   unchanged. A failed order update attempts to restore both order and files;
   if firmware rollback itself fails, retain the verified chain and report the
   failure instead of deleting files firmware might still reference.
5. Prepare MOK, queue enrollment and rebuild modules. Reboot normally to enroll
   MOK first. As in the installer, request an unlimited MokManager timeout so
   users have time to complete the instructions. Only once enrollment and module signing are ready does Driver
   Center offer rebooting to firmware to enable Secure Boot. Setup Mode gets an
   explicit factory-key warning. Firmware keys are never changed by the helper.

`EFI/Microsoft` and foreign fallback loaders remain untouched. Updates recognize
an existing installer-owned `EFI/BOOT` only when its configuration binds to this
installation and its loader matches the vendor or incoming official shim.
Unknown loaders require manual review. Only the installer creates a fallback,
on a newly formatted external erase-disk ESP; updates maintain that direct-copy
layout without adding a fallback registrar or inventing firmware entries.

Unit tests cover target ambiguity, symlinks, signature/architecture rejection,
NVRAM failure rollback, foreign-file preservation and enrollment ordering. Before
release, also exercise an OVMF/physical boot cycle: install with enforcement off,
prepare/enroll MOK, enable enforcement, boot and load DKMS modules. Repeat with
Setup Mode and Windows sharing the ESP. Tests with command doubles cannot prove
firmware acceptance, power-loss atomicity or dbx/SBAT compatibility.

An already deployed chain that matches the verified package payloads is a
read-only no-op; preparing MOK again does not rewrite healthy EFI files.
See [UEFI qualification](docs/UEFI-QUALIFICATION.md) for the exercised transition
from direct GRUB/Setup Mode through MOK enrollment to enforced Secure Boot.
