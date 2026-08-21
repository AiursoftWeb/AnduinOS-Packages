use crate::swap::types::ZswapConfig;
use std::fs;

use crate::config;
use crate::i18n::{i18n, i18n_fmt};

/// Read the current zswap configuration from sysfs.
pub fn read_zswap_config() -> Result<ZswapConfig, String> {
    let enabled = read_sysfs_bool(config::ZSWAP_ENABLED)?;
    let compressor = read_sysfs_string(config::ZSWAP_COMPRESSOR)?;
    let max_pool_percent = read_sysfs_u8(config::ZSWAP_MAX_POOL_PERCENT)?;
    let accept_threshold_percent = read_sysfs_u8(config::ZSWAP_ACCEPT_THRESHOLD)?;
    let shrinker_enabled = read_sysfs_bool(config::ZSWAP_SHRINKER)?;

    Ok(ZswapConfig {
        enabled,
        compressor,
        max_pool_percent,
        accept_threshold_percent,
        shrinker_enabled,
    })
}

/// Known zswap-supported compression algorithms.
/// These are crypto compression API names — they differ from kernel module names
/// (e.g. module `lz4_compress` registers as `lz4`).
/// /proc/crypto only lists async transforms, so we can't reliably probe
/// availability.  `set_compressor()` handles modprobe + error reporting
/// at selection time.
pub fn get_available_compressors() -> Vec<String> {
    vec![
        "lz4".to_string(),
        "zstd".to_string(),
        "lz4hc".to_string(),
        "lzo".to_string(),
        "lzo-rle".to_string(),
        "deflate".to_string(),
        "842".to_string(),
    ]
}

// ─── Internal sysfs helpers ──────────────────────────────────────────────────

fn read_sysfs_string(path: &str) -> Result<String, String> {
    fs::read_to_string(path)
        .map(|s| s.trim().to_string())
        .map_err(|e| i18n_fmt(&i18n("Cannot read {0}: {1}"), &[&path, &e.to_string()]))
}

fn read_sysfs_bool(path: &str) -> Result<bool, String> {
    let val = read_sysfs_string(path)?;
    Ok(val == "1" || val.eq_ignore_ascii_case("Y") || val.eq_ignore_ascii_case("true"))
}

fn read_sysfs_u8(path: &str) -> Result<u8, String> {
    let val = read_sysfs_string(path)?;
    val.parse::<u8>().map_err(|e| {
        i18n_fmt(
            &i18n("Cannot parse {0} as u8: {1}"),
            &[&path, &e.to_string()],
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_zswap_config() {
        let result = read_zswap_config();
        assert!(result.is_ok());
    }

    #[test]
    fn test_get_available_compressors() {
        let compressors = get_available_compressors();
        assert!(!compressors.is_empty());
        assert!(compressors.contains(&"lzo".to_string()));
    }
}
