# Changelog

## 0.4.0 — 2026-08-03

### User experience

- Reorganized the application around Overview, System History, Recover Files,
  and Automatic Protection.
- Added a real-state protection checklist with direct first-run actions and
  distinct Active, Setup Needed, and Attention states.
- Added a bounded, scrollable branch map with a truthful “You Are Here” node,
  verified parent connectors, rollback forks, and separate legacy history.
- Added state-aware history actions for browsing, verification, restore
  preparation, and cancellation.
- Promoted pending restore state to Overview and documented the one-shot GRUB,
  normal-boot escape, successful branch, and automatic fallback behavior.
- Added responsive empty states, keyboard shortcuts, and clearer snapshot
  timing information.

### Recovery and data access

- Added durable system-lineage metadata with atomic storage, cycle validation,
  conservative legacy migration, deletion tombstones, and activation outcomes.
- Exposed the read-only lineage graph through the D-Bus service.
- Kept System and Personal Files browsing and copy-out independent from full
  system restore.
- Replaced ambiguous snapshot creation actions with one explicit target
  selector for System and User Data, System Only, or User Data Only. Manual
  user-data snapshots are labeled, linked to paired System points when
  applicable, excluded from automatic retention, and independently deletable.

### Validation

- Passed the complete unit, GUI, localization, packaging, and static contract
  checks.
- Passed real Btrfs create, verify, and delete qualification on a disposable
  loopback filesystem.
- Preserved all existing translations; new 0.4 strings use a non-empty English
  fallback in non-English catalogs until their translation review is complete.
- Full GRUB/initramfs reboot and power-cut qualification still requires a
  disposable AnduinOS VM and is not claimed by this release.
