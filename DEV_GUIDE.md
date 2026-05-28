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

## APT Preferences

Users of AnduinOS should add an APT pin to ensure AnduinOS packages take
precedence:

```text
# /etc/apt/preferences.d/anduinos
Package: *
Pin: origin apkg.aiursoft.com
Pin-Priority: 1001
```

Pin 1001 ensures AnduinOS packages are always preferred even if Ubuntu ships a
higher version number. This is safe because the AnduinOS addon repository only
contains packages that are intentionally built and pushed — it is not a full
mirror.
