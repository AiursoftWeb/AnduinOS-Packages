// Btrfs operations for waypoint-helper

use anduinos_recovery_engine::{
    RECOVERY_STORE_ROOT, layout,
    model::DeploymentId,
    operations::OperationEngine,
    space::{SnapshotSpace, parse_qgroup_for_subvolume},
};
use anyhow::{Context, Result, anyhow, bail};
use std::path::Path;
use std::process::Command;
use std::sync::OnceLock;
use waypoint_common::WaypointConfig;

/// Global configuration instance
static CONFIG: OnceLock<WaypointConfig> = OnceLock::new();
const BTRFS: &str = "/usr/bin/btrfs";
const MAX_BTRFS_OUTPUT_BYTES: usize = 16 * 1024 * 1024;

/// Initialize the global configuration (called once at startup)
pub fn init_config() {
    CONFIG.get_or_init(WaypointConfig::default);
}

/// Get the snapshot directory path
fn snapshot_dir() -> &'static Path {
    CONFIG
        .get_or_init(WaypointConfig::default)
        .snapshot_dir
        .as_path()
}

/// Return truthful Btrfs accounting for each trusted deployment.
///
/// Missing quota accounting omits the deployment instead of substituting `du`, whose apparent
/// byte count includes shared extents and is neither actual cost nor deletion reclaimability.
pub fn get_deployment_spaces(
    deployment_ids: Vec<String>,
) -> Result<std::collections::HashMap<String, SnapshotSpace>> {
    use std::collections::HashMap;

    if deployment_ids.is_empty() {
        return Ok(HashMap::new());
    }
    run_btrfs(&[
        std::ffi::OsStr::new("filesystem"),
        std::ffi::OsStr::new("sync"),
        snapshot_dir().as_os_str(),
    ])?;

    let mut result = HashMap::new();
    for value in deployment_ids {
        let Ok(id) = value.parse::<DeploymentId>() else {
            continue;
        };
        let snapshot = snapshot_dir().join(id.to_string()).join("root");
        let root_id_output = run_btrfs(&[
            std::ffi::OsStr::new("inspect-internal"),
            std::ffi::OsStr::new("rootid"),
            snapshot.as_os_str(),
        ])?;
        let root_id = root_id_output.trim().parse::<u64>().with_context(|| {
            format!(
                "btrfs returned an invalid subvolume ID for recovery point {}",
                id
            )
        })?;
        let qgroups = run_btrfs(&[
            std::ffi::OsStr::new("qgroup"),
            std::ffi::OsStr::new("show"),
            std::ffi::OsStr::new("--raw"),
            snapshot.as_os_str(),
        ])?;
        if let Some(space) = parse_qgroup_for_subvolume(&qgroups, root_id) {
            result.insert(id.to_string(), space);
        }
    }
    Ok(result)
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

/// Package change information for restore preview
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PackageChange {
    pub name: String,
    pub current_version: Option<String>,
    pub snapshot_version: Option<String>,
    pub change_type: String, // "add", "remove", "upgrade", "downgrade", "unchanged"
}

/// Preview of what will happen if a snapshot is restored
#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct RestorePreview {
    pub snapshot_name: String,
    pub snapshot_timestamp: String,
    pub snapshot_description: Option<String>,
    pub current_kernel: Option<String>,
    pub snapshot_kernel: Option<String>,
    pub packages_to_add: Vec<PackageChange>,
    pub packages_to_remove: Vec<PackageChange>,
    pub packages_to_upgrade: Vec<PackageChange>,
    pub packages_to_downgrade: Vec<PackageChange>,
    pub total_package_changes: usize,
}

/// Preview what will happen if a snapshot is restored
///
/// Compares the snapshot's state with the current system state to show:
/// - Which packages will be added, removed, upgraded, or downgraded
/// - Kernel version changes
/// - The fixed System-only recovery scope
pub fn preview_restore(name: &str) -> Result<RestorePreview> {
    use crate::packages::{get_installed_packages, get_packages_from_status};
    use std::collections::HashMap;

    let id = name
        .parse::<DeploymentId>()
        .context("Invalid recovery point ID")?;
    let snapshot_meta = OperationEngine::default()
        .verify(
            &layout::inspect_current(),
            id,
            |_phase, _fraction, _message| {},
        )
        .map_err(|error| anyhow!(error.to_string()))?;
    let snapshot_root = Path::new(RECOVERY_STORE_ROOT)
        .join("deployments")
        .join(id.to_string())
        .join("root");
    let snapshot_packages = get_packages_from_status(&snapshot_root.join("var/lib/dpkg/status"))?;

    // Get current packages
    let current_packages =
        get_installed_packages().context("Failed to get current installed packages")?;

    // Build maps for easy lookup
    let current_pkg_map: HashMap<String, String> = current_packages
        .iter()
        .map(|p| (p.name.clone(), p.version.clone()))
        .collect();

    let snapshot_pkg_map: HashMap<String, String> = snapshot_packages
        .iter()
        .map(|p| (p.name.clone(), p.version.clone()))
        .collect();

    // Categorize package changes
    let mut packages_to_add = Vec::new();
    let mut packages_to_remove = Vec::new();
    let mut packages_to_upgrade = Vec::new();
    let mut packages_to_downgrade = Vec::new();

    // Check packages in snapshot
    for (snap_name, snap_version) in &snapshot_pkg_map {
        match current_pkg_map.get(snap_name) {
            None => {
                // Package is in snapshot but not currently installed - will be added
                packages_to_add.push(PackageChange {
                    name: snap_name.clone(),
                    current_version: None,
                    snapshot_version: Some(snap_version.clone()),
                    change_type: "add".to_string(),
                });
            }
            Some(current_version) => {
                if current_version != snap_version {
                    // Version mismatch - determine if upgrade or downgrade
                    let ordering = compare_debian_versions(current_version, snap_version)
                        .context("Failed to compare Debian package versions")?;
                    let change = if ordering == std::cmp::Ordering::Greater {
                        PackageChange {
                            name: snap_name.clone(),
                            current_version: Some(current_version.clone()),
                            snapshot_version: Some(snap_version.clone()),
                            change_type: "downgrade".to_string(),
                        }
                    } else {
                        PackageChange {
                            name: snap_name.clone(),
                            current_version: Some(current_version.clone()),
                            snapshot_version: Some(snap_version.clone()),
                            change_type: "upgrade".to_string(),
                        }
                    };

                    if ordering == std::cmp::Ordering::Greater {
                        packages_to_downgrade.push(change);
                    } else if ordering == std::cmp::Ordering::Less {
                        packages_to_upgrade.push(change);
                    }
                }
            }
        }
    }

    // Check for packages currently installed but not in snapshot (will be removed)
    for (current_name, current_version) in &current_pkg_map {
        if !snapshot_pkg_map.contains_key(current_name) {
            packages_to_remove.push(PackageChange {
                name: current_name.clone(),
                current_version: Some(current_version.clone()),
                snapshot_version: None,
                change_type: "remove".to_string(),
            });
        }
    }

    // Get current kernel version
    let current_kernel = get_current_kernel_version().ok();

    let total_package_changes = packages_to_add.len()
        + packages_to_remove.len()
        + packages_to_upgrade.len()
        + packages_to_downgrade.len();

    Ok(RestorePreview {
        snapshot_name: snapshot_meta.title.clone(),
        snapshot_timestamp: snapshot_meta
            .created_at
            .format("%Y-%m-%d %H:%M:%S UTC")
            .to_string(),
        snapshot_description: Some(snapshot_meta.reason.clone()),
        current_kernel,
        snapshot_kernel: snapshot_meta.kernel_release.clone(),
        packages_to_add,
        packages_to_remove,
        packages_to_upgrade,
        packages_to_downgrade,
        total_package_changes,
    })
}

/// Compare two versions using dpkg's native Debian version ordering.
///
/// Debian epochs, tildes, and package revisions do not follow semantic-version
/// ordering, so a general-purpose version crate cannot classify restore
/// previews correctly.
fn compare_debian_versions(left: &str, right: &str) -> Result<std::cmp::Ordering> {
    for (relation, ordering) in [
        ("lt", std::cmp::Ordering::Less),
        ("gt", std::cmp::Ordering::Greater),
    ] {
        let status = Command::new("/usr/bin/dpkg")
            .args(["--compare-versions", left, relation, right])
            .env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
            .env("LC_ALL", "C")
            .status()
            .context("Failed to execute dpkg --compare-versions")?;
        match status.code() {
            Some(0) => return Ok(ordering),
            Some(1) => {}
            _ => anyhow::bail!("dpkg rejected a package version comparison"),
        }
    }
    Ok(std::cmp::Ordering::Equal)
}

/// Get current kernel version
fn get_current_kernel_version() -> Result<String> {
    let output = Command::new("/usr/bin/uname")
        .arg("-r")
        .output()
        .context("Failed to execute uname")?;

    if !output.status.success() {
        bail!("Failed to get kernel version");
    }

    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compares_versions_with_debian_epoch_tilde_and_revision_rules() {
        assert_eq!(
            compare_debian_versions("1:9.9-1", "2:1.0-1").unwrap(),
            std::cmp::Ordering::Less
        );
        assert_eq!(
            compare_debian_versions("1.0~rc1-1", "1.0-1").unwrap(),
            std::cmp::Ordering::Less
        );
        assert_eq!(
            compare_debian_versions("2.43-2ubuntu2.3", "2.43-2ubuntu2").unwrap(),
            std::cmp::Ordering::Greater
        );
        assert_eq!(
            compare_debian_versions("1.0-1", "1.0-1").unwrap(),
            std::cmp::Ordering::Equal
        );
    }
}
