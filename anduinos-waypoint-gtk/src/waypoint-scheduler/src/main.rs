//! One-shot Waypoint 2.0 automation worker, invoked by systemd.timer.

use std::process::Command;

use anyhow::{Context, Result};
use chrono::{DateTime, Duration, Local, Utc};
use waypoint_common::{AutomationConfig, WaypointConfig};

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    if let Err(error) = run_once() {
        log::error!("Waypoint automatic snapshot run failed: {error:#}");
        std::process::exit(1);
    }
}

fn run_once() -> Result<()> {
    let path = WaypointConfig::default().automation_config;
    let config = AutomationConfig::load_from_file(&path)
        .with_context(|| format!("Could not load {}", path.display()))?;
    let mut failures = Vec::new();
    let status = if config.system.is_auto_snapshot_enabled || config.home.is_auto_snapshot_enabled {
        Some(load_recovery_status()?)
    } else {
        None
    };
    let now = Utc::now();
    if config.system.is_auto_snapshot_enabled
        && automatic_snapshot_due(
            status.as_ref().expect("enabled automation has status"),
            AutomaticScope::System,
            config.system.snapshot_interval_hours,
            now,
        )
        && let Err(error) = create_snapshot(
            AutomaticScope::System,
            config.notifications.notify_before_scheduled,
        )
    {
        failures.push(format!("System: {error:#}"));
    }
    if config.home.is_auto_snapshot_enabled
        && automatic_snapshot_due(
            status.as_ref().expect("enabled automation has status"),
            AutomaticScope::Home,
            config.home.snapshot_interval_hours,
            now,
        )
        && let Err(error) = create_snapshot(
            AutomaticScope::Home,
            config.notifications.notify_before_scheduled,
        )
    {
        failures.push(format!("Home: {error:#}"));
    }
    if (config.system.is_auto_cleanup_enabled || config.home.is_auto_cleanup_enabled)
        && let Err(error) = apply_retention_cleanup()
    {
        failures.push(format!("Smart Cleanup: {error:#}"));
    }
    if failures.is_empty() {
        Ok(())
    } else {
        anyhow::bail!(failures.join("\n"))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AutomaticScope {
    System,
    Home,
}

fn load_recovery_status() -> Result<serde_json::Value> {
    let output = Command::new("/usr/bin/anduinos-waypoint-cli")
        .args(["status", "--json"])
        .output()
        .context("Could not query Waypoint recovery status")?;
    if !output.status.success() {
        anyhow::bail!("{}", String::from_utf8_lossy(&output.stderr).trim());
    }
    serde_json::from_slice(&output.stdout).context("Waypoint returned invalid recovery status")
}

fn automatic_snapshot_due(
    status: &serde_json::Value,
    scope: AutomaticScope,
    interval_hours: u32,
    now: DateTime<Utc>,
) -> bool {
    let collection = match scope {
        AutomaticScope::System => "deployments",
        AutomaticScope::Home => "personal_snapshots",
    };
    let latest = status
        .get(collection)
        .and_then(serde_json::Value::as_array)
        .into_iter()
        .flatten()
        .filter(|snapshot| snapshot_satisfies_freshness(snapshot, scope))
        .filter_map(|snapshot| {
            snapshot
                .get("created_at")
                .and_then(serde_json::Value::as_str)
        })
        .filter_map(|created| DateTime::parse_from_rfc3339(created).ok())
        .map(|created| created.with_timezone(&Utc))
        .max();
    latest.is_none_or(|created| {
        now.signed_duration_since(created) >= Duration::hours(i64::from(interval_hours))
    })
}

fn snapshot_satisfies_freshness(snapshot: &serde_json::Value, scope: AutomaticScope) -> bool {
    let kind = snapshot.get("kind").and_then(serde_json::Value::as_str);
    let state = snapshot.get("state").and_then(serde_json::Value::as_str);
    match scope {
        AutomaticScope::System => {
            matches!(
                kind,
                Some("manual" | "automatic" | "apt-pre" | "apt-post" | "pre-rollback")
            ) && matches!(
                state,
                Some("ready" | "pending-rollback" | "fallback-protected")
            )
        }
        AutomaticScope::Home => {
            matches!(kind, Some("manual" | "automatic")) && state == Some("ready")
        }
    }
}

fn create_snapshot(scope: AutomaticScope, notify_before: bool) -> Result<()> {
    let now = Local::now();
    let (command, schedule_id, title, description) = match scope {
        AutomaticScope::System => (
            "create-scheduled",
            "waypoint-v2-system",
            format!(
                "{} · Automatic System Snapshot",
                now.format("%Y-%m-%d %H:%M")
            ),
            "Automatic system snapshot",
        ),
        AutomaticScope::Home => (
            "personal-create-scheduled",
            "waypoint-v2-home",
            format!("{} · Automatic Home Snapshot", now.format("%Y-%m-%d %H:%M")),
            "Automatic Home snapshot",
        ),
    };
    let scope_name = match scope {
        AutomaticScope::System => "system",
        AutomaticScope::Home => "personal",
    };
    if notify_before {
        let _ = Command::new("/usr/bin/anduinos-waypoint-cli")
            .args(["notify-automatic-event", "starting", scope_name])
            .status();
        std::thread::sleep(std::time::Duration::from_secs(10));
    }
    let output = Command::new("/usr/bin/anduinos-waypoint-cli")
        .args([command, schedule_id, &title, description])
        .output()
        .context("Could not execute the Waypoint CLI")?;
    if !output.status.success() {
        let _ = Command::new("/usr/bin/anduinos-waypoint-cli")
            .args(["notify-automatic-event", "failed", scope_name])
            .status();
        anyhow::bail!("{}", String::from_utf8_lossy(&output.stderr).trim())
    }
    Ok(())
}

fn apply_retention_cleanup() -> Result<()> {
    let output = Command::new("/usr/bin/anduinos-waypoint-cli")
        .arg("apply-retention")
        .output()
        .context("Could not start Smart Cleanup")?;
    if !output.status.success() {
        anyhow::bail!("{}", String::from_utf8_lossy(&output.stderr).trim())
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_product_scopes_are_distinct() {
        assert!(matches!(AutomaticScope::System, AutomaticScope::System));
        assert!(matches!(AutomaticScope::Home, AutomaticScope::Home));
    }

    #[test]
    fn each_scope_obeys_its_own_hourly_interval() {
        let now = DateTime::parse_from_rfc3339("2026-08-06T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let status = serde_json::json!({
            "deployments": [{
                "kind": "automatic",
                "state": "ready",
                "schedule_id": "waypoint-v2-system",
                "created_at": "2026-08-06T10:00:01Z"
            }],
            "personal_snapshots": [{
                "kind": "automatic",
                "state": "ready",
                "schedule_id": "waypoint-v2-home",
                "created_at": "2026-08-06T11:00:00Z"
            }]
        });
        assert!(!automatic_snapshot_due(
            &status,
            AutomaticScope::System,
            2,
            now
        ));
        assert!(automatic_snapshot_due(
            &status,
            AutomaticScope::Home,
            1,
            now
        ));
    }

    #[test]
    fn missing_automatic_snapshot_is_due_immediately() {
        assert!(automatic_snapshot_due(
            &serde_json::json!({}),
            AutomaticScope::System,
            24,
            Utc::now()
        ));
    }

    #[test]
    fn recent_manual_snapshot_satisfies_freshness_but_import_does_not() {
        let now = DateTime::parse_from_rfc3339("2026-08-06T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let manual = serde_json::json!({
            "deployments": [{
                "kind": "manual",
                "state": "ready",
                "created_at": "2026-08-06T11:30:00Z"
            }]
        });
        assert!(!automatic_snapshot_due(
            &manual,
            AutomaticScope::System,
            1,
            now
        ));
        let imported = serde_json::json!({
            "deployments": [{
                "kind": "imported",
                "state": "ready",
                "created_at": "2026-08-06T11:30:00Z"
            }]
        });
        assert!(automatic_snapshot_due(
            &imported,
            AutomaticScope::System,
            1,
            now
        ));
    }
}
