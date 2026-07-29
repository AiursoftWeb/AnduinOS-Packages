"""Language definitions and translations for the AnduinOS installer.

This module is the single source of truth for all supported languages,
replacing the ubiquity-languagelist / generate-languagelist-data.py
pipeline.  No external files, no compilation step — just import it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re


# ---------------------------------------------------------------------------
# Language metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Language:
    """A language that the installer supports."""
    code: str           # IETF language tag, e.g. "zh_CN"
    english_name: str   # "Chinese (Simplified)"
    native_name: str    # "中文(简体)"
    locale: str         # "zh_CN.UTF-8"
    keyboard: str       # default physical XKB layout, e.g. "us"


# Sorted alphabetically by English name (matching the existing .data file order).
# Data sourced from anduinos-installer-config/assets/ubiquity-languagelist
# and generate-languagelist-data.py.
LANGUAGES = [
    Language("ar",    "Arabic",                "العربية",              "ar_SA.UTF-8", "ara"),
    # Chinese text input is provided by IBus/Rime. Chinese locales normally
    # use a standard US physical keyboard and must not be mapped from country
    # codes to unrelated XKB layout identifiers.
    Language("zh_CN", "Chinese (Simplified)",  "中文(简体)",           "zh_CN.UTF-8", "us"),
    Language("zh_HK", "Chinese (Hong Kong)",   "中文 (香港)",          "zh_HK.UTF-8", "us"),
    Language("zh_TW", "Chinese (Traditional)", "中文(繁體)",           "zh_TW.UTF-8", "us"),
    Language("da",    "Danish",                "Dansk",                "da_DK.UTF-8", "dk"),
    Language("nl",    "Dutch",                 "Nederlands",           "nl_NL.UTF-8", "nl"),
    Language("en",    "English",               "English",              "en_US.UTF-8", "us"),
    Language("en_GB", "English (United Kingdom)", "English (United Kingdom)", "en_GB.UTF-8", "gb"),
    Language("fi",    "Finnish",               "Suomi",                "fi_FI.UTF-8", "fi"),
    Language("fr",    "French",                "Français",             "fr_FR.UTF-8", "fr"),
    Language("de",    "German",                "Deutsch",              "de_DE.UTF-8", "de"),
    Language("el",    "Greek",                 "Ελληνικά",             "el_GR.UTF-8", "gr"),
    Language("hi",    "Hindi",                 "हिन्दी",               "hi_IN.UTF-8", "in"),
    Language("id",    "Indonesian",            "Bahasa Indonesia",     "id_ID.UTF-8", "id"),
    Language("it",    "Italian",               "Italiano",             "it_IT.UTF-8", "it"),
    Language("ja",    "Japanese",              "日本語",               "ja_JP.UTF-8", "jp"),
    Language("ko",    "Korean",                "한국어",               "ko_KR.UTF-8", "kr"),
    Language("pl",    "Polish",                "Polski",               "pl_PL.UTF-8", "pl"),
    Language("pt",    "Portuguese",            "Português",            "pt_PT.UTF-8", "pt"),
    Language("pt_BR", "Portuguese (Brazil)",   "Português do Brasil",  "pt_BR.UTF-8", "br"),
    Language("ro",    "Romanian",              "Română",               "ro_RO.UTF-8", "ro"),
    Language("ru",    "Russian",               "Русский",              "ru_RU.UTF-8", "ru"),
    Language("es",    "Spanish",               "Español",              "es_ES.UTF-8", "es"),
    Language("sv",    "Swedish",               "Svenska",              "sv_SE.UTF-8", "se"),
    Language("th",    "Thai",                  "ภาษาไทย",              "th_TH.UTF-8", "th"),
    Language("tr",    "Turkish",               "Türkçe",               "tr_TR.UTF-8", "tr"),
    Language("uk",    "Ukrainian",             "Українська",           "uk_UA.UTF-8", "ua"),
    Language("vi",    "Vietnamese",            "Tiếng Việt",           "vi_VN.UTF-8", "vn"),
]

# Installer-owned defaults. These are intentionally keyed by supported
# language/region rather than inferred from the live machine's network.
DEFAULT_TIMEZONES = {
    "ar": "Asia/Riyadh",
    "zh_CN": "Asia/Shanghai",
    "zh_HK": "Asia/Hong_Kong",
    "zh_TW": "Asia/Taipei",
    "da": "Europe/Copenhagen",
    "nl": "Europe/Amsterdam",
    "en": "America/New_York",
    "en_GB": "Europe/London",
    "fi": "Europe/Helsinki",
    "fr": "Europe/Paris",
    "de": "Europe/Berlin",
    "el": "Europe/Athens",
    "hi": "Asia/Kolkata",
    "id": "Asia/Jakarta",
    "it": "Europe/Rome",
    "ja": "Asia/Tokyo",
    "ko": "Asia/Seoul",
    "pl": "Europe/Warsaw",
    "pt": "Europe/Lisbon",
    "pt_BR": "America/Sao_Paulo",
    "ro": "Europe/Bucharest",
    "ru": "Europe/Moscow",
    "es": "Europe/Madrid",
    "sv": "Europe/Stockholm",
    "th": "Asia/Bangkok",
    "tr": "Europe/Istanbul",
    "uk": "Europe/Kyiv",
    "vi": "Asia/Ho_Chi_Minh",
}


def default_timezone(code: str) -> str:
    """Return the maintained representative timezone for a language."""
    return DEFAULT_TIMEZONES.get(code, "America/New_York")


def is_chinese(code: str) -> bool:
    """Return True if the language code is a Chinese variant."""
    return code.startswith("zh_")


_LANGUAGE_BY_CODE = {language.code: language for language in LANGUAGES}
_LANGUAGE_BY_LOCALE = {
    language.locale.removesuffix(".UTF-8"): language for language in LANGUAGES
}
_LOCALE_ASSIGNMENT_RE = re.compile(
    r"""^\s*(?:export\s+)?(?P<key>LC_ALL|LC_MESSAGES|LANG)\s*=\s*
        (?P<quote>["']?)(?P<value>[^"'#\s]+)(?P=quote)\s*(?:\#.*)?$""",
    re.VERBOSE,
)


def language_for_locale(locale_name: str | None) -> Language | None:
    """Map a locale spelling to one of the installer-supported languages."""
    if not locale_name:
        return None
    normalized = locale_name.strip().replace("-", "_")
    normalized = normalized.split("@", 1)[0].split(".", 1)[0]
    if not normalized or normalized.upper() in {"C", "POSIX"}:
        return None

    # Preserve the explicitly supported regional variants first.
    exact = _LANGUAGE_BY_LOCALE.get(normalized)
    if exact is not None:
        return exact
    exact = _LANGUAGE_BY_CODE.get(normalized)
    if exact is not None:
        return exact

    language, _, territory = normalized.partition("_")
    language = language.lower()
    territory = territory.upper()
    if language == "zh":
        if territory == "HK":
            return _LANGUAGE_BY_CODE["zh_HK"]
        if territory in {"TW", "MO"}:
            return _LANGUAGE_BY_CODE["zh_TW"]
        return _LANGUAGE_BY_CODE["zh_CN"]
    if language == "pt" and territory == "BR":
        return _LANGUAGE_BY_CODE["pt_BR"]
    if language == "en" and territory == "GB":
        return _LANGUAGE_BY_CODE["en_GB"]
    return _LANGUAGE_BY_CODE.get(language)


def detect_system_language(
    environ: Mapping[str, str] | None = None,
    locale_file: Path = Path("/etc/default/locale"),
) -> Language:
    """Detect the Live session language, with a deterministic English fallback."""
    environment = os.environ if environ is None else environ
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        language = language_for_locale(environment.get(key))
        if language is not None:
            return language

    try:
        assignments = _read_locale_assignments(locale_file)
    except OSError:
        assignments = {}
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        language = language_for_locale(assignments.get(key))
        if language is not None:
            return language
    return _LANGUAGE_BY_CODE["en"]


def _read_locale_assignments(path: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LOCALE_ASSIGNMENT_RE.fullmatch(line)
        if match:
            assignments[match.group("key")] = match.group("value")
    return assignments


# Chinese mirror URLs (tried in order; first is the default).
CHINESE_MIRRORS = [
    "https://mirrors.tuna.tsinghua.edu.cn/ubuntu/",
    "https://mirrors.aliyun.com/ubuntu/",
    "https://mirrors.ustc.edu.cn/ubuntu/",
]


# ---------------------------------------------------------------------------
# Translations (dict-based, no GNU gettext dependency)
# ---------------------------------------------------------------------------
#
# Every user-visible string in the installer goes here.  The fallback chain
# is: requested lang → "en" → the key itself.
#
# Strings are grouped by the page/context where they appear.

TRANSLATIONS: dict[str, dict[str, str]] = {}

def _t(lang: str, entries: dict[str, str]) -> None:
    """Register translations for a language."""
    TRANSLATIONS.setdefault(lang, {}).update(entries)


def _(key: str, lang: str) -> str:
    """Look up a translated string, falling back to English then the key."""
    return (TRANSLATIONS.get(lang, {}).get(key)
            or TRANSLATIONS.get("en", {}).get(key)
            or key)


# ── English (base language, always complete) ────────────────────────────

_t("en", {
    # Navigation
    "nav.next":         "Next",
    "nav.back":         "Back",
    "nav.install":      "Install",
    "nav.reboot":       "Reboot Now",
    "nav.reboot_secure_boot": "Restart and Enroll Secure Boot Key",
    "nav.save_log":     "Save Log",
    "nav.close":        "Close",

    # Welcome / Language page
    "welcome.title":    "Welcome to AnduinOS",
    "welcome.subtitle": "Choose your language to begin installation",

    # Keyboard page
    "keyboard.title":   "Keyboard Layout",
    "keyboard.subtitle":"Confirm your keyboard layout",
    "keyboard.test":    "Test your keyboard here…",

    # Updates and drivers page
    "software.title":   "Updates and Drivers",
    "software.subtitle":"Choose optional software to install",
    "software.updates": "Download and install system updates during installation",
    "software.updates_detail": "Requires an Internet connection. The base installation remains available when offline.",
    "software.drivers": "Install third-party drivers for this device",
    "software.drivers_detail": "Some drivers are proprietary or otherwise non-free software.",

    # Disk page
    "disk.title":       "Select Installation Disk",
    "disk.subtitle":    "Choose the disk where AnduinOS will be installed",
    "disk.warning_small": "This disk is smaller than 20 GB. Installation may fail due to insufficient space.",
    "disk.warning_erase": "ALL DATA on the selected disk will be permanently erased.",
    "disk.column_disk": "Disk",
    "disk.column_size": "Size",
    "disk.no_disks":    "No suitable disks found.",
    "disk.live_usb":    "(Live USB — excluded)",

    # User page
    "user.title":       "User Account",
    "user.subtitle":    "Create your user account",
    "user.full_name":   "Full Name",
    "user.username":    "Username",
    "user.password":    "Password",
    "user.confirm_password": "Confirm Password",
    "user.hostname":    "Computer Name",
    "user.pass_too_short": "Password must be at least 6 characters.",
    "user.pass_mismatch": "The two passwords do not match.",
    "user.sudo_without_password": "Do not require a password for sudo commands",
    "user.lockout_heading": "Administrator access would be locked",
    "user.lockout_body": "This account has no login password, but sudo would still require one. Because the root account is locked by default, you would be unable to perform administrator tasks. Set an account password or enable passwordless sudo.",
    "user.sudo_confirm_heading": "Passwordless sudo is unsafe",
    "user.sudo_confirm_body": "Any program running as your user can obtain full administrator privileges without authentication. Your login password still protects sign-in, but it will not protect sudo. Are you sure you want to continue?",
    "user.passwordless_sudo_heading": "This configuration is very unsafe",
    "user.passwordless_sudo_body": "The system will sign in to this account automatically and allow it to obtain full administrator privileges without a password. Anyone with physical access, and any program running as this user, can completely control the system. Use this only for a kiosk, temporary virtual machine, or another controlled environment.",
    "user.return_modify": "Return to Make Changes",
    "user.return_set_password": "Return and Set a Password",
    "user.continue_unsafe": "I Understand the Risk, Continue",
    "user.name_invalid": "Username may only contain lowercase letters, digits, underscores and hyphens.",
    "user.host_invalid": "Computer name contains invalid characters.",

    # Timezone page
    "tz.title":         "Select Timezone",
    "tz.subtitle":      "Choose your location to set the system clock",
    "tz.search":        "Search timezones…",
    "tz.selected":      "Selected timezone",

    # Summary page
    "summary.title":    "Ready to Install",
    "summary.subtitle": "Please review your choices before proceeding",
    "summary.lang":     "Language",
    "summary.keyboard": "Keyboard",
    "summary.disk":     "Target Disk",
    "summary.user":     "User",
    "summary.hostname": "Computer Name",
    "summary.timezone": "Timezone",
    "summary.warning":  "⚠ This will erase ALL data on the selected disk. This action cannot be undone.",

    # Progress page
    "progress.title":   "Installing AnduinOS",
    "progress.subtitle":"Please do not turn off your computer",
    "progress.partition": "Creating partitions…",
    "progress.format":  "Formatting partitions…",
    "progress.mount":   "Mounting filesystems…",
    "progress.copy":    "Copying system files…",
    "progress.config":  "Configuring the system…",
    "progress.grub":    "Installing bootloader…",
    "progress.post":    "Running post-install hooks…",
    "progress.fstab":   "Generating fstab…",
    "progress.locale":  "Generating locales…",
    "progress.user_cfg":"Creating user account…",
    "progress.mirror":  "Configuring APT mirrors…",
    "progress.rime":    "Setting up Chinese input method…",
    "progress.cleanup": "Cleaning up…",
    "progress.done":    "Installation complete!",
    "progress.failed":  "Installation failed",
    "progress.error_detail": "An error occurred during installation. You can save the log for debugging.",

    # Done page
    "done.title":       "Installation Complete",
    "done.subtitle":    "Remove the installation media and restart your computer",
    "done.mok_notice":  "After restart, MOKManager will open. Choose Enroll MOK → Continue → Yes, then enter password 123456.",
    "done.error_title": "Installation Failed",
    "done.error_subtitle": "Something went wrong during the installation",
})


# ── Chinese (Simplified) ─────────────────────────────────────────────────

_t("zh_CN", {
    "nav.next":         "下一步",
    "nav.back":         "上一步",
    "nav.install":      "安装",
    "nav.reboot":       "立即重启",
    "nav.reboot_secure_boot": "立即重启并注册 Secure Boot 密钥",
    "nav.save_log":     "保存日志",
    "nav.close":        "关闭",

    "welcome.title":    "欢迎使用 AnduinOS",
    "welcome.subtitle": "选择您的语言以开始安装",

    "keyboard.title":   "键盘布局",
    "keyboard.subtitle":"确认您的键盘布局",
    "keyboard.test":    "在此测试键盘…",

    "software.title":   "更新与驱动",
    "software.subtitle":"选择安装期间需要的可选软件",
    "software.updates": "在安装过程中下载并安装系统更新",
    "software.updates_detail": "需要互联网连接；离线时仍可完成基础系统安装。",
    "software.drivers": "安装此设备需要的第三方驱动",
    "software.drivers_detail": "部分驱动是专有软件或其他非自由软件。",

    "disk.title":       "选择安装磁盘",
    "disk.subtitle":    "选择要安装 AnduinOS 的磁盘",
    "disk.warning_small": "该磁盘小于 20 GB，空间不足可能导致安装失败。",
    "disk.warning_erase": "所选磁盘上的所有数据将被永久清除。",
    "disk.column_disk": "磁盘",
    "disk.column_size": "大小",
    "disk.no_disks":    "未找到合适的磁盘。",
    "disk.live_usb":    "（Live USB — 已排除）",

    "user.title":       "用户账户",
    "user.subtitle":    "创建您的用户账户",
    "user.full_name":   "全名",
    "user.username":    "用户名",
    "user.password":    "密码",
    "user.confirm_password": "确认密码",
    "user.hostname":    "计算机名",
    "user.pass_too_short": "密码至少需要 6 个字符。",
    "user.pass_mismatch": "两次输入的密码不一致。",
    "user.sudo_without_password": "sudo 命令免密码",
    "user.lockout_heading": "管理员权限将被锁死",
    "user.lockout_body": "此账户没有登录密码，但 sudo 仍会要求输入密码。由于 root 账户默认锁定，安装后将无法执行管理员操作。请设置账户密码，或者启用免密码 sudo。",
    "user.sudo_confirm_heading": "免密码 sudo 存在安全风险",
    "user.sudo_confirm_body": "任何以此用户身份运行的程序都可以未经身份验证获得完整管理员权限。登录密码仍会保护登录过程，但不会保护 sudo。确定要继续吗？",
    "user.passwordless_sudo_heading": "此配置非常不安全",
    "user.passwordless_sudo_body": "系统将自动登录此账户，并允许它无需密码获得完整管理员权限。任何能够接触这台电脑的人，以及任何以此用户身份运行的程序，都可以完全控制系统。仅建议用于公共展示机、临时虚拟机或其他受控环境。",
    "user.return_modify": "返回修改",
    "user.return_set_password": "返回并设置密码",
    "user.continue_unsafe": "我了解风险，继续",
    "user.name_invalid": "用户名只能包含小写字母、数字、下划线和连字符。",
    "user.host_invalid": "计算机名包含无效字符。",

    "tz.title":         "选择时区",
    "tz.subtitle":      "选择您的位置以设置系统时钟",
    "tz.search":        "搜索时区…",
    "tz.selected":      "已选择的时区",

    "summary.title":    "准备安装",
    "summary.subtitle": "请在继续前确认您的选择",
    "summary.lang":     "语言",
    "summary.keyboard": "键盘",
    "summary.disk":     "目标磁盘",
    "summary.user":     "用户",
    "summary.hostname": "计算机名",
    "summary.timezone": "时区",
    "summary.warning":  "⚠ 这将清除所选磁盘上的所有数据。此操作不可撤销。",

    "progress.title":   "正在安装 AnduinOS",
    "progress.subtitle":"请不要关闭计算机",
    "progress.partition": "正在创建分区…",
    "progress.format":  "正在格式化分区…",
    "progress.mount":   "正在挂载文件系统…",
    "progress.copy":    "正在复制系统文件…",
    "progress.config":  "正在配置系统…",
    "progress.grub":    "正在安装引导程序…",
    "progress.post":    "正在执行安装后任务…",
    "progress.fstab":   "正在生成 fstab…",
    "progress.locale":  "正在生成本地化配置…",
    "progress.user_cfg":"正在创建用户账户…",
    "progress.mirror":  "正在配置 APT 镜像源…",
    "progress.rime":    "正在设置中文输入法…",
    "progress.cleanup": "正在清理…",
    "progress.done":    "安装完成！",
    "progress.failed":  "安装失败",
    "progress.error_detail": "安装过程中发生错误。您可以保存日志以便调试。",

    "done.title":       "安装完成",
    "done.subtitle":    "请移除安装介质并重新启动计算机",
    "done.mok_notice":  "重启后将进入 MOKManager。请选择 Enroll MOK → Continue → Yes，然后输入密码 123456。",
    "done.error_title": "安装失败",
    "done.error_subtitle": "安装过程中出现了问题",
})
