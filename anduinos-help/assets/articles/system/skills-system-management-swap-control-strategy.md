# Swap Control Strategy

AnduinOS 2.0.2 uses a **Zram-first, layered-priority** Swap architecture and creates a dedicated disk-Swap partition on new installations. Multiple packages contribute `sysctl.d` settings, and later applicable files override earlier values. This article covers manual inspection and advanced overrides. For the supported graphical workflow, see [Virtual Memory Control](../../Applications/System/Virtual-Memory-Control/Virtual-Memory-Control.md).

## Why Zram?

Traditional swap writes to a physical disk — slow, millisecond-latency I/O. Zram creates a compressed block device *in RAM itself*. With the LZ4 algorithm, compression and decompression take **nanoseconds** on a modern CPU, making Zram roughly 1,000× faster than disk swap.

The old rule of "keep swappiness low to avoid disk thrashing" no longer applies. AnduinOS defaults to:

* **`vm.swappiness = 100`** — Move idle app memory into compressed RAM instead of throwing away file cache. Your UI resources and app code stay hot, so the desktop feels snappy under memory pressure.
* **`vm.page-cluster = 0`** — Disable swap readahead (useless overhead for random-access RAM).

On a fresh install, Zram uses **50% of your total RAM, LZ4 compression, swap priority 100**, and is enabled at boot.

## Inspecting your swap

```bash title="Current swappiness"
cat /proc/sys/vm/swappiness
```

```bash title="Which sysctl.d file won?"
grep -r 'swappiness' /etc/sysctl.d/ /usr/lib/sysctl.d/ 2>/dev/null
```

The file with the **highest number** in its name wins.

```bash title="Is Zram active?"
zramctl
swapon --show        # Look for /dev/zram0
```

```bash title="Memory and swap overview"
free -h
```

## Modifying swappiness

### Method 1: Use the GUI *(Added in AnduinOS 2.0.1)*

Launch **Swap Control** from the application menu. It shows your current state, recommends a value based on whether Zram is active, and persists your choice across reboots.

### Method 2: sysctl.d drop-in (manual, permanent)

Create a file numbered higher than `90`:

```bash title="Create a custom override"
echo 'vm.swappiness = 80' | sudo tee /etc/sysctl.d/95-my-swap.conf
sudo sysctl --system
```

To revert, delete the file and re-run `sudo sysctl --system`.

### Method 3: Live change (temporary)

```bash
sudo sysctl -w vm.swappiness=80
```

Takes effect immediately, lost at the next reboot.

## Modifying Zram

Zram configuration lives at `/etc/default/anduinos-zram`. This file is **optional** — if it doesn't exist, the service uses built-in defaults.

```bash title="Example: custom Zram size"
# /etc/default/anduinos-zram
ZRAM_SIZE_MB=8192
ZRAM_ALGORITHM=lz4
ZRAM_PRIORITY=100
```

```bash title="Apply changes"
sudo systemctl restart anduinos-zram.service
```

The built-in defaults are: **50% of RAM, LZ4, priority 100**.

To disable Zram entirely:

```bash
echo 'ZRAM_ENABLED=no' | sudo tee /etc/default/anduinos-zram
sudo systemctl restart anduinos-zram.service
```

### Multi-device Zram (advanced)

You can split Zram across multiple devices, each with its own size, algorithm, and priority:

```bash
# /etc/default/anduinos-zram
ZRAM_DEVICE_COUNT=2
ZRAM_0_SIZE_MB=4096
ZRAM_0_ALGORITHM=lz4
ZRAM_0_PRIORITY=100
ZRAM_1_SIZE_MB=4096
ZRAM_1_ALGORITHM=zstd
ZRAM_1_PRIORITY=50
```

## Using Zswap instead of Zram

Zswap compresses swap pages in a fixed-size RAM pool, but still requires a backing disk swap device. It is **disabled by default**.

```bash title="Enable Zswap"
cat <<EOF | sudo tee /etc/default/anduinos-zswap
ZSWAP_ENABLED=yes
ZSWAP_COMPRESSOR=lz4
ZSWAP_MAX_POOL_PERCENT=20
ZSWAP_ACCEPT_THRESHOLD=90
ZSWAP_SHRINKER=Y
EOF
sudo systemctl enable --now anduinos-zswap.service
```

!!! warning "Zswap needs a backing swap device"

    Unlike Zram, Zswap does not create a swap device — it only compresses pages on their way to a disk swap file or partition. Make sure you have one configured first (see [Manage Swap](../../Install/Manage-Swap.md)).

!!! tip "Don't run Zram and Zswap together"

    They solve the same problem. Running both wastes RAM and CPU. Pick one.

## How the layers work

There are **five layers** that can set `vm.swappiness`. Each layer ships or writes a file under `/etc/sysctl.d/`, and `systemd-sysctl.service` processes them in lexicographic order at boot — later files override earlier ones.

| Layer | File | Set by | Default value |
|-------|------|-------|--------------|
| ⑤ Live | *(none — `/proc/sys`)* | `sysctl -w` | *immediate, lost at reboot* |
| ④ Highest priority file | `90-anduinos-swapcontrol.conf` | `anduinos-swapcontrol-gtk` (GUI) | *user's choice* |
| ③ Zram-optimized | `30-anduinos-swap.conf` | `anduinos-swap-config` | `100` + `page-cluster=0` |
| ② Last-resort fallback | `20-anduinos-tweaks.conf` | `anduinos-system-tweaks` | `10` *(only if ③ and ④ are both removed)* |
| ① Kernel default | *(built-in)* | — | `60` |

```
⑤  sysctl -w                     ← live override (lost at reboot)
    ↑
④  90-anduinos-swapcontrol.conf  ← user's GUI choice
    ↑
③  30-anduinos-swap.conf         ← Zram-optimized: swappiness=100, page-cluster=0
    ↑  (on a normal system, this is the effective layer)
②  20-anduinos-tweaks.conf       ← last resort: swappiness=10 (only active when ③④ are both gone)
    ↑
①  kernel default                ← built-in: swappiness=60
```

Two systemd services handle device setup, both from `anduinos-swap-config`:

| Service | Default | Purpose |
|---------|---------|---------|
| `anduinos-zram.service` | **enabled** | Creates Zram swap devices at boot |
| `anduinos-zswap.service` | disabled | Applies zswap sysfs parameters at boot |

Both services read their config from `/etc/default/` — if the file doesn't exist, they use built-in defaults. The config files are only created when you save changes through the GUI.

## Undoing changes

### Factory reset

Since the `/etc/default/` config files are optional (not shipped by any package), deleting one reverts that component to its built-in defaults:

```bash title="Factory reset Zram"
sudo rm /etc/default/anduinos-zram
sudo systemctl restart anduinos-zram.service
```

```bash title="Factory reset Zswap"
sudo rm /etc/default/anduinos-zswap
sudo systemctl restart anduinos-zswap.service
```

```bash title="Factory reset swappiness (back to 100)"
sudo rm /etc/sysctl.d/90-anduinos-swapcontrol.conf
sudo sysctl --system
```

No package reinstallation needed.

### Uninstalling `anduinos-swapcontrol-gtk` (the GUI)

The `/etc/sysctl.d/90-anduinos-swapcontrol.conf` file survives uninstall — it was generated at runtime, not shipped in the `.deb`. Your last swappiness choice persists until you delete the file.

To fully clean up:

```bash
sudo rm /etc/sysctl.d/90-anduinos-swapcontrol.conf
sudo sysctl --system
```

The system falls back to `30-anduinos-swap.conf` (swappiness=100).

### Uninstalling `anduinos-swap-config`

The post-removal script runs `sysctl --system`. With `30-anduinos-swap.conf` gone, the next file wins — `20-anduinos-tweaks.conf` with swappiness=10. The Zram service is also removed, so your Zram devices disappear.

!!! note "Swappiness=10 is the correct fallback when Zram is gone"

    Without Zram, your swap is a disk file. Low swappiness avoids pushing pages to slow disk I/O. The fallback chain is intentional: remove Zram → revert to disk-appropriate swappiness.

### Uninstalling `anduinos-system-tweaks`

Removes the last sysctl.d file. Swappiness returns to the kernel default of **60**.

## Quick reference

```bash
# Inspect
cat /proc/sys/vm/swappiness       # Current swappiness
grep -r 'swappiness' /etc/sysctl.d/ /usr/lib/sysctl.d/  # Which file wins
zramctl                           # Zram devices
swapon --show                     # All active swap devices
free -h                           # Memory + swap usage
systemctl status anduinos-zram    # Zram service status

# Change (temporary)
sudo sysctl -w vm.swappiness=80

# Change (permanent, custom override)
echo 'vm.swappiness = 80' | sudo tee /etc/sysctl.d/95-my-swap.conf
sudo sysctl --system
sudo rm /etc/sysctl.d/95-my-swap.conf && sudo sysctl --system   # Revert

# Zram control
sudo vim /etc/default/anduinos-zram
sudo systemctl restart anduinos-zram.service

# Factory reset
sudo rm /etc/default/anduinos-zram /etc/default/anduinos-zswap
sudo rm /etc/sysctl.d/90-anduinos-swapcontrol.conf
sudo sysctl --system
```
