# apkg for AnduinOS

This package installs the framework-dependent `Aiursoft.Apkg.Client` 10.0.70
tool in `/usr/lib/apkg` and a system-wide launcher at `/usr/bin/apkg`. The .NET
10 and ASP.NET Core 10 runtimes are supplied by Debian package dependencies;
they are not bundled here. Consequently, both `apkg` and `sudo apkg` work
without a per-user dotnet-tool installation or shell configuration.

This package targets `resolute-addon` and `stonking-addon`. Its .NET 10
runtime dependencies are not available from the configured Ubuntu Noble
repository. Version 10.0.70 supports both SHA-256 and SHA-512-only Ubuntu
repository metadata, including Stonking.

## Updating upstream

The pinned version, checksums, source commit, and package revision require
periodic maintenance. Follow the **Apkg client** entry in the repository's
[Monthly Update Manual](../README.md#monthly-update-manual), which is the
canonical checklist for updating and verifying this package.
