use std::process::ExitCode;
use std::str::FromStr;

use anduinos_timeback::recovery::{RecoveryCheckpoint, RecoveryEngine, RecoveryOutcome};
use anduinos_timeback::transaction::RollbackId;

const BOOT_ID: &str = "/proc/sys/kernel/random/boot_id";

fn main() -> ExitCode {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    let requested = match arguments.as_slice() {
        [] => None,
        [id] => match parse_id(id) {
            Ok(id) => Some(id),
            Err(message) => {
                eprintln!("{message}");
                return ExitCode::from(64);
            }
        },
        _ => {
            eprintln!("Usage: anduinos-timeback-initramfs [ROLLBACK_ID]");
            return ExitCode::from(64);
        }
    };
    let boot_id = match std::fs::read_to_string(BOOT_ID) {
        Ok(value) => value.trim().to_string(),
        Err(error) => {
            eprintln!("Could not read the initramfs boot ID: {error}");
            return ExitCode::FAILURE;
        }
    };
    match RecoveryEngine::default().execute_with_observer(requested, &boot_id, print_checkpoint) {
        Ok(RecoveryOutcome::NoAction) => ExitCode::SUCCESS,
        Ok(RecoveryOutcome::Applied) => {
            eprintln!("AnduinOS Timeback activated the selected recovery point");
            ExitCode::SUCCESS
        }
        Ok(RecoveryOutcome::Reverted) => {
            eprintln!("AnduinOS Timeback restored the protected fallback root");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("AnduinOS Timeback recovery failed: {error}");
            ExitCode::FAILURE
        }
    }
}

fn print_checkpoint(checkpoint: RecoveryCheckpoint) {
    eprintln!("TIMEBACK-CHECKPOINT {}", checkpoint.as_str());
}

fn parse_id(value: &str) -> Result<RollbackId, String> {
    let id = RollbackId::from_str(value)
        .map_err(|_| "Rollback ID must be a lowercase hyphenated UUID".to_string())?;
    if id.to_string() != value {
        return Err("Rollback ID must use canonical lowercase UUID form".into());
    }
    Ok(id)
}
