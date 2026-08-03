use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::layout::LayoutReport;
use crate::targets::{discover_targets, TargetKind};
use crate::SNAPSHOT_ROOT;

const BTRFS: &str = "/usr/bin/btrfs";
const HOME_SNAPSHOT_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HomeSnapshotRecord {
    pub schema_version: u32,
    pub id: Uuid,
    pub created_at: DateTime<Utc>,
    pub deleting: bool,
}

#[derive(Clone, Debug)]
pub struct HomeSnapshotStore {
    root: PathBuf,
    home: PathBuf,
}

impl Default for HomeSnapshotStore {
    fn default() -> Self {
        Self::new(SNAPSHOT_ROOT, "/home")
    }
}

impl HomeSnapshotStore {
    pub fn new(root: impl Into<PathBuf>, home: impl Into<PathBuf>) -> Self {
        Self {
            root: root.into(),
            home: home.into(),
        }
    }

    pub fn create(&self, layout: &LayoutReport) -> Result<HomeSnapshotRecord, String> {
        self.ensure_supported(layout)?;
        self.ensure_directories()?;
        let _lock = self.lock()?;
        let record = HomeSnapshotRecord {
            schema_version: HOME_SNAPSHOT_SCHEMA_VERSION,
            id: Uuid::new_v4(),
            created_at: Utc::now(),
            deleting: false,
        };
        let snapshot = self.snapshot_path(record.id);
        run_btrfs(&[
            OsString::from("subvolume"),
            OsString::from("snapshot"),
            OsString::from("-r"),
            self.home.as_os_str().to_owned(),
            snapshot.as_os_str().to_owned(),
        ])?;
        if let Err(error) = self.write_record(&record) {
            let cleanup = run_btrfs(&[
                OsString::from("subvolume"),
                OsString::from("delete"),
                OsString::from("--commit-after"),
                snapshot.as_os_str().to_owned(),
            ]);
            return Err(match cleanup {
                Ok(()) => error,
                Err(cleanup) => format!("{error}; cleanup also failed: {cleanup}"),
            });
        }
        run_btrfs(&[
            OsString::from("filesystem"),
            OsString::from("sync"),
            self.root.as_os_str().to_owned(),
        ])?;
        Ok(record)
    }

    pub fn discover(&self) -> Result<Vec<HomeSnapshotRecord>, String> {
        let metadata = self.metadata_directory();
        let entries = match fs::read_dir(&metadata) {
            Ok(entries) => entries,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => return Err(format!("Could not read Home snapshot metadata: {error}")),
        };
        let mut records = Vec::new();
        for entry in entries {
            let entry = entry
                .map_err(|error| format!("Could not read Home snapshot metadata entry: {error}"))?;
            if entry.path().extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            let metadata = fs::symlink_metadata(entry.path())
                .map_err(|error| format!("Could not inspect Home snapshot metadata: {error}"))?;
            if !metadata.file_type().is_file() || metadata.len() > 64 * 1024 {
                return Err("Home snapshot metadata is unsafe".into());
            }
            let record = read_record(&entry.path())?;
            validate_record(&record)?;
            if entry.file_name().to_string_lossy() != format!("{}.json", record.id) {
                return Err("Home snapshot metadata ID does not match its filename".into());
            }
            match fs::symlink_metadata(self.snapshot_path(record.id)) {
                Ok(snapshot) if snapshot.file_type().is_dir() => {}
                Ok(_) => {
                    return Err(format!(
                        "Home snapshot {} is not a real directory",
                        record.id
                    ))
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound && record.deleting => {}
                Err(error) => {
                    return Err(format!(
                        "Home snapshot {} is unavailable: {error}",
                        record.id
                    ))
                }
            }
            records.push(record);
        }
        records.sort_by(|left, right| {
            left.created_at
                .cmp(&right.created_at)
                .then_with(|| left.id.cmp(&right.id))
        });
        Ok(records)
    }

    pub fn delete(&self, id: Uuid) -> Result<(), String> {
        self.ensure_directories()?;
        let _lock = self.lock()?;
        let metadata = self.metadata_directory().join(format!("{id}.json"));
        let mut record = read_record(&metadata)?;
        validate_record(&record)?;
        if record.id != id {
            return Err("Home snapshot metadata ID does not match the requested snapshot".into());
        }
        if !record.deleting {
            record.deleting = true;
            self.write_record(&record)?;
        }
        let snapshot = self.snapshot_path(id);
        match fs::symlink_metadata(&snapshot) {
            Ok(snapshot_metadata) if snapshot_metadata.file_type().is_dir() => run_btrfs(&[
                OsString::from("subvolume"),
                OsString::from("delete"),
                OsString::from("--commit-after"),
                snapshot.as_os_str().to_owned(),
            ])?,
            Ok(_) => return Err("The Home snapshot path is not a real directory".into()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(format!("Could not inspect the Home snapshot: {error}")),
        }
        fs::remove_file(metadata)
            .map_err(|error| format!("Could not remove Home snapshot metadata: {error}"))?;
        Ok(())
    }

    fn ensure_supported(&self, layout: &LayoutReport) -> Result<(), String> {
        if !layout.is_supported() {
            return Err("The complete AnduinOS Btrfs layout is required".into());
        }
        if discover_targets(layout)
            .iter()
            .any(|target| target.kind == TargetKind::Home && target.available)
        {
            Ok(())
        } else {
            Err("Home is not an independent compatible Btrfs subvolume".into())
        }
    }

    fn ensure_directories(&self) -> Result<(), String> {
        for directory in [
            self.root.clone(),
            self.home_root(),
            self.deployments_directory(),
            self.metadata_directory(),
        ] {
            match fs::symlink_metadata(&directory) {
                Ok(metadata) if metadata.file_type().is_dir() => {}
                Ok(_) => return Err(format!("{} is not a real directory", directory.display())),
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    fs::create_dir(&directory).map_err(|error| {
                        format!("Could not create {}: {error}", directory.display())
                    })?;
                    fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).map_err(
                        |error| format!("Could not protect {}: {error}", directory.display()),
                    )?;
                }
                Err(error) => {
                    return Err(format!(
                        "Could not inspect {}: {error}",
                        directory.display()
                    ))
                }
            }
        }
        Ok(())
    }

    fn write_record(&self, record: &HomeSnapshotRecord) -> Result<(), String> {
        let target = self
            .metadata_directory()
            .join(format!("{}.json", record.id));
        let temporary = self.metadata_directory().join(format!(
            ".{}.{}.tmp",
            record.id,
            Uuid::new_v4().hyphenated()
        ));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)
            .map_err(|error| format!("Could not create Home snapshot metadata: {error}"))?;
        let contents = serde_json::to_vec_pretty(record)
            .map_err(|error| format!("Could not encode Home snapshot metadata: {error}"))?;
        let result = (|| -> io::Result<()> {
            file.write_all(&contents)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            fs::rename(&temporary, &target)?;
            OpenOptions::new()
                .read(true)
                .open(self.metadata_directory())?
                .sync_all()
        })();
        if let Err(error) = result {
            let _ = fs::remove_file(temporary);
            return Err(format!("Could not commit Home snapshot metadata: {error}"));
        }
        Ok(())
    }

    fn lock(&self) -> Result<HomeOperationLock, String> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(self.root.join("operation.lock"))
            .map_err(|error| format!("Could not open the snapshot operation lock: {error}"))?;
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(format!(
                "Could not lock the snapshot store: {}",
                io::Error::last_os_error()
            ));
        }
        Ok(HomeOperationLock(file))
    }

    fn home_root(&self) -> PathBuf {
        self.root.join("home")
    }
    fn deployments_directory(&self) -> PathBuf {
        self.home_root().join("snapshots")
    }
    fn metadata_directory(&self) -> PathBuf {
        self.home_root().join("metadata")
    }
    fn snapshot_path(&self, id: Uuid) -> PathBuf {
        self.deployments_directory().join(id.to_string())
    }
}

struct HomeOperationLock(std::fs::File);

impl Drop for HomeOperationLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

fn run_btrfs(arguments: &[OsString]) -> Result<(), String> {
    let output = Command::new(BTRFS)
        .args(arguments)
        .env_clear()
        .env("LC_ALL", "C")
        .output()
        .map_err(|error| format!("Could not execute Btrfs: {error}"))?;
    if output.status.success() {
        Ok(())
    } else {
        let diagnostic = String::from_utf8_lossy(&output.stderr);
        Err(format!(
            "Btrfs exited with {}: {}",
            output.status,
            diagnostic.trim().chars().take(1000).collect::<String>()
        ))
    }
}

fn read_record(path: &Path) -> Result<HomeSnapshotRecord, String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("Could not inspect Home snapshot metadata: {error}"))?;
    if !metadata.file_type().is_file() || metadata.len() > 64 * 1024 {
        return Err("Home snapshot metadata is unsafe".into());
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| format!("Could not open Home snapshot metadata: {error}"))?;
    let mut contents = Vec::with_capacity(metadata.len() as usize);
    file.take(64 * 1024 + 1)
        .read_to_end(&mut contents)
        .map_err(|error| format!("Could not read Home snapshot metadata: {error}"))?;
    if contents.len() > 64 * 1024 {
        return Err("Home snapshot metadata is too large".into());
    }
    serde_json::from_slice(&contents)
        .map_err(|error| format!("Home snapshot metadata is invalid: {error}"))
}

fn validate_record(record: &HomeSnapshotRecord) -> Result<(), String> {
    if record.schema_version != HOME_SNAPSHOT_SCHEMA_VERSION {
        return Err(format!(
            "Unsupported Home snapshot metadata version {}",
            record.schema_version
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::symlink;

    use super::*;

    #[test]
    fn discovers_home_snapshot_records_in_time_order() {
        let environment = TestEnvironment::new();
        let older = HomeSnapshotRecord {
            schema_version: HOME_SNAPSHOT_SCHEMA_VERSION,
            id: Uuid::new_v4(),
            created_at: Utc::now() - chrono::Duration::hours(2),
            deleting: false,
        };
        let newer = HomeSnapshotRecord {
            schema_version: HOME_SNAPSHOT_SCHEMA_VERSION,
            id: Uuid::new_v4(),
            created_at: Utc::now(),
            deleting: false,
        };
        environment.add(&newer);
        environment.add(&older);

        assert_eq!(environment.store.discover().unwrap(), vec![older, newer]);
    }

    #[test]
    fn metadata_symlinks_are_rejected() {
        let environment = TestEnvironment::new();
        let record = HomeSnapshotRecord {
            schema_version: HOME_SNAPSHOT_SCHEMA_VERSION,
            id: Uuid::new_v4(),
            created_at: Utc::now(),
            deleting: false,
        };
        fs::create_dir_all(environment.store.snapshot_path(record.id)).unwrap();
        let outside = environment.root.join("outside.json");
        fs::write(&outside, serde_json::to_vec(&record).unwrap()).unwrap();
        symlink(
            &outside,
            environment
                .store
                .metadata_directory()
                .join(format!("{}.json", record.id)),
        )
        .unwrap();

        assert!(environment.store.discover().unwrap_err().contains("unsafe"));
    }

    #[test]
    fn interrupted_delete_remains_discoverable_without_its_subvolume() {
        let environment = TestEnvironment::new();
        let record = HomeSnapshotRecord {
            schema_version: HOME_SNAPSHOT_SCHEMA_VERSION,
            id: Uuid::new_v4(),
            created_at: Utc::now(),
            deleting: true,
        };
        fs::write(
            environment
                .store
                .metadata_directory()
                .join(format!("{}.json", record.id)),
            serde_json::to_vec(&record).unwrap(),
        )
        .unwrap();

        assert_eq!(environment.store.discover().unwrap(), vec![record]);
    }

    struct TestEnvironment {
        root: PathBuf,
        store: HomeSnapshotStore,
    }

    impl TestEnvironment {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("timeback-home-test-{}", Uuid::new_v4()));
            let store = HomeSnapshotStore::new(root.join("store"), root.join("source-home"));
            fs::create_dir_all(store.deployments_directory()).unwrap();
            fs::create_dir_all(store.metadata_directory()).unwrap();
            Self { root, store }
        }

        fn add(&self, record: &HomeSnapshotRecord) {
            fs::create_dir_all(self.store.snapshot_path(record.id)).unwrap();
            fs::write(
                self.store
                    .metadata_directory()
                    .join(format!("{}.json", record.id)),
                serde_json::to_vec(record).unwrap(),
            )
            .unwrap();
        }
    }

    impl Drop for TestEnvironment {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }
}
