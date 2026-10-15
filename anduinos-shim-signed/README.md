# AnduinOS shim

This package uses Apkg's upstream derivation, just like the other Ubuntu-derived
packages in this repository. Each target selects the newest `shim-signed` in
its configured Ubuntu suite and architecture. `$(UpstreamVersion)` supplies the
package version; there are no fixed artifact URLs, versions or SHA256 inputs.
The Ubuntu archive keyring verifies the upstream repository.

`apkg build --all` builds the declared target matrix. To add another suite,
update `TargetSuites`, `UpstreamSuiteMapping` and `SuiteShortNameMap`. No download
script or Docker image needs updating. ARM64 uses Ubuntu Ports; AMD64 uses the
Ubuntu archive.

The preparation script retains the signed shim, MokManager, revocation check
and upstream documentation. Their bytes are unchanged. Ubuntu's key-generation
and deployment hooks are excluded; our maintainer scripts delegate deployment
to the shared Secure Boot toolkit. The package depends on `anduinos-grub` and
never creates or deletes signing certificates.

Updated inputs still need boot-chain acceptance testing before release.
