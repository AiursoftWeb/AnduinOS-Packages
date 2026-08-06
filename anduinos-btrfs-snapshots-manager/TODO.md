# Disk Snapshots Manager engineering acceptance

This file records the single current product baseline. Repository history is the
only home of removed product interfaces; no second UI is maintained.

## Application foundation

- [x] Use a typed `adw::Application` and typed `adw::ApplicationWindow`.
- [x] Keep one reusable main window while allowing independent File History
  windows for cold and warm GApplication activation.
- [x] Centralize application/window actions and keyboard accelerators.
- [x] Use `AdwToolbarView`, `AdwViewStack`, adaptive `AdwViewSwitcherBar`, and
  a width breakpoint compatible with libadwaita 1.4.
- [x] Remove timer sources when the main window is disposed and coalesce snapshot
  signals into scope-specific refresh generations.

## Snapshot pages and behavior

- [x] Provide symmetric System Recovery and Personal Files Recovery lists.
- [x] Model loading, unsupported-layout, error, empty, no-result, and content
  states explicitly.
- [x] Derive browse/check/rollback/delete/protect/rename availability from one
  pure capability matrix with unit coverage.
- [x] Keep batch selection explicit and delete all selected points through one
  helper call and one Polkit decision.
- [x] Preserve the rollback safety flow: target check, fixed impact summary,
  permanent current-system fallback, Personal Files unchanged, cancel before
  restart, and pending-state banner.
- [x] Keep system and Home browsing descriptor-confined and recover ordinary
  files/directories from the unprivileged process.

## Automation and settings

- [x] Configure System and Home automatic snapshots independently.
- [x] Configure a one-to-24-hour freshness target; catch-up behavior is owned by
  the always-enabled systemd timer and scheduler.
- [x] Hide Smart Cleanup details when cleanup is off and expose the five explicit
  retention tiers when it is on.
- [x] Keep package-before, package-after-success, pre-snapshot, success, and
  cleanup notification choices in Advanced Settings with truthful service state.
- [x] Run blocking D-Bus/configuration work away from the GTK main thread and
  ignore callbacks after their owning window is gone.

## Removed product surface

- [x] Remove external-drive workflows and their GUI, CLI, schemas, fixtures,
  scripts, documentation, and unreachable engine code.
- [x] Remove arbitrary snapshot comparison, Analytics, old Quota/Storage pages,
  theme switching, legacy scheduler pages, and uncompiled compatibility modules.
- [x] Keep rollback impact explanation; it is a safety confirmation, not an
  arbitrary comparison feature.
- [x] Keep the existing trusted D-Bus names, method signatures, Polkit action IDs,
  recovery metadata, and boot transaction formats unchanged.

## Release gates for 0.1.0-8

- [x] Format, workspace tests, strict Clippy, i18n coverage, prebuild guards, and
  GTK construction/destruction smoke test pass.
- [x] AppStream and desktop metadata describe only Disk Snapshots Manager 2.0.
- [ ] APKG builds the `0.1.0-8` amd64 and arm64 Debian packages.
- [ ] Install the built package, verify files/metadata/services/D-Bus activation,
  and exercise non-destructive cold/warm GUI and File History activation.
- [x] Leave the working tree uncommitted as requested.

The destructive VM and power-loss matrix remains in
[docs/VM-QUALIFICATION.md](docs/VM-QUALIFICATION.md). It is a recovery-engine
release qualification and is intentionally not run on this workstation.
