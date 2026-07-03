# anduinos-zram-config — 架构笔记

## 职责

为 AnduinOS 桌面版提供**出厂默认**的 zram 压缩内存 swap。
用户一装完系统就有 50% RAM 的 lz4 压缩 swap，无需打开任何 GUI。

**这个包不做的事情：** 不提供 GUI，不提供配置界面，不依赖 polkit，不依赖 GTK。
用户自定义 → 用 `anduinos-swapcontrol-gtk`。

---

## 五个必须注意的点

### 1. systemd unit 的三行铁律（不能多一行）

所有内存管理相关的 service **必须**严格使用这个模板：

```ini
[Unit]
DefaultDependencies=no
After=systemd-journald.socket
Before=swap.target
```

**为什么不能多？**

```
DefaultDependencies=yes（默认值）会隐式注入 After=basic.target
    ↓
tmp.mount 自动生成 After=swap.target
    ↓
形成循环链：tmp.mount → swap.target → anduinos-zram → basic.target → tmp.mount
    ↓
systemd 删除 tmp.mount 重新挂载 → /tmp 内容被空白 tmpfs 覆盖
    ↓
systemd-tmpfiles-setup 建的 /tmp/.X11-unix 消失
    ↓
GDM 登录失败（XWayland 找不到可写的 socket 目录）
```

**不要加** `After=sysinit.target`、`After=local-fs.target`、`After=basic.target`——每一个都会制造新的循环。

唯一安全的 After 是 `systemd-journald.socket`（让日志早点可用，不会引入循环）。

### 2. 为什么装在 /usr/lib 而不是 /etc

```
/usr/lib/systemd/system/anduinos-zram.service  ← 我们（供应商默认）
/etc/systemd/system/anduinos-zram.service       ← 用户自定义 / swapcontrol-gtk
```

systemd 优先级：`/etc` > `/run` > `/usr/lib`。

用户通过 swapcontrol-gtk 修改后，配置写入 `/etc`，自动覆盖供应商默认。
`dpkg` 升级包时不会因为 `/etc` 下有同名文件而报 conffile 冲突——因为我们的文件在 `/usr/lib`。

**如果装到 /etc：** dpkg 升级时会检测到用户改过 → 弹 conffile diff → 用户困惑 → 选错就炸。

### 3. 依赖极简，不链入 GUI 生态

```xml
<Dependency Include="util-linux" />  <!-- 只要这个 -->
```

`zramctl`、`mkswap`、`swapon` 都来自 util-linux。
不依赖 polkit、不依赖 gtk、不依赖 helper 脚本。

这意味着 server 版也能装，纯 systemd + bash + zramctl 就能跑。

### 4. ExecStart 用直接路径，不用 helper

```ini
# 正确：直接调用系统二进制
ExecStart=/usr/sbin/modprobe zram
ExecStart=/bin/bash -c 'MEM=$(awk "/MemTotal/{printf "%.0f",$2/2048}" /proc/meminfo); DEV=$(/usr/sbin/zramctl -f -s ${MEM}M -a lz4) && /usr/sbin/mkswap $DEV && /usr/sbin/swapon -p 100 $DEV'
```

**不要用** `/usr/lib/anduinos-swapcontrol/helper`——那是 swapcontrol-gtk 的 polkit 提权通道，只存在于安装了 GUI 包的机器上。

### 5. systemd preset 是启动的唯一入口

`90-anduinos-zram.preset`:
```
enable anduinos-zram.service
```

没有 `WantedBy=multi-user.target` 的软链接，没有 `/etc/default` 配置文件。
preset 让 systemd 在第一次安装时自动 enable。用户可以用 `systemctl disable` 关掉，也可以用 swapcontrol-gtk 的 mask 彻底屏蔽。

---

## 与 anduinos-swapcontrol-gtk 的关系

```
                     ┌─────────────────────────┐
                     │ anduinos-zram-config     │
                     │ (供应商默认，/usr/lib)     │
                     │ 开机即用，无需配置          │
                     └───────────┬─────────────┘
                                 │
                    用户打开 GTK App，看到 zram 已启用
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
         不改设置            点 Remove           调整参数点 Apply
         zram 保持           mask 掉            生成新 unit 到
         默认状态           供应商默认           /etc 覆盖供应商默认
```

- **不冲突：** 各自管各自目录，systemd 优先级机制天然解耦
- **不依赖：** 可以只装一个
- **可预测：** 用户的 GUI 操作永远有最终决定权（/etc + mask > /usr/lib + preset）

## 潜在风险

| 场景 | 后果 | 如何处理 |
|------|------|----------|
| 用户卸载 swapcontrol-gtk 但之前 mask 了 zram | mask 符号链接留在 /etc → zram 永远起不来 | 设计选择：尊重用户最后一次操作。重新安装 config 包也不会清 /etc。用户可手动 `systemctl unmask` |
| 用户同时装了 swapcontrol-gtk 开启了 zswap | zswap + zram 双重压缩，浪费 CPU | GUI 有文字提示"Use Zram or Zswap, not both"，但不强制阻止。见 swapcontrol-gtk 的 ARCHITECTURE.md |
| 内核没有 lz4 模块 | modprobe zram 成功但 zramctl -a lz4 失败 | 服务启动失败，日志可见。swapcontrol-gtk 的算法列表会自动 fallback 到可用算法 |
