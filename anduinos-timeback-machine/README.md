# AnduinOS Timeback Machine

Rust, GTK4, and Libadwaita system-recovery frontend for the AnduinOS Btrfs
storage ABI.

The current TM-3 implementation provides:

- the versioned deployment model and state machine;
- exact, read-only detection of the AnduinOS Btrfs subvolume layout;
- the `timebackctl inspect` diagnostic command;
- the system D-Bus and Polkit contracts;
- a hardened, D-Bus-activated system service with a narrow Btrfs capability;
- bounded deployment discovery that isolates malformed metadata;
- real recovery-point data in the responsive overview and timeline;
- atomic, power-loss-aware manual recovery-point creation;
- integrity verification for the snapshot, dpkg database, kernel, initramfs,
  boot artifacts, and optional MOK certificate;
- guarded pin, unpin, and idempotent delete operations;
- asynchronous progress in both the GTK interface and CLI;
- Polkit authorization for every mutation.

TM-3 also provides the atomic rollback transaction, a one-shot verified
GRUB entry, an idempotent initramfs root replacement, protected fallback,
userspace boot confirmation, D-Bus scheduling and cancellation, and the GTK
restore workflow. These paths are unit-tested but still require destructive VM
qualification before release. TM-4A adds fail-open, paired recovery points
around APT-managed dpkg changes. TM-4B adds the conservative Balanced retention
policy, live free-space planning, and re-measured post-APT cleanup. Its
destructive Btrfs path still requires VM qualification before release.
TM-5A adds a hardened, fail-open periodic maintenance service so space pressure
is detected even when no package transaction has recently completed.

## Local development

```bash
cargo test --all-targets
cargo run --bin anduinos-timeback-machine
```

The current machine's real storage layout is shown by default. A fully
populated visual-design preview is available without requiring a Btrfs system:

```bash
ANDUINOS_TIMEBACK_DEMO=1 cargo run --bin anduinos-timeback-machine
```

Inspect the machine-readable layout report with:

```bash
cargo run --bin timebackctl -- inspect --json
```

On an installed package, query or manage the D-Bus-activated service with:

```bash
timebackctl list --json
timebackctl create --pin "Before a risky change"
timebackctl verify DEPLOYMENT_ID
timebackctl pin DEPLOYMENT_ID
timebackctl unpin DEPLOYMENT_ID
timebackctl delete DEPLOYMENT_ID
timebackctl restore DEPLOYMENT_ID
timebackctl restore --cancel
timebackctl retention --json
timebackctl retention --apply
```

See `ARCHITECTURE.md` before implementing any privileged operation. The
subvolume layout, deployment schema, D-Bus API, and state transitions are
safety boundaries rather than presentation details.

The detailed rollback transaction and power-loss protocol is documented in
`TM3-DESIGN.md`.
The package hook and retention safety boundary is documented in
`TM4-DESIGN.md`.
Automatic maintenance and destructive qualification are documented in
`TM5-DESIGN.md`.
The guarded guest-side qualification entry points live under `tests/vm/` and
must run only on a disposable Btrfs VM.
