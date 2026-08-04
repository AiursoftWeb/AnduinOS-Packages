/// The kernel-reported backing type of a swap device.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum SwapDeviceKind {
    File,
    Partition,
    #[default]
    Other,
}

/// Represents the status of one swap device.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SwapStatus {
    pub active: bool,
    pub path: String,
    pub kind: SwapDeviceKind,
    pub size_bytes: u64,
    pub used_bytes: u64,
    pub priority: i32,
}

/// All non-zram swap devices, plus the legacy file managed by this app.
#[derive(Debug, Clone, Default)]
pub struct SwapInventory {
    pub devices: Vec<SwapStatus>,
    pub managed_swapfile: SwapStatus,
}

/// Represents a zswap configuration snapshot.
#[derive(Debug, Clone, Default)]
pub struct ZswapConfig {
    pub enabled: bool,
    pub compressor: String,
    pub max_pool_percent: u8,
    pub accept_threshold_percent: u8,
    pub shrinker_enabled: bool,
}

/// Represents a zram block device.
#[derive(Debug, Clone, Default)]
pub struct ZramDevice {
    pub name: String, // e.g. "zram0"
    pub size_bytes: u64,
    pub used_bytes: u64,
    pub compr_data_size: u64, // compressed size in RAM
    pub orig_data_size: u64,  // original uncompressed size
    pub mem_used_total: u64,  // total memory used (metadata + compressed)
    pub comp_algorithm: String,
    pub swap_priority: i32,
}

/// Summary of the hibernation subsystem state.
#[derive(Debug, Clone, Default)]
pub struct HibernationStatus {
    /// true if /sys/power/state contains "disk"
    pub system_supports: bool,
    /// Available suspend-to-disk modes reported by /sys/power/disk.
    pub disk_modes: Vec<String>,
    /// resume= argument from kernel cmdline (if any)
    pub resume_device: Option<String>,
    /// resume_offset= from kernel cmdline (swapfile case)
    pub resume_offset: Option<u64>,
    /// RESUME= from /etc/initramfs-tools/conf.d/resume
    pub initramfs_resume: Option<String>,
    /// The selected resume target after preferring the kernel command line.
    pub configured_target: Option<String>,
    /// The configured target resolved to a canonical device path when possible.
    pub resolved_target: Option<String>,
    /// Whether the resolved target is currently active swap.
    pub target_active: bool,
    /// Capacity of the configured active resume target.
    pub target_size_bytes: u64,
    /// Whether the managed /swapfile is the configured resume target.
    pub managed_swapfile_is_target: bool,
    /// Rounded-up RAM plus 1 GiB, matching the installer hibernation target.
    pub required_size_bytes: u64,
    /// True only when the complete, capacity-qualified resume path is healthy.
    pub ready: bool,
}
