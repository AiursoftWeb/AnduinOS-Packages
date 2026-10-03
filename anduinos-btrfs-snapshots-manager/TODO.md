# Disk Snapshots Manager engineering acceptance requirements

This file records the single current product baseline. Repository history is the
only home of removed product interfaces; no second UI is maintained.

## Application foundation

- Use a typed `adw::Application` and typed `adw::ApplicationWindow`.
- Keep one reusable main window while allowing independent File History
  windows for cold and warm GApplication activation.
- Centralize application/window actions and keyboard accelerators.
- Use `AdwToolbarView`, `AdwViewStack`, adaptive `AdwViewSwitcherBar`, and
  a width breakpoint compatible with libadwaita 1.4.
- Remove timer sources when the main window is disposed and coalesce snapshot
  signals into scope-specific refresh generations.

## Snapshot pages and behavior

- Provide symmetric System Recovery and Personal Files Recovery lists.
- Model loading, unsupported-layout, error, empty, no-result, and content
  states explicitly.
- Derive browse/check/rollback/delete/protect/rename availability from one
  pure capability matrix with unit coverage.
- Keep batch selection explicit and delete all selected points through one
  helper call and one Polkit decision.
- Preserve the rollback safety flow: target check, fixed impact summary,
  transaction-protected current-system fallback, Personal Files unchanged, cancel before
  restart, and pending-state banner.
- Keep system and Home browsing descriptor-confined and recover ordinary
  files/directories from the unprivileged process.

## Automation and settings

- Configure System and Home automatic snapshots independently.
- Configure a one-to-24-hour freshness target; catch-up behavior is owned by
  the always-enabled systemd timer and scheduler.
- Hide automatic cleanup details when cleanup is off and expose the five explicit
  retention tiers when it is on.
- Keep package-before, package-after-success, pre-snapshot, success, and
  cleanup notification choices in Advanced Settings with truthful service state.
- Run blocking D-Bus/configuration work away from the GTK main thread and
  ignore callbacks after their owning window is gone.

## Removed product surface

- Remove external-drive workflows and their GUI, CLI, schemas, fixtures,
  scripts, documentation, and unreachable engine code.
- Remove arbitrary snapshot comparison, Analytics, old Quota/Storage pages,
  theme switching, legacy scheduler pages, and uncompiled compatibility modules.
- Keep rollback impact explanation; it is a safety confirmation, not an
  arbitrary comparison feature.
- Keep the existing trusted D-Bus names, method signatures, Polkit action IDs,
  recovery metadata, and boot transaction formats unchanged.

## Release qualification

Engineering commands and package test profiles are documented in
[README.md](README.md#development-and-qualification). Release qualification
requires results for the candidate commit and package version; this checklist
does not certify a build.

Recovery test procedures and evidence requirements are maintained in
[docs/ROLLBACK-RELEASE-TEST-PLAN.md](docs/ROLLBACK-RELEASE-TEST-PLAN.md).
Destructive reboot and power-loss tests require a disposable VM following
[docs/VM-QUALIFICATION.md](docs/VM-QUALIFICATION.md).
