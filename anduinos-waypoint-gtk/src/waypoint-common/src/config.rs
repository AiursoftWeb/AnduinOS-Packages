// Centralized configuration for AnduinOS Waypoint

use std::path::PathBuf;

/// Waypoint configuration with support for environment variable overrides
#[derive(Debug, Clone)]
pub struct WaypointConfig {
    /// Directory containing immutable deployment directories.
    pub snapshot_dir: PathBuf,

    /// Waypoint 2.0 automatic snapshot and GFS retention configuration.
    pub automation_config: PathBuf,

    /// Path to APT snapshot policy (default: /etc/anduinos-waypoint/apt-snapshots.toml)
    pub apt_snapshot_policy: PathBuf,

    /// Minimum free space required before creating snapshots (in bytes)
    pub min_free_space_bytes: u64,

    /// Default window width
    pub ui_window_width: i32,

    /// Default window height
    pub ui_window_height: i32,

    /// Maximum window width
    pub ui_max_width: i32,
}

impl Default for WaypointConfig {
    fn default() -> Self {
        Self {
            snapshot_dir: PathBuf::from("/.snapshots/anduinos-waypoint/deployments"),
            automation_config: PathBuf::from("/etc/anduinos-waypoint/automation.toml"),
            apt_snapshot_policy: PathBuf::from("/etc/anduinos-waypoint/apt-snapshots.toml"),
            min_free_space_bytes: 1024 * 1024 * 1024, // 1 GB
            ui_window_width: 800,
            ui_window_height: 600,
            ui_max_width: 800,
        }
    }
}

impl WaypointConfig {
    /// Create a new configuration with environment variable overrides
    ///
    /// Supported environment variables:
    /// - ANDUINOS_WAYPOINT_SNAPSHOT_DIR: Override snapshot directory
    /// - ANDUINOS_WAYPOINT_METADATA_FILE: Override metadata file path
    /// - ANDUINOS_WAYPOINT_SCHEDULER_CONFIG: Override scheduler config path (deprecated)
    /// - ANDUINOS_WAYPOINT_SCHEDULES_CONFIG: Override schedules TOML config path
    /// - ANDUINOS_WAYPOINT_APT_POLICY: Override APT snapshot policy path
    /// - ANDUINOS_WAYPOINT_SCHEDULER_UNIT: Override the systemd scheduler unit
    /// - ANDUINOS_WAYPOINT_MIN_FREE_SPACE_GB: Override minimum free space (in GB)
    pub fn new() -> Self {
        let mut config = Self::default();

        // Override from environment variables
        if let Ok(dir) = std::env::var("ANDUINOS_WAYPOINT_SNAPSHOT_DIR") {
            config.snapshot_dir = PathBuf::from(dir);
        }

        if let Ok(conf) = std::env::var("ANDUINOS_WAYPOINT_AUTOMATION_CONFIG") {
            config.automation_config = PathBuf::from(conf);
        }

        if let Ok(conf) = std::env::var("ANDUINOS_WAYPOINT_APT_POLICY") {
            config.apt_snapshot_policy = PathBuf::from(conf);
        }

        if let Ok(space_gb) = std::env::var("ANDUINOS_WAYPOINT_MIN_FREE_SPACE_GB")
            && let Ok(gb) = space_gb.parse::<u64>()
        {
            config.min_free_space_bytes = gb * 1024 * 1024 * 1024;
        }

        config
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = WaypointConfig::default();
        assert_eq!(
            config.snapshot_dir,
            PathBuf::from("/.snapshots/anduinos-waypoint/deployments")
        );
        assert_eq!(config.min_free_space_bytes, 1024 * 1024 * 1024);
        assert_eq!(config.ui_window_width, 800);
        assert_eq!(config.ui_window_height, 600);
        assert_eq!(
            config.apt_snapshot_policy,
            PathBuf::from("/etc/anduinos-waypoint/apt-snapshots.toml")
        );
    }
}
