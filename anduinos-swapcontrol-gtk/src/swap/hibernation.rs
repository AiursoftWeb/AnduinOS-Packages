use crate::swap::swapfile;
use crate::swap::types::{HibernationStatus, SwapDeviceKind, SwapStatus};
use std::fs;
use std::path::Path;
use std::process::Command;

const GIB: u64 = 1024 * 1024 * 1024;

fn parse_cmdline(content: &str) -> (Option<String>, Option<u64>) {
    let mut resume_device = None;
    let mut resume_offset = None;
    for token in content.split_whitespace() {
        if let Some(value) = token.strip_prefix("resume=") {
            if !value.is_empty() && value != "none" {
                resume_device = Some(value.to_string());
            }
        }
        if let Some(value) = token.strip_prefix("resume_offset=") {
            resume_offset = value.parse::<u64>().ok();
        }
    }
    (resume_device, resume_offset)
}

fn parse_initramfs_resume(content: &str) -> Option<String> {
    content.lines().find_map(|line| {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            return None;
        }
        let value = line
            .strip_prefix("RESUME=")?
            .trim()
            .trim_matches(['\'', '"']);
        if value.is_empty() || value == "none" {
            None
        } else {
            Some(value.to_string())
        }
    })
}

fn parse_disk_modes(content: &str) -> Vec<String> {
    content
        .split_whitespace()
        .map(|value| value.trim_matches(['[', ']']).to_string())
        .filter(|value| !value.is_empty() && value != "disabled")
        .collect()
}

fn required_hibernation_bytes(total_ram_bytes: u64) -> u64 {
    total_ram_bytes.div_ceil(GIB).saturating_add(1) * GIB
}

fn hibernation_path_is_ready(status: &HibernationStatus) -> bool {
    status.system_supports
        && !status.disk_modes.is_empty()
        && status.configured_target.is_some()
        && status.resolved_target.is_some()
        && status.target_active
        && status.target_size_bytes >= status.required_size_bytes
}

fn prefer_exact_capacity(reported_bytes: u64, exact_bytes: Option<u64>) -> u64 {
    exact_bytes
        .filter(|size| *size > 0)
        .unwrap_or(reported_bytes)
}

/// `/proc/swaps` reports usable swap pages, which excludes the swap header.
/// Hibernation capacity policy is based on the storage object we allocated, so
/// use the file length or block-device size when it can be inspected.
fn exact_swap_capacity(device: &SwapStatus) -> u64 {
    let exact = match device.kind {
        SwapDeviceKind::File => fs::metadata(&device.path).ok().map(|item| item.len()),
        SwapDeviceKind::Partition => Command::new("lsblk")
            .args([
                "--bytes",
                "--noheadings",
                "--output",
                "SIZE",
                "--",
                &device.path,
            ])
            .output()
            .ok()
            .filter(|item| item.status.success())
            .and_then(|item| {
                String::from_utf8_lossy(&item.stdout)
                    .lines()
                    .next()
                    .and_then(|value| value.trim().parse::<u64>().ok())
            }),
        SwapDeviceKind::Other => None,
    };
    prefer_exact_capacity(device.size_bytes, exact)
}

fn resolve_device(spec: &str) -> Option<String> {
    if spec.starts_with('/') {
        return fs::canonicalize(spec)
            .ok()
            .or_else(|| Some(Path::new(spec).to_path_buf()))
            .map(|path| path.to_string_lossy().into_owned());
    }
    if !spec.contains('=') {
        return None;
    }
    let output = Command::new("blkid")
        .args(["--match-token", spec, "--output", "device"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let path = String::from_utf8_lossy(&output.stdout)
        .lines()
        .next()?
        .trim()
        .to_string();
    fs::canonicalize(&path)
        .ok()
        .or_else(|| Some(Path::new(&path).to_path_buf()))
        .map(|item| item.to_string_lossy().into_owned())
}

fn canonical_path(path: &str) -> String {
    fs::canonicalize(path)
        .unwrap_or_else(|_| Path::new(path).to_path_buf())
        .to_string_lossy()
        .into_owned()
}

fn backing_device(path: &str) -> Option<String> {
    let output = Command::new("findmnt")
        .args(["--noheadings", "--output", "SOURCE", "--target", path])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let output_text = String::from_utf8_lossy(&output.stdout);
    let source = output_text
        .trim()
        .split_once('[')
        .map(|(device, _)| device)
        .unwrap_or_else(|| output_text.trim())
        .to_string();
    if source.starts_with('/') {
        Some(canonical_path(&source))
    } else {
        None
    }
}

fn managed_swapfile_matches_target(
    target: &str,
    resume_offset: Option<u64>,
    managed: &SwapStatus,
    managed_backing_device: Option<&str>,
) -> bool {
    if managed.size_bytes == 0 {
        return false;
    }

    canonical_path(&managed.path) == target
        || (resume_offset.is_some() && managed_backing_device == Some(target))
}

/// Detect a complete hibernation path. Kernel support, resume configuration,
/// active target identity and target capacity are separate facts.
pub fn check_hibernation() -> HibernationStatus {
    let mut status = HibernationStatus {
        system_supports: fs::read_to_string(crate::config::SYS_POWER_STATE)
            .map(|content| content.split_whitespace().any(|value| value == "disk"))
            .unwrap_or(false),
        disk_modes: fs::read_to_string(crate::config::SYS_POWER_DISK)
            .map(|content| parse_disk_modes(&content))
            .unwrap_or_default(),
        ..HibernationStatus::default()
    };

    if let Ok(content) = fs::read_to_string(crate::config::PROC_CMDLINE) {
        (status.resume_device, status.resume_offset) = parse_cmdline(&content);
    }
    status.initramfs_resume = fs::read_to_string(crate::config::INITRAMFS_RESUME)
        .ok()
        .and_then(|content| parse_initramfs_resume(&content));
    status.configured_target = status
        .resume_device
        .clone()
        .or_else(|| status.initramfs_resume.clone());
    status.resolved_target = status.configured_target.as_deref().and_then(resolve_device);

    let total_ram = crate::swap::sysctl::read_total_ram().unwrap_or(0);
    status.required_size_bytes = required_hibernation_bytes(total_ram);

    if let (Some(target), Ok(inventory)) = (
        status.resolved_target.as_deref(),
        swapfile::read_swap_inventory(),
    ) {
        for device in inventory.devices.iter().filter(|item| item.active) {
            if canonical_path(&device.path) == target {
                status.target_active = true;
                status.target_size_bytes = exact_swap_capacity(device);
                break;
            }
        }

        // Swapfile resume uses the containing block device plus resume_offset.
        // Keep its identity even while it is inactive: otherwise disabling it
        // would unlock resize and silently invalidate the configured offset.
        let managed = &inventory.managed_swapfile;
        let managed_backing = if status.resume_offset.is_some() && managed.size_bytes > 0 {
            backing_device(crate::config::SWAPFILE_PATH)
        } else {
            None
        };
        status.managed_swapfile_is_target = managed_swapfile_matches_target(
            target,
            status.resume_offset,
            managed,
            managed_backing.as_deref(),
        );
        if status.managed_swapfile_is_target && managed.active {
            if !status.target_active {
                status.target_active = true;
            }
            status.target_size_bytes = exact_swap_capacity(managed);
        }
    }

    status.ready = hibernation_path_is_ready(&status);
    status
}

pub fn managed_swapfile_is_resume_target() -> bool {
    check_hibernation().managed_swapfile_is_target
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_kernel_resume_arguments() {
        let (device, offset) = parse_cmdline(
            "quiet splash resume=UUID=dead-beef resume_offset=123456 mitigations=auto",
        );
        assert_eq!(device.as_deref(), Some("UUID=dead-beef"));
        assert_eq!(offset, Some(123456));
    }

    #[test]
    fn ignores_disabled_resume_configuration() {
        assert_eq!(parse_initramfs_resume("# old\nRESUME=none\n"), None);
        assert_eq!(parse_cmdline("quiet resume=none").0, None);
    }

    #[test]
    fn parses_quoted_initramfs_resume() {
        assert_eq!(
            parse_initramfs_resume("RESUME=\"UUID=dead-beef\"\n").as_deref(),
            Some("UUID=dead-beef")
        );
    }

    #[test]
    fn kernel_disk_mode_is_not_configuration() {
        assert_eq!(
            parse_disk_modes("[platform] shutdown reboot suspend test_resume\n"),
            vec!["platform", "shutdown", "reboot", "suspend", "test_resume"]
        );
        assert!(parse_disk_modes("[disabled]\n").is_empty());
    }

    #[test]
    fn hibernation_capacity_matches_installer_policy() {
        assert_eq!(required_hibernation_bytes(512 * 1024 * 1024), 2 * GIB);
        assert_eq!(required_hibernation_bytes(8 * GIB), 9 * GIB);
        assert_eq!(required_hibernation_bytes(8 * GIB + 1), 10 * GIB);
    }

    #[test]
    fn support_alone_is_not_reported_as_ready() {
        let status = HibernationStatus {
            system_supports: true,
            disk_modes: vec!["platform".to_string()],
            ..HibernationStatus::default()
        };
        assert!(!hibernation_path_is_ready(&status));
    }

    #[test]
    fn ready_requires_an_active_capacity_qualified_target() {
        let mut status = HibernationStatus {
            system_supports: true,
            disk_modes: vec!["platform".to_string()],
            configured_target: Some("UUID=dead-beef".to_string()),
            resolved_target: Some("/dev/vda3".to_string()),
            target_active: true,
            required_size_bytes: 9 * GIB,
            target_size_bytes: 8 * GIB,
            ..HibernationStatus::default()
        };
        assert!(!hibernation_path_is_ready(&status));
        status.target_size_bytes = 9 * GIB;
        assert!(hibernation_path_is_ready(&status));
    }

    #[test]
    fn exact_capacity_ignores_proc_swaps_header_shortfall() {
        assert_eq!(
            prefer_exact_capacity(9 * GIB - 4096, Some(9 * GIB)),
            9 * GIB
        );
        assert_eq!(prefer_exact_capacity(9 * GIB - 4096, None), 9 * GIB - 4096);
    }

    #[test]
    fn inactive_managed_swapfile_remains_the_resume_target() {
        let managed = SwapStatus {
            active: false,
            path: "/test/swapfile".to_string(),
            kind: SwapDeviceKind::File,
            size_bytes: 9 * GIB,
            ..SwapStatus::default()
        };
        assert!(managed_swapfile_matches_target(
            "/dev/vda4",
            Some(123456),
            &managed,
            Some("/dev/vda4"),
        ));

        let missing = SwapStatus {
            path: "/test/swapfile".to_string(),
            kind: SwapDeviceKind::File,
            ..SwapStatus::default()
        };
        assert!(!managed_swapfile_matches_target(
            "/dev/vda4",
            Some(123456),
            &missing,
            Some("/dev/vda4"),
        ));
    }
}
