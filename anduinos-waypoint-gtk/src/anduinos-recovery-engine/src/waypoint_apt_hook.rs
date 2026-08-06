use std::process::ExitCode;

use anduinos_recovery_engine::{AptSnapshotPolicy, package_hook::PackageHookCoordinator};

fn main() -> ExitCode {
    let operation = match std::env::args().skip(1).collect::<Vec<_>>().as_slice() {
        [operation] if operation == "pre" || operation == "post" => operation.clone(),
        _ => {
            eprintln!("Usage: anduinos-waypoint-apt-hook pre|post");
            return ExitCode::from(64);
        }
    };

    let coordinator = PackageHookCoordinator::default();
    let policy = AptSnapshotPolicy::load_from_file(&AptSnapshotPolicy::system_path())
        .unwrap_or_else(|error| {
            eprintln!(
                "AnduinOS Waypoint package hook warning: {error}; using the safe pre-only default"
            );
            AptSnapshotPolicy::default()
        });
    let result = match operation.as_str() {
        "pre" => coordinator.before_packages_with_policy(policy),
        "post" => coordinator.after_packages_with_policy(policy),
        _ => unreachable!("validated above"),
    };

    match result {
        Ok(Some(transaction)) => eprintln!(
            "AnduinOS Waypoint package hook {operation}: transaction {} is {:?}",
            transaction.id, transaction.phase
        ),
        Ok(None) => eprintln!("AnduinOS Waypoint package hook {operation}: disabled by policy"),
        Err(error) => {
            // A recovery point is valuable, but package-manager availability is
            // the stronger invariant. The apt.conf wrapper is a second
            // fail-open boundary if this program cannot be executed at all.
            eprintln!("AnduinOS Waypoint package hook warning: {error}");
        }
    }

    ExitCode::SUCCESS
}
