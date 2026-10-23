# Windows EXE Runner

Windows EXE Runner lets you open compatible Windows `.exe` programs and `.msi` installers from Files. It validates that the selected file is a real Windows executable, prepares a Bottles compatibility environment when needed, and then launches the program in a reusable bottle named **Default**.

This is a compatibility feature, not a Windows virtual machine. Some programs work well, some need manual Bottles configuration, and software that depends on Windows kernel drivers or unrestricted hardware access may not work correctly.

## Open a Windows program

Windows executables can display their embedded application icons in Files. Double-click a valid `.exe` or `.msi` file to open it with Windows EXE Runner.

![CPU-Z Windows executables displaying their embedded icons in Files](images/windows-exe-runner/executable-icons.png)

EXE Runner claims only Windows executable and MSI file types. It does not take over ordinary native Linux executables.

## Prefer a native application when available

Before preparing Windows compatibility, EXE Runner recognizes many popular installers and utilities that have a better native Linux equivalent. It explains why the Windows build may be unsuitable and offers the native application directly.

![EXE Runner recommending native CPU-X before forcing CPU-Z to run](images/windows-exe-runner/native-recommendation.png)

In this example, CPU-X is recommended because it provides similar hardware information without a Windows compatibility layer. Select **Get CPU-X** for the native option, **Cancel** to stop, or **Force Run Anyway** to continue with CPU-Z.

Native recommendations are guidance rather than a claim that the downloaded file is malicious. They are especially useful for system monitors, disk-writing tools, development environments, web browsers, and other software that integrates deeply with the operating system.

## Prepare the compatibility environment

On the first ordinary launch, EXE Runner checks for Bottles and the **Default** bottle. If either is missing, it asks before downloading anything.

![First-launch prompt to install and configure the Bottles environment](images/windows-exe-runner/environment-required.png)

Select **Install and Configure** to let EXE Runner:

1. add Flathub when it is not already configured;
2. install Bottles as a system Flatpak;
3. download the Bottles runner and compatibility components;
4. create the **Default** application bottle; and
5. install common and CJK fonts for Windows applications.

This initial setup requires an Internet connection and can take several minutes. Later launches reuse the prepared environment.

![EXE Runner installing fonts in the new compatibility environment](images/windows-exe-runner/configuring-fonts.png)

Expand **Advanced Status and Logs** when you need the exact setup command or an error message.

## Start the program

Once the bottle is ready, EXE Runner passes the selected file to Bottles and displays launch progress.

![EXE Runner starting a Windows application through Bottles](images/windows-exe-runner/starting-program.png)

The runner window closes automatically after the Windows process has remained active. The Windows application continues running on the desktop.

![CPU-Z running on the AnduinOS desktop through the compatibility layer](images/windows-exe-runner/cpu-z-running.png)

The CPU-Z example demonstrates that the application can start and render. Values that require a Windows kernel driver, direct motherboard access, or vendor-specific services can still be missing or inaccurate. Use a native tool such as CPU-X or Mission Center when accurate hardware telemetry matters.

## Security and compatibility

!!! warning "Treat Windows downloads as executable code"

    A Windows program can still read or modify files exposed to its compatibility environment, use the network, and act with your user account's permissions. Bottles and Wine are not a security boundary equivalent to a disposable virtual machine. Run software only from sources you trust.

EXE Runner rejects a renamed or malformed file whose contents do not match a Windows PE executable or MSI package. This prevents accidental dispatch of unrelated files, but it does not determine whether a valid Windows program is safe.

Programs are least likely to work when they require:

- Windows kernel drivers;
- anti-cheat or anti-tamper drivers;
- direct disk-writing or firmware access;
- low-level hardware monitoring;
- a Windows hypervisor; or
- tightly integrated Microsoft system services.

For games and complex applications, open Bottles directly and create a dedicated bottle instead of placing everything in **Default**. For complete Windows compatibility and stronger isolation, use a virtual machine.

## Troubleshooting

### Double-clicking does nothing

Check that the file ends in `.exe` or `.msi` and is a valid Windows executable. A web download containing an HTML error page with an `.exe` filename is intentionally rejected.

To verify the detected file type:

```bash
file /path/to/program.exe
```

### Bottles setup fails

Confirm that the computer can reach Flathub, retry the launch, and expand **Advanced Status and Logs**. You can also open the App Store and install Bottles manually before launching the file again.

### The program starts but cannot see hardware

Use the suggested native application or run the software in Windows. A compatibility layer cannot supply a missing Windows kernel driver.

### I need manual Wine configuration

For a command-line Wine installation and `winecfg`, see [Run Windows Apps with Wine](../../../Skills/Sandboxing/Run-Windows-Apps.md).
