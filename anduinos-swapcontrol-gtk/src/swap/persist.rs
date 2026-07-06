//! Persistence layer: writes declarative config files read by vendor services.
//!
//! Strategy:
//!   - sysctl → /etc/sysctl.d/90-anduinos-swapcontrol.conf (sysctl.rs)
//!   - zram   → /etc/default/anduinos-zram → systemctl restart anduinos-zram.service
//!   - zswap  → /etc/default/anduinos-zswap → systemctl restart anduinos-zswap.service
//!
//! The GUI does NOT write systemd units or call zramctl/mkswap/swapon directly.
//! The vendor package (anduinos-swap-config) owns all execution logic via its
//! setup-zram.sh / setup-zswap.sh scripts.

use super::exec;
use crate::config;
use std::path::Path;

/// Path to the old GUI-generated systemd unit (pre-2.1 migration).
/// Removed on first run of the new persist_zram.
const LEGACY_ZRAM_UNIT: &str = "/etc/systemd/system/anduinos-zram.service";
/// Path to the old GUI-generated zswap unit (pre-2.1 migration).
/// Removed on first run of the new persist_zswap.
const LEGACY_ZSWAP_UNIT: &str = "/etc/systemd/system/anduinos-zswap.service";

fn remove_legacy_unit(path: &str) -> Result<(), String> {
    if !Path::new(path).exists() {
        return Ok(());
    }

    exec::run_helper("rm", &["-f", path])
        .map(|_| ())
        .map_err(|e| format!("Failed to remove legacy unit {path}: {e}"))
}

fn run_systemctl(args: &[&str]) -> Result<(), String> {
    exec::run_helper("systemctl", args)
        .map(|_| ())
        .map_err(|e| format!("systemctl {} failed: {e}", args.join(" ")))
}

// ─── Zram persistence ───────────────────────────────────────────────────────

/// Write /etc/default/anduinos-zram so the vendor service reads user settings.
/// If `devices` is empty, writes ZRAM_ENABLED=no to disable zram entirely.
///
/// Also cleans up legacy artifacts from the old system (GUI-generated systemd
/// unit at /etc/systemd/system/anduinos-zram.service and any service mask).
pub fn persist_zram(devices: &[(u64, String, i32)]) -> Result<String, String> {
    // devices: Vec<(size_mb, algorithm, priority)>

    // ── Migration: clean up legacy GUI-generated unit and unmask ─────────
    remove_legacy_unit(LEGACY_ZRAM_UNIT)?;

    // ── Build config file ───────────────────────────────────────────────
    let mut config_str =
        String::from("# Managed by anduinos-swapcontrol-gtk. Do not edit manually.\n");

    if devices.is_empty() {
        config_str.push_str("ZRAM_ENABLED=no\n");
    } else {
        config_str.push_str("ZRAM_ENABLED=yes\n");
        config_str.push_str(&format!("ZRAM_DEVICE_COUNT={}\n", devices.len()));
        for (i, (size_mb, algo, priority)) in devices.iter().enumerate() {
            config_str.push_str(&format!("ZRAM_{}_SIZE_MB={}\n", i, size_mb));
            config_str.push_str(&format!("ZRAM_{}_ALGORITHM={}\n", i, algo));
            config_str.push_str(&format!("ZRAM_{}_PRIORITY={}\n", i, priority));
        }
    }

    exec::write_sysfs(config::ZRAM_CONFIG, &config_str)?;

    // ── Activate ────────────────────────────────────────────────────────
    let has_vendor_service = Path::new(config::VENDOR_ZRAM_SERVICE).exists();
    if !has_vendor_service {
        return Err(format!(
            "The package 'anduinos-swap-config' is not installed.\n\n\
             Zram changes cannot be applied or persisted without it.\n\
             Install 'anduinos-swap-config' to manage zram from this GUI."
        ));
    }

    // The vendor unit is Type=oneshot, so `stop` would only mark it inactive.
    // We must restart it so setup-zram.sh re-runs, tears down old devices,
    // and applies the new enabled/disabled config.
    run_systemctl(&["daemon-reload"])?;
    run_systemctl(&["unmask", "anduinos-zram.service"])?;
    run_systemctl(&["enable", "anduinos-zram.service"])?;
    run_systemctl(&["restart", "anduinos-zram.service"])?;

    if devices.is_empty() {
        Ok("Zram persistence disabled".to_string())
    } else {
        Ok("Zram persistence enabled".to_string())
    }
}

// ─── Zswap persistence ───────────────────────────────────────────────────────

/// Write /etc/default/anduinos-zswap so the vendor service reads user settings.
/// If `enabled` is false, writes ZSWAP_ENABLED=no and restarts the vendor
/// oneshot service so the disabled state is applied immediately.
///
/// The config uses the same shell-sourceable format as the zram config,
/// consumed by setup-zswap.sh from the anduinos-swap-config package.
/// Also removes the old GUI-generated /etc/systemd/system/anduinos-zswap.service
/// so upgrades switch cleanly to the vendor unit in /usr/lib.
pub fn persist_zswap(
    enabled: bool,
    compressor: &str,
    max_pool_percent: u8,
    accept_threshold: u8,
    shrinker: bool,
) -> Result<String, String> {
    remove_legacy_unit(LEGACY_ZSWAP_UNIT)?;

    let shrinker_val = if shrinker { "Y" } else { "N" };

    let config_str = format!(
        "# Managed by anduinos-swapcontrol-gtk. Do not edit manually.\n\
         ZSWAP_ENABLED={}\n\
         ZSWAP_COMPRESSOR={}\n\
         ZSWAP_MAX_POOL_PERCENT={}\n\
         ZSWAP_ACCEPT_THRESHOLD={}\n\
         ZSWAP_SHRINKER={}\n",
        if enabled { "yes" } else { "no" },
        compressor,
        max_pool_percent,
        accept_threshold,
        shrinker_val,
    );

    exec::write_sysfs(config::ZSWAP_CONFIG, &config_str)?;

    let has_vendor_service = Path::new(config::VENDOR_ZSWAP_SERVICE).exists();
    if !has_vendor_service {
        return Err(format!(
            "The package 'anduinos-swap-config' is not installed.\n\n\
             Zswap changes cannot be applied or persisted without it.\n\
             Install 'anduinos-swap-config' to manage zswap from this GUI."
        ));
    }

    // The vendor unit is Type=oneshot, so `stop` would not write enabled=0 to
    // sysfs. Restarting re-runs setup-zswap.sh and applies ZSWAP_ENABLED=no.
    run_systemctl(&["daemon-reload"])?;
    run_systemctl(&["unmask", "anduinos-zswap.service"])?;
    run_systemctl(&["enable", "anduinos-zswap.service"])?;
    run_systemctl(&["restart", "anduinos-zswap.service"])?;

    if enabled {
        Ok("Zswap persistence enabled".to_string())
    } else {
        Ok("Zswap persistence disabled".to_string())
    }
}
