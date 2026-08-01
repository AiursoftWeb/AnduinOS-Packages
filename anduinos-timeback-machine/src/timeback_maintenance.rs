use std::process::ExitCode;

use anduinos_timeback::maintenance::{run_maintenance, MaintenanceOutcome};
use anduinos_timeback::retention::RetentionCoordinator;

fn main() -> ExitCode {
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
