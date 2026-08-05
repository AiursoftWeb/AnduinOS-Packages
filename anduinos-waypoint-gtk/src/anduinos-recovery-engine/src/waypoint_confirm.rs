use std::process::ExitCode;

use anduinos_recovery_engine::confirmation::{ConfirmationEngine, ConfirmationOutcome};

fn main() -> ExitCode {
    match ConfirmationEngine::default().reconcile() {
        Ok(ConfirmationOutcome::NoAction) => ExitCode::SUCCESS,
        Ok(ConfirmationOutcome::Confirmed) => {
            eprintln!("AnduinOS Waypoint rollback confirmed");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::RevertedRecorded) => {
            eprintln!("AnduinOS Waypoint automatic fallback recorded");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("AnduinOS Waypoint confirmation failed: {error}");
            ExitCode::FAILURE
        }
    }
}
