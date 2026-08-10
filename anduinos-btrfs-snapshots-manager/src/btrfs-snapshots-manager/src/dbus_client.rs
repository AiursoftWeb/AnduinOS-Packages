//! D-Bus client for communicating with btrfs-snapshots-manager-helper privileged service
//!
//! This module provides a safe, blocking interface to the btrfs-snapshots-manager-helper D-Bus service,
//! which runs with elevated privileges to perform snapshot operations.
//!
//! # Architecture
//! - GUI application (unprivileged) ↔ D-Bus IPC ↔ btrfs-snapshots-manager-helper (privileged)
//! - All operations require Polkit authorization
//! - Operations are blocking and should be run in background threads for UI responsiveness
//!
use anyhow::{Context, Result};
use snapshots_manager_common::*;
use zbus::blocking::Connection as BlockingConnection;

#[derive(Debug, Clone, serde::Deserialize)]
pub struct RecoveryDeployment {
    pub id: String,
    pub kind: String,
    pub state: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub title: String,
    pub reason: String,
    pub kernel_release: Option<String>,
    pub pinned: bool,
}

#[derive(Debug, serde::Deserialize)]
pub struct PendingRecovery {
    pub target_deployment_id: String,
    pub phase: String,
    #[serde(default)]
    pub failure: Option<String>,
}

#[derive(Debug, Default, serde::Deserialize)]
pub struct LayoutSummary {
    #[serde(default)]
    pub support: String,
    #[serde(default)]
    pub root_filesystem: Option<String>,
    #[serde(default)]
    pub issues: Vec<String>,
}

#[derive(Debug, serde::Deserialize)]
pub struct RecoveryEngineStatus {
    #[serde(default)]
    pub available: bool,
    #[serde(default)]
    pub deployments: Vec<RecoveryDeployment>,
    #[serde(default)]
    pub pending: Option<PendingRecovery>,
    #[serde(default)]
    pub issues: Vec<serde_json::Value>,
    #[serde(default)]
    pub layout: LayoutSummary,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub personal_snapshots: Vec<PersonalSnapshot>,
    #[serde(default)]
    pub personal_issues: Vec<serde_json::Value>,
    #[serde(default)]
    pub system_package_counts: std::collections::HashMap<String, usize>,
    #[serde(default)]
    pub system_sizes: std::collections::HashMap<String, snapshots_manager_common::SnapshotSpace>,
    #[serde(default)]
    pub personal_sizes: std::collections::HashMap<String, snapshots_manager_common::SnapshotSpace>,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct PersonalSnapshot {
    pub id: String,
    pub kind: String,
    pub state: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub title: String,
    pub reason: String,
    pub pinned: bool,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct PersonalDirectoryEntry {
    pub name: String,
    pub kind: String,
    pub size: u64,
    pub modified_unix_seconds: i64,
}

/// Result of snapshot integrity verification
///
/// Contains validation status and any errors or warnings found during verification.
/// A snapshot is considered valid only if `is_valid` is true and `errors` is empty.
#[derive(Debug, serde::Deserialize)]
pub struct VerificationResult {
    /// Whether the snapshot passed all validation checks
    pub is_valid: bool,
    /// Critical errors that make the system snapshot invalid
    pub errors: Vec<String>,
    /// Non-critical issues that don't affect validity (e.g., missing metadata)
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct BtrfsFilesystemStatus {
    #[serde(default)]
    pub available: bool,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub total_bytes: Option<u64>,
    #[serde(default)]
    pub used_bytes: Option<u64>,
    #[serde(default)]
    pub data_profile: String,
    #[serde(default)]
    pub metadata_profile: String,
    #[serde(default)]
    pub compression: String,
    #[serde(default)]
    pub discard: String,
    #[serde(default)]
    pub quota: String,
    #[serde(default)]
    pub scrub: String,
    #[serde(default)]
    pub scrub_details: BtrfsScrubDetails,
    #[serde(default)]
    pub balance: String,
    #[serde(default)]
    pub balance_details: BtrfsBalanceDetails,
    #[serde(default)]
    pub defrag: String,
    #[serde(default)]
    pub defrag_details: BtrfsDefragDetails,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct BtrfsScrubDetails {
    #[serde(default)]
    pub started_at: Option<String>,
    #[serde(default)]
    pub duration: Option<String>,
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub total_bytes: Option<u64>,
    #[serde(default)]
    pub bytes_scrubbed: Option<u64>,
    #[serde(default)]
    pub rate_bytes_per_second: Option<u64>,
    #[serde(default)]
    pub read_errors: u64,
    #[serde(default)]
    pub checksum_errors: u64,
    #[serde(default)]
    pub verify_errors: u64,
    #[serde(default)]
    pub superblock_errors: u64,
    #[serde(default)]
    pub uncorrectable_errors: u64,
    #[serde(default)]
    pub unverified_errors: u64,
    #[serde(default)]
    pub corrected_errors: u64,
}

#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct BtrfsBalanceDetails {
    #[serde(default)]
    pub generation: u64,
    #[serde(default)]
    pub elapsed_seconds: Option<u64>,
    #[serde(default)]
    pub chunks_balanced: Option<u64>,
    #[serde(default)]
    pub chunks_total: Option<u64>,
    #[serde(default)]
    pub chunks_considered: Option<u64>,
    #[serde(default)]
    pub percent_remaining: Option<u64>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct BtrfsDefragDetails {
    #[serde(default)]
    pub generation: u64,
    #[serde(default)]
    pub elapsed_seconds: Option<u64>,
    #[serde(default)]
    pub items_processed: u64,
    #[serde(default)]
    pub error: Option<String>,
}

/// Blocking D-Bus client for btrfs-snapshots-manager-helper privileged service
///
/// Provides methods to create, delete, restore, and verify btrfs snapshots through
/// the btrfs-snapshots-manager-helper D-Bus service. All operations require Polkit authorization.
///
/// # Thread Safety
/// This client uses blocking I/O and should be used from background threads when
/// called from GUI code to avoid blocking the UI.
///
/// # Connection
/// Connects to the system D-Bus bus. The btrfs-snapshots-manager-helper service must be running
/// (typically activated automatically via D-Bus service activation).
pub struct SnapshotsManagerHelperClient {
    connection: BlockingConnection,
}

impl SnapshotsManagerHelperClient {
    /// Connect to the btrfs-snapshots-manager-helper D-Bus service
    ///
    /// Establishes a connection to the system D-Bus bus and prepares to communicate
    /// with the btrfs-snapshots-manager-helper service.
    ///
    /// # Errors
    /// - D-Bus system bus connection failure (check if dbus-daemon is running)
    ///
    /// # Example
    /// ```no_run
    /// use btrfs-snapshots-manager::dbus_client::SnapshotsManagerHelperClient;
    ///
    /// let client = SnapshotsManagerHelperClient::new()?;
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
        // Administrators receive the complete system recovery state. The
        // system-bus policy rejects this method for ordinary users, who then
        // fall back to the metadata-minimized Personal Files view.
        let json: String = match proxy.call("GetPrivilegedRecoveryEngineStatus", &()) {
            Ok(json) => json,
            Err(_) => proxy
                .call("GetRecoveryEngineStatus", &())
                .context("Failed to query the recovery engine")?,
        };
        serde_json::from_str(&json).context("Failed to parse recovery engine status")
    }

    pub fn measure_snapshot_space(
        &self,
        scope: &str,
        id: String,
    ) -> Result<snapshots_manager_common::SnapshotSpace> {
        let (success, result): (bool, String) = self
            .proxy()?
            .call("MeasureSnapshotSpace", &(scope.to_string(), id))
            .context("Failed to measure snapshot size")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse snapshot size")
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
            .context("Failed to create a system snapshot")
    }

    pub fn create_personal_snapshot(
        &self,
        title: String,
        reason: String,
        pinned: bool,
    ) -> Result<PersonalSnapshot> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("CreatePersonalSnapshot", &(title, reason, pinned))
            .context("Failed to create a Home snapshot")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse personal snapshot")
    }

    pub fn delete_personal_snapshots(&self, ids: Vec<String>) -> Result<()> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("DeletePersonalSnapshots", &(ids,))
            .context("Failed to delete the selected Home snapshots")?;
        if !success {
            anyhow::bail!(result);
        }
        Ok(())
    }

    pub fn set_personal_snapshot_pinned(
        &self,
        id: String,
        pinned: bool,
    ) -> Result<PersonalSnapshot> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("SetPersonalSnapshotPinned", &(id, pinned))
            .context("Failed to change Personal Files history protection")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse personal snapshot")
    }

    pub fn rename_personal_snapshot(&self, id: String, title: String) -> Result<()> {
        let (success, result): (bool, String) = self
            .proxy()?
            .call("RenamePersonalSnapshot", &(id, title))
            .context("Failed to rename Home snapshot")?;
        if !success {
            anyhow::bail!(result);
        }
        Ok(())
    }

    pub fn verify_personal_snapshot(&self, id: String) -> Result<VerificationResult> {
        let json: String = self
            .proxy()?
            .call("VerifyPersonalSnapshot", &(id,))
            .context("Failed to check Home snapshot availability")?;
        serde_json::from_str(&json).context("Failed to parse Home snapshot check")
    }

    pub fn list_personal_files(
        &self,
        snapshot_id: String,
        relative_path: String,
    ) -> Result<Vec<PersonalDirectoryEntry>> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("ListPersonalFiles", &(snapshot_id, relative_path))
            .context("Failed to browse historical Personal Files")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse historical directory")
    }

    pub fn export_personal_file(
        &self,
        snapshot_id: String,
        relative_path: String,
    ) -> Result<std::fs::File> {
        let proxy = self.proxy()?;
        let descriptor: zbus::zvariant::OwnedFd = proxy
            .call("ExportPersonalFile", &(snapshot_id, relative_path))
            .context("Failed to export historical Personal File")?;
        Ok(std::fs::File::from(std::os::fd::OwnedFd::from(descriptor)))
    }

    pub fn list_system_snapshot_files(
        &self,
        token: String,
        deployment_id: String,
        relative_path: String,
    ) -> Result<Vec<PersonalDirectoryEntry>> {
        let (success, result): (bool, String) = self
            .proxy()?
            .call(
                "ListSystemSnapshotFiles",
                &(token, deployment_id, relative_path),
            )
            .context("Failed to browse system snapshot")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse system snapshot directory")
    }

    pub fn export_system_snapshot_file(
        &self,
        token: String,
        deployment_id: String,
        relative_path: String,
    ) -> Result<std::fs::File> {
        let descriptor: zbus::zvariant::OwnedFd = self
            .proxy()?
            .call(
                "ExportSystemSnapshotFile",
                &(token, deployment_id, relative_path),
            )
            .context("Failed to export system snapshot file")?;
        Ok(std::fs::File::from(std::os::fd::OwnedFd::from(descriptor)))
    }

    pub fn begin_system_snapshot_browse(&self, deployment_id: String) -> Result<String> {
        let (success, result): (bool, String) = self
            .proxy()?
            .call("BeginSystemSnapshotBrowse", &(deployment_id,))
            .context("Failed to authorize system snapshot browser")?;
        if !success {
            anyhow::bail!(result);
        }
        Ok(result)
    }

    pub fn end_system_snapshot_browse(&self, token: String) -> Result<()> {
        let (success, result): (bool, String) = self
            .proxy()?
            .call("EndSystemSnapshotBrowse", &(token,))
            .context("Failed to release system snapshot browser")?;
        if !success {
            anyhow::bail!(result);
        }
        Ok(())
    }

    fn proxy(&self) -> Result<zbus::blocking::Proxy<'_>> {
        zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )
        .context("Failed to connect to the Disk Snapshots Manager helper")
    }

    pub fn delete_deployments(&self, ids: Vec<String>) -> Result<()> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("DeleteDeployments", &(ids,))
            .context("Failed to delete the selected system snapshots")?;
        if !success {
            anyhow::bail!(result);
        }
        Ok(())
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
            .context("Failed to change system snapshot protection")
    }

    pub fn rename_deployment(&self, id: String, title: String) -> Result<()> {
        let (success, result): (bool, String) = self
            .proxy()?
            .call("RenameDeployment", &(id, title))
            .context("Failed to rename system snapshot")?;
        if !success {
            anyhow::bail!(result);
        }
        Ok(())
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
            .context("Failed to schedule the system snapshot")
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

    pub fn reconcile_deployment_restore(&self) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("ReconcileDeploymentRestore", &())
            .context("Failed to retry recovery confirmation")
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

    pub fn get_apt_snapshot_policy(&self) -> Result<(bool, bool)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("GetAptSnapshotPolicy", &())
            .context("Failed to load APT snapshot policy")
    }

    pub fn save_apt_snapshot_policy(
        &self,
        snapshot_before: bool,
        snapshot_after: bool,
    ) -> Result<(bool, String)> {
        let proxy = zbus::blocking::Proxy::new(
            &self.connection,
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
            DBUS_INTERFACE_NAME,
        )?;
        proxy
            .call("SaveAptSnapshotPolicy", &(snapshot_before, snapshot_after))
            .context("Failed to save APT snapshot policy")
    }

    pub fn get_automation_config(&self) -> Result<snapshots_manager_common::AutomationConfig> {
        let json: String = self
            .proxy()?
            .call("GetAutomationConfig", &())
            .context("Failed to load automatic snapshot configuration")?;
        serde_json::from_str(&json).context("Failed to parse automatic snapshot configuration")
    }

    pub fn save_automation_config(
        &self,
        config: &snapshots_manager_common::AutomationConfig,
    ) -> Result<(bool, String)> {
        let json = serde_json::to_string(config)?;
        self.proxy()?
            .call("SaveAutomationConfig", &(json,))
            .context("Failed to save automatic snapshot configuration")
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
    /// btrfs-snapshots-manager-snapshots service.
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

    pub fn get_btrfs_filesystem_status(&self) -> Result<BtrfsFilesystemStatus> {
        let json: String = self
            .proxy()?
            .call("GetBtrfsFilesystemStatus", &())
            .context("Failed to query Btrfs filesystem status")?;
        serde_json::from_str(&json).context("Failed to parse Btrfs filesystem status")
    }

    pub fn run_btrfs_maintenance_action(&self, action: &str) -> Result<String> {
        let (success, message): (bool, String) = self
            .proxy()?
            .call("RunBtrfsMaintenanceAction", &(action.to_string(),))
            .context("Failed to start Btrfs maintenance")?;
        if success {
            Ok(message)
        } else {
            anyhow::bail!(message)
        }
    }
}
