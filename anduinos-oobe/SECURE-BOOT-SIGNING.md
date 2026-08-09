# AnduinOS Secure Boot Module Signing Architecture

> The implementation is owned by `anduinos-secureboot-toolkit`. Its README is
> authoritative for helper actions, dependencies, and the shared UI. OOBE embeds
> that shared Secure Boot page during first-run setup; AnduinOS Driver Center
> owns hardware-driver detection, installation, and repair.

## Scope and Ownership

AnduinOS ships third-party kernel modules, including NVIDIA, xpadneo, and IPU6
drivers, that must pass Secure Boot validation. The responsibilities are split
as follows:

| Component | Responsibility |
| --- | --- |
| Ubuntu and Shim | Boot chain, MOK enrollment, kernel and DKMS infrastructure |
| `anduinos-secureboot-toolkit` | Secure Boot inspection, MOK preparation, enrollment queueing, DKMS signing configuration, module rebuild, and shared UI |
| AnduinOS OOBE | First-run discovery and certificate configuration through the shared toolkit page |
| AnduinOS Driver Center | Hardware detection and all driver selection, installation, health checks, and repair |

OOBE intentionally contains no NVIDIA- or Xbox-specific detection, installation,
or repair workflow. Its hardware-driver page is only a discoverability entry
point that opens `/usr/bin/anduinos-driver-center`.

## The Trust Chain

```
┌─────────────────────────────────────────────────────────────────┐
│ UEFI firmware trusts the Microsoft UEFI CA                      │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ Microsoft-signed Shim starts GRUB and maintains the MOK list    │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ GRUB starts Canonical-signed Linux; MOK keys enter kernel trust │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ DKMS modules signed by an enrolled MOK can be loaded            │
└─────────────────────────────────────────────────────────────────┘
```

The Secure Boot page establishes or repairs the local trust material used by
DKMS. Driver Center then uses that trust foundation while managing individual
hardware drivers.

## Installation-Time Trust Establishment

When installation-time third-party software support is selected, Ubuntu can:

1. Generate `MOK.priv` and `MOK.der` under
   `/var/lib/shim-signed/mok/`.
2. Queue `MOK.der` for enrollment.
3. Copy the key material to the installed system.
4. Ask the user to enroll the certificate in MOKManager after reboot.

The process can remain incomplete if the option was skipped, the enrollment
screen timed out, or a module was built before the expected signing
configuration existed. During first run, the OOBE Secure Boot page exposes the
shared toolkit so the user can prepare the certificate and queue enrollment
without opening another application.

No desktop application can perform the firmware enrollment on the user's
behalf. OOBE and Driver Center can only prepare and queue it. The user must
confirm enrollment in MOKManager after reboot.

## Shared Toolkit Behavior

The toolkit distinguishes enabled, disabled, explicitly unsupported, and
unknown Secure Boot states. OOBE shows its Secure Boot page when enforcement is
enabled or the state is unknown. The page is omitted for known non-enforcing
states such as disabled or unsupported firmware.

The shared toolkit can:

- Inspect Secure Boot and the local MOK material.
- Create the MOK key pair when needed.
- Write the global DKMS signing configuration.
- Queue certificate enrollment for the next reboot.
- Rebuild DKMS modules with the configured key.
- Guide the user through the required reboot.

Probe failures and contradictory results remain unknown rather than being
silently treated as Secure Boot disabled.

## Global DKMS Signing Configuration

The toolkit writes
`/etc/dkms/framework.conf.d/anduinos-sb-sign.conf` with only the key paths:

```ini
mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"
mok_certificate="/var/lib/shim-signed/mok/MOK.der"
```

The configuration is global. NVIDIA, xpadneo, IPU6, and future DKMS packages can
all use the same enrolled MOK. The file does not pin a `sign_file` path, so
DKMS can select the correct kernel-specific signing tool after kernel upgrades.

## OOBE User Flow

1. System update is offered.
2. If Secure Boot is enabled or its state is unknown, OOBE presents the shared
   certificate configuration page.
3. OOBE always presents the hardware-driver entry page.
4. “Open Driver Center” starts `/usr/bin/anduinos-driver-center` without
   elevation and advances only after process launch succeeds.
5. “Skip” advances without starting another process.
6. Driver Center performs any later NVIDIA, Xbox, or other hardware-driver work.

The hardware-driver entry page is independent of hardware, virtualization,
network connectivity, and installation state. It therefore remains available
when the user chooses the offline OOBE path.

## Files Involved

| File | Role |
| --- | --- |
| `/var/lib/shim-signed/mok/MOK.priv` | Local MOK private key |
| `/var/lib/shim-signed/mok/MOK.der` | Certificate queued for or stored in firmware trust |
| `/etc/dkms/framework.conf.d/anduinos-sb-sign.conf` | Global DKMS signing-key configuration |
| `/usr/bin/anduinos-oobe` | First-run Secure Boot guidance and Driver Center entry point |
| `/usr/bin/anduinos-driver-center` | Hardware-driver management UI |
| `/lib/modules/$(uname -r)/updates/dkms/` | Installed DKMS modules |

## Command-Line Verification

The shared toolkit and Driver Center provide the supported graphical workflows.
For diagnostics, administrators can still inspect the underlying state:

```bash
mokutil --sb-state
sudo mokutil --test-key /var/lib/shim-signed/mok/MOK.der
cat /etc/dkms/framework.conf.d/anduinos-sb-sign.conf
openssl x509 -in /var/lib/shim-signed/mok/MOK.der -inform DER -noout -serial
```

Module-specific health checks belong to Driver Center rather than OOBE.
