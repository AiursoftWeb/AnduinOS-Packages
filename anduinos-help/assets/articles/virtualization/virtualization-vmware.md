# Run AnduinOS in VMware

The AMD64 edition of AnduinOS 2.0.2 includes VMware guest integration in the Live image. A VMware virtual machine can therefore resize its desktop with the VMware window during the Live session and retain the integration after installation.

The integration is provided by `open-vm-tools-desktop`. It also supports VMware clipboard and drag-and-drop integration when those features are enabled by the VMware product and supported by the active desktop session.

## Install AnduinOS in a VMware virtual machine

1. Create a 64-bit Linux virtual machine and attach the AnduinOS AMD64 ISO.
2. Allocate enough memory, CPU, and virtual-disk capacity for a normal desktop installation.
3. Boot the ISO and confirm that the Live desktop can use more than the fallback `640 × 480` resolution.
4. Run **Install AnduinOS** and install to the virtual disk.
5. Remove the ISO and start the installed system.

The installer detects VMware and retains these guest packages in the installed system:

- `open-vm-tools-desktop`
- `open-vm-tools`
- `xserver-xorg-video-vmware`

The same packages are present temporarily in the AMD64 Live image but are removed from physical computers and other detected hypervisors. This keeps VMware-specific software off systems that do not need it. If virtualization detection is inconclusive, the installer keeps the packages rather than risking a broken VMware guest.

!!! note "ARM64 images"

    This automatic VMware payload is specific to the AMD64 ISO. The ARM64 ISO does not include the VMware guest package family.

## Check the installed integration

Run:

```bash title="Check VMware guest packages"
dpkg-query -W open-vm-tools open-vm-tools-desktop xserver-xorg-video-vmware
```

Check the service:

```bash title="Check VMware Tools service"
systemctl status open-vm-tools.service
```

The system should also identify the virtual machine as VMware:

```bash title="Detect the hypervisor"
systemd-detect-virt --vm
```

The expected result is `vmware`.

## Fix a desktop stuck at 640 × 480

First verify that the virtual machine is using the AMD64 ISO and that the guest packages are installed. If they are missing on an existing or manually modified system, install them with:

```bash title="Install VMware desktop integration"
sudo apt update
sudo apt install open-vm-tools-desktop
```

Then restart the virtual machine. Also check the VMware display settings and make sure automatic guest resizing is enabled in the host application.

Clipboard and drag-and-drop support can depend on the VMware product, its host-side settings, and whether the current Wayland session permits the requested integration. A working dynamic resolution does not guarantee that every optional host integration feature is available.
