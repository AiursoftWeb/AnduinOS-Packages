use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::RetentionPolicy;

pub const AUTOMATION_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NotificationPolicy {
    #[serde(default)]
    pub notify_before_scheduled: bool,
    #[serde(default = "default_true")]
    pub notify_after_success: bool,
    #[serde(default)]
    pub notify_after_cleanup: bool,
}

impl Default for NotificationPolicy {
    fn default() -> Self {
        Self {
            notify_before_scheduled: false,
            notify_after_success: true,
            notify_after_cleanup: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AutomationConfig {
    pub schema_version: u32,
    pub system: RetentionPolicy,
    pub home: RetentionPolicy,
    #[serde(default)]
    pub notifications: NotificationPolicy,
}

impl Default for AutomationConfig {
    fn default() -> Self {
        Self {
            schema_version: AUTOMATION_SCHEMA_VERSION,
            system: RetentionPolicy::system_default(),
            home: RetentionPolicy::home_default(),
            notifications: NotificationPolicy::default(),
        }
    }
}

impl AutomationConfig {
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.schema_version != AUTOMATION_SCHEMA_VERSION {
            anyhow::bail!("unsupported automation configuration version");
        }
        self.system.validate()?;
        self.home.validate()?;
        Ok(())
    }

    pub fn load_from_file(path: &Path) -> anyhow::Result<Self> {
        let config: Self = toml::from_str(&fs::read_to_string(path)?)?;
        config.validate()?;
        Ok(config)
    }

    pub fn save_to_file(&self, path: &Path) -> anyhow::Result<()> {
        self.validate()?;
        let parent = path
            .parent()
            .ok_or_else(|| anyhow::anyhow!("automation path has no parent"))?;
        let metadata = fs::symlink_metadata(parent)?;
        if !metadata.file_type().is_dir() {
            anyhow::bail!("automation parent is not a real directory");
        }
        if let Ok(metadata) = fs::symlink_metadata(path)
            && !metadata.file_type().is_file()
        {
            anyhow::bail!("automation target is not a regular file");
        }
        let temporary = parent.join(format!(
            ".automation.{}.{}.tmp",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos()
        ));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o644)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)?;
        let result = (|| -> anyhow::Result<()> {
            file.write_all(toml::to_string_pretty(self)?.as_bytes())?;
            file.sync_all()?;
            fs::set_permissions(&temporary, fs::Permissions::from_mode(0o644))?;
            fs::rename(&temporary, path)?;
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
                .open(parent)?
                .sync_all()?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }
}

const fn default_true() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_enable_both_scopes_and_use_quiet_pre_notifications() {
        let config = AutomationConfig::default();
        assert!(config.system.is_auto_snapshot_enabled);
        assert!(config.home.is_auto_snapshot_enabled);
        assert_eq!(config.system.snapshot_interval_hours, 24);
        assert_eq!(config.home.snapshot_interval_hours, 2);
        assert!(!config.notifications.notify_before_scheduled);
        assert!(config.notifications.notify_after_success);
        assert!(!config.notifications.notify_after_cleanup);
        assert!(config.validate().is_ok());
    }

    #[test]
    fn config_round_trips_through_toml() {
        let expected = AutomationConfig::default();
        let encoded = toml::to_string(&expected).unwrap();
        let decoded: AutomationConfig = toml::from_str(&encoded).unwrap();
        assert_eq!(decoded, expected);
    }

    #[test]
    fn older_notification_config_defaults_cleanup_notifications_off() {
        let encoded = toml::to_string(&AutomationConfig::default())
            .unwrap()
            .replace("notify_after_cleanup = false\n", "");
        let decoded: AutomationConfig = toml::from_str(&encoded).unwrap();
        assert!(!decoded.notifications.notify_after_cleanup);
    }
}
