use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

const SCHEMA_VERSION: u32 = 1;
const MAX_PREFERENCES_BYTES: u64 = 64 * 1024;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BrowserViewMode {
    #[default]
    List,
    Grid,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BrowserSortMode {
    #[default]
    Name,
    Modified,
    Size,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BrowserConflictPolicy {
    #[default]
    KeepBoth,
    Replace,
    Skip,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrowserPreferences {
    pub schema_version: u32,
    pub view_mode: BrowserViewMode,
    pub show_hidden: bool,
    pub sort_mode: BrowserSortMode,
    pub descending: bool,
    pub conflict_policy: BrowserConflictPolicy,
}

impl Default for BrowserPreferences {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            view_mode: BrowserViewMode::List,
            show_hidden: false,
            sort_mode: BrowserSortMode::Name,
            descending: false,
            conflict_policy: BrowserConflictPolicy::KeepBoth,
        }
    }
}

impl BrowserPreferences {
    pub fn load() -> Self {
        load_or_default_from(&preferences_path())
    }

    pub fn save(&self) -> io::Result<()> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "unsupported browser preference schema",
            ));
        }
        save_to(&preferences_path(), self)
    }
}

fn load_or_default_from(path: &Path) -> BrowserPreferences {
    load_from(path).unwrap_or_default()
}

fn preferences_path() -> PathBuf {
    glib::user_config_dir()
        .join("anduinos-timeback-machine")
        .join("browser-preferences.json")
}

fn load_from(path: &Path) -> io::Result<BrowserPreferences> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() > MAX_PREFERENCES_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "browser preferences are not a bounded regular file",
        ));
    }
    let mut contents = Vec::with_capacity(metadata.len() as usize);
    file.take(MAX_PREFERENCES_BYTES + 1)
        .read_to_end(&mut contents)?;
    let preferences: BrowserPreferences = serde_json::from_slice(&contents)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    if preferences.schema_version != SCHEMA_VERSION {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unsupported browser preference schema",
        ));
    }
    Ok(preferences)
}

fn save_to(path: &Path, preferences: &BrowserPreferences) -> io::Result<()> {
    let directory = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing preference parent"))?;
    match fs::symlink_metadata(directory) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "browser preference directory is not a real directory",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir_all(directory)?;
            fs::set_permissions(directory, fs::Permissions::from_mode(0o700))?;
        }
        Err(error) => return Err(error),
    }
    let serialized = serde_json::to_vec_pretty(preferences)
        .map_err(|error| io::Error::new(io::ErrorKind::Other, error))?;
    if serialized.len() as u64 > MAX_PREFERENCES_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "browser preferences exceed the size limit",
        ));
    }
    let temporary = directory.join(format!(
        ".browser-preferences-{}.tmp",
        Uuid::new_v4().hyphenated()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)?;
        file.write_all(&serialized)?;
        file.flush()?;
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporary_path() -> PathBuf {
        std::env::temp_dir()
            .join(format!("timeback-preferences-{}", Uuid::new_v4()))
            .join("browser-preferences.json")
    }

    #[test]
    fn preferences_round_trip_atomically() {
        let path = temporary_path();
        let preferences = BrowserPreferences {
            view_mode: BrowserViewMode::Grid,
            show_hidden: true,
            sort_mode: BrowserSortMode::Modified,
            descending: true,
            conflict_policy: BrowserConflictPolicy::Skip,
            ..BrowserPreferences::default()
        };
        save_to(&path, &preferences).unwrap();
        assert_eq!(load_from(&path).unwrap(), preferences);
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
        fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }

    #[test]
    fn corrupt_and_future_preferences_fall_back_to_defaults() {
        let path = temporary_path();
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"not json").unwrap();
        assert_eq!(load_or_default_from(&path), BrowserPreferences::default());
        fs::write(
            &path,
            serde_json::to_vec(&BrowserPreferences {
                schema_version: SCHEMA_VERSION + 1,
                ..BrowserPreferences::default()
            })
            .unwrap(),
        )
        .unwrap();
        assert_eq!(load_or_default_from(&path), BrowserPreferences::default());
        fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }

    #[test]
    fn preference_symlinks_are_never_followed() {
        let path = temporary_path();
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink("/etc/passwd", &path).unwrap();
        assert!(load_from(&path).is_err());
        fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }
}
