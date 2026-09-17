# Server Configuration and Background Jobs

Use [Host an Apkg server](Host-an-Apkg-Server.md) for the initial deployment. This reference covers configuration, scheduling, storage, and operational details moved from Apkg's original operations guide.

## Configuration sources

The application reads ASP.NET Core configuration, including `appsettings.json` and environment variables. Nested environment keys use double underscores. The Docker entrypoint initializes `/data/appsettings.json` from the image when absent and links the application configuration to that persisted file.

| Setting | Purpose |
|---------|---------|
| `ConnectionStrings:DbType` | `Sqlite` or `MySql` in deployed environments |
| `ConnectionStrings:DefaultConnection` | Database connection string |
| `Storage:Path` | Package/application storage root |
| `Storage:ExportPath` | Static export root; explicitly persist it when using exports |
| `AppSettings:AuthProvider` | `Local` or `OIDC` |
| `AppSettings:Local:AllowRegister` | Whether users can self-register |
| `AppSettings:Local:AllowWeakPassword` | Local password policy option |
| `AppSettings:OIDC:*` | Authority, client ID/secret, and claim mapping for the identity provider |
| `GlobalSettings:PublicAptServerDomain` | Public package/key/media origin |

Global settings supplied by configuration override stored web settings. The UI cannot overwrite a value controlled by an environment variable. Keep the public APT origin distinct from an upload/API origin if you use static download nodes.

Example connection values:

```json
{
  "ConnectionStrings": {
    "DbType": "Sqlite",
    "DefaultConnection": "DataSource=/data/app.db;Cache=Shared"
  },
  "Storage": {
    "Path": "/data",
    "ExportPath": "/data/export"
  }
}
```

For MySQL, use `DbType: MySql` and a connection such as `Server=db;Database=apkg;Uid=apkg;Pwd=YOUR_SECRET;`. Inject credentials through your deployment's secret/configuration mechanism. Provider selection uses the switchable database layer; changing the provider name does not migrate existing data between engines.

Local and OIDC authentication are alternative modes. Configure the identity provider and review account/session behavior before switching a live instance; clear or invalidate existing sessions as appropriate. Unit tests select InMemory separately, which is not a production database setting.

## Initial accounts and signing

A fresh database seeds an administrator with email `admin@default.com` and password `Admin@123456!`. Change it during isolated setup. Seeding can create a default certificate named `anduinos` and example mirror/repository rows. Review them before allowing unattended synchronization.

Repositories choose their signing certificate. Preserve the private key and database together with package storage. The reviewed implementation signs locally; external Vault or cloud signing integration remains a design proposal. Never expose the application data root as a public static directory.

## Runtime and deployment paths

The Docker image exposes port 5000, runs the application under `/app`, and persists configuration and data under `/data`. It includes tools needed for repository signing and AppStream processing, such as GPG and image/metadata tooling. Its healthcheck requests `/health`, with an initialization grace period.

The systemd installation script is an alternative deployment path. It installs .NET/Node prerequisites, obtains the source, installs frontend dependencies, publishes to `/opt/apps/apkg`, and registers a systemd service. It also uses a temporary checkout and cleanup steps; inspect it before running on a host with existing deployment state.

To run the web application from source during development, install .NET 10 and Node.js, run `npm install` in `src/Aiursoft.Apkg/wwwroot`, then run `dotnet run --project src/Aiursoft.Apkg/Aiursoft.Apkg.csproj`. Use an isolated development database/storage directory. Build the frontend as directed by its `package.json` when validating production assets. See [platform contribution](Contributing-to-Apkg.md).

## Scheduled jobs

The following schedule comes from the reviewed `Startup.cs` registrations. Delays are relative to application startup, not wall-clock cron times. Comments in older documents do not override the actual registration values.

| Job | Period | Initial delay |
|-----|--------|---------------|
| `MirrorSyncJob` | 6 hours | 10 minutes |
| `RepositorySyncJob` | 15 minutes | 1 minute |
| `RepositorySignJob` | 5 minutes | 25 minutes |
| `GarbageCollectionJob` | 70 minutes | 15 minutes |
| `OrphanAvatarCleanupJob` | 6 hours | 5 minutes |
| `ApkgTempCleanupJob` | 10 minutes | 7 minutes |
| `ApkgOrphanPackageCleanupJob` | 10 minutes | 8 minutes |
| `RepositoryExportJob` | 1 hour | 30 minutes |
| `ContentsCacheBackfillJob` | 30 days | 48 hours |

The conceptual schedule is startup + initial delay + repeated period; queue execution and runtime also affect completion. `RepositoryDependencyCheckJob` is an on-demand repository dependency check. Registered jobs can be triggered through `/Jobs`; wait for required predecessor jobs and avoid scheduling duplicate work while the same job is running.

The current temporary cleanup removes old temporary revisions with no attached Debian records after 30 minutes and abandoned chunk sessions after 24 hours. Orphan-family cleanup removes families with no revisions after two hours. These conditions are more specific than "delete every unpublished revision after 30 minutes"; inspect records when diagnosing a partial upload.

## Align repository and package configuration

Publishers need a shared matrix of distro, suites, components, and client architectures. A repository can cover multiple architectures, comma-separated. A package architecture of `all` is eligible for concrete-architecture indexes. Current upload matching also requires component membership.

After changing a repository's architecture or components, sync and sign it before expecting its index layout to change. Re-upload packages that were skipped because no matching repository existed. For clients, verify the exact configured source with `apt-cache policy` after `apt update`, rather than assuming a generic package lookup proves every suite is populated.

## Catalogs, exports, and recovery

Set `PublicAptServerDomain` before processing [AppStream screenshots](AppStream.md). Catalog generation produces DEP-11 metadata and icon indexes; media are served through content-addressed public paths. A catalog failure should leave the prior signed primary available while the new pending repository is repaired.

Exports produce an `artifacts/` tree under `Storage:ExportPath`, including public certificates, indexes, and package payloads. The exporter stages output and switches it into place after completing the export. Hardlinks can avoid copying CAS payloads on the same filesystem; cross-filesystem layouts require copies and more storage. Only export output belongs on a static download server.

Use [maintenance procedures](Maintenance-and-Troubleshooting.md) for backups, restoration, upgrades, and edge distribution. Database snapshots, private keys, and referenced payloads must be mutually consistent; neither a Docker image nor a public export alone is a full control-plane backup.
