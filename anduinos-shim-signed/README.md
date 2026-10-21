# AnduinOS shim

This package preserves the selected Ubuntu shim-signed EFI bytes, including
MokManager, signatures and SBAT. `apkg build --all` builds amd64 and arm64
packages from the latest version in the mapped suite and its updates/security
pockets. BuildInput/SuiteShortName support requires apkg 10.0.65 or newer.
The builder needs `ubuntu-keyring` and `dpkg`, but no container or root access.
No Ubuntu version, download filename or SHA-256 is pinned. The signed archive
metadata supplies package hashes; EFI hashes and the fetch manifest ship with
the package. Copies are byte-compared against the verified input.

It depends on `anduinos-grub`. The upstream DKMS/key-generation/deployment
maintainer scripts are not inherited. It retains the kernel revocation check
and delegates boot deployment to the Secure Boot toolkit. EFI/BOOT fallback
files are not automatically deployed. Purging it never deletes certificates.

Shim is not recompiled or re-signed. A new suite is configured in the project,
not build-script branches. New inputs require qualification and an AnduinOS
package revision bump before publication. The toolkit now selects the matching
ARM64 or AMD64 companions for deployment. ARM64 installer/ISO integration and
Secure Boot qualification remain subsequent milestones.
