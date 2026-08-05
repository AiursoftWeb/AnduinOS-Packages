use anyhow::Result;
use chrono::{DateTime, Utc};
use std::fmt;

use crate::i18n::{tr, trf};

/// Metadata for a snapshot
#[derive(Debug, Clone)]
pub struct Snapshot {
    pub id: String,
    pub kind: String,
    pub state: String,
    pub pinned: bool,
    pub name: String,
    pub timestamp: DateTime<Utc>,
    pub description: Option<String>,
    pub kernel_version: Option<String>,
    pub package_count: Option<usize>,
    pub size_bytes: Option<u64>,
}

impl Snapshot {
    /// Format timestamp for display
    pub fn format_timestamp(&self) -> String {
        self.timestamp.format("%Y-%m-%d %H:%M:%S").to_string()
    }

    /// Format timestamp as relative time (e.g., "2 hours ago", "Yesterday")
    pub fn format_relative_time(&self) -> String {
        let now = chrono::Local::now();
        let duration = now.signed_duration_since(self.timestamp);

        if duration.num_seconds() < 60 {
            tr("Just now")
        } else if duration.num_minutes() < 60 {
            let mins = duration.num_minutes();
            if mins == 1 {
                tr("1 minute ago")
            } else {
                trf("{0} minutes ago", &[&mins.to_string()])
            }
        } else if duration.num_hours() < 24 {
            let hours = duration.num_hours();
            if hours == 1 {
                tr("1 hour ago")
            } else {
                trf("{0} hours ago", &[&hours.to_string()])
            }
        } else if duration.num_days() == 1 {
            tr("Yesterday")
        } else if duration.num_days() < 7 {
            trf("{0} days ago", &[&duration.num_days().to_string()])
        } else if duration.num_weeks() == 1 {
            tr("1 week ago")
        } else if duration.num_weeks() < 4 {
            trf("{0} weeks ago", &[&duration.num_weeks().to_string()])
        } else {
            // Numeric order avoids hard-coded English month names.
            self.timestamp.format("%Y-%m-%d").to_string()
        }
    }
}

// Re-export format_bytes from waypoint_common
pub use waypoint_common::format_bytes;

/// Read the helper-owned recovery deployment model for the GTK views.
pub struct SnapshotManager;

/// A stable UI-facing classification for recovery-point loading failures.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SnapshotLoadError {
    UnsupportedLayout,
    RecoveryService(String),
}

impl SnapshotLoadError {
    pub fn is_unsupported_layout(&self) -> bool {
        matches!(self, Self::UnsupportedLayout)
    }
}

impl fmt::Display for SnapshotLoadError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedLayout => formatter
                .write_str("The current system does not use the supported AnduinOS Btrfs layout"),
            Self::RecoveryService(details) => {
                write!(formatter, "The recovery service is unavailable: {details}")
            }
        }
    }
}

impl std::error::Error for SnapshotLoadError {}

impl SnapshotManager {
    /// Create a new snapshot manager
    ///
    pub fn new() -> Self {
        Self
    }

    /// Load the helper-owned immutable system recovery points.
    ///
    /// # Returns
    /// Vector of recovery points, newest first.
    ///
    /// # Errors
    /// - D-Bus/helper unavailable
    /// - Malformed helper response
    pub fn load_snapshots(&self) -> std::result::Result<Vec<Snapshot>, SnapshotLoadError> {
        let client = crate::dbus_client::WaypointHelperClient::new()
            .map_err(|error| SnapshotLoadError::RecoveryService(error.to_string()))?;
        let status = client
            .recovery_engine_status()
            .map_err(|error| SnapshotLoadError::RecoveryService(error.to_string()))?;
        if status
            .layout
            .get("support")
            .and_then(|value| value.as_str())
            != Some("supported")
        {
            return Err(SnapshotLoadError::UnsupportedLayout);
        }
        if !status.available {
            return Err(SnapshotLoadError::RecoveryService(
                "the helper reported that recovery is unavailable".to_string(),
            ));
        }
        if !status.issues.is_empty() {
            log::warn!(
                "Recovery metadata contains {} issue(s)",
                status.issues.len()
            );
        }
        if status.pending.is_some() {
            log::info!("A system restore transaction is pending");
        }
        Ok(status
            .deployments
            .into_iter()
            .map(|deployment| Snapshot {
                id: deployment.id.clone(),
                kind: deployment.kind,
                state: deployment.state,
                pinned: deployment.pinned,
                name: deployment.title,
                timestamp: deployment.created_at,
                description: Some(deployment.reason),
                kernel_version: deployment.kernel_release,
                package_count: None,
                size_bytes: None,
            })
            .collect())
    }

    /// Get snapshot by ID
    ///
    /// Loads all snapshots and searches for one matching the given ID.
    ///
    /// # Arguments
    /// * `id` - Snapshot ID to search for
    ///
    /// # Returns
    /// * `Ok(Some(snapshot))` - Snapshot found
    /// * `Ok(None)` - Snapshot not found
    /// * `Err(_)` - Failed to load snapshots
    pub fn get_snapshot(&self, id: &str) -> Result<Option<Snapshot>> {
        let snapshots = self.load_snapshots()?;
        Ok(snapshots.into_iter().find(|s| s.id == id))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_bytes() {
        assert_eq!(format_bytes(512), "512.00 B");
        assert_eq!(format_bytes(1024), "1.00 KiB");
        assert_eq!(format_bytes(1024 * 1024), "1.00 MiB");
        assert_eq!(format_bytes(1024 * 1024 * 1024), "1.00 GiB");
    }

    #[test]
    fn snapshot_load_failures_keep_layout_and_service_errors_distinct() {
        assert!(SnapshotLoadError::UnsupportedLayout.is_unsupported_layout());
        assert!(
            !SnapshotLoadError::RecoveryService("D-Bus unavailable".to_string())
                .is_unsupported_layout()
        );
        assert_eq!(
            SnapshotLoadError::UnsupportedLayout.to_string(),
            "The current system does not use the supported AnduinOS Btrfs layout"
        );
    }
}
