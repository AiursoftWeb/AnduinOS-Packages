"""Shared catalog of Control Panel topics and their search metadata."""

from __future__ import annotations

from dataclasses import dataclass
import gettext
from functools import lru_cache
import unicodedata


LOCALE_DIR = "/usr/share/locale"
TEXT_DOMAIN = "anduinos-control-panel"
gettext.bindtextdomain(TEXT_DOMAIN, LOCALE_DIR)


def _(message: str) -> str:
    return gettext.dgettext(TEXT_DOMAIN, message)


@dataclass(frozen=True)
class ControlPanelTopic:
    """One stable, searchable destination exposed by the Control Panel."""

    identifier: str
    category: str
    title: str
    description: str
    keywords: tuple[str, ...]
    icon: str
    command: tuple[str, ...] = ()
    handler: str = ""
    install_package: str = ""
    availability_command: str = ""


def _topic(
    identifier: str,
    category: str,
    title: str,
    description: str,
    keywords: tuple[str, ...],
    icon: str,
    *,
    command: tuple[str, ...] = (),
    handler: str = "",
    install_package: str = "",
    availability_command: str = "",
) -> ControlPanelTopic:
    return ControlPanelTopic(
        identifier=identifier,
        category=category,
        title=_(title),
        description=_(description),
        keywords=keywords,
        icon=icon,
        command=command,
        handler=handler,
        install_package=install_package,
        availability_command=availability_command,
    )


@lru_cache(maxsize=1)
def topics() -> tuple[ControlPanelTopic, ...]:
    """Return the canonical topic catalog in its preferred search order."""

    return (
        _topic(
            "system.settings",
            "system",
            "System Settings",
            "Display, sound, power, privacy, and more",
            ("settings", "display", "sound", "power", "privacy", "系统设置"),
            "preferences-system",
            command=("gnome-control-center",),
        ),
        _topic(
            "system.startup-boot",
            "system",
            "Startup and Boot",
            "Change the boot menu wait time",
            ("boot", "startup", "grub", "timeout", "启动", "引导", "延时"),
            "system-reboot-symbolic",
            handler="boot-settings",
        ),
        _topic(
            "system.virtual-memory",
            "system",
            "Virtual Memory Settings",
            "Configure Zram, Zswap, swap, and memory pressure",
            (
                "swap",
                "zram",
                "zswap",
                "swappiness",
                "memory",
                "virtual memory",
                "ram",
                "虚拟内存",
                "交换空间",
                "内存",
            ),
            "com.anduinos.swapcontrol",
            command=("swapcontrol-gtk",),
        ),
        _topic(
            "security.secure-boot",
            "security",
            "Secure Boot Status",
            "Inspect firmware trust and signed drivers",
            ("secure boot", "uefi", "mok", "firmware", "安全启动", "固件"),
            "security-high-symbolic",
            command=("anduinos-driver-center", "--page", "secure-boot"),
        ),
        _topic(
            "security.passwords-keys",
            "security",
            "Passwords and Keys",
            "Manage passwords, encryption keys, and certificates",
            ("seahorse", "password", "key", "certificate", "密码", "密钥", "证书"),
            "org.gnome.seahorse.Application",
            command=("seahorse",),
            availability_command="seahorse",
        ),
        _topic(
            "network.firewall",
            "network",
            "Firewall",
            "Review connections, rules, and network protection",
            ("firewall", "ufw", "network", "security", "防火墙", "网络", "安全"),
            "com.anduinos.ufwall",
            command=("ufwall-gtk",),
        ),
        _topic(
            "network.advanced",
            "network",
            "Advanced Network Configuration",
            "Configure NetworkManager connection profiles",
            (
                "network",
                "networkmanager",
                "nm-connection-editor",
                "connection",
                "wifi",
                "ethernet",
                "vpn",
                "高级网络",
                "网络连接",
            ),
            "preferences-system-network",
            command=("nm-connection-editor",),
            install_package="network-manager-gnome",
        ),
        _topic(
            "accounts.users",
            "accounts",
            "User Account Settings",
            "Manage users, passwords, and account details",
            ("user", "account", "password", "login", "用户", "账户", "密码"),
            "system-users",
            command=("gnome-control-center", "system", "users"),
        ),
        _topic(
            "accounts.yubikey",
            "accounts",
            "YubiKey Settings",
            "Configure sign-in, sudo, SSH keys, and Git signing",
            ("yubikey", "fido2", "u2f", "sudo", "ssh", "git signing", "安全密钥"),
            "com.anduinos.yubikeymanager",
            command=("anduinos-yubikey-manager",),
        ),
        _topic(
            "accessibility.voice-typing",
            "accessibility",
            "Voice Typing",
            "Configure private, offline speech-to-text",
            ("voice", "typing", "speech", "microphone", "whisper", "语音输入", "语音识别"),
            "audio-input-microphone",
            handler="voice-typing",
        ),
        _topic(
            "hardware.drivers",
            "hardware",
            "Driver Center",
            "Graphics, audio, printers, controllers, and firmware",
            ("driver", "firmware", "nvidia", "graphics", "audio", "驱动", "固件", "显卡"),
            "com.anduinos.DriverCenter",
            command=("anduinos-driver-center",),
        ),
        _topic(
            "hardware.printers",
            "hardware",
            "Printers",
            "Add, remove, and configure printers",
            ("printer", "printing", "cups", "打印机", "打印"),
            "printer-symbolic",
            command=("gnome-control-center", "printers"),
        ),
        _topic(
            "hardware.scanners",
            "hardware",
            "Scanners",
            "Scan documents and add or select a scanner",
            ("scanner", "scan", "sane", "simple-scan", "扫描仪", "扫描"),
            "scanner-symbolic",
            command=("simple-scan",),
            install_package="simple-scan",
        ),
        _topic(
            "appearance.anduinos",
            "appearance",
            "AnduinOS Appearance Settings",
            "Configure the taskbar, panel widgets, and desktop",
            ("appearance", "theme", "taskbar", "panel", "desktop", "外观", "主题", "任务栏"),
            "anduinos-appearance",
            command=("anduinos-appearance",),
        ),
        _topic(
            "appearance.wallpaper",
            "appearance",
            "Wallpaper and Accent Color",
            "Choose the desktop background, style, and accent color",
            ("wallpaper", "background", "accent", "color", "壁纸", "背景", "强调色"),
            "preferences-desktop-wallpaper",
            command=("gnome-control-center", "background"),
        ),
        _topic(
            "programs.uninstall",
            "programs",
            "Uninstall Applications",
            "Review and remove installed applications",
            ("uninstall", "remove", "application", "software", "卸载", "删除", "应用"),
            "org.gnome.Software",
            command=("gnome-software", "--mode=installed"),
        ),
        _topic(
            "programs.permissions",
            "programs",
            "Permission Settings",
            "Manage application permissions with Flatseal",
            ("permission", "flatpak", "flatseal", "sandbox", "权限", "沙盒"),
            "com.github.tchx84.Flatseal",
            handler="flatseal",
        ),
        _topic(
            "ai.on-device",
            "ai",
            "On-device AI",
            "Configure private AI features that run on this device",
            ("ai", "on-device", "local ai", "why", "人工智能", "本地 AI", "设备端 AI"),
            "applications-science",
            handler="on-device-ai",
        ),
        _topic(
            "compatibility.windows",
            "compatibility",
            "Configure Bottles",
            "Run Windows applications in compatibility environments",
            ("windows", "wine", "bottles", "exe", "compatibility", "兼容层", "Windows 应用"),
            "com.usebottles.bottles",
            handler="bottles",
        ),
        _topic(
            "recovery.snapshots",
            "recovery",
            "System Snapshots",
            "Create, browse, and roll back system snapshots",
            ("snapshot", "btrfs", "rollback", "restore", "快照", "回滚", "恢复"),
            "org.anduinos.BtrfsSnapshotsManager",
            command=("anduinos-btrfs-snapshots-manager",),
        ),
        _topic(
            "recovery.backup",
            "recovery",
            "Back Up Home Folder",
            "Protect personal files with Deja Dup backups",
            ("backup", "deja dup", "restore", "home", "备份", "恢复", "主目录"),
            "org.gnome.DejaDup",
            handler="backup",
        ),
    )


@lru_cache(maxsize=1)
def topic_map() -> dict[str, ControlPanelTopic]:
    return {topic.identifier: topic for topic in topics()}


def get_topic(identifier: str) -> ControlPanelTopic | None:
    return topic_map().get(identifier)


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _score(topic: ControlPanelTopic, query: str) -> int | None:
    normalized_query = _normalize(query)
    if not normalized_query:
        return None

    title = _normalize(topic.title)
    description = _normalize(topic.description)
    keywords = tuple(_normalize(keyword) for keyword in topic.keywords)
    terms = normalized_query.split()
    haystack = " ".join((title, description, *keywords))
    if any(term not in haystack for term in terms):
        return None

    if title == normalized_query:
        return 1000
    if title.startswith(normalized_query):
        return 900
    if normalized_query in keywords:
        return 850
    if any(keyword.startswith(normalized_query) for keyword in keywords):
        return 800
    if normalized_query in title:
        return 700

    exact_keyword_terms = sum(term in keywords for term in terms)
    title_terms = sum(term in title for term in terms)
    return 400 + exact_keyword_terms * 40 + title_terms * 20


def search_topics(
    terms: tuple[str, ...] | list[str],
    candidates: tuple[str, ...] | list[str] | None = None,
) -> list[ControlPanelTopic]:
    """Return matching topics, ranked deterministically for GNOME Shell."""

    query = " ".join(terms)
    allowed = set(candidates) if candidates is not None else None
    ranked: list[tuple[int, int, ControlPanelTopic]] = []
    for index, topic in enumerate(topics()):
        if allowed is not None and topic.identifier not in allowed:
            continue
        score = _score(topic, query)
        if score is not None:
            ranked.append((-score, index, topic))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked]
