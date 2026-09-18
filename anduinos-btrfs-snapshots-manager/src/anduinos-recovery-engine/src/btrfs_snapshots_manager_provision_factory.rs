use std::process::ExitCode;

use anduinos_recovery_engine::{
    layout,
    model::DeploymentKind,
    operations::{FactorySnapshotOutcome, OperationEngine},
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
    match OperationEngine::default().create_factory_if_missing(
        &layout::inspect_current(),
        |_, _, message| {
            eprintln!("{message}");
        },
    ) {
        Ok(FactorySnapshotOutcome::Created(record)) => {
            println!("created {}", record.id);
            ExitCode::SUCCESS
        }
        Ok(FactorySnapshotOutcome::Existing(record)) => {
            println!("existing {}", record.id);
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("Could not provision factory recovery: {error}");
            ExitCode::FAILURE
        }
    }
}

fn check() -> ExitCode {
    let report = DeploymentStore::default().discover();
    if !report.issues.is_empty() {
        eprintln!("Factory recovery metadata has unresolved issues");
        return ExitCode::FAILURE;
    }
    let factories = report
        .deployments
        .iter()
        .filter(|record| record.kind == DeploymentKind::Factory)
        .collect::<Vec<_>>();
    let [factory] = factories.as_slice() else {
        eprintln!("Exactly one factory recovery point is required");
        return ExitCode::FAILURE;
    };
    if !factory.pinned || !factory.can_restore() {
        eprintln!("The factory recovery point is not healthy and protected");
        return ExitCode::FAILURE;
    }
    match OperationEngine::default().check_available(&layout::inspect_current(), factory.id) {
        Ok(_) => {
            println!("ready {}", factory.id);
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("Factory recovery verification failed: {error}");
            ExitCode::FAILURE
        }
    }
}
