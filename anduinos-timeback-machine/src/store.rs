use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{self, Read};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::model::{DeploymentId, DeploymentRecord};
use crate::{DEPLOYMENT_SCHEMA_VERSION, SNAPSHOT_ROOT};

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
        Self::new(SNAPSHOT_ROOT)
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
            match self.read_record(&entry.path()) {
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

    fn read_record(&self, path: &Path) -> Result<DeploymentRecord, DiscoveryIssue> {
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

        let snapshot = self
            .root
            .join("deployments")
            .join(record.id.to_string())
            .join("root");
        let snapshot_metadata = fs::symlink_metadata(&snapshot).map_err(|error| {
            issue(
                &entry_name,
                DiscoveryIssueCode::MissingSnapshot,
                format!("The deployment root is unavailable: {error}"),
            )
        })?;
        if !snapshot_metadata.file_type().is_dir() {
            return Err(issue(
                &entry_name,
                DiscoveryIssueCode::UnsafeEntry,
                "The deployment root must be a real directory".into(),
            ));
        }
        Ok(record)
    }
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
            let path = std::env::temp_dir().join(format!("timeback-store-test-{}", Uuid::new_v4()));
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
        let path = std::env::temp_dir().join(format!("timeback-missing-{}", Uuid::new_v4()));
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
}
