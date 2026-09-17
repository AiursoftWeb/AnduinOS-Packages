# Virtual Memory Control

Virtual Memory Control shows how AnduinOS uses RAM, Zram, Zswap, and disk-backed Swap. It also manages supported virtual-memory settings and checks whether the system is ready for hibernation.

Open the application menu and search for **Virtual Memory Control**.

## Memory overview

The Dashboard separates active memory, cache, free RAM, compressed-memory devices, and disk-backed Swap. It also reports total RAM, memory type and speed when available, hibernation readiness, and the active swappiness value.

![Virtual Memory Control dashboard](images/memory-overview.png)

A green **Optimal memory configuration** result means the detected compression and disk-Swap arrangement is internally consistent. It does not mean that every workload will fit in memory, and it is not a disk-health check.

## Understand the three Swap mechanisms

- **Zram** creates a compressed block device in RAM. AnduinOS enables it by default for responsive desktop behavior under memory pressure.
- **Disk Swap** uses a partition or compatible Swap file as slower overflow storage.
- **Zswap** compresses pages in a RAM cache before writing them to an existing disk-backed Swap device.

Use Zram or Zswap, not both. Running both creates two compressed-memory layers and wastes memory and CPU.

## Configure disk-backed Swap

The **Swap** page distinguishes an installer-managed Swap partition from the legacy `/swapfile` used by older installations.

![Virtual memory and Swap configuration](images/swap-configuration.png)

### New installations

The AnduinOS 2.0.2 installer creates a dedicated Swap partition for both Btrfs and Ext4 installations. Virtual Memory Control displays its device, capacity, and current use, but does not resize it while the system is running. Online resizing could require moving the adjacent root partition and is not a safe ordinary settings operation.

The installer chooses the size according to available disk space and memory while preserving at least 20 GiB for the operating system. The target is capped at 64 GiB and never smaller than 2 GiB.

### Existing installations with `/swapfile`

Virtual Memory Control continues to support the supplementary `/swapfile` used by older AnduinOS installations on compatible filesystems. It can enable, disable, or resize that specific file and keeps `/etc/fstab` consistent.

On the default Btrfs layout, an additional Swap file inside the root subvolume is disabled because an active Swap file would prevent Disk Snapshots Manager from creating system recovery points. The dedicated installer-managed Swap partition does not have this conflict.

## Configure Zram

Open **Zram** to inspect existing compressed-memory devices or create a supported configuration. Changes are stored declaratively and applied by `anduinos-swap-config` at boot.

The AnduinOS default uses Zram and recommends `vm.swappiness=100`. With fast compressed RAM available, this value allows the kernel to move idle anonymous pages into Zram while retaining useful filesystem cache. It does not mean that the system immediately fills disk Swap.

## Configure Zswap

Enable Zswap only when an active disk-backed Swap partition or file exists. Zswap does not create Swap storage by itself. Disable Zram first, then configure Zswap's compression pool if that model better suits the workload.

## Hibernation status

The Dashboard reports hibernation as ready only when all required parts agree:

- the kernel supports suspend-to-disk;
- a resume target is configured;
- the target resolves to a real, active Swap device;
- the target has enough capacity for the installed RAM.

A large Swap device alone is not proof that hibernation is configured. If a legacy Swap file is the active resume target, resizing is blocked because changing the file could change its physical resume offset.

## Run a memory stress test

The **Stress Test** page can place controlled pressure on memory and report how the configured virtual-memory stack responds. Save work before running it, close memory-sensitive applications, and stop the test if the desktop becomes unresponsive.

The test is a diagnostic tool, not a benchmark and not a reason to increase Swap automatically.

## Command-line checks

```bash title="Show active Swap devices"
swapon --show
```

```bash title="Show RAM and Swap use"
free -h
```

```bash title="Show Zram devices and swappiness"
zramctl
sysctl vm.swappiness
```

For the configuration layers and manual overrides, see [Swap Control Strategy](../../../Skills/System-Management/Swap-Control-Strategy.md).
