# Build Your First Debian Package

This tutorial packages a small shell command called `hello-apkg`. You will create a recipe, build a Debian package, inspect its contents, and install it. No server, API key, or system configuration package is required.

First [install the CLI](Install-the-CLI.md). Use a fresh directory so an older recipe or output cannot be mistaken for this example.

## Create the project

```bash title="Create the package directory and recipe"
mkdir -p ~/Source/hello-apkg/assets
cd ~/Source/hello-apkg
apkg new --name hello-apkg
```

`new` creates `hello-apkg.aosproj` in the current directory. It supplies example metadata and several targets; replace them with the deliberately small target matrix below.

Create `assets/hello-apkg` with this content:

```sh title="assets/hello-apkg"
#!/bin/sh
printf '%s\n' 'Hello from an Apkg-built Debian package!'
```

Replace `hello-apkg.aosproj` with:

```xml title="hello-apkg.aosproj"
<Project Sdk="Aiursoft.Apkg.Sdk">
  <PropertyGroup>
    <PackageName>hello-apkg</PackageName>
    <PackageVersion>1.0.0-1</PackageVersion>
    <Maintainer>Your Name &lt;you@example.com&gt;</Maintainer>
    <PackageDescription>A small command packaged with Apkg</PackageDescription>
    <Section>utils</Section>
    <TargetDistro>anduinos</TargetDistro>
    <TargetSuites>resolute-addon</TargetSuites>
    <TargetArchitectures>all</TargetArchitectures>
    <Component>main</Component>
  </PropertyGroup>
  <ItemGroup>
    <IncludeFile Include="assets/hello-apkg"
                 Target="/usr/bin/hello-apkg" Mode="755" />
  </ItemGroup>
</Project>
```

Replace the maintainer placeholder with your own details before distributing the package. For a real project, also include its license and copyright notices; declaring a license identifier does not replace shipping the required license text.

| Field | Meaning in this example |
|-------|-------------------------|
| `PackageName` | The name used by `apt install hello-apkg` |
| `PackageVersion` | Upstream version `1.0.0`, packaging revision `1` |
| `TargetDistro` and `TargetSuites` | Server repository coordinates; they do not convert an incompatible application to another OS |
| `TargetArchitectures` | `all` because the script contains no architecture-specific binary |
| `Include` | Source path relative to the recipe directory |
| `Target` | Destination path inside the installed package |
| `Mode` | Packaged permissions; `755` makes the command executable |

The equivalent way to add the file mapping with the CLI is `apkg add ./assets/hello-apkg --target /usr/bin/hello-apkg --mode 755`. Do not add it again if you already copied the complete XML above.

## Build and inspect

```bash title="Validate and build"
apkg lint
apkg build
```

The declared target fields are sufficient for a plain `apkg build`. For this recipe, expect:

```text
bin/hello-apkg_1.0.0-1_resolute-addon_all.deb
```

Inspect the package before installing it:

```bash title="Inspect metadata and installed paths"
dpkg-deb --info ./bin/hello-apkg_1.0.0-1_resolute-addon_all.deb
dpkg-deb --contents ./bin/hello-apkg_1.0.0-1_resolute-addon_all.deb
```

Check the package name, version, architecture, and `/usr/bin/hello-apkg` entry. This example should not contain GRUB configuration, system services, or maintainer scripts.

## Install and run

On your test machine, first make sure an unrelated package or command does not already use this example name:

```bash
command -v hello-apkg
dpkg-query -W hello-apkg
```

No match is expected on the first run. Then install the exact file:

```bash title="Install the local Debian package"
sudo apt install ./bin/hello-apkg_1.0.0-1_resolute-addon_all.deb
hello-apkg
dpkg-query -W -f='${Package} ${Version}\n' hello-apkg
dpkg -L hello-apkg
```

The command should print `Hello from an Apkg-built Debian package!`. The `./` in the install command tells APT that the argument is a local file. APT can resolve declared dependencies from your configured repositories.

## Make an upgrade

Edit the script's greeting and change `PackageVersion` to `1.0.0-2`. Rebuild and install the new filename:

```bash title="Test a packaging revision"
apkg lint
apkg build
sudo apt install ./bin/hello-apkg_1.0.0-2_resolute-addon_all.deb
hello-apkg
dpkg-query -W -f='${Version}\n' hello-apkg
```

Use a higher version for changed published content. For a local rebuild that intentionally keeps the same version, APT may require `--reinstall`; that does not make reusing a published version a good release workflow.

To remove the example from the test machine:

```bash
sudo apt remove hello-apkg
```

Your project files remain in `~/Source/hello-apkg`. If you follow the publishing tutorial, its filenames assume `1.0.0-1`; substitute `1.0.0-2` if you completed the upgrade exercise.

Next: [Develop AnduinOS packages](Develop-AnduinOS-Packages.md), or [host a server](Host-an-Apkg-Server.md) to distribute your package.
