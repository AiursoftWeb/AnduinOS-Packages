use std::collections::HashSet;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Datelike, Duration, Local, Utc};
use serde::{Deserialize, Serialize};

pub const AUTOMATIC_CONFIGURATION_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum AutomaticTarget {
    System,
    Home,
}

impl AutomaticTarget {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::System => "system",
            Self::Home => "home",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AutomaticPolicy {
    pub enabled: bool,
    pub interval_minutes: u32,
    pub keep_all_hours: u32,
    pub keep_daily_days: u32,
    pub keep_weekly_days: u32,
    pub keep_monthly_days: u32,
    pub delete_after_days: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AutomaticConfiguration {
    pub schema_version: u32,
    pub policies_linked: bool,
    pub system: AutomaticPolicy,
    pub home: AutomaticPolicy,
}

impl Default for AutomaticConfiguration {
    fn default() -> Self {
        Self {
            schema_version: AUTOMATIC_CONFIGURATION_SCHEMA_VERSION,
            policies_linked: false,
            system: AutomaticPolicy::system_preset(),
            home: AutomaticPolicy::home_preset(),
        }
    }
}

impl AutomaticConfiguration {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.schema_version != AUTOMATIC_CONFIGURATION_SCHEMA_VERSION {
            return Err("unsupported automatic configuration schema version");
        }
        self.system.validate()?;
        self.home.validate()?;
        if self.policies_linked && self.system != self.home {
            return Err("linked system and home policies must be identical");
        }
        Ok(())
    }

    pub fn policy(&self, target: AutomaticTarget) -> &AutomaticPolicy {
        match target {
            AutomaticTarget::System => &self.system,
            AutomaticTarget::Home => &self.home,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TargetAutomaticStatus {
    pub last_success: Option<DateTime<Utc>>,
    pub last_attempt: Option<DateTime<Utc>>,
    pub last_error: Option<String>,
    pub next_run: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AutomaticStatus {
    pub configuration: AutomaticConfiguration,
    pub system: TargetAutomaticStatus,
    pub home: TargetAutomaticStatus,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
struct AutomaticState {
    last_success: Option<DateTime<Utc>>,
    last_attempt: Option<DateTime<Utc>>,
    last_error: Option<String>,
    #[serde(default)]
    cleanup_error: Option<String>,
}

pub struct AutomaticStore {
    directory: PathBuf,
}

impl Default for AutomaticStore {
    fn default() -> Self {
        Self::new("/var/lib/anduinos-timeback-machine")
    }
}

impl AutomaticStore {
    pub fn new(directory: impl Into<PathBuf>) -> Self {
        Self {
            directory: directory.into(),
        }
    }

    pub fn configuration(&self) -> io::Result<AutomaticConfiguration> {
        let configuration = match read_json(&self.directory.join("automatic-configuration.json")) {
            Ok(configuration) => Ok(configuration),
            Err(error) if error.kind() == io::ErrorKind::NotFound => self.legacy_configuration(),
            Err(error) => Err(error),
        }?;
        configuration
            .validate()
            .map_err(|message| io::Error::new(io::ErrorKind::InvalidData, message))?;
        Ok(configuration)
    }

    pub fn set_configuration(&self, configuration: &AutomaticConfiguration) -> io::Result<()> {
        configuration
            .validate()
            .map_err(|message| io::Error::new(io::ErrorKind::InvalidInput, message))?;
        write_json(
            &self.directory.join("automatic-configuration.json"),
            configuration,
        )
    }

    pub fn status(&self, now: DateTime<Utc>) -> io::Result<AutomaticStatus> {
        let configuration = self.configuration()?;
        Ok(AutomaticStatus {
            system: self.target_status(AutomaticTarget::System, &configuration.system, now)?,
            home: self.target_status(AutomaticTarget::Home, &configuration.home, now)?,
            configuration,
        })
    }

    pub fn record_result(
        &self,
        target: AutomaticTarget,
        attempted: DateTime<Utc>,
        result: Result<DateTime<Utc>, String>,
    ) -> io::Result<()> {
        let mut state = self.state(target)?;
        state.last_attempt = Some(attempted);
        match result {
            Ok(success) => {
                state.last_success = Some(success);
                state.last_error = None;
            }
            Err(error) => state.last_error = Some(error),
        }
        self.set_state(target, &state)
    }

    pub fn record_cleanup_result(
        &self,
        target: AutomaticTarget,
        result: Result<(), String>,
    ) -> io::Result<()> {
        let mut state = self.state(target)?;
        state.cleanup_error = result.err();
        self.set_state(target, &state)
    }

    fn legacy_configuration(&self) -> io::Result<AutomaticConfiguration> {
        let system = match read_json(&self.directory.join("automatic-policy.json")) {
            Ok(policy) => policy,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                AutomaticPolicy::system_preset()
            }
            Err(error) => return Err(error),
        };
        Ok(AutomaticConfiguration {
            system,
            ..AutomaticConfiguration::default()
        })
    }

    fn target_status(
        &self,
        target: AutomaticTarget,
        policy: &AutomaticPolicy,
        now: DateTime<Utc>,
    ) -> io::Result<TargetAutomaticStatus> {
        let state = self.state(target)?;
        Ok(TargetAutomaticStatus {
            next_run: next_run(state.last_success, now, policy),
            last_success: state.last_success,
            last_attempt: state.last_attempt,
            last_error: state.cleanup_error.or(state.last_error),
        })
    }

    fn state(&self, target: AutomaticTarget) -> io::Result<AutomaticState> {
        let path = self
            .directory
            .join(format!("automatic-{}-state.json", target.as_str()));
        match read_json(&path) {
            Ok(state) => Ok(state),
            Err(error)
                if error.kind() == io::ErrorKind::NotFound && target == AutomaticTarget::System =>
            {
                read_json(&self.directory.join("automatic-state.json")).or_else(|legacy_error| {
                    if legacy_error.kind() == io::ErrorKind::NotFound {
                        Ok(AutomaticState::default())
                    } else {
                        Err(legacy_error)
                    }
                })
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(AutomaticState::default()),
            Err(error) => Err(error),
        }
    }

    fn set_state(&self, target: AutomaticTarget, state: &AutomaticState) -> io::Result<()> {
        write_json(
            &self
                .directory
                .join(format!("automatic-{}-state.json", target.as_str())),
            state,
        )
    }
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> io::Result<T> {
    serde_json::from_slice(&fs::read(path)?)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn write_json<T: Serialize>(path: &Path, value: &T) -> io::Result<()> {
    fs::create_dir_all(path.parent().expect("automatic state has a parent"))?;
    let temporary = path.with_extension("json.new");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(value)
            .map_err(|error| io::Error::new(io::ErrorKind::Other, error))?,
    )?;
    fs::rename(temporary, path)
}

impl AutomaticPolicy {
    pub fn system_preset() -> Self {
        Self::preset(120)
    }

    pub fn home_preset() -> Self {
        Self::preset(60)
    }

    fn preset(interval_minutes: u32) -> Self {
        Self {
            enabled: false,
            interval_minutes,
            keep_all_hours: 24,
            keep_daily_days: 7,
            keep_weekly_days: 30,
            keep_monthly_days: 365,
            delete_after_days: 365,
        }
    }

    pub fn validate(&self) -> Result<(), &'static str> {
        if !(15..=43_200).contains(&self.interval_minutes) {
            return Err("snapshot interval must be between 15 minutes and 30 days");
        }
        if self.keep_all_hours < 1 || self.keep_all_hours > 8_760 {
            return Err("the keep-all period must be between 1 hour and 1 year");
        }
        if self.delete_after_days > 36_500 {
            return Err("the final deletion period cannot exceed 100 years");
        }
        if self.keep_daily_days < 1
            || self.keep_weekly_days < self.keep_daily_days
            || self.keep_monthly_days < self.keep_weekly_days
            || self.delete_after_days < self.keep_monthly_days
        {
            return Err("retention boundaries must increase from daily through final deletion");
        }
        if u64::from(self.keep_all_hours) > u64::from(self.keep_daily_days) * 24 {
            return Err("the daily retention period must not end before the keep-all period");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AutomaticSnapshot {
    pub id: String,
    pub created_at: DateTime<Utc>,
    pub protected: bool,
    pub successful: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KeepReason {
    Recent,
    DailyFirst,
    WeeklyFirst,
    MonthlyFirst,
    YearlyFirst,
    Protected,
    NotSuccessful,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RetentionDecision {
    pub id: String,
    pub keep: bool,
    pub reason: Option<KeepReason>,
}

/// Pure, deterministic tiered down-sampling. Buckets use local civil time and
/// ISO weeks (Monday first); the earliest successful snapshot wins each bucket.
pub fn plan(
    policy: &AutomaticPolicy,
    now: DateTime<Utc>,
    snapshots: &[AutomaticSnapshot],
) -> Result<Vec<RetentionDecision>, &'static str> {
    policy.validate()?;
    let mut ordered: Vec<_> = snapshots.iter().collect();
    ordered.sort_by(|left, right| {
        left.created_at
            .cmp(&right.created_at)
            .then_with(|| left.id.cmp(&right.id))
    });
    let mut buckets = HashSet::new();
    let mut result = Vec::with_capacity(ordered.len());
    for snapshot in ordered {
        let age = now.signed_duration_since(snapshot.created_at);
        let (keep, reason) = if snapshot.protected {
            (true, Some(KeepReason::Protected))
        } else if !snapshot.successful {
            (true, Some(KeepReason::NotSuccessful))
        } else if age < Duration::hours(i64::from(policy.keep_all_hours)) {
            (true, Some(KeepReason::Recent))
        } else if age < Duration::days(i64::from(policy.keep_daily_days)) {
            bucket(
                &mut buckets,
                snapshot.created_at,
                'd',
                KeepReason::DailyFirst,
            )
        } else if age < Duration::days(i64::from(policy.keep_weekly_days)) {
            bucket(
                &mut buckets,
                snapshot.created_at,
                'w',
                KeepReason::WeeklyFirst,
            )
        } else if age < Duration::days(i64::from(policy.keep_monthly_days)) {
            bucket(
                &mut buckets,
                snapshot.created_at,
                'm',
                KeepReason::MonthlyFirst,
            )
        } else if age < Duration::days(i64::from(policy.delete_after_days)) {
            bucket(
                &mut buckets,
                snapshot.created_at,
                'y',
                KeepReason::YearlyFirst,
            )
        } else {
            (false, None)
        };
        result.push(RetentionDecision {
            id: snapshot.id.clone(),
            keep,
            reason,
        });
    }
    Ok(result)
}

fn bucket(
    seen: &mut HashSet<String>,
    time: DateTime<Utc>,
    tier: char,
    reason: KeepReason,
) -> (bool, Option<KeepReason>) {
    let local = time.with_timezone(&Local);
    let key = match tier {
        'd' => format!(
            "d:{:04}-{:02}-{:02}",
            local.year(),
            local.month(),
            local.day()
        ),
        'w' => {
            let week = local.iso_week();
            format!("w:{}-{}", week.year(), week.week())
        }
        'm' => format!("m:{:04}-{:02}", local.year(), local.month()),
        _ => format!("y:{:04}", local.year()),
    };
    if seen.insert(key) {
        (true, Some(reason))
    } else {
        (false, None)
    }
}

pub fn next_run(
    last_success: Option<DateTime<Utc>>,
    now: DateTime<Utc>,
    policy: &AutomaticPolicy,
) -> Option<DateTime<Utc>> {
    if !policy.enabled || policy.validate().is_err() {
        return None;
    }
    Some(
        last_success
            .map(|last| last + Duration::minutes(i64::from(policy.interval_minutes)))
            .unwrap_or(now),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presets_are_opt_in_and_independent() {
        let configuration = AutomaticConfiguration::default();
        assert!(!configuration.policies_linked);
        assert!(!configuration.system.enabled);
        assert_eq!(configuration.system.interval_minutes, 120);
        assert_eq!(configuration.home.interval_minutes, 60);
        assert_eq!(configuration.validate(), Ok(()));
    }

    #[test]
    fn linked_policies_must_match() {
        let mut configuration = AutomaticConfiguration::default();
        configuration.policies_linked = true;
        assert_eq!(
            configuration.validate(),
            Err("linked system and home policies must be identical")
        );
        configuration.home = configuration.system.clone();
        assert_eq!(configuration.validate(), Ok(()));
    }

    #[test]
    fn unknown_configuration_schema_is_never_accepted() {
        let mut configuration = AutomaticConfiguration::default();
        configuration.schema_version += 1;
        assert_eq!(
            configuration.validate(),
            Err("unsupported automatic configuration schema version")
        );
    }

    #[test]
    fn rejects_retention_windows_that_overlap_backwards() {
        let mut policy = AutomaticPolicy::system_preset();
        policy.keep_all_hours = 8 * 24;
        policy.keep_daily_days = 7;
        assert_eq!(
            policy.validate(),
            Err("the daily retention period must not end before the keep-all period")
        );

        policy = AutomaticPolicy::system_preset();
        policy.keep_weekly_days = 6;
        assert_eq!(
            policy.validate(),
            Err("retention boundaries must increase from daily through final deletion")
        );
    }

    #[test]
    fn protected_is_never_deleted() {
        let now = Utc::now();
        let snapshot = AutomaticSnapshot {
            id: "x".into(),
            created_at: now - Duration::days(800),
            protected: true,
            successful: true,
        };
        assert!(plan(&AutomaticPolicy::system_preset(), now, &[snapshot]).unwrap()[0].keep);
    }

    #[test]
    fn monthly_representatives_survive_until_final_deletion() {
        let now = Utc::now();
        let mut policy = AutomaticPolicy::system_preset();
        policy.keep_monthly_days = 365;
        policy.delete_after_days = 730;
        let snapshots = [
            AutomaticSnapshot {
                id: "within".into(),
                created_at: now - Duration::days(500),
                protected: false,
                successful: true,
            },
            AutomaticSnapshot {
                id: "expired".into(),
                created_at: now - Duration::days(731),
                protected: false,
                successful: true,
            },
        ];
        let decisions = plan(&policy, now, &snapshots).unwrap();
        assert!(
            decisions
                .iter()
                .find(|decision| decision.id == "within")
                .unwrap()
                .keep
        );
        assert!(
            !decisions
                .iter()
                .find(|decision| decision.id == "expired")
                .unwrap()
                .keep
        );
    }

    #[test]
    fn legacy_policy_and_state_are_migrated_on_read() {
        let directory = temporary_directory("migration");
        let mut legacy = AutomaticPolicy::system_preset();
        legacy.enabled = true;
        write_json(&directory.join("automatic-policy.json"), &legacy).unwrap();
        let now = Utc::now();
        write_json(
            &directory.join("automatic-state.json"),
            &AutomaticState {
                last_success: Some(now),
                ..AutomaticState::default()
            },
        )
        .unwrap();

        let status = AutomaticStore::new(&directory).status(now).unwrap();
        assert_eq!(status.configuration.system, legacy);
        assert_eq!(status.system.last_success, Some(now));
        assert_eq!(status.configuration.home, AutomaticPolicy::home_preset());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn target_errors_are_independent() {
        let directory = temporary_directory("state");
        let store = AutomaticStore::new(&directory);
        let now = Utc::now();
        store
            .record_result(AutomaticTarget::System, now, Err("system failed".into()))
            .unwrap();
        store
            .record_cleanup_result(AutomaticTarget::Home, Err("home cleanup failed".into()))
            .unwrap();

        let status = store.status(now).unwrap();
        assert_eq!(status.system.last_error.as_deref(), Some("system failed"));
        assert_eq!(
            status.home.last_error.as_deref(),
            Some("home cleanup failed")
        );
        fs::remove_dir_all(directory).unwrap();
    }

    fn temporary_directory(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "anduinos-timeback-automation-{label}-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ))
    }
}
