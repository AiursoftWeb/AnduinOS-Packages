use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::str::FromStr;

use anduinos_recovery_engine::layout::{LayoutReport, LayoutSupport, MountReport};
use anduinos_recovery_engine::model::DeploymentId;
use anduinos_recovery_engine::offline::OfflineRecoveryEngine;
use anduinos_recovery_engine::operations::{OperationEngine, SystemCommandRunner};
use anduinos_recovery_engine::store::DeploymentStore;
use serde_json::json;

fn main() -> ExitCode {
    if unsafe { libc::geteuid() } != 0 {
        eprintln!("Offline recovery must run as root");
        return ExitCode::from(77);
    }
    match run(std::env::args().skip(1).collect()) {
        Ok(value) => match serde_json::to_string(&value) {
            Ok(serialized) => {
                println!("{serialized}");
                ExitCode::SUCCESS
            }
            Err(error) => {
                eprintln!("Could not serialize the offline recovery result: {error}");
                ExitCode::FAILURE
            }
        },
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn run(arguments: Vec<String>) -> Result<serde_json::Value, String> {
    let (action, top_argument, remaining) = match arguments.as_slice() {
        [action, top, remaining @ ..] => (action.as_str(), top.as_str(), remaining),
        _ => return Err(usage()),
    };
    let top = validate_top(top_argument)?;
    let store = top.join("@snapshots/anduinos-btrfs-snapshots-manager");
    let engine = OfflineRecoveryEngine::system(&top);
    engine.validate().map_err(|error| error.to_string())?;

    match (action, remaining) {
        ("list", []) => {
            let report = DeploymentStore::new(&store).discover();
            Ok(json!({
                "schema": 1,
                "pending": engine.pending().map_err(|error| error.to_string())?,
                "deployments": report.deployments,
                "issues": report.issues,
            }))
        }
        ("resume", []) => Ok(json!({
            "schema": 1,
            "transaction": engine.resume().map_err(|error| error.to_string())?,
        })),
        ("create", [title, reason]) => {
            engine.resume().map_err(|error| error.to_string())?;
            let record = OperationEngine::new(top.join("@root"), &store, SystemCommandRunner)
                .create_offline_manual(&offline_layout(), title, reason, false, |_, _, message| {
                    eprintln!("{message}")
                })
                .map_err(|error| error.to_string())?;
            Ok(json!({"schema": 1, "deployment": record}))
        }
        ("protect", []) => {
            engine.resume().map_err(|error| error.to_string())?;
            let record = OperationEngine::new(top.join("@root"), &store, SystemCommandRunner)
                .create_offline_pre_rollback(&offline_layout(), |_, _, message| {
                    eprintln!("{message}")
                })
                .map_err(|error| error.to_string())?;
            Ok(json!({"schema": 1, "deployment": record}))
        }
        ("restore", [identifier]) => {
            let id = DeploymentId::from_str(identifier)
                .map_err(|_| "The recovery point ID is invalid".to_string())?;
            let transaction = engine.restore(id).map_err(|error| error.to_string())?;
            Ok(json!({"schema": 1, "transaction": transaction}))
        }
        _ => Err(usage()),
    }
}

fn validate_top(value: &str) -> Result<PathBuf, String> {
    let candidate = Path::new(value);
    let canonical = candidate
        .canonicalize()
        .map_err(|error| format!("Could not resolve the rescue mount: {error}"))?;
    let parent = canonical.parent();
    let name = canonical.file_name().and_then(|part| part.to_str());
    if parent != Some(Path::new("/run/anduinos-rescue-center"))
        || !name.is_some_and(|part| part.starts_with("repair-"))
    {
        return Err("Refusing an unexpected offline recovery mount path".into());
    }
    Ok(canonical)
}

fn offline_layout() -> LayoutReport {
    let mounts = [
        ("/", "/@root"),
        ("/home", "/@home"),
        ("/var/log", "/@log"),
        ("/.snapshots", "/@snapshots"),
        ("/var/lib/containers", "/@containers"),
        ("/var/lib/libvirt/images", "/@libvirt"),
    ]
    .into_iter()
    .map(|(mount_point, subvolume)| MountReport {
        mount_point: mount_point.into(),
        subvolume: subvolume.into(),
        filesystem: "btrfs".into(),
        source: "offline-rescue-target".into(),
    })
    .collect();
    LayoutReport {
        support: LayoutSupport::Supported,
        root_filesystem: Some("btrfs".into()),
        root_source: Some("offline-rescue-target".into()),
        issues: Vec::new(),
        mounts,
    }
}

fn usage() -> String {
    "Usage: anduinos-btrfs-snapshots-manager-offline \
{list|resume|protect|create TITLE REASON|restore ID} TOP"
        .into()
}
