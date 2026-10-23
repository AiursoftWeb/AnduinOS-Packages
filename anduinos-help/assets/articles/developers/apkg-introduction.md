# Apkg: Build and Distribute Debian Packages

Apkg is the package development and publishing platform used by AnduinOS. It connects application source files to the standard Debian package tools: developers build with `apkg`, and users install and update with `apt`.

You can use it to package a shell script, ship a desktop application, maintain system defaults, or distribute software from your own APT repository. Your application does not have to be written in .NET, and local builds do not require a server or an upload account.

## Two parts of the platform

| Part | Where it runs | What it does |
|------|---------------|--------------|
| Apkg SDK and CLI | Development machine or CI runner | Read package definitions, run build commands, assemble Debian packages, and prepare or upload release bundles |
| Apkg web server | Your server | Manage repositories and signing keys, receive packages, generate signed APT indexes, and optionally mirror upstream repositories |

The SDK is the library behind the `apkg` command. Most package developers only need to install the CLI. The web server has a management interface, an upload API, and endpoints serving standard APT repository content.

An Apkg server is an APT repository server. A client does not need special Apkg software to consume its packages: ordinary Debian-compatible APT clients can use its signed repository. A package must still be compatible with the client's distribution, dependencies, and architecture.

## Three file formats

| File | Purpose | Consumer |
|------|---------|----------|
| `.aosproj` | XML recipe describing metadata, files, dependencies, and build targets | `apkg` CLI |
| `.deb` | Installable Debian package containing files and package metadata | `apt` and `dpkg` |
| `.apkg` | Upload bundle containing Debian packages and a distribution manifest | Apkg server |

```text
Source files + .aosproj
    │ apkg build
    ▼
Local .deb ──────────────────────────────► apt install ./package.deb
    │ apkg publish builds and bundles
    ▼
.apkg archive
    │ apkg push
    ▼
Apkg server → repository sync → signing → APT repository
                                             │
                                     apt update / apt install
```

`apkg publish` creates a local archive; it does not upload it. `apkg deploy` combines publication and upload. Repository synchronization and signing happen on the server after upload.

## Choose a starting point

1. [Install the CLI](Install-the-CLI.md) on your development machine.
2. [Build your first package](Build-Your-First-Package.md) with a small executable script, then install and upgrade it locally.
3. [Develop AnduinOS packages](Develop-AnduinOS-Packages.md) using real package layouts, build hooks, dependencies, and multiple targets.
4. [Host an Apkg server](Host-an-Apkg-Server.md) with persistent storage and a signed standalone repository.
5. [Publish packages and configure APT](Publish-Packages.md) to deliver the sample package to another machine and automate publication in CI.
6. [Maintain and troubleshoot](Maintenance-and-Troubleshooting.md) the build and distribution pipeline.

The tutorials share one example: `hello-apkg`, version `1.0.0-1`, targeting `anduinos / resolute-addon / all`, in component `main`. These are tutorial coordinates, not permission to publish to the official AnduinOS repository. Use a server you administer or one that has granted you upload access.

## Source code and reference material

- [Apkg source](https://github.com/AiursoftWeb/Apkg): web application, CLI, SDK, and tests.
- [AnduinOS-Packages](https://github.com/AiursoftWeb/AnduinOS-Packages): the package recipes and applications that customize AnduinOS.
- [Recipe reference](Aosproj-Reference.md): XML properties, file mappings, conditions, hooks, and validation.
- [Design and architecture](Design-and-Architecture.md): publication invariants, storage, trust, and planned features.
- [Bundle and upload reference](Bundle-and-Upload-Reference.md): package identity, manifests, ownership, and conflicts.
- [Platform development](Contributing-to-Apkg.md): changing the SDK, CLI, or web application itself.

AnduinOS Documentation is the home of the Apkg manuals, including the design and implementation references formerly stored with the Apkg source. [Migration notes](Documentation-Migration.md) map that material into this section and retain historical snapshots for provenance.

The examples in this section were checked against CLI `10.0.55` and the corresponding local source. Use `apkg --version` and command-specific `--help` when comparing another release. The `.aosproj` syntax resembles MSBuild, but Apkg parses it itself; use `apkg build`, not `dotnet build package.aosproj`.
