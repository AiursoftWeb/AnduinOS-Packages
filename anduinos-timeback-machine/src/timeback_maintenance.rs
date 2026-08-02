use std::process::ExitCode;

use chrono::Utc;
use anduinos_timeback::automation::{plan, AutomaticSnapshot, AutomaticStore};
use anduinos_timeback::layout;
use anduinos_timeback::model::{DeploymentKind, DeploymentState};
use anduinos_timeback::maintenance::{run_maintenance, MaintenanceOutcome};
use anduinos_timeback::operations::OperationEngine;
use anduinos_timeback::retention::RetentionCoordinator;
use anduinos_timeback::store::DeploymentStore;

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
    let store=AutomaticStore::default();
    let now=Utc::now();
    let status=match store.status(now) { Ok(status) => status, Err(error) => { eprintln!("AnduinOS Timeback automatic snapshot warning: {error}"); return; } };
    if status.next_run.is_some_and(|next| next <= now) {
        let result=OperationEngine::default().create_automatic(&layout::inspect_current(), |_,_,_| {}).map(|record| { eprintln!("AnduinOS Timeback automatic snapshot: created {}", record.id); Utc::now() }).map_err(|error| error.to_string());
        if let Err(error)=&result { eprintln!("AnduinOS Timeback automatic snapshot warning: {error}"); }
        if let Err(error)=store.record_result(now,result) { eprintln!("AnduinOS Timeback automatic state warning: {error}"); }
    }
    let report=DeploymentStore::default().discover();
    if !report.issues.is_empty() { let message="Automatic cleanup skipped because the recovery catalog has unresolved issues"; eprintln!("AnduinOS Timeback automatic cleanup warning: {message}"); let _=store.record_error(message); return; }
    let snapshots=report.deployments.iter().filter(|record| record.kind == DeploymentKind::Automatic).map(|record| AutomaticSnapshot { id: record.id.to_string(), created_at: record.created_at, protected: record.pinned || record.state.protects_from_deletion(), successful: record.failure.is_none() && record.state != DeploymentState::Broken && record.state != DeploymentState::Incomplete }).collect::<Vec<_>>();
    let decisions=match plan(&status.policy,now,&snapshots) { Ok(plan) => plan, Err(error) => { eprintln!("AnduinOS Timeback automatic cleanup warning: {error}"); let _=store.record_error(error); return; } };
    let engine=OperationEngine::default();
    let current_layout=layout::inspect_current();
    for decision in decisions.into_iter().filter(|decision| !decision.keep) {
        let Some(record)=report.deployments.iter().find(|record| record.id.to_string() == decision.id) else { continue };
        match engine.delete_automatic(&current_layout,record.id,1) { Ok(()) => eprintln!("AnduinOS Timeback automatic cleanup: deleted {}",record.id), Err(error) => { eprintln!("AnduinOS Timeback automatic cleanup warning for {}: {error}",record.id); let _=store.record_error(format!("Could not clean automatic recovery point {}: {error}",record.id)); } }
    }
}
