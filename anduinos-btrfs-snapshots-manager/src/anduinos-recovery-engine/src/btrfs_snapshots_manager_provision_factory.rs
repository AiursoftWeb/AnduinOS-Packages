use std::process::ExitCode;

use anduinos_recovery_engine::{
    layout,
    model::DeploymentKind,
    operations::{FactorySnapshotOutcome, OperationEngine},
    personal::{FactoryHomeSnapshotOutcome, PersonalSnapshotEngine, PersonalSnapshotKind},
    store::DeploymentStore,
};

fn main() -> ExitCode {
    if unsafe { libc::geteuid() } != 0 {
        eprintln!("Factory recovery provisioning must run as root");
        return ExitCode::from(77);
    }

    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    match arguments.as_slice() {
        [] => provision(),
        [argument] if argument == "--check" => check(),
        _ => {
            eprintln!("Usage: anduinos-btrfs-snapshots-manager-provision-factory [--check]");
            ExitCode::from(64)
        }
    }
}

fn provision() -> ExitCode {
    let layout = layout::inspect_current();
    let root = OperationEngine::default().create_factory_if_missing(&layout, |_, _, message| {
        eprintln!("{message}");
    });
    let root = match root {
        Ok(FactorySnapshotOutcome::Created(record)) => ("created", record.id),
        Ok(FactorySnapshotOutcome::Existing(record)) => ("existing", record.id),
        Err(error) => {
            eprintln!("Could not provision factory recovery: {error}");
            return ExitCode::FAILURE;
        }
    };
    let home = match PersonalSnapshotEngine::default().create_factory_if_missing(&layout) {
        Ok(FactoryHomeSnapshotOutcome::Created(record)) => ("created", record.id),
        Ok(FactoryHomeSnapshotOutcome::Existing(record)) => ("existing", record.id),
        Err(error) => {
            eprintln!("Could not provision factory Home recovery: {error}");
            return ExitCode::FAILURE;
        }
    };
    println!("root {} {}; home {} {}", root.0, root.1, home.0, home.1);
    ExitCode::SUCCESS
}

fn check_home(layout: &layout::LayoutReport) -> Result<String, String> {
    let engine = PersonalSnapshotEngine::default();
    let report = engine.discover();
    if !report.issues.is_empty() {
        return Err("Factory Home recovery metadata has unresolved issues".into());
    }
    let factories = report
        .snapshots
        .iter()
        .filter(|record| record.kind == PersonalSnapshotKind::Factory)
        .collect::<Vec<_>>();
    let [factory] = factories.as_slice() else {
        return Err("Exactly one factory Home recovery point is required".into());
    };
    engine
        .verify(layout, factory.id)
        .map(|record| record.id.to_string())
        .map_err(|error| format!("Factory Home recovery verification failed: {error}"))
}

fn check_root(layout: &layout::LayoutReport) -> Result<String, String> {
    let report = DeploymentStore::default().discover();
    if !report.issues.is_empty() {
        return Err("Factory recovery metadata has unresolved issues".into());
    }
    let factories = report
        .deployments
        .iter()
        .filter(|record| record.kind == DeploymentKind::Factory)
        .collect::<Vec<_>>();
    let [factory] = factories.as_slice() else {
        return Err("Exactly one factory recovery point is required".into());
    };
    if !factory.pinned || !factory.can_restore() {
        return Err("The factory recovery point is not healthy and protected".into());
    }
    OperationEngine::default()
        .check_available(layout, factory.id)
        .map(|record| record.id.to_string())
        .map_err(|error| format!("Factory recovery verification failed: {error}"))
}

fn check() -> ExitCode {
    let layout = layout::inspect_current();
    match (check_root(&layout), check_home(&layout)) {
        (Ok(root), Ok(home)) => {
            println!("ready root {root}; home {home}");
            ExitCode::SUCCESS
        }
        (root, home) => {
            if let Err(error) = root {
                eprintln!("{error}");
            }
            if let Err(error) = home {
                eprintln!("{error}");
            }
            ExitCode::FAILURE
        }
    }
}
