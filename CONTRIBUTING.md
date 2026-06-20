# Contributing to AnduinOS Packages

This document captures the architectural conventions and best practices
developed for AnduinOS APKG packages. Follow these when adding or modifying
packages.

## The Holy Trinity: Provides, Conflicts, Replaces

Every fork package that replaces an upstream Ubuntu package **must** declare
all three relationships, in this exact order:

```xml
<Provides>upstream-package</Provides>
<Conflicts>upstream-package</Conflicts>
<Replaces>upstream-package</Replaces>
```

**Why all three:**

| Relationship | Purpose |
|---|---|
| `Provides` | Satisfies other packages' Depends/Recommends on the upstream name, preventing APT deadlocks |
| `Conflicts` | Blocks the upstream package from being co-installed alongside our fork |
| `Replaces` | Allows our package to overwrite files owned by the upstream package during upgrade |

**One without the others is a bug:**

- `Conflicts` alone → APT deadlocks when something `Recommends` the upstream package
- `Provides` + `Replaces` without `Conflicts` → both packages can coexist, causing file conflicts
- `Provides` + `Conflicts` without `Replaces` → upgrade path may fail on file overwrites

## Fork vs. Swap: When to Use Which

### Fork (new package name, P-C-R)

Use when the upstream package contains **security-sensitive or frequently
updated logic** that we must track. Our fork derives from upstream's .deb,
patches what we need, and declares P-C-R.

```xml
<PackageName>anduinos-software-properties-common</PackageName>
<Provides>software-properties-common</Provides>
<Conflicts>software-properties-common</Conflicts>
<Replaces>software-properties-common</Replaces>
```

Examples: `anduinos-software-properties-common`, `anduinos-software-properties-gtk`.

### Swap (same package name, epoch 1:)

Use when our package is a **complete replacement** with no need to track
upstream updates — branding, wallpapers, base system identity.

```xml
<PackageName>base-files</PackageName>
<PackageVersion>1:13.5+anduinos1</PackageVersion>
```

The epoch `1:` outranks any Ubuntu version. Use sparingly.

Examples: `base-files`.

## Metapackage Design

### Own your critical infrastructure

If your metapackage `Conflicts` with a session package (e.g., `ubuntu-session`),
it **must** explicitly depend on the packages that would otherwise become
orphaned and get swept away by `apt autoremove`:

```xml
<Conflicts>ubuntu-session</Conflicts>

<!-- These would be orphans without ubuntu-session — we claim them -->
<Dependency Include="gnome-session" />
<Dependency Include="gdm3" />
<Dependency Include="xwayland" />
```

APT treats the metapackage as the new owner. `autoremove` will never touch them.

### Be restrained: don't over-claim

- **Do** conflict with packages that break your brand identity (session, shell theme)
- **Do not** conflict with `ubuntu-desktop` or `ubuntu-desktop-minimal` — users may need them as dependency anchors
- **Do not** conflict with wallpaper packages — let users choose their own desktop background
- **Do not** `Provides` the Ubuntu metapackage identity unless you genuinely satisfy all its contracts

### Conditionally include suite-specific packages

Use MSBuild `Condition` attributes when a dependency only exists for certain
Ubuntu releases:

```xml
<Dependency Include="anduinos-software-properties-gtk" Condition="'$(Suite)' == 'resolute-addon'" />
```

## Upstream Derivation Patterns

### Suppress upstream maintainer scripts

Always suppress for fork packages. Our patches would break if upstream's
postinst/postrm ran:

```xml
<SuppressUpstreamScripts>true</SuppressUpstreamScripts>
```

### Suppress unwanted inherited dependencies

Upstream packages sometimes depend on things we don't want (adware, Pro
client, etc.). Strip them:

```xml
<SuppressUpstreamDependencies>ubuntu-pro-client ubuntu-advantage-desktop-daemon</SuppressUpstreamDependencies>
```

Space or comma separated. Base package name matching (version constraints
stripped before comparison).

### Symlink, don't bundle, distro data files

When you need distro identity files (like `anduinos.info` → `ubuntu.info`),
use relative symlinks with explicit runtime dependencies:

```xml
<PrebuildCommand Run="for d in obj/*; do mkdir -p &quot;$d/usr/share/python-apt/templates&quot; &amp;&amp; ln -sf ubuntu.info &quot;$d/usr/share/python-apt/templates/anduinos.info&quot;; done" />

<!-- Guarantee the symlink target exists at runtime -->
<Dependency Include="python-apt-common" />
```

Never copy static data files from the host system into the package — they will
go stale and break reproducibility.

### PrebuildCommand ordering matters

`PrebuildCommand` runs **before** `MergeDepends`. This means your sed patches
execute on the raw upstream control file, and then the local dependencies are
merged in. The upstream dependencies flow through automatically — you don't
need to re-declare them.

## Dconf Defaults: Co-locate with the Component

Bundle each component's dconf defaults inside its own package, not in a
monolithic config package:

```xml
<IncludeFile Include="dconf/14-dash-to-panel.conf"
             Target="/etc/dconf/db/anduinos.d/14-dash-to-panel.conf" />
```

This way, when the user removes the component package, its dconf defaults
source files are cleaned up automatically by dpkg, and the binary database
is automatically recompiled by the dpkg trigger in `anduinos-dconf-runtime`.

## GNOME Shell Extensions

- **Same UUID, same package name root**: If our extension has the same UUID
  as Ubuntu's, use P-C-R (dpkg will refuse to install both due to file conflict).
- **Different UUID, same function**: Use P-C-R at the meta-package level
  (`anduinos-gnome-extensions` provides `gnome-shell-ubuntu-extensions`).
- Always bundle the extension's schema compilation as a post-install script:
  ```bash
  glib-compile-schemas /usr/share/glib-2.0/schemas/
  ```

## Package Versioning

- Fork packages: `$(UpstreamVersion)+anduinos1` — tracks upstream version
  with our revision suffix.
- Native packages: semantic versioning (`1.0.2`).
- Suite-differentiated versions: `64+$(SuiteShortName)2` for extensions that
  ship different code per GNOME release.

## Before Submitting

- [ ] All three P-C-R relationships declared (for fork packages)
- [ ] `SuppressUpstreamScripts` is `true` (for derived packages)
- [ ] No static copies of host system files — use symlinks with explicit Depends
- [ ] Metapackages own their critical infrastructure deps explicitly
- [ ] `Condition` attributes are used where a dep only applies to specific suites
- [ ] Dconf defaults are co-located with their component
- [ ] `apkg lint` passes
