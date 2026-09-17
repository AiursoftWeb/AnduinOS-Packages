# Run Windows Apps on Linux

For the normal desktop workflow, first try [Windows EXE Runner](../../Applications/System/Windows-EXE-Runner/Windows-EXE-Runner.md). It opens supported `.exe` and `.msi` files through Bottles, prepares the compatibility environment graphically, and recommends native AnduinOS applications when they are a better fit.

The manual process below is intended for users who specifically need a system Wine installation or direct access to `winecfg`. Wine is a compatibility layer rather than a Windows virtual machine, and compatibility varies by application.

## Install Wine

To install Wine on AnduinOS, run the following commands:

To install Wine with 64-bit support only:

```bash title="Install Wine"
sudo apt update
sudo apt install wine64
```

To install Wine with both 64-bit and 32-bit support (recommended):

```bash title="Install Wine"
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install wine64 wine32
```

## Setup Wine

After installing Wine, you can configure it by running the following command:

```bash title="Configure Wine"
winecfg
```

This command will open the Wine configuration window, where you can set up various options, such as Windows version compatibility, audio settings, and drive mappings.

## Run Windows Apps

To run a Windows app using Wine, you can use the following command:

```bash title="Run Windows App"
wine /path/to/windows-app.exe
```

Replace `/path/to/windows-app.exe` with the actual path to the Windows application executable file.
