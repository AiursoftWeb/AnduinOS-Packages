use std::process::ExitCode;

use anduinos_recovery_engine::confirmation::{ConfirmationEngine, ConfirmationOutcome};

fn main() -> ExitCode {
    match ConfirmationEngine::default().reconcile() {
        Ok(ConfirmationOutcome::NoAction) => ExitCode::SUCCESS,
        Ok(ConfirmationOutcome::Confirmed) => {
            eprintln!("Disk Snapshots Manager rollback confirmed");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::ConfirmedCleanupPending) => {
            eprintln!("Disk Snapshots Manager rollback confirmed; old-root cleanup was deferred");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::CleanupCompleted) => {
            eprintln!("Disk Snapshots Manager completed deferred old-root cleanup");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::CleanupPending) => {
            eprintln!("Disk Snapshots Manager old-root cleanup remains deferred");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::RevertedRecorded) => {
            eprintln!("Disk Snapshots Manager automatic fallback recorded");
            ExitCode::SUCCESS
        }
        Ok(ConfirmationOutcome::FailedRecorded) => {
            eprintln!("Disk Snapshots Manager recorded a safely failed recovery attempt");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("Disk Snapshots Manager confirmation failed: {error}");
            ExitCode::FAILURE
        }
    }
}
