//! Power-loss-safe root replacement for an installation mounted by a Live
//! rescue environment.
//!
//! This deliberately does not reuse the boot rollback transaction. Offline
//! recovery has no GRUB entry, initramfs attempt, boot ID, or post-boot
//! confirmation. It does, however, reuse deployment records, lineage, and the
//! same Btrfs filesystem abstraction as the boot recovery engine.

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::lineage::LineageStore;
use crate::model::DeploymentId;
use crate::recovery::{RecoveryFilesystem, SystemRecoveryFilesystem};
use crate::store::DeploymentStore;

const OFFLINE_TRANSACTION_SCHEMA: u32 = 1;
const MAX_TRANSACTION_BYTES: u64 = 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum OfflinePhase {
    Prepared,
    WritableTargetCreated,
    CurrentRootProtected,
    TargetRootActivated,
    Completed,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OfflineTransaction {
    pub schema_version: u32,
    pub id: Uuid,
    pub target_deployment_id: DeploymentId,
    pub phase: OfflinePhase,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl OfflineTransaction {
    fn new(target_deployment_id: DeploymentId) -> Self {
        let now = Utc::now();
        Self {
            schema_version: OFFLINE_TRANSACTION_SCHEMA,
            id: Uuid::new_v4(),
            target_deployment_id,
            phase: OfflinePhase::Prepared,
            created_at: now,
            updated_at: now,
        }
    }

    fn old_root_name(&self) -> String {
        format!("@root.rescue-center-old-{}", self.id.hyphenated())
    }

    fn new_root_name(&self) -> String {
        format!("@root.rescue-center-new-{}", self.id.hyphenated())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OfflineErrorCode {
    Busy,
    InvalidTarget,
    UnsafeLayout,
    CommandFailed,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OfflineError {
    pub code: OfflineErrorCode,
    pub message: String,
}

impl OfflineError {
    fn new(code: OfflineErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for OfflineError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for OfflineError {}

#[derive(Clone, Debug)]
pub struct OfflineRecoveryEngine<F = SystemRecoveryFilesystem> {
    top_level: PathBuf,
    filesystem: F,
}

impl OfflineRecoveryEngine<SystemRecoveryFilesystem> {
    pub fn system(top_level: impl Into<PathBuf>) -> Self {
        Self::new(top_level, SystemRecoveryFilesystem)
    }
}

impl<F: RecoveryFilesystem> OfflineRecoveryEngine<F> {
    pub fn new(top_level: impl Into<PathBuf>, filesystem: F) -> Self {
        Self {
            top_level: top_level.into(),
            filesystem,
        }
    }

    /// Start and fully apply a new offline recovery transaction. If a previous
    /// confirmed transaction was interrupted, it is completed first.
    pub fn restore(
        &self,
        target_deployment_id: DeploymentId,
    ) -> Result<OfflineTransaction, OfflineError> {
        self.validate_layout()?;
        let _lock = self.acquire_lock()?;
        if let Some(transaction) = self.load_pending()? {
            let completed = self.converge(transaction)?;
            if completed.target_deployment_id == target_deployment_id {
                return Ok(completed);
            }
        }
        self.validate_target(target_deployment_id)?;
        let transaction = OfflineTransaction::new(target_deployment_id);
        self.write_pending(&transaction)?;
        self.converge(transaction)
    }

    /// Finish a transaction whose user confirmation was already durably
    /// recorded before power loss. Returns `None` when no repair is pending.
    pub fn resume(&self) -> Result<Option<OfflineTransaction>, OfflineError> {
        self.validate_layout()?;
        let _lock = self.acquire_lock()?;
        self.load_pending()?
            .map(|value| self.converge(value))
            .transpose()
    }

    pub fn pending(&self) -> Result<Option<OfflineTransaction>, OfflineError> {
        self.validate_layout()?;
        self.load_pending()
    }

    pub fn validate(&self) -> Result<(), OfflineError> {
        self.validate_layout()
    }

    fn converge(
        &self,
        mut transaction: OfflineTransaction,
    ) -> Result<OfflineTransaction, OfflineError> {
        self.validate_transaction(&transaction)?;
        if transaction.phase == OfflinePhase::Completed {
            self.archive(&transaction)?;
            return Ok(transaction);
        }
        self.validate_target(transaction.target_deployment_id)?;
        let root = self.top_level.join("@root");
        let old = self.top_level.join(transaction.old_root_name());
        let new = self.top_level.join(transaction.new_root_name());
        let target = self.deployment_root(transaction.target_deployment_id);

        for _ in 0..8 {
            let state = (
                real_directory(&root)?,
                real_directory(&old)?,
                real_directory(&new)?,
            );
            if transaction.phase == OfflinePhase::TargetRootActivated {
                match state {
                    (true, true, false) => {
                        // Commit recovery history before discarding the only
                        // temporary copy of the pre-restore root.
                        self.record_head(transaction.target_deployment_id)?;
                        self.filesystem.delete(&old).map_err(fs_error)?;
                        self.filesystem.sync(&self.top_level).map_err(fs_error)?;
                    }
                    (true, false, false) => {
                        self.record_head(transaction.target_deployment_id)?;
                        transaction.phase = OfflinePhase::Completed;
                        transaction.updated_at = Utc::now();
                        self.write_pending(&transaction)?;
                        self.archive(&transaction)?;
                        return Ok(transaction);
                    }
                    other => return Err(unsafe_state("finalize", other)),
                }
                continue;
            }
            match state {
                (true, false, false) => {
                    self.filesystem.snapshot(&target, &new).map_err(fs_error)?;
                    self.filesystem.sync(&self.top_level).map_err(fs_error)?;
                    self.update_phase(&mut transaction, OfflinePhase::WritableTargetCreated)?;
                }
                (true, false, true) => {
                    self.filesystem.rename(&root, &old).map_err(fs_error)?;
                    self.filesystem.sync(&self.top_level).map_err(fs_error)?;
                    self.update_phase(&mut transaction, OfflinePhase::CurrentRootProtected)?;
                }
                (false, true, true) => {
                    self.filesystem.rename(&new, &root).map_err(fs_error)?;
                    self.filesystem.sync(&self.top_level).map_err(fs_error)?;
                    self.update_phase(&mut transaction, OfflinePhase::TargetRootActivated)?;
                }
                (true, true, false) => {
                    // The rename was durably completed before its journal
                    // update. Advance rather than repeating the replacement.
                    self.update_phase(&mut transaction, OfflinePhase::TargetRootActivated)?;
                }
                (false, true, false) => {
                    // The prepared target vanished before activation. Put the
                    // original root back; never leave the installation rootless.
                    self.filesystem.rename(&old, &root).map_err(fs_error)?;
                    self.filesystem.sync(&self.top_level).map_err(fs_error)?;
                    return Err(OfflineError::new(
                        OfflineErrorCode::UnsafeLayout,
                        "The staged rescue root disappeared; the original root was restored",
                    ));
                }
                other => return Err(unsafe_state("apply", other)),
            }
        }
        Err(OfflineError::new(
            OfflineErrorCode::UnsafeLayout,
            "Offline recovery did not converge",
        ))
    }

    fn validate_layout(&self) -> Result<(), OfflineError> {
        ensure_real_directory(&self.top_level)?;
        for name in ["@home", "@log", "@snapshots", "@containers", "@libvirt"] {
            let path = self.top_level.join(name);
            ensure_real_directory(&path)?;
            self.filesystem.identity(&path).map_err(fs_error)?;
        }
        let root = self.top_level.join("@root");
        if real_directory(&root)? {
            self.filesystem.identity(&root).map_err(fs_error)?;
            if self.filesystem.has_descendants(&root).map_err(fs_error)? {
                return Err(OfflineError::new(
                    OfflineErrorCode::UnsafeLayout,
                    "The current root contains nested Btrfs subvolumes and cannot be replaced safely",
                ));
            }
        } else {
            let pending = self.pending_path();
            match fs::symlink_metadata(&pending) {
                Ok(metadata) if metadata.file_type().is_file() => {}
                Ok(_) => {
                    return Err(OfflineError::new(
                        OfflineErrorCode::UnsafeLayout,
                        "The installation root is missing and the recovery transaction is unsafe",
                    ));
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    return Err(OfflineError::new(
                        OfflineErrorCode::UnsafeLayout,
                        "The installation root is missing and no offline recovery is pending",
                    ));
                }
                Err(error) => {
                    return Err(io_error("Could not inspect the offline transaction", error));
                }
            }
        }
        let root = self.snapshot_root();
        ensure_real_directory(&root)?;
        Ok(())
    }

    fn validate_target(&self, id: DeploymentId) -> Result<(), OfflineError> {
        let record = DeploymentStore::new(self.snapshot_root())
            .load_record(id)
            .map_err(|error| OfflineError::new(OfflineErrorCode::InvalidTarget, error.message))?;
        if !record.can_restore() {
            return Err(OfflineError::new(
                OfflineErrorCode::InvalidTarget,
                "The selected system snapshot is not complete and restorable",
            ));
        }
        let target = self.deployment_root(id);
        ensure_real_directory(&target)?;
        let expected = record.snapshot_uuid.as_deref().ok_or_else(|| {
            OfflineError::new(OfflineErrorCode::InvalidTarget, "Snapshot UUID is missing")
        })?;
        if self.filesystem.identity(&target).map_err(fs_error)? != expected {
            return Err(OfflineError::new(
                OfflineErrorCode::InvalidTarget,
                "The selected snapshot UUID does not match its metadata",
            ));
        }
        if !self.filesystem.is_read_only(&target).map_err(fs_error)? {
            return Err(OfflineError::new(
                OfflineErrorCode::InvalidTarget,
                "The selected system snapshot is not read-only",
            ));
        }
        if self.filesystem.has_descendants(&target).map_err(fs_error)? {
            return Err(OfflineError::new(
                OfflineErrorCode::UnsafeLayout,
                "The selected snapshot contains nested Btrfs subvolumes",
            ));
        }
        Ok(())
    }

    fn record_head(&self, id: DeploymentId) -> Result<(), OfflineError> {
        let store = DeploymentStore::new(self.snapshot_root());
        let discovery = store.discover();
        if !discovery.issues.is_empty() {
            return Err(OfflineError::new(
                OfflineErrorCode::InvalidTarget,
                "Snapshot metadata contains unresolved issues",
            ));
        }
        let lineage = LineageStore::new(self.snapshot_root());
        lineage
            .ensure_initialized(&discovery.deployments)
            .map_err(|error| OfflineError::new(OfflineErrorCode::Io, error.message))?;
        lineage
            .record_offline_head(id)
            .map_err(|error| OfflineError::new(OfflineErrorCode::Io, error.message))?;
        Ok(())
    }

    fn validate_transaction(&self, transaction: &OfflineTransaction) -> Result<(), OfflineError> {
        if transaction.schema_version != OFFLINE_TRANSACTION_SCHEMA
            || transaction.updated_at < transaction.created_at
        {
            return Err(OfflineError::new(
                OfflineErrorCode::InvalidTarget,
                "The offline recovery transaction is invalid",
            ));
        }
        Ok(())
    }

    fn update_phase(
        &self,
        transaction: &mut OfflineTransaction,
        phase: OfflinePhase,
    ) -> Result<(), OfflineError> {
        transaction.phase = phase;
        transaction.updated_at = Utc::now();
        self.write_pending(transaction)
    }

    fn acquire_lock(&self) -> Result<OfflineLock, OfflineError> {
        let path = self.snapshot_root().join("operation.lock");
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)
            .map_err(|error| io_error("Could not open the recovery operation lock", error))?;
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            let error = io::Error::last_os_error();
            return Err(OfflineError::new(
                if error.kind() == io::ErrorKind::WouldBlock {
                    OfflineErrorCode::Busy
                } else {
                    OfflineErrorCode::Io
                },
                format!("Could not lock recovery storage: {error}"),
            ));
        }
        Ok(OfflineLock(file))
    }

    fn load_pending(&self) -> Result<Option<OfflineTransaction>, OfflineError> {
        let path = self.pending_path();
        let metadata = match fs::symlink_metadata(&path) {
            Ok(value) if value.file_type().is_file() => value,
            Ok(_) => {
                return Err(OfflineError::new(
                    OfflineErrorCode::UnsafeLayout,
                    "Offline recovery transaction is not a regular file",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(io_error("Could not inspect offline transaction", error)),
        };
        if metadata.len() > MAX_TRANSACTION_BYTES {
            return Err(OfflineError::new(
                OfflineErrorCode::InvalidTarget,
                "Offline recovery transaction is too large",
            ));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)
            .map_err(|error| io_error("Could not open offline transaction", error))?;
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take(MAX_TRANSACTION_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| io_error("Could not read offline transaction", error))?;
        let transaction: OfflineTransaction = serde_json::from_slice(&bytes).map_err(|error| {
            OfflineError::new(
                OfflineErrorCode::InvalidTarget,
                format!("Offline recovery transaction is invalid JSON: {error}"),
            )
        })?;
        self.validate_transaction(&transaction)?;
        Ok(Some(transaction))
    }

    fn write_pending(&self, transaction: &OfflineTransaction) -> Result<(), OfflineError> {
        let directory = self.transaction_directory()?;
        let target = directory.join("pending.json");
        if let Ok(metadata) = fs::symlink_metadata(&target)
            && !metadata.file_type().is_file()
        {
            return Err(OfflineError::new(
                OfflineErrorCode::UnsafeLayout,
                "Offline recovery transaction target is unsafe",
            ));
        }
        let temporary = directory.join(format!(".{}.tmp", Uuid::new_v4().hyphenated()));
        let bytes = serde_json::to_vec_pretty(transaction).map_err(|error| {
            OfflineError::new(
                OfflineErrorCode::Io,
                format!("Could not serialize transaction: {error}"),
            )
        })?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)
            .map_err(|error| io_error("Could not create offline transaction", error))?;
        let result = (|| -> io::Result<()> {
            file.write_all(&bytes)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            fs::rename(&temporary, &target)?;
            sync_directory(&directory)
        })();
        if let Err(error) = result {
            let _ = fs::remove_file(&temporary);
            return Err(io_error("Could not commit offline transaction", error));
        }
        Ok(())
    }

    fn archive(&self, transaction: &OfflineTransaction) -> Result<(), OfflineError> {
        let directory = self.transaction_directory()?;
        let history = directory.join("history");
        ensure_directory(&history)?;
        let pending = directory.join("pending.json");
        let target = history.join(format!("{}.json", transaction.id.hyphenated()));
        fs::rename(pending, target)
            .and_then(|_| sync_directory(&directory))
            .map_err(|error| io_error("Could not archive offline transaction", error))
    }

    fn transaction_directory(&self) -> Result<PathBuf, OfflineError> {
        let path = self.snapshot_root().join("offline-transactions");
        ensure_directory(&path)?;
        Ok(path)
    }

    fn pending_path(&self) -> PathBuf {
        self.snapshot_root()
            .join("offline-transactions")
            .join("pending.json")
    }

    fn snapshot_root(&self) -> PathBuf {
        self.top_level
            .join("@snapshots/anduinos-btrfs-snapshots-manager")
    }

    fn deployment_root(&self, id: DeploymentId) -> PathBuf {
        self.snapshot_root()
            .join("deployments")
            .join(id.to_string())
            .join("root")
    }
}

struct OfflineLock(File);

impl Drop for OfflineLock {
    fn drop(&mut self) {
        let _ = unsafe { libc::flock(self.0.as_raw_fd(), libc::LOCK_UN) };
    }
}

fn fs_error(error: crate::recovery::RecoveryError) -> OfflineError {
    OfflineError::new(OfflineErrorCode::CommandFailed, error.message)
}

fn real_directory(path: &Path) -> Result<bool, OfflineError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(true),
        Ok(_) => Err(OfflineError::new(
            OfflineErrorCode::UnsafeLayout,
            format!("{} is not a real directory", path.display()),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(io_error(
            &format!("Could not inspect {}", path.display()),
            error,
        )),
    }
}

fn ensure_real_directory(path: &Path) -> Result<(), OfflineError> {
    if real_directory(path)? {
        Ok(())
    } else {
        Err(OfflineError::new(
            OfflineErrorCode::UnsafeLayout,
            format!("{} does not exist", path.display()),
        ))
    }
}

fn ensure_directory(path: &Path) -> Result<(), OfflineError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(()),
        Ok(_) => Err(OfflineError::new(
            OfflineErrorCode::UnsafeLayout,
            format!("{} is not a real directory", path.display()),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => fs::create_dir(path)
            .and_then(|_| fs::set_permissions(path, fs::Permissions::from_mode(0o700)))
            .and_then(|_| sync_directory(path.parent().unwrap_or(path)))
            .map_err(|error| io_error(&format!("Could not create {}", path.display()), error)),
        Err(error) => Err(io_error(
            &format!("Could not inspect {}", path.display()),
            error,
        )),
    }
}

fn sync_directory(path: &Path) -> io::Result<()> {
    File::open(path)?.sync_all()
}

fn unsafe_state(operation: &str, state: (bool, bool, bool)) -> OfflineError {
    OfflineError::new(
        OfflineErrorCode::UnsafeLayout,
        format!(
            "Unsafe offline {operation} state: root={}, old={}, new={}",
            state.0, state.1, state.2
        ),
    )
}

fn io_error(context: &str, error: io::Error) -> OfflineError {
    OfflineError::new(OfflineErrorCode::Io, format!("{context}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    use crate::DEPLOYMENT_SCHEMA_VERSION;
    use crate::model::{DeploymentKind, DeploymentRecord, DeploymentState};

    #[derive(Clone)]
    struct FakeFilesystem {
        target: PathBuf,
        target_uuid: String,
        operations: Arc<Mutex<Vec<String>>>,
    }

    impl RecoveryFilesystem for FakeFilesystem {
        fn snapshot(
            &self,
            source: &Path,
            destination: &Path,
        ) -> Result<(), crate::recovery::RecoveryError> {
            fs::create_dir(destination).unwrap();
            if let Ok(value) = fs::read_to_string(source.join("marker")) {
                fs::write(destination.join("marker"), value).unwrap();
            }
            self.operations.lock().unwrap().push("snapshot".into());
            Ok(())
        }

        fn delete(&self, subvolume: &Path) -> Result<(), crate::recovery::RecoveryError> {
            fs::remove_dir_all(subvolume).unwrap();
            self.operations.lock().unwrap().push("delete".into());
            Ok(())
        }

        fn sync(&self, _filesystem_path: &Path) -> Result<(), crate::recovery::RecoveryError> {
            self.operations.lock().unwrap().push("sync".into());
            Ok(())
        }

        fn rename(
            &self,
            source: &Path,
            destination: &Path,
        ) -> Result<(), crate::recovery::RecoveryError> {
            fs::rename(source, destination).unwrap();
            self.operations.lock().unwrap().push("rename".into());
            Ok(())
        }

        fn identity(&self, subvolume: &Path) -> Result<String, crate::recovery::RecoveryError> {
            Ok(if subvolume == self.target {
                self.target_uuid.clone()
            } else {
                "11111111-1111-4111-8111-111111111111".into()
            })
        }

        fn is_read_only(&self, subvolume: &Path) -> Result<bool, crate::recovery::RecoveryError> {
            Ok(subvolume == self.target)
        }

        fn has_descendants(
            &self,
            _subvolume: &Path,
        ) -> Result<bool, crate::recovery::RecoveryError> {
            Ok(false)
        }
    }

    struct Fixture {
        top: PathBuf,
        target_id: DeploymentId,
        filesystem: FakeFilesystem,
    }

    impl Fixture {
        fn new() -> Self {
            let top =
                std::env::temp_dir().join(format!("anduinos-offline-test-{}", Uuid::new_v4()));
            for name in [
                "@root",
                "@home",
                "@log",
                "@snapshots",
                "@containers",
                "@libvirt",
            ] {
                fs::create_dir_all(top.join(name)).unwrap();
            }
            fs::write(top.join("@root/marker"), "current").unwrap();
            let store = top.join("@snapshots/anduinos-btrfs-snapshots-manager");
            fs::create_dir_all(store.join("metadata")).unwrap();
            let target_id = DeploymentId::new();
            let target = store
                .join("deployments")
                .join(target_id.to_string())
                .join("root");
            fs::create_dir_all(&target).unwrap();
            fs::write(target.join("marker"), "target").unwrap();
            let target_uuid = Uuid::new_v4().hyphenated().to_string();
            let record = DeploymentRecord {
                schema_version: DEPLOYMENT_SCHEMA_VERSION,
                id: target_id,
                parent_id: None,
                kind: DeploymentKind::Manual,
                state: DeploymentState::Ready,
                created_at: Utc::now(),
                title: "Target".into(),
                reason: "Test target".into(),
                schedule_id: None,
                snapshot_uuid: Some(target_uuid.clone()),
                snapshot_parent_uuid: Some(Uuid::new_v4().hyphenated().to_string()),
                kernel_release: Some("7.0.0-test".into()),
                initramfs_sha256: Some("a".repeat(64)),
                boot_artifact_sha256: Some("b".repeat(64)),
                dpkg_status_sha256: Some("c".repeat(64)),
                mok_certificate_sha256: None,
                pinned: false,
                failure: None,
            };
            DeploymentStore::new(&store).write_record(&record).unwrap();
            Self {
                top,
                target_id,
                filesystem: FakeFilesystem {
                    target,
                    target_uuid,
                    operations: Arc::new(Mutex::new(Vec::new())),
                },
            }
        }

        fn engine(&self) -> OfflineRecoveryEngine<FakeFilesystem> {
            OfflineRecoveryEngine::new(&self.top, self.filesystem.clone())
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.top);
        }
    }

    #[test]
    fn offline_restore_replaces_only_root_and_archives_transaction() {
        let fixture = Fixture::new();
        let completed = fixture.engine().restore(fixture.target_id).unwrap();
        assert_eq!(completed.phase, OfflinePhase::Completed);
        assert_eq!(
            fs::read_to_string(fixture.top.join("@root/marker")).unwrap(),
            "target"
        );
        assert!(fixture.top.join("@home").is_dir());
        assert!(!fixture.engine().pending().unwrap().is_some());
        assert!(
            fixture
                .top
                .join("@snapshots/anduinos-btrfs-snapshots-manager/offline-transactions/history")
                .join(format!("{}.json", completed.id))
                .is_file()
        );
    }

    #[test]
    fn interrupted_root_move_converges_without_losing_original_root() {
        let fixture = Fixture::new();
        let engine = fixture.engine();
        let mut transaction = OfflineTransaction::new(fixture.target_id);
        engine.write_pending(&transaction).unwrap();
        let target = engine.deployment_root(fixture.target_id);
        let new = fixture.top.join(transaction.new_root_name());
        let old = fixture.top.join(transaction.old_root_name());
        fixture.filesystem.snapshot(&target, &new).unwrap();
        fixture
            .filesystem
            .rename(&fixture.top.join("@root"), &old)
            .unwrap();
        transaction.phase = OfflinePhase::CurrentRootProtected;
        engine.write_pending(&transaction).unwrap();

        let completed = engine.resume().unwrap().unwrap();
        assert_eq!(completed.phase, OfflinePhase::Completed);
        assert_eq!(
            fs::read_to_string(fixture.top.join("@root/marker")).unwrap(),
            "target"
        );
        assert!(!old.exists());
    }

    #[test]
    fn completed_pending_record_is_archived_after_power_loss() {
        let fixture = Fixture::new();
        let engine = fixture.engine();
        let mut transaction = OfflineTransaction::new(fixture.target_id);
        transaction.phase = OfflinePhase::Completed;
        engine.write_pending(&transaction).unwrap();
        let completed = engine.resume().unwrap().unwrap();
        assert_eq!(completed.id, transaction.id);
        assert!(engine.pending().unwrap().is_none());
    }
}
