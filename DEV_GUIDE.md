# .aosproj Package Development Guide

## Core Principle

**资源级改动不 derive，身份级改动 derive。**

| 改动类型 | 模式 | 示例 |
|---|---|---|
| 替换资源文件（图片、图标、音频） | 独立 branding 包 | plymouth logo、壁纸、图标主题 |
| 替换系统身份文件（os-release、issue） | derive | base-files |
| 覆盖上游功能（修改配置文件逻辑） | derive | 待定 |

## Why

### Derive mode (derives from an upstream .deb)

- Downloads the upstream .deb, extracts it, overlays local files, repacks
- The package **replaces** the upstream package entirely
- Version is `$(UpstreamVersion)-anduinos`

**Use when:** you are changing system identity files that must not coexist with
the original package (e.g., `/etc/os-release`, `/etc/lsb-release`). Two packages
owning the same conffile causes dpkg conflicts.

**Do NOT use when:** the upstream package has sibling sub-packages with exact
version dependencies (e.g., `plymouth` → `plymouth-theme-spinner`). The
`-anduinos` suffix breaks `Depends: plymouth (= exact-version)`.

### Branding overlay mode (standalone, no upstream)

- Small package that drops replacement files into place
- Uses `<Replaces>` on the original packages (NOT `<Conflicts>`)
- Version is managed manually

**Use when:** you are replacing resource files (images, fonts, sounds). The
original package stays installed; your files overwrite specific targets.

## Decision Checklist

Before writing an `.aosproj`:

1. What files am I changing?
2. Do those files belong to a package that has sibling sub-packages with exact
   version dependencies?
3. If yes → branding overlay. If no but changing system identity files → derive.

## Common Patterns

### Branding overlay (recommended for media assets)

```xml
<Project Sdk="Aiursoft.Apkg.Sdk">
  <PropertyGroup>
    <PackageName>anduinos-<thing>-branding</PackageName>
    <PackageVersion>1.0.0</PackageVersion>
    ...
  </PropertyGroup>
  <ItemGroup>
    <Replaces>original-package-name</Replaces>
    <IncludeFile Include="assets/my-file.png" Target="/usr/share/.../original-file.png" />
  </ItemGroup>
</Project>
```

### Derive (for system identity packages)

```xml
<Project Sdk="Aiursoft.Apkg.Sdk">
  <PropertyGroup>
    <PackageName>base-files</PackageName>
    <PackageVersion>$(UpstreamVersion)-anduinos</PackageVersion>
    ...
    <UpstreamPackage>base-files</UpstreamPackage>
    <UpstreamSuite>$(Suite)</UpstreamSuite>
    <UpstreamSuiteMapping>noble-addon=noble, ...</UpstreamSuiteMapping>
  </PropertyGroup>
  <ItemGroup>
    <IncludeFile Include="assets/noble/os-release" Target="/etc/os-release"
                 Condition="'$(Suite)' == 'noble-addon'" />
    ...
  </ItemGroup>
</Project>
```

## Suite-specific package versions

When a package's content differs between Ubuntu suites (e.g. a GNOME Shell extension
that ships a different zip per GNOME Shell version), using a plain `arch=all` label
with the same version number across all suites is **incorrect** — APT's pool assumes
identical content for every `arch=all` package with the same name and version.

The correct approach is to embed the suite name into the version number so each suite
produces a genuinely distinct `.deb`. The SDK supports two build-time variables for this:

| Variable | Value | Example result |
|---|---|---|
| `$(Suite)` | The raw target suite (e.g. `questing-addon`) | `1.0.56+questing-addon1` |
| `$(SuiteShortName)` | The mapped short name from `SuiteShortNameMap`; falls back to `$(Suite)` if not mapped | `1.0.56+questing1` |

`$(SuiteShortName)` is preferred because it produces cleaner version strings.

### Example

```xml
<Project Sdk="Aiursoft.Apkg.Sdk">
  <PropertyGroup>
    <PackageName>gnome-shell-extension-tiling-assistant</PackageName>
    <PackageVersion>1.0.56+$(SuiteShortName)1</PackageVersion>
    <TargetSuites>noble-addon questing-addon resolute-addon</TargetSuites>
    <SuiteShortNameMap>noble-addon=noble questing-addon=questing resolute-addon=resolute</SuiteShortNameMap>
    ...
  </PropertyGroup>
  <ItemGroup>
    <IncludeFolder Include="assets/questing/tiling-assistant@leleat-on-github"
                   Target="/usr/share/gnome-shell/extensions/tiling-assistant@leleat-on-github"
                   Condition="'$(Suite)' == 'questing-addon'" />
    ...
  </ItemGroup>
</Project>
```

This produces:
- `gnome-shell-extension-tiling-assistant_1.0.56+noble1_all.deb` for noble-addon
- `gnome-shell-extension-tiling-assistant_1.0.56+questing1_all.deb` for questing-addon
- `gnome-shell-extension-tiling-assistant_1.0.56+resolute1_all.deb` for resolute-addon

Each suite's Packages index points to its own pool file, so APT hash verification
always succeeds.

### Rule of thumb

Use `+$(SuiteShortName)1` whenever the `.aosproj` contains any `IncludeFolder` (or
`IncludeFile`) with a `Condition="'$(Suite)' == '...'"`. If all suites share the same
content, a plain version number is fine.



Users of AnduinOS should add an APT pin to ensure AnduinOS packages take
precedence:

```text
# /etc/apt/preferences.d/anduinos
Package: *
Pin: origin "Aiursoft Apkg"
Pin-Priority: 1001
```

Pin 1001 ensures AnduinOS packages are always preferred even if Ubuntu ships a
higher version number. This is safe because the AnduinOS addon repository only
contains packages that are intentionally built and pushed — it is not a full
mirror.

## Desktop Entry Visibility: Search Is Not the Applications Menu

GNOME Shell search, ArcMenu's **All Apps** view, and GNOME Software do not use
the same index:

| Surface | Data source |
|---|---|
| GNOME Shell / ArcMenu search | Installed `Gio.DesktopAppInfo` applications |
| ArcMenu All Apps and categories | The freedesktop `applications.menu` tree |
| GNOME Software | AppStream/DEP-11 metadata |

An application can therefore be searchable but absent from All Apps. In
particular, GNOME places entries with `X-GNOME-Settings-Panel` in the hidden
`System Settings` menu directory. ArcMenu skips that directory when building
All Apps. These categories are reserved for components embedded in GNOME
Control Center, not for standalone settings applications.

Standalone AnduinOS applications **must not** use any of the following:

```ini
Categories=...;X-GNOME-Settings-Panel;...
Categories=...;X-GNOME-SystemSettings;...
X-GNOME-Settings-Panel=panel-name
```

Use registered freedesktop categories instead, with one main category. For a
standalone settings application:

```ini
[Desktop Entry]
Type=Application
Name=Swap Control
Exec=swapcontrol-gtk
Icon=com.anduinos.swapcontrol
Categories=Settings;GTK;GNOME;
```

Avoid combining multiple main categories such as `Settings;System;` unless the
application genuinely belongs in both; `desktop-file-validate` warns that this
can create duplicate menu entries. Also use `NoDisplay=true`, `Hidden=true`,
`OnlyShowIn`, and `NotShowIn` only for deliberately hidden handlers, autostart
entries, or environment-specific launchers. They are not substitutes for
normal application categorization.

Before publishing a package containing a Desktop Entry:

```bash
desktop-file-validate path/to/application.desktop
```

Changing a Desktop Entry changes installed package content, so bump
`PackageVersion`. The repository's `desktop-entry-policy` CI job rejects GNOME
Control Center panel categories before any package is published. If AnduinOS
ever ships a real embedded Control Center panel, its integration and a narrow
CI exception must be reviewed explicitly.

### Graphical application source layout

Keep user-facing package inputs in predictable locations:

- `src/` contains application source code and executable entry points.
- `data/` contains freedesktop integration files such as the primary `.desktop`
  file, application icon, PolicyKit policies, and systemd units.
- `screenshots/` contains images referenced by `AppStreamScreenshot` items.
- `po/` and `locale/` contain translation sources and compiled catalogs.
- `bin/`, `obj/`, and `target/` are generated build directories and are ignored
  by the repository-level `.gitignore`.

For consistent AppStream declarations, place the primary desktop entry and icon
under `data/`:

```xml
<AppStreamApplication Include="data/com.example.Application.desktop"
                      Icon="data/com.example.Application.svg" />
```

Application-specific runtime resources may remain in a dedicated `resources/`
or `assets/` directory when that distinction is useful; do not move them merely
to make directory names uniform.

## Postinst Best Practices: Never Run `dconf update` or `glib-compile-schemas`

**Do not** put `dconf update` or `glib-compile-schemas` in `postinst.sh` scripts.
These are handled automatically by Debian's **dpkg triggers**.

### How triggers work

Two core system packages declare interest in file-system changes:

- **`anduinos-dconf-runtime`** declares `interest-noawait /etc/dconf/db/anduinos.d`
- **`libglib2.0-0`** declares interest for `/usr/share/glib-2.0/schemas/`

When any package drops a file under these monitored directories, dpkg records
the trigger. At the end of the entire apt transaction, the trigger owner's
script runs **once** — no matter how many packages were installed or upgraded.

### What this means for packaging

| Instead of putting this in postinst… | …the system does it for you: |
|---|---|
| `dconf update` | Triggered once by `anduinos-dconf-runtime` when files land in `/etc/dconf/db/anduinos.d/` |
| `glib-compile-schemas /usr/share/glib-2.0/schemas/` | Triggered once by `libglib2.0-0` when files land in schemas dir |
| `glib-compile-schemas <extension>/schemas/` | **Pre-compile at build time** in `download.sh` — ship `gschemas.compiled` in the `.deb` |

### Why this matters

1. **Performance**: A single apt transaction installing 16 extensions runs the
   trigger once, not 16 times.
2. **Chroot safety**: `dconf update` broadcasts D-Bus "Settings Changed" signals.
   During chroot OS builds with bind-mounted `/run`, these leak into the host
   and can crash the host's GNOME Shell. `anduinos-dconf-runtime` avoids this
   by using `dconf compile /etc/dconf/db/anduinos /etc/dconf/db/anduinos.d`
   instead when it detects a chroot.
3. **Correctness**: The trigger always runs at the right moment (after all
   packages are unpacked), regardless of install order.

### Exception

The maintainer script exception for dconf is the centralized
`anduinos-dconf-runtime` trigger owner, which runs `dconf update` on live
systems and `dconf compile` in chroots. Separately, `anduinos-session` removes
a Ubuntu gschema override file (`10_ubuntu-session.gschema.override`). This
`rm -f` operation is idempotent, emits no D-Bus signals, and must happen at
install time. It does not call `dconf update` or `glib-compile-schemas`.
