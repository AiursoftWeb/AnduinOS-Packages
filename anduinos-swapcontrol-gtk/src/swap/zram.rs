use crate::swap::types::ZramDevice;
use std::fs;
use std::path::Path;

use super::exec;

/// List all active zram block devices.
pub fn read_zram_devices() -> Vec<ZramDevice> {
    let mut devices = Vec::new();

    let sysfs_dir = Path::new(crate::config::ZRAM_SYSFS_DIR);
    let entries = match fs::read_dir(sysfs_dir) {
        Ok(e) => e,
        Err(_) => return devices,
    };

    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("zram") {
            continue;
        }

        let base = entry.path();
        let mut dev = ZramDevice {
            name: name.clone(),
            ..Default::default()
        };

        // Read disksize (in bytes)
        if let Ok(val) = read_sysfs_u64(&base.join("disksize")) {
            dev.size_bytes = val;
        }

        // Read mm_stat: orig_data_size compr_data_size mem_used_total ...
        if let Ok(content) = fs::read_to_string(base.join("mm_stat")) {
            let parts: Vec<u64> = content
                .split_whitespace()
                .filter_map(|s| s.parse().ok())
                .collect();
            if parts.len() >= 4 {
                dev.orig_data_size = parts[0];
                dev.compr_data_size = parts[1];
                dev.mem_used_total = parts[2];
            }
        }

        // Read comp_algorithm (current in brackets)
        if let Ok(content) = fs::read_to_string(base.join("comp_algorithm")) {
            for part in content.split_whitespace() {
                let part = part.trim_matches(|c: char| c == '[' || c == ']');
                if content.contains(&format!("[{part}]")) {
                    dev.comp_algorithm = part.to_string();
                    break;
                }
            }
        }

        // Try to get swap priority and usage from /proc/swaps
        if let Ok(swaps) = fs::read_to_string("/proc/swaps") {
            for line in swaps.lines().skip(1) {
                if line.contains(&format!("/dev/{name}")) {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() >= 5 {
                        dev.used_bytes = parts[3].parse::<u64>().map(|kb| kb * 1024).unwrap_or(0);
                        dev.swap_priority = parts[4].parse::<i32>().unwrap_or(0);
                    }
                }
            }
        }

        devices.push(dev);
    }

    devices
}

/// Get the list of compression algorithms supported by the kernel zram module.
pub fn get_available_algorithms() -> Vec<String> {
    // Try to read from any existing zram device first
    let devices = read_zram_devices();
    if let Some(dev) = devices.first() {
        let path = Path::new(crate::config::ZRAM_SYSFS_DIR)
            .join(&dev.name)
            .join("comp_algorithm");
        if let Ok(content) = fs::read_to_string(&path) {
            return content
                .split_whitespace()
                .map(|s| s.trim_matches(|c: char| c == '[' || c == ']').to_string())
                .collect();
        }
    }

    // Fallback: known algorithms from kernel config
    // lz4 is the AnduinOS recommended default — fastest decompression for desktop workloads
    vec![
        "lz4".to_string(),
        "lz4hc".to_string(),
        "zstd".to_string(),
        "lzo-rle".to_string(),
        "lzo".to_string(),
        "deflate".to_string(),
        "842".to_string(),
    ]
}

// ─── Internal helpers ────────────────────────────────────────────────────────

fn read_sysfs_u64(path: &Path) -> Result<u64, ()> {
    let content = fs::read_to_string(path).map_err(|_| ())?;
    content.trim().parse::<u64>().map_err(|_| ())
}

// ─── Write operations (require pkexec) ─────────────────────────────────────

/// Create a new zram device.
pub fn create_zram_device(size_mb: u64, algo: &str, priority: i32) -> Result<String, String> {
    let _ = exec::run_modprobe("zram");

    let output = exec::run_helper(
        "zramctl",
        &["-f", "-s", &format!("{}M", size_mb), "-a", algo],
    )?;

    let dev_path = output.trim().to_string();
    if dev_path.is_empty() {
        return Err("zramctl did not return a device path".to_string());
    }

    exec::run_helper("mkswap", &[&dev_path])?;
    exec::run_helper("swapon", &["-p", &priority.to_string(), &dev_path])?;

    Ok(dev_path)
}

/// Destroy a zram device.
pub fn destroy_zram_device(dev_path: &str) -> Result<String, String> {
    let _ = exec::run_helper("swapoff", &[dev_path]);
    exec::run_helper("zramctl", &["-r", dev_path])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_zram_devices() {
        let devices = read_zram_devices();
        // May be empty if zram is not loaded
        for dev in &devices {
            assert!(dev.name.starts_with("zram"));
        }
    }

    #[test]
    fn test_get_available_algorithms() {
        let algos = get_available_algorithms();
        assert!(!algos.is_empty());
    }
}
