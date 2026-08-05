use std::ffi::OsString;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

use chrono::Utc;

use crate::model::{DeploymentId, DeploymentState};
use crate::store::DeploymentStore;
use crate::transaction::{
    MAX_APPLY_ATTEMPTS, RollbackId, RollbackPhase, RollbackTransaction, TransactionError,
    TransactionStore,
};

const BTRFS: &str = "/usr/bin/btrfs";
const MAX_DIAGNOSTIC: usize = 2000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RecoveryOutcome {
    NoAction,
    Applied,
    Reverted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RecoveryCheckpoint {
    ApplyStarted,
    WritableTargetCreated,
    CurrentRootProtected,
    TargetRootActivated,
    BootedUnconfirmedRecorded,
    RevertStarted,
    RestoredRootMovedAside,
    FallbackRootActivated,
    DiscardedRootDeleted,
    RevertedRecorded,
}

impl RecoveryCheckpoint {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ApplyStarted => "apply-started",
            Self::WritableTargetCreated => "writable-target-created",
            Self::CurrentRootProtected => "current-root-protected",
            Self::TargetRootActivated => "target-root-activated",
            Self::BootedUnconfirmedRecorded => "booted-unconfirmed-recorded",
            Self::RevertStarted => "revert-started",
            Self::RestoredRootMovedAside => "restored-root-moved-aside",
            Self::FallbackRootActivated => "fallback-root-activated",
            Self::DiscardedRootDeleted => "discarded-root-deleted",
            Self::RevertedRecorded => "reverted-recorded",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RecoveryErrorCode {
    InvalidTransaction,
    InvalidDeployment,
    UnsafeLayout,
    CommandFailed,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RecoveryError {
    pub code: RecoveryErrorCode,
    pub message: String,
}

impl RecoveryError {
    fn new(code: RecoveryErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for RecoveryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for RecoveryError {}

pub trait RecoveryFilesystem: Clone + Send + Sync + 'static {
    fn snapshot(&self, source: &Path, destination: &Path) -> Result<(), RecoveryError>;
    fn delete(&self, subvolume: &Path) -> Result<(), RecoveryError>;
    fn sync(&self, filesystem_path: &Path) -> Result<(), RecoveryError>;
    fn rename(&self, source: &Path, destination: &Path) -> Result<(), RecoveryError>;
    fn identity(&self, subvolume: &Path) -> Result<String, RecoveryError>;
    fn is_read_only(&self, subvolume: &Path) -> Result<bool, RecoveryError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemRecoveryFilesystem;

impl RecoveryFilesystem for SystemRecoveryFilesystem {
    fn snapshot(&self, source: &Path, destination: &Path) -> Result<(), RecoveryError> {
        run_btrfs(&[
            OsString::from("subvolume"),
            OsString::from("snapshot"),
            source.as_os_str().to_owned(),
            destination.as_os_str().to_owned(),
        ])?;
        Ok(())
    }

    fn delete(&self, subvolume: &Path) -> Result<(), RecoveryError> {
        run_btrfs(&[
            OsString::from("subvolume"),
            OsString::from("delete"),
            OsString::from("--commit-after"),
            subvolume.as_os_str().to_owned(),
        ])?;
        Ok(())
    }

    fn sync(&self, filesystem_path: &Path) -> Result<(), RecoveryError> {
        run_btrfs(&[
            OsString::from("filesystem"),
            OsString::from("sync"),
            filesystem_path.as_os_str().to_owned(),
        ])?;
        Ok(())
    }

    fn rename(&self, source: &Path, destination: &Path) -> Result<(), RecoveryError> {
        fs::rename(source, destination)
            .map_err(|error| io_error("Could not rename a recovery subvolume", error))
    }

    fn identity(&self, subvolume: &Path) -> Result<String, RecoveryError> {
        let output = run_btrfs(&[
            OsString::from("subvolume"),
            OsString::from("show"),
            OsString::from("--raw"),
            subvolume.as_os_str().to_owned(),
        ])?;
        let text = String::from_utf8(output).map_err(|_| {
            RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Btrfs returned a non-UTF-8 subvolume identity",
            )
        })?;
        let value = text
            .lines()
            .find_map(|line| line.trim_start().strip_prefix("UUID:").map(str::trim))
            .ok_or_else(|| {
                RecoveryError::new(
                    RecoveryErrorCode::InvalidDeployment,
                    "Btrfs did not report a snapshot UUID",
                )
            })?;
        let uuid = uuid::Uuid::parse_str(value).map_err(|_| {
            RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Btrfs reported an invalid snapshot UUID",
            )
        })?;
        Ok(uuid.hyphenated().to_string())
    }

    fn is_read_only(&self, subvolume: &Path) -> Result<bool, RecoveryError> {
        let output = run_btrfs(&[
            OsString::from("property"),
            OsString::from("get"),
            OsString::from("-ts"),
            subvolume.as_os_str().to_owned(),
            OsString::from("ro"),
        ])?;
        let value = String::from_utf8(output).map_err(|_| {
            RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Btrfs returned a non-UTF-8 read-only property",
            )
        })?;
        Ok(value.trim() == "ro=true")
    }
}

#[derive(Clone, Debug)]
pub struct RecoveryEngine<F = SystemRecoveryFilesystem> {
    top_level: PathBuf,
    filesystem: F,
}

impl Default for RecoveryEngine<SystemRecoveryFilesystem> {
    fn default() -> Self {
        Self::new("/run/anduinos-waypoint/top", SystemRecoveryFilesystem)
    }
}

impl<F: RecoveryFilesystem> RecoveryEngine<F> {
    pub fn new(top_level: impl Into<PathBuf>, filesystem: F) -> Self {
        Self {
            top_level: top_level.into(),
            filesystem,
        }
    }

    pub fn execute(
        &self,
        requested: Option<RollbackId>,
        boot_id: &str,
    ) -> Result<RecoveryOutcome, RecoveryError> {
        self.execute_with_observer(requested, boot_id, |_| {})
    }

    pub fn execute_with_observer<O>(
        &self,
        requested: Option<RollbackId>,
        boot_id: &str,
        mut checkpoint: O,
    ) -> Result<RecoveryOutcome, RecoveryError>
    where
        O: FnMut(RecoveryCheckpoint),
    {
        ensure_real_directory(&self.top_level)?;
        let store = TransactionStore::new(self.snapshot_root());
        let Some(mut transaction) = store.load_pending().map_err(transaction_error)? else {
            return Ok(RecoveryOutcome::NoAction);
        };

        match transaction.phase {
            RollbackPhase::Preparing
            | RollbackPhase::Reverted
            | RollbackPhase::Confirmed
            | RollbackPhase::Failed => Ok(RecoveryOutcome::NoAction),
            RollbackPhase::Armed => {
                if requested != Some(transaction.id) {
                    return Ok(RecoveryOutcome::NoAction);
                }
                self.validate_deployments(&transaction)?;
                transaction
                    .begin_apply(boot_id, Utc::now())
                    .map_err(transaction_error)?;
                store.update(&transaction).map_err(transaction_error)?;
                checkpoint(RecoveryCheckpoint::ApplyStarted);
                self.apply(&transaction, &mut checkpoint)?;
                transaction
                    .transition(RollbackPhase::BootedUnconfirmed, Utc::now())
                    .map_err(transaction_error)?;
                store.update(&transaction).map_err(transaction_error)?;
                checkpoint(RecoveryCheckpoint::BootedUnconfirmedRecorded);
                Ok(RecoveryOutcome::Applied)
            }
            RollbackPhase::Applying => {
                if requested == Some(transaction.id)
                    && transaction.apply_attempts < MAX_APPLY_ATTEMPTS
                {
                    self.validate_deployments(&transaction)?;
                    transaction
                        .begin_apply(boot_id, Utc::now())
                        .map_err(transaction_error)?;
                    store.update(&transaction).map_err(transaction_error)?;
                    checkpoint(RecoveryCheckpoint::ApplyStarted);
                    self.apply(&transaction, &mut checkpoint)?;
                    transaction
                        .transition(RollbackPhase::BootedUnconfirmed, Utc::now())
                        .map_err(transaction_error)?;
                    store.update(&transaction).map_err(transaction_error)?;
                    checkpoint(RecoveryCheckpoint::BootedUnconfirmedRecorded);
                    Ok(RecoveryOutcome::Applied)
                } else {
                    self.revert_transaction(&store, &mut transaction, &mut checkpoint)
                }
            }
            RollbackPhase::BootedUnconfirmed => {
                if transaction.applying_boot_id.as_deref() == Some(boot_id) {
                    Ok(RecoveryOutcome::NoAction)
                } else {
                    self.revert_transaction(&store, &mut transaction, &mut checkpoint)
                }
            }
            RollbackPhase::Reverting => {
                self.finish_revert(&store, &mut transaction, &mut checkpoint)
            }
        }
    }

    fn validate_deployments(&self, transaction: &RollbackTransaction) -> Result<(), RecoveryError> {
        let root = self.snapshot_root();
        let deployments = DeploymentStore::new(&root);
        let target = deployments
            .load_record(transaction.target_deployment_id)
            .map_err(|error| {
                RecoveryError::new(RecoveryErrorCode::InvalidDeployment, error.message)
            })?;
        if target.state != DeploymentState::PendingRollback || target.failure.is_some() {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Rollback target is not in the pending-rollback state",
            ));
        }
        let fallback = deployments
            .load_record(transaction.fallback_deployment_id)
            .map_err(|error| {
                RecoveryError::new(RecoveryErrorCode::InvalidDeployment, error.message)
            })?;
        if fallback.state != DeploymentState::FallbackProtected || fallback.failure.is_some() {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Rollback fallback is not protected",
            ));
        }
        let target_root = self.deployment_root(transaction.target_deployment_id);
        let fallback_root = self.deployment_root(transaction.fallback_deployment_id);
        ensure_real_directory(&target_root)?;
        ensure_real_directory(&fallback_root)?;
        let expected_uuid = target.snapshot_uuid.as_deref().ok_or_else(|| {
            RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Rollback target has no snapshot UUID",
            )
        })?;
        if self.filesystem.identity(&target_root)? != expected_uuid {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Rollback target snapshot UUID does not match metadata",
            ));
        }
        if !self.filesystem.is_read_only(&target_root)? {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Rollback target snapshot is not read-only",
            ));
        }
        Ok(())
    }

    fn apply(
        &self,
        transaction: &RollbackTransaction,
        checkpoint: &mut impl FnMut(RecoveryCheckpoint),
    ) -> Result<(), RecoveryError> {
        let root = self.top_level.join("@root");
        let old = self.top_level.join(transaction.old_root_name());
        let new = self.top_level.join(transaction.new_root_name());
        let target = self.deployment_root(transaction.target_deployment_id);

        for _ in 0..5 {
            match (
                real_directory(&root)?,
                real_directory(&old)?,
                real_directory(&new)?,
            ) {
                (true, false, false) => {
                    self.filesystem.snapshot(&target, &new)?;
                    self.filesystem.sync(&self.top_level)?;
                    checkpoint(RecoveryCheckpoint::WritableTargetCreated);
                }
                (true, false, true) => {
                    self.filesystem.rename(&root, &old)?;
                    self.filesystem.sync(&self.top_level)?;
                    checkpoint(RecoveryCheckpoint::CurrentRootProtected);
                }
                (false, true, true) => {
                    self.filesystem.rename(&new, &root)?;
                    self.filesystem.sync(&self.top_level)?;
                    checkpoint(RecoveryCheckpoint::TargetRootActivated);
                }
                (true, true, false) => return Ok(()),
                state => return Err(unsafe_state("apply", state)),
            }
        }
        Err(RecoveryError::new(
            RecoveryErrorCode::UnsafeLayout,
            "Rollback apply did not converge",
        ))
    }

    fn revert_transaction(
        &self,
        store: &TransactionStore,
        transaction: &mut RollbackTransaction,
        checkpoint: &mut impl FnMut(RecoveryCheckpoint),
    ) -> Result<RecoveryOutcome, RecoveryError> {
        transaction
            .transition(RollbackPhase::Reverting, Utc::now())
            .map_err(transaction_error)?;
        store.update(transaction).map_err(transaction_error)?;
        checkpoint(RecoveryCheckpoint::RevertStarted);
        self.finish_revert(store, transaction, checkpoint)
    }

    fn finish_revert(
        &self,
        store: &TransactionStore,
        transaction: &mut RollbackTransaction,
        checkpoint: &mut impl FnMut(RecoveryCheckpoint),
    ) -> Result<RecoveryOutcome, RecoveryError> {
        self.revert(transaction, checkpoint)?;
        transaction
            .transition(RollbackPhase::Reverted, Utc::now())
            .map_err(transaction_error)?;
        store.update(transaction).map_err(transaction_error)?;
        checkpoint(RecoveryCheckpoint::RevertedRecorded);
        Ok(RecoveryOutcome::Reverted)
    }

    fn revert(
        &self,
        transaction: &RollbackTransaction,
        checkpoint: &mut impl FnMut(RecoveryCheckpoint),
    ) -> Result<(), RecoveryError> {
        let root = self.top_level.join("@root");
        let old = self.top_level.join(transaction.old_root_name());
        let new = self.top_level.join(transaction.new_root_name());

        for _ in 0..6 {
            match (
                real_directory(&root)?,
                real_directory(&old)?,
                real_directory(&new)?,
            ) {
                (true, false, false) => return Ok(()),
                (true, false, true) => {
                    self.filesystem.delete(&new)?;
                    self.filesystem.sync(&self.top_level)?;
                    checkpoint(RecoveryCheckpoint::DiscardedRootDeleted);
                }
                (false, true, true) => {
                    self.filesystem.rename(&old, &root)?;
                    self.filesystem.sync(&self.top_level)?;
                    checkpoint(RecoveryCheckpoint::FallbackRootActivated);
                }
                (true, true, false) => {
                    self.filesystem.rename(&root, &new)?;
                    self.filesystem.sync(&self.top_level)?;
                    checkpoint(RecoveryCheckpoint::RestoredRootMovedAside);
                }
                (false, true, false) => {
                    self.filesystem.rename(&old, &root)?;
                    self.filesystem.sync(&self.top_level)?;
                    checkpoint(RecoveryCheckpoint::FallbackRootActivated);
                }
                state => return Err(unsafe_state("revert", state)),
            }
        }
        Err(RecoveryError::new(
            RecoveryErrorCode::UnsafeLayout,
            "Rollback revert did not converge",
        ))
    }

    fn snapshot_root(&self) -> PathBuf {
        self.top_level.join("@snapshots/anduinos-waypoint")
    }

    fn deployment_root(&self, id: DeploymentId) -> PathBuf {
        self.snapshot_root()
            .join("deployments")
            .join(id.to_string())
            .join("root")
    }
}

fn run_btrfs(arguments: &[OsString]) -> Result<Vec<u8>, RecoveryError> {
    let output = Command::new(BTRFS)
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()
        .map_err(|error| {
            RecoveryError::new(
                RecoveryErrorCode::CommandFailed,
                format!("Could not execute {BTRFS}: {error}"),
            )
        })?;
    if !output.status.success() {
        let diagnostic = String::from_utf8_lossy(&output.stderr)
            .chars()
            .map(|character| {
                if character.is_control() {
                    ' '
                } else {
                    character
                }
            })
            .take(MAX_DIAGNOSTIC)
            .collect::<String>();
        return Err(RecoveryError::new(
            RecoveryErrorCode::CommandFailed,
            format!("{BTRFS} exited with {}: {diagnostic}", output.status),
        ));
    }
    Ok(output.stdout)
}

fn real_directory(path: &Path) -> Result<bool, RecoveryError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(true),
        Ok(_) => Err(RecoveryError::new(
            RecoveryErrorCode::UnsafeLayout,
            format!("{} is not a real directory", path.display()),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(io_error(
            &format!("Could not inspect {}", path.display()),
            error,
        )),
    }
}

fn ensure_real_directory(path: &Path) -> Result<(), RecoveryError> {
    if real_directory(path)? {
        Ok(())
    } else {
        Err(RecoveryError::new(
            RecoveryErrorCode::UnsafeLayout,
            format!("{} does not exist", path.display()),
        ))
    }
}

fn unsafe_state(operation: &str, state: (bool, bool, bool)) -> RecoveryError {
    RecoveryError::new(
        RecoveryErrorCode::UnsafeLayout,
        format!(
            "Unsafe {operation} subvolume state: root={}, old={}, new={}",
            state.0, state.1, state.2
        ),
    )
}

fn transaction_error(error: TransactionError) -> RecoveryError {
    RecoveryError::new(RecoveryErrorCode::InvalidTransaction, error.message)
}

fn io_error(context: &str, error: io::Error) -> RecoveryError {
    RecoveryError::new(RecoveryErrorCode::Io, format!("{context}: {error}"))
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use chrono::Utc;

    use crate::model::{DeploymentKind, DeploymentRecord};
    use crate::{DEPLOYMENT_SCHEMA_VERSION, transaction::RollbackTransaction};

    use super::*;

    const TARGET_UUID: &str = "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb";
    const FALLBACK_UUID: &str = "cccccccc-4444-4555-8666-dddddddddddd";
    const BOOT_ONE: &str = "11111111-2222-4333-8444-555555555555";
    const BOOT_TWO: &str = "66666666-7777-4888-8999-aaaaaaaaaaaa";

    #[derive(Clone, Default)]
    struct FakeFilesystem {
        fail_at: Arc<Mutex<Option<usize>>>,
        mutation_count: Arc<Mutex<usize>>,
    }

    impl FakeFilesystem {
        fn fail_once_at(&self, operation: usize) {
            *self.fail_at.lock().unwrap() = Some(operation);
            *self.mutation_count.lock().unwrap() = 0;
        }

        fn mutation(&self) -> Result<(), RecoveryError> {
            let mut count = self.mutation_count.lock().unwrap();
            *count += 1;
            if self
                .fail_at
                .lock()
                .unwrap()
                .take_if(|at| *at == *count)
                .is_some()
            {
                return Err(RecoveryError::new(
                    RecoveryErrorCode::CommandFailed,
                    "injected power loss",
                ));
            }
            Ok(())
        }
    }

    impl RecoveryFilesystem for FakeFilesystem {
        fn snapshot(&self, source: &Path, destination: &Path) -> Result<(), RecoveryError> {
            self.mutation()?;
            copy_tree(source, destination);
            Ok(())
        }

        fn delete(&self, subvolume: &Path) -> Result<(), RecoveryError> {
            self.mutation()?;
            fs::remove_dir_all(subvolume).unwrap();
            Ok(())
        }

        fn sync(&self, _filesystem_path: &Path) -> Result<(), RecoveryError> {
            self.mutation()
        }

        fn rename(&self, source: &Path, destination: &Path) -> Result<(), RecoveryError> {
            self.mutation()?;
            fs::rename(source, destination).unwrap();
            Ok(())
        }

        fn identity(&self, _subvolume: &Path) -> Result<String, RecoveryError> {
            Ok(TARGET_UUID.into())
        }

        fn is_read_only(&self, _subvolume: &Path) -> Result<bool, RecoveryError> {
            Ok(true)
        }
    }

    struct Environment {
        root: PathBuf,
        transaction: RollbackTransaction,
    }

    impl Environment {
        fn new() -> Self {
            let root =
                std::env::temp_dir().join(format!("waypoint-recovery-{}", uuid::Uuid::new_v4()));
            fs::create_dir_all(root.join("@root")).unwrap();
            fs::write(root.join("@root/origin"), "current").unwrap();
            let snapshot_root = root.join("@snapshots/anduinos-waypoint");
            fs::create_dir_all(snapshot_root.join("metadata")).unwrap();
            fs::create_dir_all(snapshot_root.join("transactions")).unwrap();
            let target = record("target", TARGET_UUID, DeploymentState::PendingRollback);
            let fallback = record(
                "fallback",
                FALLBACK_UUID,
                DeploymentState::FallbackProtected,
            );
            write_deployment(&snapshot_root, &target, "target");
            write_deployment(&snapshot_root, &fallback, "fallback");
            let mut transaction = RollbackTransaction::new(
                target.id,
                fallback.id,
                "eeeeeeee-1111-4222-8333-ffffffffffff",
                "test-kernel",
            );
            transaction
                .transition(RollbackPhase::Armed, Utc::now())
                .unwrap();
            TransactionStore::new(&snapshot_root)
                .create(&transaction)
                .unwrap();
            Self { root, transaction }
        }

        fn phase(&self) -> RollbackPhase {
            TransactionStore::new(self.root.join("@snapshots/anduinos-waypoint"))
                .load_pending()
                .unwrap()
                .unwrap()
                .phase
        }
    }

    impl Drop for Environment {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }

    fn record(title: &str, uuid: &str, state: DeploymentState) -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind: DeploymentKind::Manual,
            state,
            created_at: Utc::now(),
            title: title.into(),
            reason: "Recovery test".into(),
            snapshot_uuid: Some(uuid.into()),
            snapshot_parent_uuid: Some("ffffffff-1111-4222-8333-aaaaaaaaaaaa".into()),
            kernel_release: Some("test-kernel".into()),
            initramfs_sha256: Some("a".repeat(64)),
            boot_artifact_sha256: Some("b".repeat(64)),
            dpkg_status_sha256: Some("c".repeat(64)),
            mok_certificate_sha256: None,
            pinned: false,
            failure: None,
        }
    }

    fn write_deployment(root: &Path, record: &DeploymentRecord, marker: &str) {
        fs::write(
            root.join("metadata").join(format!("{}.json", record.id)),
            serde_json::to_vec(record).unwrap(),
        )
        .unwrap();
        let deployment = root
            .join("deployments")
            .join(record.id.to_string())
            .join("root");
        fs::create_dir_all(&deployment).unwrap();
        fs::write(deployment.join(marker), marker).unwrap();
    }

    fn copy_tree(source: &Path, destination: &Path) {
        fs::create_dir(destination).unwrap();
        for entry in fs::read_dir(source).unwrap() {
            let entry = entry.unwrap();
            let target = destination.join(entry.file_name());
            if entry.file_type().unwrap().is_dir() {
                copy_tree(&entry.path(), &target);
            } else {
                fs::copy(entry.path(), target).unwrap();
            }
        }
    }

    #[test]
    fn armed_transaction_requires_matching_kernel_request() {
        let environment = Environment::new();
        let engine = RecoveryEngine::new(&environment.root, FakeFilesystem::default());
        assert_eq!(
            engine.execute(None, BOOT_ONE).unwrap(),
            RecoveryOutcome::NoAction
        );
        assert_eq!(environment.phase(), RollbackPhase::Armed);
        assert!(environment.root.join("@root/origin").exists());
    }

    #[test]
    fn applies_target_and_preserves_old_root() {
        let environment = Environment::new();
        let engine = RecoveryEngine::new(&environment.root, FakeFilesystem::default());
        assert_eq!(
            engine
                .execute(Some(environment.transaction.id), BOOT_ONE)
                .unwrap(),
            RecoveryOutcome::Applied
        );
        assert!(environment.root.join("@root/target").exists());
        assert!(
            environment
                .root
                .join(environment.transaction.old_root_name())
                .join("origin")
                .exists()
        );
        assert_eq!(environment.phase(), RollbackPhase::BootedUnconfirmed);
    }

    #[test]
    fn apply_checkpoints_follow_synced_persistent_boundaries() {
        let environment = Environment::new();
        let engine = RecoveryEngine::new(&environment.root, FakeFilesystem::default());
        let mut checkpoints = Vec::new();
        engine
            .execute_with_observer(Some(environment.transaction.id), BOOT_ONE, |checkpoint| {
                checkpoints.push(checkpoint)
            })
            .unwrap();
        assert_eq!(
            checkpoints,
            [
                RecoveryCheckpoint::ApplyStarted,
                RecoveryCheckpoint::WritableTargetCreated,
                RecoveryCheckpoint::CurrentRootProtected,
                RecoveryCheckpoint::TargetRootActivated,
                RecoveryCheckpoint::BootedUnconfirmedRecorded,
            ]
        );
    }

    #[test]
    fn next_boot_reverts_unconfirmed_root() {
        let environment = Environment::new();
        let filesystem = FakeFilesystem::default();
        let engine = RecoveryEngine::new(&environment.root, filesystem);
        engine
            .execute(Some(environment.transaction.id), BOOT_ONE)
            .unwrap();
        assert_eq!(
            engine.execute(None, BOOT_ONE).unwrap(),
            RecoveryOutcome::NoAction
        );
        assert_eq!(
            engine.execute(None, BOOT_TWO).unwrap(),
            RecoveryOutcome::Reverted
        );
        assert!(environment.root.join("@root/origin").exists());
        assert!(!environment.root.join("@root/target").exists());
        assert_eq!(environment.phase(), RollbackPhase::Reverted);
    }

    #[test]
    fn revert_checkpoints_follow_synced_persistent_boundaries() {
        let environment = Environment::new();
        let filesystem = FakeFilesystem::default();
        let engine = RecoveryEngine::new(&environment.root, filesystem);
        engine
            .execute(Some(environment.transaction.id), BOOT_ONE)
            .unwrap();
        let mut checkpoints = Vec::new();
        engine
            .execute_with_observer(None, BOOT_TWO, |checkpoint| checkpoints.push(checkpoint))
            .unwrap();
        assert_eq!(
            checkpoints,
            [
                RecoveryCheckpoint::RevertStarted,
                RecoveryCheckpoint::RestoredRootMovedAside,
                RecoveryCheckpoint::FallbackRootActivated,
                RecoveryCheckpoint::DiscardedRootDeleted,
                RecoveryCheckpoint::RevertedRecorded,
            ]
        );
    }

    #[test]
    fn every_apply_command_failure_can_be_reverted_on_next_boot() {
        for failure in 1..=6 {
            let environment = Environment::new();
            let filesystem = FakeFilesystem::default();
            filesystem.fail_once_at(failure);
            let engine = RecoveryEngine::new(&environment.root, filesystem);
            let _ = engine.execute(Some(environment.transaction.id), BOOT_ONE);
            let outcome = engine.execute(None, BOOT_TWO).unwrap();
            assert!(matches!(
                outcome,
                RecoveryOutcome::Reverted | RecoveryOutcome::NoAction
            ));
            assert!(
                environment.root.join("@root/origin").exists(),
                "failure {failure}"
            );
        }
    }
}
