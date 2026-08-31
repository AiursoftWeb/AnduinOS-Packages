// Disk Snapshots Manager Helper - Privileged D-Bus service for snapshot operations
// This binary runs with elevated privileges via D-Bus activation

use anduinos_recovery_engine::{
    RECOVERY_STORE_ROOT,
    confirmation::ConfirmationEngine,
    layout,
    model::{DeploymentId, DeploymentKind, DeploymentState},
    operations::{OperationEngine, ScheduledSnapshotOutcome},
    personal::{
        PersonalSnapshotEngine, PersonalSnapshotId, PersonalSnapshotState,
        ScheduledPersonalSnapshotOutcome,
    },
    rollback::RollbackCoordinator,
    store::DeploymentStore,
    system_browser::SystemSnapshotBrowser,
    transaction::TransactionStore,
};
use anyhow::{Context, Result};
use snapshots_manager_common::*;
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use tokio::signal::unix::{SignalKind, signal};
use zbus::{Connection, ConnectionBuilder, interface};

mod audit;
mod btrfs;
mod packages;
mod smart;
mod systemd_worker;

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

/// Atomic sliding-window quota for passwordless manual Home snapshots.
/// Scheduled root work deliberately uses a separate entry point and does not
/// consume this interactive-user allowance.
#[derive(Debug, Clone)]
struct PersonalSnapshotQuota {
    attempts: std::sync::Arc<std::sync::Mutex<std::collections::VecDeque<std::time::Instant>>>,
    window: std::time::Duration,
    allowance: usize,
}

impl PersonalSnapshotQuota {
    fn new(allowance: usize, window: std::time::Duration) -> Self {
        Self {
            attempts: std::sync::Arc::new(std::sync::Mutex::new(std::collections::VecDeque::new())),
            window,
            allowance,
        }
    }

    /// Reserve one machine-wide passwordless creation slot. Recording the
    /// reservation before starting Btrfs work closes the concurrent-call race.
    fn reserve(&self) -> bool {
        self.reserve_at(std::time::Instant::now())
    }

    fn reserve_at(&self, now: std::time::Instant) -> bool {
        let mut attempts = self.attempts.lock().unwrap_or_else(|poisoned| {
            MUTEX_POISON_COUNT.fetch_add(1, Ordering::Relaxed);
            log::error!("Personal snapshot quota mutex poisoned; recovering");
            poisoned.into_inner()
        });
        while attempts
            .front()
            .is_some_and(|attempt| now.duration_since(*attempt) >= self.window)
        {
            attempts.pop_front();
        }
        if attempts.len() >= self.allowance {
            return false;
        }
        attempts.push_back(now);
        true
    }
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

/// Main D-Bus service interface for Disk Snapshots Manager operations
struct SnapshotsManagerHelper {
    rate_limiter: RateLimiter,
    personal_snapshot_quota: PersonalSnapshotQuota,
    browse_leases: std::sync::Mutex<std::collections::HashMap<String, SystemBrowseLease>>,
    smart_query: tokio::sync::Mutex<SmartQueryCache>,
}

const SMART_QUERY_CACHE_TTL: std::time::Duration = std::time::Duration::from_secs(30);

#[derive(Default)]
struct SmartQueryCache {
    value: Option<(std::time::Instant, String)>,
}

impl SmartQueryCache {
    fn current(&self, now: std::time::Instant) -> Option<String> {
        self.value
            .as_ref()
            .filter(|(created_at, _)| now.duration_since(*created_at) < SMART_QUERY_CACHE_TTL)
            .map(|(_, value)| value.clone())
    }

    fn store(&mut self, value: String, now: std::time::Instant) {
        self.value = Some((now, value));
    }
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
            "Cleaned up {} automatic system snapshot(s) and {} Home snapshot(s); retained {} system and {} Home snapshot(s) for safety",
            self.system_deleted,
            self.personal_deleted,
            self.system_retained,
            self.personal_retained,
        )
    }
}

impl SnapshotsManagerHelper {
    fn new() -> Self {
        Self {
            // Rate limit: 1 operation per 5 seconds per user
            rate_limiter: RateLimiter::new(5),
            personal_snapshot_quota: PersonalSnapshotQuota::new(
                4,
                std::time::Duration::from_secs(60),
            ),
            browse_leases: std::sync::Mutex::new(std::collections::HashMap::new()),
            smart_query: tokio::sync::Mutex::new(SmartQueryCache::default()),
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

#[interface(name = "org.anduinos.BtrfsSnapshotsManager.Helper")]
impl SnapshotsManagerHelper {
    /// Signal emitted when a snapshot is created
    #[zbus(signal)]
    async fn snapshot_created(
        ctxt: &zbus::SignalContext<'_>,
        snapshot_name: &str,
        created_by: &str,
    ) -> zbus::Result<()>;

    /// Signal emitted when an independent Home snapshot is created.
    #[zbus(signal)]
    async fn personal_snapshot_created(
        ctxt: &zbus::SignalContext<'_>,
        snapshot_id: &str,
        created_by: &str,
    ) -> zbus::Result<()>;

    /// Privacy-preserving desktop event emitted when successful creation
    /// notifications are enabled for manual or automatic snapshots.
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

    /// Report only the state required by an unprivileged Personal Files client.
    /// System recovery details and user-provided snapshot labels are deliberately
    /// omitted because this method is available to every local D-Bus caller.
    async fn get_recovery_engine_status(&self) -> String {
        Self::public_recovery_engine_status_impl(std::path::Path::new(RECOVERY_STORE_ROOT))
            .unwrap_or_else(|error| {
                serde_json::json!({
                    "schema_version": 2,
                    "available": false,
                    "error": error.to_string(),
                })
                .to_string()
            })
    }

    /// Report the complete recovery state. The system-bus policy exposes this
    /// method only to root and members of the sudo group.
    async fn get_privileged_recovery_engine_status(&self) -> String {
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

    /// Measure one snapshot outside the status-query path and cache the result for the GUI.
    async fn measure_snapshot_space(&self, scope: String, id: String) -> (bool, String) {
        let measured = tokio::task::spawn_blocking(move || {
            btrfs::measure_snapshot_space(std::path::Path::new(RECOVERY_STORE_ROOT), &scope, &id)
        })
        .await;
        match measured {
            Ok(Ok(space)) => match serde_json::to_string(&space) {
                Ok(json) => (true, json),
                Err(error) => (false, format!("Could not serialize snapshot size: {error}")),
            },
            Ok(Err(error)) => (false, error.to_string()),
            Err(error) => (false, format!("Snapshot size measurement stopped: {error}")),
        }
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

    /// Create an immutable AnduinOS system snapshot.
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
                    "Please wait {} seconds before creating another system snapshot",
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
                        log::warn!("Could not emit system snapshot creation signal: {error}");
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
                    format!("Could not serialize system snapshot: {error}"),
                ),
            },
            Err(error) => {
                audit::log_snapshot_create(uid, pid, &title, false, Some(&error.to_string()));
                (false, error.to_string())
            }
        }
    }

    /// Create an automatic system snapshot while preserving its schedule label.
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
                    "Please wait {} seconds before creating another system snapshot",
                    wait.as_secs()
                ),
            );
        }
        let config = match AutomationConfig::load_from_file(
            &SnapshotsManagerConfig::new().automation_config,
        ) {
            Ok(config) => config,
            Err(error) => return (false, format!("Could not load automation policy: {error}")),
        };
        if !config.system.is_auto_snapshot_enabled {
            return (true, serde_json::json!({ "created": false }).to_string());
        }
        let engine = OperationEngine::default();
        if config.notifications.notify_before_scheduled
            && engine
                .scheduled_snapshot_due(config.system.snapshot_interval_hours, chrono::Utc::now())
                .unwrap_or(false)
        {
            if let Err(error) = Self::automatic_snapshot_starting(&ctxt, "system").await {
                log::warn!("Could not emit scheduled System pre-notification: {error}");
            }
            std::thread::sleep(std::time::Duration::from_secs(10));
        }
        match engine.create_scheduled_if_due(
            &layout::inspect_current(),
            &schedule_id,
            &title,
            &reason,
            config.system.snapshot_interval_hours,
            chrono::Utc::now(),
            |_phase, _fraction, _message| {},
        ) {
            Ok(ScheduledSnapshotOutcome::Created(record)) => match serde_json::to_string(&record) {
                Ok(json) => {
                    audit::log_snapshot_create(uid, pid, &record.id.to_string(), true, None);
                    if let Err(error) =
                        Self::snapshot_created(&ctxt, &record.id.to_string(), "scheduler").await
                    {
                        log::warn!("Could not emit scheduled system snapshot signal: {error}");
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
                    format!("Could not serialize system snapshot: {error}"),
                ),
            },
            Ok(ScheduledSnapshotOutcome::NotDue) => {
                (true, serde_json::json!({ "created": false }).to_string())
            }
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
        if let Err(error) =
            check_authorization(&hdr, connection, POLKIT_ACTION_CREATE_PERSONAL).await
        {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CREATE_PERSONAL, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if pinned
            && let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await
        {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_DELETE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        if !self.personal_snapshot_quota.reserve()
            && let Err(error) =
                check_authorization(&hdr, connection, POLKIT_ACTION_CREATE_PERSONAL_OVERRIDE).await
        {
            audit::log_auth_failure(
                uid,
                pid,
                POLKIT_ACTION_CREATE_PERSONAL_OVERRIDE,
                &error.to_string(),
            );
            return (false, format!("Authorization failed: {error}"));
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
                    "Please wait {} seconds before creating another Home snapshot",
                    wait.as_secs()
                ),
            );
        }
        let config = match AutomationConfig::load_from_file(
            &SnapshotsManagerConfig::new().automation_config,
        ) {
            Ok(config) => config,
            Err(error) => return (false, format!("Could not load automation policy: {error}")),
        };
        if !config.home.is_auto_snapshot_enabled {
            return (true, serde_json::json!({ "created": false }).to_string());
        }
        let engine = PersonalSnapshotEngine::default();
        if config.notifications.notify_before_scheduled
            && engine
                .scheduled_snapshot_due(config.home.snapshot_interval_hours, chrono::Utc::now())
                .unwrap_or(false)
        {
            if let Err(error) = Self::automatic_snapshot_starting(&ctxt, "personal").await {
                log::warn!("Could not emit scheduled Home pre-notification: {error}");
            }
            std::thread::sleep(std::time::Duration::from_secs(10));
        }
        match engine.create_scheduled_if_due(
            &layout::inspect_current(),
            &schedule_id,
            &title,
            &reason,
            config.home.snapshot_interval_hours,
            chrono::Utc::now(),
        ) {
            Ok(ScheduledPersonalSnapshotOutcome::Created(record)) => {
                match serde_json::to_string(&record) {
                    Ok(json) => {
                        audit::log_operation(
                            uid,
                            pid,
                            "create_scheduled_personal_snapshot",
                            &record.id.to_string(),
                            true,
                            None,
                        );
                        if let Err(error) = Self::personal_snapshot_created(
                            &ctxt,
                            &record.id.to_string(),
                            "scheduler",
                        )
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
                            log::warn!(
                                "Could not emit automatic Personal Files notification: {error}"
                            );
                        }
                        (true, json)
                    }
                    Err(error) => (
                        false,
                        format!("Could not serialize personal snapshot: {error}"),
                    ),
                }
            }
            Ok(ScheduledPersonalSnapshotOutcome::NotDue) => {
                (true, serde_json::json!({ "created": false }).to_string())
            }
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

    /// Delete one unpinned Home snapshot.
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
                (true, "Home snapshot deleted".into())
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

    /// Delete multiple unpinned Home snapshots under one
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
            return (false, "No Home snapshots were selected".into());
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
            (true, "Home snapshots deleted".into())
        } else {
            (false, failures.join("\n"))
        }
    }

    /// Protect or unprotect one Home snapshot.
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
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_DELETE).await {
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

    async fn verify_personal_snapshot(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        snapshot_id: String,
    ) -> String {
        if let Err(error) =
            check_authorization(&hdr, connection, POLKIT_ACTION_PERSONAL_FILES).await
        {
            return serde_json::json!({
                "is_valid": false,
                "errors": [format!("Authorization failed: {error}")],
                "warnings": [],
            })
            .to_string();
        }
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
        if let Err(error) =
            check_authorization(&hdr, connection, POLKIT_ACTION_PERSONAL_FILES).await
        {
            return (false, format!("Authorization failed: {error}"));
        }
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
        check_authorization(&hdr, connection, POLKIT_ACTION_PERSONAL_FILES)
            .await
            .map_err(|error| zbus::fdo::Error::AccessDenied(error.to_string()))?;
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
            Err(error) => return (false, format!("Invalid system snapshot ID: {error}")),
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
            Err(error) => return (false, format!("Invalid system snapshot ID: {error}")),
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

    /// Delete an unprotected immutable system snapshot.
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
            Err(error) => return (false, format!("Invalid system snapshot ID: {error}")),
        };
        match OperationEngine::default().delete(&layout::inspect_current(), id) {
            Ok(()) => {
                audit::log_snapshot_delete(uid, pid, &deployment_id, true, None);
                (true, "System snapshot deleted".into())
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

    /// Delete multiple unprotected system snapshots under one explicit
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
            return (false, "No system snapshots were selected".into());
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
            Err(error) => return (false, format!("Invalid system snapshot ID: {error}")),
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
            (true, "System snapshots deleted".into())
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
                return (false, format!("Invalid system snapshot ID: {error}"));
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
            Err(error) => return (false, format!("Invalid system snapshot ID: {error}")),
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
            Err(error) => return (false, format!("Invalid system snapshot ID: {error}")),
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

    /// Retry the identity-checked userspace reconciliation for an applied or terminal restore.
    async fn reconcile_deployment_restore(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
    ) -> (bool, String) {
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_RESTORE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_RESTORE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        match ConfirmationEngine::default().reconcile() {
            Ok(outcome) => {
                let message = format!("Recovery reconciliation completed: {outcome:?}");
                audit::log_operation(uid, pid, "reconcile_restore", "pending-restore", true, None);
                (true, message)
            }
            Err(error) => {
                audit::log_operation(
                    uid,
                    pid,
                    "reconcile_restore",
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

    /// Verify snapshot integrity
    async fn verify_snapshot(&self, name: String) -> String {
        // Verification is read-only, no authorization needed
        let id = match name.parse::<DeploymentId>() {
            Ok(id) => id,
            Err(error) => {
                return serde_json::json!({
                    "is_valid": false,
                    "errors": [format!("Invalid system snapshot ID: {error}")],
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

    /// Return a bounded, read-only summary of the Btrfs filesystem and live
    /// maintenance state. Managed task details exist only in this process and
    /// are never persisted by the helper or GUI.
    async fn get_btrfs_filesystem_status(&self) -> String {
        match tokio::task::spawn_blocking(btrfs::filesystem_status).await {
            Ok(Ok(status)) => serde_json::to_string(&status).unwrap_or_else(|error| {
                serde_json::json!({
                    "schema_version": 4,
                    "available": false,
                    "error": format!("Could not serialize Btrfs status: {error}"),
                })
                .to_string()
            }),
            Ok(Err(error)) => serde_json::json!({
                "schema_version": 4,
                "available": false,
                "error": error.to_string(),
            })
            .to_string(),
            Err(error) => serde_json::json!({
                "schema_version": 4,
                "available": false,
                "error": format!("Btrfs status query stopped: {error}"),
            })
            .to_string(),
        }
    }

    /// Return a bounded read-only summary of the important S.M.A.R.T. fields
    /// for physical devices backing the root Btrfs filesystem.
    async fn get_smart_disk_health(&self) -> String {
        // Holding this asynchronous mutex across the query provides single-flight
        // behavior without consuming blocking worker threads for duplicate calls.
        let mut cache = self.smart_query.lock().await;
        if let Some(value) = cache.current(std::time::Instant::now()) {
            return value;
        }
        let value = match tokio::task::spawn_blocking(smart::disk_health).await {
            Ok(Ok(status)) => serde_json::to_string(&status).unwrap_or_else(|error| {
                serde_json::json!({
                    "schema_version": 2,
                    "available": false,
                    "devices": [],
                    "error": format!("Could not serialize S.M.A.R.T. status: {error}"),
                })
                .to_string()
            }),
            Ok(Err(error)) => serde_json::json!({
                "schema_version": 2,
                "available": false,
                "devices": [],
                "error": error.to_string(),
            })
            .to_string(),
            Err(error) => serde_json::json!({
                "schema_version": 2,
                "available": false,
                "devices": [],
                "error": format!("S.M.A.R.T. status query stopped: {error}"),
            })
            .to_string(),
        };
        cache.store(value.clone(), std::time::Instant::now());
        value
    }

    /// Run one fixed Btrfs configuration or maintenance action. The action is
    /// an enum-like token; no caller-provided path or command argument reaches
    /// the privileged process.
    async fn run_btrfs_maintenance_action(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
        action: String,
    ) -> (bool, String) {
        let layout_report = layout::inspect_current();
        if !layout_report.is_supported() {
            return (
                false,
                format!(
                    "Btrfs maintenance requires the standard AnduinOS layout: {}",
                    layout_report.issues.join("; ")
                ),
            );
        }
        let (uid, pid) = Self::get_caller_info(&hdr, connection).await;
        if let Err(error) = check_authorization(&hdr, connection, POLKIT_ACTION_CONFIGURE).await {
            audit::log_auth_failure(uid, pid, POLKIT_ACTION_CONFIGURE, &error.to_string());
            return (false, format!("Authorization failed: {error}"));
        }
        let operation = match action.as_str() {
            "quota-enable" => btrfs::set_quota_enabled(true),
            "quota-disable" => btrfs::set_quota_enabled(false),
            "scrub-start" => btrfs::start_scrub(),
            "scrub-cancel" => btrfs::cancel_scrub(),
            "balance-start" => btrfs::start_filtered_balance(),
            "balance-cancel" => btrfs::cancel_balance(),
            "defrag-home" => btrfs::start_defragment_home(),
            "defrag-home-cancel" => btrfs::cancel_defragment_home(),
            _ => return (false, "Unknown Btrfs maintenance action".into()),
        };
        let response = match operation {
            Ok(message) => (true, message),
            Err(error) => (false, error.to_string()),
        };
        audit::log_operation(
            uid,
            pid,
            "btrfs_maintenance",
            &action,
            response.0,
            (!response.0).then_some(response.1.as_str()),
        );
        response
    }

    async fn get_apt_snapshot_policy(&self) -> (bool, bool) {
        let config = SnapshotsManagerConfig::new();
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
        let path = SnapshotsManagerConfig::new().apt_snapshot_policy;
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
        let path = SnapshotsManagerConfig::new().automation_config;
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
        match config.save_to_file(&SnapshotsManagerConfig::new().automation_config) {
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
            &[
                "enable",
                "--now",
                "anduinos-btrfs-snapshots-manager-scheduler.timer",
            ],
        ) {
            Ok(()) => {
                if let Err(error) = run_command(
                    "/usr/bin/systemctl",
                    &[
                        "start",
                        "--no-block",
                        "anduinos-btrfs-snapshots-manager-scheduler.service",
                    ],
                ) {
                    log::warn!("Could not start an immediate automation check: {error}");
                }
                audit::log_operation(
                    uid,
                    pid,
                    "apply_scheduler_state",
                    "anduinos-btrfs-snapshots-manager-scheduler.timer",
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
                    "anduinos-btrfs-snapshots-manager-scheduler.timer",
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
            &[
                "is-enabled",
                "anduinos-btrfs-snapshots-manager-scheduler.timer",
            ],
        )
        .map(|(stdout, _)| stdout.trim() == "enabled")
        .unwrap_or(false);
        if !enabled {
            return "disabled".to_string();
        }

        let active = run_command_with_output(
            "/usr/bin/systemctl",
            &[
                "is-active",
                "anduinos-btrfs-snapshots-manager-scheduler.timer",
            ],
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
                "anduinos-btrfs-snapshots-manager-scheduler.timer",
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

impl SnapshotsManagerHelper {
    #[cfg(test)]
    fn apt_history_impl(path: &std::path::Path) -> Result<String> {
        let contents = Self::read_apt_history_file(path, false)?;
        serde_json::to_string(&snapshots_manager_common::apt_history::parse_apt_history(
            &contents,
        ))
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

        let mut report = snapshots_manager_common::apt_history::AptHistoryReport::default();
        for (_, compressed, path, name) in files {
            match Self::read_apt_history_file(&path, compressed) {
                Ok(contents) => {
                    let mut parsed =
                        snapshots_manager_common::apt_history::parse_apt_history(&contents);
                    for issue in &mut parsed.issues {
                        issue.message = format!("{name}: {}", issue.message);
                    }
                    report.transactions.append(&mut parsed.transactions);
                    report.issues.append(&mut parsed.issues);
                }
                Err(error) => {
                    report
                        .issues
                        .push(snapshots_manager_common::apt_history::AptHistoryIssue {
                            block: 0,
                            message: format!("{name}: {error}"),
                        })
                }
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
        let pending_root_cleanups =
            anduinos_recovery_engine::cleanup::RootCleanupStore::new(store_root)
                .list()
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
        let system_sizes = btrfs::get_system_spaces(store_root, &deployments.deployments);
        let personal_sizes = btrfs::get_personal_spaces(store_root, &personal.snapshots);
        let layout = layout::inspect_current();
        let available = layout.is_supported();
        serde_json::to_string(&serde_json::json!({
            "schema_version": 1,
            "available": available,
            "store_root": store_root,
            "pending": pending,
            "pending_root_cleanups": pending_root_cleanups,
            "deployment_count": deployments.deployments.len(),
            "deployments": deployments.deployments,
            "system_package_counts": package_counts,
            "system_sizes": system_sizes,
            "personal_snapshot_count": personal.snapshots.len(),
            "personal_snapshots": personal.snapshots,
            "personal_sizes": personal_sizes,
            "issues": deployments.issues,
            "personal_issues": personal.issues,
            "layout": layout,
        }))
        .context("Failed to serialize recovery engine status")
    }

    fn public_recovery_engine_status_impl(store_root: &std::path::Path) -> Result<String> {
        let full = Self::recovery_engine_status_impl(store_root)?;
        let mut status: serde_json::Value =
            serde_json::from_str(&full).context("Failed to parse recovery engine status")?;
        Self::redact_public_recovery_status(&mut status)?;
        serde_json::to_string(&status).context("Failed to serialize public recovery engine status")
    }

    fn redact_public_recovery_status(status: &mut serde_json::Value) -> Result<()> {
        let object = status
            .as_object_mut()
            .context("Recovery engine status is not an object")?;

        // These fields expose system-wide recovery history or internal layout
        // details and are not needed to browse the caller's own Personal Files.
        object.insert("pending".into(), serde_json::Value::Null);
        object.insert("pending_root_cleanups".into(), serde_json::json!([]));
        object.insert("deployment_count".into(), serde_json::json!(0));
        object.insert("deployments".into(), serde_json::json!([]));
        object.insert("system_package_counts".into(), serde_json::json!({}));
        object.insert("system_sizes".into(), serde_json::json!({}));
        object.insert("issues".into(), serde_json::json!([]));
        object.insert("personal_issues".into(), serde_json::json!([]));
        object.insert("personal_sizes".into(), serde_json::json!({}));
        object.insert("layout".into(), serde_json::json!({}));
        object.remove("store_root");

        // Personal snapshots cover the shared @home subvolume, so their IDs
        // and timestamps must remain visible for per-caller file browsing. Do
        // not disclose labels, filesystem identities, or failure diagnostics
        // supplied by another desktop user.
        if let Some(snapshots) = object
            .get_mut("personal_snapshots")
            .and_then(serde_json::Value::as_array_mut)
        {
            for snapshot in snapshots {
                if let Some(snapshot) = snapshot.as_object_mut() {
                    snapshot.insert("title".into(), serde_json::json!("Home snapshot"));
                    snapshot.insert("reason".into(), serde_json::json!(""));
                    snapshot.remove("snapshot_uuid");
                    snapshot.remove("snapshot_parent_uuid");
                    snapshot.remove("failure");
                    snapshot.remove("schedule_id");
                }
            }
        }
        Ok(())
    }

    fn apply_schedule_retention_impl() -> Result<ScheduleRetentionSummary> {
        let layout = layout::inspect_current();
        if !layout.is_supported() {
            anyhow::bail!("The complete AnduinOS Btrfs layout is required");
        }
        let config = SnapshotsManagerConfig::default();
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
                        DeploymentKind::Factory | DeploymentKind::Imported
                    ) {
                    CleanupPolicy::KeepForever
                } else {
                    CleanupPolicy::Automatic
                },
                is_ready: record.state == DeploymentState::Ready,
                is_busy: false,
                // Active rollback references are enforced authoritatively by
                // OperationEngine at deletion time; they are not snapshot states.
                is_restore_referenced: false,
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
                    log::info!("Retention kept system snapshot {id}: {error}");
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
                    log::info!("Retention kept Home snapshot {id}: {error}");
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
    let path = SnapshotsManagerConfig::default().automation_config;
    automatic_success_notification_enabled_at(&path)
}

fn cleanup_success_notification_enabled() -> bool {
    AutomationConfig::load_from_file(&SnapshotsManagerConfig::new().automation_config)
        .map(|config| config.notifications.notify_after_cleanup)
        .unwrap_or(false)
}

fn automatic_pre_notification_enabled() -> bool {
    AutomationConfig::load_from_file(&SnapshotsManagerConfig::default().automation_config)
        .map(|config| config.notifications.notify_before_scheduled)
        .unwrap_or(NotificationPolicy::default().notify_before_scheduled)
}

fn automatic_success_notification_enabled_at(path: &std::path::Path) -> bool {
    match AutomationConfig::load_from_file(path) {
        Ok(config) => config.notifications.notify_after_success,
        Err(error) => {
            log::warn!(
                "Could not load Disk Snapshots Manager notification preference from {}: {error}",
                path.display()
            );
            NotificationPolicy::default().notify_after_success
        }
    }
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
        log::error!("anduinos-btrfs-snapshots-manager-helper must be run as root");
        std::process::exit(1);
    }

    log::info!(
        "Starting Disk Snapshots Manager Helper service v{}",
        env!("CARGO_PKG_VERSION")
    );

    // Build the D-Bus connection
    let helper = SnapshotsManagerHelper::new();
    let _connection = ConnectionBuilder::system()?
        .name(DBUS_SERVICE_NAME)?
        .serve_at(DBUS_OBJECT_PATH, helper)?
        .build()
        .await?;

    log::info!("Disk Snapshots Manager helper is ready at {DBUS_OBJECT_PATH}");

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
    fn smart_query_cache_reuses_only_fresh_results() {
        let mut cache = SmartQueryCache::default();
        let created_at = std::time::Instant::now();
        cache.store("first".into(), created_at);
        assert_eq!(
            cache.current(created_at + SMART_QUERY_CACHE_TTL / 2),
            Some("first".into())
        );
        assert_eq!(cache.current(created_at + SMART_QUERY_CACHE_TTL), None);
    }

    #[test]
    fn personal_snapshot_quota_allows_four_creations_per_sliding_minute() {
        let quota = PersonalSnapshotQuota::new(4, std::time::Duration::from_secs(60));
        let start = std::time::Instant::now();
        for offset in 0..4 {
            assert!(quota.reserve_at(start + std::time::Duration::from_secs(offset)));
        }
        assert!(!quota.reserve_at(start + std::time::Duration::from_secs(4)));
        assert!(quota.reserve_at(start + std::time::Duration::from_secs(60)));
    }

    #[test]
    fn automatic_success_notification_uses_snapshots_manager_v2_policy() {
        let path = std::env::temp_dir().join(format!(
            "anduinos-btrfs-snapshots-manager-notification-schedule-{}-{}.toml",
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
            "anduinos-btrfs-snapshots-manager-engine-status-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&root).unwrap();

        let status = SnapshotsManagerHelper::recovery_engine_status_impl(&root).unwrap();
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
    fn public_recovery_status_omits_privileged_metadata() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-btrfs-snapshots-manager-public-status-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&root).unwrap();

        let status = SnapshotsManagerHelper::public_recovery_engine_status_impl(&root).unwrap();
        let value: serde_json::Value = serde_json::from_str(&status).unwrap();
        assert_eq!(value["deployments"], serde_json::json!([]));
        assert!(value["pending"].is_null());
        assert_eq!(value["system_package_counts"], serde_json::json!({}));
        assert_eq!(value["system_sizes"], serde_json::json!({}));
        assert_eq!(value["layout"], serde_json::json!({}));
        assert!(value.get("store_root").is_none());

        std::fs::remove_dir_all(root).unwrap();

        let mut populated = serde_json::json!({
            "store_root": "/.snapshots/private",
            "pending": {"target_deployment_id": "secret"},
            "deployment_count": 1,
            "deployments": [{"title": "secret system title"}],
            "system_package_counts": {"secret": 42},
            "system_sizes": {"secret": {"exclusive_bytes": 1}},
            "issues": [{"message": "secret path"}],
            "personal_issues": [{"message": "secret failure"}],
            "personal_sizes": {"personal": {"exclusive_bytes": 1}},
            "layout": {"root_source": "/dev/secret"},
            "personal_snapshots": [{
                "id": "personal",
                "title": "private title",
                "reason": "private reason",
                "snapshot_uuid": "private-uuid",
                "snapshot_parent_uuid": "private-parent",
                "failure": "private failure",
                "schedule_id": "private-schedule"
            }]
        });
        SnapshotsManagerHelper::redact_public_recovery_status(&mut populated).unwrap();
        assert_eq!(populated["personal_snapshots"][0]["title"], "Home snapshot");
        assert_eq!(populated["personal_snapshots"][0]["reason"], "");
        assert!(
            populated["personal_snapshots"][0]
                .get("snapshot_uuid")
                .is_none()
        );
        assert!(populated["personal_snapshots"][0].get("failure").is_none());
        assert_eq!(populated["deployments"], serde_json::json!([]));
        assert_eq!(populated["layout"], serde_json::json!({}));
        assert!(populated.get("store_root").is_none());
    }

    #[test]
    fn reads_apt_history_as_structured_json() {
        let path = std::env::temp_dir().join(format!(
            "anduinos-btrfs-snapshots-manager-apt-history-{}.log",
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

        let history = SnapshotsManagerHelper::apt_history_impl(&path).unwrap();
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
            "anduinos-btrfs-snapshots-manager-apt-history-dir-{}",
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

        let history = SnapshotsManagerHelper::apt_history_directory_impl(&directory).unwrap();
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
}
