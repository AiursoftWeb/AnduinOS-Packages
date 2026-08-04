use crate::swap::types::{SwapDeviceKind, SwapInventory, SwapStatus};
use std::fs;
use std::process::Command;

use super::exec;

fn parse_proc_swaps(content: &str) -> Vec<SwapStatus> {
    let mut devices = Vec::new();
    for line in content.lines().skip(1) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 5 || parts[0].contains("zram") {
            continue;
        }
        let kind = match parts[1].to_ascii_lowercase().as_str() {
            "file" => SwapDeviceKind::File,
            "partition" => SwapDeviceKind::Partition,
            _ => SwapDeviceKind::Other,
        };
        devices.push(SwapStatus {
            active: true,
            path: parts[0].to_string(),
            kind,
            size_bytes: parts[2].parse::<u64>().map(|kb| kb * 1024).unwrap_or(0),
            used_bytes: parts[3].parse::<u64>().map(|kb| kb * 1024).unwrap_or(0),
            priority: parts[4].parse::<i32>().unwrap_or(0),
        });
    }
    devices
}

/// Read every active disk-backed swap device without conflating partitions and files.
pub fn read_swap_inventory() -> Result<SwapInventory, String> {
    let content = fs::read_to_string(crate::config::PROC_SWAPS)
        .map_err(|e| format!("Cannot read /proc/swaps: {e}"))?;
    let devices = parse_proc_swaps(&content);
    let mut managed_swapfile = devices
        .iter()
        .find(|item| item.path == crate::config::SWAPFILE_PATH)
        .cloned()
        .unwrap_or_else(|| SwapStatus {
            path: crate::config::SWAPFILE_PATH.to_string(),
            kind: SwapDeviceKind::File,
            ..SwapStatus::default()
        });

    if !managed_swapfile.active {
        if let Ok(metadata) = fs::metadata(crate::config::SWAPFILE_PATH) {
            if metadata.is_file() {
                managed_swapfile.size_bytes = metadata.len();
            }
        }
    }

    Ok(SwapInventory {
        devices,
        managed_swapfile,
    })
}

/// Aggregate all active disk-backed swap for the dashboard and usage bar.
pub fn read_swap_status() -> Result<SwapStatus, String> {
    let inventory = read_swap_inventory()?;
    Ok(aggregate_swap(&inventory.devices))
}

fn aggregate_swap(devices: &[SwapStatus]) -> SwapStatus {
    let active: Vec<&SwapStatus> = devices.iter().filter(|item| item.active).collect();
    SwapStatus {
        active: !active.is_empty(),
        path: active
            .iter()
            .map(|item| item.path.as_str())
            .collect::<Vec<_>>()
            .join(", "),
        kind: SwapDeviceKind::Other,
        size_bytes: active.iter().map(|item| item.size_bytes).sum(),
        used_bytes: active.iter().map(|item| item.used_bytes).sum(),
        priority: active.iter().map(|item| item.priority).max().unwrap_or(0),
    }
}

pub fn read_swap_partitions() -> Result<Vec<SwapStatus>, String> {
    Ok(read_swap_inventory()?
        .devices
        .into_iter()
        .filter(|item| item.kind == SwapDeviceKind::Partition)
        .collect())
}

pub fn read_managed_swapfile() -> Result<SwapStatus, String> {
    Ok(read_swap_inventory()?.managed_swapfile)
}

/// Filesystems supported by the compatibility swapfile editor.
/// Btrfs is deliberately excluded because an active file in @root prevents
/// Timeback from snapshotting that subvolume.
pub fn swapfile_management_support() -> Result<(bool, String), String> {
    let output = Command::new("findmnt")
        .args(["--noheadings", "--output", "FSTYPE", "--target", "/"])
        .output()
        .map_err(|e| format!("Cannot inspect the root filesystem: {e}"))?;
    if !output.status.success() {
        return Err("Cannot inspect the root filesystem".to_string());
    }
    let filesystem = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let supported = matches!(filesystem.as_str(), "ext2" | "ext3" | "ext4" | "xfs");
    Ok((supported, filesystem))
}

/// Largest supplementary swapfile size that still leaves 20 GiB free.
/// Existing legacy files remain representable even when larger than the cap.
pub fn maximum_swapfile_size_gib(current_size_bytes: u64) -> u64 {
    let output = Command::new("df")
        .args(["--output=avail", "-B1", "/"])
        .output();
    let available = output
        .ok()
        .filter(|item| item.status.success())
        .and_then(|item| {
            String::from_utf8_lossy(&item.stdout)
                .lines()
                .nth(1)
                .and_then(|value| value.trim().parse::<u64>().ok())
        })
        .unwrap_or(0);
    calculate_maximum_swapfile_size_gib(available, current_size_bytes)
}

fn calculate_maximum_swapfile_size_gib(available: u64, current_size_bytes: u64) -> u64 {
    const GIB: u64 = 1024 * 1024 * 1024;
    const ROOT_RESERVE: u64 = 20 * GIB;
    const PRODUCT_CAP_GIB: u64 = 64;
    let budget_gib = available
        .saturating_add(current_size_bytes)
        .saturating_sub(ROOT_RESERVE)
        / GIB;
    let current_gib = current_size_bytes.div_ceil(GIB);
    budget_gib.min(PRODUCT_CAP_GIB).max(current_gib)
}

/// Check if any swap is active (convenience wrapper).
pub fn is_swap_active() -> bool {
    read_swap_status().map(|s| s.active).unwrap_or(false)
}

// ─── Write operations (require pkexec) ─────────────────────────────────────

/// Enable the swapfile.
pub fn enable_swapfile() -> Result<String, String> {
    exec::run_helper("swapfile-enable", &[])
}

/// Disable the swapfile (can take seconds/minutes to flush data).
pub fn disable_swapfile() -> Result<String, String> {
    exec::run_helper("swapfile-disable", &[])
}

/// Resize the swapfile to the given size in GiB.
pub fn resize_swapfile(new_size_gb: u64) -> Result<String, String> {
    exec::run_helper("swapfile-resize", &[&new_size_gb.to_string()])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_swap_status() {
        let result = read_swap_status();
        assert!(result.is_ok());
        let _status = result.unwrap();
    }

    #[test]
    fn parses_partition_and_file_as_distinct_devices() {
        let devices = parse_proc_swaps(
            "Filename Type Size Used Priority\n/dev/vda3 partition 4194300 4 10\n/swapfile file 2097148 0 -2\n/dev/zram0 partition 4096000 32 100\n",
        );
        assert_eq!(devices.len(), 2);
        assert_eq!(devices[0].kind, SwapDeviceKind::Partition);
        assert_eq!(devices[1].kind, SwapDeviceKind::File);
        assert_eq!(devices[1].path, "/swapfile");
    }

    #[test]
    fn aggregates_all_disk_backed_swap() {
        let devices = parse_proc_swaps(
            "Filename Type Size Used Priority\n/dev/vda3 partition 4194304 1024 10\n/swapfile file 2097152 512 -2\n",
        );
        let status = aggregate_swap(&devices);
        assert!(status.active);
        assert_eq!(status.size_bytes, (4194304 + 2097152) * 1024);
        assert_eq!(status.used_bytes, (1024 + 512) * 1024);
    }

    #[test]
    fn supplementary_file_budget_preserves_root_space_and_caps_size() {
        const GIB: u64 = 1024 * 1024 * 1024;
        assert_eq!(calculate_maximum_swapfile_size_gib(10 * GIB, 0), 0);
        assert_eq!(calculate_maximum_swapfile_size_gib(40 * GIB, 0), 20);
        assert_eq!(calculate_maximum_swapfile_size_gib(40 * GIB, 34 * GIB), 54);
        assert_eq!(calculate_maximum_swapfile_size_gib(200 * GIB, 0), 64);
    }
}
