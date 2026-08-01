# TM-5 disposable-VM qualification

These tests mutate real Btrfs subvolumes, schedule a GRUB one-shot boot, and
may reboot the machine. Never run them on a workstation or a VM containing
valuable data.

The guest must be an AnduinOS installation using the exact six-subvolume Btrfs
layout. Add this kernel argument to the disposable VM before running tests:

```text
anduinos.timeback.test=1
```

Every entry point also requires root, hardware-virtualization detection, and an
explicit per-process confirmation:

```bash
export ANDUINOS_TIMEBACK_VM_CONFIRM=DESTROY_THIS_DISPOSABLE_VM
sudo --preserve-env=ANDUINOS_TIMEBACK_VM_CONFIRM tests/vm/smoke.sh
sudo --preserve-env=ANDUINOS_TIMEBACK_VM_CONFIRM \
  tests/vm/rollback-cycle.sh --reboot
```

The smoke test exercises real recovery-point creation, verification, pinning,
deletion, APT pre/post pairing, and periodic retention. The rollback cycle
installs a temporary resume service before creating its target point, changes a
file in `@root`, schedules the verified one-shot recovery entry, and checks
after reboot that the old root content returned while markers in `@home`,
`@log`, `@snapshots`, `@containers`, and `@libvirt` remained unchanged.

The persistent result is written to:

```text
/.snapshots/anduinos/tm5-vm-test/state.json
```

Power-cut injection still requires the TM-5B host-side QEMU controller. These
guest tests establish the baseline cycle that controller will repeatedly
interrupt.

## Preparing a power-cut fixture

Install the package containing the checkpoint protocol, configure the guest
kernel command line with both `anduinos.timeback.test=1` and `console=ttyS0`,
then run `rollback-cycle.sh` without `--reboot`. Power the guest off without
booting it again. That powered-off qcow2 image is the armed fixture.

The host controller never starts the fixture directly. It creates one qcow2
overlay per checkpoint and verifies the SHA-256 of the fixture before and after
the complete run. For UEFI guests, provide the immutable firmware code image
and the powered-off fixture's vars image; the vars image is also copied for
every scenario and hash-checked.

```bash
mkdir -p /var/tmp/timeback-results
tests/vm/powercut.py \
  --fixture /absolute/path/armed-timeback.qcow2 \
  --output-dir /var/tmp/timeback-results \
  --uefi-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --uefi-vars /absolute/path/armed-timeback-vars.fd \
  --confirm DESTROY_TM5_QEMU_OVERLAYS
```

The controller cuts power after each of five apply checkpoints. For each of
the five revert checkpoints it first interrupts apply, reboots into automatic
fallback, cuts power again during fallback, then boots a third time. Guest
serial output and the surviving overlay are retained for every scenario; the
controller never deletes evidence automatically.
