// Waypoint Helper - Privileged D-Bus service for snapshot operations
// This binary runs with elevated privileges via D-Bus activation

use anduinos_recovery_engine::{
    RECOVERY_STORE_ROOT, layout,
    model::{DeploymentId, DeploymentKind, DeploymentState},
    operations::OperationEngine,
    personal::{PersonalSnapshotEngine, PersonalSnapshotId, PersonalSnapshotState},
    rollback::RollbackCoordinator,
    store::DeploymentStore,
    system_browser::SystemSnapshotBrowser,
    transaction::TransactionStore,
};
use anyhow::{Context, Result};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use tokio::signal::unix::{SignalKind, signal};
use waypoint_common::*;
use zbus::{Connection, ConnectionBuilder, interface};

mod audit;
mod btrfs;
mod packages;

/// Global counter for mutex poisoning events (for monitoring)
static MUTEX_POISON_COUNT: AtomicUsize = AtomicUsize::new(0);

/// Simple rate limiter to prevent DoS via expensive operations
/// Implements a per-user, per-operation cooldown period
#[derive(Debug, Clone)]
struct RateLimiter {
    last_operation:
        std::sync::Arc<std::sync::Mutex<std::collections::HashMap<String, std::time::Instant>>>,
    window: std::time::Duration,
}

impl RateLimiter {
    fn new(window_seconds: u64) -> Self {
        Self {
            last_operation: std::sync::Arc::new(std::sync::Mutex::new(
                std::collections::HashMap::new(),
            )),
            window: std::time::Duration::from_secs(window_seconds),
        }
    }

    /// Check if operation is allowed for this user
    /// Returns Ok(()) if allowed, Err with time to wait if rate limited
    fn check_rate_limit(&self, user_id: &str, operation: &str) -> Result<(), std::time::Duration> {
        let mut state = self.last_operation.lock().unwrap_or_else(|poisoned| {
            let count = MUTEX_POISON_COUNT.fetch_add(1, Ordering::Relaxed);
            log::error!("Rate limiter mutex poisoned (count: {}), recovering", count + 1);

            // Alert if poisoning happens frequently (potential bug or attack)
            if count > 10 {
                log::error!(
                    "CRITICAL: Rate limiter mutex poisoned {} times - potential issue requiring investigation",
                    count + 1
                );
            }

            poisoned.into_inner()
        });
        let key = format!("{user_id}:{operation}");
        let now = std::time::Instant::now();

        if let Some(last_time) = state.get(&key) {
            let elapsed = now.duration_since(*last_time);
            if elapsed < self.window {
                // Still within rate limit window
                let wait_time = self.window - elapsed;
                return Err(wait_time);
            }
        }

        // Update last operation time
        state.insert(key, now);
        Ok(())
    }
}

/// Main D-Bus service interface for Waypoint operations
struct WaypointHelper {
    rate_limiter: RateLimiter,
    browse_leases: std::sync::Mutex<std::collections::HashMap<String, SystemBrowseLease>>,
}

struct SystemBrowseLease {
    pid: u32,
    deployment_id: DeploymentId,
    expires_at: std::time::Instant,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ScheduleRetentionSummary {
    system_deleted: u64,
    personal_deleted: u64,
    system_retained: u64,
    personal_retained: u64,
}

impl ScheduleRetentionSummary {
    fn message(self) -> String {
        format!(
            "Cleaned up {} automatic system recovery point(s) and {} Personal Files history point(s); retained {} system and {} personal point(s) for safety",
            self.system_deleted,
            self.personal_deleted,
            self.system_retained,
            self.personal_retained,
        )
    }
}

impl WaypointHelper {
    fn new() -> Self {
        Self {
            // Rate limit: 1 operation per 5 seconds per user
            rate_limiter: RateLimiter::new(5),
            browse_leases: std::sync::Mutex::new(std::collections::HashMap::new()),
        }
    }

    async fn validate_browse_lease(
        &self,
        hdr: &zbus::message::Header<'_>,
        connection: &Connection,
        token: &str,
        deployment_id: DeploymentId,
    ) -> Result<()> {
        let pid = Self::get_caller_pid(hdr, connection).await?;
        let mut leases = self
            .browse_leases
            .lock()
            .map_err(|_| anyhow::anyhow!("Browse lease store is unavailable"))?;
        leases.retain(|_, lease| lease.expires_at > std::time::Instant::now());
        let lease = leases
            .get(token)
            .context("System snapshot browser authorization expired")?;
        anyhow::ensure!(
            lease.pid == pid && lease.deployment_id == deployment_id,
            "System snapshot browser authorization does not match this caller"
        );
        Ok(())
    }

    /// Get caller's user ID from D-Bus header
    async fn get_caller_uid(
        hdr: &zbus::message::Header<'_>,
        connection: &Connection,
    ) -> Result<String> {
        let caller = hdr.sender().context("No sender in message header")?;

        let response = connection
            .call_method(
                Some("org.freedesktop.DBus"),
                "/org/freedesktop/DBus",
                Some("org.freedesktop.DBus"),
                "GetConnectionUnixUser",
                &caller.as_str(),
            )
            .await
            .context("Failed to get caller UID from D-Bus")?;

        let uid: u32 = response
            .body()
            .deserialize()
            .context("Failed to deserialize caller UID")?;

        Ok(uid.to_string())
    }

    /// Get caller's process ID from D-Bus header
    async fn get_caller_pid(
        hdr: &zbus::message::Header<'_>,
        connection: &Connection,
    ) -> Result<u32> {
        let caller = hdr.sender().context("No sender in message header")?;

        let response = connection
            .call_method(
                Some("org.freedesktop.DBus"),
                "/org/freedesktop/DBus",
                Some("org.freedesktop.DBus"),
                "GetConnectionUnixProcessID",
                &caller.as_str(),
            )
            .await
            .context("Failed to get caller PID from D-Bus")?;

        response
            .body()
            .deserialize()
            .context("Failed to deserialize caller PID")
    }

    /// Get both UID and PID for audit logging
    async fn get_caller_info(
        hdr: &zbus::message::Header<'_>,
        connection: &Connection,
    ) -> (String, u32) {
        let uid = Self::get_caller_uid(hdr, connection)
            .await
            .unwrap_or_else(|_| "unknown".to_string());
        let pid = Self::get_caller_pid(hdr, connection).await.unwrap_or(0);
        (uid, pid)
    }

    /// Resolve the D-Bus caller to exactly one direct child of `/home`. This
    /// prevents one desktop user from browsing another user's history even
    /// after Polkit has authorized use of the recovery feature.
    async fn caller_home_directory(
        hdr: &zbus::message::Header<'_>,
        connection: &Connection,
    ) -> Result<String> {
        let uid = Self::get_caller_uid(hdr, connection)
            .await?
            .parse::<u32>()
            .context("D-Bus returned an invalid caller UID")?;
        let user = nix::unistd::User::from_uid(nix::unistd::Uid::from_raw(uid))?
            .context("D-Bus caller has no local account")?;
        let parent = user
            .dir
            .parent()
            .context("Caller home has no parent directory")?;
        if parent != std::path::Path::new("/home") {
            anyhow::bail!("Personal history is available only for accounts directly under /home");
        }
        let directory = user
            .dir
            .file_name()
            .and_then(std::ffi::OsStr::to_str)
            .context("Caller home directory name is not valid UTF-8")?;
        if directory != user.name {
            anyhow::bail!("Caller account and home directory identity do not match");
        }
        Ok(directory.to_string())
    }
}

#[interface(name = "org.anduinos.Waypoint.Helper")]
impl WaypointHelper {
    /// Signal emitted when a snapshot is created
    #[zbus(signal)]
    async fn snapshot_created(
        ctxt: &zbus::SignalContext<'_>,
        snapshot_name: &str,
        created_by: &str,
    ) -> zbus::Result<()>;

    /// Signal emitted when an independent Personal Files history point is created.
    #[zbus(signal)]
    async fn personal_snapshot_created(
        ctxt: &zbus::SignalContext<'_>,
        snapshot_id: &str,
        created_by: &str,
    ) -> zbus::Result<()>;

    /// Privacy-preserving desktop event emitted only when the matching
    /// automatic schedule has creation notifications enabled.
    #[zbus(signal)]
    async fn snapshot_creation_succeeded(
        ctxt: &zbus::SignalContext<'_>,
        scope: &str,
        automatic: bool,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    async fn automatic_snapshot_starting(
        ctxt: &zbus::SignalContext<'_>,
        scope: &str,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    async fn automatic_snapshot_failed(
        ctxt: &zbus::SignalContext<'_>,
        scope: &str,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    async fn automatic_cleanup_succeeded(
        ctxt: &zbus::SignalContext<'_>,
        system_deleted: u64,
        personal_deleted: u64,
    ) -> zbus::Result<()>;

    /// Report the trusted deployment engine state without authorizing mutation.
    async fn get_recovery_engine_status(&self) -> String {
        Self::recovery_engine_status_impl(std::path::Path::new(RECOVERY_STORE_ROOT)).unwrap_or_else(
            |error| {
                serde_json::json!({
                    "schema_version": 1,
                    "available": false,
                    "error": error.to_string(),
                })
                .to_string()
            },
        )
    }

    /// Private root-owned scheduler notification bridge.
    async fn notify_automatic_snapshot_event(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        #[zbus(signal_context)] ctxt: zbus::SignalContext<'_>,
        event: String,
        scope: String,
    ) -> (bool, String) {
        let uid = match Self::get_caller_uid(&hdr, connection).await {
            Ok(uid) => uid,
            Err(error) => return (false, error.to_string()),
        };
        if uid != "0" {
            return (
                false,
                "Only the root-owned scheduler may emit automation events".into(),
            );
        }
        if !matches!(scope.as_str(), "system" | "personal") {
            return (false, "Invalid automation scope".into());
        }
        let result = match event.as_str() {
            "starting" => {
                if !automatic_pre_notification_enabled() {
                    return (true, "disabled".into());
                }
                Self::automatic_snapshot_starting(&ctxt, &scope).await
            }
            "failed" => Self::automatic_snapshot_failed(&ctxt, &scope).await,
            _ => return (false, "Invalid automation event".into()),
        };
        match result {
            Ok(()) => (true, "emitted".into()),
            Err(error) => (false, error.to_string()),
        }
    }

    /// Create an immutable AnduinOS system recovery point.
    async fn create_deployment(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        #[zbus(signal_context)] ctxt: zbus::SignalContext<'_>,
        title: String,
        reason: String,
        pinned: bool,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CREATE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CREATE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if let Err(wait) = self
            .rate_limiter
            .check_rate_limit(&uid, "create_deployment")
        {
            audit::log_snapshot_create(uid, pid, &title, false, Some("rate limit exceeded"));
            return (
                false,
                format!(
                    "Please wait {} seconds before creating another recovery point",
                    wait.as_secs()
                ),
            );
        }
        match OperationEngine::default().create_manual(
            &layout::inspect_current(),
            &title,
            &reason,
            pinned,
            |_phase, _fraction, _message| {},
        ) {
            Ok(record) => match serde_json::to_string(&record) {
                Ok(json) => {
                    audit::log_snapshot_create(uid, pid, &record.id.to_string(), true, None);
                    if let Err(error) =
                        Self::snapshot_created(&ctxt, &record.id.to_string(), "manual").await
                    {
                        log::warn!("Could not emit recovery-point creation signal: {error}");
                    }
                    if automatic_success_notification_enabled()
                        && let Err(error) =
                            Self::snapshot_creation_succeeded(&ctxt, "system", false).await
                    {
                        log::warn!("Could not emit manual System notification: {error}");
                    }
                    (true, json)
                }
                Err(error) => (
                    false,
                    format!("Could not serialize recovery point: {error}"),
                ),
            },
            Err(error) => {
                audit::log_snapshot_create(uid, pid, &title, false, Some(&error.to_string()));
                (false, error.to_string())
            }
        }
    }

    /// Create an automatic recovery point while preserving its schedule label.
    async fn create_scheduled_deployment(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        #[zbus(signal_context)] ctxt: zbus::SignalContext<'_>,
        schedule_id: String,
        title: String,
        reason: String,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CREATE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CREATE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if let Err(wait) = self
            .rate_limiter
            .check_rate_limit(&uid, "create_scheduled_deployment")
        {
            audit::log_snapshot_create(uid, pid, &title, false, Some("rate limit exceeded"));
            return (
                false,
                format!(
                    "Please wait {} seconds before creating another recovery point",
                    wait.as_secs()
                ),
            );
        }
        match OperationEngine::default().create_scheduled(
            &layout::inspect_current(),
            &schedule_id,
            &title,
            &reason,
            |_phase, _fraction, _message| {},
        ) {
            Ok(record) => match serde_json::to_string(&record) {
                Ok(json) => {
                    audit::log_snapshot_create(uid, pid, &record.id.to_string(), true, None);
                    if let Err(error) =
                        Self::snapshot_created(&ctxt, &record.id.to_string(), "scheduler").await
                    {
                        log::warn!("Could not emit scheduled recovery-point signal: {error}");
                    }
                    if automatic_success_notification_enabled()
                        && let Err(error) =
                            Self::snapshot_creation_succeeded(&ctxt, "system", true).await
                    {
                        log::warn!("Could not emit automatic System notification: {error}");
                    }
                    (true, json)
                }
                Err(error) => (
                    false,
                    format!("Could not serialize recovery point: {error}"),
                ),
            },
            Err(error) => {
                audit::log_snapshot_create(uid, pid, &title, false, Some(&error.to_string()));
                (false, error.to_string())
            }
        }
    }

    /// Create an immutable snapshot of the independent `@home` subvolume.
    async fn create_personal_snapshot(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        #[zbus(signal_context)] ctxt: zbus::SignalContext<'_>,
        title: String,
        reason: String,
        pinned: bool,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CREATE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CREATE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if let Err(wait) = self
            .rate_limiter
            .check_rate_limit(&uid, "create_personal_snapshot")
        {
            return (
                false,
                format!(
                    "Please wait {} seconds before creating another Personal Files history point",
                    wait.as_secs()
                ),
            );
        }
        match PersonalSnapshotEngine::default().create_manual(
            &layout::inspect_current(),
            &title,
            &reason,
            pinned,
        ) {
            Ok(record) => match serde_json::to_string(&record) {
                Ok(json) => {
                    audit::log_operation(
                        uid,
                        pid,
                        "create_personal_snapshot",
                        &record.id.to_string(),
                        true,
                        None,
                    );
                    if let Err(error) =
                        Self::personal_snapshot_created(&ctxt, &record.id.to_string(), "manual")
                            .await
                    {
                        log::warn!("Could not emit Personal Files history signal: {error}");
                    }
                    if automatic_success_notification_enabled()
                        && let Err(error) =
                            Self::snapshot_creation_succeeded(&ctxt, "personal", false).await
                    {
                        log::warn!("Could not emit manual Personal Files notification: {error}");
                    }
                    (true, json)
                }
                Err(error) => (
                    false,
                    format!("Could not serialize personal snapshot: {error}"),
                ),
            },
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "create_personal_snapshot",
                    &title,
                    false,
                    Some(&error.to_string()),
                );
                (false, error.to_string())
            }
        }
    }

    /// Trusted scheduler entry point for Personal Files history.
    async fn create_scheduled_personal_snapshot(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        #[zbus(signal_context)] ctxt: zbus::SignalContext<'_>,
        schedule_id: String,
        title: String,
        reason: String,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CREATE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CREATE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if let Err(wait) = self
            .rate_limiter
            .check_rate_limit(&uid, "create_scheduled_personal_snapshot")
        {
            return (
                false,
                format!(
                    "Please wait {} seconds before creating another Personal Files history point",
                    wait.as_secs()
                ),
            );
        }
        match PersonalSnapshotEngine::default().create_scheduled(
            &layout::inspect_current(),
            &schedule_id,
            &title,
            &reason,
        ) {
            Ok(record) => match serde_json::to_string(&record) {
                Ok(json) => {
                    audit::log_operation(
                        uid,
                        pid,
                        "create_scheduled_personal_snapshot",
                        &record.id.to_string(),
                        true,
                        None,
                    );
                    if let Err(error) =
                        Self::personal_snapshot_created(&ctxt, &record.id.to_string(), "scheduler")
                            .await
                    {
                        log::warn!(
                            "Could not emit scheduled Personal Files history signal: {error}"
                        );
                    }
                    if automatic_success_notification_enabled()
                        && let Err(error) =
                            Self::snapshot_creation_succeeded(&ctxt, "personal", true).await
                    {
                        log::warn!("Could not emit automatic Personal Files notification: {error}");
                    }
                    (true, json)
                }
                Err(error) => (
                    false,
                    format!("Could not serialize personal snapshot: {error}"),
                ),
            },
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "create_scheduled_personal_snapshot",
                    &title,
                    false,
                    Some(&error.to_string()),
                );
                (false, error.to_string())
            }
        }
    }

    /// Delete one unpinned Personal Files history point.
    async fn delete_personal_snapshot(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_id: String,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_DELETE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        let id = match snapshot_id.parse::<PersonalSnapshotId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid personal snapshot ID: {error}")),
        };
        match PersonalSnapshotEngine::default().delete(&layout::inspect_current(), id) {
            Ok(()) => {
                audit::log_operation(
                    uid,
                    pid,
                    "delete_personal_snapshot",
                    &snapshot_id,
                    true,
                    None,
                );
                (true, "Personal Files history point deleted".into())
            }
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "delete_personal_snapshot",
                    &snapshot_id,
                    false,
                    Some(&error.to_string()),
                );
                (false, error.to_string())
            }
        }
    }

    /// Delete multiple unpinned Personal Files history points under one
    /// explicit authorization decision.
    async fn delete_personal_snapshots(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_ids: Vec<String>,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_DELETE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if snapshot_ids.is_empty() {
            return (
                false,
                "No Personal Files history points were selected".into(),
            );
        }
        let parsed = snapshot_ids
            .iter()
            .map(|value| {
                value
                    .parse::<PersonalSnapshotId>()
                    .map(|id| (value, id))
                    .map_err(|error| format!("{value}: {error}"))
            })
            .collect::<Result<Vec<_>, _>>();
        let parsed = match parsed {
            Ok(parsed) => parsed,
            Err(error) => return (false, format!("Invalid personal snapshot ID: {error}")),
        };
        let engine = PersonalSnapshotEngine::default();
        let layout = layout::inspect_current();
        let mut failures = Vec::new();
        for (value, id) in parsed {
            match engine.delete(&layout, id) {
                Ok(()) => audit::log_operation(
                    uid.clone(),
                    pid,
                    "delete_personal_snapshot",
                    value,
                    true,
                    None,
                ),
                Err(error) => {
                    audit::log_operation(
                        uid.clone(),
                        pid,
                        "delete_personal_snapshot",
                        value,
                        false,
                        Some(&error.to_string()),
                    );
                    failures.push(format!("{value}: {error}"));
                }
            }
        }
        if failures.is_empty() {
            (true, "Personal Files history points deleted".into())
        } else {
            (false, failures.join("\n"))
        }
    }

    /// Protect or unprotect one Personal Files history point.
    async fn set_personal_snapshot_pinned(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_id: String,
        pinned: bool,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_DELETE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        let id = match snapshot_id.parse::<PersonalSnapshotId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid personal snapshot ID: {error}")),
        };
        match PersonalSnapshotEngine::default().set_pinned(&layout::inspect_current(), id, pinned) {
            Ok(record) => serde_json::to_string(&record)
                .map(|json| (true, json))
                .unwrap_or_else(|error| (false, error.to_string())),
            Err(error) => (false, error.to_string()),
        }
    }

    async fn rename_personal_snapshot(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_id: String,
        title: String,
    ) -> (bool, String) {
        if let Err(error) =
            check_authorization(&hdr, connection, POLKIT_ACTION_PERSONAL_FILES).await
        {
            return (false, format!("Authorization failed: {error}"));
        }
        let id = match snapshot_id.parse::<PersonalSnapshotId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid personal snapshot ID: {error}")),
        };
        match PersonalSnapshotEngine::default().rename(&layout::inspect_current(), id, &title) {
            Ok(record) => serde_json::to_string(&record)
                .map(|json| (true, json))
                .unwrap_or_else(|error| (false, error.to_string())),
            Err(error) => (false, error.to_string()),
        }
    }

    async fn verify_personal_snapshot(&self, snapshot_id: String) -> String {
        let id = match snapshot_id.parse::<PersonalSnapshotId>() {
            Ok(id) => id,
            Err(error) => {
                return serde_json::json!({
                    "is_valid": false,
                    "errors": [format!("Invalid personal snapshot ID: {error}")],
                    "warnings": [],
                })
                .to_string();
            }
        };
        match PersonalSnapshotEngine::default().verify(&layout::inspect_current(), id) {
            Ok(_) => serde_json::json!({
                "is_valid": true,
                "errors": [],
                "warnings": [],
            })
            .to_string(),
            Err(error) => serde_json::json!({
                "is_valid": false,
                "errors": [error.to_string()],
                "warnings": [],
            })
            .to_string(),
        }
    }

    /// List one bounded directory from the caller's own historical home.
    async fn list_personal_files(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_id: String,
        relative_path: String,
    ) -> (bool, String) {
        let user_directory = match Self::caller_home_directory(&hdr, connection).await {
            Ok(value) => value,
            Err(error) => return (false, error.to_string()),
        };
        let id = match snapshot_id.parse::<PersonalSnapshotId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid personal snapshot ID: {error}")),
        };
        let engine = PersonalSnapshotEngine::default();
        let result = engine
            .browser(&layout::inspect_current(), id, &user_directory)
            .and_then(|browser| browser.list(&relative_path));
        match result {
            Ok(entries) => serde_json::to_string(&entries)
                .map(|json| (true, json))
                .unwrap_or_else(|error| (false, error.to_string())),
            Err(error) => (false, error.to_string()),
        }
    }

    /// Return one regular historical file as a read-only Unix descriptor. The
    /// helper never receives or writes a destination path.
    async fn export_personal_file(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_id: String,
        relative_path: String,
    ) -> zbus::fdo::Result<zbus::zvariant::OwnedFd> {
        let user_directory = Self::caller_home_directory(&hdr, connection)
            .await
            .map_err(|error| zbus::fdo::Error::Failed(error.to_string()))?;
        let id = snapshot_id
            .parse::<PersonalSnapshotId>()
            .map_err(|error| zbus::fdo::Error::InvalidArgs(error.to_string()))?;
        let engine = PersonalSnapshotEngine::default();
        let file = engine
            .browser(&layout::inspect_current(), id, &user_directory)
            .and_then(|browser| browser.open_file(&relative_path))
            .map_err(|error| zbus::fdo::Error::Failed(error.to_string()))?;
        Ok(std::os::fd::OwnedFd::from(file).into())
    }

    /// List one directory in a system snapshot. Every call requires an
    /// active window/process-bound administrator lease.
    async fn begin_system_snapshot_browse(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        deployment_id: String,
    ) -> (bool, String) {
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_RESTORE).await {
            return (false, format!("Authorization failed: {error}"));
        }
        let id = match deployment_id.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid recovery point ID: {error}")),
        };
        if let Err(error) =
            OperationEngine::default().check_available(&layout::inspect_current(), id)
        {
            return (false, error.to_string());
        }
        let pid = match Self::get_caller_pid(&hdr, connection).await {
            Ok(pid) => pid,
            Err(error) => return (false, error.to_string()),
        };
        let token = uuid::Uuid::new_v4().to_string();
        let lease = SystemBrowseLease {
            pid,
            deployment_id: id,
            expires_at: std::time::Instant::now() + std::time::Duration::from_secs(4 * 60 * 60),
        };
        match self.browse_leases.lock() {
            Ok(mut leases) => {
                leases.insert(token.clone(), lease);
                (true, token)
            }
            Err(_) => (false, "Browse lease store is unavailable".into()),
        }
    }

    async fn end_system_snapshot_browse(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        token: String,
    ) -> (bool, String) {
        let pid = match Self::get_caller_pid(&hdr, connection).await {
            Ok(pid) => pid,
            Err(error) => return (false, error.to_string()),
        };
        match self.browse_leases.lock() {
            Ok(mut leases) => match leases.get(&token) {
                Some(lease) if lease.pid == pid => {
                    leases.remove(&token);
                    (true, "released".into())
                }
                _ => (false, "Browse lease does not belong to this caller".into()),
            },
            Err(_) => (false, "Browse lease store is unavailable".into()),
        }
    }

    async fn list_system_snapshot_files(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        token: String,
        deployment_id: String,
        relative_path: String,
    ) -> (bool, String) {
        let id = match deployment_id.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid recovery point ID: {error}")),
        };
        if let Err(error) = self
            .validate_browse_lease(&hdr, connection, &token, id)
            .await
        {
            return (false, error.to_string());
        }
        let result = OperationEngine::default()
            .check_available(&layout::inspect_current(), id)
            .map_err(anyhow::Error::from)
            .and_then(|_| {
                SystemSnapshotBrowser::open(std::path::Path::new(RECOVERY_STORE_ROOT), id)
                    .map_err(|error| anyhow::anyhow!(error.to_string()))
            })
            .and_then(|browser| {
                browser
                    .list(&relative_path)
                    .map_err(|error| anyhow::anyhow!(error.to_string()))
            });
        match result {
            Ok(entries) => serde_json::to_string(&entries)
                .map(|json| (true, json))
                .unwrap_or_else(|error| (false, error.to_string())),
            Err(error) => (false, sanitize_error_for_client(&error)),
        }
    }

    /// Return one regular system-snapshot file as a read-only descriptor.
    /// The unprivileged GUI chooses and writes the destination.
    async fn export_system_snapshot_file(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        token: String,
        deployment_id: String,
        relative_path: String,
    ) -> zbus::fdo::Result<zbus::zvariant::OwnedFd> {
        let id = deployment_id
            .parse::<DeploymentId>()
            .map_err(|error| zbus::fdo::Error::InvalidArgs(error.to_string()))?;
        self.validate_browse_lease(&hdr, connection, &token, id)
            .await
            .map_err(|error| zbus::fdo::Error::AccessDenied(error.to_string()))?;
        OperationEngine::default()
            .check_available(&layout::inspect_current(), id)
            .map_err(|error| zbus::fdo::Error::Failed(error.to_string()))?;
        let file = SystemSnapshotBrowser::open(std::path::Path::new(RECOVERY_STORE_ROOT), id)
            .and_then(|browser| browser.open_file(&relative_path))
            .map_err(|error| zbus::fdo::Error::Failed(error.to_string()))?;
        Ok(std::os::fd::OwnedFd::from(file).into())
    }

    /// Delete an unprotected immutable system recovery point.
    async fn delete_deployment(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        deployment_id: String,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_DELETE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        let id = match deployment_id.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid recovery point ID: {error}")),
        };
        match OperationEngine::default().delete(&layout::inspect_current(), id) {
            Ok(()) => {
                audit::log_snapshot_delete(uid, pid, &deployment_id, true, None);
                (true, "Recovery point deleted".into())
            }
            Err(error) => {
                audit::log_snapshot_delete(
                    uid,
                    pid,
                    &deployment_id,
                    false,
                    Some(&error.to_string()),
                );
                (false, error.to_string())
            }
        }
    }

    /// Delete multiple unprotected system recovery points under one explicit
    /// authorization decision.
    async fn delete_deployments(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        deployment_ids: Vec<String>,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_DELETE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if deployment_ids.is_empty() {
            return (false, "No system recovery points were selected".into());
        }
        let parsed = deployment_ids
            .iter()
            .map(|value| {
                value
                    .parse::<DeploymentId>()
                    .map(|id| (value, id))
                    .map_err(|error| format!("{value}: {error}"))
            })
            .collect::<Result<Vec<_>, _>>();
        let parsed = match parsed {
            Ok(parsed) => parsed,
            Err(error) => return (false, format!("Invalid recovery point ID: {error}")),
        };
        let engine = OperationEngine::default();
        let layout = layout::inspect_current();
        let mut failures = Vec::new();
        for (value, id) in parsed {
            match engine.delete(&layout, id) {
                Ok(()) => audit::log_snapshot_delete(uid.clone(), pid, value, true, None),
                Err(error) => {
                    audit::log_snapshot_delete(
                        uid.clone(),
                        pid,
                        value,
                        false,
                        Some(&error.to_string()),
                    );
                    failures.push(format!("{value}: {error}"));
                }
            }
        }
        if failures.is_empty() {
            (true, "System recovery points deleted".into())
        } else {
            (false, failures.join("\n"))
        }
    }

    /// Protect or unprotect a deployment from retention and manual deletion.
    async fn set_deployment_pinned(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        id: String,
        pinned: bool,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CONFIGURE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CONFIGURE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        let deployment_id = id.clone();
        let id = match id.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "set_recovery_protection",
                    &deployment_id,
                    false,
                    Some(&error.to_string()),
                );
                return (false, format!("Invalid recovery point ID: {error}"));
            }
        };
        match OperationEngine::default().set_pinned(&layout::inspect_current(), id, pinned) {
            Ok(record) => match serde_json::to_string(&record) {
                Ok(json) => {
                    audit::log_operation(
                        uid,
                        pid,
                        "set_recovery_protection",
                        &deployment_id,
                        true,
                        None,
                    );
                    (true, json)
                }
                Err(error) => (
                    false,
                    format!("Could not serialize recovery state: {error}"),
                ),
            },
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "set_recovery_protection",
                    &deployment_id,
                    false,
                    Some(&error.to_string()),
                );
                (false, error.to_string())
            }
        }
    }

    async fn rename_deployment(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        id: String,
        title: String,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CONFIGURE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CONFIGURE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        let id = match id.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid recovery point ID: {error}")),
        };
        match OperationEngine::default().rename(&layout::inspect_current(), id, &title) {
            Ok(record) => serde_json::to_string(&record)
                .map(|json| (true, json))
                .unwrap_or_else(|error| (false, error.to_string())),
            Err(error) => (false, error.to_string()),
        }
    }

    /// Verify, protect, and schedule a one-shot recovery boot.
    async fn schedule_deployment_restore(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        deployment_id: String,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_RESTORE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_RESTORE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        let id = match deployment_id.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => return (false, format!("Invalid recovery point ID: {error}")),
        };
        match RollbackCoordinator::default().schedule(id, |_phase, _fraction, _message| {}) {
            Ok(transaction) => match serde_json::to_string(&transaction) {
                Ok(json) => {
                    audit::log_snapshot_restore(uid, pid, &deployment_id, true, None);
                    (true, json)
                }
                Err(error) => (false, format!("Could not serialize restore state: {error}")),
            },
            Err(error) => {
                audit::log_snapshot_restore(
                    uid,
                    pid,
                    &deployment_id,
                    false,
                    Some(&error.to_string()),
                );
                (false, error.to_string())
            }
        }
    }

    /// Cancel a restore only while it is still safe to do so before reboot.
    async fn cancel_deployment_restore(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_RESTORE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_RESTORE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        match RollbackCoordinator::default().cancel() {
            Ok(()) => {
                audit::log_operation(uid, pid, "cancel_restore", "pending-restore", true, None);
                (true, "Pending restore cancelled".into())
            }
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "cancel_restore",
                    "pending-restore",
                    false,
                    Some(&error.to_string()),
                );
                (false, error.to_string())
            }
        }
    }

    /// Return structured APT transactions from the current machine log.
    async fn get_apt_history(&self) -> String {
        Self::apt_history_directory_impl(std::path::Path::new("/var/log/apt")).unwrap_or_else(
            |error| {
                serde_json::json!({
                    "transactions": [],
                    "issues": [{"block": 0, "message": error.to_string()}],
                })
                .to_string()
            },
        )
    }

    /// Return referenced and exclusive qgroup bytes for trusted deployments.
    /// List mounted external filesystems accepted by the trusted backup engine.
    /// List backup manifests by destination filesystem UUID without hashing
    /// every potentially large stream.
    /// List independent Personal Files backup manifests on an external drive.
    /// Export one immutable Personal Files history point as a full Btrfs stream.
    /// Export one trusted immutable deployment to a mounted filesystem UUID.
    /// Hash and validate one backup selected only by filesystem and backup UUID.
    /// Receive a verified backup into a new local immutable deployment.
    /// Delete only the two fixed files belonging to a validated backup UUID.
    /// Verify snapshot integrity
    async fn verify_snapshot(&self, name: String) -> String {
        // Verification is read-only, no authorization needed
        let id = match name.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => {
                return serde_json::json!({
                    "is_valid": false,
                    "errors": [format!("Invalid recovery point ID: {error}")],
                    "warnings": [],
                })
                .to_string();
            }
        };
        match OperationEngine::default().check_available(&layout::inspect_current(), id) {
            Ok(_) => serde_json::to_string(&btrfs::VerificationResult {
                is_valid: true,
                errors: Vec::new(),
                warnings: Vec::new(),
            })
            .unwrap_or_else(|_| {
                r#"{"is_valid":false,"errors":["Failed to serialize result"],"warnings":[]}"#
                    .to_string()
            }),
            Err(e) => {
                log::error!("Failed to verify snapshot: {e}");
                serde_json::to_string(&btrfs::VerificationResult {
                    is_valid: false,
                    errors: vec![format!("Verification failed: {}", e)],
                    warnings: vec![],
                })
                .unwrap_or_else(|_| {
                    r#"{"is_valid":false,"errors":["Failed to verify"],"warnings":[]}"#.to_string()
                })
            }
        }
    }

    /// Preview what will happen if a snapshot is restored
    /// Save schedules TOML configuration file
    async fn get_apt_snapshot_policy(&self) -> (bool, bool) {
        let config = WaypointConfig::new();
        match anduinos_recovery_engine::AptSnapshotPolicy::load_from_file(
            &config.apt_snapshot_policy,
        ) {
            Ok(policy) => (policy.snapshot_before, policy.snapshot_after),
            Err(error) => {
                log::warn!("Could not load APT snapshot policy: {error}");
                let policy = anduinos_recovery_engine::AptSnapshotPolicy::default();
                (policy.snapshot_before, policy.snapshot_after)
            }
        }
    }

    async fn save_apt_snapshot_policy(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_before: bool,
        snapshot_after: bool,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CONFIGURE).await {
            audit::log_auth_failure(
                uid.clone(),
                pid,
                POLKIT_ACTION_CONFIGURE,
                &error.to_string(),
            );
            return (false, format!("Authorization failed: {error}"));
        }
        let policy = anduinos_recovery_engine::AptSnapshotPolicy {
            snapshot_before,
            snapshot_after,
        };
        let path = WaypointConfig::new().apt_snapshot_policy;
        match policy.save_to_file(&path) {
            Ok(()) => {
                audit::log_config_change(uid, pid, "apt-snapshots", true, None);
                (true, "APT snapshot policy saved".into())
            }
            Err(error) => {
                audit::log_config_change(
                    uid,
                    pid,
                    "apt-snapshots",
                    false,
                    Some(&error.to_string()),
                );
                (
                    false,
                    format!("Failed to save APT snapshot policy: {error}"),
                )
            }
        }
    }

    async fn get_automation_config(&self) -> String {
        let path = WaypointConfig::new().automation_config;
        let config = AutomationConfig::load_from_file(&path).unwrap_or_else(|error| {
            log::warn!("Could not load automation policy: {error}");
            AutomationConfig::default()
        });
        serde_json::to_string(&config).unwrap_or_else(|error| {
            log::error!("Could not serialize automation policy: {error}");
            "{}".to_string()
        })
    }

    async fn save_automation_config(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        json: String,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CONFIGURE).await {
            audit::log_auth_failure(
                uid.clone(),
                pid,
                POLKIT_ACTION_CONFIGURE,
                &error.to_string(),
            );
            return (false, format!("Authorization failed: {error}"));
        }
        let config = match serde_json::from_str::<AutomationConfig>(&json) {
            Ok(config) => config,
            Err(error) => return (false, format!("Invalid automation configuration: {error}")),
        };
        if let Err(error) = config.validate() {
            return (false, format!("Invalid automation configuration: {error}"));
        }
        match config.save_to_file(&WaypointConfig::new().automation_config) {
            Ok(()) => {
                audit::log_config_change(uid, pid, "automation", true, None);
                (true, "Automation configuration saved".into())
            }
            Err(error) => {
                audit::log_config_change(uid, pid, "automation", false, Some(&error.to_string()));
                (
                    false,
                    format!("Failed to save automation configuration: {error}"),
                )
            }
        }
    }

    /// Save schedules TOML configuration file
    /// Restart scheduler service
    async fn restart_scheduler(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(e) = check_authorization(&hdr, connection, POLKIT_ACTION_CONFIGURE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CONFIGURE, &e.to_string());
            return (false, format!("Authorization failed: {e}"));
        }

        match run_command(
            "/usr/bin/systemctl",
            &["enable", "--now", "anduinos-waypoint-scheduler.timer"],
        ) {
            Ok(()) => {
                if let Err(error) = run_command(
                    "/usr/bin/systemctl",
                    &["start", "--no-block", "anduinos-waypoint-scheduler.service"],
                ) {
                    log::warn!("Could not start an immediate automation check: {error}");
                }
                audit::log_operation(
                    uid,
                    pid,
                    "apply_scheduler_state",
                    "anduinos-waypoint-scheduler.timer",
                    true,
                    None,
                );
                (true, "Automatic snapshot timer is enabled".to_string())
            }
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "apply_scheduler_state",
                    "anduinos-waypoint-scheduler.timer",
                    false,
                    Some(&error.to_string()),
                );
                (
                    false,
                    format!("Failed to apply scheduler service state: {error}"),
                )
            }
        }
    }

    /// Get scheduler service status
    async fn get_scheduler_status(&self) -> String {
        let enabled = run_command_with_output(
            "/usr/bin/systemctl",
            &["is-enabled", "anduinos-waypoint-scheduler.timer"],
        )
        .map(|(stdout, _)| stdout.trim() == "enabled")
        .unwrap_or(false);
        if !enabled {
            return "disabled".to_string();
        }

        let active = run_command_with_output(
            "/usr/bin/systemctl",
            &["is-active", "anduinos-waypoint-scheduler.timer"],
        )
        .map(|(stdout, _)| stdout.trim() == "active")
        .unwrap_or_else(|e| {
            log::warn!("Failed to query scheduler status: {e}");
            false
        });
        if !active {
            return "stopped".to_string();
        }
        run_command_with_output(
            "/usr/bin/systemctl",
            &[
                "show",
                "anduinos-waypoint-scheduler.timer",
                "--property=NextElapseUSecRealtime",
                "--value",
            ],
        )
        .map(|(stdout, _)| format!("running · next run {}", stdout.trim()))
        .unwrap_or_else(|_| "running".to_string())
    }

    /// Apply only the retention policy owned by configured automatic schedules.
    async fn apply_schedule_retention(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        #[zbus(signal_context)] ctxt: zbus::SignalContext<'_>,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(e) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_DELETE, &e.to_string());
            return (false, format!("Authorization failed: {e}"));
        }

        let response = match Self::apply_schedule_retention_impl() {
            Ok(summary) => {
                if cleanup_success_notification_enabled()
                    && summary.system_deleted + summary.personal_deleted > 0
                    && let Err(error) = Self::automatic_cleanup_succeeded(
                        &ctxt,
                        summary.system_deleted,
                        summary.personal_deleted,
                    )
                    .await
                {
                    log::warn!("Could not emit automatic cleanup notification: {error}");
                }
                (true, summary.message())
            }
            Err(error) => result_to_dbus_response(Err(error), "Schedule retention failed"),
        };
        audit::log_operation(
            uid,
            pid,
            "apply_schedule_retention",
            "automatic-recovery-points",
            response.0,
            (!response.0).then_some(response.1.as_str()),
        );
        response
    }
}

impl WaypointHelper {
    #[cfg(test)]
    fn apt_history_impl(path: &std::path::Path) -> Result<String> {
        let contents = Self::read_apt_history_file(path, false)?;
        serde_json::to_string(&waypoint_common::apt_history::parse_apt_history(&contents))
            .context("Failed to serialize APT history")
    }

    fn apt_history_directory_impl(directory: &std::path::Path) -> Result<String> {
        const MAX_HISTORY_FILES: usize = 32;
        let metadata = std::fs::symlink_metadata(directory)
            .with_context(|| format!("Failed to inspect {}", directory.display()))?;
        if !metadata.file_type().is_dir() {
            anyhow::bail!("APT history location is not a real directory");
        }

        let mut files = std::fs::read_dir(directory)
            .with_context(|| format!("Failed to read {}", directory.display()))?
            .filter_map(|entry| entry.ok())
            .filter_map(|entry| {
                let name = entry.file_name();
                let name = name.to_str()?;
                let (rank, compressed) = apt_history_file_rank(name)?;
                Some((rank, compressed, entry.path(), name.to_string()))
            })
            .collect::<Vec<_>>();
        files.sort_by_key(|(rank, _, _, _)| *rank);
        files.truncate(MAX_HISTORY_FILES);
        files.sort_by_key(|(rank, _, _, _)| std::cmp::Reverse(*rank));

        let mut report = waypoint_common::apt_history::AptHistoryReport::default();
        for (_, compressed, path, name) in files {
            match Self::read_apt_history_file(&path, compressed) {
                Ok(contents) => {
                    let mut parsed = waypoint_common::apt_history::parse_apt_history(&contents);
                    for issue in &mut parsed.issues {
                        issue.message = format!("{name}: {}", issue.message);
                    }
                    report.transactions.append(&mut parsed.transactions);
                    report.issues.append(&mut parsed.issues);
                }
                Err(error) => report
                    .issues
                    .push(waypoint_common::apt_history::AptHistoryIssue {
                        block: 0,
                        message: format!("{name}: {error}"),
                    }),
            }
        }
        report
            .transactions
            .sort_by_key(|transaction| transaction.start);
        serde_json::to_string(&report).context("Failed to serialize APT history")
    }

    fn read_apt_history_file(path: &std::path::Path, compressed: bool) -> Result<String> {
        use std::io::Read;
        use std::os::unix::fs::OpenOptionsExt;

        const MAX_HISTORY_BYTES: u64 = 8 * 1024 * 1024;
        let metadata = std::fs::symlink_metadata(path)
            .with_context(|| format!("Failed to inspect {}", path.display()))?;
        if !metadata.file_type().is_file() || metadata.len() > MAX_HISTORY_BYTES {
            anyhow::bail!("APT history is not a bounded regular file");
        }
        let file = std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)
            .with_context(|| format!("Failed to open {}", path.display()))?;
        let reader: Box<dyn Read> = if compressed {
            Box::new(flate2::read::GzDecoder::new(file))
        } else {
            Box::new(file)
        };
        let mut contents = String::new();
        reader
            .take(MAX_HISTORY_BYTES + 1)
            .read_to_string(&mut contents)
            .context("Failed to read APT history")?;
        if contents.len() as u64 > MAX_HISTORY_BYTES {
            anyhow::bail!("APT history exceeds the safety limit");
        }
        Ok(contents)
    }

    fn recovery_engine_status_impl(store_root: &std::path::Path) -> Result<String> {
        let pending = TransactionStore::new(store_root)
            .load_pending()
            .map_err(|error| anyhow::anyhow!(error.message))?;
        let deployments = DeploymentStore::new(store_root).discover();
        let personal = PersonalSnapshotEngine::default().discover();
        let package_counts = deployments
            .deployments
            .iter()
            .filter_map(|record| {
                let path = store_root
                    .join("deployments")
                    .join(record.id.to_string())
                    .join("root/var/lib/dpkg/status");
                packages::get_packages_from_status(&path)
                    .ok()
                    .map(|packages| (record.id.to_string(), packages.len()))
            })
            .collect::<std::collections::HashMap<_, _>>();
        let personal_sizes = btrfs::get_personal_spaces(&personal.snapshots);
        let layout = layout::inspect_current();
        let available = layout.is_supported();
        serde_json::to_string(&serde_json::json!({
            "schema_version": 1,
            "available": available,
            "store_root": store_root,
            "pending": pending,
            "deployment_count": deployments.deployments.len(),
            "deployments": deployments.deployments,
            "system_package_counts": package_counts,
            "personal_snapshot_count": personal.snapshots.len(),
            "personal_snapshots": personal.snapshots,
            "personal_sizes": personal_sizes,
            "issues": deployments.issues,
            "personal_issues": personal.issues,
            "layout": layout,
        }))
        .context("Failed to serialize recovery engine status")
    }

    fn apply_schedule_retention_impl() -> Result<ScheduleRetentionSummary> {
        let layout = layout::inspect_current();
        if !layout.is_supported() {
            anyhow::bail!("The complete AnduinOS Btrfs layout is required");
        }
        let config = WaypointConfig::default();
        let automation = AutomationConfig::load_from_file(&config.automation_config)
            .context("Failed to load automatic snapshot policy")?;
        let deployments = DeploymentStore::default().discover();
        if !deployments.issues.is_empty() {
            anyhow::bail!("System snapshot metadata contains unresolved issues");
        }
        let now = chrono::Utc::now();
        let system_candidates = deployments
            .deployments
            .iter()
            .map(|record| SnapshotCandidate {
                id: record.id.to_string(),
                created_at: record.created_at,
                local_offset_seconds: record
                    .created_at
                    .with_timezone(&chrono::Local)
                    .offset()
                    .local_minus_utc(),
                cleanup_policy: if record.pinned
                    || matches!(
                        record.kind,
                        DeploymentKind::Factory
                            | DeploymentKind::PreRollback
                            | DeploymentKind::Imported
                    ) {
                    CleanupPolicy::KeepForever
                } else {
                    CleanupPolicy::Automatic
                },
                is_ready: record.state == DeploymentState::Ready,
                is_busy: false,
                is_restore_referenced: record.state.protects_from_deletion(),
            })
            .collect::<Vec<_>>();
        let system_decisions = evaluate_retention(&system_candidates, &automation.system, now)
            .context("Failed to evaluate system snapshot retention")?;
        let engine = OperationEngine::default();
        let mut deleted = 0u64;
        let mut retained = 0u64;
        for decision in system_decisions
            .iter()
            .filter(|decision| decision.action == RetentionAction::Delete)
        {
            let id = decision
                .snapshot_id
                .parse::<DeploymentId>()
                .map_err(|error| {
                    anyhow::anyhow!("Retention selected an invalid deployment ID: {error}")
                })?;
            match engine.delete_automatic(&layout, id, 1) {
                Ok(()) => deleted += 1,
                Err(error) => {
                    retained += 1;
                    log::info!("Retention kept recovery point {id}: {error}");
                }
            }
        }
        let personal_engine = PersonalSnapshotEngine::default();
        let personal = personal_engine.discover();
        if !personal.issues.is_empty() {
            anyhow::bail!("Home snapshot metadata contains unresolved issues");
        }
        let personal_candidates = personal
            .snapshots
            .iter()
            .map(|record| SnapshotCandidate {
                id: record.id.to_string(),
                created_at: record.created_at,
                local_offset_seconds: record
                    .created_at
                    .with_timezone(&chrono::Local)
                    .offset()
                    .local_minus_utc(),
                cleanup_policy: if record.pinned {
                    CleanupPolicy::KeepForever
                } else {
                    CleanupPolicy::Automatic
                },
                is_ready: record.state == PersonalSnapshotState::Ready,
                is_busy: false,
                is_restore_referenced: false,
            })
            .collect::<Vec<_>>();
        let personal_decisions = evaluate_retention(&personal_candidates, &automation.home, now)
            .context("Failed to evaluate Home snapshot retention")?;
        let mut personal_deleted = 0u64;
        let mut personal_retained = 0u64;
        for decision in personal_decisions
            .iter()
            .filter(|decision| decision.action == RetentionAction::Delete)
        {
            let id = decision
                .snapshot_id
                .parse::<PersonalSnapshotId>()
                .map_err(|error| {
                    anyhow::anyhow!("Retention selected an invalid personal snapshot ID: {error}")
                })?;
            match personal_engine.delete(&layout, id) {
                Ok(()) => {
                    personal_deleted += 1;
                }
                Err(error) => {
                    personal_retained += 1;
                    log::info!("Retention kept Personal Files history point {id}: {error}");
                }
            }
        }
        Ok(ScheduleRetentionSummary {
            system_deleted: deleted,
            personal_deleted,
            system_retained: retained,
            personal_retained,
        })
    }

    #[cfg(any())]
    fn compare_snapshots_impl(old_snapshot_name: &str, new_snapshot_name: &str) -> Result<String> {
        let old_id = old_snapshot_name
            .parse::<DeploymentId>()
            .context("Invalid old recovery point ID")?;
        let new_id = new_snapshot_name
            .parse::<DeploymentId>()
            .context("Invalid new recovery point ID")?;
        let engine = OperationEngine::default();
        engine
            .verify(
                &layout::inspect_current(),
                old_id,
                |_phase, _fraction, _message| {},
            )
            .map_err(|error| anyhow::anyhow!(error.to_string()))?;
        engine
            .verify(
                &layout::inspect_current(),
                new_id,
                |_phase, _fraction, _message| {},
            )
            .map_err(|error| anyhow::anyhow!(error.to_string()))?;

        let config = WaypointConfig::default();
        let old_path = config.snapshot_dir.join(old_snapshot_name).join("root");
        let new_path = config.snapshot_dir.join(new_snapshot_name).join("root");

        // Verify both snapshots exist
        if !old_path.exists() {
            anyhow::bail!("Old snapshot not found: {}", old_path.display());
        }
        if !new_path.exists() {
            anyhow::bail!("New snapshot not found: {}", new_path.display());
        }

        let old_files = parse_find_output(&bounded_find(&old_path)?)?;
        let new_files = parse_find_output(&bounded_find(&new_path)?)?;

        // Compare and detect changes
        let changes = compare_file_lists(&old_files, &new_files);

        let json =
            serde_json::to_string(&changes).context("Failed to serialize changes to JSON")?;
        if json.len() > MAX_FILE_COMPARISON_JSON_BYTES {
            anyhow::bail!("File comparison exceeds the response safety limit");
        }
        Ok(json)
    }

    #[cfg(any())]
    fn compare_deployment_packages_impl(
        old_snapshot_name: &str,
        new_snapshot_name: &str,
    ) -> Result<String> {
        const MAX_COMPARISON_BYTES: usize = 8 * 1024 * 1024;
        let old_id = old_snapshot_name
            .parse::<DeploymentId>()
            .context("Invalid old recovery point ID")?;
        let new_id = new_snapshot_name
            .parse::<DeploymentId>()
            .context("Invalid new recovery point ID")?;
        let engine = OperationEngine::default();
        let report = layout::inspect_current();
        engine
            .verify(&report, old_id, |_phase, _fraction, _message| {})
            .map_err(|error| anyhow::anyhow!(error.to_string()))?;
        engine
            .verify(&report, new_id, |_phase, _fraction, _message| {})
            .map_err(|error| anyhow::anyhow!(error.to_string()))?;

        let config = WaypointConfig::default();
        let old_status = config
            .snapshot_dir
            .join(old_snapshot_name)
            .join("root/var/lib/dpkg/status");
        let new_status = config
            .snapshot_dir
            .join(new_snapshot_name)
            .join("root/var/lib/dpkg/status");
        let comparison = crate::packages::compare_status_files(&old_status, &new_status)?;
        let json =
            serde_json::to_string(&comparison).context("Failed to serialize package comparison")?;
        if json.len() > MAX_COMPARISON_BYTES {
            anyhow::bail!("Package comparison exceeds the response safety limit");
        }
        Ok(json)
    }

    /// Get quota usage information
    #[cfg(any())]
    fn get_quota_usage_impl() -> Result<String> {
        use waypoint_common::QuotaUsage;

        let config = WaypointConfig::default();
        let snapshot_dir = &config.snapshot_dir;
        let snapshot_dir_str = snapshot_dir.to_str().ok_or_else(|| {
            anyhow::anyhow!(
                "Snapshot directory path contains invalid UTF-8: {}",
                snapshot_dir.display()
            )
        })?;

        // Get qgroup information
        let (stdout, _) = run_command_with_output(
            "/usr/bin/btrfs",
            &["qgroup", "show", "--raw", snapshot_dir_str],
        )?;

        // Parse qgroup output
        // Format: qgroupid rfer excl max_rfer max_excl
        // Sum up all level-0 qgroups (snapshots)
        let mut total_referenced = 0u64;
        let mut total_exclusive = 0u64;
        let mut parsed_lines = 0;

        for (line_num, line) in stdout.lines().skip(2).enumerate() {
            // Skip header lines
            let parts: Vec<&str> = line.split_whitespace().collect();
            if !parts.is_empty() && parts[0].starts_with("0/") {
                // Only count level-0 qgroups (actual snapshots)
                if parts.len() < 3 {
                    log::warn!(
                        "Unexpected qgroup output format at line {}: '{}'. \
                         Expected at least 3 fields but got {}",
                        line_num + 3, // +3 because we skipped 2 header lines
                        line,
                        parts.len()
                    );
                    continue;
                }

                match (parts[1].parse::<u64>(), parts[2].parse::<u64>()) {
                    (Ok(rfer), Ok(excl)) => {
                        parsed_lines += 1;
                        // Use checked_add to detect overflow - fail loudly rather than silently saturate
                        total_referenced = total_referenced.checked_add(rfer)
                            .ok_or_else(|| anyhow::anyhow!(
                                "Quota calculation overflow: total referenced bytes exceed u64::MAX. \
                                 Current total: {total_referenced}, attempted to add: {rfer}"
                            ))?;
                        total_exclusive = total_exclusive.checked_add(excl)
                            .ok_or_else(|| anyhow::anyhow!(
                                "Quota calculation overflow: total exclusive bytes exceed u64::MAX. \
                                 Current total: {total_exclusive}, attempted to add: {excl}"
                            ))?;
                    }
                    (Err(e1), Err(e2)) => {
                        log::warn!(
                            "Failed to parse qgroup values at line {}: '{}'. \
                             Both rfer ('{}') and excl ('{}') parse failed: {}, {}",
                            line_num + 3,
                            line,
                            parts[1],
                            parts[2],
                            e1,
                            e2
                        );
                    }
                    (Err(e), Ok(_)) => {
                        log::warn!(
                            "Failed to parse qgroup rfer value at line {}: '{}'. \
                             Parse error: {}",
                            line_num + 3,
                            line,
                            e
                        );
                    }
                    (Ok(_), Err(e)) => {
                        log::warn!(
                            "Failed to parse qgroup excl value at line {}: '{}'. \
                             Parse error: {}",
                            line_num + 3,
                            line,
                            e
                        );
                    }
                }
            }
        }

        // Log if no qgroups were parsed (possible format change or quotas not enabled)
        if parsed_lines == 0 {
            log::info!(
                "No level-0 qgroups found in btrfs output. \
                 This is normal if quotas are not enabled or no snapshots exist yet."
            );
        }

        let usage = QuotaUsage {
            referenced: total_referenced,
            exclusive: total_exclusive,
        };

        serde_json::to_string(&usage).context("Failed to serialize quota usage to JSON")
    }
}

fn apt_history_file_rank(name: &str) -> Option<(u32, bool)> {
    if name == "history.log" {
        return Some((0, false));
    }
    let suffix = name.strip_prefix("history.log.")?;
    let (number, compressed) = suffix
        .strip_suffix(".gz")
        .map_or((suffix, false), |number| (number, true));
    if number.is_empty() || !number.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let rank = number.parse::<u32>().ok()?;
    (rank > 0 && rank <= 999).then_some((rank, compressed))
}

/// Sanitize error messages to avoid exposing sensitive system paths
///
/// This function removes full paths from error messages that will be sent
/// to unprivileged clients over D-Bus, logging the full error internally.
fn sanitize_error_for_client(error: &anyhow::Error) -> String {
    let full_error = format!("{error:#}");

    // Log the full error for administrators
    log::error!("Operation failed: {full_error}");

    // Return sanitized version to client
    // Remove common path prefixes that could expose system layout
    let sanitized = full_error
        .replace("/home/", "<home>/")
        .replace("/root/", "<root>/")
        .replace("/etc/", "<etc>/")
        .replace("/var/", "<var>/")
        .replace("/usr/", "<usr>/")
        .replace("/opt/", "<opt>/")
        .replace("/tmp/", "<tmp>/")
        .replace("/.snapshots/", "<snapshots>/");

    // If the error is very long (contains stack traces, etc.), truncate it
    if sanitized.len() > 500 {
        format!("{}... (see system logs for details)", &sanitized[..500])
    } else {
        sanitized
    }
}

/// Convert a Result<String> to (bool, String) for D-Bus responses
/// Applies consistent error sanitization and formatting
fn result_to_dbus_response(result: Result<String>, error_prefix: &str) -> (bool, String) {
    match result {
        Ok(msg) => (true, msg),
        Err(e) => {
            let sanitized = sanitize_error_for_client(&e);
            (false, format!("{error_prefix}: {sanitized}"))
        }
    }
}

fn automatic_success_notification_enabled() -> bool {
    let path = WaypointConfig::default().automation_config;
    automatic_success_notification_enabled_at(&path)
}

fn cleanup_success_notification_enabled() -> bool {
    AutomationConfig::load_from_file(&WaypointConfig::new().automation_config)
        .map(|config| config.notifications.notify_after_cleanup)
        .unwrap_or(false)
}

fn automatic_pre_notification_enabled() -> bool {
    AutomationConfig::load_from_file(&WaypointConfig::default().automation_config)
        .map(|config| config.notifications.notify_before_scheduled)
        .unwrap_or(NotificationPolicy::default().notify_before_scheduled)
}

fn automatic_success_notification_enabled_at(path: &std::path::PathBuf) -> bool {
    match AutomationConfig::load_from_file(path) {
        Ok(config) => config.notifications.notify_after_success,
        Err(error) => {
            log::warn!(
                "Could not load Waypoint notification preference from {}: {error}",
                path.display()
            );
            NotificationPolicy::default().notify_after_success
        }
    }
}

/// Parse btrfs receive --dump output into structured changes
#[derive(Debug, serde::Serialize, serde::Deserialize)]
#[cfg(any())]
struct FileChange {
    change_type: String, // "Added", "Modified", "Deleted"
    path: String,
}

/// File metadata for comparison
#[derive(Debug, Clone)]
#[cfg(any())]
struct FileMetadata {
    kind: u8,
    size: u64,
    mtime: String,
    ctime: String,
}

#[cfg(any())]
const MAX_FIND_OUTPUT_BYTES: u64 = 32 * 1024 * 1024;
#[cfg(any())]
const MAX_FILE_COMPARISON_JSON_BYTES: usize = 8 * 1024 * 1024;
#[cfg(any())]
const MAX_FILE_COMPARISON_ENTRIES: usize = 500_000;

#[cfg(any())]
fn bounded_find(root: &std::path::Path) -> Result<Vec<u8>> {
    use std::io::Read;
    use std::process::Stdio;

    let mut child = Command::new("/usr/bin/find")
        .arg(root)
        .arg("-xdev")
        .arg("-printf")
        // NUL-separated fields preserve spaces. Non-UTF-8 or control-bearing
        // paths are rejected by the parser instead of entering the GUI ABI.
        .arg("%y\\0%P\\0%s\\0%T@\\0%C@\\0")
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .context("Failed to run bounded deployment scan")?;
    let mut output = Vec::new();
    child
        .stdout
        .take()
        .context("Deployment scan has no output pipe")?
        .take(MAX_FIND_OUTPUT_BYTES + 1)
        .read_to_end(&mut output)
        .context("Failed to read deployment scan")?;
    if output.len() as u64 > MAX_FIND_OUTPUT_BYTES {
        let _ = child.kill();
        let _ = child.wait();
        anyhow::bail!("Deployment file listing exceeds the safety limit");
    }
    let status = child.wait().context("Failed to wait for deployment scan")?;
    if !status.success() {
        anyhow::bail!("Deployment file scan failed");
    }
    Ok(output)
}

/// Parse the NUL-delimited find output into bounded, display-safe metadata.
#[cfg(any())]
fn parse_find_output(output: &[u8]) -> Result<std::collections::HashMap<String, FileMetadata>> {
    let mut files = std::collections::HashMap::new();
    let mut fields = output.split(|byte| *byte == 0).collect::<Vec<_>>();
    if fields.last() == Some(&&b""[..]) {
        fields.pop();
    }
    if fields.len() % 5 != 0 {
        anyhow::bail!("Deployment scan returned an incomplete record");
    }

    for record in fields.chunks_exact(5) {
        let [kind, path, size, mtime, ctime] = record else {
            unreachable!();
        };
        if kind.len() != 1 || !kind[0].is_ascii_alphabetic() {
            anyhow::bail!("Deployment scan returned an invalid file type");
        }
        let path = std::str::from_utf8(path).context("Deployment path is not valid UTF-8")?;
        if path.is_empty() {
            continue;
        }
        if path.len() > 4096
            || path.starts_with('/')
            || path.chars().any(char::is_control)
            || path.split('/').any(|component| component == "..")
        {
            anyhow::bail!("Deployment scan returned an unsafe path");
        }
        let size = std::str::from_utf8(size)
            .context("Deployment size is not UTF-8")?
            .parse::<u64>()
            .context("Deployment size is invalid")?;
        let timestamp = |value: &[u8]| -> Result<String> {
            let value = std::str::from_utf8(value).context("Deployment timestamp is not UTF-8")?;
            if value.is_empty()
                || value.len() > 64
                || !value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || matches!(byte, b'.' | b'-'))
            {
                anyhow::bail!("Deployment timestamp is invalid");
            }
            Ok(value.into())
        };
        let metadata = FileMetadata {
            kind: kind[0],
            size,
            mtime: timestamp(mtime)?,
            ctime: timestamp(ctime)?,
        };
        if files.insert(path.to_string(), metadata).is_some() {
            anyhow::bail!("Deployment scan returned a duplicate path");
        }
        if files.len() > MAX_FILE_COMPARISON_ENTRIES {
            anyhow::bail!("Deployment file count exceeds the safety limit");
        }
    }

    Ok(files)
}

/// Compare two file lists and detect changes
#[cfg(any())]
fn compare_file_lists(
    old_files: &std::collections::HashMap<String, FileMetadata>,
    new_files: &std::collections::HashMap<String, FileMetadata>,
) -> Vec<FileChange> {
    let mut changes = Vec::new();
    let mut seen_paths = std::collections::HashSet::new();

    // Find added and modified files
    for (path, new_meta) in new_files {
        if let Some(old_meta) = old_files.get(path) {
            // File exists in both - check if modified
            // Compare size and mtime to detect modifications
            if old_meta.kind != new_meta.kind
                || old_meta.size != new_meta.size
                || old_meta.mtime != new_meta.mtime
                || old_meta.ctime != new_meta.ctime
            {
                let full_path = format!("/{}", path);
                if seen_paths.insert(full_path.clone()) {
                    changes.push(FileChange {
                        change_type: "Modified".to_string(),
                        path: full_path,
                    });
                }
            }
        } else {
            // File only in new snapshot - added
            let full_path = format!("/{}", path);
            if seen_paths.insert(full_path.clone()) {
                changes.push(FileChange {
                    change_type: "Added".to_string(),
                    path: full_path,
                });
            }
        }
    }

    // Find deleted files
    for path in old_files.keys() {
        if !new_files.contains_key(path) {
            let full_path = format!("/{}", path);
            if seen_paths.insert(full_path.clone()) {
                changes.push(FileChange {
                    change_type: "Deleted".to_string(),
                    path: full_path,
                });
            }
        }
    }

    // Sort by path for consistent output
    changes.sort_by(|a, b| a.path.cmp(&b.path));

    changes
}

/// Check Polkit authorization for an action
///
/// Calls org.freedesktop.PolicyKit1.Authority.CheckAuthorization to verify
/// the caller has permission to perform the requested action.
async fn check_authorization(
    hdr: &zbus::message::Header<'_>,
    connection: &Connection,
    action_id: &str,
) -> Result<()> {
    use std::collections::HashMap;
    use zbus::zvariant::{ObjectPath, Value};

    log::debug!("Authorization requested for action: {action_id}");

    // Get the caller's bus name from the message header
    let caller = hdr
        .sender()
        .context("No sender in message header")?
        .to_owned();

    log::debug!("Caller bus name: {caller}");

    // Get the caller's PID from D-Bus
    let response = connection
        .call_method(
            Some("org.freedesktop.DBus"),
            "/org/freedesktop/DBus",
            Some("org.freedesktop.DBus"),
            "GetConnectionUnixProcessID",
            &caller.as_str(),
        )
        .await
        .context("Failed to get caller PID from D-Bus")?;

    let caller_pid: u32 = response
        .body()
        .deserialize()
        .context("Failed to deserialize caller PID")?;

    log::debug!("Caller PID: {caller_pid}");

    // Get process start time from /proc
    let start_time = get_process_start_time(caller_pid)?;

    // Build the subject structure for Polkit
    // Subject is (subject_kind, subject_details)
    let mut subject_details: HashMap<String, Value> = HashMap::new();
    subject_details.insert("pid".to_string(), Value::U32(caller_pid));
    subject_details.insert("start-time".to_string(), Value::U64(start_time));

    let subject = ("unix-process", subject_details);

    // Details dict (empty for now)
    let details: HashMap<String, String> = HashMap::new();

    // Flags: 1 = AllowUserInteraction (show password prompt if needed)
    // Note: This allows interactive authentication dialogs. For automated contexts
    // or security-sensitive deployments, consider using flag 0 (no interaction)
    // and configuring passwordless Polkit rules in /etc/polkit-1/rules.d/
    let flags: u32 = 1;

    // Cancellation ID (empty string = no cancellation)
    // Could be used to cancel long-running auth requests, but not needed here
    let cancellation_id = "";

    // Call Polkit CheckAuthorization
    // Note: Polkit handles timeouts internally based on system configuration.
    // Default timeout is typically 5 minutes for authentication dialogs.
    // For more restrictive timeouts, configure in /etc/polkit-1/polkit.conf
    let polkit_path = ObjectPath::try_from("/org/freedesktop/PolicyKit1/Authority")
        .context("Invalid Polkit object path")?;

    // Add explicit timeout to D-Bus call
    // This prevents indefinite hangs if Polkit service is unresponsive
    const POLKIT_TIMEOUT_SECONDS: u64 = 120;
    let timeout_secs = POLKIT_TIMEOUT_SECONDS;

    let result = tokio::time::timeout(
        std::time::Duration::from_secs(timeout_secs),
        connection.call_method(
            Some("org.freedesktop.PolicyKit1"),
            polkit_path,
            Some("org.freedesktop.PolicyKit1.Authority"),
            "CheckAuthorization",
            &(subject, action_id, details, flags, cancellation_id),
        ),
    )
    .await
    .with_context(|| format!("Polkit authorization timed out after {timeout_secs} seconds"))?;

    let msg = result.context("Failed to call Polkit CheckAuthorization")?;

    // Result is (is_authorized, is_challenge, details)
    let (is_authorized, is_challenge, auth_details): (bool, bool, HashMap<String, String>) = msg
        .body()
        .deserialize()
        .context("Failed to deserialize Polkit response")?;

    log::debug!(
        "Authorization result: authorized={is_authorized}, challenge={is_challenge}, details={auth_details:?}"
    );

    if is_authorized {
        Ok(())
    } else {
        anyhow::bail!("Action '{action_id}' not authorized");
    }
}

/// Get process start time from `/proc/[pid]/stat`
fn get_process_start_time(pid: u32) -> Result<u64> {
    use std::fs;

    let stat_path = format!("/proc/{pid}/stat");
    let stat_content =
        fs::read_to_string(&stat_path).context(format!("Failed to read {stat_path}"))?;

    // The start time is the 22nd field in /proc/[pid]/stat
    // Fields are: pid (comm) state ppid ... starttime ...
    // We need to handle the (comm) field which may contain spaces and special characters

    // Find the last ')' to skip the comm field
    let start_pos = stat_content
        .rfind(')')
        .context("Invalid /proc/[pid]/stat format: missing closing parenthesis")?;

    // Ensure there's content after the ')' character
    if start_pos + 1 >= stat_content.len() {
        anyhow::bail!("Invalid /proc/[pid]/stat format: no fields after command name");
    }

    let fields: Vec<&str> = stat_content[start_pos + 1..].split_whitespace().collect();

    // After skipping (comm), starttime is field 20 (0-indexed 19)
    // According to proc(5) man page, there should be at least 44 fields in modern kernels
    const MIN_REQUIRED_FIELDS: usize = 20;
    if fields.len() < MIN_REQUIRED_FIELDS {
        anyhow::bail!(
            "Not enough fields in /proc/{}/stat (expected at least {}, got {})",
            pid,
            MIN_REQUIRED_FIELDS,
            fields.len()
        );
    }

    let start_time_str = fields.get(19).ok_or_else(|| {
        anyhow::anyhow!("Missing start_time field (index 19) in /proc/{pid}/stat")
    })?;
    let start_time: u64 = start_time_str.parse().context(format!(
        "Failed to parse process start time from field '{start_time_str}' (field 20)"
    ))?;

    log::debug!("Process {pid} start time: {start_time}");

    Ok(start_time)
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    // Must run as root
    if nix::unistd::geteuid().as_raw() != 0 {
        log::error!("anduinos-waypoint-helper must be run as root");
        std::process::exit(1);
    }

    // Initialize configuration
    btrfs::init_config();

    log::info!(
        "Starting Waypoint Helper service v{}",
        env!("CARGO_PKG_VERSION")
    );

    // Build the D-Bus connection
    let helper = WaypointHelper::new();
    let _connection = ConnectionBuilder::system()?
        .name(DBUS_SERVICE_NAME)?
        .serve_at(DBUS_OBJECT_PATH, helper)?
        .build()
        .await?;

    log::info!("AnduinOS Waypoint helper is ready at {DBUS_OBJECT_PATH}");

    // Wait for termination signal
    let mut sigterm = signal(SignalKind::terminate())?;
    let mut sigint = signal(SignalKind::interrupt())?;

    tokio::select! {
        _ = sigterm.recv() => log::info!("Received SIGTERM, shutting down..."),
        _ = sigint.recv() => log::info!("Received SIGINT, shutting down..."),
    }

    Ok(())
}
fn run_command(cmd: &str, args: &[&str]) -> Result<()> {
    let output = Command::new(cmd)
        .args(args)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()
        .context(format!("Failed to run {cmd}"))?;
    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(anyhow::anyhow!("{} failed: {}", cmd, stderr.trim()))
    }
}

fn run_command_with_output(cmd: &str, args: &[&str]) -> Result<(String, String)> {
    let output = Command::new(cmd)
        .args(args)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()
        .context(format!("Failed to run {cmd}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if output.status.success() {
        Ok((stdout, stderr))
    } else {
        Err(anyhow::anyhow!("{} failed: {}", cmd, stderr.trim()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn automatic_success_notification_uses_waypoint_v2_policy() {
        let path = std::env::temp_dir().join(format!(
            "anduinos-waypoint-notification-schedule-{}-{}.toml",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let mut config = AutomationConfig::default();
        config.notifications.notify_after_success = false;
        config.save_to_file(&path).unwrap();
        assert!(!automatic_success_notification_enabled_at(&path));
        config.notifications.notify_after_success = true;
        config.save_to_file(&path).unwrap();
        assert!(automatic_success_notification_enabled_at(&path));
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn empty_recovery_store_reports_layout_availability_without_inventing_deployments() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-waypoint-engine-status-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&root).unwrap();

        let status = WaypointHelper::recovery_engine_status_impl(&root).unwrap();
        let value: serde_json::Value = serde_json::from_str(&status).unwrap();
        assert_eq!(
            value["available"],
            serde_json::json!(layout::inspect_current().is_supported())
        );
        assert_eq!(value["deployment_count"], 0);
        assert!(value["pending"].is_null());
        assert_eq!(value["issues"], serde_json::json!([]));

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn reads_apt_history_as_structured_json() {
        let path = std::env::temp_dir().join(format!(
            "anduinos-waypoint-apt-history-{}.log",
            std::process::id()
        ));
        std::fs::write(
            &path,
            "Start-Date: 2026-08-04  13:50:17\n\
             Commandline: apt install example\n\
             Install: example:amd64 (1.0-1)\n\
             End-Date: 2026-08-04  13:50:18\n",
        )
        .unwrap();

        let history = WaypointHelper::apt_history_impl(&path).unwrap();
        let value: serde_json::Value = serde_json::from_str(&history).unwrap();
        assert_eq!(
            value["transactions"][0]["changes"][0]["package"],
            "example:amd64"
        );

        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn reads_rotated_and_compressed_apt_history_in_time_order() {
        let directory = std::env::temp_dir().join(format!(
            "anduinos-waypoint-apt-history-dir-{}",
            std::process::id()
        ));
        std::fs::create_dir(&directory).unwrap();
        std::fs::write(
            directory.join("history.log"),
            "Start-Date: 2026-08-04  13:50:17\n\
             Install: newest:amd64 (2.0-1)\n\
             End-Date: 2026-08-04  13:50:18\n",
        )
        .unwrap();
        let compressed = std::fs::File::create(directory.join("history.log.1.gz")).unwrap();
        let mut encoder = flate2::write::GzEncoder::new(compressed, flate2::Compression::default());
        encoder
            .write_all(
                b"Start-Date: 2026-08-03  10:00:00\n\
                  Install: oldest:amd64 (1.0-1)\n\
                  End-Date: 2026-08-03  10:00:01\n",
            )
            .unwrap();
        encoder.finish().unwrap();
        std::fs::write(directory.join("history.log.untrusted"), "ignored").unwrap();

        let history = WaypointHelper::apt_history_directory_impl(&directory).unwrap();
        let value: serde_json::Value = serde_json::from_str(&history).unwrap();
        assert_eq!(value["transactions"].as_array().unwrap().len(), 2);
        assert_eq!(
            value["transactions"][0]["changes"][0]["package"],
            "oldest:amd64"
        );
        assert_eq!(
            value["transactions"][1]["changes"][0]["package"],
            "newest:amd64"
        );

        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn accepts_only_bounded_apt_history_rotation_names() {
        assert_eq!(apt_history_file_rank("history.log"), Some((0, false)));
        assert_eq!(apt_history_file_rank("history.log.12"), Some((12, false)));
        assert_eq!(apt_history_file_rank("history.log.2.gz"), Some((2, true)));
        assert_eq!(apt_history_file_rank("history.log.0.gz"), None);
        assert_eq!(apt_history_file_rank("history.log.old.gz"), None);
        assert_eq!(apt_history_file_rank("term.log.1.gz"), None);
    }

    #[cfg(any())]
    #[test]
    fn bounded_file_scan_preserves_spaces_and_rejects_control_paths() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-waypoint-file-scan-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("name with spaces"), "content").unwrap();

        let files = parse_find_output(&bounded_find(&root).unwrap()).unwrap();
        assert!(files.contains_key("name with spaces"));

        let unsafe_record = b"f\0line\nbreak\0\x31\0\x31.\x30\0\x31.\x30\0";
        assert!(parse_find_output(unsafe_record).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
