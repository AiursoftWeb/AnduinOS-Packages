pub const APP_ID: &str = "com.anduinos.swapcontrol";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const GETTEXT_PACKAGE: &str = "swapcontrol-gtk";
// sysctl configuration
pub const SYSCTL_CONF: &str = "/etc/sysctl.d/90-anduinos-swapcontrol.conf";

// Swap file
pub const SWAPFILE_PATH: &str = "/swapfile";

// zswap sysfs parameters
pub const ZSWAP_ENABLED: &str = "/sys/module/zswap/parameters/enabled";
pub const ZSWAP_COMPRESSOR: &str = "/sys/module/zswap/parameters/compressor";
pub const ZSWAP_MAX_POOL_PERCENT: &str = "/sys/module/zswap/parameters/max_pool_percent";
pub const ZSWAP_ACCEPT_THRESHOLD: &str = "/sys/module/zswap/parameters/accept_threshold_percent";
pub const ZSWAP_SHRINKER: &str = "/sys/module/zswap/parameters/shrinker_enabled";

// zram sysfs base
pub const ZRAM_SYSFS_DIR: &str = "/sys/block";

// zram config file (written by GUI, read by vendor service)
pub const ZRAM_CONFIG: &str = "/etc/default/anduinos-zram";
// vendor zram service (provided by anduinos-swap-config package)
pub const VENDOR_ZRAM_SERVICE: &str = "/usr/lib/systemd/system/anduinos-zram.service";

// zswap config file (written by GUI, read by vendor service)
pub const ZSWAP_CONFIG: &str = "/etc/default/anduinos-zswap";
// vendor zswap service (provided by anduinos-swap-config package)
pub const VENDOR_ZSWAP_SERVICE: &str = "/usr/lib/systemd/system/anduinos-zswap.service";

// Proc / sys files
pub const PROC_SWAPS: &str = "/proc/swaps";
pub const PROC_MEMINFO: &str = "/proc/meminfo";
pub const PROC_CMDLINE: &str = "/proc/cmdline";

// Power / hibernation
pub const SYS_POWER_STATE: &str = "/sys/power/state";
pub const SYS_POWER_DISK: &str = "/sys/power/disk";
