# Apkg Documentation Ownership and Migration

AnduinOS Documentation is the maintained home for the Apkg manuals. The Apkg source repository links here instead of maintaining a parallel `docs/` tree. Package developers, server administrators, and platform contributors can all start in the [Apkg section](Introduction.md).

## Where the original material lives now

The migration reviewed the five documentation files at Apkg source commit `6009b7f76407e76d5d340babc445df894a68a3eb`, totaling 1,912 lines. Their content was reorganized into tutorials and English references:

| Original document | Maintained destinations |
|-------------------|-------------------------|
| `aosproj.md` | [Recipe properties/items](Aosproj-Reference.md), [upstream derivation](Upstream-Packages.md), [AppStream](AppStream.md), [bundle semantics](Bundle-and-Upload-Reference.md), and the CLI/build tutorials |
| `design.md` | [Design and architecture](Design-and-Architecture.md), [upload behavior](Bundle-and-Upload-Reference.md), [AppStream](AppStream.md), and [server jobs](Server-Configuration.md) |
| `development.md` | [Platform contribution](Contributing-to-Apkg.md), [current job scheduling](Server-Configuration.md#scheduled-jobs) |
| `operations.md` | [Server setup](Host-an-Apkg-Server.md), [configuration](Server-Configuration.md), [maintenance](Maintenance-and-Troubleshooting.md), and [APT publication](Publish-Packages.md) |
| `preflight.md` | [CI and preflight API](CI-and-Preflight.md), [publication tutorial](Publish-Packages.md) |

## Corrections made during migration

The source code and working CLI take precedence over contradictory old prose. These corrections preserve the original topic while preventing an obsolete claim from becoming an instruction:

| Old description | Source-checked interpretation |
|-----------------|-------------------------------|
| Component does not participate in repository matching | `RepositoryTargetService` filters by component membership as well as distro/suite/architecture |
| Uploaded local packages have separate permanent `LocalPackages/` storage | `DebUploadService` writes their bytes into SHA-256 CAS |
| `build` runs all online dependency validation from `lint` | Build uses static lint; the CLI lint handler additionally calls the online dependency validator |
| Upstream resolution invokes isolated `apt-get` and falls back to the host's trusted keys | The reviewed builder uses `Aiursoft.AptClient`; omitted `UpstreamSignedBy` enables insecure signature handling, so recipes should declare a verified key |
| A prebuild hook can generate the key needed for upstream download | Upstream resolution happens before the prebuild hook |
| File permissions are inherited when `IncludeFile` has no Mode | Default file mode is `644`; executable mappings need `755` or `IncludeScript` |
| Full MSBuild logical precedence/nesting can be assumed | The current evaluator is limited; use simple verified conditions |
| Forced upload can never cause a lower version to become public | Highest local version wins among locals, and a local override can replace a higher upstream version |
| Disabling GPG signing preserves upstream signatures | It produces an unsigned promoted repository; static replication preserves signed origin bytes instead |
| Vault/cloud signing and a general rule engine are available features | They were design proposals, distinct from the implemented local signing and local-package overrides |
| Repository sync is every four hours; export is every ten minutes | Current registrations use 15 minutes and one hour, respectively |
| All unpublished revisions expire after 30 minutes | Current temporary cleanup filters temporary revisions without attached Debian records; other cleanup has separate conditions |
| MSTest cannot assert asynchronous exceptions | The reviewed tests use `Assert.ThrowsAsync`; old API-name limitations were version-specific |
| An unversioned archive and positional push/add-source arguments can be copied verbatim | Use CLI output paths and the verified `--file` / `--url` options |

Strategic percentages, response-time targets, planned community moderation, arbitrary override rules, and emergency snapshot rollback are retained as historical goals rather than promises of current behavior. The architecture chapter records their rationale and limitations.

## Historical source snapshots

For lossless provenance, the original files are retained here as byte-preserved text downloads in their original language. They are historical records, **not current setup instructions**; use the maintained pages above for commands and behavior.

- [Original recipe/manifest reference](History/aosproj.md.txt)
- [Original design document](History/design.md.txt)
- [Original platform development notes](History/development.md.txt)
- [Original operations guide](History/operations.md.txt)
- [Original preflight specification](History/preflight.md.txt)
- [Source hashes and complete line-range mapping](History/manifest.json)

The manifest records each original path, source revision, byte count, SHA-256, and a complete partition of its lines into destination topics. The snapshots make historical wording and examples recoverable after removing the source repository's duplicate documents. Future changes belong in the maintained chapters, not in these frozen snapshots.
