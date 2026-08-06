use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::model::{DeploymentId, DeploymentRecord, DeploymentState};
use crate::{DEPLOYMENT_SCHEMA_VERSION, RECOVERY_STORE_ROOT};

const MAX_METADATA_BYTES: u64 = 1024 * 1024;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DiscoveryReport {
    pub deployment_schema_version: u32,
    pub deployments: Vec<DeploymentRecord>,
    pub issues: Vec<DiscoveryIssue>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DiscoveryIssue {
    pub entry: String,
    pub code: DiscoveryIssueCode,
    pub message: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum DiscoveryIssueCode {
    StoreUnavailable,
    UnsafeEntry,
    InvalidFilename,
    MetadataTooLarge,
    ReadFailed,
    InvalidJson,
    InvalidRecord,
    IdentifierMismatch,
    MissingSnapshot,
}

#[derive(Clone, Debug)]
pub struct DeploymentStore {
    root: PathBuf,
}

impl Default for DeploymentStore {
    fn default() -> Self {
        Self::new(RECOVERY_STORE_ROOT)
    }
}

impl DeploymentStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn discover(&self) -> DiscoveryReport {
        let mut report = DiscoveryReport {
            deployment_schema_version: DEPLOYMENT_SCHEMA_VERSION,
            deployments: Vec::new(),
            issues: Vec::new(),
        };
        let metadata_dir = self.root.join("metadata");
        let directory_metadata = match fs::symlink_metadata(&metadata_dir) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return report,
            Err(error) => {
                report.issues.push(issue(
                    "metadata",
                    DiscoveryIssueCode::StoreUnavailable,
                    format!("Could not inspect the metadata directory: {error}"),
                ));
                return report;
            }
        };
        if !directory_metadata.file_type().is_dir() {
            report.issues.push(issue(
                "metadata",
                DiscoveryIssueCode::UnsafeEntry,
                "The metadata path is not a real directory".into(),
            ));
            return report;
        }

        let entries = match fs::read_dir(&metadata_dir) {
            Ok(entries) => entries,
            Err(error) => {
                report.issues.push(issue(
                    "metadata",
                    DiscoveryIssueCode::StoreUnavailable,
                    format!("Could not read the metadata directory: {error}"),
                ));
                return report;
            }
        };

        for entry_result in entries {
            let entry = match entry_result {
                Ok(entry) => entry,
                Err(error) => {
                    report.issues.push(issue(
                        "metadata",
                        DiscoveryIssueCode::ReadFailed,
                        format!("Could not read a metadata directory entry: {error}"),
                    ));
                    continue;
                }
            };
            if entry.path().extension() != Some(OsStr::new("json")) {
                continue;
            }
            match self.read_discoverable_record(&entry.path()) {
                Ok(record) => report.deployments.push(record),
                Err(problem) => report.issues.push(problem),
            }
        }

        report.deployments.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| left.id.to_string().cmp(&right.id.to_string()))
        });
        report
    }

    pub fn load_record(&self, id: DeploymentId) -> Result<DeploymentRecord, DiscoveryIssue> {
        self.read_metadata_record(&self.root.join("metadata").join(format!("{id}.json")))
    }

    pub fn transition(
        &self,
        id: DeploymentId,
        next: DeploymentState,
    ) -> Result<DeploymentRecord, StoreMutationError> {
        let _lock = self.acquire_write_lock()?;
        let mut record = self.load_record(id).map_err(|issue| {
            StoreMutationError::new(StoreMutationErrorCode::InvalidRecord, issue.message)
        })?;
        if record.state == next {
            return Ok(record);
        }
        if !record.state.can_transition_to(next) {
            return Err(StoreMutationError::new(
                StoreMutationErrorCode::InvalidTransition,
                format!(
                    "Deployment cannot transition from {:?} to {next:?}",
                    record.state
                ),
            ));
        }
        record.state = next;
        self.write_record_atomic(&record)?;
        Ok(record)
    }

    pub fn write_record(&self, record: &DeploymentRecord) -> Result<(), StoreMutationError> {
        let _lock = self.acquire_write_lock()?;
        self.write_record_atomic(record)
    }

    fn acquire_write_lock(&self) -> Result<StoreWriteLock, StoreMutationError> {
        let metadata_dir = self.root.join("metadata");
        ensure_real_directory(&metadata_dir)?;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(metadata_dir.join("write.lock"))
            .map_err(|error| mutation_io("Could not open deployment metadata lock", error))?;
        let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
        if result != 0 {
            return Err(mutation_io(
                "Could not lock deployment metadata",
                io::Error::last_os_error(),
            ));
        }
        Ok(StoreWriteLock(file))
    }

    fn write_record_atomic(&self, record: &DeploymentRecord) -> Result<(), StoreMutationError> {
        record.validate().map_err(|error| {
            StoreMutationError::new(StoreMutationErrorCode::InvalidRecord, error.to_string())
        })?;
        let metadata_dir = self.root.join("metadata");
        ensure_real_directory(&metadata_dir)?;
        let target = metadata_dir.join(format!("{}.json", record.id));
        match fs::symlink_metadata(&target) {
            Ok(metadata) if metadata.file_type().is_file() => {}
            Ok(_) => {
                return Err(StoreMutationError::new(
                    StoreMutationErrorCode::UnsafePath,
                    "Deployment metadata target is not a regular file",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(mutation_io(
                    "Could not inspect deployment metadata target",
                    error,
                ));
            }
        }
        let temporary = metadata_dir.join(format!(
            ".{}.{}.tmp",
            record.id,
            uuid::Uuid::new_v4().hyphenated()
        ));
        let serialized = serde_json::to_vec_pretty(record).map_err(|error| {
            StoreMutationError::new(
                StoreMutationErrorCode::InvalidRecord,
                format!("Could not serialize deployment metadata: {error}"),
            )
        })?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)
            .map_err(|error| mutation_io("Could not create deployment metadata", error))?;
        let result = (|| {
            file.write_all(&serialized)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            fs::rename(&temporary, &target)?;
            sync_directory(&metadata_dir)
        })();
        if let Err(error) = result {
            let _ = fs::remove_file(&temporary);
            return Err(mutation_io(
                "Could not atomically commit deployment metadata",
                error,
            ));
        }
        Ok(())
    }

    fn read_discoverable_record(&self, path: &Path) -> Result<DeploymentRecord, DiscoveryIssue> {
        let record = self.read_metadata_record(path)?;
        if record.snapshot_uuid.is_none() {
            return Ok(record);
        }

        let entry_name = safe_entry_name(path);
        let snapshot = self
            .root
            .join("deployments")
            .join(record.id.to_string())
            .join("root");
        let snapshot_metadata = match fs::symlink_metadata(&snapshot) {
            Ok(metadata) => metadata,
            Err(error)
                if error.kind() == io::ErrorKind::NotFound
                    && record.state == DeploymentState::Deleting =>
            {
                return Ok(record);
            }
            Err(error) => {
                return Err(issue(
                    &entry_name,
                    DiscoveryIssueCode::MissingSnapshot,
                    format!("The deployment root is unavailable: {error}"),
                ));
            }
        };
        if !snapshot_metadata.file_type().is_dir() {
            return Err(issue(
                &entry_name,
                DiscoveryIssueCode::UnsafeEntry,
                "The deployment root must be a real directory".into(),
            ));
        }
        Ok(record)
    }

    fn read_metadata_record(&self, path: &Path) -> Result<DeploymentRecord, DiscoveryIssue> {
        let entry_name = safe_entry_name(path);
        let metadata = fs::symlink_metadata(path).map_err(|error| {
            issue(
                &entry_name,
                DiscoveryIssueCode::ReadFailed,
                format!("Could not inspect metadata: {error}"),
            )
        })?;
        if !metadata.file_type().is_file() {
            return Err(issue(
                &entry_name,
                DiscoveryIssueCode::UnsafeEntry,
                "Metadata must be a regular file, not a link or special file".into(),
            ));
        }
        if metadata.len() > MAX_METADATA_BYTES {
            return Err(issue(
                &entry_name,
                DiscoveryIssueCode::MetadataTooLarge,
                format!("Metadata exceeds the {MAX_METADATA_BYTES}-byte safety limit"),
            ));
        }

        let stem = path.file_stem().and_then(OsStr::to_str).ok_or_else(|| {
            issue(
                &entry_name,
                DiscoveryIssueCode::InvalidFilename,
                "Metadata filename is not valid UTF-8".into(),
            )
        })?;
        let filename_id = stem.parse::<DeploymentId>().map_err(|_| {
            issue(
                &entry_name,
                DiscoveryIssueCode::InvalidFilename,
                "Metadata filename must be a lowercase hyphenated UUID".into(),
            )
        })?;
        if filename_id.to_string() != stem {
            return Err(issue(
                &entry_name,
                DiscoveryIssueCode::InvalidFilename,
                "Metadata filename must use the canonical lowercase UUID form".into(),
            ));
        }

        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)
            .map_err(|error| {
                issue(
                    &entry_name,
                    DiscoveryIssueCode::ReadFailed,
                    format!("Could not open metadata: {error}"),
                )
            })?;
        let mut contents = Vec::with_capacity(metadata.len() as usize);
        file.take(MAX_METADATA_BYTES + 1)
            .read_to_end(&mut contents)
            .map_err(|error| {
                issue(
                    &entry_name,
                    DiscoveryIssueCode::ReadFailed,
                    format!("Could not read metadata: {error}"),
                )
            })?;
        if contents.len() as u64 > MAX_METADATA_BYTES {
            return Err(issue(
                &entry_name,
                DiscoveryIssueCode::MetadataTooLarge,
                format!("Metadata exceeds the {MAX_METADATA_BYTES}-byte safety limit"),
            ));
        }
        let record = serde_json::from_slice::<DeploymentRecord>(&contents).map_err(|error| {
            issue(
                &entry_name,
                DiscoveryIssueCode::InvalidJson,
                format!("Metadata is not a deployment record: {error}"),
            )
        })?;
        record.validate().map_err(|error| {
            issue(
                &entry_name,
                DiscoveryIssueCode::InvalidRecord,
                error.to_string(),
            )
        })?;
        if record.id != filename_id {
            return Err(issue(
                &entry_name,
                DiscoveryIssueCode::IdentifierMismatch,
                "Deployment ID does not match its metadata filename".into(),
            ));
        }

        Ok(record)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StoreMutationErrorCode {
    InvalidRecord,
    InvalidTransition,
    UnsafePath,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StoreMutationError {
    pub code: StoreMutationErrorCode,
    pub message: String,
}

impl StoreMutationError {
    fn new(code: StoreMutationErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl std::fmt::Display for StoreMutationError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for StoreMutationError {}

struct StoreWriteLock(std::fs::File);

impl Drop for StoreWriteLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

fn ensure_real_directory(path: &Path) -> Result<(), StoreMutationError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| mutation_io("Could not inspect deployment metadata directory", error))?;
    if metadata.file_type().is_dir() {
        Ok(())
    } else {
        Err(StoreMutationError::new(
            StoreMutationErrorCode::UnsafePath,
            "Deployment metadata directory is not a real directory",
        ))
    }
}

fn sync_directory(path: &Path) -> io::Result<()> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)?
        .sync_all()
}

fn mutation_io(context: &str, error: io::Error) -> StoreMutationError {
    StoreMutationError::new(StoreMutationErrorCode::Io, format!("{context}: {error}"))
}

fn safe_entry_name(path: &Path) -> String {
    path.file_name()
        .and_then(OsStr::to_str)
        .map(|name| name.chars().flat_map(char::escape_default).collect())
        .unwrap_or_else(|| "<invalid-name>".into())
}

fn issue(entry: &str, code: DiscoveryIssueCode, message: String) -> DiscoveryIssue {
    DiscoveryIssue {
        entry: entry.to_string(),
        code,
        message,
    }
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::symlink;

    use chrono::{Duration, Utc};
    use uuid::Uuid;

    use crate::model::{DeploymentKind, DeploymentState};

    use super::*;

    struct TestStore(PathBuf);

    impl TestStore {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "btrfs-snapshots-manager-store-test-{}",
                Uuid::new_v4()
            ));
            fs::create_dir_all(path.join("metadata")).unwrap();
            fs::create_dir_all(path.join("deployments")).unwrap();
            Self(path)
        }

        fn write(&self, record: &DeploymentRecord) {
            fs::create_dir_all(
                self.0
                    .join("deployments")
                    .join(record.id.to_string())
                    .join("root"),
            )
            .unwrap();
            fs::write(
                self.0.join("metadata").join(format!("{}.json", record.id)),
                serde_json::to_vec(record).unwrap(),
            )
            .unwrap();
        }
    }

    impl Drop for TestStore {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    fn valid_record(created_at: chrono::DateTime<Utc>) -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind: DeploymentKind::Manual,
            state: DeploymentState::Ready,
            created_at,
            title: "Known-good system".into(),
            reason: "Manual recovery point".into(),
            schedule_id: None,
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: None,
            kernel_release: Some("7.0.0-28-generic".into()),
            initramfs_sha256: Some("a".repeat(64)),
            boot_artifact_sha256: Some("b".repeat(64)),
            dpkg_status_sha256: Some("c".repeat(64)),
            mok_certificate_sha256: None,
            pinned: false,
            failure: None,
        }
    }

    #[test]
    fn missing_store_is_an_empty_first_run() {
        let path = std::env::temp_dir().join(format!(
            "btrfs-snapshots-manager-missing-{}",
            Uuid::new_v4()
        ));
        let report = DeploymentStore::new(path).discover();
        assert!(report.deployments.is_empty());
        assert!(report.issues.is_empty());
    }

    #[test]
    fn valid_records_are_newest_first() {
        let store = TestStore::new();
        let older = valid_record(Utc::now() - Duration::hours(1));
        let newer = valid_record(Utc::now());
        store.write(&older);
        store.write(&newer);

        let report = DeploymentStore::new(&store.0).discover();
        assert!(report.issues.is_empty());
        assert_eq!(report.deployments, vec![newer, older]);
    }

    #[test]
    fn one_bad_record_does_not_hide_valid_records() {
        let store = TestStore::new();
        let valid = valid_record(Utc::now());
        store.write(&valid);
        fs::write(store.0.join("metadata/not-a-uuid.json"), b"{}").unwrap();

        let report = DeploymentStore::new(&store.0).discover();
        assert_eq!(report.deployments, vec![valid]);
        assert_eq!(report.issues.len(), 1);
        assert_eq!(report.issues[0].code, DiscoveryIssueCode::InvalidFilename);
    }

    #[test]
    fn metadata_symlinks_are_never_followed() {
        let store = TestStore::new();
        let record = valid_record(Utc::now());
        let target = store.0.join("outside.json");
        fs::write(&target, serde_json::to_vec(&record).unwrap()).unwrap();
        symlink(
            &target,
            store.0.join("metadata").join(format!("{}.json", record.id)),
        )
        .unwrap();

        let report = DeploymentStore::new(&store.0).discover();
        assert!(report.deployments.is_empty());
        assert_eq!(report.issues[0].code, DiscoveryIssueCode::UnsafeEntry);
    }

    #[test]
    fn metadata_directory_symlinks_are_never_followed() {
        let store = TestStore::new();
        fs::remove_dir(store.0.join("metadata")).unwrap();
        symlink(store.0.join("deployments"), store.0.join("metadata")).unwrap();

        let report = DeploymentStore::new(&store.0).discover();
        assert!(report.deployments.is_empty());
        assert_eq!(report.issues[0].code, DiscoveryIssueCode::UnsafeEntry);
    }

    #[test]
    fn oversized_metadata_is_rejected_before_json_parsing() {
        let store = TestStore::new();
        let id = DeploymentId::new();
        fs::write(
            store.0.join("metadata").join(format!("{id}.json")),
            vec![b' '; MAX_METADATA_BYTES as usize + 1],
        )
        .unwrap();

        let report = DeploymentStore::new(&store.0).discover();
        assert_eq!(report.issues[0].code, DiscoveryIssueCode::MetadataTooLarge);
    }

    #[test]
    fn filename_and_record_id_must_match() {
        let store = TestStore::new();
        let record = valid_record(Utc::now());
        let other = DeploymentId::new();
        fs::write(
            store.0.join("metadata").join(format!("{other}.json")),
            serde_json::to_vec(&record).unwrap(),
        )
        .unwrap();

        let report = DeploymentStore::new(&store.0).discover();
        assert_eq!(
            report.issues[0].code,
            DiscoveryIssueCode::IdentifierMismatch
        );
    }

    #[test]
    fn deployment_root_must_exist_and_must_not_be_a_symlink() {
        let store = TestStore::new();
        let record = valid_record(Utc::now());
        fs::write(
            store.0.join("metadata").join(format!("{}.json", record.id)),
            serde_json::to_vec(&record).unwrap(),
        )
        .unwrap();
        let report = DeploymentStore::new(&store.0).discover();
        assert_eq!(report.issues[0].code, DiscoveryIssueCode::MissingSnapshot);

        let root = store
            .0
            .join("deployments")
            .join(record.id.to_string())
            .join("root");
        fs::create_dir_all(root.parent().unwrap()).unwrap();
        symlink(&store.0, root).unwrap();
        let report = DeploymentStore::new(&store.0).discover();
        assert_eq!(report.issues[0].code, DiscoveryIssueCode::UnsafeEntry);
    }

    #[test]
    fn deployment_state_transition_is_atomic_and_idempotent() {
        let store = TestStore::new();
        let record = valid_record(Utc::now());
        store.write(&record);

        let updated = DeploymentStore::new(&store.0)
            .transition(record.id, DeploymentState::PendingRollback)
            .unwrap();
        assert_eq!(updated.state, DeploymentState::PendingRollback);
        assert_eq!(
            DeploymentStore::new(&store.0)
                .transition(record.id, DeploymentState::PendingRollback)
                .unwrap(),
            updated
        );
        assert_eq!(
            DeploymentStore::new(&store.0)
                .load_record(record.id)
                .unwrap()
                .state,
            DeploymentState::PendingRollback
        );
    }

    #[test]
    fn atomic_writer_never_replaces_a_metadata_symlink() {
        let store = TestStore::new();
        let record = valid_record(Utc::now());
        let outside = store.0.join("outside");
        fs::write(&outside, "do not replace").unwrap();
        symlink(
            &outside,
            store.0.join("metadata").join(format!("{}.json", record.id)),
        )
        .unwrap();

        let error = DeploymentStore::new(&store.0)
            .write_record(&record)
            .unwrap_err();
        assert_eq!(error.code, StoreMutationErrorCode::UnsafePath);
        assert_eq!(fs::read_to_string(outside).unwrap(), "do not replace");
    }
}
