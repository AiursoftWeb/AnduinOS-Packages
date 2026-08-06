//! Disk Snapshots Manager 2.0's deterministic, side-effect-free GFS retention policy.
//!
//! This module deliberately knows nothing about Btrfs, D-Bus, GTK, or file
//! deletion.  It only turns immutable snapshot facts into explainable keep or
//! delete decisions.  The privileged execution layer must re-check leases and
//! restore references immediately before consuming a `Delete` decision.

use std::cmp::Reverse;
use std::collections::HashSet;

use chrono::{DateTime, Datelike, Duration, FixedOffset, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum CleanupPolicy {
    #[default]
    Automatic,
    KeepForever,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionPolicy {
    pub is_auto_snapshot_enabled: bool,
    #[serde(default = "default_snapshot_interval_hours")]
    pub snapshot_interval_hours: u32,
    pub is_auto_cleanup_enabled: bool,
    pub keep_all_hours: u32,
    pub keep_daily_days: u32,
    pub keep_weekly_days: u32,
    pub keep_monthly_days: u32,
    pub keep_yearly: bool,
}

impl Default for RetentionPolicy {
    fn default() -> Self {
        Self {
            is_auto_snapshot_enabled: true,
            snapshot_interval_hours: 1,
            is_auto_cleanup_enabled: true,
            keep_all_hours: 24,
            keep_daily_days: 7,
            keep_weekly_days: 30,
            keep_monthly_days: 365,
            keep_yearly: true,
        }
    }
}

impl RetentionPolicy {
    pub fn system_default() -> Self {
        Self {
            snapshot_interval_hours: 24,
            ..Self::default()
        }
    }

    pub fn home_default() -> Self {
        Self {
            snapshot_interval_hours: 2,
            ..Self::default()
        }
    }

    pub fn validate(&self) -> Result<(), RetentionPolicyError> {
        if !(1..=24).contains(&self.snapshot_interval_hours) {
            return Err(RetentionPolicyError::InvalidSnapshotInterval);
        }
        if self.keep_all_hours == 0
            || self.keep_daily_days == 0
            || self.keep_weekly_days == 0
            || self.keep_monthly_days == 0
        {
            return Err(RetentionPolicyError::ZeroBoundary);
        }
        if u64::from(self.keep_all_hours) > u64::from(self.keep_daily_days) * 24
            || self.keep_daily_days > self.keep_weekly_days
            || self.keep_weekly_days > self.keep_monthly_days
        {
            return Err(RetentionPolicyError::NonMonotonicBoundaries);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetentionPolicyError {
    ZeroBoundary,
    NonMonotonicBoundaries,
    InvalidLocalOffset,
    InvalidSnapshotInterval,
}

impl std::fmt::Display for RetentionPolicyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ZeroBoundary => {
                write!(formatter, "retention boundaries must be greater than zero")
            }
            Self::NonMonotonicBoundaries => {
                write!(
                    formatter,
                    "retention boundaries must increase monotonically"
                )
            }
            Self::InvalidLocalOffset => write!(formatter, "snapshot local offset is invalid"),
            Self::InvalidSnapshotInterval => {
                write!(
                    formatter,
                    "snapshot interval must be between 1 and 24 hours"
                )
            }
        }
    }
}

const fn default_snapshot_interval_hours() -> u32 {
    1
}

impl std::error::Error for RetentionPolicyError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotCandidate {
    pub id: String,
    pub created_at: DateTime<Utc>,
    /// UTC offset captured when the snapshot was created.  Buckets remain
    /// stable even if the machine's timezone is changed later.
    pub local_offset_seconds: i32,
    pub cleanup_policy: CleanupPolicy,
    pub is_ready: bool,
    pub is_busy: bool,
    pub is_restore_referenced: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum RetentionTier {
    None,
    Yearly,
    Monthly,
    Weekly,
    Daily,
    All,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetentionAction {
    Keep,
    Delete,
    Skip,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetentionReason {
    AllWithinRecentWindow,
    NewestInDay,
    NewestInWeek,
    NewestInMonth,
    NewestInYear,
    BucketAlreadyOccupied,
    AutomaticCleanupDisabled,
    KeepForever,
    FutureTimestamp,
    NotReady,
    Busy,
    RestoreReferenced,
    LastUsableSnapshot,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetentionDecision {
    pub snapshot_id: String,
    pub tier: RetentionTier,
    pub action: RetentionAction,
    pub reason: RetentionReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct DayKey(i32, u32, u32);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct WeekKey(i32, u32);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct MonthKey(i32, u32);

/// Evaluate one volume's snapshots. Call this independently for System and
/// Home; sharing bucket sets across scopes would incorrectly couple them.
pub fn evaluate_retention(
    snapshots: &[SnapshotCandidate],
    policy: &RetentionPolicy,
    now: DateTime<Utc>,
) -> Result<Vec<RetentionDecision>, RetentionPolicyError> {
    policy.validate()?;

    let mut ordered = snapshots.iter().collect::<Vec<_>>();
    // `sort_by_key` is stable: equal timestamps preserve discovery order.
    ordered.sort_by_key(|snapshot| Reverse(snapshot.created_at));

    let mut seen_days = HashSet::new();
    let mut seen_weeks = HashSet::new();
    let mut seen_months = HashSet::new();
    let mut seen_years = HashSet::new();
    let mut decisions = Vec::with_capacity(ordered.len());

    for snapshot in ordered {
        let decision = if !snapshot.is_ready {
            decision(
                snapshot,
                RetentionAction::Skip,
                RetentionTier::None,
                RetentionReason::NotReady,
            )
        } else if snapshot.is_busy {
            decision(
                snapshot,
                RetentionAction::Skip,
                RetentionTier::None,
                RetentionReason::Busy,
            )
        } else if snapshot.is_restore_referenced {
            decision(
                snapshot,
                RetentionAction::Skip,
                RetentionTier::None,
                RetentionReason::RestoreReferenced,
            )
        } else if snapshot.cleanup_policy == CleanupPolicy::KeepForever {
            decision(
                snapshot,
                RetentionAction::Keep,
                RetentionTier::None,
                RetentionReason::KeepForever,
            )
        } else if !policy.is_auto_cleanup_enabled {
            decision(
                snapshot,
                RetentionAction::Keep,
                RetentionTier::None,
                RetentionReason::AutomaticCleanupDisabled,
            )
        } else {
            let age = now.signed_duration_since(snapshot.created_at);
            if age < Duration::zero() {
                decision(
                    snapshot,
                    RetentionAction::Keep,
                    RetentionTier::None,
                    RetentionReason::FutureTimestamp,
                )
            } else if age < Duration::hours(i64::from(policy.keep_all_hours)) {
                decision(
                    snapshot,
                    RetentionAction::Keep,
                    RetentionTier::All,
                    RetentionReason::AllWithinRecentWindow,
                )
            } else {
                let offset = FixedOffset::east_opt(snapshot.local_offset_seconds)
                    .ok_or(RetentionPolicyError::InvalidLocalOffset)?;
                let local = snapshot.created_at.with_timezone(&offset);
                if age < Duration::days(i64::from(policy.keep_daily_days)) {
                    let key = DayKey(local.year(), local.month(), local.day());
                    bucket_decision(snapshot, RetentionTier::Daily, seen_days.insert(key))
                } else if age < Duration::days(i64::from(policy.keep_weekly_days)) {
                    let week = local.iso_week();
                    let key = WeekKey(week.year(), week.week());
                    bucket_decision(snapshot, RetentionTier::Weekly, seen_weeks.insert(key))
                } else if age < Duration::days(i64::from(policy.keep_monthly_days)) {
                    let key = MonthKey(local.year(), local.month());
                    bucket_decision(snapshot, RetentionTier::Monthly, seen_months.insert(key))
                } else if policy.keep_yearly {
                    bucket_decision(
                        snapshot,
                        RetentionTier::Yearly,
                        seen_years.insert(local.year()),
                    )
                } else {
                    decision(
                        snapshot,
                        RetentionAction::Delete,
                        RetentionTier::None,
                        RetentionReason::BucketAlreadyOccupied,
                    )
                }
            }
        };
        decisions.push(decision);
    }

    // Cleanup can never remove the last usable snapshot in a volume. Skipped
    // snapshots are not counted as a reliable recovery path.
    if !decisions
        .iter()
        .any(|decision| decision.action == RetentionAction::Keep)
        && let Some(newest) = decisions
            .iter_mut()
            .find(|decision| decision.action == RetentionAction::Delete)
    {
        newest.action = RetentionAction::Keep;
        newest.tier = RetentionTier::None;
        newest.reason = RetentionReason::LastUsableSnapshot;
    }

    Ok(decisions)
}

fn bucket_decision(
    snapshot: &SnapshotCandidate,
    tier: RetentionTier,
    claimed: bool,
) -> RetentionDecision {
    if claimed {
        let reason = match tier {
            RetentionTier::Daily => RetentionReason::NewestInDay,
            RetentionTier::Weekly => RetentionReason::NewestInWeek,
            RetentionTier::Monthly => RetentionReason::NewestInMonth,
            RetentionTier::Yearly => RetentionReason::NewestInYear,
            _ => unreachable!("only bucket tiers claim slots"),
        };
        decision(snapshot, RetentionAction::Keep, tier, reason)
    } else {
        decision(
            snapshot,
            RetentionAction::Delete,
            RetentionTier::None,
            RetentionReason::BucketAlreadyOccupied,
        )
    }
}

fn decision(
    snapshot: &SnapshotCandidate,
    action: RetentionAction,
    tier: RetentionTier,
    reason: RetentionReason,
) -> RetentionDecision {
    RetentionDecision {
        snapshot_id: snapshot.id.clone(),
        tier,
        action,
        reason,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn candidate(id: &str, created_at: DateTime<Utc>) -> SnapshotCandidate {
        SnapshotCandidate {
            id: id.into(),
            created_at,
            local_offset_seconds: 8 * 60 * 60,
            cleanup_policy: CleanupPolicy::Automatic,
            is_ready: true,
            is_busy: false,
            is_restore_referenced: false,
        }
    }

    fn decision_for<'a>(decisions: &'a [RetentionDecision], id: &str) -> &'a RetentionDecision {
        decisions
            .iter()
            .find(|decision| decision.snapshot_id == id)
            .unwrap()
    }

    #[test]
    fn defaults_are_the_product_policy() {
        let policy = RetentionPolicy::default();
        assert_eq!(policy.snapshot_interval_hours, 1);
        assert_eq!(policy.keep_all_hours, 24);
        assert_eq!(policy.keep_daily_days, 7);
        assert_eq!(policy.keep_weekly_days, 30);
        assert_eq!(policy.keep_monthly_days, 365);
        assert!(policy.keep_yearly);
        assert!(policy.validate().is_ok());
    }

    #[test]
    fn slot_filling_keeps_newest_snapshot_in_each_tier_bucket() {
        let now = Utc.with_ymd_and_hms(2026, 8, 6, 12, 0, 0).unwrap();
        let snapshots = vec![
            candidate("recent", now - Duration::hours(2)),
            candidate("daily-new", now - Duration::days(2)),
            candidate("daily-old", now - Duration::days(2) - Duration::hours(3)),
            candidate("weekly-new", now - Duration::days(10)),
            candidate("weekly-old", now - Duration::days(10) - Duration::hours(2)),
            candidate("monthly-new", now - Duration::days(50)),
            candidate("monthly-old", now - Duration::days(50) - Duration::hours(2)),
            candidate("yearly-new", now - Duration::days(500)),
            candidate("yearly-old", now - Duration::days(550)),
        ];
        let decisions = evaluate_retention(&snapshots, &RetentionPolicy::default(), now).unwrap();

        assert_eq!(decision_for(&decisions, "recent").tier, RetentionTier::All);
        assert_eq!(
            decision_for(&decisions, "daily-new").tier,
            RetentionTier::Daily
        );
        assert_eq!(
            decision_for(&decisions, "daily-old").action,
            RetentionAction::Delete
        );
        assert_eq!(
            decision_for(&decisions, "weekly-new").tier,
            RetentionTier::Weekly
        );
        assert_eq!(
            decision_for(&decisions, "weekly-old").action,
            RetentionAction::Delete
        );
        assert_eq!(
            decision_for(&decisions, "monthly-new").tier,
            RetentionTier::Monthly
        );
        assert_eq!(
            decision_for(&decisions, "monthly-old").action,
            RetentionAction::Delete
        );
        assert_eq!(
            decision_for(&decisions, "yearly-new").tier,
            RetentionTier::Yearly
        );
        assert_eq!(
            decision_for(&decisions, "yearly-old").action,
            RetentionAction::Delete
        );
    }

    #[test]
    fn permanent_busy_restore_and_future_snapshots_are_never_deleted() {
        let now = Utc.with_ymd_and_hms(2026, 8, 6, 12, 0, 0).unwrap();
        let mut permanent = candidate("permanent", now - Duration::days(900));
        permanent.cleanup_policy = CleanupPolicy::KeepForever;
        let mut busy = candidate("busy", now - Duration::days(900));
        busy.is_busy = true;
        let mut restore = candidate("restore", now - Duration::days(900));
        restore.is_restore_referenced = true;
        let future = candidate("future", now + Duration::hours(1));
        let decisions = evaluate_retention(
            &[permanent, busy, restore, future],
            &RetentionPolicy {
                keep_yearly: false,
                ..RetentionPolicy::default()
            },
            now,
        )
        .unwrap();

        assert_eq!(
            decision_for(&decisions, "permanent").action,
            RetentionAction::Keep
        );
        assert_eq!(
            decision_for(&decisions, "busy").action,
            RetentionAction::Skip
        );
        assert_eq!(
            decision_for(&decisions, "restore").action,
            RetentionAction::Skip
        );
        assert_eq!(
            decision_for(&decisions, "future").action,
            RetentionAction::Keep
        );
    }

    #[test]
    fn permanent_snapshot_does_not_occupy_an_automatic_bucket() {
        let now = Utc.with_ymd_and_hms(2026, 8, 6, 12, 0, 0).unwrap();
        let mut permanent = candidate("permanent", now - Duration::days(2));
        permanent.cleanup_policy = CleanupPolicy::KeepForever;
        let automatic = candidate("automatic", now - Duration::days(2) - Duration::hours(1));
        let decisions =
            evaluate_retention(&[permanent, automatic], &RetentionPolicy::default(), now).unwrap();
        assert_eq!(
            decision_for(&decisions, "automatic").tier,
            RetentionTier::Daily
        );
    }

    #[test]
    fn latest_usable_snapshot_survives_when_yearly_retention_is_disabled() {
        let now = Utc.with_ymd_and_hms(2026, 8, 6, 12, 0, 0).unwrap();
        let decisions = evaluate_retention(
            &[
                candidate("newest", now - Duration::days(800)),
                candidate("oldest", now - Duration::days(900)),
            ],
            &RetentionPolicy {
                keep_yearly: false,
                ..RetentionPolicy::default()
            },
            now,
        )
        .unwrap();
        assert_eq!(
            decision_for(&decisions, "newest").action,
            RetentionAction::Keep
        );
        assert_eq!(
            decision_for(&decisions, "newest").reason,
            RetentionReason::LastUsableSnapshot
        );
        assert_eq!(
            decision_for(&decisions, "oldest").action,
            RetentionAction::Delete
        );
    }

    #[test]
    fn invalid_non_monotonic_policy_is_rejected() {
        assert_eq!(
            RetentionPolicy {
                keep_daily_days: 40,
                keep_weekly_days: 30,
                ..RetentionPolicy::default()
            }
            .validate(),
            Err(RetentionPolicyError::NonMonotonicBoundaries)
        );
    }

    #[test]
    fn snapshot_interval_is_limited_to_one_through_twenty_four_hours() {
        assert_eq!(
            RetentionPolicy {
                snapshot_interval_hours: 0,
                ..RetentionPolicy::default()
            }
            .validate(),
            Err(RetentionPolicyError::InvalidSnapshotInterval)
        );
        assert_eq!(
            RetentionPolicy {
                snapshot_interval_hours: 25,
                ..RetentionPolicy::default()
            }
            .validate(),
            Err(RetentionPolicyError::InvalidSnapshotInterval)
        );
    }
}
