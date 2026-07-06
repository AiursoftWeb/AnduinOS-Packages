//! Persistence layer: generates config files and systemd units so that
//! zswap/zram/sysctl settings survive reboot.
//!
//! Strategy:
//!   - sysctl     → /etc/sysctl.d/90-anduinos-swapcontrol.conf (already done by sysctl.rs)
//!   - zswap      → systemd oneshot service that writes sysfs on boot
//!   - zram       → config file /etc/default/anduinos-zram read by the vendor
//!                   service /usr/lib/systemd/system/anduinos-zram.service.
//!                   The GUI does NOT write systemd units — it only writes the
//!                   declarative config. The vendor service owns the logic
//!                   (idempotency, modprobe, setup-zram.sh).
//!
//! All files are written via the helper (single polkit auth).

use super::exec;
use crate::config;

const ZSWAP_SERVICE: &str = "/etc/systemd/system/anduinos-zswap.service";
/// Path to the old GUI-generated systemd unit (pre-2.1 migration).
/// Removed on first run of the new persist_zram.
const LEGACY_ZRAM_UNIT: &str = "/etc/systemd/system/anduinos-zram.service";

// ─── Zram persistence ───────────────────────────────────────────────────────

/// Write /etc/default/anduinos-zram so the vendor service reads user settings.
/// If `devices` is empty, writes ZRAM_ENABLED=no to disable zram entirely.
///
/// Also cleans up legacy artifacts from the old system (GUI-generated systemd
/// unit at /etc/systemd/system/anduinos-zram.service and any service mask).
pub fn persist_zram(devices: &[(u64, String, i32)]) -> Result<String, String> {
    // devices: Vec<(size_mb, algorithm, priority)>

    // ── Migration: clean up legacy GUI-generated unit and unmask ─────────
    let _ = exec::run_helper("rm", &["-f", LEGACY_ZRAM_UNIT]);
    let _ = exec::run_helper("systemctl", &["unmask", "anduinos-zram.service"]);

    // ── Build config file ───────────────────────────────────────────────
    let mut config = String::from(
        "# Managed by anduinos-swapcontrol-gtk. Do not edit manually.\n",
    );

    if devices.is_empty() {
        config.push_str("ZRAM_ENABLED=no\n");
    } else {
        config.push_str("ZRAM_ENABLED=yes\n");
        config.push_str(&format!("ZRAM_DEVICE_COUNT={}\n", devices.len()));
        for (i, (size_mb, algo, priority)) in devices.iter().enumerate() {
            config.push_str(&format!("ZRAM_{}_SIZE_MB={}\n", i, size_mb));
            config.push_str(&format!("ZRAM_{}_ALGORITHM={}\n", i, algo));
            config.push_str(&format!("ZRAM_{}_PRIORITY={}\n", i, priority));
        }
    }

    exec::write_sysfs(config::ZRAM_CONFIG, &config)?;

    // ── Activate ────────────────────────────────────────────────────────
    let _ = exec::run_helper("systemctl", &["daemon-reload"]);
    let _ = exec::run_helper("systemctl", &["enable", "anduinos-zram.service"]);

    if devices.is_empty() {
        let _ = exec::run_helper("systemctl", &["stop", "anduinos-zram.service"]);
        return Ok("Zram persistence disabled".to_string());
    }

    let _ = exec::run_helper("systemctl", &["try-restart", "anduinos-zram.service"]);

    Ok("Zram persistence enabled".to_string())
}

// ─── Zswap persistence ───────────────────────────────────────────────────────

/// Generate and install a systemd service that configures zswap at boot.
/// Reads the current sysfs values and writes them as ExecStart lines.
/// If `enabled` is false, removes any existing zswap persistence.
pub fn persist_zswap(enabled: bool, compressor: &str, max_pool_percent: u8,
                     accept_threshold: u8, shrinker: bool) -> Result<String, String> {
    if !enabled {
        // Remove persistence.
        // Order matters: rm first (remove the custom unit), then mask
        // (create /dev/null symlink) so zswap stays off regardless of
        // any future vendor default.
        let _ = exec::run_helper("rm", &["-f", ZSWAP_SERVICE]);
        let _ = exec::run_helper("systemctl", &["mask", "--now", "anduinos-zswap.service"]);
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
