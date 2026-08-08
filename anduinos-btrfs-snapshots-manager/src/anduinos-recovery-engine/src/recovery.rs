use std::ffi::OsString;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

use chrono::Utc;

use crate::boot::{
    RECOVERY_CONFIRM, copy_regular_file_atomic, ensure_protected_executable, hash_regular_file,
};
use crate::model::DeploymentId;
#[cfg(test)]
use crate::model::DeploymentState;
use crate::store::DeploymentStore;
pub use crate::transaction::RecoveryCheckpoint;
use crate::transaction::{
    MAX_APPLY_ATTEMPTS, RECOVERY_PROTOCOL_VERSION, RollbackId, RollbackPhase, RollbackTransaction,
    TransactionError, TransactionStore,
};

const BTRFS: &str = "/usr/bin/btrfs";
const MAX_DIAGNOSTIC: usize = 2000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RecoveryOutcome {
    NoAction,
    Applied,
    Reverted,
    FailedSafe,
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
        Self::new(
            "/run/anduinos-btrfs-snapshots-manager/top",
            SystemRecoveryFilesystem,
        )
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

    /// Preserve the confirmation engine from the protocol-verified initramfs on the
    /// snapshot-external recovery store. Userspace may restore an older root, so it
    /// must not depend on the restored root's package contents. The current protocol
    /// binds the exact executable digest before any root subvolume is changed.
    pub fn stage_confirmation_artifact(&self, source: &Path) -> Result<bool, RecoveryError> {
        ensure_real_directory(&self.top_level)?;
        let snapshot_root = self.snapshot_root();
        let Some(transaction) = TransactionStore::new(&snapshot_root)
            .load_pending()
            .map_err(transaction_error)?
        else {
            return Ok(false);
        };
        ensure_protected_executable(source).map_err(|error| {
            RecoveryError::new(RecoveryErrorCode::InvalidTransaction, error.message)
        })?;
        let source_digest = hash_regular_file(source).map_err(|error| {
            RecoveryError::new(RecoveryErrorCode::InvalidTransaction, error.message)
        })?;
        if transaction.recovery_protocol_version == RECOVERY_PROTOCOL_VERSION
            && source_digest != transaction.recovery_confirm_sha256
        {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidTransaction,
                "The initramfs confirmation engine does not match the rollback transaction",
            ));
        }
        let recovery_boot = snapshot_root.join("recovery-boot");
        ensure_real_directory(&recovery_boot)?;
        let target = recovery_boot.join(RECOVERY_CONFIRM);
        copy_regular_file_atomic(source, &target, 0o700)
            .map_err(|error| RecoveryError::new(RecoveryErrorCode::Io, error.message))?;
        ensure_protected_executable(&target).map_err(|error| {
            RecoveryError::new(RecoveryErrorCode::InvalidTransaction, error.message)
        })?;
        if transaction.recovery_protocol_version == RECOVERY_PROTOCOL_VERSION
            && hash_regular_file(&target).map_err(|error| {
                RecoveryError::new(RecoveryErrorCode::InvalidTransaction, error.message)
            })? != transaction.recovery_confirm_sha256
        {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidTransaction,
                "The staged confirmation engine does not match the rollback transaction",
            ));
        }
        Ok(true)
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
                    if transaction.initramfs_attempts > 0
                        && transaction.initramfs_boot_id.as_deref() != Some(boot_id)
                    {
                        transaction
                            .record_failure(
                                "A requested recovery boot entered initramfs but did not begin applying the target",
                                Utc::now(),
                            )
                            .map_err(transaction_error)?;
                        store.update(&transaction).map_err(transaction_error)?;
                        return Ok(RecoveryOutcome::FailedSafe);
                    }
                    return Ok(RecoveryOutcome::NoAction);
                }
                if transaction.initramfs_attempts >= MAX_APPLY_ATTEMPTS {
                    transaction
                        .record_failure(
                            "The recovery initramfs entry attempt limit was reached before applying the target",
                            Utc::now(),
                        )
                        .map_err(transaction_error)?;
                    store.update(&transaction).map_err(transaction_error)?;
                    return Ok(RecoveryOutcome::FailedSafe);
                }
                transaction
                    .record_initramfs_entry(boot_id, Utc::now())
                    .map_err(transaction_error)?;
                store.update(&transaction).map_err(transaction_error)?;
                checkpoint(RecoveryCheckpoint::InitramfsEntered);
                persist_checkpoint(
                    &store,
                    &mut transaction,
                    RecoveryCheckpoint::Validating,
                    &mut checkpoint,
                )?;
                if let Err(error) = self.validate_deployments(&transaction) {
                    transaction
                        .record_failure(error.message.clone(), Utc::now())
                        .map_err(transaction_error)?;
                    store.update(&transaction).map_err(transaction_error)?;
                    return Err(error);
                }
                transaction
                    .begin_apply(boot_id, Utc::now())
                    .map_err(transaction_error)?;
                store.update(&transaction).map_err(transaction_error)?;
                checkpoint(RecoveryCheckpoint::ApplyStarted);
                self.apply(&store, &mut transaction, &mut checkpoint)?;
                transaction
                    .transition(RollbackPhase::BootedUnconfirmed, Utc::now())
                    .map_err(transaction_error)?;
                persist_checkpoint(
                    &store,
                    &mut transaction,
                    RecoveryCheckpoint::BootedUnconfirmedRecorded,
                    &mut checkpoint,
                )?;
                Ok(RecoveryOutcome::Applied)
            }
            RollbackPhase::Applying => {
                if requested == Some(transaction.id)
                    && transaction.apply_attempts < MAX_APPLY_ATTEMPTS
                    && transaction.initramfs_attempts < MAX_APPLY_ATTEMPTS
                {
                    transaction
                        .record_initramfs_entry(boot_id, Utc::now())
                        .map_err(transaction_error)?;
                    store.update(&transaction).map_err(transaction_error)?;
                    checkpoint(RecoveryCheckpoint::InitramfsEntered);
                    persist_checkpoint(
                        &store,
                        &mut transaction,
                        RecoveryCheckpoint::Validating,
                        &mut checkpoint,
                    )?;
                    self.validate_deployments(&transaction)?;
                    transaction
                        .begin_apply(boot_id, Utc::now())
                        .map_err(transaction_error)?;
                    store.update(&transaction).map_err(transaction_error)?;
                    checkpoint(RecoveryCheckpoint::ApplyStarted);
                    self.apply(&store, &mut transaction, &mut checkpoint)?;
                    transaction
                        .transition(RollbackPhase::BootedUnconfirmed, Utc::now())
                        .map_err(transaction_error)?;
                    persist_checkpoint(
                        &store,
                        &mut transaction,
                        RecoveryCheckpoint::BootedUnconfirmedRecorded,
                        &mut checkpoint,
                    )?;
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
        if transaction.recovery_protocol_version == RECOVERY_PROTOCOL_VERSION {
            let confirm = root.join("recovery-boot").join(RECOVERY_CONFIRM);
            ensure_protected_executable(&confirm).map_err(|error| {
                RecoveryError::new(RecoveryErrorCode::InvalidTransaction, error.message)
            })?;
            let digest = hash_regular_file(&confirm).map_err(|error| {
                RecoveryError::new(RecoveryErrorCode::InvalidTransaction, error.message)
            })?;
            if digest != transaction.recovery_confirm_sha256 {
                return Err(RecoveryError::new(
                    RecoveryErrorCode::InvalidTransaction,
                    "The recovery confirmation artifact no longer matches the rollback transaction",
                ));
            }
        }
        let deployments = DeploymentStore::new(&root);
        let target = deployments
            .load_record(transaction.target_deployment_id)
            .map_err(|error| {
                RecoveryError::new(RecoveryErrorCode::InvalidDeployment, error.message)
            })?;
        if !target.can_restore() {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Rollback target is not a complete, healthy snapshot",
            ));
        }
        let fallback = deployments
            .load_record(transaction.fallback_deployment_id)
            .map_err(|error| {
                RecoveryError::new(RecoveryErrorCode::InvalidDeployment, error.message)
            })?;
        if !fallback.can_restore() || fallback.kind != crate::model::DeploymentKind::PreRollback {
            return Err(RecoveryError::new(
                RecoveryErrorCode::InvalidDeployment,
                "Rollback fallback is not a complete safety snapshot",
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
        store: &TransactionStore,
        transaction: &mut RollbackTransaction,
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
                    persist_checkpoint(
                        store,
                        transaction,
                        RecoveryCheckpoint::WritableTargetCreated,
                        checkpoint,
                    )?;
                }
                (true, false, true) => {
                    self.filesystem.rename(&root, &old)?;
                    self.filesystem.sync(&self.top_level)?;
                    persist_checkpoint(
                        store,
                        transaction,
                        RecoveryCheckpoint::CurrentRootProtected,
                        checkpoint,
                    )?;
                }
                (false, true, true) => {
                    self.filesystem.rename(&new, &root)?;
                    self.filesystem.sync(&self.top_level)?;
                    persist_checkpoint(
                        store,
                        transaction,
                        RecoveryCheckpoint::TargetRootActivated,
                        checkpoint,
                    )?;
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
        persist_checkpoint(
            store,
            transaction,
            RecoveryCheckpoint::RevertStarted,
            checkpoint,
        )?;
        self.finish_revert(store, transaction, checkpoint)
    }

    fn finish_revert(
        &self,
        store: &TransactionStore,
        transaction: &mut RollbackTransaction,
        checkpoint: &mut impl FnMut(RecoveryCheckpoint),
    ) -> Result<RecoveryOutcome, RecoveryError> {
        self.revert(store, transaction, checkpoint)?;
        transaction
            .transition(RollbackPhase::Reverted, Utc::now())
            .map_err(transaction_error)?;
        persist_checkpoint(
            store,
            transaction,
            RecoveryCheckpoint::RevertedRecorded,
            checkpoint,
        )?;
        Ok(RecoveryOutcome::Reverted)
    }

    fn revert(
        &self,
        store: &TransactionStore,
        transaction: &mut RollbackTransaction,
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
                    persist_checkpoint(
                        store,
                        transaction,
                        RecoveryCheckpoint::DiscardedRootDeleted,
                        checkpoint,
                    )?;
                }
                (false, true, true) => {
                    self.filesystem.rename(&old, &root)?;
                    self.filesystem.sync(&self.top_level)?;
                    persist_checkpoint(
                        store,
                        transaction,
                        RecoveryCheckpoint::FallbackRootActivated,
                        checkpoint,
                    )?;
                }
                (true, true, false) => {
                    self.filesystem.rename(&root, &new)?;
                    self.filesystem.sync(&self.top_level)?;
                    persist_checkpoint(
                        store,
                        transaction,
                        RecoveryCheckpoint::RestoredRootMovedAside,
                        checkpoint,
                    )?;
                }
                (false, true, false) => {
                    self.filesystem.rename(&old, &root)?;
                    self.filesystem.sync(&self.top_level)?;
                    persist_checkpoint(
                        store,
                        transaction,
                        RecoveryCheckpoint::FallbackRootActivated,
                        checkpoint,
                    )?;
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

fn persist_checkpoint(
    store: &TransactionStore,
    transaction: &mut RollbackTransaction,
    value: RecoveryCheckpoint,
    observer: &mut impl FnMut(RecoveryCheckpoint),
) -> Result<(), RecoveryError> {
    transaction
        .record_checkpoint(value, Utc::now())
        .map_err(transaction_error)?;
    store.update(transaction).map_err(transaction_error)?;
    observer(value);
    Ok(())
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
            let root = std::env::temp_dir().join(format!(
                "btrfs-snapshots-manager-recovery-{}",
                uuid::Uuid::new_v4()
            ));
            fs::create_dir_all(root.join("@root")).unwrap();
            fs::write(root.join("@root/origin"), "current").unwrap();
            let snapshot_root = root.join("@snapshots/anduinos-btrfs-snapshots-manager");
            fs::create_dir_all(snapshot_root.join("metadata")).unwrap();
            fs::create_dir_all(snapshot_root.join("transactions")).unwrap();
            fs::create_dir_all(snapshot_root.join("recovery-boot")).unwrap();
            let confirm = snapshot_root.join("recovery-boot/confirm");
            fs::write(&confirm, "trusted-confirmation-engine").unwrap();
            fs::set_permissions(
                &confirm,
                <fs::Permissions as std::os::unix::fs::PermissionsExt>::from_mode(0o700),
            )
            .unwrap();
            let target = record("target", TARGET_UUID, DeploymentState::Ready);
            let mut fallback = record("fallback", FALLBACK_UUID, DeploymentState::Ready);
            fallback.kind = crate::model::DeploymentKind::PreRollback;
            write_deployment(&snapshot_root, &target, "target");
            write_deployment(&snapshot_root, &fallback, "fallback");
            let mut transaction = RollbackTransaction::new(
                target.id,
                fallback.id,
                "eeeeeeee-1111-4222-8333-ffffffffffff",
                "test-kernel",
                "a".repeat(64),
                "b".repeat(64),
                hash_regular_file(&confirm).unwrap(),
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
            TransactionStore::new(
                self.root
                    .join("@snapshots/anduinos-btrfs-snapshots-manager"),
            )
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
            schedule_id: None,
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
    fn initramfs_stages_only_the_transaction_bound_confirmation_engine() {
        let environment = Environment::new();
        let source = environment.root.join("initramfs-confirm");
        fs::write(&source, "trusted-confirmation-engine").unwrap();
        fs::set_permissions(
            &source,
            <fs::Permissions as std::os::unix::fs::PermissionsExt>::from_mode(0o700),
        )
        .unwrap();
        let target = environment
            .root
            .join("@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm");
        fs::write(&target, "stale").unwrap();
        fs::set_permissions(
            &target,
            <fs::Permissions as std::os::unix::fs::PermissionsExt>::from_mode(0o700),
        )
        .unwrap();

        let engine = RecoveryEngine::new(&environment.root, FakeFilesystem::default());
        assert!(engine.stage_confirmation_artifact(&source).unwrap());
        assert_eq!(
            fs::read_to_string(&target).unwrap(),
            "trusted-confirmation-engine"
        );

        fs::write(&source, "untrusted-confirmation-engine").unwrap();
        assert_eq!(
            engine
                .stage_confirmation_artifact(&source)
                .unwrap_err()
                .code,
            RecoveryErrorCode::InvalidTransaction
        );
        assert_eq!(
            fs::read_to_string(&target).unwrap(),
            "trusted-confirmation-engine"
        );
    }

    #[test]
    fn recovery_refuses_a_tampered_staged_confirmation_engine_before_root_mutation() {
        let environment = Environment::new();
        let confirm = environment
            .root
            .join("@snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm");
        fs::write(&confirm, "tampered").unwrap();
        fs::set_permissions(
            &confirm,
            <fs::Permissions as std::os::unix::fs::PermissionsExt>::from_mode(0o700),
        )
        .unwrap();
        let engine = RecoveryEngine::new(&environment.root, FakeFilesystem::default());
        assert_eq!(
            engine
                .execute(Some(environment.transaction.id), BOOT_ONE)
                .unwrap_err()
                .code,
            RecoveryErrorCode::InvalidTransaction
        );
        assert!(environment.root.join("@root/origin").exists());
    }

    #[test]
    fn exhausted_armed_entries_fail_safely_instead_of_looping_forever() {
        let environment = Environment::new();
        let snapshot_root = environment
            .root
            .join("@snapshots/anduinos-btrfs-snapshots-manager");
        let store = TransactionStore::new(&snapshot_root);
        let mut transaction = store.load_pending().unwrap().unwrap();
        for boot_id in [BOOT_ONE, BOOT_TWO, "33333333-3333-4333-8333-333333333333"] {
            transaction
                .record_initramfs_entry(boot_id, Utc::now())
                .unwrap();
        }
        store.update(&transaction).unwrap();

        let engine = RecoveryEngine::new(&environment.root, FakeFilesystem::default());
        assert_eq!(
            engine
                .execute(
                    Some(environment.transaction.id),
                    "44444444-4444-4444-8444-444444444444",
                )
                .unwrap(),
            RecoveryOutcome::FailedSafe
        );
        assert_eq!(environment.phase(), RollbackPhase::Failed);
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
                RecoveryCheckpoint::InitramfsEntered,
                RecoveryCheckpoint::Validating,
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
    fn exhausted_initramfs_entries_revert_instead_of_looping_forever() {
        let environment = Environment::new();
        let snapshot_root = environment
            .root
            .join("@snapshots/anduinos-btrfs-snapshots-manager");
        let store = TransactionStore::new(&snapshot_root);
        let mut transaction = store.load_pending().unwrap().unwrap();
        transaction
            .record_initramfs_entry(BOOT_ONE, Utc::now())
            .unwrap();
        transaction.begin_apply(BOOT_ONE, Utc::now()).unwrap();
        transaction
            .record_initramfs_entry(BOOT_TWO, Utc::now())
            .unwrap();
        transaction
            .record_initramfs_entry("33333333-3333-4333-8333-333333333333", Utc::now())
            .unwrap();
        store.update(&transaction).unwrap();

        let engine = RecoveryEngine::new(&environment.root, FakeFilesystem::default());
        assert_eq!(
            engine
                .execute(
                    Some(environment.transaction.id),
                    "44444444-4444-4444-8444-444444444444",
                )
                .unwrap(),
            RecoveryOutcome::Reverted
        );
        assert_eq!(environment.phase(), RollbackPhase::Reverted);
        assert!(environment.root.join("@root/origin").exists());
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
