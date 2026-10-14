# AnduinOS shim

This package preserves Ubuntu shim-signed 1.59+15.8-0ubuntu2's EFI bytes,
including MokManager, signatures and SBAT. The exact upstream .deb SHA-256 is
pinned in `build.sh`; EFI hashes and provenance ship with the package.

It depends on `anduinos-grub`. The upstream DKMS/key-generation/deployment
maintainer scripts are not inherited. It retains the kernel revocation check
and delegates boot deployment to the Secure Boot toolkit. EFI/BOOT fallback
files are not automatically deployed. Purging it never deletes certificates.

The initial target is Resolute/amd64. Shim itself is not recompiled or re-signed.
Future security updates require changing the pinned input and requalifying it.
