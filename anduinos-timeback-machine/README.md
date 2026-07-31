# AnduinOS Timeback Machine

Rust, GTK4, and Libadwaita system-recovery frontend for the AnduinOS Btrfs
storage ABI.

TM-0 provides:

- the versioned deployment model and state machine;
- exact, read-only detection of the AnduinOS Btrfs subvolume layout;
- the `timebackctl inspect` diagnostic command;
- the system D-Bus and Polkit contracts;
- a responsive application shell for the planned recovery workflow.

No privileged recovery operation is implemented in TM-0. Buttons that would
create or restore a recovery point explain the future milestone and do not
modify the system.

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

See `ARCHITECTURE.md` before implementing any privileged operation. The
subvolume layout, deployment schema, D-Bus API, and state transitions are
safety boundaries rather than presentation details.
