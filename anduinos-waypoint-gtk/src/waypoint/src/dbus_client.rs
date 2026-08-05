//! D-Bus client for communicating with waypoint-helper privileged service
//!
//! This module provides a safe, blocking interface to the waypoint-helper D-Bus service,
//! which runs with elevated privileges to perform snapshot operations.
//!
//! # Architecture
//! - GUI application (unprivileged) ↔ D-Bus IPC ↔ waypoint-helper (privileged)
//! - All operations require Polkit authorization
//! - Operations are blocking and should be run in background threads for UI responsiveness
//!
//! # Example
//! ```no_run
//! use waypoint::dbus_client::WaypointHelperClient;
//!
//! let client = WaypointHelperClient::new()?;
//! let (success, msg) = client.create_deployment(
//!     "backup-2025".to_string(),
//!     "Before upgrade".to_string(),
//!     false,
//! )?;
//! # Ok::<(), anyhow::Error>(())
//! ```

use anyhow::{Context, Result};
use waypoint_common::*;
use zbus::blocking::Connection as BlockingConnection;

#[derive(Debug, Clone, serde::Deserialize)]
pub struct RecoveryDeployment {
    pub id: String,
    pub kind: String,
    pub state: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub title: String,
    pub reason: String,
    pub schedule_id: Option<String>,
    pub kernel_release: Option<String>,
    pub pinned: bool,
}

#[derive(Debug, serde::Deserialize)]
pub struct PendingRecovery {
    pub target_deployment_id: String,
    pub phase: String,
}

#[derive(Debug, serde::Deserialize)]
pub struct RecoveryEngineStatus {
    pub available: bool,
    pub deployments: Vec<RecoveryDeployment>,
    pub pending: Option<PendingRecovery>,
    pub issues: Vec<serde_json::Value>,
    pub layout: serde_json::Value,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct ExternalBackupDestination {
    pub filesystem_uuid: String,
    pub mount_point: std::path::PathBuf,
    pub filesystem_type: String,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct ExternalBackupSource {
    pub id: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub title: String,
    pub reason: String,
    pub kernel_release: Option<String>,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct ExternalBackupManifest {
    pub backup_id: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub source: ExternalBackupSource,
    pub stream_sha256: String,
    pub stream_size_bytes: u64,
    pub referenced_bytes: u64,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct ExternalBackupIssue {
    pub entry: String,
    pub message: String,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct ExternalBackupDiscovery {
    pub backups: Vec<ExternalBackupManifest>,
    pub issues: Vec<ExternalBackupIssue>,
}

/// Result of snapshot integrity verification
///
/// Contains validation status and any errors or warnings found during verification.
/// A snapshot is considered valid only if `is_valid` is true and `errors` is empty.
#[derive(Debug, serde::Deserialize)]
pub struct VerificationResult {
    /// Whether the snapshot passed all validation checks
    pub is_valid: bool,
    /// Critical errors that make the recovery point invalid
    pub errors: Vec<String>,
    /// Non-critical issues that don't affect validity (e.g., missing metadata)
    pub warnings: Vec<String>,
}

/// Information about a single package change during restore
///
/// Represents the difference between the current system state and the snapshot state
/// for a single package.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct PackageChange {
    /// Package name
    pub name: String,
    /// Currently installed version (None if not installed)
    pub current_version: Option<String>,
    /// Version in the snapshot (None if not present in snapshot)
    pub snapshot_version: Option<String>,
}

/// Preview of system changes that will occur during snapshot restore
///
/// Provides a comprehensive summary of what will change if a restore operation proceeds,
/// including package changes, kernel changes, and the fixed recovery scope.
///
/// This allows users to review changes before committing to a restore operation.
#[derive(Debug, serde::Deserialize)]
pub struct RestorePreview {
    /// Name of the snapshot being restored
    pub snapshot_name: String,
    /// When the snapshot was created (formatted string)
    pub snapshot_timestamp: String,
    /// Optional description provided when snapshot was created
    pub snapshot_description: Option<String>,
    /// Currently running kernel version
    pub current_kernel: Option<String>,
    /// Kernel version from the snapshot
    pub snapshot_kernel: Option<String>,
    /// Packages that will be installed (present in snapshot but not in current system)
    pub packages_to_add: Vec<PackageChange>,
    /// Packages that will be removed (present in current system but not in snapshot)
    pub packages_to_remove: Vec<PackageChange>,
    /// Packages that will be upgraded (newer version in snapshot)
    pub packages_to_upgrade: Vec<PackageChange>,
    /// Packages that will be downgraded (older version in snapshot)
    pub packages_to_downgrade: Vec<PackageChange>,
    /// Total number of package changes across all categories
    pub total_package_changes: usize,
}

/// Blocking D-Bus client for waypoint-helper privileged service
///
/// Provides methods to create, delete, restore, and verify btrfs snapshots through
/// the waypoint-helper D-Bus service. All operations require Polkit authorization.
///
/// # Thread Safety
/// This client uses blocking I/O and should be used from background threads when
/// called from GUI code to avoid blocking the UI.
///
/// # Connection
/// Connects to the system D-Bus bus. The waypoint-helper service must be running
/// (typically activated automatically via D-Bus service activation).
pub struct WaypointHelperClient {
    connection: BlockingConnection,
}

impl WaypointHelperClient {
    /// Connect to the waypoint-helper D-Bus service
    ///
    /// Establishes a connection to the system D-Bus bus and prepares to communicate
    /// with the waypoint-helper service.
    ///
    /// # Errors
    /// - D-Bus system bus connection failure (check if dbus-daemon is running)
    ///
    /// # Example
    /// ```no_run
    /// use waypoint::dbus_client::WaypointHelperClient;
    ///
    /// let client = WaypointHelperClient::new()?;
    /// # Ok::<(), anyhow::Error>(())
    /// ```
    pub fn new() -> Result<Self> {
        let connection = BlockingConnection::system().context("Failed to connect to system bus")?;

        Ok(Self { connection })
    }

    pub fn recovery_engine_status(&self) -> Result<RecoveryEngineStatus> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        let json: String = proxy
            .call("GetRecoveryEngineStatus", &())
            .context("Failed to query the recovery engine")?;
        serde_json::from_str(&json).context("Failed to parse recovery engine status")
    }

    pub fn create_deployment(
        &self,
        title: String,
        reason: String,
        pinned: bool,
    ) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("CreateDeployment", &(title, reason, pinned))
            .context("Failed to create a system recovery point")
    }

    pub fn delete_deployment(&self, id: String) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("DeleteDeployment", &(id,))
            .context("Failed to delete the recovery point")
    }

    pub fn set_deployment_pinned(&self, id: String, pinned: bool) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("SetDeploymentPinned", &(id, pinned))
            .context("Failed to change recovery-point protection")
    }

    pub fn schedule_deployment_restore(&self, id: String) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("ScheduleDeploymentRestore", &(id,))
            .context("Failed to schedule the recovery point")
    }

    pub fn cancel_deployment_restore(&self) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("CancelDeploymentRestore", &())
            .context("Failed to cancel the pending restore")
    }

    pub fn get_deployment_spaces(
        &self,
        deployment_ids: Vec<String>,
    ) -> Result<std::collections::HashMap<String, waypoint_common::SnapshotSpace>> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        let json: String = proxy
            .call("GetDeploymentSpaces", &(deployment_ids,))
            .context("Failed to get Btrfs deployment space accounting")?;
        serde_json::from_str(&json).context("Failed to parse deployment space accounting")
    }

    pub fn list_backup_destinations(&self) -> Result<Vec<ExternalBackupDestination>> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        let (success, result): (bool, String) = proxy
            .call("ListBackupDestinations", &())
            .context("Failed to list external backup destinations")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse external backup destinations")
    }

    pub fn list_external_backups(
        &self,
        filesystem_uuid: String,
    ) -> Result<ExternalBackupDiscovery> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        let (success, result): (bool, String) = proxy
            .call("ListExternalBackups", &(filesystem_uuid,))
            .context("Failed to list external backups")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse external backups")
    }

    pub fn export_deployment(
        &self,
        deployment_id: String,
        filesystem_uuid: String,
    ) -> Result<ExternalBackupManifest> {
        self.external_backup_call(
            "ExportDeployment",
            &(deployment_id, filesystem_uuid),
            "Failed to export the recovery point",
        )
    }

    pub fn verify_external_backup(
        &self,
        filesystem_uuid: String,
        backup_id: String,
    ) -> Result<ExternalBackupManifest> {
        self.external_backup_call(
            "VerifyExternalBackup",
            &(filesystem_uuid, backup_id),
            "Failed to verify the external backup",
        )
    }

    pub fn import_external_backup(
        &self,
        filesystem_uuid: String,
        backup_id: String,
    ) -> Result<RecoveryDeployment> {
        self.external_backup_call(
            "ImportExternalBackup",
            &(filesystem_uuid, backup_id),
            "Failed to import the external backup",
        )
    }

    pub fn delete_external_backup(&self, filesystem_uuid: String, backup_id: String) -> Result<()> {
        let _: serde_json::Value = self.external_backup_call(
            "DeleteExternalBackup",
            &(filesystem_uuid, backup_id),
            "Failed to delete the external backup",
        )?;
        Ok(())
    }

    fn external_backup_call<T, B>(&self, method: &str, body: &B, context: &'static str) -> Result<T>
    where
        T: serde::de::DeserializeOwned,
        B: serde::ser::Serialize + zbus::zvariant::DynamicType,
    {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        let (success, result): (bool, String) = proxy.call(method, body).context(context)?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse external backup response")
    }

    /// Verify snapshot integrity and consistency
    ///
    /// Checks if a snapshot is valid by verifying:
    /// - Snapshot directory exists
    /// - The immutable deployment root is present and has a trusted Btrfs identity
    /// - Metadata is consistent (if available)
    ///
    /// # Arguments
    /// * `name` - Snapshot name to verify
    ///
    /// # Returns
    /// `VerificationResult` containing validation status, errors, and warnings
    ///
    /// # Errors
    /// - D-Bus connection failure
    /// - JSON parsing error
    ///
    /// # Note
    /// This is a read-only operation and does not require authentication.
    /// Older snapshots may show warnings about missing metadata, which is normal.
    pub fn verify_snapshot(&self, name: String) -> Result<VerificationResult> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;

        let json: String = proxy
            .call("VerifySnapshot", &(name,))
            .context("Failed to call VerifySnapshot")?;

        let result: VerificationResult =
            serde_json::from_str(&json).context("Failed to parse verification result")?;

        Ok(result)
    }

    /// Preview system changes before restoring a snapshot
    ///
    /// Analyzes the differences between the current system state and the snapshot
    /// to show what will change if the restore proceeds. This includes package
    /// changes, kernel changes, and the fixed System-only scope.
    ///
    /// # Arguments
    /// * `name` - Snapshot name to preview
    ///
    /// # Returns
    /// `RestorePreview` containing detailed change information
    ///
    /// # Errors
    /// - D-Bus connection failure
    /// - Snapshot not found
    /// - Package list comparison failure
    /// - JSON parsing error
    ///
    /// # Security
    /// Requires restore authorization via Polkit before data is returned.
    pub fn preview_restore(&self, name: String) -> Result<RestorePreview> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;

        let result: (bool, String) = proxy
            .call("PreviewRestore", &(name,))
            .context("Failed to call PreviewRestore")?;

        if !result.0 {
            anyhow::bail!(result.1);
        }

        let preview: RestorePreview =
            serde_json::from_str(&result.1).context("Failed to parse restore preview result")?;

        Ok(preview)
    }

    /// Save schedules TOML configuration file
    ///
    /// Writes the schedules configuration in TOML format to the system config directory.
    /// This requires elevated privileges.
    ///
    /// # Arguments
    /// * `toml_content` - Complete schedules configuration in TOML format
    ///
    /// # Returns
    /// * `Ok((true, msg))` - Configuration saved successfully
    /// * `Ok((false, msg))` - Save failed, `msg` contains error details
    /// * `Err(_)` - D-Bus communication error
    ///
    /// # Errors
    /// - D-Bus connection failure
    /// - Polkit authorization denied
    /// - Invalid TOML configuration
    /// - File write permission error
    ///
    /// # Security
    /// Requires root privileges via Polkit authentication.
    pub fn save_schedules_config(&self, toml_content: String) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;

        let result: (bool, String) = proxy
            .call("SaveSchedulesConfig", &(toml_content,))
            .context("Failed to call SaveSchedulesConfig")?;

        Ok(result)
    }

    /// Restart the snapshot scheduler service
    ///
    /// Restarts the systemd service that runs scheduled snapshots. Call this after
    /// updating scheduler configuration to apply changes.
    ///
    /// # Returns
    /// * `Ok((true, msg))` - Service restarted successfully
    /// * `Ok((false, msg))` - Restart failed, `msg` contains error details
    /// * `Err(_)` - D-Bus communication error
    ///
    /// # Errors
    /// - D-Bus connection failure
    /// - Polkit authorization denied
    /// - Service control command failure
    ///
    /// # Security
    /// Requires root privileges via Polkit authentication.
    pub fn restart_scheduler(&self) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;

        let result: (bool, String) = proxy
            .call("RestartScheduler", &())
            .context("Failed to call RestartScheduler")?;

        Ok(result)
    }

    /// Get current status of the snapshot scheduler service
    ///
    /// Queries systemd for the current state of the
    /// waypoint-snapshots service.
    ///
    /// # Returns
    /// Service status string (e.g., "run", "down", "finish")
    ///
    /// # Errors
    /// - D-Bus connection failure
    /// - Service status query failure
    ///
    /// # Note
    /// This is a read-only operation and does not require authentication.
    pub fn get_scheduler_status(&self) -> Result<String> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;

        let status: String = proxy
            .call("GetSchedulerStatus", &())
            .context("Failed to call GetSchedulerStatus")?;

        Ok(status)
    }

    /// Compare two snapshots and get list of changed files
    ///
    /// Returns JSON string containing array of changes
    ///
    /// **Limitation**: Due to a 25-second D-Bus timeout in zbus 4.0, this operation
    /// will fail for large snapshots that take longer than 25 seconds to compare.
    /// This is a known limitation. For very large snapshots, use package comparison instead.
    ///
    pub fn compare_snapshots(
        &self,
        old_snapshot_name: String,
        new_snapshot_name: String,
    ) -> Result<String> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;

        let result: (bool, String) = proxy
            .call("CompareSnapshots", &(old_snapshot_name, new_snapshot_name))
            .context("Failed to call CompareSnapshots")?;

        if !result.0 {
            anyhow::bail!(result.1);
        }

        Ok(result.1)
    }

    /// Compare package states captured inside two trusted deployments.
    pub fn compare_deployment_packages(
        &self,
        old_snapshot_name: String,
        new_snapshot_name: String,
    ) -> Result<crate::packages::PackageDiff> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        let (success, result): (bool, String) = proxy
            .call(
                "CompareDeploymentPackages",
                &(old_snapshot_name, new_snapshot_name),
            )
            .context("Failed to compare recovery-point package states")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse package comparison")
    }

    /// Get quota usage information
    pub fn get_quota_usage(&self) -> Result<waypoint_common::QuotaUsage> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;

        let result: (bool, String) = proxy
            .call("GetQuotaUsage", &())
            .context("Failed to call GetQuotaUsage")?;

        if !result.0 {
            anyhow::bail!(result.1);
        }

        let usage: waypoint_common::QuotaUsage = serde_json::from_str(&result.1)?;
        Ok(usage)
    }
}
