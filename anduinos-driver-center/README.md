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
the AnduinOS xpadneo package, and fixed audio, printing, and Intel graphics operations.

## Intel graphics

Intel display devices are enumerated independently from `ubuntu-drivers` through
PCI sysfs. The Intel page supports both `i915` and `xe`; it shows PCI identity,
the bound kernel driver, kernel version, firmware evidence and diagnostic advice.
PSR, FBC, display C-states and Panel Replay can use the kernel default or be
disabled when the installed modules expose the corresponding parameter. These
are module-wide settings, not independent per-GPU switches. PSR controls are
shown for connected internal panels. A module parameter is policy, not proof
that the feature is active. Missing status or journal access remains unknown.

Opening the page does not authenticate or write files. The optional detailed
status action uses the existing restricted helper to read bounded, fixed Intel
debugfs status files and Intel GPU boot-log lines. It never mounts debugfs or
loads/unloads a graphics module. Export saves a local JSON report through the
user's file chooser; no upload is performed. Reports include only Intel-related
configuration and GPU evidence rather than the full journal or kernel command
line; custom Intel boot parameters and GPU logs should still be reviewed before
sharing.

Applying settings creates only this managed boot fragment:

```text
/etc/default/grub.d/99-anduinos-intel-graphics.cfg
```

The fragment appends fixed, validated parameters to `GRUB_CMDLINE_LINUX_DEFAULT`
and makes the boot menu visible for at least five seconds. Existing longer
timeouts are retained. It does not replace `/etc/default/grub`, introduce
modprobe configuration, rebuild initramfs, or install third-party modules.
The existing `preempt=full` setting is retained. Other graphics overrides are
reported as conflicts instead of being overwritten. Capability validation
covers every installed boot kernel; a later kernel update may change support,
so hardware behavior still requires validation after kernel updates.

The privileged helper serializes its own operations, holds the standard dpkg
locks to exclude concurrent package updates, and keeps private backups in
`/var/lib/anduinos-driver-center/intel-graphics/`, generates and checks a candidate
GRUB configuration, and verifies that recovery entries omit the overrides before
replacing the boot configuration. A generation failure restores the previous
files. A pending marker identifies an interrupted transaction. Restoring defaults
removes only the managed fragment and regenerates GRUB; external settings remain.
Live sessions and unsupported boot configurations are rejected.

While graphics overrides are enabled, the existing recovery entries use
`systemd.unit=multi-user.target` with GRUB's recovery `nomodeset`. This provides
normal user console login rather than a `single` root shell: AnduinOS locks root
and does not ship friendly-recovery. An existing custom recovery command line
is reported as a conflict. Restoring defaults also removes this temporary
recovery configuration; no rescue service or authentication policy is changed.

If a changed setting prevents normal graphical startup, choose the recovery
entry under GRUB's advanced options, log in with your normal account, and run:

```sh
sudo /usr/libexec/anduinos-driver-center/driver-helper intel-reset
sudo reboot
```

Recovery must remain enabled. Selecting an older *normal* kernel entry does not bypass
`GRUB_CMDLINE_LINUX_DEFAULT`. No automatic desktop-health or reboot-loop daemon
is installed.

Driver switching uses paired `i915.force_probe` / `xe.force_probe` parameters,
never a global module blacklist. `VALIDATED_SWITCHES` is intentionally empty:
no real Intel hardware/kernel combination has been certified by this change.
Before adding an exact kernel/PCI-ID/target tuple, validate firmware availability
in the boot environment, Secure Boot, OpenGL/Vulkan, video acceleration,
internal/external displays, suspend/resume and the recovery path. PCI aliases
alone are not sufficient. Duplicate device IDs cannot be switched independently.
Restore display overrides before changing drivers; use the target driver's
settings after reboot. No additional MOK enrollment is needed merely to change
parameters of the existing signed distribution modules.

Packages tests use temporary directories and command doubles. Real GRUB
generation and normal/recovery/default boot coverage live in AnduinOS-2's
`intel-graphics-boot` acceptance suite. Its QEMU inventory is explicitly a fixture;
passing it does not certify Intel display behavior or authorize driver switching.

Secure Boot, MOK enrollment, DKMS signing health, repair operations, and the
trust panel are provided by `anduinos-secureboot-toolkit`. This is the same
implementation and fixed enrollment-code experience used by AnduinOS OOBE;
Driver Center must not add a second Secure Boot backend or diverging prompts.

The final sidebar item, **About This Computer**, shows a screenshot-friendly
hardware overview. The compact view contains the CPU, system-usable memory,
graphics, physical disk(s) backing `/`, displays, motherboard, and an estimated
system installation date. The date uses the birth time of `/`, displayed in
the local timezone and date format, without writing a marker or requiring root.
Cloning, snapshot recovery, or filesystem reuse can make it differ from the
actual installation date. If birth time is unavailable, the date is unknown;
modification time is never used as a fallback. Expand it
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
PYTHONPATH=src python3 -m anduinos_driver_center --page intel-graphics
python3 -m unittest discover -s tests -v
```

GTK behavior tests can also run headlessly with `xvfb-run -a python3 -m unittest discover -s tests -v`.
