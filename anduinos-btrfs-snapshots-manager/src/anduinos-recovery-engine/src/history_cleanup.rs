//! Explicit post-confirmation cleanup. The pending transaction is the durable
//! consent and retry journal; never discard it before all deletion completes.
use std::path::Path;

use crate::model::DeploymentKind;
use crate::operations::OperationEngine;
use crate::personal::{PersonalSnapshotEngine, PersonalSnapshotKind};
use crate::store::DeploymentStore;
use crate::transaction::{RollbackPhase, RollbackTransaction, TransactionStore};
use crate::{RECOVERY_STORE_ROOT, layout};

pub(crate) fn ensure_creation_allowed(root: &Path) -> Result<(), String> {
    if TransactionStore::new(root)
        .load_pending()
        .map_err(|e| e.to_string())?
        .is_some_and(|t| t.purge_history)
    {
        return Err("Snapshot creation is paused until factory history cleanup completes".into());
    }
    Ok(())
}

pub(crate) fn validate_consent(root: &Path) -> Result<RollbackTransaction, String> {
    let transaction = TransactionStore::new(root)
        .load_pending()
        .map_err(|e| e.to_string())?
        .ok_or("History cleanup requires a pending factory reset")?;
    if !transaction.purge_history
        || !transaction.reset_home
        || transaction.home_only
        || transaction.phase != RollbackPhase::Confirmed
    {
        return Err(
            "History cleanup requires an explicitly authorized, confirmed factory reset".into(),
        );
    }
    let target = DeploymentStore::new(root)
        .load_record(transaction.target_deployment_id)
        .map_err(|e| e.message)?;
    if target.kind != DeploymentKind::Factory || !target.pinned || !target.can_restore() {
        return Err("History cleanup requires a healthy protected factory System snapshot".into());
    }
    Ok(transaction)
}

pub(crate) fn purge(transaction: &RollbackTransaction) -> Result<(), String> {
    let root = Path::new(RECOVERY_STORE_ROOT);
    purge_at(
        root,
        &layout::inspect_current(),
        &OperationEngine::default(),
        &PersonalSnapshotEngine::default(),
        transaction,
        || {
            let status = std::process::Command::new("/usr/bin/btrfs")
                .args(["subvolume", "sync", "/.snapshots"])
                .status()
                .map_err(|e| format!("Could not wait for snapshot deletion: {e}"))?;
            if status.success() {
                Ok(())
            } else {
                Err("Snapshot deletion has not completed".into())
            }
        },
    )
}

fn purge_at<R: crate::operations::CommandRunner>(
    root: &Path,
    report: &layout::LayoutReport,
    system: &OperationEngine<R>,
    home: &PersonalSnapshotEngine<R>,
    transaction: &RollbackTransaction,
    wait: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    let pending = validate_consent(root)?;
    if &pending != transaction {
        return Err("The history cleanup transaction changed".into());
    }
    let systems = DeploymentStore::new(root).discover();
    let homes = home.discover();
    if !systems.issues.is_empty() || !homes.issues.is_empty() {
        return Err("History cleanup stopped: snapshot metadata has unresolved issues".into());
    }
    let factory_systems = systems
        .deployments
        .iter()
        .filter(|r| r.kind == DeploymentKind::Factory)
        .collect::<Vec<_>>();
    let factory_homes = homes
        .snapshots
        .iter()
        .filter(|r| r.kind == PersonalSnapshotKind::Factory)
        .collect::<Vec<_>>();
    if factory_systems.len() != 1
        || factory_systems[0].id != transaction.target_deployment_id
        || factory_homes.len() != 1
        || Some(factory_homes[0].id) != transaction.factory_home_snapshot_id
    {
        return Err(
            "History cleanup must retain exactly the bound factory System and Home snapshots"
                .into(),
        );
    }
    system
        .check_available(report, transaction.target_deployment_id)
        .map_err(|e| e.to_string())?;
    let factory_home = home
        .verify(report, factory_homes[0].id)
        .map_err(|e| e.to_string())?;
    if !factory_home.pinned || factory_home.snapshot_uuid != transaction.factory_home_snapshot_uuid
    {
        return Err("The factory Home identity changed".into());
    }
    for record in systems.deployments {
        if record.kind != DeploymentKind::Factory {
            system
                .delete_factory_reset_history(report, record.id)
                .map_err(|e| e.to_string())?;
        }
    }
    for record in homes.snapshots {
        if record.kind != PersonalSnapshotKind::Factory {
            home.delete_factory_reset_history(report, record.id)
                .map_err(|e| e.to_string())?;
        }
    }
    wait()?;
    let systems = DeploymentStore::new(root).discover();
    let homes = home.discover();
    if !systems.issues.is_empty()
        || !homes.issues.is_empty()
        || systems.deployments.len() != 1
        || homes.snapshots.len() != 1
        || systems.deployments[0].id != transaction.target_deployment_id
        || Some(homes.snapshots[0].id) != transaction.factory_home_snapshot_id
    {
        return Err("Factory reset history cleanup is incomplete".into());
    }
    ensure_only_factory_container(
        &root.join("deployments"),
        &transaction.target_deployment_id.to_string(),
    )?;
    ensure_only_factory_container(
        &root.join("personal/snapshots"),
        &transaction.factory_home_snapshot_id.unwrap().to_string(),
    )?;
    Ok(())
}

fn ensure_only_factory_container(directory: &Path, factory_id: &str) -> Result<(), String> {
    for entry in std::fs::read_dir(directory).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_name() != factory_id {
            return Err(format!(
                "Unregistered snapshot storage remains: {}",
                entry.path().display()
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::{LayoutReport, LayoutSupport, MountReport};
    use crate::operations::{FactorySnapshotOutcome, SystemCommandRunner};
    use crate::personal::FactoryHomeSnapshotOutcome;
    use crate::transaction::RollbackPhase;
    use std::env;
    use std::path::PathBuf;
    fn fixture_root() -> PathBuf {
        let root = PathBuf::from(
            env::var_os("ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_LOOPBACK_ROOT")
                .expect("the loopback qualification script must provide its mount root"),
        );
        let canonical = root
            .canonicalize()
            .expect("the loopback mount root must be canonicalizable");
        let test_directory = canonical
            .parent()
            .expect("the loopback mount root must have a parent");
        let test_directory_name = test_directory
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default();
        assert!(
            test_directory.parent() == Some(Path::new("/tmp"))
                && test_directory_name.starts_with("anduinos-btrfs-snapshots-manager-operations.")
                && canonical.file_name().and_then(|name| name.to_str()) == Some("mount"),
            "refusing to exercise an unexpected path: {}",
            canonical.display()
        );
        canonical
    }

    fn supported_layout() -> LayoutReport {
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
            source: "/dev/loop-snapshots-manager-test".into(),
        })
        .collect();
        LayoutReport {
            support: LayoutSupport::Supported,
            root_filesystem: Some("btrfs".into()),
            root_source: Some("/dev/loop-snapshots-manager-test".into()),
            issues: Vec::new(),
            mounts,
        }
    }

    #[test]
    #[ignore = "requires root and the disposable loopback qualification image"]
    fn real_btrfs_factory_history_cleanup_preserves_baselines_and_is_repeatable() {
        assert_eq!(unsafe { libc::geteuid() }, 0);
        let top = fixture_root();
        let root = top.join("@snapshots/history-cleanup-test");
        let system = OperationEngine::new(top.join("@root"), &root, SystemCommandRunner);
        let home = PersonalSnapshotEngine::new(top.join("@home"), &root, SystemCommandRunner);
        let report = supported_layout();
        let factory = match system
            .create_factory_if_missing(&report, |_, _, _| {})
            .unwrap()
        {
            FactorySnapshotOutcome::Created(r) | FactorySnapshotOutcome::Existing(r) => r,
        };
        let factory_home = match home.create_factory_if_missing(&report).unwrap() {
            FactoryHomeSnapshotOutcome::Created(r) | FactoryHomeSnapshotOutcome::Existing(r) => r,
        };
        for _ in 0..2 {
            let old_system = system
                .create_manual(
                    &report,
                    "Retained private system",
                    "private",
                    true,
                    |_, _, _| {},
                )
                .unwrap();
            let old_home = home
                .create_manual(&report, "Retained private Home", "private", true)
                .unwrap();
            let mut transaction = RollbackTransaction::new(
                factory.id,
                old_system.id,
                "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb",
                "7.0.0-test",
                "a".repeat(64),
                "b".repeat(64),
                "c".repeat(64),
            );
            transaction.enable_home_reset(&factory_home).unwrap();
            transaction.purge_history = true;
            transaction.fallback_home_snapshot_id = Some(old_home.id);
            transaction
                .transition(RollbackPhase::Armed, chrono::Utc::now())
                .unwrap();
            transaction
                .record_initramfs_entry("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb", chrono::Utc::now())
                .unwrap();
            transaction
                .begin_apply("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb", chrono::Utc::now())
                .unwrap();
            transaction
                .transition(RollbackPhase::BootedUnconfirmed, chrono::Utc::now())
                .unwrap();
            transaction
                .transition(RollbackPhase::Confirmed, chrono::Utc::now())
                .unwrap();
            TransactionStore::new(&root).create(&transaction).unwrap();
            assert!(
                system
                    .create_manual(&report, "Blocked", "blocked", false, |_, _, _| {})
                    .is_err()
            );
            assert!(
                home.create_manual(&report, "Blocked", "blocked", false)
                    .is_err()
            );
            assert!(
                system
                    .delete_factory_reset_history(&report, factory.id)
                    .is_err()
            );
            assert!(
                home.delete_factory_reset_history(&report, factory_home.id)
                    .is_err()
            );
            let mut unconfirmed = transaction.clone();
            unconfirmed.phase = RollbackPhase::BootedUnconfirmed;
            TransactionStore::new(&root).update(&unconfirmed).unwrap();
            assert!(purge_at(&root, &report, &system, &home, &unconfirmed, || Ok(())).is_err());
            assert!(system.delete(&report, old_system.id).is_err());
            assert!(home.delete(&report, old_home.id).is_err());
            assert_eq!(DeploymentStore::new(&root).discover().deployments.len(), 2);
            assert_eq!(home.discover().snapshots.len(), 2);
            TransactionStore::new(&root).update(&transaction).unwrap();
            // Deletion succeeded but waiting was interrupted. The transaction
            // survives, and rerunning cleanup must retain both factory snapshots.
            assert!(
                purge_at(&root, &report, &system, &home, &transaction, || Err(
                    "Injected wait failure".into()
                ))
                .is_err()
            );
            assert!(
                TransactionStore::new(&root)
                    .load_pending()
                    .unwrap()
                    .is_some()
            );
            purge_at(&root, &report, &system, &home, &transaction, || {
                let status = std::process::Command::new("/usr/bin/btrfs")
                    .args(["subvolume", "sync"])
                    .arg(&top)
                    .status()
                    .unwrap();
                assert!(status.success());
                Ok(())
            })
            .unwrap();
            assert_eq!(DeploymentStore::new(&root).discover().deployments.len(), 1);
            assert_eq!(home.discover().snapshots.len(), 1);
            system.check_available(&report, factory.id).unwrap();
            home.verify(&report, factory_home.id).unwrap();
            TransactionStore::new(&root).remove().unwrap();
        }
        // Restore remains possible after two successive history cleanups.
        assert!(
            matches!(system.create_factory_if_missing(&report, |_,_,_| {}).unwrap(),
            FactorySnapshotOutcome::Existing(r) if r.id == factory.id)
        );
    }
}
