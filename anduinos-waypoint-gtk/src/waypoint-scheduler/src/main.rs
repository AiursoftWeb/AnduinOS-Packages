// Waypoint Snapshot Scheduler - Rust Implementation
// Manages multiple concurrent snapshot schedules using a thread-per-schedule model

use anyhow::{Context, Result};
use chrono::{Datelike, Duration as ChronoDuration, Local, NaiveDate, TimeZone, Timelike};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use waypoint_common::{Schedule, ScheduleType, SchedulesConfig, WaypointConfig};

fn main() {
    // Initialize logging
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("================================================");
    log::info!("AnduinOS Waypoint scheduler starting");
    log::info!("================================================");

    // Load configuration
    // This service runs as root, so inherited environment variables must not redirect its
    // configuration or privileged unit name.
    let config = WaypointConfig::default();
    log::info!("Schedules config: {}", config.schedules_config.display());

    // Shared mutex to ensure only one snapshot creation happens at a time
    let snapshot_lock = Arc::new(Mutex::new(()));

    // Main service loop - monitors config and spawns schedule threads
    loop {
        match run_scheduler(&config, Arc::clone(&snapshot_lock)) {
            Ok(_) => {
                // Should never return normally, but if it does, restart
                log::warn!("Scheduler thread manager exited unexpectedly, restarting...");
            }
            Err(e) => {
                log::error!("Scheduler error: {e}");
                log::info!("Will retry in 60 seconds...");
            }
        }
        thread::sleep(Duration::from_secs(60));
    }
}

/// Main scheduler - spawns one thread per enabled schedule
fn run_scheduler(config: &WaypointConfig, snapshot_lock: Arc<Mutex<()>>) -> Result<()> {
    // Load schedules
    let schedules = load_schedules(config)?;

    // Get enabled schedules
    let enabled = schedules.enabled_schedules();

    if enabled.is_empty() {
        log::warn!("No schedules are enabled. Waiting 5 minutes before checking again...");
        thread::sleep(Duration::from_secs(300));
        return Ok(());
    }

    log::info!(
        "Starting scheduler threads for {} enabled schedule(s):",
        enabled.len()
    );
    for schedule in &enabled {
        log::info!(
            "  - {} ({}) - {}",
            schedule.prefix,
            schedule.schedule_type.as_str(),
            schedule.description
        );
    }

    // Spawn one thread per schedule
    let mut handles = vec![];

    for schedule in enabled {
        let schedule_clone = schedule.clone();
        let lock_clone = Arc::clone(&snapshot_lock);

        let handle = thread::spawn(move || {
            run_schedule_thread(schedule_clone, lock_clone);
        });

        handles.push(handle);
    }

    // Wait for all schedule threads to complete
    // (they should run indefinitely, but if any exits, we'll restart)
    for handle in handles {
        let _ = handle.join();
    }

    Ok(())
}

/// Run a single schedule thread - calculates next run, sleeps, creates snapshot, repeat
fn run_schedule_thread(schedule: Schedule, snapshot_lock: Arc<Mutex<()>>) {
    log::info!("[{}] Schedule thread started", schedule.prefix);

    loop {
        // Calculate when to run next
        match calculate_next_run(&schedule) {
            Ok(sleep_duration) => {
                log::info!(
                    "[{}] Next snapshot in {} ({})",
                    schedule.prefix,
                    format_duration(sleep_duration),
                    schedule.description
                );

                // Sleep until it's time
                thread::sleep(sleep_duration);

                // Acquire lock to ensure only one snapshot creation at a time
                let _lock = snapshot_lock.lock().unwrap();

                // Create the snapshot
                if let Err(e) = create_snapshot(&schedule) {
                    log::error!("[{}] Failed to create snapshot: {}", schedule.prefix, e);
                } else {
                    // Apply retention cleanup after successful snapshot creation
                    if let Err(e) = apply_retention_cleanup() {
                        log::warn!(
                            "[{}] Failed to apply retention cleanup: {}",
                            schedule.prefix,
                            e
                        );
                        // Don't fail the schedule thread if cleanup fails
                    }
                }

                // Release lock (happens automatically when _lock goes out of scope)
            }
            Err(e) => {
                log::error!(
                    "[{}] Failed to calculate next run time: {}",
                    schedule.prefix,
                    e
                );
                // Sleep for a bit before retrying
                thread::sleep(Duration::from_secs(60));
            }
        }
    }
}

/// Load schedules from configuration file
fn load_schedules(config: &WaypointConfig) -> Result<SchedulesConfig> {
    if !config.schedules_config.exists() {
        log::warn!(
            "Schedules config not found at {}. Using defaults.",
            config.schedules_config.display()
        );
        return Ok(SchedulesConfig::default());
    }

    SchedulesConfig::load_from_file(&config.schedules_config)
        .context("Failed to load schedules configuration")
}

/// Calculate duration until next run for a schedule
fn calculate_next_run(schedule: &Schedule) -> Result<Duration> {
    let now = Local::now();

    match schedule.schedule_type {
        ScheduleType::Hourly => {
            // Next hour
            let seconds_into_hour = now.minute() * 60 + now.second();
            let seconds_until_next_hour = 3600 - seconds_into_hour;
            Ok(Duration::from_secs(seconds_until_next_hour as u64))
        }

        ScheduleType::Daily => {
            let time = schedule
                .time
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("Daily schedule missing time"))?;

            calculate_next_daily(now, time)
        }

        ScheduleType::Weekly => {
            let time = schedule
                .time
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("Weekly schedule missing time"))?;

            let day_of_week = schedule
                .day_of_week
                .ok_or_else(|| anyhow::anyhow!("Weekly schedule missing day_of_week"))?;

            calculate_next_weekly(now, time, day_of_week)
        }

        ScheduleType::Monthly => {
            let time = schedule
                .time
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("Monthly schedule missing time"))?;

            let day_of_month = schedule
                .day_of_month
                .ok_or_else(|| anyhow::anyhow!("Monthly schedule missing day_of_month"))?;

            calculate_next_monthly(now, time, day_of_month)
        }
    }
}

/// Calculate next daily run time
fn calculate_next_daily(now: chrono::DateTime<Local>, time: &str) -> Result<Duration> {
    let (target_hour, target_min) = parse_schedule_time(time)?;

    let current_secs = now.hour() * 3600 + now.minute() * 60 + now.second();
    let target_secs = target_hour * 3600 + target_min * 60;

    let seconds = if current_secs < target_secs {
        // Later today
        target_secs - current_secs
    } else {
        // Tomorrow
        86400 - current_secs + target_secs
    };

    Ok(Duration::from_secs(seconds as u64))
}

/// Calculate next weekly run time
fn calculate_next_weekly(
    now: chrono::DateTime<Local>,
    time: &str,
    day_of_week: u8,
) -> Result<Duration> {
    let (target_hour, target_min) = parse_schedule_time(time)?;
    if day_of_week > 6 {
        anyhow::bail!("Weekly schedule day must be between 0 and 6");
    }

    let current_day = now.weekday().num_days_from_sunday();
    let target_day = day_of_week as u32;

    let mut days_until = if target_day >= current_day {
        target_day - current_day
    } else {
        7 - current_day + target_day
    };

    // If it's the target day but time has passed, wait until next week
    if days_until == 0 {
        let current_secs = now.hour() * 3600 + now.minute() * 60 + now.second();
        let target_secs = target_hour * 3600 + target_min * 60;

        if current_secs >= target_secs {
            days_until = 7;
        }
    }

    let current_secs = now.hour() * 3600 + now.minute() * 60 + now.second();
    let target_secs = target_hour * 3600 + target_min * 60;

    let seconds = if days_until == 0 {
        target_secs - current_secs
    } else {
        days_until * 86400 + target_secs - current_secs
    };

    Ok(Duration::from_secs(seconds as u64))
}

/// Calculate next monthly run time
fn calculate_next_monthly(
    now: chrono::DateTime<Local>,
    time: &str,
    day_of_month: u8,
) -> Result<Duration> {
    let (target_hour, target_min) = parse_schedule_time(time)?;
    if !(1..=31).contains(&day_of_month) {
        anyhow::bail!("Monthly schedule day must be between 1 and 31");
    }

    let requested_day = u32::from(day_of_month);
    let current_target = monthly_target(
        now.year(),
        now.month(),
        requested_day,
        target_hour,
        target_min,
    )?;
    let target = if current_target > now {
        current_target
    } else {
        let (year, month) = next_month(now.year(), now.month());
        monthly_target(year, month, requested_day, target_hour, target_min)?
    };
    target
        .signed_duration_since(now)
        .to_std()
        .context("Monthly schedule resolved to a time in the past")
}

fn parse_schedule_time(value: &str) -> Result<(u32, u32)> {
    let mut fields = value.split(':');
    let hour = fields
        .next()
        .ok_or_else(|| anyhow::anyhow!("Schedule time has no hour"))?
        .parse::<u32>()?;
    let minute = fields
        .next()
        .ok_or_else(|| anyhow::anyhow!("Schedule time has no minute"))?
        .parse::<u32>()?;
    if fields.next().is_some() || hour > 23 || minute > 59 {
        anyhow::bail!("Schedule time must use valid 24-hour HH:MM form");
    }
    Ok((hour, minute))
}

/// Resolve a requested day against a real calendar month. Day 31 means the
/// last day of shorter months, rather than drifting into the following month.
/// If a local wall-clock time does not exist during a DST transition, advance
/// to the first valid minute within the next three hours.
fn monthly_target(
    year: i32,
    month: u32,
    requested_day: u32,
    hour: u32,
    minute: u32,
) -> Result<chrono::DateTime<Local>> {
    let day = requested_day.min(last_day_of_month(year, month)?);
    let date = NaiveDate::from_ymd_opt(year, month, day)
        .ok_or_else(|| anyhow::anyhow!("Invalid monthly schedule date"))?;
    let naive = date
        .and_hms_opt(hour, minute, 0)
        .ok_or_else(|| anyhow::anyhow!("Invalid monthly schedule time"))?;
    for offset in 0..=180 {
        if let Some(target) = Local
            .from_local_datetime(&(naive + ChronoDuration::minutes(offset)))
            .earliest()
        {
            return Ok(target);
        }
    }
    anyhow::bail!("Monthly schedule time could not be represented locally")
}

fn last_day_of_month(year: i32, month: u32) -> Result<u32> {
    let (next_year, next_month) = next_month(year, month);
    NaiveDate::from_ymd_opt(next_year, next_month, 1)
        .and_then(|date| date.pred_opt())
        .map(|date| date.day())
        .ok_or_else(|| anyhow::anyhow!("Invalid monthly schedule month"))
}

fn next_month(year: i32, month: u32) -> (i32, u32) {
    if month == 12 {
        (year + 1, 1)
    } else {
        (year, month + 1)
    }
}

/// Create a snapshot for the given schedule
fn create_snapshot(schedule: &Schedule) -> Result<()> {
    waypoint_common::validate_snapshot_name(&schedule.prefix)
        .map_err(|e| anyhow::anyhow!("Invalid schedule prefix '{}': {}", schedule.prefix, e))?;
    let snapshot_name = format!("{}-{}", schedule.prefix, Local::now().format("%Y%m%d-%H%M"));

    log::info!(
        "[{}] Creating scheduled snapshot: {}",
        schedule.prefix,
        snapshot_name
    );

    // Call the packaged CLI to create a snapshot through the privileged helper.
    let output = Command::new("/usr/bin/anduinos-waypoint-cli")
        .arg("create-scheduled")
        .arg(&schedule.prefix)
        .arg(&snapshot_name)
        .arg(&schedule.description)
        .output()
        .context("Failed to execute anduinos-waypoint-cli")?;

    if output.status.success() {
        log::info!(
            "[{}] ✓ Snapshot created successfully: {}",
            schedule.prefix,
            snapshot_name
        );
        log::info!("[{}]   Scope: System", schedule.prefix);
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        log::error!(
            "[{}] ✗ Failed to create snapshot: {}",
            schedule.prefix,
            stderr
        );
        return Err(anyhow::anyhow!("Snapshot creation failed: {stderr}"));
    }

    Ok(())
}

/// Apply retention cleanup after creating a snapshot
fn apply_retention_cleanup() -> Result<()> {
    log::info!("Running retention cleanup...");

    // Call the packaged CLI to apply retention.
    let output = Command::new("anduinos-waypoint-cli")
        .arg("apply-retention")
        .output()
        .context("Failed to execute anduinos-waypoint-cli apply-retention")?;

    if output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        if !stdout.trim().is_empty() {
            log::info!("Retention cleanup: {}", stdout.trim());
        }
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        log::warn!("Retention cleanup warning: {stderr}");
        // Don't fail the entire operation if cleanup fails - just log it
    }

    Ok(())
}

/// Format duration into human-readable string
fn format_duration(duration: Duration) -> String {
    let secs = duration.as_secs();

    if secs < 60 {
        format!("{secs}s")
    } else if secs < 3600 {
        format!("{}m {}s", secs / 60, secs % 60)
    } else if secs < 86400 {
        format!("{}h {}m", secs / 3600, (secs % 3600) / 60)
    } else {
        format!("{}d {}h", secs / 86400, (secs % 86400) / 3600)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_only_valid_twenty_four_hour_times() {
        assert_eq!(parse_schedule_time("02:05").unwrap(), (2, 5));
        for invalid in ["", "2", "24:00", "12:60", "12:00:00", "xx:00"] {
            assert!(parse_schedule_time(invalid).is_err(), "accepted {invalid}");
        }
    }

    #[test]
    fn calendar_month_lengths_include_leap_years() {
        assert_eq!(last_day_of_month(2026, 2).unwrap(), 28);
        assert_eq!(last_day_of_month(2028, 2).unwrap(), 29);
        assert_eq!(last_day_of_month(2026, 4).unwrap(), 30);
        assert_eq!(last_day_of_month(2026, 12).unwrap(), 31);
        assert!(last_day_of_month(2026, 13).is_err());
    }

    #[test]
    fn december_rolls_into_the_next_year() {
        assert_eq!(next_month(2026, 11), (2026, 12));
        assert_eq!(next_month(2026, 12), (2027, 1));
    }

    #[test]
    fn day_thirty_one_clamps_to_the_real_month_end() {
        let february = monthly_target(2026, 2, 31, 3, 0).unwrap();
        assert_eq!(february.day(), 28);
        assert_eq!(february.month(), 2);
    }
}
