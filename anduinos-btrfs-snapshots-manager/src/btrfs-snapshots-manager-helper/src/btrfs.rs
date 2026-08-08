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
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;

const BTRFS: &str = "/usr/bin/btrfs";
const MAX_BTRFS_OUTPUT_BYTES: usize = 16 * 1024 * 1024;
const MAX_CACHE_BYTES: u64 = 64 * 1024;
static MEASUREMENT_LOCK: Mutex<()> = Mutex::new(());

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
    pub balance: String,
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
    let scrub = run_btrfs_allow_failure(&["scrub", "status", "-R", ROOT_MOUNT]);
    let balance = run_btrfs_allow_failure(&["balance", "status", ROOT_MOUNT]);
    Ok(FilesystemStatus {
        schema_version: 1,
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
        scrub: scrub_status(&scrub.stdout, &scrub.stderr, scrub.success),
        balance: balance_status(&balance.stdout, &balance.stderr, balance.success),
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
    run_btrfs_mutating(&["scrub", "start", ROOT_MOUNT])?;
    Ok("The integrity check has started in the background".into())
}

pub fn cancel_scrub() -> Result<String> {
    run_btrfs_mutating(&["scrub", "cancel", ROOT_MOUNT])?;
    Ok("The integrity check was cancelled".into())
}

pub fn start_filtered_balance() -> Result<String> {
    run_btrfs_mutating(&[
        "balance",
        "start",
        "--background",
        "-dusage=50",
        "-musage=50",
        ROOT_MOUNT,
    ])?;
    Ok("A limited space rebalance has started in the background".into())
}

pub fn cancel_balance() -> Result<String> {
    run_btrfs_mutating(&["balance", "cancel", ROOT_MOUNT])?;
    Ok("The space rebalance was cancelled".into())
}

pub fn defragment_home() -> Result<String> {
    run_btrfs_mutating(&["filesystem", "defragment", "-r", "-czstd", "/home"])?;
    Ok("Home file defragmentation completed".into())
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

fn scrub_status(stdout: &str, stderr: &str, success: bool) -> String {
    let combined = format!("{stdout}\n{stderr}").to_ascii_lowercase();
    if combined.contains("status: running") {
        "running".into()
    } else if combined.contains("no stats available") {
        "never-run".into()
    } else if success {
        let uncorrectable = metric(stdout, "uncorrectable_errors:").unwrap_or(0);
        let corrected = metric(stdout, "corrected_errors:").unwrap_or(0);
        if uncorrectable > 0 {
            format!("finished-with-errors:{uncorrectable}")
        } else if corrected > 0 {
            format!("finished-repaired:{corrected}")
        } else {
            "finished-clean".into()
        }
    } else {
        "unavailable".into()
    }
}

fn balance_status(stdout: &str, stderr: &str, success: bool) -> String {
    let combined = format!("{stdout}\n{stderr}").to_ascii_lowercase();
    if combined.contains("is running") {
        "running".into()
    } else if combined.contains("is paused") {
        "paused".into()
    } else if combined.contains("no balance found") || combined.contains("not in progress") {
        "idle".into()
    } else if success {
        "idle".into()
    } else {
        "unavailable".into()
    }
}

fn metric(output: &str, label: &str) -> Option<u64> {
    output.lines().find_map(|line| {
        line.trim()
            .strip_prefix(label)?
            .trim()
            .split_whitespace()
            .next()?
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

        let clean = "uncorrectable_errors: 0\ncorrected_errors: 0\n";
        assert_eq!(scrub_status(clean, "", true), "finished-clean");
        assert_eq!(
            scrub_status("uncorrectable_errors: 2\n", "", true),
            "finished-with-errors:2"
        );
        assert_eq!(scrub_status("", "status: running", false), "running");
        assert_eq!(scrub_status("no stats available", "", true), "never-run");

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
