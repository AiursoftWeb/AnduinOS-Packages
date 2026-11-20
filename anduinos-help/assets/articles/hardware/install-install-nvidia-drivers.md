# Installing NVIDIA Drivers on AnduinOS

This guide provides a comprehensive approach to installing proprietary NVIDIA drivers on **AnduinOS**. By using NVIDIA's proprietary drivers, you can achieve better performance and gain access to features not available through the open-source Nouveau driver.

---

## Prerequisites

- **AnduinOS 2.0**.
- A compatible NVIDIA GPU.
- Basic knowledge of using the terminal.
- Internet connection to download drivers and dependencies.
- Backup your system or important data before proceeding with driver installations.

## (Recommended) Using Driver Center

For most users, the easiest way to install the recommended NVIDIA driver is the built-in **Driver Center**:

1. Open **Driver Center** from the application menu.
2. Open **Graphics**.
3. Select the version marked **Recommended for this device**.
4. Apply the change. Driver Center downloads the packaged driver selected by Ubuntu's hardware detection.
5. **Reboot** your system to apply the changes.

![NVIDIA driver selection in Driver Center](../Applications/System/Driver-Center/images/driver-center-graphics.png)

!!! note "How does module signing work under the hood?"

    For a detailed explanation of how the MOK certificate, DKMS, and the OOBE state machine work together — and why NVIDIA drivers automatically benefit from the AnduinOS Secure Boot infrastructure — see the [Secure Boot Signing Architecture](./Secure-Boot-Signing-Architecture.md) reference.

## (Alternative) Automatic CLI Installation

No graphical session is required. On SSH-only servers, you can detect and install a packaged driver from the terminal. Arrange console access before rebooting if Secure Boot enrollment is needed:

```bash
sudo apt update
ubuntu-drivers devices
sudo ubuntu-drivers install
```

1. **Update repositories**: This ensures you have the latest package lists.
2. **Install drivers**: The `ubuntu-drivers install` command automatically detects your NVIDIA GPU and installs the recommended driver.
3. **Reboot** your system to apply the changes.

!!! note "Not Always the Latest Driver"

    The automatically installed driver might **not** be the latest available from NVIDIA. Check hardware and kernel compatibility before changing branches. If the packaged choices do not meet a specific requirement, the PPA and manual installation paths below remain available.

!!! warning "Known Issues and Stability"

    NVIDIA proprietary drivers can sometimes introduce regressions or stability issues, especially with Wayland or very recent kernels. If you encounter issues, please check the [NVIDIA driver release notes](https://www.nvidia.com/en-us/drivers/unix/) or report them to the [NVIDIA Linux Developer Forum](https://forums.developer.nvidia.com/c/gpu-graphics/linux/148).

    If you experience visual glitches or crashes, you can try:

    - Falling back to an older, more stable driver version.
    - Checking the release notes for known issues affecting your GPU, kernel, or Wayland session.

    AnduinOS 2.0 is Wayland-only and does not provide an Xorg session as a fallback.

    To list all available NVIDIA driver versions for your hardware, use:

    ```bash title="List available NVIDIA driver versions"
    ubuntu-drivers devices
    ```

---

## Headless compute servers

For compute-oriented driver choices, use `ubuntu-drivers list --gpgpu` and
`sudo ubuntu-drivers install --gpgpu` instead of assuming desktop driver
selection is appropriate. Inspect the proposed packages for your workload.
See [Ubuntu's server driver guidance](https://ubuntu.com/server/docs/nvidia-drivers-installation/).
No desktop application is required for these commands.

## PPA Installation (Optional third-party source)

If you don't want to use the automatic installation method or need a specific version of the NVIDIA driver, you can use a PPA (Personal Package Archive) to install the drivers. This method is useful for getting newer drivers that may not yet be available in the default repositories.

To add the graphics-drivers PPA, run the following commands:

```bash title="Add the graphics-drivers PPA"
sudo add-apt-repository ppa:graphics-drivers/ppa
sudo apt update
```

Check that the PPA supports your Ubuntu base release and architecture. Adding it changes the trust and maintenance boundary; it is not required for terminal installation. List the available choices with `ubuntu-drivers devices`, then install the exact package you selected:

```bash title="Install NVIDIA driver from PPA"
read -r -p 'Exact package name from ubuntu-drivers devices: ' nvidia_package
apt-cache policy "$nvidia_package"
sudo apt install "$nvidia_package"
```

Then reboot your system:

```bash
sudo reboot
```

Review the proposed package changes before confirming. A newer PPA driver is not a guarantee of stability or compatibility.

---

## Manual Installation (For advanced Users)

The manual installation method allows you to install specific (often newer) NVIDIA drivers, especially if you need features not yet packaged in the default AnduinOS/Ubuntu repositories.

### Step 1: Identify the existing installation

Do not combine APT-managed NVIDIA drivers and a `.run` installation. Inspect
the existing packages before deciding which installation method to keep:

```bash
dpkg-query -W 'nvidia-*' 'libnvidia-*' 'linux-modules-nvidia-*'
dkms status
```

Missing-package messages are normal when no matching packages are installed.
If changing from APT to `.run`, identify the exact conflicting packages and
review an APT removal simulation before removing them. Do not use wildcard
purges or automatic `autoremove` as a generic cleanup procedure: these can
remove CUDA, graphics and application dependencies.

For a previous `.run` installation, use its supplied `nvidia-uninstall`
procedure when switching installation methods. Download the replacement
installer and arrange recovery access before removing a working driver.

### Step 2: Download the NVIDIA Driver

1. **Visit the [NVIDIA Drivers Website](https://www.nvidia.com/en-us/drivers/)**.
2. **Locate the correct driver**:
   - Select your GPU model (e.g., GeForce RTX 4090, GTX 1080, etc.).
   - Select the correct architecture and a driver branch supporting your GPU and kernel.
   - Click "Search" and download the appropriate `.run` file.
3. **Make the file executable**. The filename below is an example, not a recommended version; replace it everywhere with your actual download:

   ```bash
   chmod +x NVIDIA-Linux-x86_64-565.77.run
   ```

!!! note "How to Choose the Correct Driver"

    - **Long Lived Branch (Production Branch)**: Often more stable and tested.
    - **Short Lived Branch**: Provides the latest features and improvements but may be less tested.
    - **Legacy Drivers**: Required for older GPUs that no longer receive current driver support.

---

### Step 3: Prepare Keys for Secure Boot

Secure Boot ensures your system only loads drivers or kernel modules signed by a trusted key. Keep **Secure Boot enabled** in your BIOS/UEFI and sign the NVIDIA driver module with the AnduinOS Machine Owner Key (MOK).

First run `mokutil --sb-state`. Traditional BIOS does not support Secure Boot. If signing is required, verify that `/var/lib/shim-signed/mok/MOK.der` and the corresponding private key `MOK.priv` exist. AnduinOS prepares them during its Secure Boot setup, but they are not guaranteed to exist on every server or installation. Never overwrite an existing key pair blindly.

If the pair is missing, follow the [Secure Boot signing architecture](./Secure-Boot-Signing-Architecture.md) before continuing. MOK enrollment takes place at boot: SSH alone cannot complete it; arrange physical, BMC or remote-console access. Signing and enrollment are separate requirements.

1. **Verify if your key is already enrolled**:

   If you already completed the First Boot setup (Welcome Center) and enrolled the Secure Boot certificate, you are good to go. You can verify it by running:

   ```bash
   sudo mokutil --test-key /var/lib/shim-signed/mok/MOK.der
   ```

   If it says `is already enrolled`, you can skip to **Step 4**.

2. **Enroll your key (if not already enrolled)**:

   If the key is not enrolled, open the Welcome Center's **Secure Boot Configuration** page and follow the displayed enrollment action. See the [Secure Boot Guide](./First-Boot-For-Secure-Boot.md) for the complete procedure. Advanced users can also queue the existing certificate for enrollment manually:

   ```bash
   sudo mokutil --import /var/lib/shim-signed/mok/MOK.der
   ```

   You will be prompted to create a password. **Remember this password**, as you will use it after the reboot.

3. **Reboot and enroll your key**:

   ```bash
   sudo reboot
   ```

   - During the boot, **MokManager** appears (a blue screen).
   - Select **"Enroll MOK"**, then **"Continue"**.
   - Enter the password you set previously.
   - Confirm to enroll the key and reboot again.

   Run the `mokutil --test-key` command again. If it reports `is already enrolled`, Secure Boot now trusts modules signed with the corresponding private key.

!!! note "Keep Your Keys Safe"

    - **Never share your private key (`MOK.priv`)** with others.
    - Always keep these files in a secure place. If you lose them, you’ll need to re-sign or re-enroll future kernel modules.

---

### Step 4: Check Nouveau and the initramfs

This step concerns the manual installer, not a blanket prerequisite for APT.
Check whether Nouveau is loaded with `lsmod | grep nouveau`. If it must be
disabled for your installation, edit a dedicated file:

```bash
sudoedit /etc/modprobe.d/blacklist-nouveau.conf
```

Use only the relevant settings:

```text
blacklist nouveau
options nouveau modeset=0
```

Do not blacklist unrelated framebuffer or hardware-error-detection modules.
Regenerate the initramfs using the toolchain already installed on that system.
On the current Dracut-based AnduinOS installation:

```bash
sudo dracut --regenerate-all --force
```

Only on an older installation still maintained by `initramfs-tools`, use
`sudo update-initramfs -u -k all` instead. Do not install or switch toolchains
just to run this command. Check that regeneration succeeds and that you have
console recovery access before rebooting. Reboot and verify Nouveau is no
longer loaded before running the manual installer.

### Step 5: Prepare to Install the NVIDIA Driver

1. **Install necessary build dependencies**:

   ```bash
   sudo apt update
   sudo apt install gcc make build-essential dkms linux-headers-$(uname -r)
   ```

   This ensures that you can compile the NVIDIA kernel module correctly.

2. **Stop GPU users before installation**:

   Stop GPU compute jobs, containers, persistence services and graphical
   sessions that use the driver. Save work and verify SSH/console access first.
   Do not stop networking or SSH to perform this step.

   If a graphical display manager is active, stop it from a console or SSH:

   ```bash
   systemctl is-active display-manager.service
   sudo systemctl stop display-manager.service
   ```

   On a headless server there may be no display manager; skip that operation.
   Do not change the default boot target or force a headless machine into a
   graphical target. Record which services you stopped so they can be restored
   after installation. Follow the installer's checks for modules still in use.

---

### Step 6: Install the NVIDIA Driver

1. **Navigate to the directory containing the downloaded driver**:

   ```bash
   cd ~/Downloads
   ```

2. **Run the installer** (example filename below):

   ```bash
   sudo ./NVIDIA-Linux-x86_64-565.77.run
   ```

3. **Follow the on-screen prompts**:
   - **License Agreement**: Accept the license.
   - **Installation Path**: The default path is usually fine.
   - **32-bit Compatibility Libraries**: 
     - **Recommended** to install if you run software such as Steam, Wine, or certain games requiring 32-bit libraries.
     - **Not necessary** if you only run 64-bit applications.
   - **Signing the module**:
     - The installer will ask if you want to sign the kernel module. Select **Yes**.
     - Provide the **absolute path** to your **private key**: `/var/lib/shim-signed/mok/MOK.priv`
     - Provide the **absolute path** to your **public certificate**: `/var/lib/shim-signed/mok/MOK.der`
     - Alternatively, use the installer's `--module-signing-secret-key` and `--module-signing-public-key` options with these existing paths. Check `--advanced-options` for your downloaded version; never put the private key contents in a command or report.

!!! warning "Provide the Correct Key"

    Installing the driver will automatically sign the kernel module with your private key if configured correctly.

---

### Step 7: Reboot and Validate Installation

1. **Reboot** your system:

   ```bash
   sudo reboot
   ```

2. **Check driver status**:

   ```bash
   nvidia-smi
   ```

   If the driver is installed correctly, you should see a table showing your GPU, driver version, and other details.

3. **Secure Boot Verification**:

   ```bash
   sudo mokutil --sb-state
   ```

   - If it shows `SecureBoot enabled`, Secure Boot is active. If `nvidia-smi` still fails, follow **Troubleshooting point 3** to verify the enrolled MOK and repair module signing.

!!! note "Kernel Updates"

    **When your kernel updates**, you may need to recompile or re-sign the NVIDIA driver module. If you installed via a .run file, you may have to rerun the installer or rely on DKMS (if configured properly).

---

### Step 8: Configure Displays on Wayland

This step is only for graphical desktop installations. Skip it on a headless or SSH-only server. AnduinOS 2.0's desktop supports **Wayland only**; it does not offer an Xorg session.

**Check your current session type**:

```bash
echo $XDG_SESSION_TYPE
```

Run this inside the graphical session, where the expected result is `wayland`. An empty value or `tty` over SSH is normal and is not a driver failure.

**Adjust your display configuration**:

Open **Settings → Displays** to select the resolution, refresh rate, scale, orientation, and monitor arrangement. These system settings are the supported way to configure displays in the Wayland session.

---

### Step 9: (Optional) Install NVIDIA Docker Toolkit

If you use **Docker** and want to take advantage of your NVIDIA GPU inside containers (for machine learning, AI workloads, or GPU-accelerated applications), install the **NVIDIA Container Toolkit**. Please refer to the [Docker NVIDIA Container Toolkit documentation](../Applications/Development/Docker/Docker.md) for detailed steps.

---

## Troubleshooting

### 0. First Diagnostic: Check Driver Status

Run `nvidia-smi`, `uname -r` and `sudo journalctl -b -k --no-pager`.
A working management interface does not prove every graphics or compute
workload is healthy. A failure can mean a missing module, an untrusted
signature, a library/module version mismatch or another device problem.
Inspect the actual error rather than assuming a single cause.

### 1. Black screen or system freeze after reboot

Use an available console or a known-working boot entry to inspect the logs.
A black screen does not by itself establish a Nouveau conflict. Do not purge
every NVIDIA package or assume Nouveau will automatically work after removal.
Identify whether the installation is APT-managed or manual, then repair or
remove the specific installation using its own tooling.

If you added a Nouveau blacklist in Step 4 and later undo that installation,
review that file and regenerate the initramfs with the installed toolchain;
otherwise Nouveau may remain disabled. Keep another boot/recovery route.

### 2. Driver mismatch (nvidia-smi fails)

Check the running kernel, installed driver package version and `dkms status`.
For a DKMS build, matching headers must be available for the target kernel.
Prebuilt module packages may not appear in DKMS at all. Do not assume headers
or a generic `dpkg-reconfigure` command will fix every installation.

For a `.run` driver, inspect its installer/DKMS logs and verify compatibility
with the new kernel before rebuilding. An old loaded module after an upgrade
may simply require a planned reboot. Check signatures as described below.

### 3. Secure Boot issues

Secure Boot is a UEFI feature that prevents untrusted code from running at boot. If the NVIDIA driver is installed but its kernel modules do not load, the modules may not be signed with the enrolled AnduinOS MOK, or the AnduinOS MOK may not yet be enrolled.

Keep Secure Boot enabled. Open **Driver Center**, select **Secure Boot**, and follow the displayed action to create and enroll the certificate or repair the signing configuration. Reboot when prompted. In the blue **MOKManager** screen, use the code associated with the enrollment request (`123456` for the documented AnduinOS 2.0.2 managed flow; a manual `mokutil --import` uses the password you chose), then boot back into AnduinOS.

On an SSH-only system, the manual `mokutil` procedure in Step 3 remains available; firmware enrollment still requires console access. On UEFI with Secure Boot enabled, verify the trust chain and driver after rebooting:

```bash
sudo mokutil --sb-state
sudo mokutil --test-key /var/lib/shim-signed/mok/MOK.der
nvidia-smi
```

The first command should report `SecureBoot enabled`, the second should report that the certificate is already enrolled, and `nvidia-smi` should display your GPU and driver details. If one of these checks fails, follow the [Secure Boot Guide](./First-Boot-For-Secure-Boot.md) for the enrollment and recovery procedure. For details about how the AnduinOS MOK, DKMS, and NVIDIA modules fit together, see [Secure Boot Signing Architecture](./Secure-Boot-Signing-Architecture.md).

### 4. Updates break the driver

A manually installed driver needs modules built for each target kernel. A
`.run` installation registered with DKMS may rebuild automatically; without
that setup, rebuilding or reinstalling for the new kernel may be necessary.
Neither DKMS nor packaged drivers guarantee compatibility with every new
kernel. Verify build success and signature trust before rebooting a remote
machine. Keep a known-working kernel until the new one is validated.

See [NVIDIA's installer documentation](https://download.nvidia.com/XFree86/Linux-x86_64/580.95.05/README/installdriver.html)
for the supported DKMS and signing options, and consult the README accompanying
your chosen driver version.

### 5. Laptop animations are laggy

Do not assume every slowdown is caused by PRIME on-demand mode or that forcing
the discrete GPU will fix it. Check driver errors, load, power settings and the
actual graphics topology first. Integrated graphics are not inherently too slow
for the desktop.

If the installed hardware and driver support PRIME profiles, inspect
`prime-select query` and the available profile controls before changing them.
Record the previous profile so you can restore it. Discrete-GPU mode can
increase power consumption and may not be supported by every laptop.
This desktop-only troubleshooting does not apply to headless compute servers.

## Conclusion

You have now installed the NVIDIA driver on **AnduinOS** (Ubuntu-based) either by using the **automatic** method (recommended for most users) or via the **manual** method (for more specific or newer versions of the driver). Properly installed and configured drivers can significantly improve graphics performance and unlock advanced GPU features. Always remember to keep your drivers and system updated, and ensure that you have a **backup** or **snapshot** strategy when making significant changes to the system (like updating drivers or switching kernels).

Enjoy your enhanced GPU performance!
