# AnduinOS Offline Updater

Choose or drop an official `.atgz` cumulative update and preview its effect on
your system. Open **Offline Updater** from the applications menu, or run
`anduinos-offline-updater` without arguments.

This version is **read-only**: it verifies and previews updates, but does not
install them. It does not require activation or check your purchase status.
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

Actual installation and privileged jobs are subsequent milestones. To run the
GTK interaction tests, use the existing APKG `gui` profile (GTK4, Libadwaita,
PyGObject, Xvfb and a session bus are required).
