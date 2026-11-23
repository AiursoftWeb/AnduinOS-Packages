# Install AnduinOS from USB

This guide follows the AnduinOS 2.0.2 installer from the Live USB to the first boot of the installed system.

!!! note "Newer installer storage features"

    The [2.0.3 development release](../Release-Notes/2.0.3.md) adds an expanded manual-storage workflow, including guarded NTFS shrinking. The screenshots and guided coexistence instructions here describe 2.0.2; they are not instructions for using the new manual editor.

Before starting, back up anything important on every disk you may modify. For the most predictable installation, give AnduinOS its own physical disk. Installing beside Windows on the same disk is an advanced operation; read [Dual Boot with Windows](./Dual-Boot-With-Windows.md) before selecting that route.

!!! tip "Before booting the USB"

    - Enable Secure Boot in UEFI firmware when your computer supports it. See the [Secure Boot Guide](./First-Boot-For-Secure-Boot.md).
    - Finish pending Windows updates and disable Windows Fast Startup before changing a Windows disk.
    - Back up the BitLocker recovery key if Windows uses BitLocker.
    - On a compatible NVMe SSD, you may optionally [change the NVMe LBA size to 4K](./Change-NVME-LBA-Size.md) before installation.
    - Connect the computer to reliable power.

## Boot the Live USB

1. Insert the AnduinOS USB drive.
2. Turn on the computer and immediately open its boot-device menu. Common keys include <kbd>F12</kbd>, <kbd>F11</kbd>, <kbd>Esc</kbd>, <kbd>F10</kbd>, or **Volume Down + Power**.
3. Select the UEFI entry for the USB drive when one is available.
4. In the AnduinOS boot menu, select a language and start AnduinOS.
5. When the Live desktop appears, open **Install AnduinOS**.

The language selected here controls the installer interface. It also becomes the starting point for the installed system's language, regional settings, and input-method selection.

![Welcome page of the AnduinOS Installer with its language selector](images/installer/language.png)

Choose the keyboard layout on the next page. Use the preview field to verify letters, punctuation, and any layout-specific keys before continuing.

## Review the Secure Boot recommendation

On a UEFI computer where Secure Boot is disabled, the installer explains its benefits before making any disk changes.

![Installer recommendation shown when UEFI Secure Boot is disabled](images/installer/secure-boot-recommendation.png)

- Select **Restart to UEFI Firmware Settings** if you want to enable it now.
- Select **Skip** to continue without Secure Boot.
- If Secure Boot is already enabled, this recommendation page is skipped.
- Legacy BIOS systems do not have Secure Boot and do not use this step.

Skipping the recommendation does not prevent installation. When Secure Boot is enabled, however, the first restart includes a one-time MOK enrollment. The complete procedure and enrollment code are documented in the [Secure Boot Guide](./First-Boot-For-Secure-Boot.md).

## Connect to the Internet, or continue offline

The installer can connect to Wi-Fi without leaving the installation window. A network connection is useful for package updates, language-specific input methods, multimedia support, and hardware-driver discovery.

![Installer network page with the offline installation option](images/installer/offline-network.png)

If no network is available, select **Continue Installation**. The base operating system remains installable offline. Features that require downloads are disabled and omitted from the final installation plan; they can be installed after the first boot.

When online, first make sure the package source is actually reachable, then choose any optional components you need:

![Optional updates, drivers, and multimedia selections](images/installer/updates-and-drivers.png)

- **Download and install system updates during installation** brings the installed system up to date before its first boot.
- **Install third-party drivers for this device** runs hardware detection and installs a recommended driver when one is available.
- **Install extended multimedia format support** adds support for additional legacy and specialist media formats.
- For languages that require an input method, the installer can download the matching input package. For Simplified Chinese, this is AnduinOS Rime.

These choices are optional. Driver detection can correctly finish without installing another package when the hardware needs no additional driver.

## Select the installation disk

The disk page lists storage devices that can be used as the installation target. Check the device name, model, capacity, and current contents carefully.

Minimum installation disk capacity: **25 GiB**. Recommended: **50 GiB or more**.

![AnduinOS Installer disk selection page](images/installer/select-disk.png)

!!! danger "The selected disk may be erased"

    Disk selection is not merely a destination-folder choice. The normal installation route repartitions the selected disk. Do not continue until the selected model and capacity identify the intended disk.

## Choose how AnduinOS uses the disk

For a dedicated AnduinOS disk, select an erase-and-install option and choose a filesystem:

- **Btrfs (recommended)** enables the Disk Snapshots Manager and separates system data, personal files, logs, snapshots, containers, and virtual-machine images into appropriate subvolumes.
- **Ext4 (classic)** provides a traditional Linux root filesystem. The installer removes the Btrfs snapshot manager because system rollback is not available on Ext4.

![Storage method selection with Btrfs, Ext4, and advanced coexistence choices](images/installer/storage-method.png)

The installer also creates the required boot and Swap layout. Swap size is selected for the target disk while preserving enough space for the operating system.

### Keep another operating system

The advanced coexistence route uses existing **unallocated space** and, on UEFI systems, a suitable EFI System Partition. It does not shrink Windows for you.

If **Unallocated space** or **EFI System Partition** displays **(None)**, the installer has not found a safe candidate. Go back instead of forcing the operation. Finish pending Windows maintenance, create genuine unallocated space using an appropriate partitioning tool, verify the EFI layout, and select **Rescan and Reselect Disk**. The detailed risks and preparation steps are in [Dual Boot with Windows](./Dual-Boot-With-Windows.md).

!!! warning "Prefer separate physical disks"

    Same-disk coexistence can be affected by BitLocker, Fast Startup, Windows feature updates, firmware boot-order changes, TPM measurements, and a shared EFI System Partition. A dedicated AnduinOS disk provides a clearer failure boundary and is strongly recommended.

## Create the user account

Enter your full name, username, account password, and computer name.

![User account and computer-name page](images/installer/user-account.png)

- The password cannot be blank.
- The installer validates the account before it creates the final installation plan.
- The computer name may contain ASCII letters, digits, and internal hyphens. It cannot begin or end with a hyphen and may contain at most 63 characters.
- Uppercase computer names are accepted and stored in lowercase. For example, `TT-VIEW-71` becomes `tt-view-71`.

## Choose advanced access options

The three advanced switches are independent, and **all three are off by default**:

![Advanced access options in the AnduinOS Installer](images/installer/advanced-options.png)

- **Run sudo commands without a password** removes password confirmation from administrative commands.
- **Log in to the desktop automatically** opens this user's desktop after startup without asking for the account password.
- **Allow SSH login with the account password** starts Secure Shell in the installed system and permits the new user to authenticate over the network.

!!! warning "Each switch reduces security"

    The screenshot demonstrates one switch in its enabled state; it does not show the defaults. Leave an option disabled unless you specifically need that behavior. SSH password login never enables root password login.

New AnduinOS 2.0.2 installations contain the SSH server so GNOME Settings can enable Secure Shell later without installing another package. If the installer switch remains off, SSH does not listen for connections after installation. See [Enable SSH](./Enable-SSH.md).

## Select the timezone

Choose your location on the map or search for a timezone. This controls the installed system's local time and regional clock behavior.

![Timezone selection page in the AnduinOS Installer](images/installer/timezone.png)

## Review the installation plan

The review page is the last checkpoint before destructive work begins. Read the selected disk, filesystem, partition layout, account policy, optional software, Secure Boot state, and bootloader target.

![Final installation plan for a Btrfs installation](images/installer/review-plan.png)

For Btrfs, the plan shows the system subvolumes that will be created. This makes the actual disk operation visible before you approve it.

Select **Install** only when the plan matches your intent. The installer treats this reviewed configuration as a fixed plan; it does not silently reinterpret form values after installation starts.

## Wait for installation to finish

Keep the computer powered on and do not remove the USB drive while the progress page is active.

![AnduinOS installation progress with individual installation steps](images/installer/installation-progress.png)

The left side shows the current installation step. **Output** displays detailed diagnostics, while **Discover AnduinOS** provides an introduction during the wait.

If installation fails, preserve the installer log before closing the window. The step name and detailed output are important when reporting the problem.

## Restart into the installed system

1. When the completion page appears, select the restart option.
2. Remove the USB drive when prompted.
3. Let the computer boot from the installed disk.
4. If Secure Boot was enabled during installation, complete the one-time [MOK enrollment](./First-Boot-For-Secure-Boot.md).
5. Sign in using the account created in the installer, unless automatic login was explicitly enabled.

Wi-Fi profiles created in the Live environment are migrated to the installed system, so a network used during installation should normally reconnect after the first boot.
