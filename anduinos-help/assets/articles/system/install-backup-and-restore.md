# Backup and Restore

It is crucial to back up your data regularly to prevent data loss. AnduinOS supports both modern graphical tools for effortless backups and powerful command-line utilities for advanced users.

## Local recovery with Disk Snapshots Manager

New AnduinOS installations using Btrfs include **Disk Snapshots Manager**. It can roll the operating system back after a broken update and recover earlier versions of files from independent Personal Files snapshots.

![System snapshots in Disk Snapshots Manager](../Applications/System/Disk-Snapshots-Manager/images/system-recovery.png)

See the complete [Disk Snapshots Manager guide](../Applications/System/Disk-Snapshots-Manager/Disk-Snapshots-Manager.md) for system rollback, Personal Files history, automatic snapshots, retention, and disk-health checks.

!!! warning "A local snapshot is not an independent backup"

    Btrfs snapshots normally share the same physical disk as the current system. If that disk fails or the computer is lost, both the live files and snapshots may disappear. Use the methods below to keep another copy on external storage or a remote service.

## Independent graphical backups

Use an external disk or remote service for protection that survives failure of the system drive.

### 1. Cloud Sync via Online Accounts

You can seamlessly sync your files, contacts, and calendar with cloud providers without installing extra software.

1. Open **Settings** -> **Online Accounts**.
2. Select your provider (Google, Microsoft, or Nextcloud) and sign in.
3. Your cloud storage will automatically appear in the File Manager, allowing you to drag and drop files directly.

### 2. Automated Backups with Deja Dup

For comprehensive backups (including automatic scheduling and encryption), we recommend **Deja Dup**.

![Deja Dup Backups](images/deja-dup-backups.png)

1. Open **Software** and search for `Deja Dup` (or click "Get Deja Dup" in the Welcome Center).
2. Install and launch the **Backups** app.
3. Follow the setup wizard to:
   - Select which folders to back up (defaults to your Home folder).
   - Choose a destination (an external USB drive, Google Drive, or a network location).
   - Enable automatic scheduling so backups happen silently in the background.

---

## Command Line (For Power Users)

If you prefer terminal-based workflows, you can use built-in Linux commands to manually manage backups.

### Locate the backup device

Before backing up your data, you should add an external backup device. This can be an external hard drive, a USB flash drive, or a network-attached storage (NAS) device. 

If you attached a new USB flash drive to your computer, you can find the device name by running:

```bash
sudo fdisk -l
```

The device name will be something like `/dev/sdb1`. You can use this device name to mount the USB flash drive. For example, to mount the USB flash drive to the `/mnt/backup` directory:

```bash
sudo mkdir -p /mnt/backup
sudo mount /dev/sdb1 /mnt/backup
```

You can verify that the USB flash drive is mounted by running:

```bash
cd /mnt/backup
df . -Th
```

### Backup home directory

To backup your home directory, you can use the `rsync` command. The following command will sync your home directory to the backup directory:

```bash
sudo rsync -Aavx --update --delete /home/$USER /mnt/backup
```

This command is cumulative, and incremental. You can run this command multiple times, and it will only copy the changes since the last run.

To restore your home directory from the backup, you can run with reverse source and destination:

```bash
sudo rsync -Aavx --update --delete /mnt/backup/$USER /home
```

### Backup dconf

`dconf` is a low-level configuration system that is used by the GNOME desktop environment. It contains data including wallpapers, themes, extensions, and application settings.

To backup your dconf settings, you can use the `dconf dump` command:

```bash
dconf dump / > /mnt/backup/dconf-settings
```

To restore your dconf settings from the backup:

```bash
dconf load / < /mnt/backup/dconf-settings
```

### Backup /etc directory

The `/etc` directory contains system-wide configuration files. To backup the `/etc` directory, you can use the `tar` command:

```bash
sudo tar -czvf /mnt/backup/etc-backup.tar.gz /etc
```

To restore the `/etc` directory from the backup:

```bash
sudo tar -xzvf /mnt/backup/etc-backup.tar.gz -C /
```

### Automate backup

You can automate the backup process by creating a cron job. For example, to backup your home directory every day at 2:00 AM, you can run:

```bash title="Setup automatic backup at 2:00 AM"
echo "
#!/bin/bash
DEVICE=/dev/sda1
if [ -e \$DEVICE ]; then
    sudo mkdir -p /mnt/backup
    sudo mount \$DEVICE /mnt/backup
    
    sudo rsync -Aavx --delete --update /home/$USER /mnt/backup/
    sudo umount /mnt/backup
else
    echo \"No \$DEVICE, skipping backup...\"
    DATE=\$(date +'%Y-%m-%d-%H-%M-%S')
    echo \"On \$DATE, no \$DEVICE, backup failed\" | sudo tee -a /etc/motd
    exit 1
fi
" | sudo tee /usr/local/bin/backup.sh
sudo chmod +x /usr/local/bin/backup.sh
(crontab -l ; echo "0 2 * * * /usr/local/bin/backup.sh") | crontab -
```
