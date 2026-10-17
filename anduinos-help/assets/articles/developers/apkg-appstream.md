# AppStream and GNOME Software

AppStream metadata lets a software center show a package as an application, with a name, icon, description, and screenshots. Apkg can generate or include the application metadata and produce a repository DEP-11 catalog for GNOME Software.

## Declare an application

The files in this example must exist relative to the `.aosproj` directory:

```xml
<Project Sdk="Aiursoft.Apkg.Sdk">
  <PropertyGroup>
    <PackageName>example-firewall</PackageName>
    <PackageVersion>1.0.0-1</PackageVersion>
    <Maintainer>Example Team &lt;packages@example.com&gt;</Maintainer>
    <PackageDescription>A desktop firewall configuration application</PackageDescription>
    <TargetDistro>anduinos</TargetDistro>
    <TargetSuites>resolute-addon</TargetSuites>
    <TargetArchitectures>all</TargetArchitectures>
    <Component>main</Component>
    <LicenseType>MIT</LicenseType>
    <AppStreamDeveloperName>Example Team</AppStreamDeveloperName>
    <AppStreamMetadataLicense>CC0-1.0</AppStreamMetadataLicense>
  </PropertyGroup>
  <ItemGroup>
    <AppStreamApplication Include="data/com.example.firewall.desktop"
                          Icon="data/com.example.firewall.svg" />
    <AppStreamScreenshot Include="screenshots/overview.png"
                         Default="true" Caption="View firewall status and rules" />
    <AppStreamScreenshot Include="screenshots/rules.png"
                         Caption="Create application rules" />
  </ItemGroup>
</Project>
```

This describes presentation metadata only; also package the actual executable and runtime dependencies. The desktop entry's `Exec` must point to the installed application. The application ID is the desktop filename without `.desktop`, here `com.example.firewall`.

## Attributes

| AppStreamApplication attribute | Meaning |
|-------------------------------|---------|
| `Include` | Required desktop entry path |
| `Icon` | Required SVG, PNG, JPEG, or WebP icon |
| `Metainfo` | Optional complete standard AppStream metainfo file; otherwise generated |

SVG icons install into hicolor `scalable/apps`; bitmap icons into `256x256/apps`. Desktop entries install under `/usr/share/applications`, and metainfo under `/usr/share/metainfo/<id>.metainfo.xml`. Do not duplicate those automatic mappings with ordinary file entries.

| AppStreamScreenshot attribute | Meaning |
|------------------------------|---------|
| `Include` | Required local PNG, JPEG, or WebP file |
| `AppId` | Required for a package with multiple applications; inferred for a single application |
| `Default` | Whether this is the primary screenshot; at most one per application |
| `Caption` | Descriptive caption; keep it short, preferably under 100 characters |
| `Locale` | Caption locale, default `C`, for example `zh-CN` |
| `Environment` | Optional desktop/theme annotation, such as `GNOME:dark` |

The documented limits are ten screenshots per application and 14 MiB per image. Aim for at least 620 pixels wide and a 16:9 aspect ratio. These entries are package-level metadata; the initial implementation does not support per-target `Condition` on them. Videos and store-specific private fields are not part of this workflow.

## Where each file goes

Desktop entries, icons, and metainfo travel in the `.deb`. Screenshots travel in the `.apkg` resource area, not in each architecture's `.deb`, so users do not repeatedly install the same presentation images with native payloads.

The publisher emits manifest v3 when applications are present and checks `/api/system/capabilities` before uploading v3. The server verifies resource hashes, decodes images, removes embedded metadata, and stores canonical media by content hash. An older server must not silently accept a bundle while discarding its application resources.

During repository synchronization, explicitly declared local applications contribute to:

```text
dists/<suite>/<component>/dep11/Components-<arch>.yml
dists/<suite>/<component>/dep11/Components-<arch>.yml.gz
dists/<suite>/<component>/dep11/icons-48x48.tar.gz
dists/<suite>/<component>/dep11/icons-64x64.tar.gz
artifacts/<distro>/media/<suite>/<sha256>.png
```

The DEP-11 paths above are relative to the distribution's repository root. Media URLs use the public origin. Set `PublicAptServerDomain` before publishing screenshots; a background job cannot infer it from an HTTP request. Missing configuration can stop a new pending catalog while the existing signed repository continues serving.

The initial catalog implementation does not download and scan every virtual upstream package for application metadata. Command-not-found (`cnf`) and translation (`i18n`) index generation were separate roadmap items in the original design.

## Advanced metainfo and releases

A custom `Metainfo` file can include releases, OARS content ratings, provides, and hardware requirements. Apkg validates its component ID. If local screenshots are also declared, those declarations control the repository catalog's screenshots.

Increase `PackageVersion` when desktop metadata, metainfo, icons, or screenshots change. Preflight treats existing target versions as immutable and will not separately refresh presentation assets for the same version. After upload, complete repository sync and signing and refresh the software center's metadata. Garbage collection removes media when no retained revision references it.

See [bundle semantics](Bundle-and-Upload-Reference.md) and [server configuration](Server-Configuration.md) for the platform details.
