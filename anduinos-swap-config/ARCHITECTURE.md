# anduinos-swap-config — 架构笔记

## 职责

为 AnduinOS 提供**出厂默认**的 swap 配置。
用户装完系统默认只有 **50% RAM 的 lz4 zram**，以及 `swappiness=100`；`zswap` 保持关闭，按需再由 GUI 开启。

**这个包不做的事情：** 不提供 GUI，不依赖 polkit，不依赖 GTK。
用户自定义 → 用 `anduinos-swapcontrol-gtk`（纯配置编辑器，写 `/etc/default/anduinos-{zram,zswap}`）。

## 包含的服务

| Service | Setup 脚本 | Config 文件 |
|---------|-----------|-------------|
| `anduinos-zram.service` | `setup-zram.sh` | `/etc/default/anduinos-zram` |
| `anduinos-zswap.service` | `setup-zswap.sh` | `/etc/default/anduinos-zswap` |

Sysctl drop-in: `30-anduinos-swap.conf` → swappiness=100, page-cluster=0

---

## 必须注意的点

### 1. systemd unit 的铁律

所有内存管理 service **必须**使用：

```ini
[Unit]
DefaultDependencies=no
After=systemd-journald.socket
Before=swap.target
```

**原因：** `DefaultDependencies=yes` 会注入 `After=basic.target`，与 swap.target 形成循环依赖，导致 systemd 重新挂载 `/tmp`，清空 `/tmp/.X11-unix`，GDM 登录失败。

**不要加** `After=sysinit.target`、`local-fs.target`、`basic.target`。

### 2. 为什么装到 /usr/lib 而不是 /etc

```
/usr/lib/systemd/system/anduinos-zram.service   ← 供应商默认
/etc/systemd/system/anduinos-zram.service         ← 不存在（GUI 不再写 unit）
```

GUI 不再生成 systemd unit 到 `/etc`。GUI 只写 `/etc/default/anduinos-{zram,zswap}` 配置文件，然后 `systemctl restart` 对应的 vendor service。

### 3. setup-zram.sh: teardown + rebuild 模式

每次运行先清理所有已有 zram 设备，再按配置重建。这保证 `systemctl restart` 能正确应用新配置。

### 4. setup-zswap.sh: 声明式配置应用

读取 `/etc/default/anduinos-zswap`，echo 到 `/sys/module/zswap/parameters/*`。
如果内核根本不支持 zswap，则脚本静默退出，不把 vendor service 置为 failed。

### 5. 依赖极简

```xml
<Dependency Include="util-linux" />  <!-- 只要这个 -->
```

不依赖 polkit、gtk、helper 脚本。Server 版也能装。

### 6. Preset 是启用入口

`90-anduinos-swap.preset` 在首次安装时只自动 enable `anduinos-zram.service`。
`anduinos-zswap.service` 作为可选能力安装，但默认不启用。

### 7. 包迁移

`anduinos-swap-config` 通过 Provides/Replaces/Conflicts 无缝替换旧的 `anduinos-zram-config`。

---

## 与 anduinos-swapcontrol-gtk 的关系

```
anduinos-swapcontrol-gtk (GUI)
  "纯配置编辑器 + 只读监控"
       │
       │ 写 /etc/default/anduinos-zram
       │ 写 /etc/default/anduinos-zswap
       │ systemctl restart anduinos-{zram,zswap}.service
       │ 读 /sys/block/zram* / /sys/module/zswap/* / /proc/swaps
       ▼
anduinos-swap-config (vendor)
  "拥有所有执行逻辑"
  setup-zram.sh  → zramctl / mkswap / swapon
  setup-zswap.sh → echo to sysfs
```

- **GUI 不写 systemd unit** — 只写声明式 config
- **GUI 不调 zramctl/mkswap/swapon** — service 脚本全权负责
- **可以只装一个** — 没有硬依赖，但 GUI 会提示安装 swap-config 以启用持久化
