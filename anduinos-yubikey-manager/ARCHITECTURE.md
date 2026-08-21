# AnduinOS YubiKey Security Center

GTK4/Libadwaita security center for using YubiKeys with AnduinOS sign-in,
administrator access, SSH authentication, and Git commit signing.

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
authentication. The installer and Security Center share the neutral
`/etc/sudoers.d/90-anduinos-passwordless-admin` policy and the root-owned, mode 0644
`/var/lib/anduinos-passwordless-sudo/users` state snapshot. The snapshot contains only
validated usernames, lets the unprivileged UI remain passive, and is regenerated from
effective full-user rules by the privileged helper. The helper recognizes only full
current-user `NOPASSWD: ALL` rules, preserves scoped and other-user rules, writes
atomically, and validates the complete configuration with `visudo` before and after
every change. A passwordless account cannot disable NOPASSWD until at least one sudo
credential is enrolled.

Package upgrades preserve all authentication state. The pre-removal script exits before
cleanup for `upgrade` and `failed-upgrade`, and detaches only YubiKey-owned PAM entries
for an actual removal or deconfiguration. The shared passwordless-sudo policy survives
Security Center removal. The restricted helper's `repair` action
can reconstruct PAM mapping files, passwordless-sudo ownership, and integration lines
from validated enrollment metadata without requiring users to touch or re-enroll their
YubiKeys. The package post-install script invokes that repair path after upgrades so
older installations gain the current metadata model automatically.

`pamu2fcfg` cannot select a device by serial number. The GUI therefore enrolls a selected
key only when it is the sole connected YubiKey. Keys may be reconnected together after
enrollment.

Some FIDO-only YubiKeys intentionally expose no hardware serial number. They are still
detected through vendor ID `1050` and shown with a temporary `usb-*` locator. The PAM
credential, rather than that locator, is the cryptographic identity used at login.

## Home and live device state

Home is a persistent, responsive component rather than a reconstructed preferences
list. It presents distinct first-use, connected, multi-key, and
configured-but-disconnected states. Capability cards summarize sign-in, sudo, SSH, and
Git and navigate directly to the relevant page.

Automatic Home and hotplug discovery is deliberately passive. It reads USB sysfs only
and never invokes `ykman`, `pamu2fcfg`, `fido2-token`, SSH agent commands, PAM, or sudo.
Consequently opening Home and plugging or removing a key cannot make a YubiKey flash or
wait for touch/PIN input.

Some FIDO-only models omit their stable serial from the USB descriptor even though it is
available through YubiKey management. Home and the hotplug path keep the anonymous sysfs
identity to remain fully passive. When a GDM or sudo page explicitly opens, an anonymous
device triggers one bounded, non-interactive `ykman list` summary probe. Its result is
accepted only when it accounts for every sysfs YubiKey. This joins enrollment metadata
to the correct connected key without guessing by position or running per-device
inspection.

An asynchronous `udevadm monitor` stream schedules a 350 ms debounced sysfs snapshot for
USB changes. A five-second poll runs only while Home is visible and the window is active,
covering environments where udev monitoring is unavailable. The monitor receives a
parent-death signal and is also stopped during normal window disposal, so it cannot
outlive the application.

Full Home refreshes read enrollment metadata and Git configuration on GLib's blocking
pool, merge any cached SSH inspection results, and update the existing page in place.
GDM and sudo use the same fast device snapshot and metadata-backed passwordless-sudo
state. Slow or touch-requiring operations remain explicit actions on their respective
pages and use progress UI before work begins.

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

Entering the SSH page never runs YubiKey or agent commands on GTK's main thread.
`ykman`, `ssh-add`, and `fido2-token` discovery runs on GLib's blocking pool while a
modal spinner tells the user to touch the YubiKey if it flashes. A refresh-in-progress
guard prevents duplicate probes. The general USB/YubiKey discovery used by other pages
is skipped entirely for the SSH route, so it cannot block the page before the progress
dialog is rendered.

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

## SSH connection persistence

The SSH page exposes an independent, default-off connection-persistence switch. It is
available even when no YubiKey or SSH agent is detected because it manages only the
current desktop user's OpenSSH client configuration. Enabling it does not connect to a
server, load a key, change an authenticator touch policy, or use `pkexec`.

The fixed preset uses `ControlMaster auto`, `ControlPath ~/.ssh/cm-%r@%h:%p`, and
`ControlPersist 10m`. The directives live in
`~/.ssh/config.d/anduinos-yubikey-manager.inc`; one explicitly marked `Host *` and
`Include config.d/anduinos-yubikey-manager.inc` block is appended to `~/.ssh/config`.
OpenSSH keeps the first value obtained for each option, so host-specific values that
appear earlier in the user's configuration continue to take priority over these global
defaults.

Configuration reads and `ssh -G` validation run on GLib's blocking pool. New directories
and files use modes 0700 and 0600 respectively. Existing ownership, restrictive modes,
non-managed bytes, and original permissions are preserved. Candidate files are written
and synchronized in the destination directory, rechecked against the original snapshot,
validated without making a network connection, and atomically renamed. A concurrent
edit aborts the operation instead of being overwritten.

The switch recognizes only its exact marker block and exact managed fragment. Duplicate
or incomplete markers, modified content, unsafe paths, symlinks, or an additional
`Include` that could load the fragment produce a needs-attention state and are never
silently repaired. Disabling removes only the marked block, then deletes the exact
fragment only when no other include may reference it. It neither searches for equivalent
handwritten settings nor terminates existing ControlMaster processes; an already-running
master exits according to its existing idle timeout.

## Git SSH commit signing

The Git Signing page is self-contained. It lists connected authenticators and can inspect
their resident SSH credentials without requiring a visit to the SSH page. The resulting
credential cache is shared by both pages.

Git is configured for the current user with `gpg.format=ssh`, an exact
`user.signingKey`, and `commit.gpgSign=true`. The page presents one radio group containing
`No signing` and every inspected SSH credential. Selecting an option applies it
immediately; there is no separate strategy, Apply, or Restore workflow. Users create a
shared authentication/signing setup by selecting their authentication credential, or a
separated setup by selecting another credential.

A local OpenSSH FIDO key-handle path is preferred because it remains usable without a
preloaded agent. Otherwise Git receives an inline `key::` public key and the UI explains
that the matching resident credential must be available through the SSH agent. Every
write is transactional at the application level: a partial command failure restores the
values read immediately before the selection.

Selecting `No signing` disables automatic commit and tag signing without deleting the
remembered signing-key choice. Enabling a key shows a success row with the exact public
key, a copy action, and GitHub Signing Key guidance.

The signing test creates temporary data and an SSH signature in a private temporary
directory, verifies it with OpenSSH's `git` namespace, and removes it automatically. It
does not create a repository, commit, tag, or branch.

## Passkeys delegation

The Passkeys page provides educational guidance about creating and using passkeys with a
YubiKey, plus the installation status of Yubico Authenticator. Registration and sign-in
remain browser- or application-initiated; the page does not inspect connected hardware or
access account data.

Credential management is delegated completely. The security center does not read, create,
delete, or modify passkeys or YubiKey configuration. It only checks whether the fixed
Flatpak application ID `com.yubico.yubioath` is installed and, when available, opens that
external application. When it is unavailable, the page can open the application's official
Flathub `.flatpakref` through the system `flatpak+https` handler, which opens
GNOME Software directly. The security center never installs the Flatpak itself.
