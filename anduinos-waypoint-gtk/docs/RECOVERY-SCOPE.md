# Recovery scope decisions

This document records the trusted boundaries shared by System recovery and the
independent Personal Files history.

## External backups use full streams

Waypoint exports one complete, read-only Btrfs send stream for one verified
System or Personal Files history point. The two stream types use separate
directories and manifests and are never treated as an atomic pair. Each manifest authenticates the exact stream size and SHA-256,
and an import is accepted only after the complete stream has been validated and
received into private staging.

Incremental chains are not part of the release baseline. They save space, but
also make every child depend on the availability and authenticity of its parent
chain. Deletion, retention, interrupted replacement, removable-media loss, and
parent UUID validation would all become recovery-critical operations. A full
stream is independently verifiable and restorable, which is the more valuable
property for disaster recovery. Incremental backup may be reconsidered only
with a versioned chain manifest, atomic chain-level retention, and power-loss
qualification; it must never silently replace the full-stream format.

## Personal files are exported, never restored by root

The recovery store is intentionally root-owned and mode `0700`. Personal Files
history snapshots remain private there; the desktop never receives a store path
and cannot browse another local user's home.

The shipped individual-file feature uses this boundary:

1. The GUI supplies a verified Personal Files snapshot ID and a bounded relative source
   path. It never supplies a destination path to the privileged helper.
2. The helper anchors lookup at the verified deployment root and resolves every
   component with `openat2(2)` using `RESOLVE_BENEATH`, `RESOLVE_NO_MAGICLINKS`,
   and a no-symlink first-release policy. Absolute paths, `..`, control bytes,
   devices, sockets, FIFOs, and mount crossings are rejected.
3. Directory listing is a separate, bounded metadata operation. It returns no
   file contents and no host paths. A snapshot read lock prevents deletion
   while a listing or export is active.
4. For one regular file, the helper returns a read-only Unix file descriptor
   over D-Bus after non-cached Polkit authorization. The descriptor is the
   capability; no temporary world-readable directory or persistent mount is
   created.
5. The unprivileged GUI chooses the destination with the desktop file chooser
   and writes it under the caller's credentials. The helper never creates,
   overwrites, changes ownership of, or follows links in a caller-selected
   destination. Single-file recovery writes a private temporary file beside
   the destination, synchronizes it, and atomically replaces the selected name;
   a failed stream never truncates the existing file.
6. Folder recovery repeats those descriptor-confined operations and creates a
   fresh destination tree as the desktop user. Existing folders are never
   merged or overwritten implicitly. The feature does not claim to preserve
   ownership, ACLs, extended attributes, hard links, or symlinks.

Automatic notification events form a separate metadata-minimal channel. A
root helper may announce only the fixed history scope (`system` or `personal`)
and aggregate deletion counts. The unprivileged per-session notifier converts
those events into desktop banners; recovery-point titles, caller identities,
paths, and file metadata are not included.

Required tests include symlink swaps at every component, deleted/replaced
snapshots, oversized directories and files, special files, mount crossings,
caller disconnects, concurrent deletion, denied authorization, and destination
overwrite behavior under the unprivileged GUI process.

## Nautilus only activates the unprivileged application

The Nautilus 4 extension contributes “View File History…” for one selected
item and “Browse This Folder’s History…” for the current folder background.
It accepts only native `file://` locations that resolve directly beneath the
current user's home, and hides both actions for remote/GVfs locations, Trash,
special files, multi-selection, and paths containing symlinks.

Menu construction never queries snapshots or contacts the system service. On
activation the extension sends the mode and URI to the `file-history`
GApplication action over the user's session D-Bus. A session service starts
Waypoint when needed, with no selected path in `argv`. The GTK application
canonicalizes and validates the request again, converts it to a relative
Personal Files source, and only then uses the existing bounded listing and
read-only descriptor API. Neither the extension nor the session activation
service has a privileged destination-path interface.
