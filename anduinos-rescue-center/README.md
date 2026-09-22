# AnduinOS Rescue Center

AnduinOS Rescue Center is a graphical recovery tool intended for the AnduinOS
Live environment. It discovers installed AnduinOS systems, presents both a
simple installation picker and an advanced disk/partition view, and performs
bounded offline repairs after re-validating the selected block device.

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
- Encrypted filesystems, package repair, bootloader repair, and emergency
  chroot shells are outside the first release.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src LANGUAGE=C \
  python3 -m unittest discover -s tests -v
```

The shared recovery engine also has a disposable real-Btrfs loopback test that
creates a recovery point, changes the system, restores it offline, and verifies
that Home data remains unchanged.
