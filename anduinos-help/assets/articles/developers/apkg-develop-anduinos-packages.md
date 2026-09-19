# Develop AnduinOS Packages

[AnduinOS-Packages](https://github.com/AiursoftWeb/AnduinOS-Packages) holds the package definitions, applications, assets, and integration scripts used by AnduinOS. Each package directory is a separate Apkg project. Work on the relevant package rather than rebuilding the entire operating system for every local change.

Complete [the first package tutorial](Build-Your-First-Package.md) before using this chapter as a development reference.

## Find the right project

```bash title="Inspect the package sources"
git clone https://github.com/AiursoftWeb/AnduinOS-Packages.git
cd AnduinOS-Packages
find . -name '*.aosproj' -not -path '*/obj/*' -not -path '*/bin/*'
```

Read the repository README, the chosen recipe, and any `README.md`, `build.sh`, or `download.sh` in that package. A build hook is executable code and may compile software or download upstream assets.

| Directory | Role |
|-----------|------|
| `assets/` | Files included as-is: configuration, icons, scripts, extension source |
| `src/` | Application source to compile |
| `scripts/` | Debian installation/removal hooks |
| `tests/` | Package or application checks |
| `deploy/` | Generated files staged for packaging |
| `obj/` | Apkg/build intermediates |
| `bin/` | Generated packages and build output |

Generated output is ignored by the root `.gitignore`. Keep source and recipes under version control, and check `git status --short` after building.

For a small real example, `anduinos-kernel-parameters` packages a GRUB drop-in. You can inspect and build it without installing it:

```bash
cd anduinos-kernel-parameters
apkg lint
apkg build
dpkg-deb --contents ./bin/anduinos-kernel-parameters_2.0.2-1+resolute_resolute-addon_all.deb
```

Use the filename printed by the build if the repository has a newer version. Installing this particular package changes boot configuration; use the harmless `hello-apkg` example for first-time installation practice.

## Run application builds and tests

For compiled software, build into `deploy/`, then map the output into the package. Put prebuild commands inside an `ItemGroup`:

```xml title="Build hook inside the Project element"
<ItemGroup>
  <PrebuildCommand Run="bash build.sh" />
  <IncludeFile Include="deploy/my-tool"
               Target="/usr/bin/my-tool" Mode="755" />
</ItemGroup>
```

For example, a C application could use:

```bash title="build.sh"
#!/bin/bash
set -euo pipefail
mkdir -p deploy
cc -O2 -Wall -Wextra -o deploy/my-tool src/main.c
```

Install `build-essential` on that builder. Rust applications can call Cargo, Python applications can package their scripts, and desktop applications can include icons and `.desktop` files. The application language is independent of Apkg's implementation language.

On a clean checkout, lint may warn that `deploy/my-tool` does not exist yet. That is expected only when the verified build hook creates it. Confirm that the hook runs and the final package contains the executable; do not dismiss missing-file warnings for static assets.

!!! warning "A successful package build does not prove tests ran"

    Apkg reads `PrebuildCommand` from `ItemGroup`, not `PropertyGroup`. Check for the prebuild log and actual test output. Run the project's documented tests explicitly when they are not part of a verified hook. A misplaced XML element can leave you with a valid `.deb` that never ran the intended checks.

Build hooks run on the builder, not inside an automatically provisioned target distribution. Install their tools on your development machine and CI image. A `<Dependency Include="gjs" />` declares a dependency for package installation; it does not install `gjs` before a build hook executes.

## Declare runtime dependencies and configuration

These snippets belong inside the recipe's `Project` element:

```xml title="Runtime dependencies and an editable configuration file"
<ItemGroup>
  <Dependency Include="bash (&gt;= 4.4)" />
  <ConfFile Include="assets/my-tool.conf" Target="/etc/my-tool.conf" />
</ItemGroup>
```

Use `ConfFile` for configuration whose local edits should be managed by dpkg during upgrades. Use `IncludeFile` for ordinary package-owned files. Avoid claiming paths owned by another package unless you deliberately implement and test the relevant Debian replacement or diversion behavior.

If your project declares `DependencyCheckSource`, it supplies repository information for dependency validation. It does not change the target machine's sources or provision your compiler. Follow the repository's dependency-check procedure as well as checking the final `Depends` field.

## Target suites and architectures

For scripts and static assets, use `all`. For native binaries, list only architectures you actually build and test:

```xml title="Example build matrix"
<PropertyGroup>
  <TargetDistro>anduinos</TargetDistro>
  <TargetSuites>noble-addon resolute-addon</TargetSuites>
  <TargetArchitectures>amd64 arm64</TargetArchitectures>
  <PackageVersion>1.0.0-1+$(SuiteShortName)</PackageVersion>
  <SuiteShortNameMap>noble-addon=noble resolute-addon=resolute</SuiteShortNameMap>
</PropertyGroup>
```

Use this to replace the corresponding properties, not as a second conflicting set. This matrix produces four targets. An amd64 binary does not become arm64 merely because it is packaged under that target: install a cross-toolchain or use native builders and select the correct output.

```xml title="Select build commands and payloads by architecture"
<ItemGroup>
  <PrebuildCommand Run="bash build.sh amd64" Condition="'$(Arch)' == 'amd64'" />
  <PrebuildCommand Run="bash build.sh arm64" Condition="'$(Arch)' == 'arm64'" />
  <IncludeFile Include="deploy/amd64/my-tool" Target="/usr/bin/my-tool"
               Mode="755" Condition="'$(Arch)' == 'amd64'" />
  <IncludeFile Include="deploy/arm64/my-tool" Target="/usr/bin/my-tool"
               Mode="755" Condition="'$(Arch)' == 'arm64'" />
</ItemGroup>
```

This variant requires a `build.sh` that accepts the architecture argument and creates those paths; the simple C script above does not implement cross-compilation.

```bash title="Build one target, then the declared matrix"
apkg build --distro anduinos --suite resolute-addon --arch amd64
apkg build --all
```

## Installation hooks and desktop integration

Maintainer scripts run with package-manager privileges during installation, upgrade, or removal. Keep them short, handle repeated execution, and test the action arguments (`configure`, `remove`, `upgrade`, and so on). Apkg assembles the final script wrappers; inspect them in the `.deb` instead of assuming the source file is copied verbatim.

```bash title="Inspect control scripts without executing them"
apkg_control_dir=$(mktemp -d)
dpkg-deb --control ./bin/your-package.deb "$apkg_control_dir"
ls -l "$apkg_control_dir"
```

For GUI applications, include a desktop entry, appropriately sized icons, and [AppStream metadata](AppStream.md) so software centers can describe the package. For systemd services and dependency declarations, consult the [recipe reference](Aosproj-Reference.md) and a current neighboring package. [Upstream derivation](Upstream-Packages.md) repackages an existing Debian payload; it is not the same as recompiling that upstream application.

## A repeatable development cycle

1. Change the application or assets and update the package revision when preparing a release.
2. Run the package's own tests and `apkg lint`.
3. Build the intended suite and architecture, then inspect the exact `.deb`.
4. Install it on a compatible test machine and exercise startup, upgrade, and removal behavior.
5. Review source changes separately from generated files.
6. Follow the repository's contribution instructions and [publish](Publish-Packages.md) through an authorized repository.

Keep a record of the CLI version, target, package version, and test results. This makes a later CI failure much easier to compare with a successful local build.
