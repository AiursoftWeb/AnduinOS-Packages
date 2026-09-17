# The .aosproj Reference

An `.aosproj` is an XML package recipe consumed by Apkg's `AosprojSerializer`, not a general MSBuild project. This reference complements [the first package tutorial](Build-Your-First-Package.md). It documents the source reviewed for CLI 10.0.55; use [migration notes](Documentation-Migration.md) when comparing older documentation.

## Structure and properties

Use one `Project` root with `Sdk="Aiursoft.Apkg.Sdk"`. Put scalar metadata inside `PropertyGroup`, and file mappings, dependencies, and commands inside `ItemGroup`. Multiple groups are supported. Unknown XML is not a substitute for an implemented feature: a misplaced command can be ignored.

| Property | Meaning and default |
|----------|---------------------|
| `PackageName` | Required Debian package name; lowercase letters, digits, `-`, `+`, and `.`, beginning with a letter or digit: `^[a-z0-9][a-z0-9\-+.]*$` |
| `PackageVersion` | Required Debian version; may use `$(Suite)`, `$(SuiteShortName)`, or the derived `$(UpstreamVersion)` |
| `PackageDescription` | Required package description |
| `Maintainer` | Debian maintainer; overrides `PackageAuthors` |
| `PackageAuthors` | Fallback maintainer, normally `Name <email>`; escape angle brackets in XML |
| `TargetDistro` | Repository namespace such as `anduinos`; declare it explicitly |
| `TargetSuites` | Required space-separated suite list |
| `TargetArchitectures` | Space-separated architectures; use `all` for architecture-independent payloads |
| `Component` | Repository component, normally `main` |
| `Section` | Debian section; local value, then upstream value, then `utils` |
| `Priority` | Debian priority; local value, then upstream value, then `optional` |
| `PackageHomepage` | Package homepage, with upstream fallback for derived packages |
| `RepositoryUrl` | Source repository URL, carried into the upload manifest/platform record rather than Debian control |
| `LicenseType` | License identifier, for example `MIT` or `GPL-3.0` |
| `LicenseFile` | Relative license file path |
| `PackageTags` | Reserved package tags; do not depend on tag routing |
| `Provides` | Virtual packages provided by this package |
| `Conflicts` | Packages that cannot coexist with this package |
| `Replaces` | Package file replacement relationship; not a general permission to overwrite unrelated files |
| `Breaks` | Incompatible package/version relationships |
| `Recommends` | Comma-separated strong recommendations, installed by APT by default |
| `Suggests` | Optional enhancements, not installed automatically by default |
| `SuiteShortNameMap` | Target-to-short-name mapping, e.g. `noble-addon=noble resolute-addon=resolute`; without a mapping the full suite name is used |
| `AppStreamDeveloperName` | Displayed developer; falls back to `PackageAuthors` |
| `AppStreamMetadataLicense` | Metadata license, default `CC0-1.0`; separate from the application's license |

All upstream fields, including conditional URLs and suppression controls, are covered in [Upstream packages](Upstream-Packages.md). GUI fields are covered in [AppStream](AppStream.md).

## Conditions and variables

Item conditions select files or commands for a target. The context exposes `Distro`, `Suite`, `Arch` (alias `Architecture`), `Component`, `UpstreamDistro`, `UpstreamSuite`, and `UpstreamArch` (alias `UpstreamArchitecture`), written as `$(Name)`.

```xml
<ItemGroup>
  <IncludeFile Include="deploy/amd64/tool" Target="/usr/bin/tool" Mode="755"
               Condition="'$(Arch)' == 'amd64'" />
  <Dependency Include="python3" Condition="'$(Suite)' != 'legacy'" />
</ItemGroup>
```

An absent/empty condition is true. Equality (`==`) and inequality (`!=`) compare strings without case sensitivity. The implementation recognizes simple `and` or `or` combinations. It is not a complete expression parser: do not assume arbitrary nesting, parentheses, or long mixed logical chains have MSBuild semantics. Prefer separate entries with simple conditions and verify every target.

`UpstreamVersion` is a version-template value resolved from upstream package metadata, not an arbitrary property available before upstream resolution. `SuiteShortName` is resolved from the mapping when calculating versions.

## Files and permissions

| Item | Attributes and behavior |
|------|-------------------------|
| `IncludeFile` | `Include` is a relative source; `Target` is the installed absolute filename; default mode `644` |
| `IncludeScript` | Same mapping, default mode `755` |
| `IncludeFolder` | Recursively copy a directory to the target directory, preserving relative layout |
| `ConfFile` | Install a configuration file and register its path in `DEBIAN/conffiles`; default mode `644` |

Use `Mode="755"`, `"644"`, or `"600"` to set supported file-item permissions explicitly. `IncludeFile` does not promise to inherit the source's executable bit. `IncludeFolder` preserves file and directory symlinks as links and does not recurse into directory symlinks, avoiding cycles and duplicate payloads.

```xml
<ItemGroup>
  <IncludeScript Include="assets/tool" Target="/usr/bin/tool" />
  <IncludeFolder Include="assets/themes" Target="/usr/share/my-tool/themes" />
  <ConfFile Include="assets/tool.conf" Target="/etc/my-tool.conf" Mode="644" />
</ItemGroup>
```

Conffiles let dpkg handle user edits during upgrades. They do not make a package's other files user-owned or protect arbitrary data generated by an application.

## Dependencies and metapackages

`Dependency` items become `Depends`. Use XML escaping for version operators, and `|` for alternatives:

```xml
<ItemGroup>
  <Dependency Include="python3 (&gt;= 3.10)" />
  <Dependency Include="my-preferred-tool | my-fallback-tool" />
  <Recommend Include="my-tool-extras" Condition="'$(Suite)' == 'resolute-addon'" />
  <Suggest Include="my-tool-examples" />
</ItemGroup>
```

`Recommend` and `Suggest` are conditional item forms; `Recommends` and `Suggests` are property strings. A metapackage can contain only dependency/recommendation relationships and no payload. Its empty-payload lint warning can be intentional. Recommendations allow users to remove optional members without breaking the metapackage's hard dependencies.

Changing dependency declarations does not install build tools. Provision the compiler, runtime used by tests, and other build prerequisites separately.

## Build and installation hooks

```xml
<ItemGroup>
  <PrebuildCommand Run="make release" />
  <PreInstallScript Include="scripts/preinst.sh" />
  <PostInstallScript Include="scripts/postinst.sh" />
  <PreRemoveScript Include="scripts/prerm.sh" />
  <PostRemoveScript Include="scripts/postrm.sh" />
</ItemGroup>
```

`PrebuildCommand` runs in the recipe directory after upstream extraction, when used, and before local file mappings are overlaid. It can compile files or patch the extracted staging tree. Multiple matching commands run in declaration order. A nonzero exit stops the build.

Installation hooks become `DEBIAN/preinst`, `postinst`, `prerm`, and `postrm`. Matching custom fragments are appended in order, with inherited upstream scripts and generated systemd actions handled by the builder. Check the final control archive for ordering and shell syntax. Hooks must handle dpkg's action arguments and repeated execution; they run during package installation, not during `apkg build`.

## Systemd units

```xml
<ItemGroup>
  <SystemdUnit Include="service/my-daemon.service" AutoEnable="true" UsePreset="true" />
</ItemGroup>
```

The unit is installed with default mode `644`. `AutoEnable` defaults to true. With it enabled, generated scripts enable and start on a new install, use `try-restart` on upgrade, stop on removal, and disable/reload as appropriate during removal/purge. `UsePreset=true` selects `systemctl preset` rather than unconditional enablement, allowing a shipped preset to express policy. Its default is false. `AutoEnable=false` suppresses the automatic enable/start policy; inspect generated scripts for the exact lifecycle of the version you use.

## Dpkg triggers

```xml
<ItemGroup>
  <DpkgTrigger Include="/etc/dconf/db" Type="interest-noawait" />
  <DpkgTrigger Include="/usr/share/glib-2.0/schemas" Type="interest" />
</ItemGroup>
```

These create one `Type Name` line per item in `DEBIAN/triggers`. Supported types are `interest`, `interest-noawait` (default), `activate`, and `activate-noawait`. Interest declarations request notification; activation declarations activate a trigger. Implement the appropriate `postinst triggered` behavior rather than assuming the declaration performs the update itself. Conditions can limit a trigger to selected targets.

## Dependency source validation

```xml
<ItemGroup>
  <DependencyCheckSource Url="https://archive.ubuntu.com/ubuntu"
      SuiteMap="noble-addon=noble resolute-addon=resolute"
      Condition="'$(Arch)' == 'amd64'" />
  <DependencyCheckSource Url="https://apkg.example.com/artifacts/anduinos/"
      SuiteMap="noble-addon=noble-addon resolute-addon=resolute-addon" />
</ItemGroup>
```

`Url` is required. `SuiteMap` maps package targets to the source's suite names; without it the target suite is used unchanged. Sources and dependency items are evaluated per target. Multiple sources form a union: a package or provided virtual name found in any applicable source satisfies name validation. For alternatives, one available alternative suffices.

The validator checks `Dependency` and `Recommend` item names across the declared suite/architecture matrix. Version constraints are stripped, so this is not full dependency solving. `all` targets use the amd64 index as the lookup entry point. Missing names and per-source network failures are warnings. With no sources, online dependency validation is skipped.

Run `apkg lint` explicitly to invoke this online validator. In the reviewed implementation, `build` and normal `publish` run the static linter; they do not call the entire `lint` command's online validation pipeline.

## Static lint and build outputs

Errors include missing required metadata, invalid package names, empty required file sources/targets, unsupported conditions, incomplete upstream declarations, empty mapping targets, and empty dependency source URLs. Warnings cover missing maintainer/target defaults, missing files that may be generated, an empty payload, incomplete suite mappings, omitted default upstream component/architecture, and missing signing-key files.

An error stops the build. Warnings require interpretation; they are neither proof of failure nor permission to ignore a missing static asset. Always declare distro and architecture explicitly rather than relying on fallback values.

Each selected target stages files under `obj/<suite>_<arch>/`, generates control metadata and scripts, and invokes `dpkg-deb --build --root-owner-group`. Inspect that staging directory after failures. Output files are `bin/<name>_<version>_<suite>_<arch>.deb`. A plain build uses the declared matrix; `--suite` and `--arch` narrow it, and `--all` requests the full matrix.

See [package development](Develop-AnduinOS-Packages.md) for a practical matrix and [bundle format](Bundle-and-Upload-Reference.md) for the next stage.
