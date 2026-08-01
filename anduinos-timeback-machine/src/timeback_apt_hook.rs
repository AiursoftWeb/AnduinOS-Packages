use std::process::ExitCode;

use anduinos_timeback::package_hooks::{PackageHookEngine, PackageHookOutcome};
use anduinos_timeback::retention::RetentionCoordinator;

fn main() -> ExitCode {
    let argument = std::env::args().nth(1);
    let result = match argument.as_deref() {
        Some("pre") => PackageHookEngine::default().pre(),
        Some("post") => PackageHookEngine::default().post(),
        _ => {
            eprintln!("Usage: anduinos-timeback-apt-hook pre|post");
            return ExitCode::from(64);
        }
    };

    match result {
        Ok(outcome) => {
            eprintln!("AnduinOS Timeback package hook: {outcome:?}");
            if matches!(
                outcome,
                PackageHookOutcome::PostCaptured | PackageHookOutcome::InterruptedArchived
            ) {
                match RetentionCoordinator::default().apply() {
                    Ok(report) => eprintln!(
                        "AnduinOS Timeback retention: deleted {} automatic recovery point(s)",
                        report.deleted.len()
                    ),
                    Err(error) => eprintln!(
                        "AnduinOS Timeback retention warning ({}): {error}",
                        error.code.as_str()
                    ),
                }
            }
        }
        Err(error) => {
            // Package-manager availability is more important than an automatic
            // recovery point. The apt.conf wrapper is fail-open as a second
            // safety layer in case this helper cannot even be executed.
            eprintln!("AnduinOS Timeback package hook warning: {error}");
        }
    }
    ExitCode::SUCCESS
}
