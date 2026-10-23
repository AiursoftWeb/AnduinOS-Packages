# AnduinOS Recovery Environment

A small, independent kernel and initramfs for restoring AnduinOS Btrfs system
snapshots when the installed system cannot boot. The text menu reuses Disk
Snapshots Manager's offline recovery engine and restarts normally after restore.

This initial development package requires an unmounted, dedicated ext4 volume
labelled `AOS_RECOVERY` (256 MiB) and an unencrypted AnduinOS Btrfs root using
the standard subvolume layout. It never creates or formats partitions. Initial
deployment binds both filesystem UUIDs; later updates reuse that binding.
Existing users do not receive the package through dependencies. Installer
integration, a graphical browser and Secure Boot trust-chain enforcement are
separate stages; qualify this version with Secure Boot disabled.

Build with `apkg build --all` (10.0.65 or newer), Cargo's two Linux GNU targets,
the corresponding GCC linkers, `cpio`, `gzip`, `kmod`, `zstd` and `binutils`.
Verified Ubuntu inputs supply the generic kernel and runtime; no target programs
or maintainer scripts execute during construction. The source-tree offline
engine is compiled into the image. Rebuild and bump this package when updating
that engine. Input versions, hashes and copyright notices accompany the package.

Installation deploys and checks the payload, unmounts the volume and refreshes
the GRUB menu. No snapshot hooks, background services or permanent mounts are
added. Removal removes the menu entry and retains the recovery volume's files.
