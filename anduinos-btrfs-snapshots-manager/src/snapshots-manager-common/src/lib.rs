// Shared types and utilities for Disk Snapshots Manager

pub mod anduinos_layout;
pub mod apt_history;
pub mod automation;
pub mod config;
pub mod format;
pub mod quota;
pub mod retention_v2;

use serde::{Deserialize, Serialize};

pub use anduinos_layout::{
    LayoutReport, LayoutSupport, MountReport, inspect_current as inspect_anduinos_layout,
};
pub use automation::{AUTOMATION_SCHEMA_VERSION, AutomationConfig, NotificationPolicy};
pub use config::SnapshotsManagerConfig;
pub use format::{format_bytes, format_elapsed_time};
pub use quota::SnapshotSpace;
pub use retention_v2::{
    CleanupPolicy, RetentionAction, RetentionDecision, RetentionPolicy, RetentionPolicyError,
    RetentionReason, RetentionTier, SnapshotCandidate, evaluate_retention,
};

/// A package installed on the system
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Package {
    pub name: String,
    pub version: String,
}

/// Result of a snapshot operation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationResult {
    pub success: bool,
    pub message: String,
}

impl OperationResult {
    pub fn success(message: impl Into<String>) -> Self {
        Self {
            success: true,
            message: message.into(),
        }
    }

    pub fn error(message: impl Into<String>) -> Self {
        Self {
            success: false,
            message: message.into(),
        }
    }
}

/// D-Bus interface constants
pub const DBUS_SERVICE_NAME: &str = "org.anduinos.BtrfsSnapshotsManager";
pub const DBUS_OBJECT_PATH: &str = "/org/anduinos/BtrfsSnapshotsManager";
pub const DBUS_INTERFACE_NAME: &str = "org.anduinos.BtrfsSnapshotsManager.Helper";

/// Polkit action IDs
pub const POLKIT_ACTION_CREATE: &str = "org.anduinos.btrfs-snapshots-manager.create-snapshot";
pub const POLKIT_ACTION_DELETE: &str = "org.anduinos.btrfs-snapshots-manager.delete-snapshot";
pub const POLKIT_ACTION_RESTORE: &str = "org.anduinos.btrfs-snapshots-manager.restore-snapshot";
pub const POLKIT_ACTION_CONFIGURE: &str = "org.anduinos.btrfs-snapshots-manager.configure-system";
pub const POLKIT_ACTION_PERSONAL_FILES: &str =
    "org.anduinos.btrfs-snapshots-manager.personal-files";

/// Validate snapshot name for security and filesystem compatibility
///
/// # Arguments
/// * `name` - The snapshot name to validate
///
/// # Returns
/// `Ok(())` if the name is valid, `Err` with description if invalid
///
/// # Validation Rules
/// - Name must not be empty and must be ≤ 255 characters
/// - Cannot contain `/`, null bytes, or `..`
/// - Cannot start with `-` or `.`
/// - Cannot be exactly `.` or `..`
///
/// # Security
/// This prevents path traversal attacks and ensures filesystem safety.
/// Even though we use `.arg()` which escapes properly, this provides defense-in-depth.
pub fn validate_snapshot_name(name: &str) -> Result<(), String> {
    if name.is_empty() {
        return Err("Snapshot name cannot be empty".to_string());
    }

    if name.len() > 255 {
        return Err("Snapshot name too long (max 255 characters)".to_string());
    }

    // Reject names with problematic characters
    if name.contains('/') {
        return Err("Snapshot name cannot contain '/'".to_string());
    }

    if name.contains('\0') {
        return Err("Snapshot name cannot contain null bytes".to_string());
    }

    if name.contains("..") {
        return Err("Snapshot name cannot contain '..'".to_string());
    }

    // Reject names starting with - or .
    if name.starts_with('-') {
        return Err("Snapshot name cannot start with '-'".to_string());
    }

    if name.starts_with('.') {
        return Err("Snapshot name cannot start with '.'".to_string());
    }

    // Reject special names
    if name == "." || name == ".." {
        return Err("Snapshot name cannot be '.' or '..'".to_string());
    }

    Ok(())
}
