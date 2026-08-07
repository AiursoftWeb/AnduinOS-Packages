use std::process::ExitCode;
use std::str::FromStr;

use anduinos_recovery_engine::recovery::{RecoveryCheckpoint, RecoveryEngine, RecoveryOutcome};
use anduinos_recovery_engine::transaction::{RECOVERY_PROTOCOL_VERSION, RollbackId};

const BOOT_ID: &str = "/proc/sys/kernel/random/boot_id";

fn main() -> ExitCode {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments.as_slice() == ["--protocol-version"] {
        println!("{RECOVERY_PROTOCOL_VERSION}");
        return ExitCode::SUCCESS;
    }
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
            eprintln!("Usage: anduinos-btrfs-snapshots-manager-initramfs [ROLLBACK_ID]");
            return ExitCode::from(64);
        }
    };

    let boot_id = match std::fs::read_to_string(BOOT_ID) {
        Ok(value) if !value.trim().is_empty() => value.trim().to_string(),
        Ok(_) => {
            eprintln!("The initramfs boot ID is empty");
            return ExitCode::FAILURE;
        }
        Err(error) => {
            eprintln!("Could not read the initramfs boot ID: {error}");
            return ExitCode::FAILURE;
        }
    };

    match RecoveryEngine::default().execute_with_observer(requested, &boot_id, print_checkpoint) {
        Ok(RecoveryOutcome::NoAction) => ExitCode::SUCCESS,
        Ok(RecoveryOutcome::Applied) => {
            eprintln!("Disk Snapshots Manager activated the selected system snapshot");
            ExitCode::SUCCESS
        }
        Ok(RecoveryOutcome::Reverted) => {
            eprintln!("Disk Snapshots Manager restored the protected fallback root");
            ExitCode::SUCCESS
        }
        Ok(RecoveryOutcome::FailedSafe) => {
            eprintln!("Disk Snapshots Manager recorded a safely failed recovery attempt");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("Disk Snapshots Manager recovery failed: {error}");
            ExitCode::FAILURE
        }
    }
}

fn print_checkpoint(checkpoint: RecoveryCheckpoint) {
    eprintln!("SNAPSHOTS-MANAGER-CHECKPOINT {}", checkpoint.as_str());
}

fn parse_id(value: &str) -> Result<RollbackId, String> {
    let id = RollbackId::from_str(value)
        .map_err(|_| "Rollback ID must be a lowercase hyphenated UUID".to_string())?;
    if id.to_string() != value {
        return Err("Rollback ID must use canonical lowercase UUID form".into());
    }
    Ok(id)
}

#[cfg(test)]
mod tests {
    use super::parse_id;

    #[test]
    fn accepts_only_canonical_lowercase_ids() {
        assert!(parse_id("123e4567-e89b-42d3-a456-426614174000").is_ok());
        assert!(parse_id("123E4567-E89B-42D3-A456-426614174000").is_err());
        assert!(parse_id("123e4567e89b42d3a456426614174000").is_err());
        assert!(parse_id("../../pending-rollback.json").is_err());
    }
}
