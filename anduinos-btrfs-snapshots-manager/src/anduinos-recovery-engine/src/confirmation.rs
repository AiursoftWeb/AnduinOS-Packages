use std::ffi::OsStr;
use std::fmt;
use std::fs;
use std::path::Path;
use std::process::Command;

use chrono::Utc;

use crate::boot::BootIntegration;
use crate::layout::{self, LayoutReport};
use crate::lineage::{ActivationOutcome, LineageStore};
#[cfg(test)]
use crate::model::DeploymentState;
use crate::model::{DeploymentId, DeploymentRecord};
use crate::store::DeploymentStore;
use crate::transaction::{RollbackId, RollbackPhase, RollbackTransaction, TransactionStore};

const BTRFS: &str = "/usr/bin/btrfs";
const MOUNT: &str = "/usr/bin/mount";
const UMOUNT: &str = "/usr/bin/umount";
const UPDATE_GRUB: &str = "/usr/sbin/update-grub";
const COMMAND_PATH: &str =
    "/usr/libexec/anduinos-btrfs-snapshots-manager/no-os-prober:/usr/sbin:/usr/bin:/sbin:/bin";
const BOOT_ID: &str = "/proc/sys/kernel/random/boot_id";
const KERNEL_RELEASE: &str = "/proc/sys/kernel/osrelease";
const KERNEL_COMMAND_LINE: &str = "/proc/cmdline";
const TOP_LEVEL: &str = "/run/anduinos-btrfs-snapshots-manager/top";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConfirmationOutcome {
    NoAction,
    Confirmed,
    RevertedRecorded,
    FailedRecorded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConfirmationErrorCode {
    InvalidTransaction,
    IdentityMismatch,
    StateCommit,
    CommandFailed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfirmationError {
    pub code: ConfirmationErrorCode,
    pub message: String,
}

impl ConfirmationError {
    fn new(code: ConfirmationErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for ConfirmationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for ConfirmationError {}

pub trait ConfirmationBackend {
    fn pending(&self) -> Result<Option<RollbackTransaction>, ConfirmationError>;
    fn deployment(&self, id: DeploymentId) -> Result<DeploymentRecord, ConfirmationError>;
    fn boot_id(&self) -> Result<String, ConfirmationError>;
    fn kernel_release(&self) -> Result<String, ConfirmationError>;
    fn requested_rollback(&self) -> Result<Option<RollbackId>, ConfirmationError>;
    fn current_snapshot_parent_uuid(&self) -> Result<String, ConfirmationError>;
    fn update_transaction(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<(), ConfirmationError>;
    fn delete_old_root(&self, transaction: &RollbackTransaction) -> Result<(), ConfirmationError>;
    fn remove_transaction(&self) -> Result<(), ConfirmationError>;
    fn archive_transaction(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<(), ConfirmationError>;
    fn clear_once(&self) -> Result<(), ConfirmationError>;
    fn regenerate_grub(&self) -> Result<(), ConfirmationError>;
    fn record_lineage_activation(
        &self,
        transaction: &RollbackTransaction,
        outcome: ActivationOutcome,
    ) -> Result<(), ConfirmationError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemConfirmationBackend;

impl ConfirmationBackend for SystemConfirmationBackend {
    fn pending(&self) -> Result<Option<RollbackTransaction>, ConfirmationError> {
        TransactionStore::default()
            .load_pending()
            .map_err(transaction_error)
    }

    fn deployment(&self, id: DeploymentId) -> Result<DeploymentRecord, ConfirmationError> {
        DeploymentStore::default().load_record(id).map_err(|error| {
            ConfirmationError::new(ConfirmationErrorCode::InvalidTransaction, error.message)
        })
    }

    fn boot_id(&self) -> Result<String, ConfirmationError> {
        read_canonical_uuid(Path::new(BOOT_ID), "boot ID")
    }

    fn kernel_release(&self) -> Result<String, ConfirmationError> {
        let value = fs::read_to_string(KERNEL_RELEASE).map_err(|error| {
            ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                format!("Could not read the running kernel release: {error}"),
            )
        })?;
        let value = value.trim();
        if value.is_empty()
            || value.len() > 128
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
        {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "The running kernel release is unsafe",
            ));
        }
        Ok(value.into())
    }

    fn requested_rollback(&self) -> Result<Option<RollbackId>, ConfirmationError> {
        let command_line = fs::read_to_string(KERNEL_COMMAND_LINE).map_err(|error| {
            ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                format!("Could not read the kernel command line: {error}"),
            )
        })?;
        parse_requested_rollback(&command_line)
    }

    fn current_snapshot_parent_uuid(&self) -> Result<String, ConfirmationError> {
        let output = run_command(
            Path::new(BTRFS),
            &[
                OsStr::new("subvolume"),
                OsStr::new("show"),
                OsStr::new("--raw"),
                OsStr::new("/"),
            ],
        )?;
        parse_parent_uuid(&output)
    }

    fn update_transaction(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<(), ConfirmationError> {
        TransactionStore::default()
            .update(transaction)
            .map_err(transaction_error)
    }

    fn delete_old_root(&self, transaction: &RollbackTransaction) -> Result<(), ConfirmationError> {
        let report = layout::inspect_current();
        ensure_supported_root(&report)?;
        let source = report.root_source.as_deref().ok_or_else(|| {
            ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "The root filesystem source is unavailable",
            )
        })?;
        let top = Path::new(TOP_LEVEL);
        ensure_mount_directory(top)?;
        run_command(
            Path::new(MOUNT),
            &[
                OsStr::new("-o"),
                OsStr::new("subvolid=5"),
                OsStr::new(source),
                top.as_os_str(),
            ],
        )?;
        let old_root = top.join(transaction.old_root_name());
        let result = match fs::symlink_metadata(&old_root) {
            Ok(metadata) if metadata.file_type().is_dir() => run_command(
                Path::new(BTRFS),
                &[
                    OsStr::new("subvolume"),
                    OsStr::new("delete"),
                    OsStr::new("--commit-after"),
                    old_root.as_os_str(),
                ],
            )
            .map(|_| ()),
            Ok(_) => Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "The protected old root is not a real directory",
            )),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(ConfirmationError::new(
                ConfirmationErrorCode::CommandFailed,
                format!("Could not inspect the protected old root: {error}"),
            )),
        };
        let unmount = run_command(Path::new(UMOUNT), &[top.as_os_str()]).map(|_| ());
        match (result, unmount) {
            (Err(error), _) => Err(error),
            (Ok(()), Err(error)) => Err(error),
            (Ok(()), Ok(())) => Ok(()),
        }
    }

    fn remove_transaction(&self) -> Result<(), ConfirmationError> {
        TransactionStore::default()
            .remove()
            .map_err(transaction_error)
    }

    fn archive_transaction(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<(), ConfirmationError> {
        TransactionStore::default()
            .archive(transaction)
            .map_err(transaction_error)
    }

    fn clear_once(&self) -> Result<(), ConfirmationError> {
        BootIntegration::default()
            .clear_pending_once()
            .map_err(|error| {
                ConfirmationError::new(ConfirmationErrorCode::CommandFailed, error.message)
            })
    }

    fn regenerate_grub(&self) -> Result<(), ConfirmationError> {
        run_command(Path::new(UPDATE_GRUB), &[]).map(|_| ())
    }

    fn record_lineage_activation(
        &self,
        transaction: &RollbackTransaction,
        outcome: ActivationOutcome,
    ) -> Result<(), ConfirmationError> {
        let deployments = DeploymentStore::default().discover();
        let store = LineageStore::default();
        store
            .ensure_initialized(&deployments.deployments)
            .and_then(|_| store.record_activation(transaction, outcome, Utc::now()))
            .map(|_| ())
            .map_err(|error| {
                ConfirmationError::new(
                    ConfirmationErrorCode::StateCommit,
                    format!("Could not update system history: {error}"),
                )
            })
    }
}

#[derive(Clone, Debug)]
pub struct ConfirmationEngine<B = SystemConfirmationBackend> {
    backend: B,
}

impl Default for ConfirmationEngine<SystemConfirmationBackend> {
    fn default() -> Self {
        Self::new(SystemConfirmationBackend)
    }
}

impl<B: ConfirmationBackend> ConfirmationEngine<B> {
    pub fn new(backend: B) -> Self {
        Self { backend }
    }

    pub fn reconcile(&self) -> Result<ConfirmationOutcome, ConfirmationError> {
        let Some(mut transaction) = self.backend.pending()? else {
            return Ok(ConfirmationOutcome::NoAction);
        };
        match transaction.phase {
            RollbackPhase::Armed if self.backend.requested_rollback()? == Some(transaction.id) => {
                transaction
                    .record_failure(
                        "The recovery boot reached userspace without entering the initramfs recovery engine",
                        Utc::now(),
                    )
                    .map_err(transaction_error)?;
                self.backend.update_transaction(&transaction)?;
                self.finish_failed(&transaction)?;
                Ok(ConfirmationOutcome::FailedRecorded)
            }
            RollbackPhase::BootedUnconfirmed => {
                self.verify_running_target(&transaction)?;
                transaction
                    .transition(RollbackPhase::Confirmed, Utc::now())
                    .map_err(transaction_error)?;
                self.backend.update_transaction(&transaction)?;
                self.finish_confirmed(&transaction)?;
                Ok(ConfirmationOutcome::Confirmed)
            }
            RollbackPhase::Confirmed => {
                self.finish_confirmed(&transaction)?;
                Ok(ConfirmationOutcome::Confirmed)
            }
            RollbackPhase::Reverted => {
                self.finish_reverted(&transaction)?;
                Ok(ConfirmationOutcome::RevertedRecorded)
            }
            RollbackPhase::Failed => {
                self.finish_failed(&transaction)?;
                Ok(ConfirmationOutcome::FailedRecorded)
            }
            _ => Ok(ConfirmationOutcome::NoAction),
        }
    }

    fn verify_running_target(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<(), ConfirmationError> {
        if self.backend.boot_id()? != transaction.applying_boot_id.clone().unwrap_or_default() {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "Rollback confirmation is running in a different boot",
            ));
        }
        if self.backend.kernel_release()? != transaction.kernel_release {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "The running kernel does not match the rollback transaction",
            ));
        }
        let target = self.backend.deployment(transaction.target_deployment_id)?;
        let expected_parent = target.snapshot_uuid.ok_or_else(|| {
            ConfirmationError::new(
                ConfirmationErrorCode::InvalidTransaction,
                "The rollback target has no snapshot UUID",
            )
        })?;
        if self.backend.current_snapshot_parent_uuid()? != expected_parent {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "The running root is not a writable snapshot of the rollback target",
            ));
        }
        Ok(())
    }

    fn finish_confirmed(&self, transaction: &RollbackTransaction) -> Result<(), ConfirmationError> {
        self.backend
            .record_lineage_activation(transaction, ActivationOutcome::Confirmed)?;
        self.backend.delete_old_root(transaction)?;
        self.backend.clear_once()?;
        self.backend.archive_transaction(transaction)?;
        self.backend.remove_transaction()?;
        self.backend.regenerate_grub()?;
        Ok(())
    }

    fn finish_reverted(&self, transaction: &RollbackTransaction) -> Result<(), ConfirmationError> {
        self.backend
            .record_lineage_activation(transaction, ActivationOutcome::Reverted)?;
        self.backend.clear_once()?;
        self.backend.archive_transaction(transaction)?;
        self.backend.remove_transaction()?;
        self.backend.regenerate_grub()?;
        Ok(())
    }

    fn finish_failed(&self, transaction: &RollbackTransaction) -> Result<(), ConfirmationError> {
        self.backend.clear_once()?;
        self.backend.archive_transaction(transaction)?;
        self.backend.remove_transaction()?;
        self.backend.regenerate_grub()?;
        Ok(())
    }
}

fn parse_requested_rollback(command_line: &str) -> Result<Option<RollbackId>, ConfirmationError> {
    let mut requested = None;
    for argument in command_line.split_whitespace() {
        let Some(value) = argument.strip_prefix("anduinos.btrfs_snapshots_manager=") else {
            continue;
        };
        let id = value.parse::<RollbackId>().map_err(|_| {
            ConfirmationError::new(
                ConfirmationErrorCode::InvalidTransaction,
                "The kernel command line contains an invalid rollback ID",
            )
        })?;
        if id.to_string() != value || requested.replace(id).is_some() {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::InvalidTransaction,
                "The kernel command line contains an ambiguous rollback request",
            ));
        }
    }
    Ok(requested)
}

fn ensure_supported_root(report: &LayoutReport) -> Result<(), ConfirmationError> {
    if report.is_supported() {
        Ok(())
    } else {
        Err(ConfirmationError::new(
            ConfirmationErrorCode::IdentityMismatch,
            "The running root no longer has the AnduinOS Btrfs layout",
        ))
    }
}

fn ensure_mount_directory(path: &Path) -> Result<(), ConfirmationError> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| {
            ConfirmationError::new(
                ConfirmationErrorCode::CommandFailed,
                format!("Could not create the recovery runtime directory: {error}"),
            )
        })?;
    }
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(()),
        Ok(_) => Err(ConfirmationError::new(
            ConfirmationErrorCode::IdentityMismatch,
            "The recovery mount point is not a real directory",
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path).map_err(|error| {
                ConfirmationError::new(
                    ConfirmationErrorCode::CommandFailed,
                    format!("Could not create the recovery mount point: {error}"),
                )
            })
        }
        Err(error) => Err(ConfirmationError::new(
            ConfirmationErrorCode::CommandFailed,
            format!("Could not inspect the recovery mount point: {error}"),
        )),
    }
}

fn read_canonical_uuid(path: &Path, name: &str) -> Result<String, ConfirmationError> {
    let value = fs::read_to_string(path).map_err(|error| {
        ConfirmationError::new(
            ConfirmationErrorCode::IdentityMismatch,
            format!("Could not read {name}: {error}"),
        )
    })?;
    let value = value.trim();
    let parsed = uuid::Uuid::parse_str(value).map_err(|_| {
        ConfirmationError::new(
            ConfirmationErrorCode::IdentityMismatch,
            format!("{name} is invalid"),
        )
    })?;
    if parsed.hyphenated().to_string() != value {
        return Err(ConfirmationError::new(
            ConfirmationErrorCode::IdentityMismatch,
            format!("{name} is not canonical"),
        ));
    }
    Ok(value.into())
}

fn parse_parent_uuid(output: &str) -> Result<String, ConfirmationError> {
    let value = output
        .lines()
        .find_map(|line| line.trim().strip_prefix("Parent UUID:"))
        .map(str::trim)
        .ok_or_else(|| {
            ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "Btrfs did not report the running root parent UUID",
            )
        })?;
    let parsed = uuid::Uuid::parse_str(value).map_err(|_| {
        ConfirmationError::new(
            ConfirmationErrorCode::IdentityMismatch,
            "The running root parent UUID is invalid",
        )
    })?;
    Ok(parsed.hyphenated().to_string())
}

fn run_command(program: &Path, arguments: &[&OsStr]) -> Result<String, ConfirmationError> {
    let output = Command::new(program)
        .args(arguments)
        .env_clear()
        .env("PATH", COMMAND_PATH)
        .env("LC_ALL", "C")
        .output()
        .map_err(|error| {
            ConfirmationError::new(
                ConfirmationErrorCode::CommandFailed,
                format!("Could not execute {}: {error}", program.display()),
            )
        })?;
    if !output.status.success() {
        return Err(ConfirmationError::new(
            ConfirmationErrorCode::CommandFailed,
            format!("{} exited with {}", program.display(), output.status),
        ));
    }
    if output.stdout.len() > 4096 {
        return Err(ConfirmationError::new(
            ConfirmationErrorCode::CommandFailed,
            format!("{} returned excessive output", program.display()),
        ));
    }
    String::from_utf8(output.stdout).map_err(|_| {
        ConfirmationError::new(
            ConfirmationErrorCode::CommandFailed,
            format!("{} returned non-UTF-8 output", program.display()),
        )
    })
}

fn transaction_error(error: crate::transaction::TransactionError) -> ConfirmationError {
    ConfirmationError::new(ConfirmationErrorCode::InvalidTransaction, error.message)
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::DEPLOYMENT_SCHEMA_VERSION;
    use crate::model::{DeploymentKind, DeploymentRecord};

    const BOOT: &str = "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb";
    const SNAPSHOT: &str = "cccccccc-1111-4222-8333-dddddddddddd";

    #[derive(Clone)]
    struct FakeBackend {
        inner: Arc<Mutex<FakeState>>,
    }

    struct FakeState {
        transaction: Option<RollbackTransaction>,
        records: HashMap<DeploymentId, DeploymentRecord>,
        boot_id: String,
        parent_uuid: String,
        requested: Option<RollbackId>,
        archived: Vec<RollbackTransaction>,
        calls: Vec<String>,
    }

    impl FakeBackend {
        fn booted() -> (Self, RollbackTransaction) {
            let target = record(DeploymentKind::Manual, DeploymentState::Ready);
            let fallback = record(DeploymentKind::PreRollback, DeploymentState::Ready);
            let mut transaction = RollbackTransaction::new(
                target.id,
                fallback.id,
                "eeeeeeee-1111-4222-8333-ffffffffffff",
                "7.0.0-test",
                "a".repeat(64),
                "b".repeat(64),
                "c".repeat(64),
            );
            transaction
                .transition(RollbackPhase::Armed, Utc::now())
                .unwrap();
            transaction
                .record_initramfs_entry(BOOT, Utc::now())
                .unwrap();
            transaction.begin_apply(BOOT, Utc::now()).unwrap();
            transaction
                .transition(RollbackPhase::BootedUnconfirmed, Utc::now())
                .unwrap();
            let mut records = HashMap::new();
            records.insert(target.id, target);
            records.insert(fallback.id, fallback);
            (
                Self {
                    inner: Arc::new(Mutex::new(FakeState {
                        transaction: Some(transaction.clone()),
                        records,
                        boot_id: BOOT.into(),
                        parent_uuid: SNAPSHOT.into(),
                        requested: None,
                        archived: Vec::new(),
                        calls: Vec::new(),
                    })),
                },
                transaction,
            )
        }

        fn call(&self, name: &str) {
            self.inner.lock().unwrap().calls.push(name.into());
        }
    }

    impl ConfirmationBackend for FakeBackend {
        fn pending(&self) -> Result<Option<RollbackTransaction>, ConfirmationError> {
            self.call("pending");
            Ok(self.inner.lock().unwrap().transaction.clone())
        }

        fn deployment(&self, id: DeploymentId) -> Result<DeploymentRecord, ConfirmationError> {
            self.call("deployment");
            Ok(self.inner.lock().unwrap().records[&id].clone())
        }

        fn boot_id(&self) -> Result<String, ConfirmationError> {
            self.call("boot-id");
            Ok(self.inner.lock().unwrap().boot_id.clone())
        }

        fn kernel_release(&self) -> Result<String, ConfirmationError> {
            self.call("kernel");
            Ok("7.0.0-test".into())
        }

        fn requested_rollback(&self) -> Result<Option<RollbackId>, ConfirmationError> {
            self.call("requested-rollback");
            Ok(self.inner.lock().unwrap().requested)
        }

        fn current_snapshot_parent_uuid(&self) -> Result<String, ConfirmationError> {
            self.call("parent-uuid");
            Ok(self.inner.lock().unwrap().parent_uuid.clone())
        }

        fn update_transaction(
            &self,
            transaction: &RollbackTransaction,
        ) -> Result<(), ConfirmationError> {
            self.call("update-transaction");
            self.inner.lock().unwrap().transaction = Some(transaction.clone());
            Ok(())
        }

        fn delete_old_root(
            &self,
            _transaction: &RollbackTransaction,
        ) -> Result<(), ConfirmationError> {
            self.call("delete-old-root");
            Ok(())
        }

        fn remove_transaction(&self) -> Result<(), ConfirmationError> {
            self.call("remove-transaction");
            self.inner.lock().unwrap().transaction = None;
            Ok(())
        }

        fn archive_transaction(
            &self,
            transaction: &RollbackTransaction,
        ) -> Result<(), ConfirmationError> {
            self.call("archive-transaction");
            self.inner
                .lock()
                .unwrap()
                .archived
                .push(transaction.clone());
            Ok(())
        }

        fn clear_once(&self) -> Result<(), ConfirmationError> {
            self.call("clear-once");
            Ok(())
        }

        fn regenerate_grub(&self) -> Result<(), ConfirmationError> {
            self.call("regenerate-grub");
            Ok(())
        }

        fn record_lineage_activation(
            &self,
            _transaction: &RollbackTransaction,
            outcome: ActivationOutcome,
        ) -> Result<(), ConfirmationError> {
            self.call(&format!("lineage-{outcome:?}"));
            Ok(())
        }
    }

    fn record(kind: DeploymentKind, state: DeploymentState) -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind,
            state,
            created_at: Utc::now(),
            title: "Confirmation test".into(),
            reason: "Confirmation test".into(),
            schedule_id: None,
            snapshot_uuid: Some(SNAPSHOT.into()),
            snapshot_parent_uuid: None,
            kernel_release: Some("7.0.0-test".into()),
            initramfs_sha256: Some("a".repeat(64)),
            boot_artifact_sha256: Some("b".repeat(64)),
            dpkg_status_sha256: Some("c".repeat(64)),
            mok_certificate_sha256: None,
            pinned: false,
            failure: None,
        }
    }

    #[test]
    fn matching_boot_is_confirmed_before_old_root_is_deleted() {
        let (backend, transaction) = FakeBackend::booted();
        assert_eq!(
            ConfirmationEngine::new(backend.clone())
                .reconcile()
                .unwrap(),
            ConfirmationOutcome::Confirmed
        );
        let inner = backend.inner.lock().unwrap();
        assert!(inner.transaction.is_none());
        assert_eq!(
            inner.records[&transaction.target_deployment_id].state,
            DeploymentState::Ready
        );
        assert_eq!(
            inner.records[&transaction.fallback_deployment_id].state,
            DeploymentState::Ready
        );
        let committed = inner
            .calls
            .iter()
            .position(|call| call == "update-transaction")
            .unwrap();
        let deleted = inner
            .calls
            .iter()
            .position(|call| call == "delete-old-root")
            .unwrap();
        assert!(committed < deleted);
        let lineage = inner
            .calls
            .iter()
            .position(|call| call == "lineage-Confirmed")
            .unwrap();
        assert!(lineage < deleted);
    }

    #[test]
    fn a_different_boot_is_never_confirmed() {
        let (backend, transaction) = FakeBackend::booted();
        backend.inner.lock().unwrap().boot_id = "11111111-2222-4333-8444-555555555555".into();
        assert_eq!(
            ConfirmationEngine::new(backend.clone())
                .reconcile()
                .unwrap_err()
                .code,
            ConfirmationErrorCode::IdentityMismatch
        );
        let inner = backend.inner.lock().unwrap();
        assert_eq!(
            inner.transaction.as_ref().unwrap().phase,
            RollbackPhase::BootedUnconfirmed
        );
        assert_eq!(
            inner.records[&transaction.target_deployment_id].state,
            DeploymentState::Ready
        );
    }

    #[test]
    fn requested_recovery_that_reaches_userspace_without_initramfs_is_failed_and_archived() {
        let (backend, original) = FakeBackend::booted();
        let mut armed = RollbackTransaction::new(
            original.target_deployment_id,
            original.fallback_deployment_id,
            original.root_filesystem_uuid,
            original.kernel_release,
            original.recovery_kernel_sha256,
            original.recovery_initramfs_sha256,
            original.recovery_confirm_sha256,
        );
        armed.transition(RollbackPhase::Armed, Utc::now()).unwrap();
        {
            let mut inner = backend.inner.lock().unwrap();
            inner.requested = Some(armed.id);
            inner.transaction = Some(armed.clone());
        }

        assert_eq!(
            ConfirmationEngine::new(backend.clone())
                .reconcile()
                .unwrap(),
            ConfirmationOutcome::FailedRecorded
        );
        let inner = backend.inner.lock().unwrap();
        assert!(inner.transaction.is_none());
        assert_eq!(inner.archived.len(), 1);
        assert_eq!(inner.archived[0].phase, RollbackPhase::Failed);
        assert!(
            inner.archived[0]
                .failure
                .as_deref()
                .unwrap()
                .contains("without entering the initramfs")
        );
        assert_eq!(
            inner.records[&armed.target_deployment_id].state,
            DeploymentState::Ready
        );
        assert_eq!(
            inner.records[&armed.fallback_deployment_id].state,
            DeploymentState::Ready
        );
        let archived = inner
            .calls
            .iter()
            .position(|call| call == "archive-transaction")
            .unwrap();
        let removed = inner
            .calls
            .iter()
            .position(|call| call == "remove-transaction")
            .unwrap();
        assert!(archived < removed);
    }

    #[test]
    fn confirmed_cleanup_is_resumable() {
        let (backend, transaction) = FakeBackend::booted();
        backend
            .inner
            .lock()
            .unwrap()
            .transaction
            .as_mut()
            .unwrap()
            .transition(RollbackPhase::Confirmed, Utc::now())
            .unwrap();
        assert_eq!(
            ConfirmationEngine::new(backend.clone())
                .reconcile()
                .unwrap(),
            ConfirmationOutcome::Confirmed
        );
        assert!(backend.inner.lock().unwrap().transaction.is_none());
        assert_eq!(
            backend.inner.lock().unwrap().records[&transaction.target_deployment_id].state,
            DeploymentState::Ready
        );
    }

    #[test]
    fn reverted_transaction_records_the_automatic_fallback() {
        let (backend, transaction) = FakeBackend::booted();
        {
            let mut inner = backend.inner.lock().unwrap();
            let pending = inner.transaction.as_mut().unwrap();
            pending
                .transition(RollbackPhase::Reverting, Utc::now())
                .unwrap();
            pending
                .transition(RollbackPhase::Reverted, Utc::now())
                .unwrap();
        }
        assert_eq!(
            ConfirmationEngine::new(backend.clone())
                .reconcile()
                .unwrap(),
            ConfirmationOutcome::RevertedRecorded
        );
        let inner = backend.inner.lock().unwrap();
        assert_eq!(
            inner.records[&transaction.target_deployment_id].state,
            DeploymentState::Ready
        );
        assert_eq!(
            inner.records[&transaction.fallback_deployment_id].state,
            DeploymentState::Ready
        );
    }
}
