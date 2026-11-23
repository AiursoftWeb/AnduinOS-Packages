# Disk Snapshots Manager

Disk Snapshots Manager provides fast local recovery for AnduinOS installations that use Btrfs. It keeps system snapshots and Personal Files snapshots separate, so rolling back the operating system does not roll back the user's Home directory.

!!! important "Snapshots are not backups"

    Snapshots normally remain on the same physical disk as the live system. They are useful for recovering from a broken update, configuration change, or accidental file edit, but they do not protect against disk failure, theft, or loss of the computer. Keep an independent backup on another disk or service.

The application is installed on new Btrfs systems. It is not used on Ext4 installations because Ext4 does not provide the required Btrfs subvolume and snapshot model.

## System Recovery

The **System Recovery** page manages read-only snapshots of the operating system. Each row records when and why the snapshot was created, together with information such as the kernel and package count.

![System Recovery snapshots](images/system-recovery.png)

Select **Create Snapshot Now** before a risky system change. Automatic and package-management snapshots may also appear here according to the configured policy.

The actions for a system snapshot include browsing its files, viewing properties, changing protection, deleting it, and preparing a rollback when the snapshot is healthy and available.

### Roll back the operating system

Use **Roll Back** when an update or system configuration change has made AnduinOS unusable or unstable.

1. Save your current work and close applications.
2. Select the intended system snapshot and choose **Roll Back**.
3. Read the confirmation carefully and authenticate when requested.
4. Allow Disk Snapshots Manager to prepare the recovery files.
5. Restart when prompted. Once rollback preparation has been armed, the computer also starts an automatic 60-second restart countdown.
6. Let the recovery boot finish without interrupting power.

Before changing the active system root, the recovery engine creates and protects a fallback snapshot of the current system. The selected snapshot remains reusable after a successful rollback.

!!! warning "Personal Files are not rolled back"

    A system rollback changes the operating-system root but deliberately leaves Personal Files unchanged. This prevents a driver or package rollback from silently discarding newer documents. Recover older personal files separately from **Personal Files Recovery**.

## Personal Files Recovery

The **Personal Files Recovery** page manages snapshots of Home directories independently from the operating system.

![Personal Files Recovery snapshots](images/personal-files-recovery.png)

Select **Browse Files** to explore the contents of a snapshot. You can recover a file or folder without replacing the whole Home directory. Recovery writes an ordinary copy chosen by the current user; when necessary, the application uses a distinct recovered name instead of silently overwriting unrelated data.

Personal Files history is restricted to the authenticated user's own Home directory. System snapshot browsing requires administrator authorization.

## Recover an earlier version from Files

Disk Snapshots Manager integrates with Files (Nautilus). Right-click one local Home file and choose **View File History…**, or right-click a Home folder and choose **Browse This Folder's History…**.

![File History opened from Files](images/file-history.png)

The File History window lists snapshots containing the selected item:

- **Browse** opens that historical version without changing the current file.
- **Recover…** writes a recovered copy after you choose the destination.

Only local paths inside the user's Home directory are eligible. Network locations, mounted external paths, and special files are not exported through this workflow.

## Automatic snapshots and cleanup

Select **Automatic Snapshots** on either recovery page to configure that scope. System and Personal Files schedules are independent.

![Automatic snapshot and retention settings](images/automatic-snapshots.png)

You can configure:

- whether automatic snapshots are created;
- the freshness interval, from one to 24 hours;
- whether old snapshots are cleaned automatically;
- how long to keep every snapshot;
- daily, weekly, monthly, and yearly representatives.

If the computer is asleep or powered off when a snapshot was due, the scheduler creates at most one catch-up snapshot after the next start. Protected snapshots and snapshots involved in an active recovery transaction are not removed by automatic cleanup.

AnduinOS also creates a system snapshot before a real DPKG package transaction by default. Advanced Settings controls this behavior and its notifications.

## Snapshot protection, deletion, and size

A protected snapshot is kept until protection is removed. Other manual, automatic, and package-change snapshots can participate in automatic cleanup.

Btrfs snapshots share unchanged data. The apparent size of several snapshots must not be added together as though each were a complete independent copy. Open **Properties** when you need the size information available for one snapshot. Enabling Btrfs quota accounting can provide shared and exclusive subvolume sizes, but its initial scan may take time.

The storage bar at the bottom of the main window reports use of the Btrfs filesystem. If free space becomes low, remove unneeded unprotected snapshots or adjust retention instead of deleting files inside snapshot storage manually.

## File system information

Open the application menu and select **Information** to inspect the mounted root Btrfs filesystem.

![Btrfs file system information](images/filesystem-information.png)

The page explains physical data redundancy, duplicated file-system metadata, transparent compression, SSD discard behavior, quota accounting, and whether content-based deduplication is managed. These are live properties of the mounted filesystem, not generic recommendations for another disk.

## Disk health

The **Disk Health** tab reports physical drives backing the current root Btrfs filesystem. It summarizes S.M.A.R.T. health, temperature, power history, SSD endurance, NVMe warnings, data read and written, and relevant error counters when the device supplies them.

![System drive health](images/disk-health.png)

A healthy result means no important warning was reported at the time of the check. It does not replace backups or guarantee that a drive cannot fail. Unsupported, unavailable, and incomplete S.M.A.R.T. reports are shown separately rather than treated as healthy values.

## Choose the right recovery method

| Situation | Recommended method |
|---|---|
| A package or driver update broke the system | System snapshot rollback |
| A document was edited or deleted | Personal Files history |
| The internal disk failed or the computer was lost | Independent external or cloud backup |
| An Ext4 installation needs file protection | Deja Dup, cloud sync, or another backup tool |

See [Backup and Restore](../../../Install/Backup-And-Restore.md) for an independent backup strategy.
