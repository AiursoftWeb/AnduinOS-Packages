# Recovery scope decisions

This document records two deliberate product boundaries for the first trusted
AnduinOS Waypoint release. They are security decisions, not missing migrations
from upstream Waypoint.

## External backups use full streams

Waypoint exports one complete, read-only Btrfs send stream for one verified
recovery point. The manifest authenticates the exact stream size and SHA-256,
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

## Individual files are exported, never restored by root

The recovery store is intentionally root-owned and mode `0700`. Opening its
deployment path in a desktop file manager cannot work for a normal user, and
loosening those permissions would expose historical copies of sensitive system
files. Therefore the imported “Browse Files” action is not shipped.

A future individual-file feature must be an explicit administrator-authorized
export with this boundary:

1. The GUI supplies a verified deployment ID and a bounded relative source
   path. It never supplies a destination path to the privileged helper.
2. The helper anchors lookup at the verified deployment root and resolves every
   component with `openat2(2)` using `RESOLVE_BENEATH`, `RESOLVE_NO_MAGICLINKS`,
   and a no-symlink first-release policy. Absolute paths, `..`, control bytes,
   devices, sockets, FIFOs, and mount crossings are rejected.
3. Directory listing is a separate, bounded metadata operation. It returns no
   file contents and no host paths. A deployment read lock prevents deletion
   while a listing or export is active.
4. For one regular file, the helper returns a read-only Unix file descriptor
   over D-Bus after non-cached Polkit authorization. The descriptor is the
   capability; no temporary world-readable directory or persistent mount is
   created.
5. The unprivileged GUI chooses the destination with the desktop file chooser
   and writes it under the caller's credentials. The helper never creates,
   overwrites, changes ownership of, or follows links in a caller-selected
   destination.
6. The GUI calls this operation “Export a File”. It does not claim to restore
   ownership, ACLs, extended attributes, hard links, or an in-place system
   pathname.

Required tests include symlink swaps at every component, deleted/replaced
deployments, oversized directories and files, special files, mount crossings,
caller disconnects, concurrent deletion, denied authorization, and destination
overwrite behavior under the unprivileged GUI process. Until that interface and
its tests exist, whole-system recovery and full external backup are the only
recovery mechanisms Waypoint presents.
