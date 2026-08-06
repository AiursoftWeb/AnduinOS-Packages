use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anduinos_recovery_engine::layout::{LayoutReport, LayoutSupport, MountReport};
use anduinos_recovery_engine::model::{DeploymentId, DeploymentKind, DeploymentState};
use anduinos_recovery_engine::operations::{
    OperationEngine, OperationErrorCode, SystemCommandRunner,
};
use anduinos_recovery_engine::store::DeploymentStore;

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
            "Loopback manual recovery point",
            "Real Btrfs operation qualification",
            false,
            |_, _, _| {},
        )
        .expect("a real Btrfs recovery point must be created");
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
        .expect("an unchanged recovery point must verify");

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
            "Hourly automatic recovery point",
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
            "Hourly automatic recovery point",
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
