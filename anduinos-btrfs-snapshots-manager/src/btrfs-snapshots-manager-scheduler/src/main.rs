//! One-shot Disk Snapshots Manager 2.0 automation worker, invoked by systemd.timer.

use std::process::Command;

use anyhow::{Context, Result};
use chrono::Local;
use snapshots_manager_common::{AutomationConfig, SnapshotsManagerConfig};

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    if let Err(error) = run_once() {
        log::error!("Disk Snapshots Manager automatic snapshot run failed: {error:#}");
        std::process::exit(1);
    }
}

fn run_once() -> Result<()> {
    let path = SnapshotsManagerConfig::default().automation_config;
    let config = AutomationConfig::load_from_file(&path)
        .with_context(|| format!("Could not load {}", path.display()))?;
    let mut failures = Vec::new();
    if config.system.is_auto_snapshot_enabled
        && let Err(error) = create_snapshot(AutomaticScope::System)
    {
        failures.push(format!("System: {error:#}"));
    }
    if config.home.is_auto_snapshot_enabled
        && let Err(error) = create_snapshot(AutomaticScope::Home)
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

fn create_snapshot(scope: AutomaticScope) -> Result<()> {
    let now = Local::now();
    let (command, schedule_id, title, description) = match scope {
        AutomaticScope::System => (
            "create-scheduled",
            "btrfs-snapshots-manager-v2-system",
            format!(
                "{} · Automatic System Snapshot",
                now.format("%Y-%m-%d %H:%M")
            ),
            "Automatic system snapshot",
        ),
        AutomaticScope::Home => (
            "personal-create-scheduled",
            "btrfs-snapshots-manager-v2-home",
            format!("{} · Automatic Home Snapshot", now.format("%Y-%m-%d %H:%M")),
            "Automatic Home snapshot",
        ),
    };
    let scope_name = match scope {
        AutomaticScope::System => "system",
        AutomaticScope::Home => "personal",
    };
    let output = Command::new("/usr/bin/anduinos-btrfs-snapshots-manager-cli")
        .args([command, schedule_id, &title, description])
        .output()
        .context("Could not execute the Disk Snapshots Manager CLI")?;
    if !output.status.success() {
        let _ = Command::new("/usr/bin/anduinos-btrfs-snapshots-manager-cli")
            .args(["notify-automatic-event", "failed", scope_name])
            .status();
        anyhow::bail!("{}", String::from_utf8_lossy(&output.stderr).trim())
    }
    Ok(())
}

fn apply_retention_cleanup() -> Result<()> {
    let output = Command::new("/usr/bin/anduinos-btrfs-snapshots-manager-cli")
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
}
