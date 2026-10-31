//! Btrfs space measurements and their non-authoritative cache.
//!
//! Status queries read only this cache. An explicit Properties request first reads an existing
//! level-zero qgroup and falls back to a targeted `btrfs filesystem du` measurement when quota
//! accounting is off. Quotas are never enabled or synchronized here, and the cache is never used
//! for recovery or deletion decisions.

use anduinos_recovery_engine::{
    model::{DeploymentId, DeploymentRecord},
    operations::SystemCommandRunner,
    personal::{PersonalSnapshotEngine, PersonalSnapshotId, PersonalSnapshotRecord},
    space::parse_qgroup_for_subvolume,
    store::DeploymentStore,
};
use anyhow::{Context, Result, bail};
use chrono::Utc;
use snapshots_manager_common::SnapshotSpace;
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{
    LazyLock, Mutex,
    atomic::{AtomicBool, AtomicU64, Ordering},
};
use std::thread;
use std::time::Instant;

use crate::systemd_worker;

const BTRFS: &str = "/usr/bin/btrfs";
const MAX_BTRFS_OUTPUT_BYTES: usize = 16 * 1024 * 1024;
const MAX_CACHE_BYTES: u64 = 64 * 1024;
static MEASUREMENT_LOCK: Mutex<()> = Mutex::new(());
static SCRUB_TASK_RUNNING: AtomicBool = AtomicBool::new(false);
static SCRUB_GENERATION: AtomicU64 = AtomicU64::new(0);
static SCRUB_TASK: LazyLock<Mutex<ScrubTaskState>> =
    LazyLock::new(|| Mutex::new(ScrubTaskState::default()));
static BALANCE_TASK_RUNNING: AtomicBool = AtomicBool::new(false);
static BALANCE_GENERATION: AtomicU64 = AtomicU64::new(0);
static BALANCE_CANCEL_GENERATION: AtomicU64 = AtomicU64::new(0);
static BALANCE_TASK: LazyLock<Mutex<ManagedTaskState>> =
    LazyLock::new(|| Mutex::new(ManagedTaskState::default()));
static DEFRAG_TASK_RUNNING: AtomicBool = AtomicBool::new(false);
static DEFRAG_GENERATION: AtomicU64 = AtomicU64::new(0);
static DEFRAG_ITEMS_PROCESSED: AtomicU64 = AtomicU64::new(0);
static DEFRAG_TASK: LazyLock<Mutex<ManagedTaskState>> =
    LazyLock::new(|| Mutex::new(ManagedTaskState::default()));

const ROOT_MOUNT: &str = "/";

#[derive(Debug, serde::Serialize)]
pub struct FilesystemStatus {
    pub schema_version: u32,
    pub available: bool,
    pub source: String,
    pub total_bytes: Option<u64>,
    pub used_bytes: Option<u64>,
    pub data_profile: String,
    pub metadata_profile: String,
    pub compression: String,
    pub discard: String,
    pub quota: String,
    pub scrub: String,
    pub scrub_details: ScrubDetails,
    pub balance: String,
    pub balance_details: BalanceDetails,
    pub defrag: String,
    pub defrag_details: DefragDetails,
}

#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct ScrubDetails {
    pub generation: u64,
    pub elapsed_seconds: Option<u64>,
    pub started_at: Option<String>,
    pub duration: Option<String>,
    pub time_left: Option<String>,
    pub total_bytes: Option<u64>,
    pub bytes_scrubbed: Option<u64>,
    pub rate_bytes_per_second: Option<u64>,
    pub read_errors: u64,
    pub checksum_errors: u64,
    pub verify_errors: u64,
    pub superblock_errors: u64,
    pub uncorrectable_errors: u64,
    pub unverified_errors: u64,
    pub corrected_errors: u64,
    pub error: Option<String>,
}

#[derive(Debug, Default, serde::Serialize)]
pub struct BalanceDetails {
    pub generation: u64,
    pub elapsed_seconds: Option<u64>,
    pub chunks_balanced: Option<u64>,
    pub chunks_total: Option<u64>,
    pub chunks_considered: Option<u64>,
    pub percent_remaining: Option<u64>,
    pub error: Option<String>,
}

#[derive(Debug, Default, serde::Serialize)]
pub struct DefragDetails {
    pub generation: u64,
    pub elapsed_seconds: Option<u64>,
    pub items_processed: u64,
    pub error: Option<String>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum TaskPhase {
    #[default]
    Idle,
    Starting,
    Running,
    Cancelling,
    Finished,
    Cancelled,
    Failed,
}

impl TaskPhase {
    const fn as_wire_name(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Starting => "starting",
            Self::Running => "running",
            Self::Cancelling => "cancelling",
            Self::Finished => "finished",
            Self::Cancelled => "cancelled",
            Self::Failed => "failed",
        }
    }

    const fn is_active(self) -> bool {
        matches!(self, Self::Starting | Self::Running | Self::Cancelling)
    }
}

#[derive(Debug, Default)]
struct ManagedTaskState {
    generation: u64,
    phase: TaskPhase,
    started: Option<Instant>,
    elapsed_seconds: Option<u64>,
    first_count: Option<u64>,
    total_count: Option<u64>,
    considered_count: Option<u64>,
    percent_remaining: Option<u64>,
    error: Option<String>,
    pid: Option<u32>,
    cancel_requested: bool,
}

#[derive(Debug, Default)]
struct ScrubTaskState {
    generation: u64,
    phase: TaskPhase,
    started: Option<Instant>,
    elapsed_seconds: Option<u64>,
    details: ScrubDetails,
    error: Option<String>,
    cancel_requested: bool,
}

#[derive(Debug)]
struct ScrubRunResult {
    phase: TaskPhase,
    details: ScrubDetails,
    error: Option<String>,
}

pub fn filesystem_status() -> Result<FilesystemStatus> {
    let (source, mount_options) = root_btrfs_mount()?;
    let usage = run_btrfs(&[
        std::ffi::OsStr::new("filesystem"),
        std::ffi::OsStr::new("usage"),
        std::ffi::OsStr::new("--raw"),
        std::ffi::OsStr::new(ROOT_MOUNT),
    ])?;
    let quota = run_btrfs_allow_failure(&["quota", "status", ROOT_MOUNT]);
    // The summary provides aggregate byte progress while -R provides the
    // individual error counters needed for a useful diagnostic result.
    let scrub_summary = run_btrfs_allow_failure(&["scrub", "status", "--raw", ROOT_MOUNT]);
    let scrub_raw = run_btrfs_allow_failure(&["scrub", "status", "-R", ROOT_MOUNT]);
    let balance = run_btrfs_allow_failure(&["balance", "status", ROOT_MOUNT]);
    let native_scrub_details = parse_scrub_details(&scrub_summary.stdout, &scrub_raw.stdout);
    let native_scrub = scrub_status(
        &scrub_summary.stdout,
        &scrub_summary.stderr,
        scrub_summary.success,
        scrub_counters_complete(&scrub_raw.stdout),
        &native_scrub_details,
    );
    let (scrub, scrub_details) = scrub_task_status(&native_scrub, native_scrub_details);
    let native_balance_status = balance_status(&balance.stdout, &balance.stderr, balance.success);
    let native_balance_progress = parse_balance_progress(&balance.stdout);
    let (balance, balance_details) =
        balance_task_status(&native_balance_status, native_balance_progress);
    let (defrag, defrag_details) = defrag_task_status();
    Ok(FilesystemStatus {
        schema_version: 4,
        available: true,
        source,
        total_bytes: usage_value(&usage, "Device size:"),
        used_bytes: usage_value(&usage, "Used:"),
        data_profile: block_group_profile(&usage, "Data,").unwrap_or_else(|| "unknown".into()),
        metadata_profile: block_group_profile(&usage, "Metadata,")
            .unwrap_or_else(|| "unknown".into()),
        compression: compression_option(&mount_options),
        discard: mount_options
            .iter()
            .find(|option| option.starts_with("discard"))
            .cloned()
            .unwrap_or_else(|| "off".into()),
        quota: quota_status(&quota.stdout, quota.success),
        scrub,
        scrub_details,
        balance,
        balance_details,
        defrag,
        defrag_details,
    })
}

pub fn set_quota_enabled(enabled: bool) -> Result<String> {
    if enabled {
        run_btrfs_mutating(&["quota", "enable", ROOT_MOUNT])?;
        Ok("Btrfs quota accounting is enabled and its initial scan has started".into())
    } else {
        run_btrfs_mutating(&["quota", "disable", ROOT_MOUNT])?;
        Ok("Btrfs quota accounting is disabled".into())
    }
}

pub fn start_scrub() -> Result<String> {
    if SCRUB_TASK_RUNNING
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        bail!("An integrity check is already running");
    }

    let generation = SCRUB_GENERATION
        .fetch_add(1, Ordering::AcqRel)
        .saturating_add(1);
    {
        let mut task = SCRUB_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        *task = ScrubTaskState {
            generation,
            phase: TaskPhase::Starting,
            started: Some(Instant::now()),
            details: ScrubDetails {
                generation,
                ..ScrubDetails::default()
            },
            ..ScrubTaskState::default()
        };
    }

    if let Err(error) = thread::Builder::new()
        .name("btrfs-scrub".into())
        .spawn(move || run_scrub_task(generation))
    {
        SCRUB_TASK_RUNNING.store(false, Ordering::Release);
        let mut task = SCRUB_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        task.phase = TaskPhase::Failed;
        task.error = Some(format!("Failed to start the integrity check task: {error}"));
        bail!("Failed to start the integrity check task: {error}");
    }

    Ok("The integrity check has started".into())
}

fn run_scrub_task(generation: u64) {
    {
        let mut task = SCRUB_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        if task.generation != generation {
            SCRUB_TASK_RUNNING.store(false, Ordering::Release);
            return;
        }
        task.phase = if task.cancel_requested {
            TaskPhase::Cancelling
        } else {
            TaskPhase::Running
        };
    }

    let result = run_scrub_foreground();
    let mut task = SCRUB_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
    if task.generation != generation {
        SCRUB_TASK_RUNNING.store(false, Ordering::Release);
        return;
    }
    task.elapsed_seconds = task.started.map(|started| started.elapsed().as_secs());
    task.details = result.details;
    task.details.generation = generation;
    task.details.elapsed_seconds = task.elapsed_seconds;
    task.phase = if task.cancel_requested {
        TaskPhase::Cancelled
    } else {
        result.phase
    };
    task.details.error.clone_from(&result.error);
    task.error = result.error;
    match task.phase {
        TaskPhase::Finished => log::info!("Btrfs integrity check finished"),
        TaskPhase::Cancelled => log::info!("Btrfs integrity check was cancelled"),
        TaskPhase::Failed => log::error!(
            "Btrfs integrity check failed: {}",
            task.error.as_deref().unwrap_or("unknown error")
        ),
        _ => {}
    }
    SCRUB_TASK_RUNNING.store(false, Ordering::Release);
}

fn run_scrub_foreground() -> ScrubRunResult {
    // Keep btrfs-progs attached to the privileged helper for the whole run.
    // A background scrub launched from the hardened systemd service can lose
    // its userspace monitor as soon as the launcher exits and be marked
    // aborted before reading any extents. `-B` makes this D-Bus operation
    // complete only after this exact scrub has reached a terminal state. The
    // read-only scrub mode verifies every allocated extent without requiring
    // write access to the system root inside the hardened service namespace.
    let result = run_btrfs_allow_failure(&["scrub", "start", "-B", "-R", "-r", "-f", ROOT_MOUNT]);
    scrub_run_result(result)
}

pub fn cancel_scrub() -> Result<String> {
    let previous_phase = {
        let mut task = SCRUB_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        if !task.phase.is_active() {
            bail!("The integrity check is not running");
        }
        let previous_phase = task.phase;
        task.cancel_requested = true;
        task.phase = TaskPhase::Cancelling;
        previous_phase
    };
    if let Err(error) = run_btrfs_mutating(&["scrub", "cancel", ROOT_MOUNT]) {
        let mut task = SCRUB_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        task.cancel_requested = false;
        task.phase = previous_phase;
        return Err(error);
    }
    Ok("The integrity check was cancelled".into())
}

pub fn start_filtered_balance() -> Result<String> {
    if BALANCE_TASK_RUNNING
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        bail!("A space rebalance is already running");
    }

    let generation = BALANCE_GENERATION
        .fetch_add(1, Ordering::AcqRel)
        .saturating_add(1);
    {
        let mut task = BALANCE_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        *task = ManagedTaskState {
            generation,
            phase: TaskPhase::Starting,
            started: Some(Instant::now()),
            ..ManagedTaskState::default()
        };
    }

    if let Err(error) = thread::Builder::new()
        .name("btrfs-balance".into())
        .spawn(move || run_balance_task(generation))
    {
        BALANCE_TASK_RUNNING.store(false, Ordering::Release);
        let mut task = BALANCE_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        task.phase = TaskPhase::Failed;
        task.error = Some(format!("Failed to start the space rebalance task: {error}"));
        bail!("Failed to start the space rebalance task: {error}");
    }

    Ok("The limited space rebalance has started".into())
}

pub fn cancel_balance() -> Result<String> {
    let was_starting = {
        let mut task = BALANCE_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        let was_starting = task.phase == TaskPhase::Starting;
        task.cancel_requested = true;
        if matches!(task.phase, TaskPhase::Starting | TaskPhase::Running) {
            task.phase = TaskPhase::Cancelling;
        }
        was_starting
    };
    let cancel_generation = BALANCE_CANCEL_GENERATION
        .fetch_add(1, Ordering::AcqRel)
        .saturating_add(1);
    let result = systemd_worker::run_btrfs(
        &format!("anduinos-btrfs-balance-cancel-{cancel_generation}"),
        &["balance", "cancel", ROOT_MOUNT],
    );
    let combined = format!("{}\n{}", result.stdout, result.stderr).to_ascii_lowercase();
    if !result.success
        && !(was_starting
            && (combined.contains("no balance found") || combined.contains("not in progress")))
    {
        bail!("Btrfs operation failed: {}", result.stderr.trim());
    }
    Ok("The space rebalance was cancelled".into())
}

fn run_balance_task(generation: u64) {
    {
        let mut task = BALANCE_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        if task.generation != generation {
            BALANCE_TASK_RUNNING.store(false, Ordering::Release);
            return;
        }
        if task.cancel_requested {
            task.phase = TaskPhase::Cancelled;
            task.elapsed_seconds = task.started.map(|started| started.elapsed().as_secs());
            BALANCE_TASK_RUNNING.store(false, Ordering::Release);
            return;
        }
        task.phase = TaskPhase::Running;
    }
    // The main helper intentionally runs with ProtectSystem=strict. A Btrfs
    // balance needs a writable view of the root mount even though it only
    // issues fixed ioctls. Run that one fixed command in a short-lived,
    // separately sandboxed systemd unit instead of weakening the broad D-Bus
    // helper's filesystem protection.
    let result = systemd_worker::run_btrfs(
        &format!("anduinos-btrfs-balance-{generation}"),
        &["balance", "start", "-dusage=50", "-musage=50", ROOT_MOUNT],
    );

    let mut task = BALANCE_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
    if task.generation != generation {
        BALANCE_TASK_RUNNING.store(false, Ordering::Release);
        return;
    }
    task.elapsed_seconds = task.started.map(|started| started.elapsed().as_secs());
    let cancelled = task.cancel_requested;
    if let Some(progress) = parse_balance_completion(&result.stdout) {
        task.first_count = progress.chunks_balanced;
        task.total_count = progress.chunks_total;
        task.percent_remaining = Some(0);
    }
    if cancelled {
        task.phase = TaskPhase::Cancelled;
    } else if result.success {
        task.phase = TaskPhase::Finished;
    } else {
        task.phase = TaskPhase::Failed;
        task.error = Some(format_btrfs_error(&result.stderr));
    }
    BALANCE_TASK_RUNNING.store(false, Ordering::Release);
}

pub fn start_defragment_home() -> Result<String> {
    if DEFRAG_TASK_RUNNING
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        bail!("Home file defragmentation is already running");
    }

    DEFRAG_ITEMS_PROCESSED.store(0, Ordering::Release);
    let generation = DEFRAG_GENERATION
        .fetch_add(1, Ordering::AcqRel)
        .saturating_add(1);
    {
        let mut task = DEFRAG_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        *task = ManagedTaskState {
            generation,
            phase: TaskPhase::Starting,
            started: Some(Instant::now()),
            ..ManagedTaskState::default()
        };
    }

    if let Err(error) = thread::Builder::new()
        .name("btrfs-defrag-home".into())
        .spawn(move || run_defrag_task(generation))
    {
        DEFRAG_TASK_RUNNING.store(false, Ordering::Release);
        let mut task = DEFRAG_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        task.phase = TaskPhase::Failed;
        task.error = Some(format!(
            "Failed to start Home file defragmentation: {error}"
        ));
        bail!("Failed to start Home file defragmentation: {error}");
    }

    Ok("Home file defragmentation has started".into())
}

pub fn cancel_defragment_home() -> Result<String> {
    let pid = {
        let mut task = DEFRAG_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        if !task.phase.is_active() {
            bail!("Home file defragmentation is not running");
        }
        task.cancel_requested = true;
        task.phase = TaskPhase::Cancelling;
        task.pid
    };

    if let Some(pid) = pid {
        // The PID comes only from the child process spawned by this helper and
        // is never accepted from D-Bus callers.
        let result = unsafe { libc::kill(pid as i32, libc::SIGTERM) };
        if result != 0 {
            bail!(
                "Could not cancel Home file defragmentation: {}",
                std::io::Error::last_os_error()
            );
        }
    }
    Ok("Home file defragmentation is being cancelled".into())
}

fn run_defrag_task(generation: u64) {
    let mut child = match Command::new(BTRFS)
        .args(["-v", "filesystem", "defragment", "-r", "-czstd", "/home"])
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(error) => {
            finish_defrag_task(
                generation,
                TaskPhase::Failed,
                Some(format!("Could not start Btrfs defragmentation: {error}")),
            );
            return;
        }
    };

    let cancel_immediately = {
        let mut task = DEFRAG_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
        if task.generation == generation {
            task.phase = if task.cancel_requested {
                TaskPhase::Cancelling
            } else {
                TaskPhase::Running
            };
            task.pid = Some(child.id());
        }
        task.cancel_requested
    };
    if cancel_immediately {
        // Cancellation can arrive while the child is still being spawned.
        // Honor it before beginning the wait so a fast click never gets lost.
        let _ = unsafe { libc::kill(child.id() as i32, libc::SIGTERM) };
    }

    let stdout_reader = child.stdout.take().map(|stdout| {
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if !line.trim().is_empty() {
                    DEFRAG_ITEMS_PROCESSED.fetch_add(1, Ordering::AcqRel);
                }
            }
        })
    });
    let stderr_reader = child.stderr.take().map(|stderr| {
        thread::spawn(move || {
            let mut message = String::new();
            let _ = stderr
                .take(MAX_BTRFS_OUTPUT_BYTES as u64)
                .read_to_string(&mut message);
            message
        })
    });

    let exit_status = child.wait();
    if let Some(reader) = stdout_reader {
        let _ = reader.join();
    }
    let stderr = stderr_reader
        .and_then(|reader| reader.join().ok())
        .unwrap_or_default();
    let cancelled = DEFRAG_TASK
        .lock()
        .unwrap_or_else(|lock| lock.into_inner())
        .cancel_requested;
    match exit_status {
        Ok(_) if cancelled => finish_defrag_task(generation, TaskPhase::Cancelled, None),
        Ok(status) if status.success() => finish_defrag_task(generation, TaskPhase::Finished, None),
        Ok(_) => finish_defrag_task(
            generation,
            TaskPhase::Failed,
            Some(format_btrfs_error(&stderr)),
        ),
        Err(error) => finish_defrag_task(
            generation,
            TaskPhase::Failed,
            Some(format!("Could not wait for Btrfs defragmentation: {error}")),
        ),
    }
}

fn finish_defrag_task(generation: u64, phase: TaskPhase, error: Option<String>) {
    let mut task = DEFRAG_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
    if task.generation == generation {
        task.phase = phase;
        task.elapsed_seconds = task.started.map(|started| started.elapsed().as_secs());
        task.first_count = Some(DEFRAG_ITEMS_PROCESSED.load(Ordering::Acquire));
        task.error = error;
        task.pid = None;
    }
    DEFRAG_TASK_RUNNING.store(false, Ordering::Release);
}

struct CommandResult {
    success: bool,
    stdout: String,
    stderr: String,
}

fn root_btrfs_mount() -> Result<(String, Vec<String>)> {
    let mountinfo = fs::read_to_string("/proc/self/mountinfo")?;
    parse_root_btrfs_mount(&mountinfo)
}

fn parse_root_btrfs_mount(mountinfo: &str) -> Result<(String, Vec<String>)> {
    for line in mountinfo.lines() {
        let Some((left, right)) = line.split_once(" - ") else {
            continue;
        };
        let left_fields = left.split_whitespace().collect::<Vec<_>>();
        let right_fields = right.split_whitespace().collect::<Vec<_>>();
        if left_fields.get(4) == Some(&ROOT_MOUNT) && right_fields.first() == Some(&"btrfs") {
            let source = right_fields
                .get(1)
                .copied()
                .unwrap_or("unknown")
                .to_string();
            let options = left_fields
                .get(5)
                .into_iter()
                .chain(right_fields.get(2))
                .flat_map(|value| value.split(','))
                .map(str::to_string)
                .collect();
            return Ok((source, options));
        }
    }
    bail!("The system root is not mounted from Btrfs")
}

fn usage_value(output: &str, label: &str) -> Option<u64> {
    output.lines().find_map(|line| {
        let value = line.trim().strip_prefix(label)?.trim();
        value.split_whitespace().next()?.parse().ok()
    })
}

fn block_group_profile(output: &str, prefix: &str) -> Option<String> {
    output.lines().find_map(|line| {
        let rest = line.trim().strip_prefix(prefix)?;
        Some(rest.split(':').next()?.trim().to_string())
    })
}

fn mount_option(options: &[String], prefix: &str) -> Option<String> {
    options
        .iter()
        .find_map(|option| option.strip_prefix(prefix).map(str::to_string))
}

fn compression_option(options: &[String]) -> String {
    if let Some(compression) = mount_option(options, "compress-force=") {
        format!("{compression} (forced)")
    } else {
        mount_option(options, "compress=").unwrap_or_else(|| "off".into())
    }
}

fn quota_status(stdout: &str, success: bool) -> String {
    if !success {
        return "unavailable".into();
    }
    if stdout.lines().any(|line| line.trim() == "Enabled: yes") {
        if stdout.to_ascii_lowercase().contains("rescan") {
            "scanning".into()
        } else {
            "enabled".into()
        }
    } else {
        "disabled".into()
    }
}

fn scrub_status(
    stdout: &str,
    stderr: &str,
    success: bool,
    counters_complete: bool,
    details: &ScrubDetails,
) -> String {
    let combined = format!("{stdout}\n{stderr}").to_ascii_lowercase();
    if combined.contains("status: running")
        // btrfs-progs creates the progress pipe before its status file is
        // populated. During those first seconds it prints "no stats
        // available" together with live progress fields. Treat that as
        // running so the UI cannot mistake a newly started scrub for an old
        // completed run.
        || combined.contains("bytes scrubbed:")
        || combined.contains("time left:")
    {
        "running".into()
    } else if combined.contains("status: canceled")
        || combined.contains("status: cancelled")
        || combined.contains("status: aborted")
    {
        "cancelled".into()
    } else if combined.contains("no stats available") {
        "never-run".into()
    } else if success && combined.contains("status:") && combined.contains("finished") {
        if !counters_complete {
            return "unavailable".into();
        }
        scrub_completed_status(details)
    } else {
        "unavailable".into()
    }
}

fn scrub_task_status(native_status: &str, native_details: ScrubDetails) -> (String, ScrubDetails) {
    let task = SCRUB_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
    scrub_task_status_from(native_status, native_details, &task)
}

fn scrub_task_status_from(
    native_status: &str,
    mut native_details: ScrubDetails,
    task: &ScrubTaskState,
) -> (String, ScrubDetails) {
    if task.generation == 0 {
        return (native_status.into(), native_details);
    }

    let elapsed_seconds = task
        .elapsed_seconds
        .or_else(|| task.started.map(|started| started.elapsed().as_secs()));
    if task.phase.is_active() {
        native_details.generation = task.generation;
        native_details.elapsed_seconds = elapsed_seconds;
        native_details.error.clone_from(&task.error);
        return ("running".into(), native_details);
    }

    let mut details = task.details.clone();
    details.generation = task.generation;
    details.elapsed_seconds = elapsed_seconds;
    details.error.clone_from(&task.error);
    let status = match task.phase {
        TaskPhase::Finished => scrub_completed_status(&details),
        TaskPhase::Cancelled => "cancelled".into(),
        TaskPhase::Failed => "failed".into(),
        TaskPhase::Idle => native_status.into(),
        TaskPhase::Starting | TaskPhase::Running | TaskPhase::Cancelling => unreachable!(),
    };
    (status, details)
}

fn scrub_completed_status(details: &ScrubDetails) -> String {
    let errors = details.unrepaired_error_count();
    if errors > 0 {
        format!("finished-with-errors:{errors}")
    } else if details.corrected_errors > 0 {
        format!("finished-repaired:{}", details.corrected_errors)
    } else if details.detected_error_count() > 0 {
        format!("finished-with-errors:{}", details.detected_error_count())
    } else {
        "finished-clean".into()
    }
}

fn scrub_run_result(result: CommandResult) -> ScrubRunResult {
    let combined = format!("{}\n{}", result.stdout, result.stderr);
    let normalized = combined.to_ascii_lowercase();
    let mut details = parse_scrub_details(&result.stdout, &result.stdout);
    let cancelled = normalized.contains("status: canceled")
        || normalized.contains("status: cancelled")
        || normalized.contains("status: aborted");
    if cancelled {
        return ScrubRunResult {
            phase: TaskPhase::Cancelled,
            details,
            error: None,
        };
    }

    let finished = normalized.contains("status:") && normalized.contains("finished");
    let has_counters = scrub_counters_complete(&result.stdout);
    if result.success && finished && has_counters {
        return ScrubRunResult {
            phase: TaskPhase::Finished,
            details,
            error: None,
        };
    }

    let error = if result.success {
        "Btrfs exited without a complete terminal scrub result".into()
    } else {
        format_btrfs_error(&result.stderr)
    };
    details.error = Some(error.clone());
    ScrubRunResult {
        phase: TaskPhase::Failed,
        details,
        error: Some(error),
    }
}

fn scrub_counters_complete(output: &str) -> bool {
    [
        "data_bytes_scrubbed:",
        "tree_bytes_scrubbed:",
        "read_errors:",
        "csum_errors:",
        "verify_errors:",
        "super_errors:",
        "uncorrectable_errors:",
        "unverified_errors:",
        "corrected_errors:",
    ]
    .into_iter()
    .all(|label| metric(output, label).is_some())
}

impl ScrubDetails {
    fn detected_error_count(&self) -> u64 {
        self.read_errors
            .saturating_add(self.checksum_errors)
            .saturating_add(self.verify_errors)
            .saturating_add(self.superblock_errors)
    }

    fn unrepaired_error_count(&self) -> u64 {
        self.uncorrectable_errors
            .saturating_add(self.unverified_errors)
    }
}

fn parse_scrub_details(summary: &str, raw: &str) -> ScrubDetails {
    let data_bytes_scrubbed = metric(raw, "data_bytes_scrubbed:");
    let tree_bytes_scrubbed = metric(raw, "tree_bytes_scrubbed:");
    let raw_bytes_scrubbed = match (data_bytes_scrubbed, tree_bytes_scrubbed) {
        (None, None) => None,
        (data, tree) => data.unwrap_or(0).checked_add(tree.unwrap_or(0)),
    };
    ScrubDetails {
        started_at: text_metric(summary, "Scrub started:"),
        duration: text_metric(summary, "Duration:"),
        time_left: text_metric(summary, "Time left:"),
        total_bytes: metric(summary, "Total to scrub:").or(raw_bytes_scrubbed),
        bytes_scrubbed: metric(summary, "Bytes scrubbed:").or(raw_bytes_scrubbed),
        rate_bytes_per_second: metric(summary, "Rate:"),
        read_errors: metric(raw, "read_errors:").unwrap_or(0),
        checksum_errors: metric(raw, "csum_errors:").unwrap_or(0),
        verify_errors: metric(raw, "verify_errors:").unwrap_or(0),
        superblock_errors: metric(raw, "super_errors:").unwrap_or(0),
        uncorrectable_errors: metric(raw, "uncorrectable_errors:").unwrap_or(0),
        unverified_errors: metric(raw, "unverified_errors:").unwrap_or(0),
        corrected_errors: metric(raw, "corrected_errors:").unwrap_or(0),
        ..ScrubDetails::default()
    }
}

fn text_metric(output: &str, label: &str) -> Option<String> {
    output.lines().find_map(|line| {
        let value = line.trim().strip_prefix(label)?.trim();
        (!value.is_empty()).then(|| value.to_string())
    })
}

fn balance_status(stdout: &str, stderr: &str, success: bool) -> String {
    let combined = format!("{stdout}\n{stderr}").to_ascii_lowercase();
    if combined.contains("is running") {
        "running".into()
    } else if combined.contains("is paused") {
        "paused".into()
    } else if success
        || combined.contains("no balance found")
        || combined.contains("not in progress")
    {
        "idle".into()
    } else {
        "unavailable".into()
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct BalanceProgress {
    chunks_balanced: Option<u64>,
    chunks_total: Option<u64>,
    chunks_considered: Option<u64>,
    percent_remaining: Option<u64>,
}

fn parse_balance_progress(output: &str) -> BalanceProgress {
    let Some(line) = output
        .lines()
        .find(|line| line.contains(" chunks balanced"))
    else {
        return BalanceProgress::default();
    };
    let Some((balanced, rest)) = line.trim().split_once(" out of about ") else {
        return BalanceProgress::default();
    };
    let Some((total, suffix)) = rest.split_once(" chunks balanced") else {
        return BalanceProgress::default();
    };
    BalanceProgress {
        chunks_balanced: balanced.trim().parse().ok(),
        chunks_total: total.trim().parse().ok(),
        chunks_considered: suffix
            .split_once('(')
            .and_then(|(_, value)| value.split_once(" considered)"))
            .and_then(|(value, _)| value.trim().parse().ok()),
        percent_remaining: suffix
            .split_once("% left")
            .and_then(|(value, _)| value.split_whitespace().last())
            .and_then(|value| value.parse().ok()),
    }
}

fn parse_balance_completion(output: &str) -> Option<BalanceProgress> {
    let line = output.lines().find(|line| line.contains("relocate "))?;
    let (_, counts) = line.split_once("relocate ")?;
    let (balanced, total) = counts.split_once(" out of ")?;
    let total = total.split_whitespace().next()?;
    Some(BalanceProgress {
        chunks_balanced: balanced.trim().parse().ok(),
        chunks_total: total.trim().parse().ok(),
        percent_remaining: Some(0),
        ..BalanceProgress::default()
    })
}

fn balance_task_status(
    native_status: &str,
    native_progress: BalanceProgress,
) -> (String, BalanceDetails) {
    let task = BALANCE_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
    let elapsed_seconds = task
        .elapsed_seconds
        .or_else(|| task.started.map(|started| started.elapsed().as_secs()));
    let native_running = matches!(native_status, "running" | "paused");
    let managed_active = task.phase.is_active();
    let status = if native_running {
        if task.phase == TaskPhase::Cancelling {
            "cancelling".into()
        } else {
            native_status.into()
        }
    } else if managed_active || task.generation > 0 {
        task.phase.as_wire_name().into()
    } else {
        native_status.into()
    };
    let progress = if native_running {
        native_progress
    } else {
        BalanceProgress {
            chunks_balanced: task.first_count,
            chunks_total: task.total_count,
            chunks_considered: task.considered_count,
            percent_remaining: task.percent_remaining,
        }
    };
    (
        status,
        BalanceDetails {
            generation: task.generation,
            elapsed_seconds,
            chunks_balanced: progress.chunks_balanced,
            chunks_total: progress.chunks_total,
            chunks_considered: progress.chunks_considered,
            percent_remaining: progress.percent_remaining,
            error: task.error.clone(),
        },
    )
}

fn defrag_task_status() -> (String, DefragDetails) {
    let task = DEFRAG_TASK.lock().unwrap_or_else(|lock| lock.into_inner());
    let elapsed_seconds = task
        .elapsed_seconds
        .or_else(|| task.started.map(|started| started.elapsed().as_secs()));
    (
        task.phase.as_wire_name().into(),
        DefragDetails {
            generation: task.generation,
            elapsed_seconds,
            items_processed: if task.phase.is_active() {
                DEFRAG_ITEMS_PROCESSED.load(Ordering::Acquire)
            } else {
                task.first_count.unwrap_or(0)
            },
            error: task.error.clone(),
        },
    )
}

fn format_btrfs_error(stderr: &str) -> String {
    let message = stderr.trim().trim_start_matches("ERROR: ").trim();
    if message.is_empty() {
        "Btrfs did not provide an error message".into()
    } else {
        message.into()
    }
}

fn metric(output: &str, label: &str) -> Option<u64> {
    output.lines().find_map(|line| {
        line.trim()
            .strip_prefix(label)?
            .split_whitespace()
            .next()?
            .trim_end_matches("/s")
            .parse()
            .ok()
    })
}

fn run_btrfs_allow_failure(arguments: &[&str]) -> CommandResult {
    match Command::new(BTRFS)
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()
    {
        Ok(output) => CommandResult {
            success: output.status.success(),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        },
        Err(error) => CommandResult {
            success: false,
            stdout: String::new(),
            stderr: error.to_string(),
        },
    }
}

fn run_btrfs_mutating(arguments: &[&str]) -> Result<()> {
    let result = run_btrfs_allow_failure(arguments);
    if result.success {
        Ok(())
    } else {
        bail!("Btrfs operation failed: {}", result.stderr.trim())
    }
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct VerificationResult {
    pub is_valid: bool,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}

pub fn get_system_spaces(
    store_root: &Path,
    snapshots: &[DeploymentRecord],
) -> HashMap<String, SnapshotSpace> {
    get_cached_spaces(
        store_root,
        "system",
        snapshots.iter().map(|record| record.id.to_string()),
    )
}

pub fn get_personal_spaces(
    store_root: &Path,
    snapshots: &[PersonalSnapshotRecord],
) -> HashMap<String, SnapshotSpace> {
    get_cached_spaces(
        store_root,
        "home",
        snapshots.iter().map(|record| record.id.to_string()),
    )
}

pub fn measure_snapshot_space(store_root: &Path, scope: &str, id: &str) -> Result<SnapshotSpace> {
    let _measurement = MEASUREMENT_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let snapshot = snapshot_path(store_root, scope, id)?;
    let metadata = fs::symlink_metadata(&snapshot)
        .with_context(|| format!("Failed to inspect {}", snapshot.display()))?;
    if !metadata.file_type().is_dir() {
        bail!("Snapshot path is not a real directory");
    }

    let identity = run_btrfs(&[
        std::ffi::OsStr::new("subvolume"),
        std::ffi::OsStr::new("show"),
        std::ffi::OsStr::new("--raw"),
        snapshot.as_os_str(),
    ])?;
    let subvolume_id =
        parse_subvolume_id(&identity).context("Btrfs did not report the snapshot subvolume ID")?;
    let qgroup_output = run_btrfs(&[
        std::ffi::OsStr::new("qgroup"),
        std::ffi::OsStr::new("show"),
        std::ffi::OsStr::new("--raw"),
        // Restrict output to qgroups that affect this subvolume. In particular, do not use
        // --sync or enumerate every qgroup on a large filesystem for a Properties dialog.
        std::ffi::OsStr::new("-f"),
        snapshot.as_os_str(),
    ]);
    let mut space = match qgroup_output
        .ok()
        .and_then(|output| parse_qgroup_for_subvolume(&output, subvolume_id))
    {
        Some(measured) => {
            let referenced = measured.referenced_bytes;
            let exclusive = measured.exclusive_bytes;
            SnapshotSpace {
                referenced_bytes: referenced,
                exclusive_bytes: exclusive,
                shared_bytes: referenced
                    .zip(exclusive)
                    .and_then(|(referenced, exclusive)| referenced.checked_sub(exclusive)),
                measured_at_unix_seconds: None,
            }
        }
        None => measure_snapshot_space_without_quotas(&snapshot)?,
    };
    space.measured_at_unix_seconds = Some(Utc::now().timestamp());
    write_cache(store_root, scope, id, &space)?;
    Ok(space)
}

fn measure_snapshot_space_without_quotas(snapshot: &Path) -> Result<SnapshotSpace> {
    let output = run_btrfs(&[
        std::ffi::OsStr::new("filesystem"),
        std::ffi::OsStr::new("du"),
        std::ffi::OsStr::new("-s"),
        std::ffi::OsStr::new("--raw"),
        snapshot.as_os_str(),
    ])
    .context("Could not calculate snapshot space without Btrfs quota accounting")?;
    parse_filesystem_du(&output).context("Btrfs did not report snapshot space usage")
}

fn snapshot_path(store_root: &Path, scope: &str, id: &str) -> Result<PathBuf> {
    match scope {
        "system" => {
            let id = id
                .parse::<DeploymentId>()
                .context("Invalid system snapshot identifier")?;
            DeploymentStore::new(store_root)
                .load_record(id)
                .map_err(|error| anyhow::anyhow!(error.message))?;
            Ok(store_root
                .join("deployments")
                .join(id.to_string())
                .join("root"))
        }
        "home" => {
            let id = id
                .parse::<PersonalSnapshotId>()
                .context("Invalid Home snapshot identifier")?;
            let engine = PersonalSnapshotEngine::new("/home", store_root, SystemCommandRunner);
            engine.load(id)?;
            Ok(engine.snapshot_path(id))
        }
        _ => bail!("Invalid snapshot scope"),
    }
}

fn get_cached_spaces(
    store_root: &Path,
    scope: &str,
    ids: impl Iterator<Item = String>,
) -> HashMap<String, SnapshotSpace> {
    let ids = ids.collect::<Vec<_>>();
    prune_stale_cache(store_root, scope, &ids);
    ids.into_iter()
        .filter_map(|id| {
            read_cache(store_root, scope, &id)
                .ok()
                .flatten()
                .map(|space| (id, space))
        })
        .collect()
}

fn prune_stale_cache(store_root: &Path, scope: &str, active_ids: &[String]) {
    let directory = cache_directory(store_root, scope);
    let Ok(entries) = fs::read_dir(directory) else {
        return;
    };
    let active = active_ids
        .iter()
        .map(String::as_str)
        .collect::<std::collections::HashSet<_>>();
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(id) = path.file_stem().and_then(|value| value.to_str()) else {
            continue;
        };
        if path.extension().and_then(|value| value.to_str()) == Some("json")
            && uuid::Uuid::parse_str(id).is_ok()
            && !active.contains(id)
        {
            let _ = fs::remove_file(path);
        }
    }
}

fn cache_directory(store_root: &Path, scope: &str) -> PathBuf {
    store_root.join("space-cache").join(scope)
}

fn cache_path(store_root: &Path, scope: &str, id: &str) -> PathBuf {
    cache_directory(store_root, scope).join(format!("{id}.json"))
}

fn read_cache(store_root: &Path, scope: &str, id: &str) -> Result<Option<SnapshotSpace>> {
    let path = cache_path(store_root, scope, id);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if !metadata.file_type().is_file() || metadata.len() > MAX_CACHE_BYTES {
        bail!("Snapshot space cache is not a bounded regular file");
    }
    let contents = fs::read(&path)?;
    Ok(Some(serde_json::from_slice(&contents)?))
}

fn write_cache(store_root: &Path, scope: &str, id: &str, space: &SnapshotSpace) -> Result<()> {
    let directory = cache_directory(store_root, scope);
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(&directory)?;
    let temporary = directory.join(format!(".{id}.{}.tmp", uuid::Uuid::new_v4()));
    let target = cache_path(store_root, scope, id);
    let result = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)?;
        file.write_all(&serde_json::to_vec_pretty(space)?)?;
        file.sync_all()?;
        fs::rename(&temporary, &target)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn parse_subvolume_id(output: &str) -> Option<u64> {
    output.lines().find_map(|line| {
        line.trim()
            .strip_prefix("Subvolume ID:")
            .and_then(|value| value.trim().parse().ok())
    })
}

fn parse_filesystem_du(output: &str) -> Option<SnapshotSpace> {
    output.lines().find_map(|line| {
        let mut fields = line.split_whitespace();
        let referenced_bytes = fields.next()?.parse::<u64>().ok()?;
        let exclusive_bytes = fields.next().and_then(|value| value.parse::<u64>().ok());
        let shared_bytes = fields.next().and_then(|value| value.parse::<u64>().ok());
        Some(SnapshotSpace {
            referenced_bytes: Some(referenced_bytes),
            exclusive_bytes,
            shared_bytes,
            measured_at_unix_seconds: None,
        })
    })
}

fn run_btrfs(arguments: &[&std::ffi::OsStr]) -> Result<String> {
    let output = Command::new(BTRFS)
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()
        .context("Failed to execute btrfs")?;
    if !output.status.success() {
        let error = String::from_utf8_lossy(&output.stderr);
        bail!("btrfs accounting failed: {}", error.trim());
    }
    if output.stdout.len() > MAX_BTRFS_OUTPUT_BYTES {
        bail!("btrfs accounting returned excessive output");
    }
    String::from_utf8(output.stdout).context("btrfs accounting returned non-UTF-8 output")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn managed_task_phases_keep_the_existing_wire_protocol() {
        let phases = [
            (TaskPhase::Idle, "idle", false),
            (TaskPhase::Starting, "starting", true),
            (TaskPhase::Running, "running", true),
            (TaskPhase::Cancelling, "cancelling", true),
            (TaskPhase::Finished, "finished", false),
            (TaskPhase::Cancelled, "cancelled", false),
            (TaskPhase::Failed, "failed", false),
        ];
        for (phase, wire_name, active) in phases {
            assert_eq!(phase.as_wire_name(), wire_name);
            assert_eq!(phase.is_active(), active);
        }
    }

    #[test]
    fn parses_raw_subvolume_identity() {
        assert_eq!(
            parse_subvolume_id("Name: root\nSubvolume ID: 1234\nUUID: test\n"),
            Some(1234)
        );
    }

    #[test]
    fn parses_root_btrfs_source_and_behavior_options() {
        let mountinfo = concat!(
            "27 22 0:24 / / rw,relatime - ext4 /dev/sda1 rw\n",
            "35 22 0:31 /@ / rw,relatime,ssd,discard=async,space_cache=v2,subvolid=256,subvol=/@ ",
            "- btrfs /dev/nvme0n1p3 rw,compress=zstd:3\n",
        );
        let (source, options) = parse_root_btrfs_mount(mountinfo).unwrap();
        assert_eq!(source, "/dev/nvme0n1p3");
        assert_eq!(mount_option(&options, "compress="), Some("zstd:3".into()));
        assert_eq!(compression_option(&options), "zstd:3");
        assert!(options.contains(&"discard=async".to_string()));

        assert_eq!(
            compression_option(&["compress-force=zstd:5".into()]),
            "zstd:5 (forced)"
        );
    }

    #[test]
    fn parses_raw_usage_and_profiles() {
        let usage = concat!(
            "Overall:\n",
            "    Device size: 987698823168\n",
            "    Used: 188516794368\n",
            "Data,single: Size:190060691456, Used:185263587328\n",
            "Metadata,DUP: Size:3221225472, Used:1626554368\n",
        );
        assert_eq!(usage_value(usage, "Device size:"), Some(987_698_823_168));
        assert_eq!(usage_value(usage, "Used:"), Some(188_516_794_368));
        assert_eq!(block_group_profile(usage, "Data,"), Some("single".into()));
        assert_eq!(block_group_profile(usage, "Metadata,"), Some("DUP".into()));
    }

    #[test]
    fn renders_native_quota_scrub_and_balance_states() {
        assert_eq!(
            quota_status("Quotas on /:\n  Enabled: no\n", true),
            "disabled"
        );
        assert_eq!(
            quota_status("Quotas on /:\n  Enabled: yes\n", true),
            "enabled"
        );
        assert_eq!(quota_status("", false), "unavailable");

        let clean_details = ScrubDetails::default();
        assert_eq!(
            scrub_status("Status: finished\n", "", true, true, &clean_details),
            "finished-clean"
        );
        let broken_details = ScrubDetails {
            uncorrectable_errors: 2,
            ..ScrubDetails::default()
        };
        assert_eq!(
            scrub_status("Status: finished\n", "", true, true, &broken_details),
            "finished-with-errors:2"
        );
        assert_eq!(
            scrub_status("", "status: running", false, false, &clean_details),
            "running"
        );
        assert_eq!(
            scrub_status("no stats available", "", true, false, &clean_details),
            "never-run"
        );
        assert_eq!(
            scrub_status("Status: aborted\n", "", true, false, &clean_details),
            "cancelled"
        );

        assert_eq!(
            balance_status("Balance on '/' is running", "", true),
            "running"
        );
        assert_eq!(balance_status("", "No balance found on '/'", false), "idle");
        assert_eq!(
            balance_status("", "Operation not permitted", false),
            "unavailable"
        );
    }

    #[test]
    fn parses_live_scrub_progress_and_error_counters() {
        let summary = concat!(
            "Scrub started:    Mon Aug 10 03:55:45 2026\n",
            "Status:           running\n",
            "Duration:         0:00:10\n",
            "Time left:        0:00:26\n",
            "Total to scrub:   98885677056\n",
            "Bytes scrubbed:   26921783296  (27.23%)\n",
            "Rate:             2692178329/s\n",
        );
        let raw = concat!(
            "read_errors: 3\n",
            "csum_errors: 2\n",
            "verify_errors: 1\n",
            "super_errors: 4\n",
            "uncorrectable_errors: 1\n",
            "unverified_errors: 2\n",
            "corrected_errors: 7\n",
        );
        let details = parse_scrub_details(summary, raw);
        assert_eq!(details.total_bytes, Some(98_885_677_056));
        assert_eq!(details.bytes_scrubbed, Some(26_921_783_296));
        assert_eq!(details.rate_bytes_per_second, Some(2_692_178_329));
        assert_eq!(details.time_left.as_deref(), Some("0:00:26"));
        assert_eq!(details.checksum_errors, 2);
        assert_eq!(details.corrected_errors, 7);
        assert_eq!(details.unrepaired_error_count(), 3);
        assert_eq!(scrub_status(summary, "", true, true, &details), "running");
    }

    #[test]
    fn foreground_scrub_result_is_authoritative_without_a_status_file() {
        let output = concat!(
            "Starting scrub on devid 1\n",
            "scrub done for a7c04a1f-7190-4762-a58d-6b902536861e\n",
            "Scrub started:    Mon Aug 31 15:02:55 2026\n",
            "Status:           finished\n",
            "Duration:         0:00:45\n",
            "\tdata_extents_scrubbed: 512\n",
            "\ttree_extents_scrubbed: 24\n",
            "\tdata_bytes_scrubbed: 183000000000\n",
            "\ttree_bytes_scrubbed: 385608192\n",
            "\tread_errors: 0\n",
            "\tcsum_errors: 0\n",
            "\tverify_errors: 0\n",
            "\tsuper_errors: 0\n",
            "\tuncorrectable_errors: 0\n",
            "\tunverified_errors: 0\n",
            "\tcorrected_errors: 0\n",
        );
        let result = scrub_run_result(CommandResult {
            success: true,
            stdout: output.into(),
            stderr: "cannot create scrub data file, status recording disabled\n".into(),
        });
        assert_eq!(result.phase, TaskPhase::Finished);
        assert_eq!(result.details.total_bytes, Some(183_385_608_192));
        assert_eq!(result.details.bytes_scrubbed, Some(183_385_608_192));
        assert_eq!(result.details.duration.as_deref(), Some("0:00:45"));
        assert_eq!(scrub_completed_status(&result.details), "finished-clean");
        assert!(result.error.is_none());
    }

    #[test]
    fn successful_exit_without_terminal_counters_is_not_reported_as_clean() {
        let result = scrub_run_result(CommandResult {
            success: true,
            stdout: "Starting scrub on devid 1\n".into(),
            stderr: String::new(),
        });
        assert_eq!(result.phase, TaskPhase::Failed);
        assert_eq!(result.details.total_bytes, None);
        assert!(
            result
                .error
                .as_deref()
                .is_some_and(|error| error.contains("complete terminal scrub result"))
        );
    }

    #[test]
    fn persisted_finished_status_requires_every_health_counter() {
        let details = ScrubDetails::default();
        assert_eq!(
            scrub_status("Status: finished\n", "", true, false, &details),
            "unavailable"
        );
        assert!(!scrub_counters_complete(
            "data_bytes_scrubbed: 1\ntree_bytes_scrubbed: 2\nread_errors: 0\n"
        ));
    }

    #[test]
    fn managed_scrub_state_survives_missing_and_stale_native_status() {
        let native = parse_scrub_details(
            "no stats available\nTotal to scrub: 183385608192\nBytes scrubbed: 0\n",
            "",
        );
        let running = ScrubTaskState {
            generation: 7,
            phase: TaskPhase::Running,
            started: Some(Instant::now()),
            ..ScrubTaskState::default()
        };
        let (status, details) = scrub_task_status_from("running", native, &running);
        assert_eq!(status, "running");
        assert_eq!(details.generation, 7);
        assert_eq!(details.bytes_scrubbed, Some(0));

        let final_details = ScrubDetails {
            generation: 7,
            total_bytes: Some(183_385_608_192),
            bytes_scrubbed: Some(183_385_608_192),
            ..ScrubDetails::default()
        };
        let finished = ScrubTaskState {
            generation: 7,
            phase: TaskPhase::Finished,
            elapsed_seconds: Some(45),
            details: final_details,
            ..ScrubTaskState::default()
        };
        let (status, details) = scrub_task_status_from(
            "never-run",
            parse_scrub_details("no stats available\n", ""),
            &finished,
        );
        assert_eq!(status, "finished-clean");
        assert_eq!(details.generation, 7);
        assert_eq!(details.elapsed_seconds, Some(45));
        assert_eq!(details.bytes_scrubbed, Some(183_385_608_192));
    }

    #[test]
    fn parses_live_and_completed_balance_progress() {
        let live = parse_balance_progress(
            "Balance on '/' is running\n3 out of about 20 chunks balanced (7 considered), 85% left\n",
        );
        assert_eq!(live.chunks_balanced, Some(3));
        assert_eq!(live.chunks_total, Some(20));
        assert_eq!(live.chunks_considered, Some(7));
        assert_eq!(live.percent_remaining, Some(85));

        let completed = parse_balance_completion("Done, had to relocate 4 out of 128 chunks\n")
            .expect("completion line should parse");
        assert_eq!(completed.chunks_balanced, Some(4));
        assert_eq!(completed.chunks_total, Some(128));
        assert_eq!(completed.percent_remaining, Some(0));
    }

    #[test]
    fn treats_initial_progress_pipe_as_running_before_status_file_exists() {
        let summary = concat!(
            "no stats available\n",
            "Time left:        0:00:00\n",
            "Total to scrub:   96748163072\n",
            "Bytes scrubbed:   0  (0.00%)\n",
        );
        let details = parse_scrub_details(summary, "");
        assert_eq!(scrub_status(summary, "", true, false, &details), "running");
    }

    #[test]
    fn parses_quota_free_filesystem_du_fields_independently() {
        let complete = parse_filesystem_du(
            "     Total   Exclusive  Set shared  Filename\n33762402304 0 21135024128 /snapshot\n",
        )
        .unwrap();
        assert_eq!(complete.referenced_bytes, Some(33_762_402_304));
        assert_eq!(complete.exclusive_bytes, Some(0));
        assert_eq!(complete.shared_bytes, Some(21_135_024_128));

        let partial = parse_filesystem_du("100 unavailable unavailable /snapshot\n").unwrap();
        assert_eq!(partial.referenced_bytes, Some(100));
        assert_eq!(partial.exclusive_bytes, None);
        assert_eq!(partial.shared_bytes, None);
    }

    #[test]
    fn cache_reads_active_measurements_and_prunes_deleted_snapshots() {
        let root = std::env::temp_dir().join(format!(
            "snapshots-manager-space-cache-{}-{}",
            std::process::id(),
            uuid::Uuid::new_v4()
        ));
        let active = uuid::Uuid::new_v4().to_string();
        let deleted = uuid::Uuid::new_v4().to_string();
        let space = SnapshotSpace {
            referenced_bytes: Some(100),
            exclusive_bytes: Some(10),
            shared_bytes: Some(90),
            measured_at_unix_seconds: Some(1),
        };
        write_cache(&root, "system", &active, &space).unwrap();
        write_cache(&root, "system", &deleted, &space).unwrap();

        let cached = get_cached_spaces(&root, "system", [active.clone()].into_iter());
        assert_eq!(cached.get(&active), Some(&space));
        assert!(!cache_path(&root, "system", &deleted).exists());

        fs::remove_dir_all(root).unwrap();
    }
}
