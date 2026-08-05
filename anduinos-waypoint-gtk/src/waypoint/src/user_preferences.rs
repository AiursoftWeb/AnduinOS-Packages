//! User-specific preferences for snapshots
//!
//! This module manages user-specific metadata like favorites and notes,
//! stored separately from the main snapshot metadata to allow multiple users
//! to have their own preferences for the same snapshots.

use anyhow::{Context, Result, anyhow};
use serde::{Deserialize, Serialize};
use std::cell::RefCell;
use std::collections::HashMap;
use std::fs;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::PathBuf;

/// User preferences for a specific snapshot
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SnapshotPreferences {
    /// Whether this snapshot is favorited/pinned by this user
    #[serde(default)]
    pub is_favorite: bool,

    /// User's personal note for this snapshot
    #[serde(default)]
    pub note: Option<String>,
}

/// Manager for user-specific snapshot preferences
pub struct UserPreferencesManager {
    preferences_file: Option<PathBuf>,
    ephemeral_preferences: RefCell<HashMap<String, SnapshotPreferences>>,
}

impl UserPreferencesManager {
    /// Create a new user preferences manager
    ///
    /// Uses `~/.local/share/anduinos-waypoint/user-preferences.json` to store user-specific
    /// metadata like favorites and notes.
    pub fn new() -> Result<Self> {
        let data_dir = dirs::data_dir()
            .ok_or_else(|| anyhow!("The user data directory could not be determined"))?;
        let waypoint_dir = data_dir.join("anduinos-waypoint");

        fs::create_dir_all(&waypoint_dir).context("Failed to create user preferences directory")?;

        Ok(Self {
            preferences_file: Some(waypoint_dir.join("user-preferences.json")),
            ephemeral_preferences: RefCell::new(HashMap::new()),
        })
    }

    /// Create a process-local manager when persistent user storage is unavailable.
    ///
    /// Recovery-point data and trusted state remain in the system helper. Only optional
    /// per-user notes and display preferences are lost when the application exits.
    pub fn ephemeral() -> Self {
        Self {
            preferences_file: None,
            ephemeral_preferences: RefCell::new(HashMap::new()),
        }
    }

    /// Load all user preferences
    ///
    /// Returns a HashMap mapping snapshot IDs to their preferences.
    /// Returns empty map if file doesn't exist (not an error).
    pub fn load(&self) -> Result<HashMap<String, SnapshotPreferences>> {
        let Some(preferences_file) = self.preferences_file.as_ref() else {
            return Ok(self.ephemeral_preferences.borrow().clone());
        };

        if !preferences_file.exists() {
            return Ok(HashMap::new());
        }

        let mut file = self.locked_file(false)?;
        let mut content = String::new();
        file.read_to_string(&mut content)
            .context("Failed to read user preferences")?;
        fs2::FileExt::unlock(&file).ok();

        let prefs: HashMap<String, SnapshotPreferences> =
            serde_json::from_str(&content).context("Failed to parse user preferences")?;

        Ok(prefs)
    }

    /// Save all user preferences
    pub fn save(&self, preferences: &HashMap<String, SnapshotPreferences>) -> Result<()> {
        let Some(preferences_file) = self.preferences_file.as_ref() else {
            *self.ephemeral_preferences.borrow_mut() = preferences.clone();
            return Ok(());
        };

        let content = serde_json::to_string_pretty(preferences)
            .context("Failed to serialize user preferences")?;

        let _lock = self.locked_file(true)?;
        let tmp_path = preferences_file.with_extension("tmp");

        {
            let mut file = OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .open(&tmp_path)
                .with_context(|| {
                    format!(
                        "Failed to open temporary preferences file {}",
                        tmp_path.display()
                    )
                })?;
            file.write_all(content.as_bytes())
                .context("Failed to write user preferences")?;
            file.sync_all().context("Failed to sync user preferences")?;
        }

        fs::rename(&tmp_path, preferences_file)
            .with_context(|| format!("Failed to replace {}", preferences_file.display()))?;

        Ok(())
    }

    /// Get preferences for a specific snapshot
    pub fn get(&self, snapshot_id: &str) -> Result<SnapshotPreferences> {
        let prefs = self.load()?;
        Ok(prefs.get(snapshot_id).cloned().unwrap_or_default())
    }

    /// Update preferences for a specific snapshot
    pub fn update(&self, snapshot_id: &str, preferences: SnapshotPreferences) -> Result<()> {
        let mut all_prefs = self.load()?;

        // If preferences are default (not favorite, no note), remove the entry to keep file clean
        if !preferences.is_favorite && preferences.note.is_none() {
            all_prefs.remove(snapshot_id);
        } else {
            all_prefs.insert(snapshot_id.to_string(), preferences);
        }

        self.save(&all_prefs)
    }

    /// Update note for a snapshot
    pub fn update_note(&self, snapshot_id: &str, note: Option<String>) -> Result<()> {
        let mut prefs = self.get(snapshot_id)?;
        prefs.note = note;
        self.update(snapshot_id, prefs)
    }

    fn locked_file(&self, write: bool) -> Result<std::fs::File> {
        let preferences_file = self
            .preferences_file
            .as_ref()
            .ok_or_else(|| anyhow!("Ephemeral preferences do not have a backing file"))?;
        let file = OpenOptions::new()
            .read(true)
            .write(write)
            .create(write)
            .open(preferences_file)
            .with_context(|| format!("Failed to open {}", preferences_file.display()))?;

        if write {
            fs2::FileExt::lock_exclusive(&file)
                .context("Failed to lock preferences for writing")?;
        } else {
            fs2::FileExt::lock_shared(&file).context("Failed to lock preferences for reading")?;
        }

        Ok(file)
    }
}

#[cfg(test)]
mod tests {
    use super::{SnapshotPreferences, UserPreferencesManager};

    #[test]
    fn ephemeral_preferences_round_trip_without_a_shared_temp_file() {
        let manager = UserPreferencesManager::ephemeral();
        manager
            .update_note("deployment-id", Some("keep this one".to_string()))
            .expect("ephemeral update should succeed");

        let saved = manager
            .get("deployment-id")
            .expect("ephemeral load should succeed");
        assert_eq!(saved.note.as_deref(), Some("keep this one"));
        assert!(!saved.is_favorite);

        manager
            .update("deployment-id", SnapshotPreferences::default())
            .expect("ephemeral removal should succeed");
        assert!(manager.load().expect("load should succeed").is_empty());
    }
}
