# Manage Swap

AnduinOS uses a combination of compressed RAM and disk-backed Swap to remain responsive when physical memory is under pressure. New AnduinOS 2.0.2 installations already have a managed configuration; most users do not need to create a Swap file manually.

## Use Virtual Memory Control

Open **Virtual Memory Control** from the application menu. Its Dashboard reports RAM, Zram, Zswap, disk Swap, hibernation readiness, and swappiness without requiring terminal commands.

![Virtual Memory Control dashboard](../Applications/System/Virtual-Memory-Control/images/memory-overview.png)

The application can manage Zram and Zswap, show the dedicated Swap partition created by the installer, and retain compatibility with `/swapfile` on older installations.

See the complete [Virtual Memory Control guide](../Applications/System/Virtual-Memory-Control/Virtual-Memory-Control.md) before changing the configuration.

## Check the current configuration

```bash title="Show active Swap devices"
swapon --show
```

```bash title="Show memory and Swap totals"
free -h
```

```bash title="Show Zram devices"
zramctl
```

On a new installation, expect a dedicated disk-Swap partition and a Zram device. Zswap is normally disabled because Zram and Zswap should not be active together.

## When disabling Swap may be appropriate

Most desktop systems should keep the default enabled. A specialized database, Kubernetes node, or latency-sensitive appliance may require a different policy documented by that workload.

The following command disables active Swap only until the configuration is reapplied or the computer restarts:

```bash title="Temporarily disable active Swap"
sudo swapoff -a
```

!!! warning "Do not disable a hibernation resume target casually"

    Hibernation depends on a configured Swap target with sufficient capacity. Virtual Memory Control identifies this relationship and warns or blocks unsafe changes that would invalidate a Swap-file resume offset.

For advanced `sysctl.d`, Zram service, Zswap, and rollback details, see [Swap Control Strategy](../Skills/System-Management/Swap-Control-Strategy.md).
