use std::process::ExitCode;

use anduinos_timeback::confirmation::{ConfirmationEngine, ConfirmationOutcome};

fn main() -> ExitCode {
    match ConfirmationEngine::default().reconcile() {
        Ok(ConfirmationOutcome::NoAction) => ExitCode::SUCCESS,
        Ok(ConfirmationOutcome::Confirmed) => {
            eprintln!("AnduinOS Timeback rollback confirmed");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::RevertedRecorded) => {
            eprintln!("AnduinOS Timeback automatic fallback recorded");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("AnduinOS Timeback confirmation failed: {error}");
            ExitCode::FAILURE
        }
    }
}
