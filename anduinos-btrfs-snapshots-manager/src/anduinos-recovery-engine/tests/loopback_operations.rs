use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anduinos_recovery_engine::confirmation::{OldRootCleanupOutcome, cleanup_old_root_at};
use anduinos_recovery_engine::layout::{LayoutReport, LayoutSupport, MountReport};
use anduinos_recovery_engine::model::{DeploymentId, DeploymentKind, DeploymentState};
use anduinos_recovery_engine::operations::{
    OperationEngine, OperationErrorCode, SystemCommandRunner,
};
use anduinos_recovery_engine::store::DeploymentStore;
use anduinos_recovery_engine::transaction::RollbackId;

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

fn rejected_layout(support: LayoutSupport, issue: &str) -> LayoutReport {
    let mut layout = supported_layout();
    layout.support = support;
    layout.issues = vec![issue.into()];
    layout
}

fn assert_read_only(path: &Path) {
    let output = Command::new("/usr/bin/btrfs")
        .args(["property", "get", "-ts"])
        .arg(path)
        .arg("ro")
        .output()
        .expect("btrfs property must execute");
    assert!(output.status.success());
    assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "ro=true");
}

fn create_subvolume(path: &Path) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    let status = Command::new("/usr/bin/btrfs")
        .args(["subvolume", "create"])
        .arg(path)
        .status()
        .unwrap();
    assert!(status.success());
}

fn snapshot_subvolume(source: &Path, destination: &Path) {
    let status = Command::new("/usr/bin/btrfs")
        .args(["subvolume", "snapshot"])
        .arg(source)
        .arg(destination)
        .status()
        .unwrap();
    assert!(status.success());
}

fn old_root_name() -> String {
    format!("@root.snapshots-manager-old-{}", RollbackId::new())
}

#[test]
#[ignore = "requires root and a disposable Btrfs loopback image"]
fn real_btrfs_aaa_offline_restore_replaces_root_and_preserves_home() {
    use anduinos_recovery_engine::offline::{OfflinePhase, OfflineRecoveryEngine};
    use std::os::unix::fs::symlink;

    assert_eq!(unsafe { libc::geteuid() }, 0);
    let root = fixture_root();
    let system_root = root.join("@root");
    let store = root.join("@snapshots/anduinos-btrfs-snapshots-manager");
    let kernel = fs::read_to_string(system_root.join("proc/sys/kernel/osrelease"))
        .unwrap()
        .trim()
        .to_string();
    let default_kernel = system_root.join("boot/vmlinuz");
    match fs::remove_file(&default_kernel) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("could not reset kernel link: {error}"),
    }
    symlink(format!("vmlinuz-{kernel}"), &default_kernel).unwrap();
    fs::write(system_root.join("offline-marker"), "before").unwrap();
    fs::write(root.join("@home/offline-home-marker"), "must survive").unwrap();

    let operations = OperationEngine::new(&system_root, &store, SystemCommandRunner);
    let target = operations
        .create_offline_manual(
            &supported_layout(),
            "Offline loopback target",
            "Exercise Live recovery root replacement",
            false,
            |_, _, _| {},
        )
        .unwrap();
    fs::write(system_root.join("offline-marker"), "after").unwrap();

    let transaction = OfflineRecoveryEngine::system(&root)
        .restore(target.id)
        .unwrap();
    assert_eq!(transaction.phase, OfflinePhase::Completed);
    assert_eq!(
        fs::read_to_string(root.join("@root/offline-marker")).unwrap(),
        "before"
    );
    assert_eq!(
        fs::read_to_string(root.join("@home/offline-home-marker")).unwrap(),
        "must survive"
    );
    assert!(
        !fs::read_dir(&root)
            .unwrap()
            .filter_map(Result::ok)
            .any(|entry| entry
                .file_name()
                .to_string_lossy()
                .starts_with("@root.rescue-center-"))
    );
    operations.delete(&supported_layout(), target.id).unwrap();
}

#[test]
#[ignore = "requires root and a disposable Btrfs loopback image"]
fn real_btrfs_home_rollback_preserves_root_and_history_and_can_revert() {
    use anduinos_recovery_engine::personal::{FactoryHomeSnapshotOutcome, PersonalSnapshotEngine};
    use anduinos_recovery_engine::recovery::{
        RecoveryEngine, RecoveryFilesystem, RecoveryOutcome, SystemRecoveryFilesystem,
    };
    use anduinos_recovery_engine::transaction::{
        RollbackPhase, RollbackTransaction, TransactionStore,
    };
    use sha2::{Digest, Sha256};
    use std::os::unix::fs::PermissionsExt;

    assert_eq!(unsafe { libc::geteuid() }, 0);
    let root = fixture_root();
    let store = root.join("@snapshots/anduinos-btrfs-snapshots-manager");
    let layout = supported_layout();
    let system = OperationEngine::new(root.join("@root"), &store, SystemCommandRunner);
    fs::create_dir_all(root.join("@root/etc")).unwrap();
    fs::write(
        root.join("@root/etc/passwd"),
        "root:x:0:0::/root:/bin/bash\nalice:x:0:0::/home/alice:/bin/bash\n",
    )
    .unwrap();
    fs::create_dir_all(root.join("@home/alice")).unwrap();
    fs::write(root.join("@home/alice/settings"), "initial").unwrap();
    let personal = PersonalSnapshotEngine::new(root.join("@home"), &store, SystemCommandRunner);
    let FactoryHomeSnapshotOutcome::Created(factory) =
        personal.create_factory_if_missing(&layout).unwrap()
    else {
        panic!("fresh loopback store must have no factory Home");
    };
    let baseline = personal
        .create_manual(&layout, "User baseline", "Home rollback target", false)
        .unwrap();
    fs::write(root.join("@home/alice/settings"), "changed").unwrap();
    let safety = personal
        .create_manual(&layout, "Before rollback", "Safety", false)
        .unwrap();
    let anchor = system.create_pre_rollback(&layout, |_, _, _| {}).unwrap();
    let root_uuid = SystemRecoveryFilesystem
        .identity(&root.join("@root"))
        .unwrap();
    fs::write(
        root.join("@root/latest-change"),
        "must survive Home rollback",
    )
    .unwrap();
    fs::create_dir_all(store.join("recovery-boot")).unwrap();
    let confirm = store.join("recovery-boot/confirm");
    fs::write(&confirm, "test confirmation artifact").unwrap();
    fs::set_permissions(&confirm, fs::Permissions::from_mode(0o700)).unwrap();
    let mut transaction = RollbackTransaction::new(
        anchor.id,
        anchor.id,
        "eeeeeeee-1111-4222-8333-ffffffffffff",
        "test-kernel",
        "a".repeat(64),
        "b".repeat(64),
        format!("{:x}", Sha256::digest(fs::read(&confirm).unwrap())),
    );
    transaction.home_only = true;
    transaction.enable_home_reset(&baseline).unwrap();
    transaction.fallback_home_snapshot_id = Some(safety.id);
    transaction
        .transition(RollbackPhase::Armed, chrono::Utc::now())
        .unwrap();
    let transactions = TransactionStore::new(&store);
    transactions.create(&transaction).unwrap();
    assert!(personal.delete(&layout, baseline.id).is_err());
    assert!(personal.delete(&layout, safety.id).is_err());
    let recovery = RecoveryEngine::new(&root, SystemRecoveryFilesystem);
    assert_eq!(
        recovery
            .execute(Some(transaction.id), "11111111-2222-4333-8444-555555555555")
            .unwrap(),
        RecoveryOutcome::Applied
    );
    assert_eq!(
        fs::read_to_string(root.join("@home/alice/settings")).unwrap(),
        "initial"
    );
    assert_eq!(
        SystemRecoveryFilesystem
            .identity(&root.join("@root"))
            .unwrap(),
        root_uuid
    );
    assert!(root.join("@root/latest-change").exists());
    assert_eq!(
        fs::read_to_string(personal.snapshot_path(safety.id).join("alice/settings")).unwrap(),
        "changed"
    );
    assert_read_only(&personal.snapshot_path(factory.id));
    assert_eq!(
        recovery
            .execute(None, "66666666-7777-4888-8999-aaaaaaaaaaaa")
            .unwrap(),
        RecoveryOutcome::Reverted
    );
    assert_eq!(
        fs::read_to_string(root.join("@home/alice/settings")).unwrap(),
        "changed"
    );
    assert_eq!(
        SystemRecoveryFilesystem
            .identity(&root.join("@root"))
            .unwrap(),
        root_uuid
    );
    transactions.remove().unwrap();
    // A nested Home subvolume is not copied into ordinary Btrfs snapshots.
    // Refuse the next restore before moving either live subvolume.
    personal.check_restore_capacity(&layout).unwrap();
    create_subvolume(&root.join("@home/alice/nested"));
    fs::write(root.join("@home/alice/nested/important"), "not in snapshot").unwrap();
    assert!(personal.check_restore_capacity(&layout).is_err());
    transactions.create(&transaction).unwrap();
    assert!(
        recovery
            .execute(Some(transaction.id), "11111111-2222-4333-8444-555555555555")
            .is_err()
    );
    assert_eq!(
        fs::read_to_string(root.join("@home/alice/nested/important")).unwrap(),
        "not in snapshot"
    );
    assert_eq!(
        SystemRecoveryFilesystem
            .identity(&root.join("@root"))
            .unwrap(),
        root_uuid
    );
    transactions.remove().unwrap();
}

#[test]
#[ignore = "requires root and a disposable Btrfs loopback image"]
fn real_btrfs_old_root_cleanup_handles_empty_nested_subvolumes() {
    assert_eq!(unsafe { libc::geteuid() }, 0, "this test must run as root");
    let root = fixture_root();

    let plain = old_root_name();
    snapshot_subvolume(&root.join("@root"), &root.join(&plain));
    assert_eq!(
        cleanup_old_root_at(&root, &plain).unwrap(),
        OldRootCleanupOutcome::Removed
    );
    assert!(!root.join(&plain).exists());

    let nested = old_root_name();
    snapshot_subvolume(&root.join("@root"), &root.join(&nested));
    create_subvolume(&root.join(&nested).join("var/lib/machines"));
    create_subvolume(&root.join(&nested).join("var/lib/portables"));
    assert_eq!(
        cleanup_old_root_at(&root, &nested).unwrap(),
        OldRootCleanupOutcome::Removed
    );
    assert!(!root.join(&nested).exists());
}

#[test]
#[ignore = "requires root and a disposable Btrfs loopback image"]
fn real_btrfs_old_root_cleanup_defers_nonempty_descendants() {
    assert_eq!(unsafe { libc::geteuid() }, 0, "this test must run as root");
    let root = fixture_root();
    let old = old_root_name();
    snapshot_subvolume(&root.join("@root"), &root.join(&old));
    let machines = root.join(&old).join("var/lib/machines");
    create_subvolume(&machines);
    fs::write(machines.join("customer-machine.raw"), b"must be preserved").unwrap();

    let outcome = cleanup_old_root_at(&root, &old).unwrap();
    assert_eq!(
        outcome,
        OldRootCleanupOutcome::Deferred {
            blocked_subvolumes: vec!["var/lib/machines".into()],
            diagnostic: "The old root contains non-empty descendant subvolumes; automatic deletion was deferred".into(),
        }
    );
    assert_eq!(
        fs::read(machines.join("customer-machine.raw")).unwrap(),
        b"must be preserved"
    );
    assert!(root.join(&old).exists());
}

#[test]
#[ignore = "requires root and a disposable Btrfs loopback image"]
fn real_btrfs_old_root_cleanup_is_idempotent_after_partial_progress() {
    assert_eq!(unsafe { libc::geteuid() }, 0, "this test must run as root");
    let root = fixture_root();
    let old = old_root_name();
    snapshot_subvolume(&root.join("@root"), &root.join(&old));
    let machines = root.join(&old).join("var/lib/machines");
    let portables = root.join(&old).join("var/lib/portables");
    create_subvolume(&machines);
    create_subvolume(&portables);
    let status = Command::new("/usr/bin/btrfs")
        .args(["subvolume", "delete", "--commit-after"])
        .arg(&machines)
        .status()
        .unwrap();
    assert!(status.success());

    assert_eq!(
        cleanup_old_root_at(&root, &old).unwrap(),
        OldRootCleanupOutcome::Removed
    );
    assert!(!root.join(&old).exists());
}

#[test]
#[ignore = "requires root and a disposable Btrfs loopback image"]
fn real_btrfs_create_verify_protect_retention_and_cleanup() {
    assert_eq!(unsafe { libc::geteuid() }, 0, "this test must run as root");
    let root = fixture_root();
    let system_root = root.join("@root");
    let store_root = root.join("@snapshots/anduinos-btrfs-snapshots-manager");
    let layout = supported_layout();
    let engine = OperationEngine::new(&system_root, &store_root, SystemCommandRunner);

    let manual = engine
        .create_manual(
            &layout,
            "Loopback manual system snapshot",
            "Real Btrfs operation qualification",
            false,
            |_, _, _| {},
        )
        .expect("a real Btrfs system snapshot must be created");
    assert_eq!(manual.kind, DeploymentKind::Manual);
    assert_eq!(manual.state, DeploymentState::Ready);
    let manual_root = store_root
        .join("deployments")
        .join(manual.id.to_string())
        .join("root");
    assert_read_only(&manual_root);
    let captured_os_release = fs::read(manual_root.join("etc/os-release")).unwrap();
    fs::write(
        system_root.join("etc/os-release"),
        b"changed after snapshot\n",
    )
    .unwrap();
    assert_eq!(
        fs::read(manual_root.join("etc/os-release")).unwrap(),
        captured_os_release
    );
    engine
        .verify(&layout, manual.id, |_, _, _| {})
        .expect("an unchanged system snapshot must verify");

    engine.set_pinned(&layout, manual.id, true).unwrap();
    let protected = engine.delete(&layout, manual.id).unwrap_err();
    assert_eq!(protected.code, OperationErrorCode::Protected);
    engine.set_pinned(&layout, manual.id, false).unwrap();
    engine.delete(&layout, manual.id).unwrap();
    assert!(!manual_root.exists());

    let first = engine
        .create_scheduled(
            &layout,
            "loopback-hourly",
            "loopback-hourly-first",
            "Hourly automatic system snapshot",
            |_, _, _| {},
        )
        .unwrap();
    let floor = engine.delete_automatic(&layout, first.id, 1).unwrap_err();
    assert_eq!(floor.code, OperationErrorCode::Protected);
    let second = engine
        .create_scheduled(
            &layout,
            "loopback-hourly",
            "loopback-hourly-second",
            "Hourly automatic system snapshot",
            |_, _, _| {},
        )
        .unwrap();
    engine.delete_automatic(&layout, first.id, 1).unwrap();
    engine.verify(&layout, second.id, |_, _, _| {}).unwrap();

    let kernel = fs::read_to_string(system_root.join("proc/sys/kernel/osrelease"))
        .unwrap()
        .trim()
        .to_string();
    let kernel_path = system_root.join("boot").join(format!("vmlinuz-{kernel}"));
    let kernel_fixture = fs::read(&kernel_path).unwrap();
    fs::remove_file(&kernel_path).unwrap();
    let failed = engine
        .create_manual(
            &layout,
            "Incomplete loopback point",
            "Failure cleanup qualification",
            false,
            |_, _, _| {},
        )
        .unwrap_err();
    assert_eq!(failed.code, OperationErrorCode::Io);
    fs::write(&kernel_path, kernel_fixture).unwrap();

    let discovery = DeploymentStore::new(&store_root).discover();
    assert!(discovery.issues.is_empty());
    assert_eq!(
        discovery
            .deployments
            .iter()
            .filter(|record| record.state == DeploymentState::Incomplete)
            .count(),
        1
    );
    let deployment_directories = fs::read_dir(store_root.join("deployments"))
        .unwrap()
        .filter_map(Result::ok)
        .count();
    assert_eq!(
        deployment_directories, 1,
        "the failed point must not leave a Btrfs subvolume or staging directory"
    );
    engine.delete_automatic(&layout, second.id, 0).unwrap();
}

#[test]
#[ignore = "requires root and a disposable Btrfs loopback image"]
fn real_btrfs_rejects_malformed_layouts_without_mutation() {
    assert_eq!(unsafe { libc::geteuid() }, 0, "this test must run as root");
    let root = fixture_root();
    let system_root = root.join("@root");
    let store_root = root.join("@snapshots/anduinos-btrfs-snapshots-manager-malformed");
    let engine = OperationEngine::new(&system_root, &store_root, SystemCommandRunner);

    let full_store_root = root.join("@snapshots/anduinos-btrfs-snapshots-manager-no-space");
    let full_engine = OperationEngine::new(&system_root, &full_store_root, SystemCommandRunner)
        .with_minimum_free_bytes(u64::MAX);
    let no_space = full_engine
        .create_manual(
            &supported_layout(),
            "Must not fit",
            "Real Btrfs reserve-boundary qualification",
            false,
            |_, _, _| {},
        )
        .unwrap_err();
    assert_eq!(no_space.code, OperationErrorCode::InsufficientSpace);
    assert!(
        !full_store_root.exists(),
        "the free-space gate must run before recovery state is created"
    );

    let rejected = [
        rejected_layout(
            LayoutSupport::OtherFilesystem,
            "Root filesystem is ext4, not Btrfs",
        ),
        rejected_layout(
            LayoutSupport::IncompatibleBtrfs,
            "Required mount /home is missing",
        ),
        rejected_layout(
            LayoutSupport::IncompatibleBtrfs,
            "/home is on /dev/loop-other, expected /dev/loop-snapshots-manager-test",
        ),
        rejected_layout(
            LayoutSupport::Unavailable,
            "The root mount is missing from /proc/self/mountinfo",
        ),
    ];

    for layout in &rejected {
        let error = engine
            .create_manual(
                layout,
                "Must not be created",
                "Malformed-layout mutation qualification",
                false,
                |_, _, _| {},
            )
            .unwrap_err();
        assert_eq!(error.code, OperationErrorCode::UnsupportedLayout);
        assert!(
            !store_root.exists(),
            "layout rejection must happen before creating recovery state"
        );
    }

    let supported = supported_layout();
    let deployment = engine
        .create_manual(
            &supported,
            "Malformed-layout guard fixture",
            "Prove existing recovery state cannot be changed through a rejected layout",
            false,
            |_, _, _| {},
        )
        .unwrap();
    let deployment_root = store_root
        .join("deployments")
        .join(deployment.id.to_string())
        .join("root");
    assert!(deployment_root.exists());

    let missing_id = DeploymentId::new();
    let missing_verify = engine
        .verify(&supported, missing_id, |_, _, _| {})
        .unwrap_err();
    assert_eq!(missing_verify.code, OperationErrorCode::NotFound);
    let missing_delete = engine.delete(&supported, missing_id).unwrap_err();
    assert_eq!(missing_delete.code, OperationErrorCode::NotFound);

    for layout in &rejected {
        let pin_error = engine.set_pinned(layout, deployment.id, true).unwrap_err();
        assert_eq!(pin_error.code, OperationErrorCode::UnsupportedLayout);
        let verify_error = engine
            .verify(layout, deployment.id, |_, _, _| {})
            .unwrap_err();
        assert_eq!(verify_error.code, OperationErrorCode::UnsupportedLayout);
        let delete_error = engine.delete(layout, deployment.id).unwrap_err();
        assert_eq!(delete_error.code, OperationErrorCode::UnsupportedLayout);

        let stored = DeploymentStore::new(&store_root)
            .discover()
            .deployments
            .into_iter()
            .find(|record| record.id == deployment.id)
            .expect("the protected fixture must remain registered");
        assert!(!stored.pinned);
        assert_eq!(stored.state, DeploymentState::Ready);
        assert!(deployment_root.exists());
    }

    engine.delete(&supported, deployment.id).unwrap();
    assert!(!deployment_root.exists());

    let kernel = fs::read_to_string(system_root.join("proc/sys/kernel/osrelease"))
        .unwrap()
        .trim()
        .to_string();
    let initramfs_path = system_root
        .join("boot")
        .join(format!("initrd.img-{kernel}"));
    let initramfs_fixture = fs::read(&initramfs_path).unwrap();
    fs::remove_file(&initramfs_path).unwrap();
    let missing_initramfs_store =
        root.join("@snapshots/anduinos-btrfs-snapshots-manager-missing-initramfs");
    let missing_initramfs_engine =
        OperationEngine::new(&system_root, &missing_initramfs_store, SystemCommandRunner);
    let missing_initramfs = missing_initramfs_engine
        .create_manual(
            &supported,
            "Must remain incomplete",
            "Missing initramfs failure-cleanup qualification",
            false,
            |_, _, _| {},
        )
        .unwrap_err();
    assert_eq!(missing_initramfs.code, OperationErrorCode::Io);
    fs::write(&initramfs_path, initramfs_fixture).unwrap();
    let incomplete = DeploymentStore::new(&missing_initramfs_store).discover();
    assert_eq!(
        incomplete
            .deployments
            .iter()
            .filter(|record| record.state == DeploymentState::Incomplete)
            .count(),
        1
    );
    let leftover_roots = fs::read_dir(missing_initramfs_store.join("deployments"))
        .unwrap()
        .filter_map(Result::ok)
        .filter(|entry| entry.path().join("root").exists())
        .count();
    assert_eq!(
        leftover_roots, 0,
        "missing initramfs must not leave a snapshot subvolume"
    );
}
