# AnduinOS Waypoint implementation plan

This is a release-gated engineering checklist, not a list of optional ideas.
Items marked complete must have direct test or artifact evidence.

## 1. Import and package foundation

- [x] Import upstream Waypoint at commit
  `693c92ee877a13a37fbae1fb93957e138a01733d`.
- [x] Preserve the MIT license and upstream attribution under `upstream/`.
- [x] Commit the generated `Cargo.lock` for reproducible application builds.
- [x] Move compiled source under `src/` and package resources into the standard
  AnduinOS directories.
- [x] Make `cargo fmt`, tests, strict Clippy, and APKG lint blocking CI gates.
- [x] Produce clean amd64 and arm64 Deb packages with `apkg build` and verify
  that their application/helper ELF architecture matches the Deb architecture.

## 2. AnduinOS platform conversion

- [x] Replace every XBPS query and package comparison with dpkg/APT data.
- [x] Import current and rotated APT transaction history, including bounded
  gzip logs, without parsing localized terminal output.
- [x] Replace runit service control with systemd service management.
- [x] Replace `wheel` authorization with a least-privilege policy for local
  AnduinOS administrators in the `sudo` group.
- [x] Move D-Bus ownership to `org.anduinos.Waypoint` and Polkit actions to the
  `org.anduinos.waypoint` namespace.
- [x] Remove the upstream installer and all `/usr/bin` installation side effects;
  APKG must own every installed file.
- [x] Replace Void labels, help text, package-manager instructions, resource
  identifiers, and user paths.
- [x] Install the AnduinOS icon, desktop entry, and project-owned AppStream
  metadata with a stable developer identity and OARS declaration.
- [x] Install gettext catalogs and migrate all user-visible GTK strings to the
  localization framework.
  - [x] Make prebuild fail when a Rust `tr`/`trf` literal is absent from the POT
    or has no non-empty Simplified Chinese translation, and reject common GTK
    controls or response buttons that still contain direct language literals.
  - [x] Localize the desktop entry, AppStream summary and description, Polkit
    authentication prompts, desktop notifications, exported comparisons, and
    scheduler-generated recovery-point descriptions.
- [ ] Capture new 16:9 AppStream screenshots from the branded AnduinOS build;
  upstream screenshots are reference material and must not be published as if
  they depict the finished derivative.

## 3. Fixed AnduinOS storage ABI

- [x] Recognize only a mounted, internally consistent AnduinOS Btrfs layout.
- [x] Treat `@root` as the system deployment and `@home` as an independent,
  policy-controlled personal-data stream.
- [x] Never recursively snapshot `@snapshots`, swap, container storage, virtual
  machine images, or other excluded subvolumes.
- [x] Report unsupported/non-Btrfs layouts read-only; never guess or partially
  mutate them.
- [x] Use versioned, atomically replaced metadata and quarantine malformed state.
- [x] Calculate per-deployment referenced/exclusive qgroup space, label exclusive
  bytes only as estimated reclaimability, and enforce a 2 GiB transaction reserve.

## 4. Recovery engine

- [x] Remove the upstream default-subvolume rollback implementation.
- [x] Create immutable system recovery points and separate writable deployments.
- [x] Build rollback as an idempotent, resumable transaction.
- [x] Preserve a known-good fallback deployment.
- [x] Generate a verified one-shot GRUB recovery entry and provision the
  writable external GRUB environment block required on Btrfs.
- [x] Perform root replacement in initramfs.
- [x] Confirm successful userspace boot before committing the new deployment.
- [x] Expose cancellation and truthful pending/confirmed/failed state over D-Bus.
- [x] Verify kernel, initramfs, dpkg database, filesystem UUID, Secure Boot/MOK
  requirements, and all referenced subvolumes before scheduling rollback.

## 5. Product integration

- [ ] Adapt Waypoint's overview, list, comparison, scheduling, retention, quota,
  exclusion, and backup screens to the AnduinOS domain model.
  - [x] Connect the systemd scheduler to the internal
    `CreateScheduledDeployment` operation, derive schedule history from typed
    automatic-deployment metadata instead of display UUIDs, and remove the
    obsolete `ListSnapshots` compatibility method.
  - [x] Make schedule cards and editing use recovery-point terminology and the
    active timeline policy; keep legacy count/age fields readable in the config
    model without exposing a misleading deprecated editor.
  - [x] Remove the unreferenced legacy package/file comparison dialogs and the
    obsolete path-opening validator that existed only for the removed UI.
  - [x] Make package comparison read the two verified deployments' bounded
    dpkg status databases in the helper instead of comparing empty GUI caches.
  - [x] Replace the external-backup screen and privileged boundary with manual,
    UUID-only full-stream export, discovery, verification, import, and deletion.
  - [x] Remove the imported path-based `BackupManager`, mount watcher, backup
    dialogs, obsolete D-Bus client calls, progress signal, and per-user backup
    configuration from the compiled product.
  - [x] Keep independently verifiable full streams as the release baseline;
    authenticated incremental chains are deferred until they have atomic
    chain-level retention and power-loss qualification. See
    `docs/RECOVERY-SCOPE.md`.
- [x] Replace generic/custom subvolume selection with a fixed System recovery
  policy and an explicit independent Personal Files boundary; `/home` is never
  implied to be recoverable until a separate trusted design exists.
- [x] Make recovery previews explain packages, kernel, personal-data scope, boot
  fallback, and required restart.
- [x] Remove the imported individual-file restore GUI, CLI, D-Bus method, root
  path-copy implementation, and `rsync` dependency from the compiled product.
- [x] Design a non-privileged, descriptor-confined export path before adding
  individual-file recovery again; the root helper must never follow
  user-controlled destination paths. Remove the non-functional imported browse
  action while the root-owned store remains private. See
  `docs/RECOVERY-SCOPE.md`.
- [x] Integrate paired APT pre/post recovery points using fail-open hooks that
  can never make APT or dpkg fail.
- [ ] Finish hardening and integration-testing the root D-Bus helper and systemd
  services. The release build already removes legacy path-based backup methods,
  restricts callers to root/local sudo administrators, fixes privileged paths and
  environment handling, narrows the helper write sandbox, and gives external backup
  its own non-cached administrator authorization action.
  - [x] Bound file-comparison scan input, entry count, and serialized D-Bus
    output; use NUL-delimited records, reject unsafe/non-UTF-8 paths, and include
    file kind and ctime instead of trusting size and mtime alone.
- [x] Keep all arbitrary package installation and unrelated system-management
  features out of this application.

## 6. Qualification gates

- [x] Unit-test all parsers, validation, retention, space, and state transitions.
- [x] Integration-test D-Bus caller identity and every Polkit action.
  - [x] Add an installed-system qualification covering the exact action set,
    root authorization, sudo-group read access, non-administrator denial, every
    action's safe validation path, state non-mutation, and required methods.
- [x] Loopback-test supported and malformed Btrfs layouts.
  - [x] Exercise full send, dump validation, chrooted receive, read-only state,
    duplicate receive identity, and disposable-image cleanup.
  - [x] Exercise real immutable recovery-point creation, content isolation,
    verification, pin/delete protection, automatic-retention floors, failure
    metadata, and failed-subvolume cleanup on a disposable Btrfs image.
  - [x] Reject non-Btrfs, incomplete, cross-filesystem, and unavailable layout
    reports before creating state, and prove that rejected layouts cannot pin,
    verify, delete, or otherwise mutate an existing real Btrfs recovery point.
- [ ] VM-test create, update, rollback, reboot, confirm, cancel, and repeated
  rollback cycles.
  - [x] Provide a VM-only, explicit-consent qualification helper and a
    reproducible normal/cancellation/repeated-cycle result contract.
- [ ] Inject power loss at every destructive transaction boundary.
  - [x] Publish the exact apply/revert checkpoint matrix, collection commands,
    and pass criteria without shipping a runtime fault-injection interface.
- [x] Test full disk, missing snapshot, damaged metadata, missing kernel/initramfs,
  GRUB generation failure, and non-Btrfs systems.
  - [x] Exercise the real Btrfs reserve gate, unknown deployment IDs, missing
    kernel and initramfs cleanup, and rejected nonstandard layouts on disposable
    loopback filesystems.
  - [x] Cover malformed and oversized metadata, missing deployment roots, and
    every post-fallback GRUB command failure with deterministic state-machine
    tests; verify the packaged helper remains read-only on a real ext4 host.
- [x] Inspect Deb ownership, permissions, maintainer scripts, D-Bus activation,
  systemd sandboxing, AppStream metadata, and uninstall behavior.
  - [x] Unpack both APKG-built architectures; validate control scripts,
    AppStream metadata, locales, executable modes, initramfs payload, installed
    file ownership, and absence of the removed path-based backup ABI.
  - [x] Reinstall the final amd64 Deb twice and verify that the new pre-removal
    script stops the old D-Bus helper before on-demand reactivation.
  - [x] Verify that package and recovery GRUB refresh paths isolate
    `os-prober`, do not discover unrelated operating systems, and leave no
    probing processes behind.
  - [x] Exercise clean install, repeated package replacement, purge, and
    reinstall while proving that purge removes generated configuration but
    never recovery-point data. There is no older released Waypoint version to
    upgrade from.
    - [x] Preserve unknown administrator configuration and runtime data while
      removing the generated schedule file and external GRUB environment.
    - [x] Repeat purge with the guarded APT hook and prove that no removed hook
      binary is executed.
    - [x] Move confirmation-unit enable/disable into the explicit maintainer
      scripts and repeat the lifecycle without an APKG postrm warning.
- [ ] Install the APKG-built packages on a real Secure Boot AnduinOS machine and
  manually validate the GUI and non-destructive workflows.
  - [x] Install and repeatedly overwrite the amd64 Deb on a real Secure Boot
    ext4 machine; confirm Secure Boot remains enabled, D-Bus reports the layout
    as unsupported, the GUI presents a translated read-only unavailable state,
    and `dpkg -V` is clean.
  - [ ] Repeat on a clean machine using the exact installer-created AnduinOS
    Btrfs layout; test create, export, verify, import, delete, and restore
    previews before testing an actual rebooting rollback.
