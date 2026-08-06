use std::process::ExitCode;

use anduinos_recovery_engine::confirmation::{ConfirmationEngine, ConfirmationOutcome};

fn main() -> ExitCode {
    match ConfirmationEngine::default().reconcile() {
        Ok(ConfirmationOutcome::NoAction) => ExitCode::SUCCESS,
        Ok(ConfirmationOutcome::Confirmed) => {
            eprintln!("Disk Snapshots Manager rollback confirmed");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::RevertedRecorded) => {
            eprintln!("Disk Snapshots Manager automatic fallback recorded");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("Disk Snapshots Manager confirmation failed: {error}");
            ExitCode::FAILURE
        }
    }
}
