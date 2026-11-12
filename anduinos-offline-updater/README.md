# AnduinOS Offline Updater

Choose or drop an official `.atgz` cumulative update and preview its effect on
your system. Open **Control Panel → System → Offline Update** when this package
is installed, or run `anduinos-offline-updater` without arguments.
The updater is hidden from application menus, like other Control Panel tools.

The desktop currently provides a **read-only preview**. The command line also
provides an installation preview for validation on supported systems.
Neither interface requires activation or checks your purchase status.
Official update packages are a paid AnduinOS Pro benefit, available through the
[Pro benefits center](https://www.anduinos.com/pro/offline-update-package).
The download page is not available yet; an existing local file can still be
checked without visiting it or signing in.

```sh
anduinos-offline-updater inspect update.atgz
anduinos-offline-updater inspect update.atgz --json
anduinos-offline-updater verify update.atgz --json
```

A trusted external keyring is required. The default path is
`/usr/share/keyrings/anduinos-offline-updates.gpg`; this package does not ship a
production or test signing key. For development, explicitly select your trusted
fixture key with `--keyring /path/to/trusted.gpg`. Never trust a key supplied by an
unverified bundle.

The preview validates signatures, payload hashes and repository metadata, then
uses native APT with copied package state and preferences. It does not contact
online repositories, execute package scripts or modify your APT sources. Held
packages, missing required targets, unapproved removals and downgrades block the
plan. Additional applications are retained; they are not all promised updates.

`--system-root /path/to/captured-root` reads captured `usr/lib/os-release`,
`var/lib/dpkg/status`, optional APT preferences and extended state. This is for
validation and cannot prove the captured machine's live filesystem or snapshot
layout. `--work-directory` chooses temporary storage for the unpacked bundle.

Results distinguish `update-available`, `already-installed`, `blocked` and
`updater-update-required`. The latter previews only the updater bootstrap,
not the later OS transaction. Space estimates do not claim an exact installation
peak or snapshot size; live installation must recheck mount-specific capacity,
APT locks and the confirmed plan. Snapshot availability is not a requirement.

The format is defined in
[ATGZ v1](https://gitlab.aiursoft.com/aiursoft/anduinosofflinebundles/-/blob/master/docs/atgz-v1.md).
The builder is a separate C# tool; the desktop does not need its .NET runtime.

## Build and test

Use the repository's standard APKG workflow for this `.aosproj`.
Run the source tests with the existing `anduinos-package-release-test` profile.
Tests use temporary keys/repositories and never install packages.

`anduinos-desktop-apps` recommends this package on Resolute and Stonking, so
normal desktop/ISO installations with recommendations enabled include it.
It is not recommended on Noble. Disabling recommendations or removing the
application changes that behavior.

The GTK interface uses the same read-only inspector in a separate unprivileged
process. It checks local files without contacting the Pro website; the link only
opens when requested. Temporary files use the user's cache directory and are
removed when inspection finishes, including after the window closes. The default
production trust key is still required and is not supplied by this package.

To run the
GTK interaction tests, use the existing APKG `gui` profile (GTK4, Libadwaita,
PyGObject, Xvfb and a session bus are required).

## Install from the command line

Back up important files first. `prepare` requests administrator authorization,
imports the selected file into root-owned storage and returns a fresh plan.
It does not install packages. Inspect the returned changes before confirming:

```sh
anduinos-offline-updater prepare update.atgz
anduinos-offline-updater install JOB_ID --confirm-plan PLAN_SHA256
anduinos-offline-updater status JOB_ID
```

Use the exact `job_id` and `plan_sha256` returned by `prepare`. Installation
accepts only the fixed system trust keyring, not an arbitrary key or APT options.
APT rechecks the plan under its native lock, installs only from the authenticated
local repository, preserves existing package hooks and retains changed dpkg
configuration files. Extra applications are not automatically removed to make
an upgrade succeed. Broken package state or an incompatible plan stops the job.

A transient system service owns installation, so closing the caller does not
terminate dpkg. Reconnect using `status`; package progress and the final result
are separate. `complete` means actual package targets and OS metadata passed
verification; a requested reboot must still be performed. `partial` or
`interrupted` requires inspection of the system and the service journal, not a
blind retry. There is no force-cancel operation during package installation.

Existing APT snapshot policy is retained. Systems without that capability update
normally. Snapshot results are reported separately, and an unconfirmed snapshot
is not a recovery guarantee. Local staging, package expansion and separate boot
partition capacity are checked conservatively; script and CoW peaks can vary.

Imported files and results remain in `/var/lib/anduinos-offline-updater` until
explicitly removed. Save any needed diagnostic results, then release the space:

```sh
anduinos-offline-updater discard JOB_ID
```

This installation preview rejects updater self-replacement and bootstrap jobs;
process handoff, the desktop install button and broader failure-recovery
qualification are subsequent milestones. It is not yet a production updater.
