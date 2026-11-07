# AnduinOS Recovery Environment

A small, independent kernel and initramfs for restoring AnduinOS Btrfs system
snapshots when the installed system cannot boot. An English graphical browser
reuses Disk Snapshots Manager's offline recovery engine: choose a snapshot,
confirm the rollback, then restart. Personal files remain unchanged. A text
menu remains available if graphical startup fails. The System Information tab
shows read-only boot, installed-system, storage and hardware details. Disk
encryption reports the target root partition, including an unlocked LUKS mapping;
it does not describe swap or other partitions. Unidentified or unsupported storage
is reported as unavailable. Unavailable hardware fields do not prevent recovery.
**Snapshot Current State** saves the
installed system before a rollback and refreshes the list. Until a snapshot has
been created successfully in this recovery session, rollback confirmation shows
a prominent data-loss warning. Snapshot creation requires readable installed
kernel, initramfs and package metadata; failures leave the warning in place.

This initial development package requires an unmounted, dedicated ext4 volume
labelled `AOS_RECOVERY` (256 MiB) and an AnduinOS Btrfs root using the standard
subvolume layout. The root may be a plain partition or the installer's single
LUKS2 partition; LVM, RAID and other mapped layouts remain unsupported. It never
creates or formats partitions. Initial deployment binds both filesystem UUIDs
and, for encryption, the outer LUKS UUID; later updates reuse that binding.
New Btrfs erase-disk installations create this volume and explicitly deploy
the preinstalled payload before generating GRUB. Existing users do not receive
the package automatically: Disk Snapshots Manager only suggests it. Installation
without a suitable volume, including Live image assembly, skips deployment.
The encrypted GRUB entry reads the existing machine credential from the unlocked
root into the RAM-only `/anduinos-unlock.key` cpio entry. The public recovery
volume never stores that credential. Recovery opens only the bound LUKS2
container, verifies its inner Btrfs UUID and removes the incoming file before
starting the browser. If the credential is missing or rejected, the native text
prompt accepts the disk password or emergency recovery key; failed input can be retried
or the machine restarted. The mapping lives for the recovery session, while
individual actions still mount/unmount the filesystem themselves.

New encrypted Btrfs installations also bind the recovery volume UUID in their
EFI unlock stub. If the installed `grub.cfg` is missing, the native GRUB opens
an independent menu on that volume, using Recovery's own kernel and initramfs.
Deployment generates both menus from one boot recipe. Existing three-argument
unlock stubs and plain installations keep their previous behavior; ext4 does
not gain snapshot recovery. The encrypted root must still be readable enough
to open its LUKS container. Missing machine credentials use the manual Recovery
prompt described above. Secure Boot being enabled does not by itself
authenticate this initramfs. This package does not add payload signing or claim
that removing the RAM file erases every copy from physical memory.

Build with `apkg build --all` (10.0.70 or newer), Cargo's two Linux GNU targets,
the corresponding GCC/G++ toolchains, CMake, Git, `cpio`, `gzip`, `kmod`,
`zstd` and `binutils`.
Verified Ubuntu inputs supply the generic kernel and runtime; no target programs
or maintainer scripts execute during construction. The source-tree offline
engine is compiled into the image. LVGL draws directly through DRM/framebuffer
and reads Linux input devices; no desktop or display server is included. Rebuild and bump this package when updating
that engine. Input versions, hashes and copyright notices accompany the package.
ARM64 images also load the SC8280XP PCIe/USB PHY, controller and regulator
providers needed to reach NVMe storage and USB input on Windows Dev Kit 2023.
Their module dependencies alone do not describe the board's supplier topology.
The GRUB entry preserves `clk_ignore_unused`, `pd_ignore_unused` and
`arm64.nopauth` only when present in the target's GRUB kernel configuration;
other normal-system arguments are excluded. Firmware framebuffer operation,
USB input and system-disk discovery still require qualification on real hardware.
The pointer is the original Fluent-dark-cursors image from
[Fluent-icon-theme](https://github.com/vinceliuice/Fluent-icon-theme), under GPL-3.0.

Deployment checks the payload and unmounts the volume. Package updates refresh
the GRUB menu when a volume is configured. Updates stage and verify a complete
replacement before switching the directory selected by `current`. Retrying
an interrupted deployment removes unselected staging directories before checking
free space, retaining the selected image. If a terminated process leaves the
volume mounted, close its users, unmount it normally and run
`sudo dpkg --configure -a`; the deployment error lists the mount paths.
On configured systems, a brief boot
service records the recovery point available before a successful graphical
boot. The browser labels it **Last successful boot**; this is a checkpoint,
not an independent boot test of that snapshot. Recording neither creates a
snapshot nor runs `update-grub`. Removal removes the menu entry and retains
the recovery volume's files.
