# Dual Boot AnduinOS with Windows

Back up all important data and your BitLocker recovery key before changing
partitions or EFI boot settings. Although AnduinOS supports installation
alongside Windows, using a separate physical disk for AnduinOS is strongly
recommended. It is the safest and simplest configuration.

A same-disk installation requires more care. BitLocker and TPM measurements,
Windows Fast Startup and hibernation, Secure Boot, Windows maintenance, and
changes to EFI boot entries or boot order can all affect a dual-boot system.
Windows and AnduinOS must also share one partition table, so a partitioning
mistake can affect both systems.

!!! tip "Secure Boot Support"

    AnduinOS supports Secure Boot alongside Windows. Read the
    [Secure Boot Guide](./First-Boot-For-Secure-Boot.md) before installation
    so that you understand the MOK enrollment process.

## Two physical disks (strongly recommended)

Use one physical disk for Windows and a different physical disk for AnduinOS.
This keeps their operating-system and data partitions independent. AnduinOS
can create its own EFI System Partition on its target disk instead of placing
both systems' boot files in the same partition.

1. Back up your data and BitLocker recovery key.
2. Install Windows on the first disk. Leave the intended AnduinOS disk unused.
3. Fully shut down Windows. Do not restart from a hibernated or Fast Startup
   state.
4. Boot the AnduinOS installation media in UEFI mode.
5. Select only the dedicated AnduinOS disk and choose **Erase Disk**. Carefully
   verify its model, capacity, and device name before confirming—the selected
   disk will be erased.
6. After installation, select Windows or AnduinOS from GRUB or the firmware's
   boot menu.

Even with separate disks, firmware boot order and TPM measurements are
machine-wide. Windows may therefore request the BitLocker recovery key after
some firmware or bootloader changes. Keep that key available.

!!! warning "Install Windows first"

    Installing Windows after AnduinOS can change EFI boot entries or make
    Windows Boot Manager the first boot option. Installing Windows first
    avoids that additional recovery work.

## One physical disk

The guided coexistence procedure below preserves existing partitions and uses
only space that is **already unallocated**. It deliberately does not shrink or
move the Windows filesystem.

!!! note "Guided coexistence versus manual partitioning"

    This procedure follows the 2.0.2 guided installer. The [2.0.3 development installer](../Release-Notes/2.0.3.md) also implements a separate manual GPT editor with guarded shrinking of healthy, unencrypted NTFS partitions. That is not an automatic step in guided coexistence and remains subject to release qualification. Suspending BitLocker is not sufficient for installer-side NTFS shrinking: the volume must be fully decrypted. The steps below instead prepare free space in Windows.

1. Back up all important data and the BitLocker recovery key.
2. Finish pending Windows updates.
3. Disable Windows Fast Startup and hibernation. From an Administrator command
   prompt, you can disable hibernation with:

```powershell title="Windows Administrator terminal"
powercfg /h off
```

4. Suspend BitLocker protection before resizing partitions or changing the
   boot configuration. Do not proceed unless you can recover the Windows disk
   with its recovery key.
5. Open **Disk Management** in Windows, right-click the Windows partition
   (usually `C:`), and select **Shrink Volume**.
6. Reserve space for AnduinOS: **25 GiB minimum**, **50 GiB or more recommended**
   for normal use. Leave the result as **Unallocated**. Do not create or format
   a new Windows volume in that space.
7. Fully shut down Windows, then boot the AnduinOS installation media in UEFI
   mode.
8. Select the Windows disk, choose **Advanced: Install Alongside**, and select
   the prepared unallocated extent. Review every disk and partition shown on
   the confirmation page before starting installation.

### If the storage lists are disabled

If **Unallocated space** displays `(None)`, the installer has not found a safe
extent large enough for AnduinOS. The EFI System Partition list consequently
remains unavailable as well.

Return to Windows Disk Management and confirm that the space is genuinely
shown as **Unallocated**, rather than as a new partition or volume. Then fully
shut down Windows, return to the installer, and select **Rescan and Reselect
Disk**.

The guided coexistence path also requires UEFI boot, a GPT disk, stable
partition identities, and a supported storage layout. It will refuse to
continue if a target partition is mounted or if the disk uses an unsupported
mapper, array, or nested block-device layout. This refusal is intentional: the
installer does not provide a force-continue option around storage safety
checks.

If the lists remain unavailable, save the installer log and include it with a
screenshot of the complete Windows Disk Management layout when requesting
support.
