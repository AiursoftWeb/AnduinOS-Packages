# anduinos-swapcontrol-gtk — 架构笔记

## 职责

AnduinOS 虚拟内存配置的 GTK4 GUI。
用户可以**查看、修改并持久化**：磁盘 swap、zswap 参数、zram 设备。

---

## 五个必须注意的点

### 1. systemd ExecStart 里不能用 `<<<`（here-string 是 bash 语法，systemd 不认）

这是踩过的最坑的 bug。

```ini
# ❌ 错误：<<< 是 bash here-string，systemd ExecStart 不调用 shell
ExecStart=/usr/lib/.../helper tee /sys/.../enabled <<< "1"

# ✅ 正确：显式调用 bash -c
ExecStart=/usr/lib/.../helper bash -c 'echo 1 > /sys/.../enabled'
```

systemd 的 `ExecStart=` 直接 `execve()` 目标二进制，**不走 shell**。
`<<<` 会被当成普通字符串参数传给 `tee`：
- `helper tee` 收到的是字面量 `/sys/.../enabled` `<<<` `1`——tee 理解不了
- 如果写成 `/bin/sh -c '...<<<...'`，`sh`（dash）也不支持 `<<<`
- **必须用 `bash -c`**，因为 AnduinOS 上 dash 不认 here-string

### 2. 关闭服务时，先 rm 再 mask（顺序反了 mask 就白做了）

这是刚修好的 bug。

```rust
// ✅ 正确顺序
let _ = exec::run_helper("rm", &["-f", ZRAM_SERVICE]);          // 先删用户自定义 unit
let _ = exec::run_helper("systemctl", &["mask", "--now", "anduinos-zram.service"]); // 再 mask

// ❌ 错误顺序（v2.0.0-7 及之前）
let _ = exec::run_helper("systemctl", &["mask", "--now", "..."]); // mask 创建了 /dev/null 符号链接
let _ = exec::run_helper("rm", &["-f", ZRAM_SERVICE]);            // rm 把刚创建的符号链接删了！
```

`systemctl mask` 的实现就是在 `/etc/systemd/system/<name>.service` 创建一个指向 `/dev/null` 的符号链接。
如果 `mask` 之后 `rm`，符号链接就被删了。重启后 `/usr/lib` 下的供应商默认复活。

**先 rm 再 mask** 确保 mask 符号链接是最后留下的东西。

### 3. 不要往 GUI 包里塞系统修复黑魔法

我们犯过这个错误：

```
# 曾经手动创建的东西（已删除）：
/etc/systemd/system/gdm.service.d/10-fix-x11-socket.conf  ← GDM drop-in
/usr/lib/anduinos-swapcontrol/fix-x11-socket               ← PAM 脚本
/etc/pam.d/gdm-password (加了 pam_exec 行)                 ← PAM hook
```

**为什么这是错的：**

- GDM 登录失败的根本原因不是 X11 socket 权限，而是 zswap/zram service 的 ordering cycle 导致 tmp.mount 被 systemd 重启，tmpfs 空白覆盖了 `/tmp/.X11-unix`
- 修好 ordering cycle 之后，这些 hack 一个都不需要
- 把系统级修复塞进 GUI 包 → 职责混乱 → 删又不敢删 → 升级变噩梦
- PAM hook 以 root 跑脚本改 `/tmp` 权限本身就是架构异味

**教训：** GUI app = 用户配置界面，不应承担 systemd 启动修复、X11 socket 管理、PAM 注入等系统级职责。

### 4. /etc 覆盖 /usr/lib 是故意设计的，不是意外

```
用户点 Apply → 生成 unit → 写入 /etc/systemd/system/anduinos-zram.service
供应商默认    →          → 位于 /usr/lib/systemd/system/anduinos-zram.service

systemd 优先级：/etc > /usr/lib
→ 用户配置生效，供应商默认被覆盖
→ dpkg 升级供应商包时不会报 conffile 冲突
```

**删除时为什么需要 mask：** 如果只 `rm /etc` 版本，systemd 会 fallback 到 `/usr/lib` 版本→ 供应商默认复活。
`systemctl mask` 创建一个 `/etc/systemd/system/<name>.service → /dev/null` 符号链接，这会阻止所有路径的加载。

### 5. DefaultDependencies=no 是生死线

所有由 persist.rs 生成的 service unit **必须**包含：

```ini
[Unit]
DefaultDependencies=no
After=systemd-journald.socket
Before=swap.target
```

为什么必须这样、不能加什么 After= —— 详见 `anduinos-zram-config/ARCHITECTURE.md` 第 1 节。两个包共享同一个约束。

---

## 与 anduinos-zram-config 的潜在冲突

### 冲突 1：zswap + zram 双重压缩

用户通过 GUI 开启 zswap 时，zram 可能仍在运行（来自供应商默认或之前的配置）。

**后果：** 内存页先被 zswap 压缩存入 RAM pool，然后被 swap 到 zram 设备时又被 zram 二次压缩。浪费 CPU、浪费内存、没有收益。

**当前处理：** swap_view.rs 的 zswap 开关 subtitle 里有文字警告：
> "Use Zram or Zswap, not both."

但这只是**软提示**，不阻止用户同时开启。是故意不作为的——GUI 不应替用户做决定。

### 冲突 2：卸载 swapcontrol-gtk 后 mask 残留

如果用户通过 GUI 关闭了 zram（触发 mask），然后卸载 swapcontrol-gtk：
- `/etc/systemd/system/anduinos-zram.service → /dev/null` 符号链接留在磁盘
- 即使 `anduinos-zram-config` 还在，zram 也起不来
- 用户困惑："明明装了 zram-config，为什么没有 zram？"

**这是设计选择，不是 bug：** mask 是用户有意操作的最终结果，卸载 GUI 工具不应偷偷复活被用户明确关闭的服务。

**恢复方法：** `sudo systemctl unmask anduinos-zram.service && sudo systemctl enable --now anduinos-zram.service`

### 冲突 3：ExecStart 语法不同，但语义等价

| | anduinos-zram-config（供应商默认） | swapcontrol-gtk（用户覆盖） |
|---|---|---|
| 提权方式 | 直接 root（systemd 启动时） | 通过 polkit helper |
| ExecStart 路径 | `/usr/sbin/zramctl` | `/usr/lib/anduinos-swapcontrol/helper zramctl` |
| 大小计算 | `awk` 动态 50% | 用户选择的固定 MiB 值 |

如果用户先装了 config 包（动态 50%）再通过 GUI 调整（固定 N MiB），GUI 会生成新 unit 到 /etc，覆盖供应商默认。这是预期行为。

### 冲突 4：config 包升级不会影响已自定义的用户

如果 AnduinOS 升级了 `anduinos-zram-config`（比如把默认算法从 lz4 改成 zstd），已通过 GUI 自定义的用户**完全看不到这个变化**——他们的 `/etc` 版本覆盖了升级后的 `/usr/lib`。

这是正确的：用户的显式选择优先于供应商默认。
但意味着我们改供应商默认只能影响**新用户**和**从未在 GUI 点过 Apply 的用户**。

### 冲突 5：preset vs mask 的优先级

```
anduinos-zram-config: preset 说 enable
swapcontrol-gtk:      用户关了 → mask (symlink to /dev/null)

结果: mask 赢。systemd 看到 /etc 下的 mask 符号链接，根本不去读 /usr/lib，也不管 preset。
```

这是 systemd 的标准行为，也是我们想要的。但值得在文档里写清楚。
