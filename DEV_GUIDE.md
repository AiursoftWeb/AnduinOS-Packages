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
    <IncludeFile Include="deploy/my-file.png" Target="/usr/share/.../original-file.png" />
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
    <IncludeFile Include="deploy/noble/os-release" Target="/etc/os-release"
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
    <IncludeFolder Include="deploy/questing/tiling-assistant@leleat-on-github"
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
