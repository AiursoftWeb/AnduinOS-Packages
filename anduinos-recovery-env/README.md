# AnduinOS Recovery Environment

A small, independent kernel and initramfs for restoring AnduinOS Btrfs system
snapshots when the installed system cannot boot. The text menu reuses Disk
Snapshots Manager's offline recovery engine and restarts normally after restore.

This initial development package requires an unmounted, dedicated ext4 volume
labelled `AOS_RECOVERY` (256 MiB) and an unencrypted AnduinOS Btrfs root using
the standard subvolume layout. It never creates or formats partitions. Initial
deployment binds both filesystem UUIDs; later updates reuse that binding.
New Btrfs erase-disk installations create this volume and explicitly deploy
the preinstalled payload before generating GRUB. Existing users do not receive
the package automatically: Disk Snapshots Manager only suggests it. Installation
without a suitable volume, including Live image assembly, skips deployment.
A graphical browser and Secure Boot trust-chain enforcement are separate
stages; qualify this version with Secure Boot disabled.

Build with `apkg build --all` (10.0.65 or newer), Cargo's two Linux GNU targets,
the corresponding GCC linkers, `cpio`, `gzip`, `kmod`, `zstd` and `binutils`.
Verified Ubuntu inputs supply the generic kernel and runtime; no target programs
or maintainer scripts execute during construction. The source-tree offline
engine is compiled into the image. Rebuild and bump this package when updating
that engine. Input versions, hashes and copyright notices accompany the package.

Deployment checks the payload and unmounts the volume. Package updates refresh
the GRUB menu when a volume is configured. No snapshot hooks, background services or permanent mounts are
added. Removal removes the menu entry and retains the recovery volume's files.
