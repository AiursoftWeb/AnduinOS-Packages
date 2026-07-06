# anduinos-swapcontrol-gtk — 架构笔记

## 职责

AnduinOS 虚拟内存配置的 GTK4 GUI。**纯配置编辑器 + 只读状态展示。**

用户可以**查看、修改并持久化**：磁盘 swap、zswap 参数、zram 设备。
**执行权完全交给 `anduinos-swap-config` 包。**

---

## 架构原则

```
GUI (本包)                           anduinos-swap-config (vendor)
────────                             ─────────────────────────────
写 /etc/default/anduinos-zram   →    setup-zram.sh 读取并执行
写 /etc/default/anduinos-zswap  →    setup-zswap.sh 读取并执行
systemctl restart service       →    service 重启 = 立即应用
读 /sys/* /proc/*               ←    (只读 sysfs，无需 root)
```

**GUI 不做的事情：** 不调 zramctl，不写 sysfs，不生成 systemd unit。

---

## 技术要点

### 1. 单入口提权

所有需要 root 的操作通过 `/usr/lib/anduinos-swapcontrol/helper` + polkit：

```
GUI → pkexec → helper → {swapon, swapoff, mkswap, dd, chmod, rm, sysctl, tee, systemctl}
```

已从 helper 中删除的废弃子命令：`zramctl`、`bash`（迁入 vendor service 后不再需要）。

### 2. persist 层只写 config 文件

`persist_zram()`: 写 `/etc/default/anduinos-zram` → `systemctl restart anduinos-zram.service`
`persist_zswap()`: 写 `/etc/default/anduinos-zswap` → `systemctl restart anduinos-zswap.service`

注意：即使是“关闭” zram / zswap，也必须 `restart` vendor service，而不是 `stop`。因为这两个 unit 都是 `Type=oneshot`，`stop` 不会把“禁用配置”重新写回内核状态。

不再生成 systemd unit 到 `/etc`，不再调 mask/unmask。

### 3. vendor service 存在性检查

如果 `anduinos-swap-config` 未安装（用户跳过 Recommends 或手动卸载），persist 层返回明确的错误消息提示用户安装。GUI 不会静默失败。

### 4. DefaultDependencies=no 是生死线

所有 vendor service（由 anduinos-swap-config 提供）必须包含：
```ini
DefaultDependencies=no
After=systemd-journald.socket
Before=swap.target
```

详见 `anduinos-swap-config/ARCHITECTURE.md`。

### 5. 只读操作不需要提权

- `zram.rs`：读 `/sys/block/zram*/` 和 `/proc/swaps`（纯 fs::read_to_string）
- `zswap.rs`：读 `/sys/module/zswap/parameters/*`（纯 fs::read_to_string）
- `swapfile.rs`：读 `/proc/swaps`

所有只读函数都不需要 pkexec，GUI 打开即可看到实时状态。

---

## 与 anduinos-swap-config 的关系

- **无硬依赖：** Recommends，不是 Depends。可以不装 config 包，GUI 仍能查看状态
- **无 systemd unit 冲突：** GUI 不写 `/etc/systemd/system/`，vendor service 永远在 `/usr/lib/`
- **用户配置优先：** `/etc/default/anduinos-zram` 是用户的显式选择，vendor service 优先读它，不存在才用 fallback

---

## 迁移兼容性

`persist_zram()` 和 `persist_zswap()` 首次运行时自动清理旧版遗留：
- 删除 `/etc/systemd/system/anduinos-zram.service`（旧版 GUI 生成的 unit）
- 删除 `/etc/systemd/system/anduinos-zswap.service`（旧版 GUI 生成的 unit）
- 之后再对 vendor service 执行 `daemon-reload` / `unmask` / `restart`，避免 systemd 继续优先使用 `/etc` 下的旧 unit

### 6. systemctl 失败必须向上抛

写配置文件不等于“已经应用成功”。

`persist_zram()` / `persist_zswap()` 对 `systemctl enable|restart|unmask|daemon-reload` 的失败都会返回错误，让 GUI 明确提示用户，而不是出现“配置写入了但实际没生效”的假成功。
