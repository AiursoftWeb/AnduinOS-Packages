// Centralized configuration for AnduinOS Waypoint

use std::path::PathBuf;

/// Waypoint configuration with support for environment variable overrides
#[derive(Debug, Clone)]
pub struct WaypointConfig {
    /// Directory containing immutable deployment directories.
    pub snapshot_dir: PathBuf,

    /// Path to metadata file (default: /var/lib/anduinos-waypoint/snapshots.json)
    pub metadata_file: PathBuf,

    /// Path to scheduler configuration (default: /etc/anduinos-waypoint/scheduler.conf)
    /// DEPRECATED: Use schedules_config instead
    pub scheduler_config: PathBuf,

    /// Path to schedules TOML configuration (default: /etc/anduinos-waypoint/schedules.toml)
    pub schedules_config: PathBuf,

    /// Path to APT snapshot policy (default: /etc/anduinos-waypoint/apt-snapshots.toml)
    pub apt_snapshot_policy: PathBuf,

    /// systemd unit name for the scheduler.
    pub scheduler_service_unit: String,

    /// Minimum free space required before creating snapshots (in bytes)
    pub min_free_space_bytes: u64,

    /// Default window width
    pub ui_window_width: i32,

    /// Default window height
    pub ui_window_height: i32,

    /// Maximum window width
    pub ui_max_width: i32,

    /// Default maximum number of snapshots to retain
    pub retention_max_snapshots: usize,

    /// Default maximum age for snapshots (in days)
    pub retention_max_age_days: u64,

    /// Minimum number of snapshots to always keep
    pub retention_min_snapshots: usize,
}

impl Default for WaypointConfig {
    fn default() -> Self {
        Self {
            snapshot_dir: PathBuf::from("/.snapshots/anduinos-waypoint/deployments"),
            metadata_file: PathBuf::from("/var/lib/anduinos-waypoint/snapshots.json"),
            scheduler_config: PathBuf::from("/etc/anduinos-waypoint/scheduler.conf"),
            schedules_config: PathBuf::from("/etc/anduinos-waypoint/schedules.toml"),
            apt_snapshot_policy: PathBuf::from("/etc/anduinos-waypoint/apt-snapshots.toml"),
            scheduler_service_unit: "anduinos-waypoint-scheduler.service".to_string(),
            min_free_space_bytes: 1024 * 1024 * 1024, // 1 GB
            ui_window_width: 800,
            ui_window_height: 600,
            ui_max_width: 800,
            retention_max_snapshots: 10,
            retention_max_age_days: 30,
            retention_min_snapshots: 3,
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

        if let Ok(file) = std::env::var("ANDUINOS_WAYPOINT_METADATA_FILE") {
            config.metadata_file = PathBuf::from(file);
        }

        if let Ok(conf) = std::env::var("ANDUINOS_WAYPOINT_SCHEDULER_CONFIG") {
            config.scheduler_config = PathBuf::from(conf);
        }

        if let Ok(conf) = std::env::var("ANDUINOS_WAYPOINT_SCHEDULES_CONFIG") {
            config.schedules_config = PathBuf::from(conf);
        }

        if let Ok(conf) = std::env::var("ANDUINOS_WAYPOINT_APT_POLICY") {
            config.apt_snapshot_policy = PathBuf::from(conf);
        }

        if let Ok(unit) = std::env::var("ANDUINOS_WAYPOINT_SCHEDULER_UNIT") {
            config.scheduler_service_unit = unit;
        }

        if let Ok(space_gb) = std::env::var("ANDUINOS_WAYPOINT_MIN_FREE_SPACE_GB")
            && let Ok(gb) = space_gb.parse::<u64>()
        {
            config.min_free_space_bytes = gb * 1024 * 1024 * 1024;
        }

        config
    }

    /// Get the systemd scheduler unit name.
    pub fn scheduler_service_name(&self) -> &str {
        &self.scheduler_service_unit
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
        assert_eq!(
            config.metadata_file,
            PathBuf::from("/var/lib/anduinos-waypoint/snapshots.json")
        );
        assert_eq!(config.min_free_space_bytes, 1024 * 1024 * 1024);
        assert_eq!(config.ui_window_width, 800);
        assert_eq!(config.ui_window_height, 600);
        assert_eq!(
            config.apt_snapshot_policy,
            PathBuf::from("/etc/anduinos-waypoint/apt-snapshots.toml")
        );
    }

    #[test]
    fn test_scheduler_service_name() {
        let config = WaypointConfig::default();
        assert_eq!(
            config.scheduler_service_name(),
            "anduinos-waypoint-scheduler.service"
        );
    }
}
