use std::process::ExitCode;

use anduinos_timeback::automatic_home::{HomeSnapshotKind, HomeSnapshotStore};
use anduinos_timeback::automation::{
    plan, AutomaticSnapshot, AutomaticStore, AutomaticTarget, TargetAutomaticStatus,
};
use anduinos_timeback::layout;
use anduinos_timeback::maintenance::{run_maintenance, MaintenanceOutcome};
use anduinos_timeback::model::{DeploymentKind, DeploymentState};
use anduinos_timeback::operations::OperationEngine;
use anduinos_timeback::retention::RetentionCoordinator;
use anduinos_timeback::store::DeploymentStore;
use chrono::Utc;

fn main() -> ExitCode {
    run_automatic_snapshots();
    match run_maintenance(&RetentionCoordinator::default()) {
        MaintenanceOutcome::UnsupportedLayout => {
            eprintln!("AnduinOS Timeback maintenance: unsupported layout; skipped");
        }
        MaintenanceOutcome::Healthy {
            available_bytes,
            free_space_target_bytes,
        } => {
            eprintln!(
                "AnduinOS Timeback maintenance: healthy ({available_bytes} bytes available; \
                 target {free_space_target_bytes})"
            );
        }
        MaintenanceOutcome::PressureBlocked {
            available_bytes,
            free_space_target_bytes,
        } => {
            eprintln!(
                "AnduinOS Timeback maintenance warning: space pressure remains \
                 ({available_bytes} bytes available; target {free_space_target_bytes}); \
                 no automatic recovery point is safely eligible"
            );
        }
        MaintenanceOutcome::Cleaned {
            report,
            pressure_remaining,
        } => {
            eprintln!(
                "AnduinOS Timeback maintenance: deleted {} automatic recovery point(s)",
                report.deleted.len()
            );
            if pressure_remaining {
                eprintln!(
                    "AnduinOS Timeback maintenance warning: space pressure remains after safe cleanup"
                );
            }
        }
        MaintenanceOutcome::Warning { code, message } => {
            eprintln!(
                "AnduinOS Timeback maintenance warning ({}): {message}",
                code.as_str()
            );
        }
    }

    // Automatic maintenance is deliberately fail-open. A damaged recovery
    // catalog must be repaired, but it must not make the installed OS or the
    // systemd timer unhealthy.
    ExitCode::SUCCESS
}

fn run_automatic_snapshots() {
    let store = AutomaticStore::default();
    let now = Utc::now();
    let status = match store.status(now) {
        Ok(status) => status,
        Err(error) => {
            eprintln!("AnduinOS Timeback automatic snapshot warning: {error}");
            return;
        }
    };
    run_system_snapshots(&store, now, &status.system, &status.configuration.system);
    run_home_snapshots(&store, now, &status.home, &status.configuration.home);
}

fn run_system_snapshots(
    store: &AutomaticStore,
    now: chrono::DateTime<Utc>,
    status: &TargetAutomaticStatus,
    policy: &anduinos_timeback::automation::AutomaticPolicy,
) {
    if status.next_run.is_some_and(|next| next <= now) {
        let result = OperationEngine::default()
            .create_automatic(&layout::inspect_current(), |_, _, _| {})
            .map(|record| {
                eprintln!(
                    "AnduinOS Timeback automatic System snapshot: created {}",
                    record.id
                );
                Utc::now()
            })
            .map_err(|error| error.to_string());
        if let Err(error) = &result {
            eprintln!("AnduinOS Timeback automatic System snapshot warning: {error}");
        }
        if let Err(error) = store.record_result(AutomaticTarget::System, now, result) {
            eprintln!("AnduinOS Timeback automatic System state warning: {error}");
        }
    }

    let report = DeploymentStore::default().discover();
    if !report.issues.is_empty() {
        let message =
            "Automatic System cleanup skipped because the recovery catalog has unresolved issues";
        eprintln!("AnduinOS Timeback automatic cleanup warning: {message}");
        let _ = store.record_cleanup_result(AutomaticTarget::System, Err(message.into()));
        return;
    }
    let snapshots = report
        .deployments
        .iter()
        .filter(|record| record.kind == DeploymentKind::Automatic)
        .map(|record| AutomaticSnapshot {
            id: record.id.to_string(),
            created_at: record.created_at,
            protected: record.pinned || record.state.protects_from_deletion(),
            successful: record.failure.is_none()
                && record.state != DeploymentState::Broken
                && record.state != DeploymentState::Incomplete,
        })
        .collect::<Vec<_>>();
    let decisions = match plan(policy, now, &snapshots) {
        Ok(plan) => plan,
        Err(error) => {
            let _ = store.record_cleanup_result(AutomaticTarget::System, Err(error.into()));
            return;
        }
    };
    let engine = OperationEngine::default();
    let current_layout = layout::inspect_current();
    let mut cleanup_error = None;
    for decision in decisions.into_iter().filter(|decision| !decision.keep) {
        let Some(record) = report
            .deployments
            .iter()
            .find(|record| record.id.to_string() == decision.id)
        else {
            continue;
        };
        if let Err(error) = engine.delete_automatic(&current_layout, record.id, 1) {
            cleanup_error = Some(format!(
                "Could not clean automatic System recovery point {}: {error}",
                record.id
            ));
        }
    }
    let _ = store.record_cleanup_result(AutomaticTarget::System, cleanup_error.map_or(Ok(()), Err));
}

fn run_home_snapshots(
    store: &AutomaticStore,
    now: chrono::DateTime<Utc>,
    status: &TargetAutomaticStatus,
    policy: &anduinos_timeback::automation::AutomaticPolicy,
) {
    let snapshots = HomeSnapshotStore::default();
    if status.next_run.is_some_and(|next| next <= now) {
        let result = snapshots.create(&layout::inspect_current()).map(|record| {
            eprintln!(
                "AnduinOS Timeback automatic Home snapshot: created {}",
                record.id
            );
            Utc::now()
        });
        if let Err(error) = &result {
            eprintln!("AnduinOS Timeback automatic Home snapshot warning: {error}");
        }
        if let Err(error) = store.record_result(AutomaticTarget::Home, now, result) {
            eprintln!("AnduinOS Timeback automatic Home state warning: {error}");
        }
    }

    let mut records = match snapshots.discover() {
        Ok(records) => records,
        Err(error) => {
            let _ = store.record_cleanup_result(AutomaticTarget::Home, Err(error));
            return;
        }
    };
    for record in records.iter().filter(|record| record.deleting) {
        if let Err(error) = snapshots.delete(record.id) {
            let message = format!(
                "Could not finish deleting automatic Home snapshot {}: {error}",
                record.id
            );
            let _ = store.record_cleanup_result(AutomaticTarget::Home, Err(message));
            return;
        }
    }
    if records.iter().any(|record| record.deleting) {
        records = match snapshots.discover() {
            Ok(records) => records,
            Err(error) => {
                let _ = store.record_cleanup_result(AutomaticTarget::Home, Err(error));
                return;
            }
        };
    }
    let catalog = automatic_home_catalog(&records);
    let decisions = match plan(policy, now, &catalog) {
        Ok(decisions) => decisions,
        Err(error) => {
            let _ = store.record_cleanup_result(AutomaticTarget::Home, Err(error.into()));
            return;
        }
    };
    let mut cleanup_error = None;
    for decision in decisions.into_iter().filter(|decision| !decision.keep) {
        let Some(record) = records
            .iter()
            .find(|record| record.id.to_string() == decision.id)
        else {
            continue;
        };
        if let Err(error) = snapshots.delete(record.id) {
            cleanup_error = Some(format!(
                "Could not clean automatic Home snapshot {}: {error}",
                record.id
            ));
            break;
        }
    }
    let _ = store.record_cleanup_result(AutomaticTarget::Home, cleanup_error.map_or(Ok(()), Err));
}

fn automatic_home_catalog(
    records: &[anduinos_timeback::automatic_home::HomeSnapshotRecord],
) -> Vec<AutomaticSnapshot> {
    records
        .iter()
        .filter(|record| record.kind == HomeSnapshotKind::Automatic)
        .map(|record| AutomaticSnapshot {
            id: record.id.to_string(),
            created_at: record.created_at,
            protected: record.pinned,
            successful: true,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use anduinos_timeback::automatic_home::{HomeSnapshotKind, HomeSnapshotRecord};
    use chrono::Utc;
    use uuid::Uuid;

    use super::*;

    fn home_record(kind: HomeSnapshotKind, pinned: bool) -> HomeSnapshotRecord {
        HomeSnapshotRecord {
            schema_version: 2,
            id: Uuid::new_v4(),
            created_at: Utc::now(),
            deleting: false,
            kind,
            title: "Snapshot".into(),
            reason: "Retention test".into(),
            pinned,
            system_recovery_point_id: None,
        }
    }

    #[test]
    fn automatic_retention_never_considers_manual_home_snapshots() {
        let automatic = home_record(HomeSnapshotKind::Automatic, false);
        let pinned = home_record(HomeSnapshotKind::Automatic, true);
        let manual = home_record(HomeSnapshotKind::Manual, false);
        let catalog = automatic_home_catalog(&[automatic.clone(), pinned.clone(), manual]);

        assert_eq!(catalog.len(), 2);
        assert_eq!(catalog[0].id, automatic.id.to_string());
        assert!(!catalog[0].protected);
        assert_eq!(catalog[1].id, pinned.id.to_string());
        assert!(catalog[1].protected);
    }
}
