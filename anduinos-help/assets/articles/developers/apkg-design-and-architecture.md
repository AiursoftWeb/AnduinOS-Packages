# Apkg Design and Architecture

Apkg separates the work of maintaining a distribution from the APT client's job of resolving and installing software. Its SDK expresses package changes as recipes; its server combines upstream metadata and local packages into signed repository snapshots. This chapter describes that design, its invariants, and the distinction between implemented behavior and future ambitions.

## Why build Apkg?

The original design identified three goals: control the distribution's software supply chain, lower the effort required to package applications, and control publication across download infrastructure. Examples include replacing upstream launcher packages, maintaining distribution branding, and responding to a bad release without relying on a fleet of ad hoc client scripts.

Early notes used a 90% reduction in packaging effort and roughly 30-minute global removal as aspirations. They are not benchmark results or service guarantees. Actual publication/removal latency depends on jobs, mirrors, edges, and client caches.

Apkg is a distribution-building and publication platform, not a replacement dependency solver. Users continue using APT. The declared multi-suite matrix expresses release differences without requiring a separate source branch for every supported distribution.

## Ingress, transformation, and egress

```text
Ubuntu/upstream metadata
          │ MirrorSyncJob
          ▼
AptMirror primary snapshot
          │ RepositorySyncJob: copy, then apply enabled local packages
          ▼
AptRepository secondary snapshot
          │ RepositorySignJob: sign and promote
          ▼
AptRepository primary snapshot ──► APT metadata and package endpoints
          │ optional RepositoryExportJob
          ▼
Static export ──► independent download nodes
```

An `AptMirror` is an input source. An `AptRepository` is the output clients consume, optionally linked to a mirror and configured with a signing certificate. `AptBucket` is a versioned metadata snapshot. `AptPackage` is a structured package record inside a bucket.

Standalone repositories start from enabled local uploads; they do not copy their previous primary snapshot as source content. Disabling or removing local content therefore affects the next synchronized/signed snapshot rather than leaving old rows perpetually carried forward.

## Snapshot invariants

| Object | Primary | Secondary |
|--------|---------|-----------|
| Mirror | Current upstream snapshot | In-progress snapshot, then the previous primary retained during promotion |
| Repository | Live snapshot consumed by clients | Pending snapshot awaiting signing/promotion |

`ReleaseContent` holds the Release headers and checksums, including index paths and sizes. `InReleaseContent` holds the clearsigned result; `SignedAt` records signing time. A newly created pending bucket is not public.

The design relies on these rules:

1. Repository publication goes through the signing/promotion job. Normal synchronization must not replace the live primary pointer directly.
2. Secondary pointers protect work in progress from garbage collection; they do not authorize public reads.
3. Garbage collection derives its active set from mirror/repository primary and secondary references, rather than a guessed age-based grace period.
4. Create a bucket and attach its navigation-property reference in one `SaveChanges` transaction to avoid an unreferenced intermediate window.
5. Jobs must be retryable after failure without destroying the last live repository.
6. Mirror promotion retains the prior primary as secondary so a repository sync streaming that snapshot is not immediately cut off by collection.

| Failure or race | Intended behavior |
|-----------------|-------------------|
| Sync fails partway through | Live primary remains; referenced work is protected |
| Sync succeeds but signing has not run | Clients continue seeing the previous live snapshot |
| Signing runs before Release data is ready | Guard skips publication |
| GC runs during bucket creation | Atomic creation/reference prevents an orphan window |
| GC overlaps signing/promotion | Both primary and secondary references protect active buckets |
| Mirror advances while a repository copies it | Retained old mirror reference protects the reader |

These rules prevent exposing an unfinished individual snapshot. They do not mean every sequence of separate HTTP requests or independently updated edge nodes is automatically pinned to one snapshot. Published paths, hashes, and distribution timing must also remain consistent.

## Virtual packages and content-addressed storage

An upstream package can initially be represented by metadata alone (`IsVirtual=true`). On download, Apkg obtains its payload from `RemoteUrl`, validates it, and materializes it in content-addressed storage (CAS). A real payload has a path such as `Objects/<first-two-hash-characters>/<sha256>.deb`.

If the CAS object already exists, download serving can take the fast path and repair materialization state. During resync, previously materialized hashes from the current primary are carried into the new snapshot so already-cached payloads are not treated as missing again.

GC is based on references, not `IsVirtual` alone: hashes still needed by active package records or retained local uploads must remain. Identical payloads may be shared across suites. In the current upload service, uploaded local `.deb` files also enter CAS; the former `LocalPackages/<repositoryId>/...` description is historical.

Hash fidelity matters throughout the pipeline: upload computes payload hashes from actual bytes; mirror metadata supplies expected upstream hashes; local-to-repository mapping preserves hash/size/control fields; CAS filenames identify those bytes. A re-signed index cannot make mismatched payload bytes valid.

## Local package precedence and versions

The current entity for a local upload is `ApkgDebPackage`; older notes called it `LocalPackage`. Enabled local uploads are grouped by package, architecture, suite, and repository, with the highest Debian version chosen among local candidates. The winner replaces upstream entries of the same package and architecture, even if its version is below the upstream version.

The upload downgrade guard separately compares a new upload against the live primary's version and normally rejects a lower version with 403. `--allow-downgrade` bypasses that guard; it does not bypass ownership, duplicate checks, or all subsequent resolution. If a higher enabled local version remains, local resolution still selects it. If the competing higher version was upstream, the local override can replace it. Do not assume the old claim that "force can never result in a lower published version" is true.

Disabling a local entry removes it from local resolution. For a mirror-backed repository, the upstream version can then reappear after sync/sign. Overrides are exact to `(Package, Architecture)`; an amd64 upload should not replace an arm64 row.

Relevant fields include identity (`RepositoryId`, `Package`, `Architecture`), control (`IsEnabled`, uploader/revision ownership), Debian metadata (version, dependencies, recommendations, conflicts, replacements, provides, section, priority, homepage), and payload fields (filename, size, SHA-256, SHA-1, MD5, SHA-512).

## Repository coordinates and routes

| Axis | Purpose | Example |
|------|---------|---------|
| Distro | Public namespace and upload matching | `anduinos` |
| Suite | Release/channel | `resolute-addon` |
| Component | Package grouping and matching filter | `main` |
| Architecture | Payload compatibility/index selection | `amd64`, `arm64`, or package `all` |

A repository can generate multiple component/architecture indexes. These are not necessarily separate database repositories. Four components, three architectures, and four suite variants imply 48 index combinations, illustrating why matrix alignment matters.

The canonical routes include:

```text
/artifacts/<distro>/dists/<suite>/InRelease
/artifacts/<distro>/dists/<suite>/Release
/artifacts/<distro>/dists/<suite>/<component>/binary-<arch>/Packages.gz
/artifacts/<distro>/pool/<path>
/artifacts/certs/<certificate-name>
```

The controller also has repository-name and direct-suite routing forms. Prefer the explicit distro routes and source configuration generated by the server to avoid ambiguous names. Pool files are served from actual package storage or materialized on demand. The generic file-download controller is a separate surface, with its own cache/ETag behavior; it should not be confused with the APT manifest and hash chain.

## Signing and trust

Each repository selects a certificate; multiple repositories may deliberately use the same certificate. The signing job produces the repository's InRelease. Clients trust the publisher's public key because the publisher's repository metadata may differ from upstream.

```text
Trusted repository public key
    → signed InRelease
    → index checksum and size
    → package checksum and size
    → downloaded .deb bytes
```

Use a source-scoped `Signed-By` keyring on clients. `EnableGpgSign=false` skips signing but still permits promotion; it is an unsigned repository mode, not preservation of an upstream signature on modified metadata. Default secure APT clients reject it. A static edge can serve the origin's already-signed bytes without holding the origin's private key; it need not rebuild an unsigned repository.

External signing via Vault/Key Vault was a design proposal, not an implemented key-management option in the reviewed source. Signing currently uses the configured private key with local GPG. See [server configuration](Server-Configuration.md) for storage and backup implications.

## Platform entities and naming

`Apt*` names describe APT-level concepts: mirrors, repositories, buckets, indexed packages, and certificates. `Apkg*` names describe platform concepts: the database context, upload families/revisions, SDK, and upload manifest.

`ApkgPackage` identifies a package family by `(Name, Distro, Component)` and its owner. `ApkgRevision` records a received bundle, uploader, timestamps, stored archive, and publication/listing state. `ApkgDebPackage` connects uploaded Debian payloads to repositories. See [bundle and upload semantics](Bundle-and-Upload-Reference.md) for validation and conflicts.

Structured `AptPackage` records support exact override matching, lazy materialization, searching, and GC references. Repeatedly rewriting raw `Packages.gz` text would obscure those operations. Persistent primary/secondary pointers retain publication state across restart; dual snapshots let failures leave the live view intact.

The web interface exposes package discovery, revisions and targets, repository client setup, certificates, accounts/roles, API keys, and job history. Treat the implemented pages as the UI contract; the original design's general-purpose rule editor and community-review workflow were ambitions, not existing screens.

## Planned rule engine and community policy

The original proposed override pipeline included `DropPackage`, package-version overrides, and dependency rewrites. The implemented local-package replacement mechanism is not that entire rule engine.

Dropping a package creates a dependency problem. Cascading removal can affect a large fraction of the repository; deleting dependencies from consumers may break runtime behavior; leaving consumers unchanged makes them uninstallable. Empty mock packages with `Provides` can satisfy metadata without satisfying real runtime requirements. A future impact-analysis interface should calculate affected packages before administrators approve a change.

Historical proposals also included retaining two or three rollback snapshots and switching the primary pointer to a known-good snapshot. Reference-based GC alone is not an archival rollback policy. Do not promise retained history unless retention is explicitly implemented and verified.

A moderated `community` component was proposed to accept contributions while reducing package-name hijacking risk. The example policy gave an Official release label priority 900 and a Community label priority 100. Those values express an intended separation; a label must actually exist in Release metadata and APT policy must be verified on clients. This is not an automatically installed protection or a reason to trust arbitrary uploaders.

Multi-server coordination was likewise a proposal. Current static exports and independent synchronization scripts provide a practical distribution mechanism, but do not imply a coordinated multi-master control plane.

## Regression evidence and historical incidents

| Test family | What it protects |
|-------------|------------------|
| `AtomicBucketCreationTests` | Bucket insertion and reference assignment without a GC window |
| `GcSignRaceConditionTests` | Active sets include secondary buckets during signing races |
| `RepositorySignJobTests` | Pending repositories do not become public before promotion |
| `RepositorySyncLocalPackagesTests` | Exact overrides, enabled filtering, metadata fidelity, materialization state |
| `BackgroundJobsTests` | Queues, cancellation/failure handling, authenticated triggers |
| `DebUploadServiceTests` | Duplicate slots, byte duplicates, downgrade behavior |

One historical incident involved architecture-independent packages rebuilt for several suites. Different embedded timestamps produced different hashes for identical source, while suite-independent pool paths could resolve to the wrong bucket's bytes. APT then reported size/hash mismatches, and rebuilding reproduced the problem.

The recorded remedies included `SOURCE_DATE_EPOCH=0` during packaging and deterministic newest-bucket selection for ambiguous lookups. The current repository builder also namespaces local pool paths by suite. Reproducible packaging helps, but does not excuse serving a payload inconsistent with the requesting index. Relevant regressions include `BuildAsync_RepeatBuildsProduceIdenticalDeb` and the multi-suite `GetLocalPoolPath` test.

Repeated uploads are checked both by content hash and by repository/package/version/architecture slot. Multiple repository records for an `all` package can be legitimate because each suite needs its own relationship. Component is not part of the local duplicate slot; see the [upload reference](Bundle-and-Upload-Reference.md) before moving a family across components.
