use std::process::ExitCode;

use anduinos_recovery_engine::package_hook::PackageHookCoordinator;

fn main() -> ExitCode {
    let operation = match std::env::args().skip(1).collect::<Vec<_>>().as_slice() {
        [operation] if operation == "pre" || operation == "post" => operation.clone(),
        _ => {
            eprintln!("Usage: anduinos-waypoint-apt-hook pre|post");
            return ExitCode::from(64);
        }
    };

    let coordinator = PackageHookCoordinator::default();
    let result = match operation.as_str() {
        "pre" => coordinator.before_packages(),
        "post" => coordinator.after_packages(),
        _ => unreachable!("validated above"),
    };

    match result {
        Ok(transaction) => eprintln!(
            "AnduinOS Waypoint package hook {operation}: transaction {} is {:?}",
            transaction.id, transaction.phase
        ),
        Err(error) => {
            // A recovery point is valuable, but package-manager availability is
            // the stronger invariant. The apt.conf wrapper is a second
            // fail-open boundary if this program cannot be executed at all.
            eprintln!("AnduinOS Waypoint package hook warning: {error}");
        }
    }

    ExitCode::SUCCESS
}
