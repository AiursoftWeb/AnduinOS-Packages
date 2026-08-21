use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct AptSnapshotPolicy {
    pub snapshot_before: bool,
    pub snapshot_after: bool,
}

impl Default for AptSnapshotPolicy {
    fn default() -> Self {
        Self {
            snapshot_before: true,
            snapshot_after: false,
        }
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyFile {
    apt: AptSnapshotPolicy,
}

impl AptSnapshotPolicy {
    pub fn system_path() -> PathBuf {
        std::env::var_os("ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_APT_POLICY")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from("/etc/anduinos-btrfs-snapshots-manager/apt-snapshots.toml")
            })
    }

    pub fn load_from_file(path: &Path) -> Result<Self, String> {
        match fs::read_to_string(path) {
            Ok(content) => toml::from_str::<PolicyFile>(&content)
                .map(|file| file.apt)
                .map_err(|error| format!("Invalid APT snapshot policy: {error}")),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(format!("Failed to read {}: {error}", path.display())),
        }
    }

    pub fn save_to_file(&self, path: &Path) -> Result<(), String> {
        let parent = path.parent().ok_or("APT policy path has no parent")?;
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o755))
            .map_err(|error| error.to_string())?;
        let content = toml::to_string_pretty(&PolicyFile { apt: *self })
            .map_err(|error| error.to_string())?;
        let name = path.file_name().ok_or("APT policy path has no file name")?;
        let temporary = parent.join(format!(
            ".{}.{}.tmp",
            name.to_string_lossy(),
            Uuid::new_v4()
        ));
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&temporary)
            .map_err(|error| error.to_string())?;
        file.write_all(content.as_bytes())
            .map_err(|error| error.to_string())?;
        file.sync_all().map_err(|error| error.to_string())?;
        fs::rename(&temporary, path).map_err(|error| error.to_string())?;
        fs::set_permissions(path, fs::Permissions::from_mode(0o644))
            .map_err(|error| error.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_to_pre_only() {
        assert_eq!(
            AptSnapshotPolicy::default(),
            AptSnapshotPolicy {
                snapshot_before: true,
                snapshot_after: false,
            }
        );
    }

    #[test]
    fn parses_the_apt_table() {
        let parsed: PolicyFile =
            toml::from_str("[apt]\nsnapshot_before = false\nsnapshot_after = true\n").unwrap();
        assert_eq!(
            parsed.apt,
            AptSnapshotPolicy {
                snapshot_before: false,
                snapshot_after: true,
            }
        );
    }
}
