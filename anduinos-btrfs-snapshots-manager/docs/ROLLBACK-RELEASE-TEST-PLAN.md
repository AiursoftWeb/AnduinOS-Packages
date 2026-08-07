# Rollback release test plan and evidence register

This document is the release ledger for recovery protocol 2. It identifies every
manual or automated qualification lane, records what has already passed, and
keeps unresolved work visible. Passing unit tests or restoring one package on a
developer workstation does not by itself qualify a global recovery release.

## Release rule

Do not publish a stable global build while any P0 lane is pending or failed. A
lane is complete only when its evidence has been collected and reviewed. After
any rebooting rollback, stop before arming another transaction so the previous
transaction, journal, boot IDs, GRUB selector, and subvolume state can be
captured without being overwritten or confused with another run.

The disposable-VM procedure in [VM-QUALIFICATION.md](VM-QUALIFICATION.md) is
authoritative for destructive, fallback, Secure Boot, and power-loss lanes.

## Qualification register

| Test ID | Priority | Lane | Current status | Required evidence |
| --- | --- | --- | --- | --- |
| `RRP2-BUILD-001` | P0 | amd64 and arm64 APKG release build | Passed 2026-08-07 | Both Debs produced; prebuild and recovery artifact checks passed |
| `RRP2-UNIT-001` | P0 | Workspace, shell, initramfs, i18n, and zero-warning checks | Passed 2026-08-07 | Test output and `-D warnings` check |
| `RRP2-BTRFS-001` | P0 | Real operations on a disposable Btrfs loopback image | Passed 2026-08-07 | Both privileged loopback cases passed |
| `RRP2-UPGRADE-001` | P0 | Applied schema-2/protocol-1 transaction upgraded and reconciled by protocol 2 | Passed 2026-08-07 | Transaction `35fb6a1c-897b-4e27-b29c-9e5f9ad49952` archived `confirmed`; target `current`; fallback `ready` |
| `RRP2-HOST-001` | P0 acceptance | Fresh protocol-2 LKG, remove `docker.io`, App rollback, reboot, confirm | Pending | Checklist and evidence bundle below |
| `RRP2-VM-001` | P0 | Normal rollback and confirmation with `/run` mounted `noexec` | Pending | VM state, transaction JSON, service journal, GRUB state |
| `RRP2-VM-002` | P0 | Docker autoremove regression | Pending | Exact package version and executable restored; terminal transaction confirmed |
| `RRP2-VM-003` | P0 | Confirmation failure followed by automatic fallback | Pending | Two successful boots, fallback `current`, target `failed-reverted`, no orphan roots |
| `RRP2-VM-004` | P0 | Secure Boot enabled, disabled, and unsupported lanes | Pending | Toolkit state and pass/fail-closed results |
| `RRP2-VM-005` | P0 | Apply and revert hard-power-loss checkpoint matrix | Pending | Serial console, checkpoint logs, state and subvolume evidence for every row |
| `RRP2-UI-001` | P0 | Reboot inhibitor and phase-aware pending banner | Automated checks passed; interactive observation pending in `RRP2-HOST-001` | Error is visible when reboot is refused; no invalid cancel action after early boot |

`RRP2-HOST-001` is useful acceptance evidence on the affected machine, but it
does not replace the disposable VM lanes.

## RRP2-HOST-001 procedure

Preconditions:

- installed package is the APKG-built `0.1.0-16+resolute` or its reviewed successor;
- installed and initramfs recovery protocol both report `2`;
- CLI status reports no pending rollback and no recovery issue;
- the EFI one-shot GRUB environment is empty;
- no package snapshot transaction is pending; and
- `/.snapshots` is executable while `/run` remains `noexec`.

Procedure:

1. Create a manual snapshot titled `LKG protocol v2 rollback test` and record its
   deployment ID, creation time, snapshot UUID, and installed `docker.io` version.
2. Run `sudo apt-get remove --yes docker.io`. Do not use `autoremove` in this host
   lane; the broader autoremove scenario belongs to disposable VM test
   `RRP2-VM-002`.
3. Prove that `dpkg-query` no longer reports `docker.io` installed and that
   `/usr/bin/docker` is absent.
4. In the App, select the exact manual LKG by title, timestamp, and ID. Do not
   select the newer automatic `Before package changes` snapshot.
5. Prepare the rollback and choose **Restart Now**. If a logind/systemd inhibitor
   refuses the request, the App must display the diagnostic. Record it, close the
   inhibitor, and restart through the desktop power UI without cancelling the
   armed transaction.
6. Allow the GRUB one-shot selector to enter the recovery entry. Do not manually
   select normal boot.
7. After the graphical session is fully online, collect the evidence bundle
   before creating or arming any other rollback.

Pass criteria:

- the exact prior `docker.io` version and `/usr/bin/docker` return;
- CLI status reports `pending: null` and no issues;
- the target deployment is `current` and the protected fallback is `ready`;
- terminal history is `confirmed` under transaction schema 3 and protocol 2;
- the confirmation service executed successfully with no `203/EXEC`;
- its executable was the digest-bound external `recovery-boot/confirm`, not a
  payload below `/run`;
- the EFI one-shot selector is empty;
- the transaction-specific old and new root staging subvolumes are absent; and
- one subsequent ordinary reboot remains on the confirmed root.

Abort and preserve evidence if any criterion fails. Do not immediately retry or
start a second rollback.

## Evidence bundle

Capture this output after every rebooting lane:

```bash
date --iso-8601=seconds
dpkg-query -W -f='${Package} ${Version} ${Status}\n' \
    anduinos-btrfs-snapshots-manager docker.io
/usr/bin/anduinos-btrfs-snapshots-manager-cli status --json
cat /proc/sys/kernel/random/boot_id
uname -r
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS --target /run
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS --target /.snapshots
systemctl show -p FragmentPath -p ActiveState -p Result -p ExecMainStatus \
    anduinos-btrfs-snapshots-manager-confirm.service
sudo journalctl -b -u anduinos-btrfs-snapshots-manager-confirm.service --no-pager
sudo grub-editenv /boot/efi/EFI/anduinos/btrfs-snapshots-manager-grubenv list
sudo btrfs subvolume show --raw /
sudo btrfs subvolume list /
sudo find /.snapshots/anduinos-btrfs-snapshots-manager/rollback-history \
    -maxdepth 1 -type f -printf '%T@ %p\n' | sort -n | tail -3
```

Copy the relevant terminal rollback JSON from `rollback-history`. If the pending
file still exists, copy it before taking any corrective action. For a hang or
unexpected reset, also preserve the previous and current kernel journals:

```bash
sudo journalctl -b -1 -b 0 -k --no-pager
sudo journalctl -b -1 -b 0 -u \
    anduinos-btrfs-snapshots-manager-confirm.service --no-pager
```

## Test record template

Create one record per test ID in the release issue or artifact store:

```text
Test ID:
Result: PASS / FAIL / BLOCKED
Date and operator:
Commit and package version:
Architecture, firmware, and Secure Boot state:
Source VM/hypervisor snapshot:
Target deployment ID, title, time, and snapshot UUID:
Rollback transaction ID and boot IDs:
Expected result:
Observed result:
Evidence location:
Follow-up issue or commit:
Reviewer and sign-off time:
```

## Incident and root-cause register

| Incident ID | Observed failure | Root cause | Resolution |
| --- | --- | --- | --- |
| `RR-INC-001` | Recovery boot returned without restoring Docker | Premount trusted a non-exported `FSTYPE` shell variable and silently skipped recovery | Detect the root filesystem from its device; explicit requests fail visibly (`91a5673d`) |
| `RR-INC-002` | **Restart Now** appeared to do nothing | GUI checked only `Command::spawn`; `systemctl reboot` later failed because of an active inhibitor | Wait for exit status and display bounded stderr (`456506c2`) |
| `RR-INC-003` | Root switched and Docker returned, but transaction stayed `booted-unconfirmed` | Confirmation executable was placed below `/run`, which is mounted `noexec`; systemd returned `203/EXEC` | Protocol 2 stages and hash-binds the executable in external `recovery-boot` (`456506c2`) |
| `RR-INC-004` | Installing the fixed package still executed the obsolete path | A transient unit below `/run/systemd/system` shadowed the new vendor unit | Postinst removes only the product-generated transient unit and link before daemon-reload (`456506c2`) |
| `RR-INC-005` | Banner offered cancellation after the root switch while rollback actions were disabled | UI collapsed all pending phases into one presentation although cancellation is valid only before early boot | Phase-aware banner and authenticated reconciliation action (`456506c2`) |
| `RR-INC-006` | amd64/arm64 APKG release builds crashed in LLVM | Fat LTO with one codegen unit exhausted LLVM worker stacks | Reliable non-LTO release profile with bounded compiler stack (`456506c2`) |
| `RR-INC-007` | Entire graphical Linux session reportedly froze during investigation | Undetermined; retained logs do not establish a kernel, GPU, I/O, OOM, or recovery-engine cause | Open; collect previous/current kernel journals and console evidence on any recurrence |

## Remaining TODO

Release-blocking:

- [ ] Run and review `RRP2-HOST-001` once; stop and collect evidence before any second rollback.
- [ ] Run `RRP2-VM-001` and `RRP2-VM-002` from the clean disposable VM snapshot.
- [ ] Run the automatic fallback lane `RRP2-VM-003` without manual root repair.
- [ ] Complete Secure Boot lanes `RRP2-VM-004`.
- [ ] Complete and review every apply/revert interruption in `RRP2-VM-005`.
- [ ] Attach all records to the release and obtain an explicit recovery sign-off.

Follow-up hardening after the release gate is satisfied:

- [ ] Automate disposable-VM reboot and fallback lanes in the release pipeline.
- [ ] Add a one-command, privacy-reviewed recovery evidence bundle exporter.
- [ ] Make manual LKG and automatic APT snapshots more visually distinct in the selector.
- [ ] If `RR-INC-007` recurs, capture persistent kernel logs, hypervisor/serial console,
  GPU reset messages, I/O stalls, OOM records, and watchdog output before assigning a cause.

Do not delete or disable a recovery test merely because it is difficult. Move
destructive work to a disposable VM and record a genuine environmental blocker
when a lane cannot run.
