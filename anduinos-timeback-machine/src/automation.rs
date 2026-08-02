use std::collections::HashSet;
use chrono::{DateTime, Datelike, Duration, Local, Utc};
use serde::{Deserialize, Serialize};

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

impl AutomaticPolicy {
    pub fn system_preset() -> Self { Self::preset(120) }
    pub fn home_preset() -> Self { Self::preset(60) }
    fn preset(interval_minutes: u32) -> Self { Self { enabled: false, interval_minutes, keep_all_hours: 24, keep_daily_days: 7, keep_weekly_days: 30, keep_monthly_days: 365, delete_after_days: 365 } }
    pub fn validate(&self) -> Result<(), &'static str> {
        if !(15..=43_200).contains(&self.interval_minutes) { return Err("snapshot interval must be between 15 minutes and 30 days"); }
        if self.keep_daily_days < 1 || self.keep_weekly_days < self.keep_daily_days || self.keep_monthly_days < self.keep_weekly_days || self.delete_after_days < self.keep_monthly_days { return Err("retention boundaries must increase from daily through final deletion"); }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AutomaticSnapshot { pub id: String, pub created_at: DateTime<Utc>, pub protected: bool, pub successful: bool }

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KeepReason { Recent, DailyFirst, WeeklyFirst, MonthlyFirst, Protected, NotSuccessful }

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RetentionDecision { pub id: String, pub keep: bool, pub reason: Option<KeepReason> }

/// Pure, deterministic tiered down-sampling. Buckets use local civil time and
/// ISO weeks (Monday first); the earliest successful snapshot wins each bucket.
pub fn plan(policy: &AutomaticPolicy, now: DateTime<Utc>, snapshots: &[AutomaticSnapshot]) -> Result<Vec<RetentionDecision>, &'static str> {
    policy.validate()?;
    let mut ordered: Vec<_> = snapshots.iter().collect();
    ordered.sort_by(|a,b| a.created_at.cmp(&b.created_at).then_with(|| a.id.cmp(&b.id)));
    let mut buckets = HashSet::new();
    let mut result = Vec::with_capacity(ordered.len());
    for snapshot in ordered {
        let age = now.signed_duration_since(snapshot.created_at);
        let (keep, reason) = if snapshot.protected { (true, Some(KeepReason::Protected)) }
        else if !snapshot.successful { (true, Some(KeepReason::NotSuccessful)) }
        else if age < Duration::hours(i64::from(policy.keep_all_hours)) { (true, Some(KeepReason::Recent)) }
        else if age < Duration::days(i64::from(policy.keep_daily_days)) { bucket(&mut buckets, snapshot.created_at, 'd', KeepReason::DailyFirst) }
        else if age < Duration::days(i64::from(policy.keep_weekly_days)) { bucket(&mut buckets, snapshot.created_at, 'w', KeepReason::WeeklyFirst) }
        else if age < Duration::days(i64::from(policy.keep_monthly_days)) { bucket(&mut buckets, snapshot.created_at, 'm', KeepReason::MonthlyFirst) }
        else { (false, None) };
        result.push(RetentionDecision { id: snapshot.id.clone(), keep, reason });
    }
    Ok(result)
}

fn bucket(seen: &mut HashSet<String>, time: DateTime<Utc>, tier: char, reason: KeepReason) -> (bool, Option<KeepReason>) {
    let local = time.with_timezone(&Local);
    let key = match tier { 'd' => format!("d:{:04}-{:02}-{:02}", local.year(), local.month(), local.day()), 'w' => { let w=local.iso_week(); format!("w:{}-{}", w.year(), w.week()) }, _ => format!("m:{:04}-{:02}", local.year(), local.month()) };
    if seen.insert(key) { (true, Some(reason)) } else { (false, None) }
}

pub fn next_run(last_success: Option<DateTime<Utc>>, now: DateTime<Utc>, policy: &AutomaticPolicy) -> Option<DateTime<Utc>> {
    if !policy.enabled || policy.validate().is_err() { return None; }
    Some(last_success.map(|last| last + Duration::minutes(i64::from(policy.interval_minutes))).unwrap_or(now))
}

#[cfg(test)] mod tests { use super::*; #[test] fn presets_are_opt_in() { assert!(!AutomaticPolicy::system_preset().enabled); assert_eq!(AutomaticPolicy::home_preset().interval_minutes, 60); } #[test] fn protected_is_never_deleted() { let now=Utc::now(); let s=AutomaticSnapshot{id:"x".into(),created_at:now-Duration::days(800),protected:true,successful:true}; assert!(plan(&AutomaticPolicy::system_preset(),now,&[s]).unwrap()[0].keep); } }
