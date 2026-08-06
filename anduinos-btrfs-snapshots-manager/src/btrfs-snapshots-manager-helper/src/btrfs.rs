//! Btrfs space measurements and their non-authoritative cache.
//!
//! Measuring one snapshot requires walking its files and extents with FIEMAP. Status queries
//! therefore read only this cache; the GUI explicitly asks the helper to fill missing entries in
//! the background. The cache is never used for recovery or deletion decisions.

use anduinos_recovery_engine::{
    model::{DeploymentId, DeploymentRecord},
    operations::SystemCommandRunner,
    personal::{PersonalSnapshotEngine, PersonalSnapshotId, PersonalSnapshotRecord},
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

    let output = run_btrfs(&[
        std::ffi::OsStr::new("filesystem"),
        std::ffi::OsStr::new("du"),
        std::ffi::OsStr::new("--raw"),
        std::ffi::OsStr::new("--summarize"),
        snapshot.as_os_str(),
    ])?;
    let mut space = parse_filesystem_du(&output).context("btrfs returned no space measurement")?;
    space.measured_at_unix_seconds = Some(Utc::now().timestamp());
    write_cache(store_root, scope, id, &space)?;
    Ok(space)
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

fn parse_filesystem_du(output: &str) -> Option<SnapshotSpace> {
    output.lines().rev().find_map(|line| {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        if fields.len() < 3 {
            return None;
        }
        let (Ok(total), Ok(exclusive), Ok(shared)) = (
            fields[0].parse::<u64>(),
            fields[1].parse::<u64>(),
            fields[2].parse::<u64>(),
        ) else {
            return None;
        };
        Some(SnapshotSpace {
            referenced_bytes: Some(total),
            exclusive_bytes: Some(exclusive),
            shared_bytes: Some(shared),
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
    fn parses_raw_filesystem_du_with_a_spaced_path() {
        let space = parse_filesystem_du(
            "     Total   Exclusive  Set shared  Filename\n\
             18943447040 80666624 11177463808 /path/with spaces\n",
        )
        .unwrap();
        assert_eq!(space.referenced_bytes, Some(18_943_447_040));
        assert_eq!(space.exclusive_bytes, Some(80_666_624));
        assert_eq!(space.shared_bytes, Some(11_177_463_808));
        assert_eq!(space.measured_at_unix_seconds, None);
    }

    #[test]
    fn rejects_the_filesystem_du_header_without_values() {
        assert!(parse_filesystem_du("Total Exclusive Set shared Filename\n").is_none());
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
