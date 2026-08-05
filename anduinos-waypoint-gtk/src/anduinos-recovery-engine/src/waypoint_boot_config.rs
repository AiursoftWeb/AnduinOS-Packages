use std::process::ExitCode;

use anduinos_recovery_engine::boot::BootIntegration;

fn main() -> ExitCode {
    match BootIntegration::default().recovery_menu_entry() {
        Ok(Some(entry)) => {
            print!("{entry}");
            ExitCode::SUCCESS
        }
        Ok(None) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!(
                "AnduinOS Waypoint could not generate its recovery boot entry: {}",
                error.message
            );
            ExitCode::FAILURE
        }
    }
}
