// Btrfs operations for waypoint-helper

use anduinos_recovery_engine::space::{SnapshotSpace, parse_qgroup_for_subvolume};
use anyhow::{Context, Result, bail};
use std::path::Path;
use std::process::Command;

const BTRFS: &str = "/usr/bin/btrfs";
const MAX_BTRFS_OUTPUT_BYTES: usize = 16 * 1024 * 1024;

pub fn get_personal_spaces(
    snapshots: &[anduinos_recovery_engine::personal::PersonalSnapshotRecord],
) -> std::collections::HashMap<String, SnapshotSpace> {
    let engine = anduinos_recovery_engine::personal::PersonalSnapshotEngine::default();
    snapshots
        .iter()
        .filter_map(|record| {
            snapshot_space(&engine.snapshot_path(record.id))
                .ok()
                .flatten()
                .map(|space| (record.id.to_string(), space))
        })
        .collect()
}

fn snapshot_space(snapshot: &Path) -> Result<Option<SnapshotSpace>> {
    let root_id = run_btrfs(&[
        std::ffi::OsStr::new("inspect-internal"),
        std::ffi::OsStr::new("rootid"),
        snapshot.as_os_str(),
    ])?
    .trim()
    .parse::<u64>()
    .context("btrfs returned an invalid subvolume ID")?;
    let qgroups = run_btrfs(&[
        std::ffi::OsStr::new("qgroup"),
        std::ffi::OsStr::new("show"),
        std::ffi::OsStr::new("--raw"),
        snapshot.as_os_str(),
    ])?;
    Ok(parse_qgroup_for_subvolume(&qgroups, root_id))
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

/// Verification result for a snapshot
#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct VerificationResult {
    pub is_valid: bool,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}
