# Bundle Format, Identity, and Upload Semantics

`apkg publish` builds a gzip-compressed tar archive with a `manifest.xml` and its Debian payloads. Read [the publishing tutorial](Publish-Packages.md) for the normal commands. This chapter explains what the server validates and how packages map into repositories.

## Manifest v2 and v3

Ordinary bundles use v2. Bundles declaring AppStream applications use v3 and may carry screenshot resources under `appstream/<component-id>/screenshots/`. The CLI checks `/api/system/capabilities` before uploading v3 so an older server cannot silently lose those resources.

```xml
<?xml version="1.0" encoding="utf-8"?>
<ApkgPackage FormatVersion="2">
  <Name>hello-apkg</Name>
  <Distro>anduinos</Distro>
  <Component>main</Component>
  <Maintainer>Your Name &lt;you@example.com&gt;</Maintainer>
  <Description>A small command packaged with Apkg</Description>
  <Entries>
    <Entry>
      <DebFile>hello-apkg_1.0.0-1_resolute-addon_all.deb</DebFile>
      <Suite>resolute-addon</Suite>
      <Architecture>all</Architecture>
    </Entry>
  </Entries>
</ApkgPackage>
```

This is explanatory; let `publish` generate the manifest. Version is read from the actual `.deb`, not an independently trusted manifest version field.

| Root field | Recipe source |
|------------|---------------|
| `FormatVersion` | Publisher-selected format, 2 or 3 |
| `Name` | `PackageName` |
| `Distro` | `TargetDistro` |
| `Component` | `Component` |
| `Maintainer` | `Maintainer`, falling back to `PackageAuthors` |
| `Description` | `PackageDescription` |
| `Homepage` | `PackageHomepage` |
| `License` | `LicenseType` |
| `RepositoryUrl` | Source repository URL |

Each `Entry` names an archive `DebFile`, `Suite`, and `Architecture`. Filenames normally follow `<name>_<version>_<suite>_<arch>.deb`. Multiple entries can have different versions, suites, or architectures; all share the root distro and component.

## Three different identities

| Layer | Identity/rule | Consequence |
|-------|---------------|-------------|
| Package family | `(Name, Distro, Component)` | Globally identifies the platform family and its owner |
| Repository routing | Matching distro and suite, containing component, compatible architecture | Determines which repositories receive each payload |
| Enabled local slot | `(RepositoryId, Package, Version, Architecture)` | Rejects duplicates regardless of family component |

The family identity is immutable: changing any field creates a different family. The first uploader owns a new family, and another user cannot subsequently claim the same identity. Version, suite, and architecture vary across revisions.

Current routing includes component membership. Older documents described a distro/suite/architecture-only router; that is no longer the rule in `RepositoryTargetService`. Package `all` matches repositories with concrete client architectures. Repository architecture lists and component lists are comma-separated in server configuration.

## Validation and revision lifecycle

The server parses the manifest and verifies that its referenced archive entries exist before creating a revision. It reads control metadata from each Debian payload and computes hashes from actual bytes. It checks family ownership, repository access, duplicate content, an already-enabled slot, and the live-version downgrade guard.

An upload key operates with its owner's permissions. A repository can allow uploads broadly, or require repository-management/restricted-upload permission. The family ownership check remains separate from the repository permission check.

| Result | Revision behavior |
|--------|-------------------|
| All new payloads accepted | Published revision |
| All payloads skipped with `--skip-duplicate` | No retained empty revision; warnings can accompany success |
| All payloads conflict without skipping | Conflict response and no retained empty revision |
| Some accepted, others skipped intentionally | Published revision with the accepted payloads |
| Some accepted, unresolved conflicts remain | Unpublished revision retained for diagnosis; conflict response |

`IsPublished` describes the platform revision result, not completion of repository sync/signing or global edge distribution. `IsListed` concerns listing state. The platform family may remain after an empty revision is removed. A cleanup job removes abandoned old unpublished drafts and temporary data; do not use those drafts as a durable rollback archive.

Duplicate SHA-256 checks are scoped to repository records. A same-version payload with different bytes still conflicts in the enabled slot. `--skip-duplicate` makes intentional retries idempotent; it is not permission to replace a published version silently. `--allow-downgrade` only overrides the downgrade guard; [architecture](Design-and-Architecture.md) explains local version resolution afterward.

## Missing repository targets

Suppose a bundle targets `noble-addon` and `resolute-addon`, but the server only has the first repository. A family record can exist and an upload can partially succeed while the second target is skipped with a warning. An HTTP success code alone does not establish full matrix publication.

Publishers and administrators should share a matrix listing the distro, suite, components, and client architectures. Before publishing, check every declared target against it; afterward, verify each intended source from a compatible client. Creating the missing repository later does not automatically route a previously skipped payload into it: re-publish/re-upload through the supported workflow.

This differs from a registry that retains a package untouched and defers framework selection entirely to consumers. Apkg routes Debian payloads into APT repositories at upload/publication time. Read both the target summary and server logs.

## Moving between components

Changing a family's component creates a new family identity but does not change an existing local duplicate slot. A second upload of the same package/version/architecture to the same repository can therefore fail with 409 even when the component is different.

Plan component migration with the administrator: inspect ownership and repository permissions, disable the old local entry where appropriate, upload the intended replacement, then sync/sign and verify clients. Retained content-hash checks can also affect retries; inspect the actual conflict rather than assuming changing a label bypasses validation. This is Apkg's deduplication policy, not a universal claim that APT cannot represent packages in multiple components.

## Supplemental CLI commands

`apkg install` selects a compatible `.deb` from a local `.apkg` and installs through dpkg. `apkg unpack` extracts a selected payload. Selection uses the system's distro, suite, and architecture and supports explicit options shown by `--help`. Neither command replaces APT's repository dependency solver. `apkg add-source --url ...` configures a source and keyring; it is optional for users who configure native APT manually.

Use `apkg push --file ...` for the reviewed command version. Published filenames can include a static package-version suffix, such as `hello-apkg.1.0.0-1.apkg`; always use the path printed by the publisher rather than an old example's unversioned filename.

For preflight request/response contracts and compatibility fallback, see [CI and preflight](CI-and-Preflight.md).
