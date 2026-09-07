# AnduinOS Driver Center

A focused GTK4/libadwaita application for inspecting, installing, and repairing
hardware drivers and updating device firmware. It replaces the only broadly useful part of
`software-properties-gtk` without inheriting Ubuntu's repository, update,
authentication, and release-upgrade user interfaces.

The responsive home page summarizes automatic driver recommendations and the
health of graphics, audio, printing, Xbox controller, and Secure Boot support.
It compares installed and candidate versions of the recommended graphics
driver, can refresh package information, and exposes the equivalent of
`ubuntu-drivers install` through the same restricted privileged helper used by
the individual hardware pages.

The firmware page uses the fwupd client API directly to list supported devices,
refresh enabled metadata sources, inspect available releases, install one or
all updates, report live progress and device requests, prompt for required
restarts, and show the daemon's update history. Firmware authorization and
signature verification remain owned by fwupd rather than the driver helper.

The audio page reports the installed Intel SOF firmware and ALSA UCM packages,
deployed support files, loaded SOF modules, and active PCI audio drivers.
Missing AnduinOS audio support packages can be installed through the same
restricted polkit helper used for other driver operations.

The printing page reports CUPS service and startup health, configured and
paused queues, the default destination, and package versions grouped by their
roles in core printing, driverless IPP, network discovery, and optional
compatibility. Missing optional legacy or scanning packages are informational
rather than failures on a healthy driverless setup.

When components are missing, a single polkit-backed action installs a fixed
printing package allowlist. The printing availability switch can mask every
CUPS activation path, network discovery, and the static USB IPP service to
reduce attack surface without uninstalling packages; enabling it reverses the
masks and starts the normal printing units.

The unprivileged UI reads hardware state. Mutating operations go through a
fixed polkit helper which only accepts drivers reported by `ubuntu-drivers`,
the AnduinOS xpadneo package, and fixed audio and printing operations.

Secure Boot, MOK enrollment, DKMS signing health, repair operations, and the
trust panel are provided by `anduinos-secureboot-toolkit`. This is the same
implementation and fixed enrollment-code experience used by AnduinOS OOBE;
Driver Center must not add a second Secure Boot backend or diverging prompts.

The final sidebar item, **About This Computer**, shows a screenshot-friendly
hardware overview. The compact view contains the CPU, system-usable memory,
graphics, physical disk(s) backing `/`, displays, and motherboard. Expand it
for all physical disks and their volumes, device drivers, firmware identity,
CPU details, and display mode information. The system and desktop section adds
the running GNOME Shell version, Mutter/Wayland or X11 session information,
GTK/Shell/icon themes, font and cursor settings, and installed package counts.
The dpkg count includes only installed packages; Flatpak counts applications
and runtimes across user and system installations, excluding auxiliary locale
and debug extensions, as in the default `flatpak list` output.

Current usage is a snapshot taken on each scan: uptime, memory (total minus
MemAvailable), Swap, and local filesystem usage. The filesystem rows distinguish
used space from space available to ordinary users and deduplicate Btrfs
subvolumes and bind mounts. Network mounts, pseudo filesystems, and snap loop
images are excluded. Refresh with **Scan again**. CPU maximum frequency is
formatted in GHz; display details distinguish built-in and external connectors
when the connection type is known. The AnduinOS logo is shown only
when the distribution identifies itself as AnduinOS in os-release (or, when
that identity is absent, lsb-release).

Opening, expanding, refreshing, and collapsing the page never require root.
An optional **Read memory specifications** button in the detailed view makes
at most one authentication attempt per window. Its separate read-only polkit
helper accepts no arguments and reads only SMBIOS memory-device records,
returning an allowlist of specifications without serial numbers or asset tags.
Cancellation leaves the other details usable. Like swapcontrol-gtk, the basic
memory capacity comes from `/proc/meminfo`; exact DIMM types and configured
speeds come from `dmidecode`. Unsupported or inaccessible fields remain
unavailable instead of being guessed. Disk capacities use decimal units;
memory uses binary units. Display sizes come from reported physical dimensions,
and refresh rates describe the current mode, not advertised maximum capabilities.

No fastfetch or inxi dependency is needed. Inventory uses procfs/sysfs,
`lscpu`, `lsblk`, `lspci`, GDK, and optional NVIDIA tooling. The overview omits
hostnames, usernames, IP/MAC addresses, and serial numbers.

## Development

```bash
PYTHONPATH=src python3 -m anduinos_driver_center
PYTHONPATH=src python3 -m anduinos_driver_center --page computer
python3 -m unittest discover -s tests -v
```

GTK behavior tests can also run headlessly with `xvfb-run -a python3 -m unittest discover -s tests -v`.
