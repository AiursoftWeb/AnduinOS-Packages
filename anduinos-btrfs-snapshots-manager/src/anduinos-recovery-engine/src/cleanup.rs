use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::RECOVERY_STORE_ROOT;
use crate::transaction::{RollbackId, RollbackTransaction};

pub const ROOT_CLEANUP_SCHEMA_VERSION: u32 = 1;
const CLEANUP_DIRECTORY: &str = "cleanup-pending";
const MAX_CLEANUP_BYTES: u64 = 1024 * 1024;
const MAX_CLEANUP_RECORDS: usize = 1024;
const MAX_BLOCKED_SUBVOLUMES: usize = 256;
const MAX_DIAGNOSTIC_BYTES: usize = 4096;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RootCleanupRecord {
    pub schema_version: u32,
    pub transaction_id: RollbackId,
    pub old_root_name: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub attempts: u32,
    pub blocked_subvolumes: Vec<String>,
    pub last_error: Option<String>,
}

impl RootCleanupRecord {
    pub fn new(transaction: &RollbackTransaction) -> Self {
        let now = Utc::now();
        Self {
            schema_version: ROOT_CLEANUP_SCHEMA_VERSION,
            transaction_id: transaction.id,
            old_root_name: transaction.old_root_name(),
            created_at: now,
            updated_at: now,
            attempts: 0,
            blocked_subvolumes: Vec::new(),
            last_error: None,
        }
    }

    pub fn record_deferred(
        &mut self,
        blocked_subvolumes: Vec<String>,
        diagnostic: impl AsRef<str>,
    ) -> Result<(), RootCleanupError> {
        self.attempts = self.attempts.saturating_add(1);
        self.updated_at = Utc::now();
        self.blocked_subvolumes = blocked_subvolumes;
        self.last_error = Some(bounded_diagnostic(diagnostic.as_ref()));
        self.validate()
    }

    pub fn validate(&self) -> Result<(), RootCleanupError> {
        if self.schema_version != ROOT_CLEANUP_SCHEMA_VERSION {
            return Err(RootCleanupError::new(format!(
                "Unsupported root cleanup schema {}",
                self.schema_version
            )));
        }
        if self.old_root_name != format!("@root.snapshots-manager-old-{}", self.transaction_id) {
            return Err(RootCleanupError::new(
                "Root cleanup record has an invalid old-root name",
            ));
        }
        if self.blocked_subvolumes.len() > MAX_BLOCKED_SUBVOLUMES
            || self
                .blocked_subvolumes
                .iter()
                .any(|path| !safe_relative_path(path))
        {
            return Err(RootCleanupError::new(
                "Root cleanup record contains an unsafe blocked subvolume",
            ));
        }
        if self.last_error.as_deref().is_some_and(|diagnostic| {
            diagnostic.is_empty()
                || diagnostic.len() > MAX_DIAGNOSTIC_BYTES
                || diagnostic.chars().any(|character| {
                    character.is_control() && character != '\n' && character != '\t'
                })
        }) {
            return Err(RootCleanupError::new("Root cleanup diagnostic is invalid"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RootCleanupError {
    pub message: String,
}

impl RootCleanupError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for RootCleanupError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for RootCleanupError {}

#[derive(Clone, Debug)]
pub struct RootCleanupStore {
    directory: PathBuf,
}

impl RootCleanupStore {
    pub fn new(snapshot_root: impl AsRef<Path>) -> Self {
        Self {
            directory: snapshot_root.as_ref().join(CLEANUP_DIRECTORY),
        }
    }

    pub fn schedule(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<RootCleanupRecord, RootCleanupError> {
        let record = RootCleanupRecord::new(transaction);
        if let Some(existing) = self.load(transaction.id)? {
            if existing.old_root_name != record.old_root_name {
                return Err(RootCleanupError::new(
                    "Existing root cleanup record targets a different old root",
                ));
            }
            return Ok(existing);
        }
        self.write_atomic(&record)?;
        Ok(record)
    }

    pub fn update(&self, record: &RootCleanupRecord) -> Result<(), RootCleanupError> {
        record.validate()?;
        if self.load(record.transaction_id)?.is_none() {
            return Err(RootCleanupError::new(
                "Root cleanup record disappeared before it could be updated",
            ));
        }
        self.write_atomic(record)
    }

    pub fn remove(&self, transaction_id: RollbackId) -> Result<(), RootCleanupError> {
        let target = self.record_path(transaction_id);
        match fs::symlink_metadata(&target) {
            Ok(metadata) if metadata.file_type().is_file() => {}
            Ok(_) => {
                return Err(RootCleanupError::new(
                    "Root cleanup target is not a regular file",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(io_error("Could not inspect root cleanup record", error)),
        }
        fs::remove_file(&target)
            .and_then(|_| sync_directory(&self.directory))
            .map_err(|error| io_error("Could not remove root cleanup record", error))
    }

    pub fn list(&self) -> Result<Vec<RootCleanupRecord>, RootCleanupError> {
        match fs::symlink_metadata(&self.directory) {
            Ok(metadata) if metadata.file_type().is_dir() => {}
            Ok(_) => {
                return Err(RootCleanupError::new(
                    "Root cleanup path is not a real directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => return Err(io_error("Could not inspect root cleanup directory", error)),
        }
        let mut records = Vec::new();
        for entry in fs::read_dir(&self.directory)
            .map_err(|error| io_error("Could not list root cleanup records", error))?
        {
            let entry =
                entry.map_err(|error| io_error("Could not read root cleanup entry", error))?;
            if records.len() >= MAX_CLEANUP_RECORDS {
                return Err(RootCleanupError::new(
                    "Root cleanup record count exceeds the safety limit",
                ));
            }
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                return Err(RootCleanupError::new(
                    "Root cleanup record has a non-UTF-8 name",
                ));
            };
            let Some(id) = name.strip_suffix(".json") else {
                return Err(RootCleanupError::new(
                    "Root cleanup directory contains an unexpected entry",
                ));
            };
            let id = id
                .parse::<RollbackId>()
                .map_err(|_| RootCleanupError::new("Root cleanup filename is not a UUID"))?;
            let record = self.load(id)?.ok_or_else(|| {
                RootCleanupError::new("Root cleanup record disappeared while listing it")
            })?;
            records.push(record);
        }
        records.sort_by_key(|record| record.created_at);
        Ok(records)
    }

    fn load(
        &self,
        transaction_id: RollbackId,
    ) -> Result<Option<RootCleanupRecord>, RootCleanupError> {
        let target = self.record_path(transaction_id);
        let metadata = match fs::symlink_metadata(&target) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(io_error("Could not inspect root cleanup record", error)),
        };
        if !metadata.file_type().is_file() {
            return Err(RootCleanupError::new(
                "Root cleanup record is not a regular file",
            ));
        }
        if metadata.len() > MAX_CLEANUP_BYTES {
            return Err(RootCleanupError::new(
                "Root cleanup record exceeds the safety limit",
            ));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&target)
            .map_err(|error| io_error("Could not open root cleanup record", error))?;
        let mut bytes = Vec::with_capacity(metadata.len() as usize);
        Read::by_ref(&mut file)
            .take(MAX_CLEANUP_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| io_error("Could not read root cleanup record", error))?;
        if bytes.len() as u64 > MAX_CLEANUP_BYTES {
            return Err(RootCleanupError::new(
                "Root cleanup record exceeds the safety limit",
            ));
        }
        let record: RootCleanupRecord = serde_json::from_slice(&bytes).map_err(|error| {
            RootCleanupError::new(format!("Invalid root cleanup JSON: {error}"))
        })?;
        record.validate()?;
        if record.transaction_id != transaction_id {
            return Err(RootCleanupError::new(
                "Root cleanup filename does not match its transaction ID",
            ));
        }
        Ok(Some(record))
    }

    fn write_atomic(&self, record: &RootCleanupRecord) -> Result<(), RootCleanupError> {
        record.validate()?;
        ensure_directory(&self.directory)?;
        let serialized = serde_json::to_vec_pretty(record).map_err(|error| {
            RootCleanupError::new(format!("Could not serialize root cleanup record: {error}"))
        })?;
        let target = self.record_path(record.transaction_id);
        if let Ok(metadata) = fs::symlink_metadata(&target)
            && !metadata.file_type().is_file()
        {
            return Err(RootCleanupError::new(
                "Root cleanup target is not a regular file",
            ));
        }
        let temporary = self.directory.join(format!(
            ".{}.{}.tmp",
            record.transaction_id,
            Uuid::new_v4().hyphenated()
        ));
        let result = (|| -> io::Result<()> {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
                .open(&temporary)?;
            file.write_all(&serialized)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            fs::rename(&temporary, &target)?;
            sync_directory(&self.directory)
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result.map_err(|error| io_error("Could not commit root cleanup record", error))
    }

    fn record_path(&self, transaction_id: RollbackId) -> PathBuf {
        self.directory.join(format!("{transaction_id}.json"))
    }
}

impl Default for RootCleanupStore {
    fn default() -> Self {
        Self::new(RECOVERY_STORE_ROOT)
    }
}

pub fn bounded_diagnostic(value: &str) -> String {
    let mut result = String::new();
    for character in value.trim().chars() {
        let character = if character == '\r' || character == '\n' || character == '\t' {
            ' '
        } else if character.is_control() {
            '\u{fffd}'
        } else {
            character
        };
        if result.len() + character.len_utf8() > MAX_DIAGNOSTIC_BYTES {
            break;
        }
        result.push(character);
    }
    if result.is_empty() {
        "No diagnostic was provided".into()
    } else {
        result
    }
}

fn safe_relative_path(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 4096
        && !value.chars().any(char::is_control)
        && Path::new(value)
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
}

fn ensure_directory(path: &Path) -> Result<(), RootCleanupError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(()),
        Ok(_) => Err(RootCleanupError::new(
            "Root cleanup path is not a real directory",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let parent = path
                .parent()
                .ok_or_else(|| RootCleanupError::new("Root cleanup directory has no parent"))?;
            fs::DirBuilder::new()
                .mode(0o700)
                .create(path)
                .and_then(|_| sync_directory(parent))
                .map_err(|error| io_error("Could not create root cleanup directory", error))
        }
        Err(error) => Err(io_error("Could not inspect root cleanup directory", error)),
    }
}

fn sync_directory(path: &Path) -> io::Result<()> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)?
        .sync_all()
}

fn io_error(context: &str, error: io::Error) -> RootCleanupError {
    RootCleanupError::new(format!("{context}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::DeploymentId;
    use crate::transaction::TransactionStore;

    struct TestStore(PathBuf);

    impl TestStore {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "snapshots-manager-root-cleanup-{}",
                Uuid::new_v4().hyphenated()
            ));
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }

        fn store(&self) -> RootCleanupStore {
            RootCleanupStore::new(&self.0)
        }
    }

    impl Drop for TestStore {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    fn transaction() -> RollbackTransaction {
        RollbackTransaction::new(
            DeploymentId::new(),
            DeploymentId::new(),
            "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb",
            "7.0.0-test",
            "a".repeat(64),
            "b".repeat(64),
            "c".repeat(64),
        )
    }

    #[test]
    fn cleanup_records_are_independent_and_resumable() {
        let environment = TestStore::new();
        let transaction = transaction();
        let mut record = environment.store().schedule(&transaction).unwrap();
        record
            .record_deferred(
                vec!["var/lib/machines".into()],
                "old-root descendant is not empty",
            )
            .unwrap();
        environment.store().update(&record).unwrap();
        assert_eq!(environment.store().list().unwrap(), vec![record.clone()]);
        assert_eq!(
            environment.store().schedule(&transaction).unwrap(),
            record,
            "rescheduling must preserve the cleanup attempt history"
        );
        environment.store().remove(transaction.id).unwrap();
        assert!(environment.store().list().unwrap().is_empty());
    }

    #[test]
    fn pending_cleanup_does_not_block_a_new_rollback_transaction() {
        let environment = TestStore::new();
        fs::create_dir_all(environment.0.join("transactions")).unwrap();
        let completed = transaction();
        environment.store().schedule(&completed).unwrap();

        let next = transaction();
        TransactionStore::new(&environment.0).create(&next).unwrap();
        assert_eq!(
            TransactionStore::new(&environment.0)
                .load_pending()
                .unwrap(),
            Some(next)
        );
        assert_eq!(environment.store().list().unwrap().len(), 1);
    }

    #[test]
    fn diagnostics_are_bounded_and_single_line() {
        let diagnostic = bounded_diagnostic(&format!("failure\n{}", "x".repeat(5000)));
        assert!(diagnostic.len() <= MAX_DIAGNOSTIC_BYTES);
        assert!(!diagnostic.contains('\n'));
        assert!(diagnostic.starts_with("failure "));
    }
}
