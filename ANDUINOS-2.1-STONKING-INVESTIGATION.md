# AnduinOS 2.1 / Ubuntu 26.10 Stonking：发布前一个月调查与实施清单

> 调查基线：2026-09-19；目标窗口：2026-09-19—2026-10-19；Ubuntu 26.10 计划 2026-10-15 发布。本文是**实施计划和代码草案**，不是已经完成的兼容性声明。实际发布必须以冻结后的 Ubuntu 软件包、真实 ISO、QEMU 和真机结果为准。本文不修改任何运行时实现。

## 0. 结论与必须先作出的决定

**AnduinOS 2.1 可以作为 Ubuntu 26.10/GNOME 51 的 Latest 分支开发；不能仅因为 2.1 ISO 可以安装，就开放 2.0 LTS → 2.1 原地升级。** 当前仓库的 78 个 `.aosproj` 目标套件声明均没有 `stonking-addon`；只有 `anduinos-desktop-apps` 预埋了一个尚不生效的 Stonking `impression` 推荐项。`anduinos-desktop` 还与 Ubuntu release-upgrader 冲突，AnduinOS APT 源由套件专属包写入。因此 2.1 的发行与 2.0→2.1 升级是两个独立发布门槛。

最先决定四件事：

1. **Secure Boot + Btrfs 启动链。** Ubuntu 曾提出从 26.10 签名 GRUB 删除 Btrfs `/boot` 支持；截至本调查不能把提案当成最终事实。必须检查候选 `grub-efi-*-signed` 二进制并实际冷启动。若删掉，现有 `/boot` 位于 Btrfs `@root` 的布局与快照回滚 ABI 都受影响。这是 P0 发布阻断项，不是换一行 GRUB 参数能解决的问题。[Ubuntu 讨论原文](https://discourse.ubuntu.com/t/streamlining-secure-boot-for-26-10/79069)，仓库的 [Btrfs 设计](anduinos-installer-beta/BTRFS-DESIGN.md)。
2. **升级路径。** 先做 Ubuntu `ubuntu-release-upgrader` 的 AnduinOS 兼容性实验，包含源切换和品牌包保留；若不能在规定时间内证明正确，则 2.1 全新安装可以发布，但 2.0 系统升级 App 只显示“暂不可用”，并给出备份/全新安装指引。**绝不自动改 sources 后运行 `apt full-upgrade`。** Ubuntu 原生升级会禁用第三方仓库，恰好与 AnduinOS 关键包仓库冲突。[Ubuntu 升级说明](https://documentation.ubuntu.com/server/how-to/software/upgrade-your-release/index.html)。
3. **2.1 范围。** GNOME 51、扩展、现有应用与启动链适配是必需；新加的“系统升级”App 是产品功能，但 2.0→2.1 操作只有通过完整资格验证才开放。LUKS、TPM 解锁、全新备份服务、启动架构重写不宜混进四周交付。
4. **支持政策。** 2.0 显示为 **LTS**，2.1 显示为 **Latest，非 LTS**。Ubuntu 过渡版通常只有九个月支持；AnduinOS 若不能自行维护底层安全更新，就不能暗示 2.1 具有 2.0 的长期支持期。[Ubuntu LTS/过渡版说明](https://documentation.ubuntu.com/desktop/en/latest/how-to/switch-between-an-lts-and-interim-release/)。产品团队仍需明确 AnduinOS 自己的确切 EOL 日期与公告措辞。

### 证据分级

| 标记 | 含义 | 本文例子 |
|---|---|---|
| 已核实 | 当前仓库或官方已发布资料可直接验证 | GNOME 51 已发布；仓库无 Stonking 目标套件；2.0 APT 源仅 noble/resolute |
| 待冻结复核 | 26.10 开发仓库会变化 | 内核版本、GNOME 包版本、签名 GRUB 模块、具体依赖名 |
| 建议/设计 | 尚未实现、必须经过原型和测试 | 升级 App、发行通道元数据、跨版本事务协调器 |

## 1. 时间与工作量现实

Ubuntu 官方时间表：9 月 21 日 Beta/硬件支持冻结，9 月 24 日 Beta，10 月 1 日内核冻结，10 月 8 日 Final Freeze/RC，10 月 15 日正式发布。[官方时间表](https://documentation.ubuntu.com/release-notes/26.10/schedule/)。[26.10 发布说明](https://documentation.ubuntu.com/release-notes/26.10/)截至调查时仍有 `VERSION` 占位；不要把开发版的内核或 systemd 版本写死在产品承诺中。GNOME 51 已于 9 月 16 日发布。[GNOME 51 发布说明](https://release.gnome.org/51/)。

下面是**粗略工程量，不含等待上游修复**；同一人员不能把并行人日当自然日：

| 工作流 | 估计人日 | 阻断性 | 可并行性 |
|---|---:|---|---|
| Stonking 包图、APT 仓库、ISO 构建和依赖审计 | 12–20 | P0 | 可与扩展移植并行 |
| GNOME 51 扩展、主题、会话与 GDM | 12–22 | P0 | 可并行 |
| 签名 GRUB/Btrfs/Dracut/内核验证及必要重构 | 15–35+ | P0 | 高风险、决定关键路径 |
| 安装器和真机/VM 发行矩阵 | 12–20 | P0 | 依赖可启动 ISO |
| 系统升级 App 的只读 UI、通道与资格检测 | 8–15 | P0 产品功能 | 可早做 |
| **经验证的** 2.0→2.1 原地升级事务与恢复 | 20–40+ | 独立发布门槛 | 依赖前述稳定包图 |
| 28 语言、文档、发布工程、回归 | 10–18 | P0 | 可并行 |

合计约 **89–170+ 人日**。若只有一两位全职工程师，一个月内同时保证新装与安全原地升级很可能不现实；需要并行团队、严格冻结范围，或把升级按钮设为后续点版本才开放。更不能为了赶 10 月 15 日降低启动/数据安全门槛。

### 建议的四周节奏

| 日期 | 交付物/决策门 | 不通过时的动作 |
|---|---|---|
| 9/19–9/25 | Stonking 仓库/基础包图、首个 ISO；签名 GRUB 模块与 Btrfs 冷启动实验；扩展 51 问题清单；升级器技术路线 spike | 立即冻结高风险新功能；升级能力默认关闭 |
| 9/26–10/02 | amd64/arm64 构建、GNOME 日用冒烟、安装器全矩阵第一轮、2.0→2.1 VM 事务原型；10/1 内核冻结后复测 | 缺陷按 P0/P1 分类，停止非必要 App 替换 |
| 10/03–10/09 | 启动、快照、断电、升级回退演练；28 语言；10/8 前 RC 候选 | 未达升级门槛则只发禁用升级的 App；未达启动门槛则延期 2.1 |
| 10/10–10/15 | 基于 Ubuntu 最终包重建 ISO、签名与镜像校验、真机回归、发行说明 | 不随 Ubuntu 当天发布也比不可启动安全 |
| 10/16–10/19 | 上线观察、阻断回归的紧急修复、2.0 用户升级范围再评审 | 保留 2.0 LTS 正常更新，不强推 2.1 |

## 2. 当前代码基线与 2.1 包装总任务

当前仓库的核心事实：

- [lib/gnome-versions.sh](lib/gnome-versions.sh) 已映射 `stonking=51`，但扫描 78 个项目的 `<TargetSuites>`，没有一个真正声明 `stonking-addon`。不能机械地给 78 个包全加 Stonking；应按“复用/套件分支/不适用”逐包审计。
- [anduinos-desktop](anduinos-desktop/anduinos-desktop.aosproj) 冲突 `ubuntu-release-upgrader-core` 和 `-gtk`；[anduinos-apt-config](anduinos-apt-config/anduinos-apt-config.aosproj) 与 `-dev` 两组只写 noble/resolute 的 Deb822 源。`base-files` 当前仅目标 resolute，`/etc/os-release` 报 2.0.2/resolute。[文件](base-files/base-files.aosproj)。
- [anduinos-core-system](anduinos-core-system/anduinos-core-system.aosproj) 在 Resolute 依赖 `linux-generic-hwe-26.04`，并强依赖 Dracut、签名 GRUB/shim；Stonking 不能沿用“Resolute HWE”作为无审计默认。`systemd-sysv` 是 systemd 启动兼容包名，**不等于**仓库使用旧 `/etc/init.d` 服务；本仓库扫描未发现自有 SysV init 脚本。
- [anduinos-desktop-apps](anduinos-desktop-apps/anduinos-desktop-apps.aosproj) 已有条件性 `impression`，但目标套件/依赖检查源映射未扩展；`usb-creator-gtk` 仍留在旧套件。Stonking 官方仓库有 `impression` 二进制，且在 universe。[Ubuntu 包页](https://packages.ubuntu.com/en/stonking/gnome/impression)。
- [安装器](anduinos-installer-beta/anduinos-installer-beta.aosproj) 仍为 beta 命名且只构建 Resolute；其 [DESIGN](anduinos-installer-beta/DESIGN.md) 已涉及 Btrfs、ext4、手工分区、Secure Boot、网络、压缩、Dracut 等，旧文档中“release-one 只擦盘”的措辞与现有实现/测试不完全同步。2.1 前应让实现、文档、实际 ISO 三者一致，不按过时文档推断功能缺失。
- [Dracut 迁移设计](anduinos-dracut-migration/DESIGN.md) 已有独立 fallback、原子写入和验证模型；这是 **2.0 内的 initramfs-tools→Dracut 包升级**，不是已经证明 2.0→2.1 发行版升级安全。[快照恢复范围](anduinos-btrfs-snapshots-manager/docs/RECOVERY-SCOPE.md) 明确区分 `@root`、`@home`、外部恢复工件。
- [.gitlab-ci.yml](.gitlab-ci.yml) 有 lint、`apkg test` 与按包发布；[VM-TESTING](anduinos-installer-beta/VM-TESTING.md) 定义了安装后的真实启动证据。CI 测试绿灯不代替对最终 ISO 跑完整 VM/真机矩阵。
- **跨仓库依赖已核查：** 相邻 [AnduinOS-2 构建参数](../AnduinOS-2/args.sh) 默认 `TARGET_UBUNTU_VERSION=resolute`，菜单提示尚不列 stonking；[Live 包安装模组](../AnduinOS-2/mods/05-live-kernel-apps-installer/install.sh) 显式安装 `anduinos-installer-beta`、桌面/扩展/快照包；[ISO 构建器](../AnduinOS-2/build.sh) 另行生成 Live GRUB、字体、squashfs 与 arm64/amd64 启动映像。相邻 [AnduinOS-Container Dockerfile](../AnduinOS-Container/Dockerfile) 和 [CI](../AnduinOS-Container/.gitlab-ci.yml) 也默认 resolute。**只修改本仓库不会产出 2.1 ISO**；这些仓库需要独立变更/测试/发布，本报告仅调查、不改它们。

### 包图落地顺序

1. 为 `base-files`、APT 配置（正式和 dev）、keyring、core、desktop-core、session、主题、扩展、desktop-apps、installer、live-settings、快照管理器及其他被依赖包分别加入 `stonking-addon`；每个项目同步 `SuiteShortNameMap`、`DependencyCheckSource/SuiteMap`、条件资产路径、版本号与测试，而非只改 `<TargetSuites>`。
2. 为 stonking 创建 `/etc/os-release`、`lsb-release`、branding、`anduinos.sources`，确认 `Suites: stonking-addon stonking-webapps` 在实际仓库存在且签名可信；核验 `apt-cache policy` 不会从 resolute 混入底层 ABI 包。
3. `anduinos-core-system` 在 Stonking 依赖目标发行版的**当前受支持内核元包**（初选 `linux-generic`，发行冻结时验证），不要把版本数字硬编码；amd64/arm64 分别核验内核、headers、DKMS、firmware、Dracut、shim、GRUB 联动。
4. `apkg lint/build/test --all` 对两个架构和新套件跑通；求解器必须验证安装 `anduinos-desktop` 不移除品牌包/桌面包、不引入 `snapd`，且 `impression` 真实可安装。所有包一起发布到 staging，先完成候选包图、再切 ISO，最后才让升级 App 宣布可用。
5. 把 suite 到 GNOME 的映射升级为构建约束：若选取的扩展版本并未通过 51 实测，即便 metadata 写了 51，也不视为兼容。
6. 在 `AnduinOS-Container` 增加 stonking rootfs/CI，接着使 `AnduinOS-2` 的构建参数、菜单、镜像源、Live GRUB/Dracut/ISO 工具版本和包安装模组面向 stonking；**分别**重建 amd64 与 arm64 ISO，避免由 Resolute 宿主工具偶然拼出看似可用的 Stonking 镜像。

建议最小包条件草案（**示意，不直接复制成已验证补丁**）：

```xml
<TargetSuites>noble-addon resolute-addon stonking-addon</TargetSuites>
<SuiteShortNameMap>noble-addon=noble resolute-addon=resolute stonking-addon=stonking</SuiteShortNameMap>
<Dependency Include="linux-generic" Condition="'$(Suite)' == 'stonking-addon'" />
<Recommend Include="impression" Condition="'$(Suite)' == 'stonking-addon'" />
```

## 3. GNOME 51、扩展与桌面会话

GNOME 51 涉及 Mutter 帧调度/录屏、较新 NVIDIA 接口、截图与辅助功能、GDM 多认证方式、oo7 密钥存储、SVG 光标；GTK 4.23/Libadwaita 1.10 提供更好的分数缩放与 reduced-motion 支持。[用户变化](https://release.gnome.org/51/)、[开发者变化](https://release.gnome.org/51/developers/)。对 AnduinOS 而言，最高风险不是控件外观而是 Shell 扩展和 GDM/登录路径。

| 组件 | 当前证据 | 工作/门槛 |
|---|---|---|
| 自有 Voice Typing | [extension.js](anduinos-whisper-gtk/data/voice-typing@anduinos.com/extension.js) 调用 51 已删的 `Clutter.get_default_backend()`，并用已删的 St `vertical`；metadata 只写 49/50 | 先在 50 用新 API 回归，再在 51 测注入、快捷键、启动/禁用、会话锁定；**仅仅加 51 metadata 不够** |
| ArcMenu | [Stonking 生成树](gnome-shell-extension-arcmenu/deploy/stonking/arcmenu@arcmenu.com/menuButton.js) 仍导入 51 已删的 `pointerWatcher.js`；`open(PopupAnimation.FULL)` 也需检查 | 重选已支持 51 的上游版本或维护补丁；指针追踪逻辑实改后做 GNOME Shell 会话测试，不能只改导入 |
| Simple Weather | Resolute 生成树大量 `St.BoxLayout({vertical: ...})` | 重拉兼容 51 的上游或修源、回归；当前推荐安装不等于默认启用，也须保证用户自行启用后不崩 |
| Dash-to-Panel、DING、Blur My Shell、AppIndicator | 它们影响面板、桌面、模糊/托盘的登录后核心 UX | 逐个锁定确切上游提交、构建包、在 Wayland 51 上启用/禁用/热重载/多屏测试；缺失时宁可可见地降级而不是阻断登录 |
| 四个自有扩展与其余第三方包 | 自有 metadata 最高到 50；扩展元包通过 `Recommends` 汇集 | 完成每个 UUID 的“源版本→补丁→51 实测→打包”的签字矩阵；默认集与可选集分别验收 |

[GNOME 官方 51 移植指南](https://gjs.guide/extensions/upgrading/gnome-shell-51.html) 特别指出：`St.vertical` 移除、`Clutter.get_default_backend()` 移除、`Shell.GLSLEffect` 移除、PopupMenu `open/close` 改参数对象、`pointerWatcher.js` 移除；旧 `St.ButtonMask` 名称目前仍有兼容别名。`St.vertical` 和 backend 调用可先改并同时惠及 GNOME 50；ArcMenu 指针监视不能机械替换为 `Meta.CursorTracker`，要保持相同行为并实测。

```js
// Voice Typing 源中的可提前移植片段；按所在函数实际导入/生命周期整合。
const backend = global.stage.context.get_backend();
const column = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL});
// 不是：Clutter.get_default_backend() / {vertical: true}
```

扩展脚本 [lib/resolve-gnome-ext.py](lib/resolve-gnome-ext.py) 当前描述了“下载最接近版本后强制补 metadata”。2.1 要改成“找不到原生 51 支持则标为需移植，必须有测试记录才准发布”，不能让 metadata 的虚假兼容承诺隐藏启动错误。生成的 `deploy/` 是产物，优先更新下载/补丁源，避免每次构建覆盖手修。额外检查 dconf schema、扩展 CSS、GDM 品牌页、Ptyxis、Wayland 下截图/录屏/屏幕共享以及 28 语言的 RTL、搜索与输入法。

## 4. 预装 App 与用户层体验

- **确定替换：** Stonking 使用 `impression`，不再预装 `usb-creator-gtk`；现有条件项已经有方向，还要补目标套件和依赖检查。验证真实 ISO 写盘、设备选择/误写防护、Polkit、arm64/amd64、失败后重新插拔 USB。[Impression 包页](https://packages.ubuntu.com/en/stonking/gnome/impression)、[上游介绍](https://apps.gnome.org/Impression/)。
- **保持：** `celluloid` 暂不因 GNOME 51 就换 Showtime；现有默认 MIME 与多媒体插件链依赖 Celluloid/libmpv，Showtime 换入需要重新验证 codec、字幕、硬解、默认关联。`remmina`、`transmission-gtk` 等也不因为有更“GNOME”的替代品就换，除非功能覆盖和迁移成本有实测收益。
- **可选精简，不上关键路径：** `gnome-chess`、`gnome-power-manager` 等按 ISO 大小、定位和用户数据讨论；不把“删旧 App”与底层迁移绑定。[默认 App 列表](anduinos-desktop-apps/anduinos-desktop-apps.aosproj)、[MIME 默认值](anduinos-mimeapps/anduinos-mimeapps.aosproj)。
- **新 App：系统升级。** 详见第 8–9 节。控制面板可增加入口，但独立 App 直接可启动；它不是 GNOME Software 的普通包更新页。新 UI 与安装器共同使用 28 语言政策及 [本地化 CI](lib/verify-localizations.py)，展示键盘焦点、屏幕阅读器标签和清楚的危险/支持期文字。

## 5. 系统组件：DBus、systemd、工具链、图形与安全

### D-Bus：这里已有一个具体包图问题

Ubuntu 明确宣布 26.10 的 system/user bus 默认改用 `dbus-broker`，但应用层协议与配置应兼容；Resolute 也能提前安装测试。[Ubuntu 公告](https://discourse.ubuntu.com/t/ubuntu-26-10-is-switching-to-dbus-broker/84060/)。当前 [core-system](anduinos-core-system/anduinos-core-system.aosproj) 同时硬依赖 `dbus`、`dbus-bin`、`dbus-user-session`。Stonking 的 `dbus` 包仍依赖 `dbus-daemon`，而 [`dbus-bin`](https://packages.ubuntu.com/en/stonking/admin/dbus-bin) 提供可独立安装的命令行工具；[`dbus-user-session`](https://packages.ubuntu.com/de/stonking/admin/dbus-user-session) 可依赖 broker。因此 **Stonking 分支应重新求解/审计 `dbus` 硬依赖，优先保留实际需要的 `dbus-bin`、`dbus-user-session`，显式保证 broker 运行**；不要在未验证反向依赖前全局卸载 daemon。

先在 Resolute **专用 VM** 安装 broker，跑 GDM、登录、控制面板、Installer 的 NetworkManager/Polkit、语音输入、YubiKey、驱动中心、托盘、portal/Flatpak、文件选择/屏幕共享测试；测试 `systemctl status dbus-broker` 与 user bus、AppArmor 拒绝日志。**不在正在使用的开发主机上做实验性 bus 切换。** 代码中的 Gio/D-Bus API 大多不用迁移；最大的风险是包依赖、进程生命周期与权限。

### systemd/uutils/ABI

- Ubuntu 26.04 的说明已经预告：26.10 的 systemd 260 将移除传统 SysV init 脚本兼容。[官方说明](https://documentation.ubuntu.com/release-notes/26.04/changes-since-previous-interim/)。本仓库没有发现自有 `/etc/init.d` 脚本，优先扫描依赖链与现场用户配置，验证自有 service/timer、`system-update.target`、GDM/SSH socket、Dracut 迁移确认服务。不要误删 `systemd-sysv` 包；它不是“传统 SysV init 脚本”本身。
- 26.10 草案说明默认 coreutils 全部转到 Rust `uutils`，包括先前保留的 `cp/mv/rm`。[26.10 草案](https://documentation.ubuntu.com/release-notes/26.10/)。对 [core preinst](anduinos-core-system/scripts/preinst.sh)、Dracut fallback、安装器与快照管理器的 shell 脚本做 GNU 选项/退出码/原子性差异回归。尤其是 `cp --reflink`、`mv`、`rm`、权限和符号链接语义，不能只跑静态 shellcheck。保留纯 GNU 依赖时应显式依赖并调用适当工具，勿靠环境碰巧安装。
- GTK 4.23/Libadwaita 1.10：回归自有 GTK App（控制面板、外观、安装器、快照、升级 App）的布局、焦点、缩放 125/150/200%、RTL、暗色与 reduced motion；检查 C/Python 扩展是否被新 ABI 或 Python 次版本打断。`oo7` 接替密钥存储相关机制时，实际登录后验证 Wi-Fi/VPN 凭据、Seahorse 和账户密钥，不假定数据迁移成功。[GNOME 51 开发者说明](https://release.gnome.org/51/developers/)。
- 图形与硬件：GNOME 51 不再支持旧 NVIDIA 驱动接口。[GNOME 51](https://release.gnome.org/51/)。用新驱动/旧卡、Intel/AMD/NVIDIA 混合显卡矩阵测 GDM、Wayland、Xwayland、外接显示器、HDR、休眠/恢复、录屏；给不兼容 GPU 明确最低要求或保留经验证的替代登录路径。

## 6. GRUB、内核、Dracut、Btrfs 与安装器

### 6.1 启动链的决策树（必须用二进制事实回答）

1. 从 Stonking staging 仓库取实际 `grub-efi-amd64-signed` / `grub-efi-arm64-signed`、shim、`grub-efi-*-bin`；核对签名映像是否能读取 Btrfs 与主题资源，制作 **Secure Boot 真正开启** 的双架构 QEMU 冷启动、升级后冷启动及回滚启动证据。提案还涉及 PNG/JPEG；[anduinos-grub-style](anduinos-grub-style/anduinos-grub-style.aosproj) 的图像/字体显示也要实测。查看包版本号或裸 GRUB 模块目录不足以证明签名映像内置了什么。
2. **若签名 GRUB 仍可读 Btrfs**：保持现有 `/boot` 在 `@root` 的 ABI，增加 2.0→2.1 升级后的跨内核/快照回滚矩阵；持续监视 Ubuntu 后续安全更新。只有经过真实 shim→GRUB→kernel 链的测试才可解除阻断。
3. **若签名 GRUB 不能读 Btrfs**：先冻结 2.0 Btrfs 用户升级，不要留下不可启动系统。新装可评估 ext4 `/boot` + Btrfs root，或签名 UKI/其他可验证路径；但这改变 `BTRFS-DESIGN.md` 中“`/boot` 与 `@root` 同回滚边界”的 ABI。必须由快照管理器提供外部化、哈希绑定的 kernel/initrd/boot entry 事务和断电回退，安装器、GRUB 主题、工厂重置一起重设计。**不能简单增加 ext4 `/boot` 分区后仍宣称系统快照可完整回滚。** 这项若无法在窗口内验证，就延期 2.1，而不是取消 Secure Boot 保障。

### 6.2 内核/Dracut 的独立升级门槛

- `linux-generic-hwe-26.04` 在 2.0 是明确选择；2.1 默认应由 Stonking 官方支持的 `linux-generic` 或经审定元包持续提供 kernel+headers。最终 ABI 以 Ubuntu 冻结后仓库为准，不把搜索结果中的 7.x 小版本视为承诺。DKMS、MOK、NVIDIA/VirtualBox/第三方模块必须和实际运行内核及签名策略匹配。
- 保留现有 [Dracut 迁移](anduinos-dracut-migration/DESIGN.md) 的独立 fallback、不覆盖活跃 initrd、原子替换 GRUB 配置、`lsinitrd` 验证、不同 boot-id 确认。Stonking 上重新跑完整多内核与断电注入测试。新的发行版升级器要在**升级前确认当前 2.0 Dracut 迁移已完成**；不允许把旧 initramfs-tools→Dracut 迁移与跨发行版升级叠成一个未经测试事务。
- installed initrd 不应含 Live-only `dmsquash-live` 模块；Live ISO 必须能以 Stonking kernel/Dracut 解压、保留安装源，再由安装器清理 Live 包。`anduinos-live-layers`、`anduinos-live-settings` 和安装器三方同测。Btrfs 回滚用受保护、与目标版本绑定的恢复工件，不能执行快照里可能较旧的恢复代码。[恢复设计](anduinos-btrfs-snapshots-manager/docs/RECOVERY-SCOPE.md)。

### 6.3 安装器 2.1 待办

现有安装器的 [VM-TESTING](anduinos-installer-beta/VM-TESTING.md) 已有 amd64 BIOS、amd64/arm64 UEFI、Secure Boot 开/关、Btrfs/ext4 十行基础矩阵；另有 coexistence/手工分区矩阵。2.1 需要以 **实际 Stonking ISO** 全部重跑，覆盖 ESP 复用/新建、Windows 共存、NTFS 缩容、Btrfs 压缩、不同 Swap 容量、自动/手工分支汇合、28 语言、离线/在线、MOK 入库、异常中断。每行留 ISO SHA-256、磁盘镜像、串口日志、截图、目标系统启动证据，不能只接受模拟模式通过。

代码审计重点是安装器的源套件推导和 package check、写入目标的 `base-files`、内核/Dracut/GRUB 安装顺序、分区几何、Secure Boot 状态探测，以及 `InstallPlan` 前端与特权执行器的 schema 一致。2.1 要避免把 2.0 的 boot 布局假设固化在安装器新目标和升级老目标之间。`TODO_LUKS.md` 的加密仍属后续路线图，不在本次 UI 放一个未实现的选项。若最终将 `installer-beta` 改为稳定命名，需单独审查包替代、Polkit action、desktop entry 和 2.0 用户安装状态，而不是只更名。

## 7. 老用户从 2.0 安全到 2.1：明确分阶段承诺

### 7.1 与“应用包更新”严格区分

2.0 的 GNOME Software/PackageKit 更新 2.0 包，只在 resolute 仓库内完成；2.0→2.1 涉及 Ubuntu 主仓库、AnduinOS `-addon/-webapps` 仓库、`base-files`、内核/引导、GNOME 扩展和大量 ABI。正常 `apt upgrade`/`full-upgrade`、GNOME Software“全部更新”、单纯安装一个 2.1 `anduinos-desktop` 包都**不是**受支持的跨版本升级。Ubuntu 的标准工具会禁用第三方源；当前 [`anduinos-desktop`](anduinos-desktop/anduinos-desktop.aosproj) 又与其冲突，必须先验证并改变该关系，或构建自己的受限升级事务。

### 7.2 资格检查（只读；任何一项无法证明则不启用按钮）

- 只接受 `ID=anduinos`、`VERSION_CODENAME=resolute`、版本属 2.0、架构 amd64/arm64、官方可验证包源；拒绝 Ubuntu/自制混合系统和跳级降级。
- `dpkg --audit` 空、无正在执行的 APT/dpkg/PackageKit 事务、无关键包 hold、2.0 已更新到要求的最低修复版本、Dracut boot-confirmed、当前运行内核/initrd/GRUB/ESP 一致；不要仅信一个 marker。EFI/NVRAM/ESP 可写且有空间；Btrfs `@root`/`@home` 布局可识别；ext4 走另外的备份/恢复契约。
- APT 源签名有效；目标仓库索引全部可下载且目标 amd64/arm64 包图封闭。模拟目标事务不得移除 AnduinOS 桌面、GDM、网络、内核、shim/GRUB、Dracut、用户数据和快照管理器等保护集合；必须给出待删第三方包与 PPA 解释，不静默禁用用户仓库。
- 足够下载、根、`/boot`、ESP 空间；笔记本电源/电量合理；用户已做独立备份并确认。Btrfs 预快照是额外保护，不代替外部备份，且 ext4 没有同等自动回滚；`@home` 快照也不是云备份。
- Secure Boot、Btrfs、Windows 共存、手工布局、加密、外接盘、第三方 DKMS 等只开放在已通过相应矩阵的配置。未知布局不能“尽力升级”。只提供明确可恢复的手动迁移说明。

### 7.3 事务与恢复（需在 VM 中先证明）

推荐先评估**使用 Ubuntu release-upgrader 作为底层迁移引擎并加 AnduinOS 专有前/后置校验**，而不是从零写 `apt` 依赖求解器。原型必须证明：处理 `ID=anduinos` 的检测、不会把 AnduinOS 源禁用/错改、能按 Stonking 包仓库顺序取到品牌包、迁移 conffiles、保留用户的 dconf/扩展状态，重启后能明确证明已运行 2.1。若这些条件不成立，可设计自己的离线协调器，但这至少是额外 20–40 人日级项目，需要与 PackageKit `/system-update` 互斥、持久状态、重入/断电处理和包管理日志。[systemd 离线更新约定](https://www.freedesktop.org/software/systemd/man/latest/systemd.offline-updates.html)、[现有 Dracut 事务设计](anduinos-dracut-migration/DESIGN.md)。

升级事务推荐分为：`eligible` → `downloaded` → `rehearsed` → `armed` → `packages-switching` → `boot-artifacts-verified` → `reboot-pending` → `boot-confirmed`。只有前四阶段允许无损取消；包事务开始后失败必须保留诊断和可启动 fallback，不能用“升级失败，重新再试”掩盖 dpkg 半配置状态。恢复范围包括 root、包数据库、内核/initrd/GRUB/ESP 一致性和用户 Home 保留；单纯 Btrfs root snapshot 不等于跨发行版全机回退。对 ext4 应明确使用外部备份/Live USB 修复流程，除非另行开发等价的可验证回退。

### 7.4 最少验证矩阵

| 入口系统 | 启动/磁盘 | 必测结果 |
|---|---|---|
| 2.0 最新稳定点版本 | amd64 UEFI Secure Boot+Btrfs、ext4 | 先更新后升级，MOK/DKMS、冷启动、登录、旧 Home 保留、升级 App 状态正确 |
| 2.0 较早但仍受支持点版本 | 与上同 | 先完成 2.0 内 Dracut 迁移和包更新，再升级；不能合并两个事务 |
| 2.0 | amd64 BIOS；arm64 UEFI Secure Boot 开/关 | GRUB/Dracut/架构工件一致、失败可启动旧系统 |
| 2.0 与 Windows 共存 | 复用 ESP、新 ESP、NTFS 缩容过的布局 | Windows 文件不改、引导链均存在、固件启动项可恢复 |
| 2.0 用户自定义 | 第三方源、DKMS、空间不足、掉电、网络中断、锁包 | 有解释的拒绝/暂停，绝不静默损坏或越过失败 |

每个成功案例至少检查 `/etc/os-release`、`apt-cache policy`、`dpkg --audit`、`uname -r`、`lsinitrd`、`mokutil --sb-state`、ESP 文件哈希、GNOME 登录、D-Bus 系统/用户总线、应用启动、扩展日志、Flatpak portal、快照/恢复和 Windows 启动。故障注入在模拟器和真实包仓库下各跑一次；升级结果需要和同版全新安装比较包集差异。通过率不是“测试脚本退出码为 0”，而是能再次冷启动且验证所有受保护状态。

## 8. 新 App“系统升级”：2.0 LTS / 2.1 Latest / 将来 2.2

### 8.1 产品行为与文案

新增独立 `anduinos-system-upgrade` 包，**同时发布 Resolute 与 Stonking 版本**，由 [desktop-apps](anduinos-desktop-apps/anduinos-desktop-apps.aosproj) 推荐安装，控制面板在“系统”中显示入口。2.0 必须通过普通 2.0 包更新先收到这个 App，升级 App 本身不可要求先升级到 2.1。入口不应暗示 GNOME Software 的“应用更新”就是系统跨版本升级。

| 当前系统 | 默认显示 | 操作 |
|---|---|---|
| 2.0 LTS / resolute | “当前：AnduinOS 2.0 LTS”；“可选：AnduinOS 2.1 Latest（非 LTS）”；提示较短支持周期、需要更频繁升级，且 2.0→2.1 **不可降级** | 只有资格和发布闸门都通过才启用“升级到 2.1”；否则显示具体原因、备份/新装指引 |
| 2.1 Latest / stonking，2.2 未发布 | “已是最新版”；保留“非 LTS、支持期较短”说明 | 不显示 2.0 降级按钮；正常包更新仍由软件商店负责 |
| 2.1，在 2.2/Ubuntu 27.04 发布并验收后 | “可升级到 2.2 Latest”；2.1 支持剩余期按已发布政策显示 | 通过新边 `2.1 → 2.2` 升级；绝不通过简单改“Latest”字符串启动事务 |
| 2.0，在 2.2 发布后 | 仍可选择维持 LTS；若 2.0→2.2 未单独验收，只能显示已验收的下一跳 2.1，再进行 2.1→2.2 | 不自动跳版本或跨过某一版；明确告知可能需两次升级 |

“Latest”描述最新功能，**不是**“更推荐、更稳定、支持更久”。显示 2.0 LTS 与 2.1 Latest 的对比卡片：底层 Ubuntu 版本、GNOME 版本、支持类型、预期更新频率、已验证升级路径、潜在不可逆性。若产品未来决定 2.0 用户默认留在 LTS，则不要用红色升级提醒/倒计时制造压力。支持截止日只能从已发布政策与后端源状态得出；目前不可伪造一个 AnduinOS EOL 日期。

### 8.2 可演进的数据模型，不硬编码“2.1 是永远最新版”

建议独立的、随 AnduinOS **APT 签名包**发布的根拥有 release catalog（例如 `/usr/share/anduinos-system-upgrade/releases.json`）；不把浏览器下载的任意 JSON 当成授权命令。catalog schema 有单调版本、release ID、Ubuntu codename、track、可选展示文案、升级边及最低源版本/验收门槛。发布 2.2 时，向 catalog 加入 2.2 节点及 **通过测试的** `2.1→2.2` 边，更新 2.0/2.1 两分支包。路径查找只用于显示下一跳，**不自动连跑多跳**。root helper 独立重读 catalog、/etc/os-release 和当前 dpkg 状态，不能信任 UI 缓存。

```json
{
  "schema": 1,
  "releases": {
    "2.0": {"codename": "resolute", "track": "lts", "label": "AnduinOS 2.0 LTS"},
    "2.1": {"codename": "stonking", "track": "latest", "label": "AnduinOS 2.1 Latest"}
  },
  "latest": "2.1",
  "edges": [
    {"from": "2.0", "to": "2.1", "policy": "gated", "minimum_source": "2.0.x"}
  ]
}
```

上例 `minimum_source` 是占位，实施时必须改成经过测试的真实 Debian 包版本及资格规则；`policy: gated` **不表示自动开放**。未来加入 `2.2`、`latest: 2.2` 和新边，无需改变“按钮如何决定下一跳”的核心代码。2.0→2.2 若无直接边，不得自己推导。

下面是建议的**只读展示层核心算法草案**，不是可运行的升级执行器。输入前须验证 schema、节点唯一性、无环、release ID/代码名白名单，真实系统身份应由 root helper 重新读取。

```python
def next_hop(current: str, catalog: dict) -> str | None:
    latest = catalog["latest"]
    if current not in catalog["releases"] or latest not in catalog["releases"]:
        raise ValueError("unknown release")
    if current == latest:
        return None                   # 最新版；绝无降级选项

    edges = {edge["from"]: edge["to"] for edge in catalog["edges"]}
    seen = set()
    cursor = current
    first = None
    while cursor != latest:
        if cursor in seen or cursor not in edges:
            return None               # 无已发布、已验证路径
        seen.add(cursor)
        cursor = edges[cursor]
        if cursor not in catalog["releases"]:
            raise ValueError("broken release catalog")
        if first is None:
            first = cursor
    return first                       # 每次只提供一跳
```

若 catalog 将来出现多条路径，改为显式选择“已验收边”的有向图，不允许字典覆盖重复 `from`；schema 单调升级并加入验收签名/发行控制。后台资格判断另有 `available / blocked(reason) / unsupported / up-to-date / in-progress / failed-needs-repair` 状态，前端不可由版本号自行推断 `available`。

### 8.3 安全边界和打包草案

采用安装器已有的“非特权 GTK 前端 + 窄 Polkit helper”模型。`anduinos-system-upgrade` 包可携带 `GTK4/libadwaita` UI、AppStream desktop/icon、`/usr/libexec/anduinos-system-upgrade-helper`、Polkit action、catalog、28 语言 PO、单元/GUI 测试；控制面板只负责启动 desktop entry。helper 接受固定动作 `status`, `preflight`, `prepare`, `start`, `cancel-before-arm`，**不接受包名、shell 文本、路径或源 URL**。所有源地址来自根拥有且 APT 签名验证的配置；事务计划有 schema、目标 suite、当前/目标元数据摘要、校验期和事务 ID。按钮点击时需要新的资格检测，不能使用几小时前的 UI 状态。

关键特权边界伪代码：

```python
def start_upgrade(request, installed_catalog, live_system):
    if request.action != "start" or request.target not in installed_catalog.releases:
        raise Refused("unsupported request")
    edge = installed_catalog.exact_edge(live_system.release, request.target)
    if edge is None or not edge.release_gate_enabled:
        raise Refused("transition not released")
    report = preflight_again(live_system, edge)   # root 重新探测，非 UI 传入
    if not report.all_required_checks_passed:
        raise Refused(report.public_reason)
    plan = build_fixed_transition(edge, report)   # 不接受 UI 提供的命令
    journal_plan_atomically(plan)                  # fsync + rename，独占锁
    return arm_verified_engine(plan)               # 事务/断电语义另经 VM 证明
```

优先做**只读 App + 2.1 最新状态**和**2.0 资格检测/禁用原因**；真正 `start` 的 release gate 可以保持关闭，直到第 7 节的升级矩阵签字通过。不能在 2.0 包里埋一个未经验证却可点击的危险按钮。也不要仅通过 Polkit 限制按钮就认为状态安全：特权 helper 必须独立再校验源、磁盘身份、包图和 boot 证据。所有用户输入和日志均不得包含账户密码、API key、Wi-Fi/VPN secrets、MOK 私钥。

### 8.4 测试和本地化

- 单元：2.0 当前/2.1 最新/2.2 出现/未知版/缺边/环/重复边/降级请求/过期 catalog；2.0 上 catalog 不可用时清楚显示“无法检查”，而不是“已是最新版”。
- 集成：非特权 UI 无法直接变更 APT 源；Polkit 拒绝不触发状态变动；root 重新发现状态变化会拒绝旧计划；断电/锁文件/重启后状态可解释；和 PackageKit 常规更新互斥。
- UX：28 语言、长字符串、中文/德语/阿拉伯语 RTL、读屏、键盘操作、弱网/离线、电量不足、未知 EOL。所有“非 LTS/不能降级/备份数据”的措辞要与实际行为一致。

## 9. 硬件、用户数据与发行工程

### 真机覆盖面

VM 是破坏性存储、BIOS/UEFI、Secure Boot 和断电注入的基础，但不能覆盖全部硬件。至少有：Intel/AMD/NVIDIA（含老 NVIDIA 与混合显卡）、一台 arm64 UEFI/ACPI、NVMe/SATA、不同 4K/HiDPI/多显示器、Wi-Fi/蓝牙音频、Webcam/麦克风、打印/扫描、触摸板/指纹、休眠唤醒，以及 BitLocker/Windows 双启动机器。记录固件版本、实际 GPU 驱动、`mokutil` 结果和 ISO 哈希。GNOME 51 对旧 NVIDIA 接口的处理使“能开 GDM”不等于“所有旧卡有完整 3D 桌面”。

### 用户状态迁移与可观察性

测试旧用户 Home 的 dconf、扩展配置、布局/主题、输入法/IBus/Rime、默认应用、键盘快捷键、GNOME Keyring、账户自动登录/SSH/无密码 sudo、Flatpak 权限和 MIME 关联。包提供的默认值更新不得覆盖用户设置；GNOME 51 不再加载的扩展需要清楚提示，必要时安全禁用但保留配置供将来重新启用。比较全新装与升级系统的核心文件和已安装包，列出预期差异，不能盲目追求完全相同。

所有失败路径向用户提供可复制的**脱敏**诊断包：发行版本、包状态、事务 ID、内核/启动证据、安装器/升级器阶段、被拒绝的检查项；不要收集 Home 内容、令牌、密码、私钥。`@log` 在 Btrfs 回滚外，正适合保留诊断，但日志也须最小化。导出前预览内容并取得用户明确同意，不自动上传。

### ISO/仓库/供应链门槛

构建与发布记录要包含：本仓库、`AnduinOS-Container`、`AnduinOS-2` 三者的源提交和依赖锁定、两架构包列表与版本、APT `Release/InRelease` 签名、ISO SHA-256、生成器/内核/GRUB/shim/扩展来源、许可审计、AppStream 桌面项、SBOM 或包清单。先在 staging 仓库完成封闭求解和镜像冷启动，再发布生产仓库；确认 `unattended-upgrades` 能继续收到 2.0 LTS 安全更新，不把 2.0 用户意外切到 2.1。新 `stonking-addon` 的 origin/pinning、正式/dev 源及 keyring 轮换必须一起测。[现有 APT 策略](anduinos-apt-config/assets/52anduinos-unattended-upgrades)。

不建议用 Docker 容器代替本次核心验证。容器适合快速查询 Stonking 包存在性、依赖和单元测试，却不能证明 Secure Boot、GNOME session、Dracut、硬件或冷启动；本调查已经用 Ubuntu 官方包索引确认了 `impression`、broker 等关键包。真正的发布证据必须来自 ISO/QEMU/真机。

## 10. 发布门槛与明确暂缓项

| Gate | 必须提供的可核验证据 | 失败处理 |
|---|---|---|
| G1 包图 | 两架构 Stonking desktop 与 Installer 的 staging 安装、无意外卸载/混套件、签名源与 keyring 验证 | 不发布 ISO |
| G2 启动 | 最终签名 shim/GRUB/Btrfs 或替代布局在 Secure Boot/BIOS/arm64 的冷启动、回滚、双内核与断电记录 | 不发布 2.1 |
| G3 桌面 | GNOME 51 登录/锁屏/登出、默认扩展、GDM、Wayland/NVIDIA/portal、28 语言基本操作 | 高影响缺陷阻断；可选扩展可禁用并公告 |
| G4 安装 | 实际 ISO 的基础十行 VM、共存/手工分区额外矩阵、真机代表性安装、ESP/Windows 不被破坏 | 阻断相关安装选项或延期 |
| G5 系统升级 App | 2.0/2.1 均能安装启动；正确标注 LTS/Latest、非 LTS 支持风险、最新状态与禁止降级；28 语言 | 不发布声称具备升级能力的 App |
| G6 2.0→2.1 事务 | 第 7 节每一类已开放配置通过包事务、重启、断电、故障恢复与用户数据检查 | **保持升级按钮禁用**，不必自动阻断经过验证的 2.1 全新安装 |
| G7 发布 | ISO 校验、仓库签名、更新通道、发行说明、EOL/已知问题、应急撤回方案 | 不向大众开放 |

明确不混入 2.1 首发的“画饼”：LUKS/TPM/FIDO2 全盘加密新 UX、原子式全系统 A/B 更新、无条件跨版本自动回滚、2.0→2.2 直接跳级、新 GNOME App 大规模换血、重写整个安装器、RISC-V 新架构支持。它们可以在路线图里保留研究 spike，但不牺牲本次安全门槛。需要优先建设的长期能力是可复用的发行版关系图、跨版本资格检测、包图差异报告、可重启事务日志和固定 VM 升级基线；这些在 2.2/27.04 会直接复用。

## 11. 下一步可分派的第一批任务

1. **启动负责人：** 在 Stonking Beta 包冻结后检查签名 GRUB 的 Btrfs/主题能力，交付双架构 Secure Boot 冷启动证据；两天内给“保持当前布局/必须设计替代方案”的决策。
2. **打包负责人：** 形成 78 包的 suite/架构/依赖差异表；优先打通 `base-files`、APT 源、core、desktop、installer、session、扩展与 live 组件，交付 staging `apt` 求解报告。
3. **扩展负责人：** 先修 Voice Typing 的两个确定移除 API、ArcMenu 的 `pointerWatcher`，并对默认扩展出 GNOME 51 实测矩阵；缺原生支持者提供补丁或清楚禁用。
4. **升级负责人：** 在一次性可销毁的 2.0 VM 上验证 release-upgrader 与 AnduinOS ID/仓库/品牌包是否兼容，报告哪些步骤可复用、哪些需要自有协调器；**不得在主机上试升级**。
5. **App/UX 负责人：** 做 `anduinos-system-upgrade` 的 catalog、2.0/2.1 只读状态页与 28 语言文案，先让 `start` 默认关闭；同时做控制面板入口与 accessibility 检查。
6. **QA/发行负责人：** 准备最终 ISO 的基础十行及共存/手工扩展矩阵、真机清单、断电注入、发布日志模板与阻断项面板，每一条以可复现工件关闭。

**最终决策规则：** 2.1 新装、2.0→2.1 原地升级、2.1→未来 2.2 是三个分别验收的产品承诺。不要因为时间紧把它们当作同一项“升级已支持”。
