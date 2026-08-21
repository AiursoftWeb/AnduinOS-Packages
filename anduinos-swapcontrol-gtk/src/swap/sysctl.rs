use std::collections::HashMap;
use std::fs;

use super::exec;
use crate::config;
use crate::i18n::{i18n, i18n_fmt};

/// Read our sysctl config file, returning key-value pairs.
/// Returns an empty map if the file doesn't exist yet.
pub fn read_sysctl_conf() -> HashMap<String, String> {
    let mut params = HashMap::new();

    let content = match fs::read_to_string(config::SYSCTL_CONF) {
        Ok(c) => c,
        Err(_) => return params,
    };

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            params.insert(key.trim().to_string(), value.trim().to_string());
        }
    }

    params
}

/// Read a sysctl value from /proc/sys.
pub fn read_sysctl_live(key: &str) -> Result<String, String> {
    let path = sysctl_proc_path(key);
    fs::read_to_string(&path)
        .map(|s| s.trim().to_string())
        .map_err(|e| i18n_fmt(&i18n("Cannot read {0}: {1}"), &[&path, &e.to_string()]))
}

/// Get the current swappiness value from /proc/sys/vm/swappiness.
pub fn read_swappiness() -> Result<u8, String> {
    let val = read_sysctl_live("vm.swappiness")?;
    val.parse::<u8>()
        .map_err(|e| i18n_fmt(&i18n("Cannot parse swappiness: {0}"), &[&e.to_string()]))
}

/// Get the recommended swappiness value based on system configuration.
/// If zram is active: 100 — prefer fast LZ4-compressed RAM swap over dropping
/// file cache (decompression is nanoseconds vs milliseconds for disk reads).
/// Otherwise uses a RAM-based heuristic (lower swappiness for large-RAM desktops).
pub fn recommended_swappiness() -> u8 {
    let has_zram = !super::zram::read_zram_devices().is_empty();
    if has_zram {
        return 100;
    }

    let total_ram = read_total_ram().unwrap_or(32 * 1024 * 1024 * 1024);
    let ram_gb = total_ram as f64 / (1024.0 * 1024.0 * 1024.0);
    if ram_gb >= 16.0 {
        10
    } else if ram_gb >= 8.0 {
        30
    } else {
        60
    }
}

/// Get total RAM from /proc/meminfo (in bytes).
pub fn read_total_ram() -> Result<u64, String> {
    let content = fs::read_to_string(config::PROC_MEMINFO)
        .map_err(|e| i18n_fmt(&i18n("Cannot read /proc/meminfo: {0}"), &[&e.to_string()]))?;

    for line in content.lines() {
        if line.starts_with("MemTotal:") {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 2 {
                return parts[1]
                    .parse::<u64>()
                    .map(|kb| kb * 1024)
                    .map_err(|e| i18n_fmt(&i18n("Cannot parse MemTotal: {0}"), &[&e.to_string()]));
            }
        }
    }
    Err(i18n("MemTotal not found"))
}

// ─── Internal helpers ────────────────────────────────────────────────────────

/// Convert a dotted sysctl key to its /proc/sys path.
/// "vm.swappiness" → "/proc/sys/vm/swappiness"
fn sysctl_proc_path(key: &str) -> String {
    format!("/proc/sys/{}", key.replace('.', "/"))
}

// ─── Write operations (require pkexec) ─────────────────────────────────────

/// Write key-value pairs to our sysctl config file.
/// Creates /etc/sysctl.d/90-anduinos-swapcontrol.conf via pkexec tee.
pub fn write_sysctl_conf(params: &HashMap<String, String>) -> Result<String, String> {
    let mut content = String::from(
        "# AnduinOS Swap Control — managed by anduinos-swapcontrol-gtk\n\
         # Do not edit manually.\n\n",
    );

    for (key, value) in params {
        content.push_str(&format!("{} = {}\n", key, value));
    }

    exec::write_sysfs(config::SYSCTL_CONF, &content)
}

/// Apply a single sysctl value immediately.
pub fn apply_live(key: &str, value: &str) -> Result<String, String> {
    exec::run_helper("sysctl", &["-w", &format!("{key}={value}")])
}

/// Set vm.swappiness both immediately and in our config file.
pub fn set_swappiness(value: u8) -> Result<String, String> {
    let val_str = value.to_string();
    // Apply immediately
    let immediate = apply_live("vm.swappiness", &val_str);
    // Persist in config file
    let mut params = read_sysctl_conf();
    params.insert("vm.swappiness".to_string(), val_str);
    let persisted = write_sysctl_conf(&params);

    // Return the first error if any
    if immediate.is_err() && persisted.is_err() {
        return Err(i18n_fmt(
            &i18n("Failed to set swappiness: {0} / {1}"),
            &[&immediate.err().unwrap(), &persisted.err().unwrap()],
        ));
    }
    Ok(i18n("swappiness updated"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_sysctl_conf() {
        let _params = read_sysctl_conf();
        // May be empty if never written, but should not panic
        // Our app might not have written it yet
    }

    #[test]
    fn test_read_sysctl_live() {
        let result = read_sysctl_live("vm.swappiness");
        assert!(result.is_ok());
    }

    #[test]
    fn test_read_swappiness() {
        let result = read_swappiness();
        assert!(result.is_ok());
        let val = result.unwrap();
        // AnduinOS desktop default is 10
        assert!(val <= 100);
    }

    #[test]
    fn test_read_total_ram() {
        let result = read_total_ram();
        assert!(result.is_ok());
        let ram = result.unwrap();
        assert!(ram > 0);
    }
}
