//! Persistence layer: generates systemd units and config files so that
//! zswap/zram/sysctl settings survive reboot.
//!
//! Strategy:
//!   - sysctl     → /etc/sysctl.d/90-anduinos-swapcontrol.conf (already done by sysctl.rs)
//!   - zswap      → systemd oneshot service that writes sysfs on boot
//!   - zram       → systemd service that creates zram device on boot
//!
//! All files are written via the helper (single polkit auth).
//!
//! IMPORTANT: Both services use DefaultDependencies=no to avoid ordering cycles.
//! Without this, the implicit After=basic.target creates a circular dependency
//! chain: tmp.mount → swap.target → service → basic.target → tmp.mount, which
//! causes systemd to delete tmp.mount/start, breaking /tmp/.X11-unix ownership
//! and preventing GDM login.

use super::exec;
use crate::config;

const ZSWAP_SERVICE: &str = "/etc/systemd/system/anduinos-zswap.service";
const ZRAM_SERVICE: &str = "/etc/systemd/system/anduinos-zram.service";

// ─── Zram persistence ───────────────────────────────────────────────────────

/// Generate and install a systemd service that creates a zram device at boot.
/// If `devices` is empty, removes any existing zram persistence.
pub fn persist_zram(devices: &[(u64, String, i32)]) -> Result<String, String> {
    // devices: Vec<(size_mb, algorithm, priority)>

    if devices.is_empty() {
        // Remove persistence
        // Use mask to prevent the vendor default (/usr/lib) from resurrecting zram
        let _ = exec::run_helper("swapoff", &["/dev/zram0"]);
        let _ = exec::run_helper("zramctl", &["-r", "/dev/zram0"]);
        let _ = exec::run_helper("systemctl", &["mask", "--now", "anduinos-zram.service"]);
        let _ = exec::run_helper("rm", &["-f", ZRAM_SERVICE]);
        let _ = exec::run_helper("systemctl", &["daemon-reload"]);
        return Ok("Zram persistence removed".to_string());
    }

    let mut exec_start_lines = String::new();

    for (i, (size_mb, algo, priority)) in devices.iter().enumerate() {
        exec_start_lines.push_str(&format!(
            "# Device {}: {} MiB, algo={}, priority={}\n\
             ExecStart=/usr/lib/anduinos-swapcontrol/helper modprobe zram\n\
             ExecStart=/bin/bash -c 'DEV=$(/usr/lib/anduinos-swapcontrol/helper zramctl -f -s {}M -a {}) && /usr/lib/anduinos-swapcontrol/helper mkswap $DEV && /usr/lib/anduinos-swapcontrol/helper swapon -p {} $DEV'\n",
            i, size_mb, algo, priority,
            size_mb, algo, priority
        ));
    }

    let unit = format!(
        "# Managed by anduinos-swapcontrol-gtk. Do not edit manually.\n\
         [Unit]\n\
         Description=AnduinOS Zram Devices\n\
         DefaultDependencies=no\n\
         After=systemd-journald.socket\n\
         Before=swap.target\n\n\
         [Service]\n\
         Type=oneshot\n\
         RemainAfterExit=yes\n\
         {}\n\
         [Install]\n\
         WantedBy=multi-user.target\n",
        exec_start_lines
    );

    exec::write_sysfs(ZRAM_SERVICE, &unit)?;

    let _ = exec::run_helper("systemctl", &["daemon-reload"]);
    let _ = exec::run_helper("systemctl", &["unmask", "anduinos-zram.service"]);
    let _ = exec::run_helper("systemctl", &["enable", "--now", "anduinos-zram.service"]);

    Ok("Zram persistence enabled".to_string())
}

// ─── Zswap persistence ───────────────────────────────────────────────────────

/// Generate and install a systemd service that configures zswap at boot.
/// Reads the current sysfs values and writes them as ExecStart lines.
/// If `enabled` is false, removes any existing zswap persistence.
pub fn persist_zswap(enabled: bool, compressor: &str, max_pool_percent: u8,
                     accept_threshold: u8, shrinker: bool) -> Result<String, String> {
    if !enabled {
        // Remove persistence
        // Use mask so it can't be started via any path
        let _ = exec::run_helper("systemctl", &["mask", "--now", "anduinos-zswap.service"]);
        let _ = exec::run_helper("rm", &["-f", ZSWAP_SERVICE]);
        let _ = exec::run_helper("systemctl", &["daemon-reload"]);
        return Ok("Zswap persistence removed".to_string());
    }

    let shrinker_val = if shrinker { "Y" } else { "N" };

    let unit = format!(
        "# Managed by anduinos-swapcontrol-gtk. Do not edit manually.\n\
         [Unit]\n\
         Description=AnduinOS Zswap Configuration\n\
         DefaultDependencies=no\n\
         After=systemd-journald.socket\n\
         Before=swap.target\n\n\
         [Service]\n\
         Type=oneshot\n\
         RemainAfterExit=yes\n\
         ExecStart=/usr/lib/anduinos-swapcontrol/helper bash -c 'echo 1 > /sys/module/zswap/parameters/enabled'\n\
         ExecStart=/usr/lib/anduinos-swapcontrol/helper bash -c 'echo \"{}\" > /sys/module/zswap/parameters/compressor'\n\
         ExecStart=/usr/lib/anduinos-swapcontrol/helper bash -c 'echo \"{}\" > /sys/module/zswap/parameters/max_pool_percent'\n\
         ExecStart=/usr/lib/anduinos-swapcontrol/helper bash -c 'echo \"{}\" > /sys/module/zswap/parameters/accept_threshold_percent'\n\
         ExecStart=/usr/lib/anduinos-swapcontrol/helper bash -c 'echo \"{}\" > /sys/module/zswap/parameters/shrinker_enabled'\n\
         [Install]\n\
         WantedBy=multi-user.target\n",
        compressor, max_pool_percent, accept_threshold, shrinker_val
    );

    exec::write_sysfs(ZSWAP_SERVICE, &unit)?;

    let _ = exec::run_helper("systemctl", &["daemon-reload"]);
    let _ = exec::run_helper("systemctl", &["unmask", "anduinos-zswap.service"]);
    let _ = exec::run_helper("systemctl", &["enable", "--now", "anduinos-zswap.service"]);

    Ok("Zswap persistence enabled".to_string())
}
