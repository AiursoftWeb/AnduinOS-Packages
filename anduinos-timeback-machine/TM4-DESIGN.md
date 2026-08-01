# TM-4 package recovery and retention protocol

## Scope

TM-4A creates paired recovery points around package changes made through APT.
TM-4B adds conservative retention and space-pressure handling. Neither
phase changes APT's package selection, invokes APT recursively, or treats a
recovery point as a backup.

Raw `dpkg -i` invocations are outside the first hook contract. APT-managed dpkg
transactions, including unattended upgrades, execute the installed
`DPkg::Pre-Invoke` and `DPkg::Post-Invoke` hooks.

## Availability rule

Package installation must never depend on Timeback Machine. The apt.conf
commands test that the helper exists and end with `|| true`. The helper itself
also reports every internal error as a warning and exits successfully. Missing
Btrfs support, an incomplete installer mount layout, snapshot failure, corrupt
metadata, and a competing rollback therefore cannot make APT fail.

## Transaction state

The single active package transaction is stored at:

```text
/.snapshots/anduinos/transactions/pending-package.json
```

Completed or interrupted records move atomically to:

```text
/.snapshots/anduinos/transactions/package-history/<transaction-id>.json
```

The records conform to `docs/package-transaction-v1.schema.json` and use this
state machine:

```text
PreparingPre -> AwaitingPost -> Complete
      |              |
      +--------------+-------> Interrupted
```

`PreparingPre` is committed before the first snapshot. `AwaitingPost` contains
the `AptPre` deployment ID. `Complete` contains both `AptPre` and `AptPost`
IDs. If a newer APT operation finds an old nonterminal transaction, it archives
that transaction as `Interrupted` before starting a new pair. A valid pre
recovery point is retained even when the package operation or post snapshot was
interrupted.

## Coordination with system rollback

Package hooks and rollback scheduling are mutually exclusive at their start
boundary. Both take `transactions/start.lock`, re-check the other pending
record while holding the lock, and then commit their own pending record. A
pending rollback makes the package hook skip snapshots. A pending package
transaction makes rollback scheduling stop before creating its fallback.

## Snapshot identity

Both automatic points use the same complete identity contract as manual
points: Btrfs UUID and read-only state, kernel and initramfs, boot artifact,
dpkg status database, and optional MOK certificate. They differ only by their
typed `AptPre` or `AptPost` kind and transaction-linked description.

## TM-4B retention boundary

The fixed **Balanced** policy considers only complete or interrupted package-
history records and deployments which are all of:

- `Ready`;
- unpinned;
- `AptPre` or `AptPost`;
- not referenced by a pending rollback or package transaction;
- not the sole known-good restorable deployment.

Pair membership must be respected: a normal age-based cleanup deletes an
eligible pair together. If either member is pinned, protected, or non-ready,
the whole normal pair is retained. Under space pressure, keeping the pre point
is more valuable than keeping an unpaired post point, so eligible post points
are considered before their pre points.

Balanced retains at least the newest two complete package transactions, keeps
at most ten complete transactions, and considers older transactions after 30
days. It always preserves at least one known-good restorable deployment. Its
free-space target is 10% of the Btrfs filesystem, with a 4 GiB floor, a 32 GiB
cap, and a final cap of one quarter of small filesystems.

The executor re-reads deployment and transaction metadata, re-measures free
space, and rebuilds the plan after every deletion. It stops as soon as the
space target is met. Corrupt metadata, an unsafe history entry, a competing
transaction, or a deletion failure stops cleanup. The APT hook reports that as
a warning and still exits successfully.

`InspectRetention` exposes the exact plan without mutation. `RunRetention`
applies it after Polkit authorization. The same coordinator runs automatically
after a package transaction reaches a terminal archive state. Destructive
Btrfs VM and power-loss qualification remains a release gate.
