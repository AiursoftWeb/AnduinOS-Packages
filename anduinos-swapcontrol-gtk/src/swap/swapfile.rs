use crate::swap::types::SwapStatus;
use std::fs;
use std::path::Path;

use super::exec;

/// Read current swap status from /proc/swaps.
pub fn read_swap_status() -> Result<SwapStatus, String> {
    let content = fs::read_to_string(crate::config::PROC_SWAPS)
        .map_err(|e| format!("Cannot read /proc/swaps: {e}"))?;

    let mut status = SwapStatus::default();

    // Parse /proc/swaps — skip the header line
    for line in content.lines().skip(1) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 5 {
            let path = parts[0].to_string();
            // Look specifically for the disk swapfile, not zram devices
            let is_swapfile = path == crate::config::SWAPFILE_PATH;
            if is_swapfile || (status.path.is_empty() && !path.contains("zram")) {
                status.path = path.clone();
                status.size_bytes = parts[2].parse::<u64>().map(|kb| kb * 1024).unwrap_or(0);
                status.used_bytes = parts[3].parse::<u64>().map(|kb| kb * 1024).unwrap_or(0);
                status.priority = parts[4].parse::<i32>().unwrap_or(0);
                status.active = true;
                if is_swapfile {
                    break;
                }
            }
        }
    }

    // Double-check: is the swapfile still a valid file?
    if !status.path.is_empty() {
        if !Path::new(crate::config::SWAPFILE_PATH).exists()
            && status.path == crate::config::SWAPFILE_PATH
        {
            status.active = false;
        }
    }

    Ok(status)
}

/// Check if any swap is active (convenience wrapper).
pub fn is_swap_active() -> bool {
    read_swap_status().map(|s| s.active).unwrap_or(false)
}

// ─── Write operations (require pkexec) ─────────────────────────────────────

/// Enable the swapfile.
pub fn enable_swapfile() -> Result<String, String> {
    exec::run_helper("swapon", &[crate::config::SWAPFILE_PATH])
}

/// Disable the swapfile (can take seconds/minutes to flush data).
pub fn disable_swapfile() -> Result<String, String> {
    exec::run_helper("swapoff", &[crate::config::SWAPFILE_PATH])
}

/// Resize the swapfile to the given size in GiB.
pub fn resize_swapfile(new_size_gb: u64) -> Result<String, String> {
    let path = crate::config::SWAPFILE_PATH;
    let count = (new_size_gb * 1024).to_string();

    let _ = exec::run_helper("swapoff", &[path]);
    exec::run_helper(
        "dd",
        &[
            "if=/dev/zero",
            &format!("of={path}"),
            "bs=1M",
            &format!("count={count}"),
            "status=none",
        ],
    )?;
    exec::run_helper("chmod", &["600", path])?;
    exec::run_helper("mkswap", &[path])?;
    exec::run_helper("swapon", &[path])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_swap_status() {
        let result = read_swap_status();
        assert!(result.is_ok());
        let status = result.unwrap();
        // On AnduinOS, /swapfile should exist and be active
        if Path::new(crate::config::SWAPFILE_PATH).exists() {
            assert!(status.active);
        }
    }
}
