# AnduinOS Packages

[![GPL licensed](https://img.shields.io/badge/license-GPL-blue.svg)](https://github.com/AiursoftWeb/AnduinOS-Packages/blob/master/LICENSE)

AnduinOS APKG package sources.

## Package conflicts

The following AnduinOS packages explicitly replace or conflict with
Ubuntu's official packages.  Every Ubuntu package name appears exactly
once — there is no overlap.

---

### 🔴 Hard Replace — dpkg `Conflicts` + `Replaces` + `Provides`

User **cannot** install both.  Dpkg removes the Ubuntu package at install time.

| # | AnduinOS Package | Ubuntu Package Removed | Provides? | Notes |
|---|---|---|---|---|
| 1 | `anduinos-no-snapd` | `snapd` | — | APT pin (-10); postinst unmounts & purges `/snap`, `/var/snap` |
| 2 | `anduinos-desktop` | `ubuntu-desktop` | `yaru-theme-gnome-shell` | Metapackage — also kills the 7 packages below |
| 3 | 〃 | `yaru-theme-gnome-shell` | 〃 | 〃 |
| 4 | 〃 | `update-notifier` | 〃 | 〃 |
| 5 | 〃 | `update-notifier-common` | 〃 | 〃 |
| 6 | 〃 | `update-manager` | 〃 | 〃 |
| 7 | 〃 | `update-manager-core` | 〃 | 〃 |
| 8 | 〃 | `ubuntu-release-upgrader-core` | 〃 | 〃 |
| 9 | 〃 | `ubuntu-release-upgrader-gtk` | 〃 | 〃 |
| 9a | 〃 | `whoopsie` | 〃 | Error reporting → Ubuntu servers |
| 10 | `anduinos-session` | `ubuntu-session` | yes | + postinst purges `10_ubuntu-session.gschema.override` |
| 11 | `anduinos-gnome-extensions` | `gnome-shell-ubuntu-extensions` | yes | Metapackage — AnduinOS-curated extension set |
| 12 | `anduinos-installer-config` | `ubiquity-slideshow-ubuntu` | yes | + postinst `dpkg-divert` of Ubiquity languagelist |
| 13 | `anduinos-fonts` | (none — co-installs with `fonts-noto-color-emoji`) | — | CascadiaCode, NerdFonts, Noto Sans/Serif, Twitter & Noto Color Emoji |
| 14 | `anduinos-software-properties-common` | `software-properties-common` | yes | Patches `add-apt-repository` → `--distro=ubuntu` |
| 15 | `anduinos-software-properties-gtk` | `software-properties-gtk` | yes | Strips Ubuntu Pro ads; suppresses `ubuntu-pro-client` dep |
| 16 | `firefox-anduinos` | `firefox` | — | Mozilla Apt `.deb`, not the snap wrapper |
| 17 | `firmware-sof-anduinos` | `firmware-sof-signed` | yes | Newer Intel SOF from `thesofproject/sof-bin` |
| 18 | `alsa-ucm-conf-anduinos` | `alsa-ucm-conf` | yes | `1.2.16` vs Ubuntu `1.2.15.3` |
| 19 | `plymouth-anduinos` | `plymouth-theme-spinner` | yes | Boot splash in `themes/anduinos/` namespace |
| 20 | `gnome-shell-extension-appindicator-anduinos` | `gnome-shell-extension-appindicator` | yes | Same UUID: `appindicatorsupport@rgcjonas.gmail.com` |
| 21 | `gnome-shell-extension-dash-to-panel-anduinos` | `gnome-shell-extension-dash-to-panel` | yes | Same UUID: `dash-to-panel@jderose9.github.com` |
| 22 | `gnome-shell-extension-desktop-icons-ng-anduinos` | `gnome-shell-extension-desktop-icons-ng` | yes | Same UUID: `ding@rastersoft.com` (original DING) |
| 23 | `gnome-shell-extension-desktop-icons-ng-anduinos` | `gnome-shell-extension-gtk4-desktop-icons-ng` | yes | 〃 also replaces the GTK4 port |
| 24 | `gnome-shell-extension-gtk4-desktop-icons-ng` | `gnome-shell-extension-desktop-icons-ng` | yes | GTK4 port → same UUID. Also Conflicts `desktop-icons-ng-anduinos` |

> **Desktop icons**: two mutually-exclusive AnduinOS packages (#22-24) both replace the same Ubuntu package.  Users pick one — `desktop-icons-ng-anduinos` (original DING) or `gtk4-desktop-icons-ng` (GTK4 port).

---

### 🟡 Dual Recommend — `anduinos-X | ubuntu-X`

Defined in `anduinos-desktop-core` as alternative `Depends`.  The AnduinOS
version is listed first (preferred), but the user can `apt install ubuntu-X`
to switch back.

| Preferred (AnduinOS) | Fallback options | Line | Notes |
|---|---|---|---|
| `anduinos-session` | `ubuntu-session` \| `gnome-session` | 52 | |
| `firmware-sof-anduinos` | `firmware-sof-signed` | 81 | |
| `alsa-ucm-conf-anduinos` | `alsa-ucm-conf` | 82 | |
| `anduinos-wallpapers` | `ubuntu-wallpapers` | — | `Provides` only — `gnome-shell` hard-depends on `ubuntu-wallpapers`, satisfied without pulling the real package |

---

### 🟢 Soft Override — files only, package stays

These replace Ubuntu **files** without removing the Ubuntu **package**.

| AnduinOS Package | Files overridden | Mechanism |
|---|---|---|
| `base-files` | `os-release`, `lsb-release`, `issue`, `issue.net`, `ubuntu-logo-*.png`, `legal` | Epoch `1:` outranks Ubuntu |
| `anduinos-apt-config` | APT sources (`packages.anduinos.com`) + preferences | Dual pin: `origin` (domain) + `release o=` (Origin field), both at priority 1001; also shipped as `anduinos-apt-config-dev` (→ `apkg-dev.aiursoft.com`) |
| `anduinos-mimeapps` | `gnome-mimeapps.list` | `dpkg-divert` (original → `.ubuntu-original`) |
| `anduinos-bwrap-hack` | `bwrap` → `bwrap.real` + shim | Swallows `bwrap` failures on Live squashfs |

---

### Cascading removal

`anduinos-desktop` → `anduinos-desktop-core` → `anduinos-no-snapd` triggers:

```
anduinos-desktop  ──Conflicts──→  ubuntu-desktop
                              →  yaru-theme-gnome-shell
                              →  update-notifier, update-notifier-common
                              →  update-manager, update-manager-core
                              →  ubuntu-release-upgrader-core, ubuntu-release-upgrader-gtk
      │
      ├─Depends──→ anduinos-desktop-core
      │            └─Depends──→ anduinos-session | ubuntu-session | gnome-session
      │                       → firmware-sof-anduinos | firmware-sof-signed
      │                       → alsa-ucm-conf-anduinos | alsa-ucm-conf
      │
      ├─Depends──→ anduinos-no-snapd  ──Conflicts──→ snapd
      │
      └─Recommends─→ anduinos-software-properties-common
                   → anduinos-software-properties-gtk  (resolute only)
```

**Total: 14 Ubuntu packages removed or pinned out.**

---

### 🟦 Brand Packages — AnduinOS only, no Ubuntu conflict

These ship files or declare dependencies without replacing any Ubuntu package.

| Package | Type | Description |
|---|---|---|
| `anduinos-core-system` | Metapackage | Core system foundation (kernel, networking, boot, firmware, APT, security) |
| `anduinos-desktop-apps` | Metapackage | Default application selection (browser, office, media, utilities) |
| `anduinos-theme` | Metapackage | AnduinOS theme stack (Fluent GTK + Fluent icons + fonts + wallpapers) |
| `anduinos-appstore` | App | Flatpak-based app store with Flathub remote |
| `anduinos-deskmon` | Service | Desktop monitoring / hardware info agent |
| `anduinos-system-tweaks` | Config | System tuning (swappiness, I/O scheduler, sysctl) |
| `anduinos-system-tweaks-server` | Service | Background service for system tweaks |
| `anduinos-templates` | Data | Default file templates (`~/Templates`) |
| `anduinos-dconf-defaults` | Config | dconf / gsettings defaults for GNOME |
| `anduinos-gnome-shell-locale` | Locale | GNOME Shell locale / text overrides |

## Build

Each package is built via the GitLab CI pipeline (`.gitlab-ci.yml`). Packages use the `Aiursoft.Apkg.Sdk` and can be built locally with:

```
apkg publish
```

## Monthly Update Manual

All external sources must be checked **at least once per month** to keep packages from falling behind upstream. This section is the step-by-step checklist.

---

### A. Quick Checklist (5 min triage)

Run through this table each month. If anything has changed upstream, follow the detailed steps in the matching section below.

| # | What | Where to check | Update action |
|---|---|---|---|
| 1 | **Fluent GTK theme** | `anduinos-fluent-gtk-theme/download.sh:5` (commit) + [gitlab mirror] | Update commit → section B |
| 2 | **Fluent icon theme** | `anduinos-fluent-icon-theme/download.sh:5` (commit) + [gitlab mirror] | Update commit → section B |
| 3 | **ALSA UCM Conf** | `alsa-ucm-conf-anduinos/download.sh:5` (commit) + upstream [repo] | Update commit → section B |
| 4 | **SOF firmware** | `firmware-sof-anduinos/download.sh:5` (`SOF_VERSION`) + upstream [releases] | Update version → section C |
| 5 | **GNOME Shell version map** | `lib/gnome-versions.sh:3-7` — compare with Ubuntu's `gnome-shell` package for each supported suite | Update map → section D |
| 6 | **Fluent upstream versions** | [Fluent-gtk-theme] and [Fluent-icon-theme] GitHub releases — determine latest upstream version | Update version → section B |
| 7 | **GNOME Shell extensions** | Run a CI build — the resolver fetches the latest compatible version dynamically | Update version → section D |

[releases]: https://github.com/vinceliuice/Fluent-gtk-theme
[repo]: https://github.com/alsa-project/alsa-ucm-conf
[gitlab mirror]: https://gitlab.aiursoft.com/mirror/fluent-gtk-theme/

---

### B. Git-Pinned Packages (Fluent GTK, Fluent Icon, ALSA UCM Conf)

Three packages clone a git repo and pin to a specific commit hash. Both the **commit hash** and the **`.aosproj` PackageVersion** must be updated together.

#### B.1 Check for updates

```bash
# Fluent GTK theme (mirrored on gitlab.aiursoft.com)
git ls-remote https://gitlab.aiursoft.com/mirror/fluent-gtk-theme.git HEAD

# Fluent icon theme (mirrored on gitlab.aiursoft.com)
git ls-remote https://gitlab.aiursoft.com/mirror/fluent-icon-theme.git HEAD

# ALSA UCM Conf (upstream: alsa-project/alsa-ucm-conf)
git ls-remote https://github.com/alsa-project/alsa-ucm-conf.git HEAD
```

Compare the returned HEAD hash against the pinned commit in each `download.sh`. If different, an update is available.

For **ALSA UCM Conf**, check the upstream `configure.ac` for the version. For **Fluent** packages, check [GitHub releases](https://github.com/vinceliuice/Fluent-gtk-theme/releases) for the current upstream version (informational — not encoded in PackageVersion).

#### B.2 Apply the update

For each outdated package, update **two files**:

1. **`download.sh`** — update the `*_COMMIT` variable to the new HEAD hash
2. **`*.aosproj`** — bump the Debian revision suffix in `<PackageVersion>` (e.g. `-1` → `-2`)

Example diff for Fluent GTK theme:

```diff
# download.sh
-FLUENT_GTK_COMMIT="9fc5291"
+FLUENT_GTK_COMMIT="a1b2c3d"

# anduinos-fluent-gtk-theme.aosproj
-<PackageVersion>2.0.0~beta1-1+$(SuiteShortName)</PackageVersion>
+<PackageVersion>2.0.0~beta1-2+$(SuiteShortName)</PackageVersion>
```

#### B.3 Rebuild and verify

```bash
cd <package-dir>
apkg publish
```

---

### C. Firmware SOF (Version-Pinned Tarball)

`firmware-sof-anduinos` now derives from Ubuntu's `firmware-sof-signed` package at build time, then replaces the unpacked SOF payload with a newer Intel `sof-bin` release tarball. This keeps file ownership, upgrades, and removals under dpkg instead of a post-install `rsync`.

#### C.1 Check for updates

Visit [sof-bin releases](https://github.com/thesofproject/sof-bin/releases) and compare the latest tag against `SOF_VERSION` in `firmware-sof-anduinos/download.sh`.

#### C.2 Apply the update

Update **two files**:

```diff
# download.sh
-SOF_VERSION="2025.12"
+SOF_VERSION="2026.03"   # update to new release tag

# firmware-sof-anduinos.aosproj
-<PackageVersion>2.0.0~beta1+$(UpstreamVersion)-1+$(SuiteShortName)</PackageVersion>
+<PackageVersion>2.0.0~beta1+$(UpstreamVersion)-2+$(SuiteShortName)</PackageVersion>   # bump the Debian revision
```

The downloaded Intel tarball and extracted cache under `deploy/` are **not** committed — the CI regenerates them at build time via `download.sh`.

#### C.3 Rebuild and verify

```bash
cd firmware-sof-anduinos
apkg publish
```

---

### D. GNOME Shell Extensions (21 packages)

These are resolved **dynamically at build time**: the resolver (`lib/resolve-gnome-ext.py`) queries `extensions.gnome.org` for the best compatible version for each target GNOME Shell version. This means extension code is always up-to-date on every build — no monthly check needed for the extension code itself.

However, two things still need attention:

#### D.1 GNOME Shell version map (`lib/gnome-versions.sh`)

This file maps Ubuntu suite codenames to GNOME Shell major versions. It must be updated whenever:

- A new Ubuntu release ships (new suite codename + new GNOME Shell version)
- An existing Ubuntu point-release updates its GNOME Shell version (rare)

To check:

```bash
# For each supported suite, check what GNOME Shell version Ubuntu ships:
apt-cache policy gnome-shell  # (run on each supported Ubuntu release)
```

If a mismatch is found, update `lib/gnome-versions.sh`:

```bash
declare -A GNOME_TARGETS=(
    [noble]=46      # Ubuntu 24.04 LTS
    [questing]=49   # Ubuntu 25.10
    [resolute]=50   # Ubuntu 26.04 LTS
    # ^ update or add entries as needed
)
```

Then **CI rebuilds all 21 extension packages automatically** — the new GNOME version will be picked up by the resolver on the next build.

#### D.2 Extension `.aosproj` version numbers

Each extension's `.aosproj` uses a unified `<PackageVersion>` of `2.0.0~beta1-1+$(SuiteShortName)`. Bump the Debian revision suffix (e.g. `-1` → `-2`) when packaging changes. The resolver fetches the latest extension code at build time, so the extension code itself is always up-to-date regardless of the package version.

```diff
-<PackageVersion>2.0.0~beta1-1+$(SuiteShortName)</PackageVersion>
+<PackageVersion>2.0.0~beta1-2+$(SuiteShortName)</PackageVersion>
```

#### D.3 Special-cased extension: desktop-icons-ng-anduinos

`gnome-shell-extension-desktop-icons-ng-anduinos` explicitly conflicts with Ubuntu's `gnome-shell-extension-desktop-icons-ng` (same UUID `ding@rastersoft.com`). It also carries a custom `metadata.patch` in its deploy directories. When the upstream DING extension releases a new version, verify the patch still applies cleanly.

---

### E. Upstream-Derived Packages

Six packages derive from upstream `.deb` packages at build time via `UpstreamUrl`:

| Package | Upstream source | Repository |
|---|---|---|
| `base-files` | `base-files` | Ubuntu mirror |
| `firmware-sof-anduinos` | `firmware-sof-signed` | Ubuntu mirror |
| `plymouth-anduinos` | `plymouth-theme-spinner` | Ubuntu mirror |
| `anduinos-software-properties-common` | `software-properties-common` | Ubuntu mirror |
| `anduinos-software-properties-gtk` | `software-properties-gtk` | Ubuntu mirror |
| `firefox-anduinos` | `firefox` | Mozilla APT (`packages.mozilla.org`) |

These are rebuilt by CI and pull the latest upstream source at build time, so the **upstream base** stays up-to-date. `firmware-sof-anduinos` still needs the separate Intel release check from section C.

**Monthly check**: confirm the mirrors (`https://mirror.aiursoft.com/ubuntu` and Mozilla APT) are syncing correctly. No code changes needed unless the upstream changes the package name or the mirror URL changes.

---

### F. Internal Infrastructure (As-Needed)

These URLs are not on a monthly schedule, but should be reviewed whenever infrastructure changes:

| Reference | File | Purpose |
|---|---|---|
| `https://apkg-dev.aiursoft.com` | `.gitlab-ci.yml` (dev CI target) | APKG build server (dev) |
| `https://apkg.aiursoft.com` | `.gitlab-ci.yml` (prod CI target) | APKG build server (production) |
| `https://apkg-dev.aiursoft.com/artifacts/certs/anduinos` | `anduinos-archive-keyring.aosproj:17` | GPG signing key |
| `https://apkg-dev.aiursoft.com/artifacts/anduinos/` | `anduinos-apt-config-dev/deploy/**/*.sources` | APT repo (dev channel) |
| `https://packages.anduinos.com/artifacts/anduinos/` | `anduinos-apt-config/deploy/**/*.sources` | APT repo (production channel) |
| `https://nuget.aiursoft.com/v3/index.json` | `nuget.config:5` | NuGet source for build tools |
| `https://dl.flathub.org/repo/flathub.flatpakrepo` | `anduinos-appstore/scripts/postinst.sh:4` | Flathub remote |
| `https://mirror.aiursoft.com/ubuntu` | Multiple `.aosproj` files | Ubuntu mirror |

---

### G. Monthly Update Order

When doing a full monthly triage, follow this order — earlier packages are dependencies of later ones:

1. **Fluent icon theme** + **Fluent GTK theme** (no deps)
2. **ALSA UCM Conf** + **SOF firmware** (alsa-ucm-conf depends on nothing; SOF recommends alsa-ucm-conf but doesn't build-depend on it)
3. **GNOME Shell extension** version audits (no cross-deps)
4. **GNOME version map** update (triggers extension rebuilds)
5. **Ubuntu-derived packages** (no cross-deps; build picks up latest)
6. **Meta-packages** — rebuild last (`anduinos-desktop`, `anduinos-desktop-core`, `anduinos-gnome-extensions`, `anduinos-theme`)

For each updated package, push to `master` — CI runs `apkg publish && apkg push` automatically.
