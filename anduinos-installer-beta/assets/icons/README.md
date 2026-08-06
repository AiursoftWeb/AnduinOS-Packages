# Installer illustration provenance

These files are deliberately copied into the installer package. Runtime code
must not depend on a sibling source checkout or on the live session's current
icon theme.

| Packaged file | Source |
| --- | --- |
| `welcome.svg` | `anduinos-oobe/resources/icons/anduinos-oobe.svg` |
| `keyboard.svg` | `anduinos-oobe/resources/icons/keyboard.svg` |
| `network.svg` | Package-local Wi-Fi illustration derived from the Fluent network glyph |
| `updates.svg` | `anduinos-oobe/resources/icons/yast-upgrade.svg` |
| `disk.svg` | `anduinos-oobe/resources/icons/disk.svg` |
| `coexistence.svg` | `anduinos-oobe/resources/icons/window-duplicate.svg` |
| `secure-boot.svg` | `anduinos-oobe/resources/icons/secureboot-chip.svg` |
| `timezone.svg` | `anduinos-oobe/resources/icons/gnome-maps.svg` |
| `review.svg` | `anduinos-oobe/resources/icons/open-book-symbolic.svg` |
| `disk-snapshots-manager.svg` | AnduinOS-owned application artwork shared with `anduinos-btrfs-snapshots-manager/data/org.anduinos.BtrfsSnapshotsManager.svg` |
| `language.svg` | Fluent icon theme `src/scalable/apps/preferences-desktop-locale.svg` |
| `account.svg` | Fluent icon theme `src/scalable/apps/userinfo.svg` |
| `advanced.svg` | User-curated storage illustration (`Desktop/disks/advanced.svg`) |
| `btrfs.svg` | User-curated storage illustration (`Desktop/disks/btrfs.svg`) |
| `ext4.svg` | User-curated storage illustration (`Desktop/disks/ext4.svg`) |
| `flashing-disk.svg` | User-curated storage illustration (`Desktop/disks/flashing-disk.svg`) |
| `how-should-use.svg` | User-curated storage illustration (`Desktop/disks/how-should-use.svg`) |
| `one-single-disk.svg` | User-curated storage illustration (`Desktop/disks/one-single-disk.svg`) |
| `select-installation-disk.svg` | User-curated storage illustration (`Desktop/disks/select_installation_disk.svg`) |

The OOBE and installer packages are distributed under GPL-3.0. Disk Snapshots
Manager is distributed under MIT. The Fluent icon theme is also distributed
under GPL-3.0; its upstream project is
<https://github.com/vinceliuice/Fluent-icon-theme>. The AnduinOS-owned and
user-curated illustrations were supplied specifically for these applications
and are kept byte-for-byte in the package source.
