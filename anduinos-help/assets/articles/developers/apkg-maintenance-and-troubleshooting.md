# Apkg Maintenance and Troubleshooting

Use this chapter after [building a package](Build-Your-First-Package.md) or [hosting a server](Host-an-Apkg-Server.md). Diagnose the stage that failed before rebuilding or re-uploading a large package.

## Follow the evidence through the pipeline

| Stage | Evidence to inspect |
|-------|---------------------|
| Recipe and application build | `apkg --version`, lint result, prebuild output, application tests |
| Debian package | `dpkg-deb --info` and `--contents`; exact version and architecture |
| Upload | CLI result, target warnings, server package/revision records |
| Repository sync | Background job result and pending repository state |
| Signing/promotion | Signing job result and live `InRelease` |
| Static distribution, if used | Export job result and each edge's active snapshot |
| Client | `apt update`, `apt-cache policy`, installed version and actual application behavior |

## Common development failures

### The CLI is missing or needs a framework

Run `type -a apkg`, `dotnet --list-sdks`, and `dotnet --list-runtimes`. Check which installation your shell selected. Use the [CLI installation guide](Install-the-CLI.md) for the matching installation method; changing a distribution-managed package with `dotnet tool update` updates a different installation.

### A build hook cannot find a command

`gjs: command not found`, a missing compiler, or a missing gettext tool means the builder lacks a build-time prerequisite. Install it in both the local build environment and CI image. Runtime `<Dependency>` entries do not provision the build host.

### The build succeeds but tests never run

Check that `PrebuildCommand` is in an `ItemGroup`, that its `Condition` matches the target, and that the build log contains the expected invocation. Execute the test independently to distinguish an unexecuted hook from a passing test. A test that pins a neighboring package's old version may itself need maintenance; inspect the assertion before changing production metadata to satisfy it.

### One architecture builds and another fails

Compare the toolchain, native libraries, and target output paths. Build the failing target explicitly with `--distro`, `--suite`, and `--arch`. Do not relabel an amd64 binary as arm64 or use `all` to suppress an architecture failure. A valid package envelope does not prove that the binary runs on its claimed target.

### An unexpected old file appears in the package

Inspect generated staging directories and the recipe's file mappings. Use a fresh build directory or the package's documented cleanup procedure, then rebuild. Avoid `publish --no-build` when `bin/` contains stale targets or multiple versions. Keep cleanup scoped to generated files, never the entire source tree.

## Upload succeeds but APT cannot see the version

Check in this order:

1. The upload reached the intended development or production server.
2. The target distro and suite match a repository, its components include the package component, and its architecture accepts the payload (`all` is architecture-independent).
3. The publishing account has access and owns the package where ownership is required.
4. Repository sync completed after that upload.
5. Signing/promotion completed after sync.
6. If using static edges, export and edge synchronization completed after promotion.
7. The client refreshed its indexes and `apt-cache policy` shows the expected source and version.

The management/API origin and public download origin may differ. Query the same download origin the client's source file uses. A green pipeline does not establish that every CDN or edge node serves the new repository yet.

### Large uploads and timeouts

The CLI supports chunked uploads; inspect `apkg push --help` for `--chunk-size`. Chunking avoids sending the entire bundle in one request, but the final merge, hash verification, unpacking, and database work still need time and storage. A reverse-proxy timeout during finalization may occur after all chunks arrived.

Check server logs, available disk space, storage latency, and the package record before retrying. Reducing the chunk size does not necessarily solve a slow finalization request. Compare the proxy's timeout with the actual operation and use the upload behavior supported by your server/client versions.

### Signature errors or wrong candidates

Check the key fingerprint, `Signed-By` path, repository certificate, and whether `InRelease` exists after signing. Confirm that the configured URL is the public APT origin and that the suite exists. For a wrong candidate, inspect APT pinning, duplicate sources, and other repositories publishing the same package. Keep signature checks enabled.

## Back up a single-server deployment

For the SQLite Compose example, make a consistent backup while the application is stopped. Back up `data/`, the Compose files, and any reverse-proxy configuration. The database contains sensitive accounts and signing material, so restrict access to the backup.

Run in `~/Source/ServiceConfigs/Apkg`:

```bash title="Create a consistent backup"
mkdir -p backups
chmod 700 backups
(
  set -euo pipefail
  apkg_backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
  sudo docker compose stop apkg
  trap 'sudo docker compose start apkg' EXIT
  sudo tar -czf "backups/apkg-$apkg_backup_stamp.tar.gz" data compose.yaml
  sudo chmod 600 "backups/apkg-$apkg_backup_stamp.tar.gz"
)
sudo docker compose ps
```

For the HTTPS variant, use both Compose files in the commands (including the restart trap) and include `compose.https.yaml`, `Caddyfile`, `caddy-data/`, and `caddy-config/` in the backup plan. Stop the proxy too if you need a consistent copy of its mutable state. The subshell attempts to restart Apkg even if archiving fails; check its result and do not mistake an incomplete archive for a usable backup.

Copy successful backups to separate storage. For MySQL, use a consistent database dump or snapshot together with package storage; copying a running database's raw directory is not a substitute. Test restoration into a separate deployment using the same image digest and isolated ports, then check login, signing, repository metadata, and a client install before considering the backup verified.

## Upgrade deliberately

Record the running image and create a verified backup before an upgrade:

```bash
sudo docker compose images
sudo docker image inspect aiursoft/apkg:latest --format '{{json .RepoDigests}}'
```

Record the digest associated with the running container if the local tag has moved. Test the new image against a restored copy first. For a deployment that intentionally tracks a tag:

```bash title="Apply an evaluated update"
sudo docker compose pull apkg
sudo docker compose up -d apkg
sudo docker compose logs --tail=100 apkg
curl --fail http://127.0.0.1:5000/health
```

For a pinned deployment, replace the image digest in Compose with the evaluated digest before running `up -d`. Use both `-f` arguments for the HTTPS variant. Check repository jobs and an APT client after startup. A database migration can make an older image incompatible; rollback may require restoring the matching pre-upgrade database and storage, not just reverting an image tag.

`docker compose down` stops/removes this deployment's containers and network while retaining the bind-mounted data. Do not delete `data/` to resolve a startup failure. It contains the repository state you need to recover.

## Add static edge distribution when needed

Apkg can export the live repositories into a static tree through `RepositoryExportJob`. With the server example's `Storage__ExportPath`, the exported root is `data/export/`, with an `artifacts/` subtree containing metadata, public certificates, and package files.

To introduce separate download servers:

1. Complete repository sync and signing on the origin.
2. Run the export job and verify the exported snapshot.
3. Transfer the complete export to a staging location on each edge.
4. Switch each edge to the new snapshot only after transfer and checks finish.
5. Serve the export root so `/artifacts/...` URLs remain identical.
6. Configure the public APT origin and test signed APT updates through that origin.

Expose only the export tree through a static server or restricted synchronization endpoint. Do not expose the application data root: it includes the database, settings, and private state. The upload/API origin should continue pointing to the web application; static nodes do not implement `/api/`.

AnduinOS's [edge deployment scripts and architecture notes](https://github.com/anduin2017/ApkgServerInitScript) are an advanced example using Caddy and synchronization from an origin. Review their domains, network access rules, and host changes before adapting them. A single signed repository is sufficient to learn the publishing workflow; a global edge network is an optional operational expansion.
