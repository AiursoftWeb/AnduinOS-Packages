# anduinos-swapcontrol-gtk — 架构笔记

## 职责

AnduinOS 虚拟内存配置的 GTK4 GUI。它必须区分三种完全不同的对象：

- 安装器创建的磁盘 Swap 分区：只读展示，不在线调整大小；
- 旧版 AnduinOS 的 `/swapfile`：继续支持启停和调整大小；
- Zram/Zswap/sysctl：写声明式配置，并交给 `anduinos-swap-config` 应用。

`/proc/swaps` 中发现一个非 Zram 设备，绝不意味着它就是 `/swapfile`。状态读取和
写入目标必须使用同一个明确的设备身份。

## Swap 分区

新版安装器为 Btrfs 和 ext4 都创建动态大小的专用 Swap 分区。GUI 显示设备路径、
容量和使用量，但不提供大小滑块。在线扩大该分区可能需要移动紧邻的根分区，不能
包装成普通设置操作。

分区容量由安装器策略决定：至少 2 GiB，保留至少 20 GiB 根空间，空间允许时优先
满足向上取整的 RAM + 1 GiB，否则使用 RAM/2 的回退目标并封顶 64 GiB。

## `/swapfile` 兼容层

老用户已有的 `/swapfile` 是稳定、受支持的兼容路径。GUI 单独显示它；不存在时大小
为 0 GiB，绝不能把 Swap 分区的容量填入这个滑块。

- 支持 ext2/ext3/ext4/XFS；
- 新大小最多 64 GiB，并始终为系统保留 20 GiB 可用空间；
- Apply 仅在大小确实变化时触发文件替换；
- `swapoff` 失败时不得修改文件；
- 空间足够时先构建并验证替换文件，再切换路径；
- 空间不足以同时容纳新旧文件时，释放已停用的旧文件，并在失败时重建旧容量；
- 开关同步维护 `/etc/fstab`，而不是只改变当前启动周期；
- 0 GiB 表示停用、移除 `/swapfile` 及其 fstab 项。

AnduinOS 默认 Btrfs 根禁止创建额外 `/swapfile`。即使正确创建 NOCOW Swapfile，活跃
文件仍会阻止包含它的 `@root` 子卷被 Disk Snapshots Manager 创建恢复点。增加第七个 `@swap` 子卷属于
安装器和 Disk Snapshots Manager 的系统 ABI 变更，不能由这个 App 私自创建。

## 休眠健康状态

“内核提供 suspend-to-disk”不等于“休眠已经配置”。Dashboard 只有在以下条件全部
满足时才显示 Ready：

1. `/sys/power/state` 支持 `disk`，并且存在可用的 `/sys/power/disk` 模式；
2. 内核命令行或 initramfs 配置声明了 resume 目标；
3. UUID/路径能够解析为真实设备；
4. 该目标当前是活跃 Swap；
5. 目标容量至少达到向上取整的 RAM + 1 GiB。

容量判断使用 Swap 文件的真实长度或块设备的真实容量，而不是 `/proc/swaps` 扣除
Swap header 后的可用页数，避免将安装器精确创建的容量误报为不足。

对于 Swapfile，resume 依赖底层块设备和 `resume_offset`。已作为 resume 目标的
`/swapfile` 禁止调整大小，因为重新分配会改变物理 offset；用户必须先禁用休眠。
关闭该文件也必须显示明确警告；即使文件暂时 inactive，仍保持目标身份和调整锁。
无关的 Swap 分区休眠配置不会阻止调整兼容文件。

## 提权边界

所有 root 操作通过 `/usr/lib/anduinos-swapcontrol/helper` 和一个 polkit action。Helper
只接受固定语义的 Swapfile 操作，以及固定路径/固定 unit 的 Zram、Zswap、sysctl
操作。不得重新引入可向 `dd`、`rm`、`tee` 或 `systemctl` 透传任意参数的入口。

## 与 `anduinos-swap-config` 的关系

```text
GUI                                      anduinos-swap-config
──────────────────────────────────       ───────────────────────
读 /proc/swaps、/sys、resume 配置
管理兼容 /swapfile
写 /etc/default/anduinos-zram       →    setup-zram.sh
写 /etc/default/anduinos-zswap      →    setup-zswap.sh
systemctl restart vendor service    →    应用 Zram/Zswap 配置
```

GUI 首次持久化 Zram/Zswap 时继续清理旧 GUI 生成的 `/etc/systemd/system` unit。Vendor
service 固定由 `anduinos-swap-config` 安装在 `/usr/lib/systemd/system`。
