use std::fs;
use std::path::PathBuf;

use anduinos_timeback::layout::{LayoutReport, LayoutSupport};
use anduinos_timeback::operations::{OperationEngine, SystemCommandRunner};

#[test]
#[ignore = "requires a disposable, mounted Btrfs loopback filesystem"]
fn creates_verifies_and_deletes_on_real_btrfs() {
    let mount = PathBuf::from(
        std::env::var_os("ANDUINOS_TIMEBACK_LOOPBACK_MOUNT")
            .expect("ANDUINOS_TIMEBACK_LOOPBACK_MOUNT must name the disposable mount"),
    );
    let root = mount.join("@root");
    let snapshot_root = mount.join("@snapshots/anduinos");
    assert!(root.is_dir(), "the loopback @root subvolume is missing");
    assert!(
        mount.join("@snapshots").is_dir(),
        "the loopback @snapshots subvolume is missing"
    );

    let kernel = "7.0.0-timeback-loopback";
    for directory in [
        root.join("proc/sys/kernel"),
        root.join("boot"),
        root.join("var/lib/dpkg"),
    ] {
        fs::create_dir_all(directory).expect("the synthetic system tree must be writable");
    }
    fs::write(root.join("proc/sys/kernel/osrelease"), kernel)
        .expect("the synthetic kernel release must be writable");
    fs::write(
        root.join("boot").join(format!("initrd.img-{kernel}")),
        b"initramfs",
    )
    .expect("the synthetic initramfs must be writable");
    fs::write(
        root.join("boot").join(format!("vmlinuz-{kernel}")),
        b"kernel",
    )
    .expect("the synthetic kernel must be writable");
    fs::write(
        root.join("var/lib/dpkg/status"),
        b"Package: loopback-test\n",
    )
    .expect("the synthetic dpkg database must be writable");

    let layout = LayoutReport {
        support: LayoutSupport::Supported,
        root_filesystem: Some("btrfs".into()),
        root_source: Some("loopback-test".into()),
        issues: Vec::new(),
        mounts: Vec::new(),
    };
    let engine = OperationEngine::new(&root, &snapshot_root, SystemCommandRunner);
    let record = engine
        .create_manual(
            &layout,
            "Loopback integration test",
            "Exercises real Btrfs ioctls without touching the host filesystem",
            false,
            |_, _, _| {},
        )
        .expect("a recovery point must be created on real Btrfs");
    engine
        .verify(&layout, record.id, |_, _, _| {})
        .expect("the real Btrfs recovery point must verify");
    engine
        .delete(&layout, record.id)
        .expect("the real Btrfs recovery point must be deletable");
}
