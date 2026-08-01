# TM-5 automatic maintenance and failure qualification

## Scope

TM-5A runs the fixed Balanced retention policy independently of package
transactions. TM-5B qualifies the destructive snapshot, rollback, recovery,
and retention paths with VM failure injection. Neither phase changes the
release-one Btrfs storage ABI or turns recovery points into backups.

## Automatic maintenance

The post-APT hook remains the fastest cleanup path after package activity. A
persistent systemd timer additionally runs every six hours, with a randomized
delay, so space pressure caused by unrelated workloads is eventually noticed.
The timer invokes a dedicated root helper rather than the graphical D-Bus API.
It therefore does not create an interactive Polkit request in the background.

Every pass first performs a read-only retention inspection. A healthy system
with no eligible actions performs no mutation. If cleanup is required, the
existing coordinator deletes at most one planned automatic recovery point,
re-reads all metadata, re-measures free space, and plans again. Manual, pinned,
pending, fallback, non-ready, and sole known-good recovery points remain
ineligible.

The helper is fail-open and always exits successfully. Unsupported ext4 or
nonstandard layouts are skipped. Unsafe metadata, a concurrent transaction, a
failed deletion, or unresolved pressure is logged for diagnosis without making
the timer, boot, APT, or the desktop session fail.

The service receives only `CAP_SYS_ADMIN`, has a read-only system view, and may
write only beneath `/.snapshots`. It does not write `/boot`, schedule rollback,
or create package recovery points.

## TM-5B qualification boundary

The destructive suite must run only on disposable VM disks. It will exercise
power loss or command failure after every persistent transition in:

- manual and automatic snapshot creation;
- paired APT pre/post transactions;
- retention deletion and re-planning;
- rollback scheduling and GRUB one-shot arming;
- initramfs root replacement and automatic fallback;
- userspace confirmation and final cleanup.

Every injected failure must leave either the original `@root`, the selected
recovery point, or the protected fallback bootable. The suite must also verify
that all persistent subvolumes remain mounted and byte-identical across root
replacement. Passing unit tests alone does not qualify TM-3 through TM-5 for
release.

## Guest qualification harness

`tests/vm/smoke.sh` exercises real Btrfs recovery-point, APT-pair, and
maintenance operations without rebooting. `tests/vm/rollback-cycle.sh` performs
one real GRUB/initramfs rollback and installs a temporary resume verifier inside
the selected target snapshot. After boot confirmation, that verifier checks the
restored root identity and proves that all five persistent boundaries retained
their test token.

Both entry points require root, VM detection, an explicit destructive
confirmation string, the kernel argument `anduinos.timeback.test=1`, and the
exact supported storage ABI. The resume verifier requires the persistent state
created behind those gates. The harness is never installed in the binary
package.

## Host power-cut controller

`tests/vm/powercut.py` accepts a powered-off, already-armed qcow2 fixture and
creates a fresh overlay for every checkpoint. The recovery engine emits stable
diagnostic checkpoint messages only after the corresponding transaction update
or Btrfs sync has completed. The controller consumes the serial stream over a
Unix socket and sends `SIGKILL` to the QEMU process group when the requested
message appears.

Apply checkpoints require two boots: the interrupted recovery boot followed by
automatic fallback. Revert checkpoints require three boots: an initial apply
interruption, a second interruption during fallback, and a final reconciliation
boot. The guest resume verifier reads a QEMU fw_cfg expectation, proves that the
protected root returned, and rechecks all persistent boundaries.

The controller never launches the fixture as a writable disk. It hash-checks
the fixture and optional UEFI vars fixture before and after the suite, copies
UEFI vars per scenario, retains every overlay and serial log, invokes no shell,
and performs no automatic evidence deletion.
