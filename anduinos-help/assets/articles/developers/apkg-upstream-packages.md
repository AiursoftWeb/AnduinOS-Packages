# Derive Packages from Upstream

Upstream derivation starts with an existing Debian package, extracts its payload and control data, and overlays your changes. Use it when maintaining distribution-specific versions of upstream files or applications. It does not compile the upstream source and does not remove the obligation to preserve upstream license notices.

## Upstream properties

Setting `UpstreamPackage` activates derivation.

| Property | Meaning |
|----------|---------|
| `UpstreamPackage` | Name to find in the upstream repository |
| `UpstreamUrl` | Repository base URL; required with `UpstreamPackage`; can occur multiple times with conditions |
| `UpstreamDistro` | Upstream distribution context, required |
| `UpstreamSuite` | Upstream suite or target-dependent expression, required |
| `UpstreamSuiteMapping` | Output-to-upstream suite mappings, separated by spaces or commas |
| `UpstreamComponent` | Upstream component, default `main` |
| `UpstreamArch` | Architecture to search, default `all`; may use `$(Arch)` |
| `UpstreamSignedBy` | Relative path to the trusted upstream public keyring |
| `SuppressUpstreamScripts` | Default false; true omits inherited maintainer scripts |
| `SuppressUpstreamDependencies` | Space/comma-separated base package names to omit from inherited `Depends` |
| `AutoConvertUpstreamExactVersions` | Default true; relax inherited `= version` relationships to `>= version` |

The version conversion applies to inherited dependency, recommendation, and suggestion strings. Disable it if your upstream package requires exact lockstep versions. Suppressing dependencies can make a package uninstallable or broken at runtime; validate the resulting control file and behavior.

## Example recipe

This illustrative `base-files` derivative follows upstream versions while changing branding. Build it in a test checkout; do not install a core-package replacement on your everyday system just to learn the format.

```xml
<Project Sdk="Aiursoft.Apkg.Sdk">
  <PropertyGroup>
    <PackageName>base-files</PackageName>
    <PackageVersion>$(UpstreamVersion)-anduinos1</PackageVersion>
    <PackageDescription>Distribution branding derived from upstream base-files</PackageDescription>
    <Maintainer>Your Distribution Team &lt;packages@example.com&gt;</Maintainer>
    <TargetDistro>anduinos</TargetDistro>
    <TargetSuites>noble-addon resolute-addon</TargetSuites>
    <TargetArchitectures>amd64 arm64</TargetArchitectures>
    <Component>main</Component>
    <UpstreamUrl Condition="'$(Arch)' == 'amd64'">https://archive.ubuntu.com/ubuntu/</UpstreamUrl>
    <UpstreamUrl Condition="'$(Arch)' == 'arm64'">https://ports.ubuntu.com/ubuntu-ports/</UpstreamUrl>
    <UpstreamDistro>ubuntu</UpstreamDistro>
    <UpstreamPackage>base-files</UpstreamPackage>
    <UpstreamSuite>$(Suite)</UpstreamSuite>
    <UpstreamSuiteMapping>noble-addon=noble resolute-addon=resolute</UpstreamSuiteMapping>
    <UpstreamComponent>main</UpstreamComponent>
    <UpstreamArch>$(Arch)</UpstreamArch>
    <UpstreamSignedBy>keys/ubuntu-archive-keyring.gpg</UpstreamSignedBy>
  </PropertyGroup>
  <ItemGroup>
    <IncludeFile Include="assets/issue" Target="/etc/issue" />
    <IncludeFile Include="assets/issue.net" Target="/etc/issue.net" />
  </ItemGroup>
</Project>
```

Provide the branding assets and a verified upstream public keyring at the declared paths. Review the upstream package's license and maintainer scripts. The version suffix shown is an example; use Debian version comparisons and your distribution's versioning policy before publishing a replacement.

## Resolution and build order

1. Resolve the target's conditional upstream URL and variable values. URLs use first-match selection; put an unconditional fallback last.
2. Map the resolved upstream suite when `UpstreamSuiteMapping` contains a matching key.
3. Read repository metadata, select a matching package with Debian version ordering, and download it.
4. Verify the downloaded payload against its SHA-256 metadata and extract the data/control archives.
5. Resolve `$(UpstreamVersion)` from the selected Debian package version.
6. Run applicable prebuild commands against the extracted staging tree.
7. Overlay local file mappings and merge package metadata and scripts.
8. Build the final `.deb`.

`$(SuiteShortName)` concerns your output version naming, whereas `UpstreamSuiteMapping` selects the upstream suite. They solve different problems. `$(UpstreamVersion)` is useful for following upstream security revisions without copying their version number manually into the recipe.

## Trust and architecture selection

!!! warning "Provide UpstreamSignedBy explicitly"

    The reviewed builder uses `Aiursoft.AptClient` to read HTTP repository metadata. If `UpstreamSignedBy` is omitted, it constructs the upstream source with insecure signature verification enabled. It does not automatically fall back to the host's trusted APT keyrings. Commit a verified public keyring alongside the recipe and check its fingerprint before trusting it.

The key file must exist before upstream resolution. A prebuild command runs too late to create a key needed for the upstream download. Static lint may only warn about the missing path, but the build rejects it.

HTTP metadata verification, package-index checksums, and downloaded `.deb` SHA-256 validation are distinct checks. `file://` repositories are useful for controlled fixtures; the builder reads local indexes and verifies the referenced payload hash, but they are not a substitute for authenticated remote metadata.

For a concrete upstream architecture the resolver searches it and `all`; for `UpstreamArch=all` it also uses the host's architecture index to locate architecture-independent packages. The resolver chooses the highest matching version among its candidates. Choose architecture-specific URLs for Ubuntu archives versus ports.

Older implementation notes described isolated `apt-get update/download` with `arch=` to avoid foreign-architecture index pollution. That describes an earlier implementation. The current direct APT client requests its selected indexes; registering a foreign architecture on the host is not how you choose its target.

## Metadata and script merging

Local maintainer, description, version, and supported overriding control properties define the output. Inherited `Depends` are merged with local dependencies and deduplicated by base package name; the inherited constrained relationship is retained when a local unconstrained entry names the same package. `SuppressUpstreamDependencies` removes selected inherited names before merging.

For `Provides`, `Conflicts`, `Replaces`, `Breaks`, `Recommends`, `Suggests`, and `Homepage`, an explicit supported local value takes precedence over an inherited value. `Section` and `Priority` additionally fall back to `utils` and `optional`. Inspect the final result rather than assuming every arbitrary upstream field is copied unchanged.

Inherited maintainer scripts, local fragments, and generated systemd actions are assembled in that order. `SuppressUpstreamScripts=true` keeps the upstream payload but omits the upstream script fragments, allowing explicit local lifecycle management. It does not suppress local or generated actions.

## Test a derived package

Use `apkg guess version` before building to see resolved target versions. Build each supported target, inspect `dpkg-deb --info`, extract its control scripts, and test upgrade behavior in a disposable system matching the target suite. Repository overrides and APT pinning are separate: a higher server-side priority does not prove the client's version policy will select or safely install the derivative.

Return to [the recipe reference](Aosproj-Reference.md) or continue to [publication](Publish-Packages.md).
