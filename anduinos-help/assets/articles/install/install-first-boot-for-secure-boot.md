# Secure Boot Guide

AnduinOS supports UEFI Secure Boot while still allowing packaged third-party kernel modules, such as NVIDIA, VirtualBox, and the optional Xbox controller driver, to load.

The installer creates a certificate owned by the newly installed computer. On the first restart, you confirm that certificate in the firmware-level MOKManager screen. This is a one-time physical-presence step; it does not disable Secure Boot.

!!! important "The enrollment code is 123456"

    AnduinOS 2.0.2 uses the one-time MOK enrollment code `123456`. You do not create this code in the installer, and it is not your user password or disk-encryption password.

## Before installation

1. Open the computer's UEFI settings. The setup key is commonly `F2`, `F10`, `Del`, or a model-specific button.
2. Find **Secure Boot** under the **Security** or **Boot** section.
3. Enable Secure Boot. If the firmware asks for a mode, use **Standard** or **Windows UEFI mode**, not a custom-key mode.
4. Save the settings and boot the AnduinOS USB drive in UEFI mode.

The installer detects the running firmware state. When Secure Boot is enabled, it prepares the installed system before changing the firmware enrollment queue: it verifies the signed boot packages, creates a machine-local MOK certificate, signs relevant DKMS modules, validates the signatures, and then requests enrollment.

When Secure Boot is disabled or unsupported, installation can continue without MOK enrollment. You will not be sent to MOKManager on the next boot.

## First restart: enroll the certificate

After an installation performed with Secure Boot enabled, the first restart opens a blue **MOKManager** screen before AnduinOS starts.

1. Select **Enroll MOK**.

![Select Enroll MOK in MOKManager](images/moq-manager-enroll.png)

2. Select **Continue**, then select **Yes** to confirm the certificate.

![Confirm the MOK enrollment](images/sure-enroll-mok-key.png)

3. Enter `123456`. MOKManager normally uses a US keyboard layout.
4. Select **Reboot**.

The following boot should enter the installed AnduinOS desktop with Secure Boot still enabled.

!!! warning "Do not skip enrollment when you use third-party modules"

    AnduinOS itself can usually boot before the local certificate is enrolled because the standard boot chain is already signed. However, the kernel will reject third-party modules that are not signed by a trusted key. NVIDIA, VirtualBox, the optional Xbox controller driver, and other DKMS-based features may therefore fail until enrollment is complete.

## Verify the result in Driver Center

Open **Driver Center**, then select **Secure Boot**. A fully configured system displays **System Trust Established** and four green checks:

- Secure Boot is enabled.
- The local MOK certificate exists.
- The certificate is trusted by the motherboard.
- Installed third-party kernel modules are signed correctly.

![Secure Boot status in Driver Center](../Applications/System/Driver-Center/images/driver-center-secure-boot.png)

You can also verify the firmware state from a terminal:

```bash title="Check Secure Boot status"
mokutil --sb-state
```

The expected result is `SecureBoot enabled`.

## If enrollment was skipped or did not finish

Open **Driver Center** and select **Secure Boot**. The page distinguishes between a missing certificate, an enrollment that is waiting for the next restart, and modules that need to be signed again.

- If no local certificate exists, select **Create & Enroll Certificate**.
- If enrollment is pending, select **Reboot & Configure Secure Boot**.
- In MOKManager, follow **Enroll MOK** → **Continue** → **Yes** and enter `123456`.
- After returning to AnduinOS, reopen Driver Center and confirm that all four checks are green.

The Welcome to AnduinOS application uses the same Secure Boot component and can also guide first-boot enrollment. Driver Center is the normal place to inspect or repair the configuration later; both interfaces operate on the same machine-local certificate.

## If Secure Boot is shown as disabled

Driver Center cannot enable a motherboard setting from inside the operating system. Reopen the UEFI settings, enable Secure Boot, and boot AnduinOS again. If the firmware offers **Setup Mode**, **Custom Keys**, or **Restore Factory Keys**, consult the computer manufacturer's instructions before changing the key database.

Enabling Secure Boot after AnduinOS has already been installed may require creating and enrolling the local certificate from Driver Center before third-party modules can load.

## Command-line certificate check

Advanced users can check whether the current machine certificate is enrolled:

```bash title="Check the AnduinOS MOK certificate"
sudo mokutil --test-key /var/lib/shim-signed/mok/MOK.der
```

The command should report that the certificate is already enrolled. Keep `/var/lib/shim-signed/mok/MOK.priv` private; it is the local signing key used for modules on this computer.

For implementation details, see the [Secure Boot Signing Architecture](./Secure-Boot-Signing-Architecture.md).
