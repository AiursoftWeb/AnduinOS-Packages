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
    pub kernel_release: Option<String>,
    pub pinned: bool,
}

#[derive(Debug, serde::Deserialize)]
pub struct PendingRecovery {
    pub target_deployment_id: String,
    pub phase: String,
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
    pub personal_sizes: std::collections::HashMap<String, waypoint_common::SnapshotSpace>,
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
    /// Critical errors that make the recovery point invalid
    pub errors: Vec<String>,
    /// Non-critical issues that don't affect validity (e.g., missing metadata)
    pub warnings: Vec<String>,
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

    pub fn create_personal_snapshot(
        &self,
        title: String,
        reason: String,
        pinned: bool,
    ) -> Result<PersonalSnapshot> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("CreatePersonalSnapshot", &(title, reason, pinned))
            .context("Failed to create a Personal Files history point")?;
        if !success {
            anyhow::bail!(result);
        }
        serde_json::from_str(&result).context("Failed to parse personal snapshot")
    }

    pub fn delete_personal_snapshots(&self, ids: Vec<String>) -> Result<()> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("DeletePersonalSnapshots", &(ids,))
            .context("Failed to delete the selected Personal Files history points")?;
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
        .context("Failed to connect to the Waypoint helper")
    }

    pub fn delete_deployments(&self, ids: Vec<String>) -> Result<()> {
        let proxy = self.proxy()?;
        let (success, result): (bool, String) = proxy
            .call("DeleteDeployments", &(ids,))
            .context("Failed to delete the selected recovery points")?;
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
            .context("Failed to change recovery-point protection")
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

    pub fn get_automation_config(&self) -> Result<waypoint_common::AutomationConfig> {
        let json: String = self
            .proxy()?
            .call("GetAutomationConfig", &())
            .context("Failed to load automatic snapshot configuration")?;
        serde_json::from_str(&json).context("Failed to parse automatic snapshot configuration")
    }

    pub fn save_automation_config(
        &self,
        config: &waypoint_common::AutomationConfig,
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
}
