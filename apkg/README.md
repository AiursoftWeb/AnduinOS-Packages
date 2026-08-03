# apkg for AnduinOS

This package installs the framework-dependent `Aiursoft.Apkg.Client` 10.0.51
tool in `/usr/lib/apkg` and a system-wide launcher at `/usr/bin/apkg`. The .NET
10 and ASP.NET Core 10 runtimes are supplied by Debian package dependencies;
they are not bundled here. Consequently, both `apkg` and `sudo apkg` work
without a per-user dotnet-tool installation or shell configuration.

## Updating upstream

1. Update `VERSION`, `PACKAGE_URL`, and `PACKAGE_SHA256` in `download.sh`.
2. Set `SOURCE_COMMIT`, `LICENSE_SHA256`, and `PackageVersion` to match the new
   upstream release.
3. Recreate `deploy/` with `bash download.sh`, then lint and build:

   ```bash
   apkg lint --path ./apkg
   apkg build --path ./apkg
   ```

The SHA-256 values can be calculated with `sha256sum` after downloading the
official NuGet package and the license from the pinned source commit.
