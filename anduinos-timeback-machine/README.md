# AnduinOS Timeback Machine

Rust, GTK4, and Libadwaita system-recovery frontend for the AnduinOS Btrfs
storage ABI.

TM-1 provides:

- the versioned deployment model and state machine;
- exact, read-only detection of the AnduinOS Btrfs subvolume layout;
- the `timebackctl inspect` diagnostic command;
- the system D-Bus and Polkit contracts;
- a hardened, D-Bus-activated, read-only system service;
- bounded deployment discovery that isolates malformed metadata;
- real recovery-point data in the responsive overview and timeline;
- CLI access to both local layout diagnostics and daemon-owned deployments.

No recovery-point mutation is implemented in TM-1. The daemon explicitly
rejects every create, pin, delete, rollback, and retention request until its
transactional implementation and failure-injection tests arrive in later
milestones.

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

On an installed package, query the D-Bus-activated read-only service with:

```bash
timebackctl list --json
```

See `ARCHITECTURE.md` before implementing any privileged operation. The
subvolume layout, deployment schema, D-Bus API, and state transitions are
safety boundaries rather than presentation details.
