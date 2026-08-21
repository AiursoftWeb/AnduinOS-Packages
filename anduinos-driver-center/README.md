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

## Development

```bash
PYTHONPATH=src python3 -m anduinos_driver_center
python3 -m unittest discover -s tests -v
```
