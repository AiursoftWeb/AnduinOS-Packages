# AnduinOS Secure Boot Toolkit

`anduinos-secureboot-toolkit` is the single shared implementation of the
Secure Boot trust experience used by AnduinOS OOBE and AnduinOS Driver Center.
It owns Secure Boot inspection, Machine Owner Key (MOK) enrollment, DKMS
signing configuration, DKMS signature health, repair operations, and the
shared GTK/libadwaita trust panel.

## Scope contract

The toolkit is deliberately narrow. It may:

- inspect Secure Boot, MOK enrollment, matching kernel headers, and DKMS;
- create the standard Ubuntu MOK key pair through
  `update-secureboot-policy`;
- queue the MOK certificate for enrollment with the product enrollment code
  `123456`;
- write `/etc/dkms/framework.conf.d/anduinos-sb-sign.conf` atomically;
- rebuild installed DKMS modules and report partial success accurately;
- expose a versioned state model, a restricted privileged helper, and a
  shared GTK/libadwaita trust panel.

It must not:

- detect NVIDIA hardware or choose a package through `ubuntu-drivers`;
- install NVIDIA, Xbox, audio, printing, or any other APT package;
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

Keep implementation files in the documented layer. In particular, package
installation and hardware selection do not belong under `src/` or `scripts/`.

## Architecture

`inspect.py` is read-only and independent from GTK. It returns immutable
objects from `model.py`. `operations.py` contains the fixed privileged action
implementation. The installed helper exposes only `prepare` and
`repair-dkms`; it never evaluates a shell command.

Enrollment is determined by matching the local DER certificate's complete
SHA-1 fingerprint against `mokutil --list-enrolled`. Pending enrollment uses
the same exact match against `mokutil --list-new`. Never interpret
`mokutil --test-key` as a boolean exit status: upstream 0.7.2 returns `0` for
"not enrolled" and `1` for "already enrolled", while some distributions patch
that convention. Its C-locale message is only a compatibility fallback.

Firmware trust and the persistent DKMS signing configuration are separate
states. A missing DKMS configuration must offer signing repair; it must never
make an enrolled certificate appear unenrolled or offer enrollment again.

When firmware reports Secure Boot disabled, applications must omit the Secure
Boot management page from OOBE navigation and the Driver Center device list.
MOK enrollment, DKMS signing configuration, and module signatures are then not
installation prerequisites: NVIDIA, Xbox, and other driver workflows must
remain available. The state model deliberately reports trust readiness in this
case because firmware is not enforcing the signature chain.

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
`dkms`, and `pkexec`. Applications depend on the toolkit instead of invoking
those tools themselves. Hardware-facing applications retain their own direct
dependencies on `ubuntu-drivers-common` and `pciutils`.

Shipping working DKMS necessarily ships `gcc`, `make`, `dpkg-dev`, `binutils`,
and `patch`. The ISO build explicitly rejects the optional `build-essential`
recommendation so that the unrelated C++ toolchain is not included.
