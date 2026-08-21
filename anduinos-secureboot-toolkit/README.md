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

Firmware detection has four states and must never collapse command failure into
a disabled boolean:

- `enabled`: firmware enforces Secure Boot and the complete MOK chain applies;
- `disabled`: firmware supports Secure Boot but enforcement is off;
- `unsupported`: firmware explicitly reports that Secure Boot is unavailable;
- `unknown`: the probe failed, timed out, returned malformed output, or reported
  contradictory states.

Disabled and unsupported are known non-enforcing states. Applications omit the
Secure Boot management page and keep NVIDIA, Xbox, and other driver workflows
available. Unknown fails closed: trust readiness is false, driver trust cannot
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
and `pkexec`. DKMS is suggested rather than required: MOK enrollment and the
persistent signing configuration remain useful before any third-party module
is installed. Applications depend on the toolkit instead of invoking those
tools themselves. Hardware-facing applications retain their own direct
dependencies on `ubuntu-drivers-common` and `pciutils`.

When DKMS is present, the toolkit rebuilds and reinstalls each registered
module for the running kernel so the new build is signed by the configured
MOK. When DKMS is absent or has no installed module for that kernel, the module
step is reported as skipped without weakening the firmware trust operation.
