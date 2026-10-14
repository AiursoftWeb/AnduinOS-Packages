# AnduinOS GRUB

The first package targets Resolute/amd64. It builds Ubuntu GRUB 2.14 with the
Ubuntu patch set, BIOS and EFI modules, the normal GRUB tools, configuration,
themes and kernel hooks. The EFI image additionally embeds LUKS2/Argon2id.
Its SBAT identity is `grub.anduinos`; shim verification remains enabled.

The build uses a pinned Resolute container so the tools run on Resolute. Source
archives are pinned by SHA-256. `bash build.sh amd64` exports the native build;
then `umask 022; apkg build --all` packages it. Docker is a build prerequisite. Source and
Ubuntu patches are available from the URLs in the Dockerfile; hashes and build
package versions ship in `/usr/share/doc/anduinos-grub`.

This package owns the GRUB tools and platform modules it replaces. It ships no
private key and never generates a shared signing identity. The Secure Boot
toolkit signs the pristine package image with the machine's unified certificate
and deploys it together with the official shim from `anduinos-shim-signed`.

Disk encryption installation is a later phase; the embedded reader support
does not itself encrypt a disk. ARM64 is not yet qualified.
