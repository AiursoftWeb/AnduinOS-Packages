# AnduinOS Rescue Center

AnduinOS Rescue Center is a graphical recovery tool intended for the AnduinOS
Live environment. It discovers installed AnduinOS systems, presents both a
simple installation picker and an advanced disk/partition view, and performs
bounded offline repairs after re-validating the selected block device. The
picker presents each detected installation as a card. The recovery workspace
then provides Home, Password reset, File browser, Boot repair, Emergency
terminal, System details, and (for a compatible Btrfs layout) Snapshots &
restore in one sidebar-driven window.

The package may be installed on a normal system for development or to rescue a
different offline installation. It must never modify the installation backing
the currently running root filesystem.

## Safety model

- The GTK application is unprivileged.
- Fixed Polkit helpers own storage probing and repair operations. The ordinary
  helper requires administrator authentication. A separate passwordless Live
  entry point verifies the root-owned AnduinOS Live runtime contract before it
  delegates to the same narrow command dispatcher.
- Discovery mounts filesystems read-only with journal replay disabled where the
  filesystem provides such an option.
- Quick mode lists only installations positively identified by their
  `/etc/os-release` data.
- A partition path alone is never durable authorization. Mutating operations
  must re-probe stable device and filesystem identities immediately before use.
- Passwords are passed over standard input, never process arguments.
- File browsing is descriptor-confined; symlinks and special files are never
  followed into the Live system. Exports are limited to the calling user's Home
  and removable-media roots.
- System snapshots use the existing Disk Snapshots Manager deployment format.
  Offline restore replaces only `@root`, leaves `@home` untouched, and uses a
  small atomic transaction that resumes after interruption. A compatible
  pre-restore safety snapshot is enabled by default in the UI.
- Boot diagnosis statically checks the installed kernel/initrd pairs, GRUB
  configuration, EFI loader and an active matching firmware entry without
  writing to the disk. Only a real reboot can prove that the system boots.
  Guided repair is limited to an unmounted UEFI installation whose FAT EFI
  partition is named in its fstab and found on the selected disk. It uses the
  installer's vendor-only GRUB policy: no writes to other EFI vendors or
  EFI/BOOT. Separate /boot, BIOS boot and cross-disk EFI arrangements are
  deliberately not guessed at. During repair the interface shows the current
  stage, an indeterminate activity bar, and expandable live command output;
  it does not invent a percentage for operations of unpredictable duration.
  For Btrfs installations, the selected `@root` subvolume is mounted directly
  as the chroot root, so GRUB can resolve the device containing `/boot/grub`.
- The emergency terminal is an advanced Live-only root shell in the selected
  offline system. Device identity is checked again, temporary chroot mounts
  are unmounted on exit (or a cleanup failure is reported), and a target
  directory symlink cannot redirect a mount onto the Live host. A root chroot
  shell is **not** a security sandbox.
- Encrypted filesystems and package repair remain outside this release.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src LANGUAGE=C \
  python3 -m unittest discover -s tests -v
```

The shared recovery engine also has a disposable real-Btrfs loopback test that
creates a recovery point, changes the system, restores it offline, and verifies
that Home data remains unchanged.

## Localization

The English source and 27 translated locales provide 28 language choices. The
GTK interface, helper diagnostics, desktop launcher, Live shortcut, and Polkit
prompts are localized. When changing user-visible text, mark it with `_()` in
the interface or `tr()` in the helper, then regenerate and merge the catalog:

```bash
sh update-pot.sh
for po_file in po/*.po; do
  msgmerge --update --backup=none "$po_file" po/anduinos-rescue-center.pot
done
```

Review every new translation before release, especially instructions concerning
power, mounted filesystems, the selected disk, and changes to the offline
system. `compile-locales.sh` rejects untranslated/fuzzy entries and validates
format placeholders before compiling the catalogs. After translation, run
`render-desktop-locales.sh` to refresh the localized desktop-entry fields.
