# AnduinOS GRUB Style

This package owns the installed system's GRUB presentation defaults. Its policy
is deliberately small: prefer a readable graphics-mode fallback list and leave
GRUB's trusted default Unicode font untouched. The Linux graphics payload uses
`auto` so Plymouth does not inherit the boot menu's lower resolution.

## Installed files

- `/etc/default/grub.d/20-anduinos-style.cfg`

The package refreshes GRUB after installation, upgrade, removal, and purge on a
normal running system. In a chroot it defers the refresh; the AnduinOS installer
runs `update-grub` after deploying the target system.

The package does not install fonts and does not modify initrd or EFI files.
