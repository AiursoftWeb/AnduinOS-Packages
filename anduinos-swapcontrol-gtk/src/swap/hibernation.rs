use crate::swap::types::HibernationStatus;
use std::fs;

/// Detect hibernation capability from multiple sources.
pub fn check_hibernation() -> HibernationStatus {
    let mut status = HibernationStatus::default();

    // 1. Check /sys/power/state — does the kernel support suspend-to-disk?
    if let Ok(content) = fs::read_to_string(crate::config::SYS_POWER_STATE) {
        status.system_supports = content.split_whitespace().any(|s| s == "disk");
    }

    // 2. Check /sys/power/disk — is it enabled? "[disabled]" means explicitly off
    if let Ok(content) = fs::read_to_string(crate::config::SYS_POWER_DISK) {
        status.enabled = !content.trim().starts_with("[disabled]");
    }

    // 3. Parse /proc/cmdline for resume= and resume_offset=
    if let Ok(content) = fs::read_to_string(crate::config::PROC_CMDLINE) {
        for token in content.split_whitespace() {
            if let Some(val) = token.strip_prefix("resume=") {
                status.resume_device = Some(val.to_string());
            }
            if let Some(val) = token.strip_prefix("resume_offset=") {
                status.resume_offset = val.parse::<u64>().ok();
            }
        }
    }

    // 4. Check /etc/initramfs-tools/conf.d/resume
    if let Ok(content) = fs::read_to_string(crate::config::INITRAMFS_RESUME) {
        for line in content.lines() {
            let line = line.trim();
            if let Some(val) = line.strip_prefix("RESUME=") {
                let val = val.trim_matches('"');
                if !val.is_empty() {
                    status.initramfs_resume = Some(val.to_string());
                }
            }
        }
    }

    status
}

/// Check whether hibernation is actively configured (any resume source present).
pub fn is_hibernation_configured() -> bool {
    let status = check_hibernation();
    status.enabled || status.resume_device.is_some() || status.initramfs_resume.is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_check_hibernation() {
        let status = check_hibernation();
        // This system has hibernation disabled
        assert!(!status.enabled);
    }

    #[test]
    fn test_is_hibernation_configured() {
        let result = is_hibernation_configured();
        // Should be false on this system
        assert!(!result);
    }
}
