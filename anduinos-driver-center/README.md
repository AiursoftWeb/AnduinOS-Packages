# AnduinOS Driver Center

A focused GTK4/libadwaita application for inspecting, installing, and repairing
hardware drivers. It replaces the only broadly useful part of
`software-properties-gtk` without inheriting Ubuntu's repository, update,
authentication, and release-upgrade user interfaces.

The unprivileged UI reads hardware state. Mutating operations go through a
fixed polkit helper which only accepts drivers reported by `ubuntu-drivers`,
the AnduinOS xpadneo package, and the local Secure Boot enrollment workflow.

## Development

```bash
PYTHONPATH=src python3 -m anduinos_driver_center
python3 -m unittest discover -s tests -v
```
