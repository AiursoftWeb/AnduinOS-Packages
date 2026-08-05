// Snapshot schedule configuration with TOML support

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;

use crate::retention::TimelineRetention;

/// Type of snapshot schedule
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ScheduleType {
    Hourly,
    Daily,
    Weekly,
    Monthly,
}

/// Recovery histories are intentionally independent: system schedules create
/// bootable `@root` deployments while personal schedules create `@home`
/// history that can only export files.
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "lowercase")]
pub enum ScheduleScope {
    #[default]
    System,
    Personal,
}

impl ScheduleScope {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::System => "system",
            Self::Personal => "personal",
        }
    }
}

impl ScheduleType {
    pub fn as_str(&self) -> &str {
        match self {
            ScheduleType::Hourly => "hourly",
            ScheduleType::Daily => "daily",
            ScheduleType::Weekly => "weekly",
            ScheduleType::Monthly => "monthly",
        }
    }
}

/// A single snapshot schedule configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Schedule {
    /// Whether this schedule is enabled
    pub enabled: bool,

    /// Independent recovery history targeted by this schedule. Missing in old
    /// configuration means System, preserving the previous ABI.
    #[serde(default)]
    pub scope: ScheduleScope,

    /// Whether a successful scheduled creation should be announced to active
    /// desktop sessions. Missing in older configuration defaults to enabled.
    #[serde(default = "default_notify_on_create")]
    pub notify_on_create: bool,

    /// Type of schedule (hourly, daily, weekly, monthly)
    #[serde(rename = "type")]
    pub schedule_type: ScheduleType,

    /// Time of day for daily/weekly/monthly schedules (HH:MM format)
    /// Only used for daily, weekly, and monthly schedules
    #[serde(skip_serializing_if = "Option::is_none")]
    pub time: Option<String>,

    /// Day of week for weekly schedules (0-6, where 0=Sunday)
    /// Only used for weekly schedules
    #[serde(skip_serializing_if = "Option::is_none")]
    pub day_of_week: Option<u8>,

    /// Day of month for monthly schedules (1-31)
    /// Only used for monthly schedules
    #[serde(skip_serializing_if = "Option::is_none")]
    pub day_of_month: Option<u8>,

    /// Recovery-point name prefix (e.g., "hourly", "daily")
    pub prefix: String,

    /// Description for recovery points created by this schedule
    pub description: String,

    /// Maximum number of snapshots to keep for this schedule (legacy)
    /// Deprecated: Use timeline_retention instead
    #[serde(default)]
    pub keep_count: u32,

    /// Maximum age in days for snapshots from this schedule (legacy)
    /// Deprecated: Use timeline_retention instead
    #[serde(default)]
    pub keep_days: u32,

    /// Timeline-based retention policy
    /// If None, falls back to keep_count and keep_days for backward compatibility
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeline_retention: Option<TimelineRetention>,
}

impl Schedule {
    /// Create a default hourly schedule (disabled)
    pub fn default_hourly() -> Self {
        Self {
            enabled: false,
            scope: ScheduleScope::System,
            notify_on_create: true,
            schedule_type: ScheduleType::Hourly,
            time: None,
            day_of_week: None,
            day_of_month: None,
            prefix: "hourly".to_string(),
            description: "Hourly automatic recovery point".to_string(),
            keep_count: 24,
            keep_days: 1,
            timeline_retention: Some(TimelineRetention::for_hourly()),
        }
    }

    /// Create a default daily schedule (enabled)
    pub fn default_daily() -> Self {
        Self {
            enabled: false,
            scope: ScheduleScope::System,
            notify_on_create: true,
            schedule_type: ScheduleType::Daily,
            time: Some("02:00".to_string()),
            day_of_week: None,
            day_of_month: None,
            prefix: "daily".to_string(),
            description: "Daily automatic recovery point".to_string(),
            keep_count: 7,
            keep_days: 7,
            timeline_retention: Some(TimelineRetention::for_daily()),
        }
    }

    /// Create a default weekly schedule (disabled)
    pub fn default_weekly() -> Self {
        Self {
            enabled: false,
            scope: ScheduleScope::System,
            notify_on_create: true,
            schedule_type: ScheduleType::Weekly,
            time: Some("03:00".to_string()),
            day_of_week: Some(0), // Sunday
            day_of_month: None,
            prefix: "weekly".to_string(),
            description: "Weekly automatic recovery point".to_string(),
            keep_count: 4,
            keep_days: 28,
            timeline_retention: Some(TimelineRetention::for_weekly()),
        }
    }

    /// Create a default monthly schedule (disabled)
    pub fn default_monthly() -> Self {
        Self {
            enabled: false,
            scope: ScheduleScope::System,
            notify_on_create: true,
            schedule_type: ScheduleType::Monthly,
            time: Some("04:00".to_string()),
            day_of_week: None,
            day_of_month: Some(1), // First of month
            prefix: "monthly".to_string(),
            description: "Monthly automatic recovery point".to_string(),
            keep_count: 3,
            keep_days: 90,
            timeline_retention: Some(TimelineRetention::for_monthly()),
        }
    }

    /// Hourly personal-file history with a broad timeline. It remains opt-in,
    /// but is present in the shipped configuration so enabling it is one click.
    pub fn default_personal_hourly() -> Self {
        Self {
            enabled: false,
            scope: ScheduleScope::Personal,
            notify_on_create: true,
            schedule_type: ScheduleType::Hourly,
            time: None,
            day_of_week: None,
            day_of_month: None,
            prefix: "personal-hourly".to_string(),
            description: "Hourly Personal Files history".to_string(),
            keep_count: 24,
            keep_days: 1,
            timeline_retention: Some(TimelineRetention {
                hourly_limit: 24,
                daily_limit: 7,
                weekly_limit: 4,
                monthly_limit: 6,
                yearly_limit: 0,
            }),
        }
    }

    /// Validate this schedule configuration
    pub fn validate(&self) -> Result<(), String> {
        // Validate time format if present
        if let Some(ref time) = self.time
            && !is_valid_time_format(time)
        {
            return Err(format!(
                "Invalid time format '{time}'. Expected HH:MM (24-hour)"
            ));
        }

        crate::validate_snapshot_name(&self.prefix)
            .map_err(|error| format!("Invalid schedule prefix: {error}"))?;
        if self.prefix.chars().count() > 40 {
            return Err("Schedule prefix must not exceed 40 characters".to_string());
        }
        if self.description.trim().is_empty()
            || self.description.chars().count() > 200
            || self.description.chars().any(char::is_control)
        {
            return Err("Schedule description must contain 1-200 printable characters".to_string());
        }
        if self.keep_count > 1_000 || self.keep_days > 3_650 {
            return Err("Schedule retention is outside the supported range".to_string());
        }
        if let Some(timeline) = &self.timeline_retention
            && [
                timeline.hourly_limit,
                timeline.daily_limit,
                timeline.weekly_limit,
                timeline.monthly_limit,
                timeline.yearly_limit,
            ]
            .into_iter()
            .any(|limit| limit > 10_000)
        {
            return Err("Timeline retention is outside the supported range".to_string());
        }

        // Validate day_of_week if present
        if let Some(day) = self.day_of_week
            && day > 6
        {
            return Err(format!("Invalid day_of_week {day}. Must be 0-6 (0=Sunday)"));
        }

        // Validate day_of_month if present
        if let Some(day) = self.day_of_month
            && !(1..=31).contains(&day)
        {
            return Err(format!("Invalid day_of_month {day}. Must be 1-31"));
        }

        // Type-specific validations
        match self.schedule_type {
            ScheduleType::Hourly => {
                // Hourly doesn't need time/day
            }
            ScheduleType::Daily => {
                if self.time.is_none() {
                    return Err("Daily schedule requires 'time' field".to_string());
                }
            }
            ScheduleType::Weekly => {
                if self.time.is_none() {
                    return Err("Weekly schedule requires 'time' field".to_string());
                }
                if self.day_of_week.is_none() {
                    return Err("Weekly schedule requires 'day_of_week' field".to_string());
                }
            }
            ScheduleType::Monthly => {
                if self.time.is_none() {
                    return Err("Monthly schedule requires 'time' field".to_string());
                }
                if self.day_of_month.is_none() {
                    return Err("Monthly schedule requires 'day_of_month' field".to_string());
                }
            }
        }

        Ok(())
    }
}

const fn default_notify_on_create() -> bool {
    true
}

/// Container for all snapshot schedules
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SchedulesConfig {
    #[serde(rename = "schedule")]
    pub schedules: Vec<Schedule>,
}

impl Default for SchedulesConfig {
    fn default() -> Self {
        Self {
            schedules: vec![
                Schedule::default_hourly(),
                Schedule::default_daily(),
                Schedule::default_weekly(),
                Schedule::default_monthly(),
                Schedule::default_personal_hourly(),
            ],
        }
    }
}

impl SchedulesConfig {
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.schedules.len() > 8 {
            anyhow::bail!("At most eight recovery schedules are supported");
        }
        let mut types = HashSet::new();
        let mut prefixes = HashSet::new();
        for schedule in &self.schedules {
            schedule
                .validate()
                .map_err(|error| anyhow::anyhow!(error))?;
            if !types.insert((schedule.scope, schedule.schedule_type.as_str())) {
                anyhow::bail!("Recovery schedule types must be unique within each scope");
            }
            if !prefixes.insert(schedule.prefix.as_str()) {
                anyhow::bail!("Recovery schedule prefixes must be unique");
            }
        }
        Ok(())
    }

    /// Load schedules from a TOML file
    pub fn load_from_file(path: &PathBuf) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: SchedulesConfig = toml::from_str(&content)?;

        config.validate()?;

        Ok(config)
    }

    /// Save schedules to a TOML file
    pub fn save_to_file(&self, path: &PathBuf) -> anyhow::Result<()> {
        self.validate()?;

        let content = toml::to_string_pretty(self)?;

        let parent = path
            .parent()
            .ok_or_else(|| anyhow::anyhow!("Schedules path has no parent"))?;
        let parent_metadata = fs::symlink_metadata(parent)?;
        if !parent_metadata.file_type().is_dir() {
            anyhow::bail!("Schedules parent is not a real directory");
        }
        match fs::symlink_metadata(path) {
            Ok(metadata) if metadata.file_type().is_file() => {}
            Ok(_) => anyhow::bail!("Schedules target is not a regular file"),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        let temporary = parent.join(format!(
            ".schedules.{}.{}.tmp",
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
        let result = (|| {
            file.write_all(content.as_bytes())?;
            file.sync_all()?;
            fs::set_permissions(&temporary, fs::Permissions::from_mode(0o644))?;
            fs::rename(&temporary, path)?;
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
                .open(parent)?
                .sync_all()
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result?;
        Ok(())
    }

    /// Get all enabled schedules
    pub fn enabled_schedules(&self) -> Vec<&Schedule> {
        self.schedules.iter().filter(|s| s.enabled).collect()
    }

    /// Get schedule by type
    pub fn get_schedule(&self, schedule_type: ScheduleType) -> Option<&Schedule> {
        self.schedules
            .iter()
            .find(|s| s.scope == ScheduleScope::System && s.schedule_type == schedule_type)
    }

    /// Get mutable schedule by type
    pub fn get_schedule_mut(&mut self, schedule_type: ScheduleType) -> Option<&mut Schedule> {
        self.schedules
            .iter_mut()
            .find(|s| s.scope == ScheduleScope::System && s.schedule_type == schedule_type)
    }
}

/// Validate time format (HH:MM in 24-hour format)
fn is_valid_time_format(time: &str) -> bool {
    let parts: Vec<&str> = time.split(':').collect();
    if parts.len() != 2 {
        return false;
    }

    let hour: Result<u8, _> = parts[0].parse();
    let minute: Result<u8, _> = parts[1].parse();

    match (hour, minute) {
        (Ok(h), Ok(m)) => h < 24 && m < 60,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_schedules() {
        let config = SchedulesConfig::default();
        assert_eq!(config.schedules.len(), 5);

        // Every default is fail-safe and requires explicit opt-in.
        let daily = config.get_schedule(ScheduleType::Daily).unwrap();
        assert!(!daily.enabled);
        assert_eq!(daily.prefix, "daily");

        // Others should be disabled
        let hourly = config.get_schedule(ScheduleType::Hourly).unwrap();
        assert!(!hourly.enabled);
        assert!(hourly.notify_on_create);
        let personal = config
            .schedules
            .iter()
            .find(|schedule| schedule.scope == ScheduleScope::Personal)
            .unwrap();
        assert_eq!(personal.prefix, "personal-hourly");
        assert_eq!(
            personal.timeline_retention.as_ref().unwrap().monthly_limit,
            6
        );
    }

    #[test]
    fn test_time_validation() {
        assert!(is_valid_time_format("00:00"));
        assert!(is_valid_time_format("12:30"));
        assert!(is_valid_time_format("23:59"));
        assert!(!is_valid_time_format("24:00"));
        assert!(!is_valid_time_format("12:60"));
        assert!(!is_valid_time_format("12"));
        assert!(!is_valid_time_format("12:30:00"));
    }

    #[test]
    fn test_schedule_validation() {
        let mut schedule = Schedule::default_daily();
        assert!(schedule.validate().is_ok());

        // Invalid time
        schedule.time = Some("25:00".to_string());
        assert!(schedule.validate().is_err());

        // Missing required time for daily
        schedule.time = None;
        assert!(schedule.validate().is_err());
    }

    #[test]
    fn test_toml_serialization() {
        let config = SchedulesConfig::default();
        let toml = toml::to_string(&config).unwrap();

        assert!(toml.contains("[[schedule]]"));
        assert!(toml.contains("type = \"daily\""));
        assert!(toml.contains("enabled = false"));
        assert!(toml.contains("notify_on_create = true"));
    }

    #[test]
    fn old_configs_default_creation_notifications_to_enabled() {
        let serialized = toml::to_string(&SchedulesConfig::default()).unwrap();
        let old_style = serialized
            .lines()
            .filter(|line| !line.starts_with("notify_on_create = "))
            .collect::<Vec<_>>()
            .join("\n");
        let parsed: SchedulesConfig = toml::from_str(&old_style).unwrap();
        assert!(
            parsed
                .schedules
                .iter()
                .all(|schedule| schedule.notify_on_create)
        );

        let mut explicit = SchedulesConfig::default();
        explicit.schedules[0].notify_on_create = false;
        let parsed: SchedulesConfig = toml::from_str(&toml::to_string(&explicit).unwrap()).unwrap();
        assert!(!parsed.schedules[0].notify_on_create);
    }

    #[test]
    fn test_enabled_schedules() {
        let config = SchedulesConfig::default();
        let enabled = config.enabled_schedules();

        assert!(enabled.is_empty());
    }

    #[test]
    fn rejects_duplicate_types_and_prefixes() {
        let mut config = SchedulesConfig::default();
        config.schedules[1].schedule_type = ScheduleType::Hourly;
        assert!(config.validate().is_err());

        let mut config = SchedulesConfig::default();
        config.schedules[1].prefix = config.schedules[0].prefix.clone();
        assert!(config.validate().is_err());
    }

    #[test]
    fn rejects_removed_free_form_subvolume_field() {
        let serialized = toml::to_string(&SchedulesConfig::default()).unwrap();
        let injected = serialized.replacen(
            "prefix = \"hourly\"",
            "prefix = \"hourly\"\nsubvolumes = [\"/home\"]",
            1,
        );
        assert!(toml::from_str::<SchedulesConfig>(&injected).is_err());
    }

    #[test]
    fn rejects_unbounded_or_unsafe_display_fields() {
        let mut config = SchedulesConfig::default();
        config.schedules[0].description = "bad\ndescription".into();
        assert!(config.validate().is_err());

        let mut config = SchedulesConfig::default();
        config.schedules[0].keep_count = 1_001;
        assert!(config.validate().is_err());
    }
}
