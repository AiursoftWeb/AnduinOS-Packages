# anduinos-yubikey-manager

GTK4/Libadwaita frontend for enrolling YubiKey FIDO credentials for GDM.

## Security model

- Device discovery and FIDO enrollment run as the desktop user.
- Device discovery reads Linux USB sysfs directly. `ykman` is optional and only enriches
  the model, firmware, and interface details when installed.
- Only the restricted helper runs through `pkexec`.
- The helper accepts `enroll USER SERIAL CREDENTIAL` and `remove USER SERIAL`; it cannot
  execute arbitrary commands or write arbitrary paths.
- `/etc/anduinos-yubikey-manager/u2f_mappings` is root-owned and mode 0600.
- GDM uses `pam_u2f` as `sufficient`, before `common-auth`. Password authentication is
  retained as a recovery path.
- The fixed origin `pam://anduinos` survives hostname changes.

## Multiple users and multiple keys

The PAM authorization file contains one line per user and multiple colon-separated
credentials per line. GDM and sudo use separate mapping files, so each user may choose
different keys for each purpose. `enrollments.json` records the association between
purpose, username, YubiKey serial number, and public FIDO credential. Removing one
association preserves every other user, purpose, and key.

Passwordless sudo is effective before PAM and therefore takes precedence over YubiKey
authentication. The helper recognizes only full current-user `NOPASSWD: ALL` rules,
preserves scoped and other-user rules, and validates the complete configuration with
`visudo` before and after every change. A passwordless account cannot disable NOPASSWD
until at least one sudo credential is enrolled.

`pamu2fcfg` cannot select a device by serial number. The GUI therefore enrolls a selected
key only when it is the sole connected YubiKey. Keys may be reconnected together after
enrollment.

Some FIDO-only YubiKeys intentionally expose no hardware serial number. They are still
detected through vendor ID `1050` and shown with a temporary `usb-*` locator. The PAM
credential, rather than that locator, is the cryptographic identity used at login.

## SSH resident credentials

The SSH page uses `fido2-token -L` to enumerate exact `/dev/hidrawN` devices. Credential
inspection is initiated explicitly per device because FIDO credential management requires
a PIN. The PIN is collected with a masked GTK entry, sent only through the subprocess
standard input, wrapped in zeroizing memory, and never written to arguments, logs, files,
or application metadata.

Only relying parties beginning with `ssh:` are displayed. Their P-256 or Ed25519 public
keys are converted to the corresponding OpenSSH `sk-*` public blob, fingerprinted using
OpenSSH's SHA-256 format, and compared with `ssh-add -L` output from the current desktop
SSH agent.

Agent loading uses `ssh-add -K` only when one FIDO device is connected. Exact public-key
fingerprints are checked before and after the command, making an already-loaded identity
an idempotent success. Removal and signing tests use temporary public-only files with
`ssh-add -d` and `ssh-add -T`; private material is never copied from the authenticator.

Resident creation uses `ssh-keygen -t ecdsa-sk -O resident` by default. Touch remains
required because `no-touch-required` is never selected by the default workflow.
`device=/dev/hidrawN` binds creation to the device explicitly selected in the UI.
Advanced users may select Ed25519-SK, a custom `ssh:` application, resident username,
local handle path, and `verify-required`.

Before creation, the app validates all metadata, rejects existing output paths, and takes
a read-only credential snapshot using the selected device and PIN. After `ssh-keygen`
returns, a second snapshot must contain a new fingerprint and both local handle files
must exist. Errors after hardware creation explicitly warn the user not to retry until
they inspect the key, preventing accidental duplicate resident credentials.

OpenSSH PIN prompts use the application binary as a private askpass helper. The PIN travels
through an inherited pipe and zeroizing memory; it is never placed in argv, environment
values, terminal transcripts, logs, or temporary files.
