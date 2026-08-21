use std::ffi::OsStr;
use std::fmt;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::Command;

use chrono::Utc;

use crate::boot::BootIntegration;
use crate::cleanup::{RootCleanupRecord, RootCleanupStore, bounded_diagnostic};
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
const MAX_COMMAND_OUTPUT: usize = 1024 * 1024;
const MAX_COMMAND_DIAGNOSTIC: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConfirmationOutcome {
    NoAction,
    Confirmed,
    ConfirmedCleanupPending,
    CleanupCompleted,
    CleanupPending,
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
    fn pending_root_cleanups(&self) -> Result<Vec<RootCleanupRecord>, ConfirmationError>;
    fn schedule_old_root_cleanup(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<RootCleanupRecord, ConfirmationError>;
    fn cleanup_old_root(
        &self,
        record: &RootCleanupRecord,
    ) -> Result<OldRootCleanupOutcome, ConfirmationError>;
    fn defer_old_root_cleanup(
        &self,
        record: &mut RootCleanupRecord,
        blocked_subvolumes: Vec<String>,
        diagnostic: &str,
    ) -> Result<(), ConfirmationError>;
    fn complete_old_root_cleanup(
        &self,
        record: &RootCleanupRecord,
    ) -> Result<(), ConfirmationError>;
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
            "inspecting the running root subvolume",
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

    fn pending_root_cleanups(&self) -> Result<Vec<RootCleanupRecord>, ConfirmationError> {
        RootCleanupStore::default().list().map_err(cleanup_error)
    }

    fn schedule_old_root_cleanup(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<RootCleanupRecord, ConfirmationError> {
        RootCleanupStore::default()
            .schedule(transaction)
            .map_err(cleanup_error)
    }

    fn cleanup_old_root(
        &self,
        record: &RootCleanupRecord,
    ) -> Result<OldRootCleanupOutcome, ConfirmationError> {
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
            "mounting the Btrfs top level for old-root cleanup",
        )?;
        let result = cleanup_old_root_at(top, &record.old_root_name);
        let unmount = run_command(
            Path::new(UMOUNT),
            &[top.as_os_str()],
            "unmounting the Btrfs top level after old-root cleanup",
        )
        .map(|_| ());
        match (result, unmount) {
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(error),
            (Ok(outcome), Ok(())) => Ok(outcome),
        }
    }

    fn defer_old_root_cleanup(
        &self,
        record: &mut RootCleanupRecord,
        blocked_subvolumes: Vec<String>,
        diagnostic: &str,
    ) -> Result<(), ConfirmationError> {
        record
            .record_deferred(blocked_subvolumes, diagnostic)
            .map_err(cleanup_error)?;
        RootCleanupStore::default()
            .update(record)
            .map_err(cleanup_error)
    }

    fn complete_old_root_cleanup(
        &self,
        record: &RootCleanupRecord,
    ) -> Result<(), ConfirmationError> {
        RootCleanupStore::default()
            .remove(record.transaction_id)
            .map_err(cleanup_error)
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
        run_command(
            Path::new(UPDATE_GRUB),
            &[],
            "regenerating GRUB after recovery confirmation",
        )
        .map(|_| ())
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
            return match self.retry_pending_root_cleanups()? {
                CleanupPass::NoRecords => Ok(ConfirmationOutcome::NoAction),
                CleanupPass::Completed => Ok(ConfirmationOutcome::CleanupCompleted),
                CleanupPass::Pending => Ok(ConfirmationOutcome::CleanupPending),
            };
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
                if self.finish_confirmed(&transaction)? {
                    Ok(ConfirmationOutcome::ConfirmedCleanupPending)
                } else {
                    Ok(ConfirmationOutcome::Confirmed)
                }
            }
            RollbackPhase::Confirmed => {
                if self.finish_confirmed(&transaction)? {
                    Ok(ConfirmationOutcome::ConfirmedCleanupPending)
                } else {
                    Ok(ConfirmationOutcome::Confirmed)
                }
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

    fn finish_confirmed(
        &self,
        transaction: &RollbackTransaction,
    ) -> Result<bool, ConfirmationError> {
        self.backend
            .record_lineage_activation(transaction, ActivationOutcome::Confirmed)?;
        self.backend.schedule_old_root_cleanup(transaction)?;
        self.backend.clear_once()?;
        self.backend.archive_transaction(transaction)?;
        self.backend.remove_transaction()?;
        self.backend.regenerate_grub()?;
        Ok(matches!(
            self.retry_pending_root_cleanups()?,
            CleanupPass::Pending
        ))
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

    fn retry_pending_root_cleanups(&self) -> Result<CleanupPass, ConfirmationError> {
        let records = self.backend.pending_root_cleanups()?;
        if records.is_empty() {
            return Ok(CleanupPass::NoRecords);
        }
        let mut pending = false;
        for mut record in records {
            match self.backend.cleanup_old_root(&record) {
                Ok(OldRootCleanupOutcome::Removed) => {
                    self.backend.complete_old_root_cleanup(&record)?;
                }
                Ok(OldRootCleanupOutcome::Deferred {
                    blocked_subvolumes,
                    diagnostic,
                }) => {
                    self.backend.defer_old_root_cleanup(
                        &mut record,
                        blocked_subvolumes,
                        &diagnostic,
                    )?;
                    pending = true;
                }
                Err(error) => {
                    self.backend
                        .defer_old_root_cleanup(&mut record, Vec::new(), &error.message)?;
                    pending = true;
                }
            }
        }
        Ok(if pending {
            CleanupPass::Pending
        } else {
            CleanupPass::Completed
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CleanupPass {
    NoRecords,
    Completed,
    Pending,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum OldRootCleanupOutcome {
    Removed,
    Deferred {
        blocked_subvolumes: Vec<String>,
        diagnostic: String,
    },
}

pub fn cleanup_old_root_at(
    top_level: &Path,
    old_root_name: &str,
) -> Result<OldRootCleanupOutcome, ConfirmationError> {
    validate_old_root_name(old_root_name)?;
    let old_root = top_level.join(old_root_name);
    match fs::symlink_metadata(&old_root) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(_) => {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "The protected old root is not a real directory",
            ));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(OldRootCleanupOutcome::Removed);
        }
        Err(error) => {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::CommandFailed,
                format!("Could not inspect the protected old root: {error}"),
            ));
        }
    }

    let output = run_command(
        Path::new(BTRFS),
        &[
            OsStr::new("subvolume"),
            OsStr::new("list"),
            OsStr::new("-o"),
            old_root.as_os_str(),
        ],
        "listing descendant subvolumes below the old root",
    )?;
    let mut descendants = parse_descendant_subvolumes(&output, old_root_name)?;
    descendants.sort_by_key(|path| std::cmp::Reverse(path.components().count()));

    let mut blocked = Vec::new();
    for relative in descendants {
        let target = old_root.join(&relative);
        let metadata = match fs::symlink_metadata(&target) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => {
                return Err(ConfirmationError::new(
                    ConfirmationErrorCode::CommandFailed,
                    format!("Could not inspect an old-root descendant subvolume: {error}"),
                ));
            }
        };
        if !metadata.file_type().is_dir() {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "An old-root descendant subvolume is not a real directory",
            ));
        }
        let mut entries = fs::read_dir(&target).map_err(|error| {
            ConfirmationError::new(
                ConfirmationErrorCode::CommandFailed,
                format!("Could not inspect an old-root descendant subvolume: {error}"),
            )
        })?;
        if entries.next().is_some() {
            blocked.push(path_to_record(&relative)?);
            continue;
        }
        run_command(
            Path::new(BTRFS),
            &[
                OsStr::new("subvolume"),
                OsStr::new("delete"),
                OsStr::new("--commit-after"),
                target.as_os_str(),
            ],
            "deleting an empty descendant subvolume from the old root",
        )?;
    }

    if !blocked.is_empty() {
        return Ok(OldRootCleanupOutcome::Deferred {
            blocked_subvolumes: blocked,
            diagnostic: "The old root contains non-empty descendant subvolumes; automatic deletion was deferred"
                .into(),
        });
    }

    run_command(
        Path::new(BTRFS),
        &[
            OsStr::new("subvolume"),
            OsStr::new("delete"),
            OsStr::new("--commit-after"),
            old_root.as_os_str(),
        ],
        "deleting the old root subvolume",
    )?;
    Ok(OldRootCleanupOutcome::Removed)
}

fn validate_old_root_name(value: &str) -> Result<(), ConfirmationError> {
    let Some(id) = value.strip_prefix("@root.snapshots-manager-old-") else {
        return Err(ConfirmationError::new(
            ConfirmationErrorCode::InvalidTransaction,
            "The root cleanup target has an invalid name",
        ));
    };
    let parsed = id.parse::<RollbackId>().map_err(|_| {
        ConfirmationError::new(
            ConfirmationErrorCode::InvalidTransaction,
            "The root cleanup target has an invalid transaction ID",
        )
    })?;
    if parsed.to_string() != id {
        return Err(ConfirmationError::new(
            ConfirmationErrorCode::InvalidTransaction,
            "The root cleanup target has a non-canonical transaction ID",
        ));
    }
    Ok(())
}

fn parse_descendant_subvolumes(
    output: &str,
    old_root_name: &str,
) -> Result<Vec<PathBuf>, ConfirmationError> {
    let prefix = format!("{old_root_name}/");
    let mut paths = Vec::new();
    for line in output.lines().filter(|line| !line.trim().is_empty()) {
        let reported = line
            .split_once(" path ")
            .map(|(_, path)| path)
            .ok_or_else(|| {
                ConfirmationError::new(
                    ConfirmationErrorCode::CommandFailed,
                    "Btrfs returned an unrecognized descendant-subvolume record",
                )
            })?;
        let reported = reported.strip_prefix("<FS_TREE>/").unwrap_or(reported);
        let relative = reported.strip_prefix(&prefix).ok_or_else(|| {
            ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "Btrfs reported a descendant outside the protected old root",
            )
        })?;
        let path = PathBuf::from(relative);
        if relative.is_empty()
            || relative.chars().any(char::is_control)
            || !path
                .components()
                .all(|component| matches!(component, Component::Normal(_)))
        {
            return Err(ConfirmationError::new(
                ConfirmationErrorCode::IdentityMismatch,
                "Btrfs reported an unsafe old-root descendant path",
            ));
        }
        paths.push(path);
    }
    paths.sort();
    paths.dedup();
    Ok(paths)
}

fn path_to_record(path: &Path) -> Result<String, ConfirmationError> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        ConfirmationError::new(
            ConfirmationErrorCode::IdentityMismatch,
            "An old-root descendant path is not UTF-8",
        )
    })
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

fn run_command(
    program: &Path,
    arguments: &[&OsStr],
    stage: &str,
) -> Result<String, ConfirmationError> {
    let output = Command::new(program)
        .args(arguments)
        .env_clear()
        .env("PATH", COMMAND_PATH)
        .env("LC_ALL", "C")
        .output()
        .map_err(|error| {
            ConfirmationError::new(
                ConfirmationErrorCode::CommandFailed,
                format!(
                    "Could not execute {} while {stage}: {error}",
                    program.display()
                ),
            )
        })?;
    if !output.status.success() {
        let diagnostic = command_diagnostic(&output.stderr);
        return Err(ConfirmationError::new(
            ConfirmationErrorCode::CommandFailed,
            format!(
                "{} exited with {} while {stage}{}",
                program.display(),
                output.status,
                if diagnostic.is_empty() {
                    String::new()
                } else {
                    format!(": {diagnostic}")
                }
            ),
        ));
    }
    if output.stdout.len() > MAX_COMMAND_OUTPUT {
        return Err(ConfirmationError::new(
            ConfirmationErrorCode::CommandFailed,
            format!(
                "{} returned excessive output while {stage}",
                program.display()
            ),
        ));
    }
    String::from_utf8(output.stdout).map_err(|_| {
        ConfirmationError::new(
            ConfirmationErrorCode::CommandFailed,
            format!(
                "{} returned non-UTF-8 output while {stage}",
                program.display()
            ),
        )
    })
}

fn command_diagnostic(stderr: &[u8]) -> String {
    if stderr.len() > MAX_COMMAND_DIAGNOSTIC {
        return format!("diagnostic output exceeded {MAX_COMMAND_DIAGNOSTIC} bytes");
    }
    match std::str::from_utf8(stderr) {
        Ok(value) => {
            let value = value.trim();
            if value.is_empty() {
                String::new()
            } else {
                bounded_diagnostic(value)
            }
        }
        Err(_) => "diagnostic output was not UTF-8".into(),
    }
}

fn transaction_error(error: crate::transaction::TransactionError) -> ConfirmationError {
    ConfirmationError::new(ConfirmationErrorCode::InvalidTransaction, error.message)
}

fn cleanup_error(error: crate::cleanup::RootCleanupError) -> ConfirmationError {
    ConfirmationError::new(ConfirmationErrorCode::StateCommit, error.message)
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
        cleanups: Vec<RootCleanupRecord>,
        cleanup_outcome: OldRootCleanupOutcome,
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
                        cleanups: Vec::new(),
                        cleanup_outcome: OldRootCleanupOutcome::Removed,
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

        fn pending_root_cleanups(&self) -> Result<Vec<RootCleanupRecord>, ConfirmationError> {
            self.call("pending-root-cleanups");
            Ok(self.inner.lock().unwrap().cleanups.clone())
        }

        fn schedule_old_root_cleanup(
            &self,
            transaction: &RollbackTransaction,
        ) -> Result<RootCleanupRecord, ConfirmationError> {
            self.call("schedule-old-root-cleanup");
            let mut inner = self.inner.lock().unwrap();
            if let Some(record) = inner
                .cleanups
                .iter()
                .find(|record| record.transaction_id == transaction.id)
            {
                return Ok(record.clone());
            }
            let record = RootCleanupRecord::new(transaction);
            inner.cleanups.push(record.clone());
            Ok(record)
        }

        fn cleanup_old_root(
            &self,
            _record: &RootCleanupRecord,
        ) -> Result<OldRootCleanupOutcome, ConfirmationError> {
            self.call("cleanup-old-root");
            Ok(self.inner.lock().unwrap().cleanup_outcome.clone())
        }

        fn defer_old_root_cleanup(
            &self,
            record: &mut RootCleanupRecord,
            blocked_subvolumes: Vec<String>,
            diagnostic: &str,
        ) -> Result<(), ConfirmationError> {
            self.call("defer-old-root-cleanup");
            record
                .record_deferred(blocked_subvolumes, diagnostic)
                .map_err(cleanup_error)?;
            let mut inner = self.inner.lock().unwrap();
            let stored = inner
                .cleanups
                .iter_mut()
                .find(|stored| stored.transaction_id == record.transaction_id)
                .unwrap();
            *stored = record.clone();
            Ok(())
        }

        fn complete_old_root_cleanup(
            &self,
            record: &RootCleanupRecord,
        ) -> Result<(), ConfirmationError> {
            self.call("complete-old-root-cleanup");
            self.inner
                .lock()
                .unwrap()
                .cleanups
                .retain(|stored| stored.transaction_id != record.transaction_id);
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
    fn matching_boot_is_terminal_before_old_root_cleanup() {
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
            .position(|call| call == "cleanup-old-root")
            .unwrap();
        assert!(committed < deleted);
        let lineage = inner
            .calls
            .iter()
            .position(|call| call == "lineage-Confirmed")
            .unwrap();
        assert!(lineage < deleted);
        let removed = inner
            .calls
            .iter()
            .position(|call| call == "remove-transaction")
            .unwrap();
        let grub = inner
            .calls
            .iter()
            .position(|call| call == "regenerate-grub")
            .unwrap();
        assert!(removed < grub && grub < deleted);
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
    fn confirmed_rollback_releases_pending_slot_when_old_root_cleanup_is_deferred() {
        let (backend, _transaction) = FakeBackend::booted();
        backend.inner.lock().unwrap().cleanup_outcome = OldRootCleanupOutcome::Deferred {
            blocked_subvolumes: vec!["var/lib/machines".into()],
            diagnostic: "old-root descendant is not empty".into(),
        };
        assert_eq!(
            ConfirmationEngine::new(backend.clone())
                .reconcile()
                .unwrap(),
            ConfirmationOutcome::ConfirmedCleanupPending
        );
        let inner = backend.inner.lock().unwrap();
        assert!(inner.transaction.is_none());
        assert_eq!(inner.cleanups.len(), 1);
        assert_eq!(
            inner.cleanups[0].blocked_subvolumes,
            vec!["var/lib/machines"]
        );
        assert_eq!(inner.cleanups[0].attempts, 1);
    }

    #[test]
    fn deferred_cleanup_retries_without_a_rollback_transaction() {
        let (backend, transaction) = FakeBackend::booted();
        {
            let mut inner = backend.inner.lock().unwrap();
            inner.transaction = None;
            inner.cleanups.push(RootCleanupRecord::new(&transaction));
            inner.cleanup_outcome = OldRootCleanupOutcome::Removed;
        }
        assert_eq!(
            ConfirmationEngine::new(backend.clone())
                .reconcile()
                .unwrap(),
            ConfirmationOutcome::CleanupCompleted
        );
        assert!(backend.inner.lock().unwrap().cleanups.is_empty());
    }

    #[test]
    fn command_failure_preserves_bounded_stderr_and_stage() {
        let error = run_command(
            Path::new("/bin/sh"),
            &[
                OsStr::new("-c"),
                OsStr::new("printf 'Directory not empty\\n' >&2; exit 1"),
            ],
            "deleting the old root subvolume",
        )
        .unwrap_err();
        assert!(error.message.contains("deleting the old root subvolume"));
        assert!(error.message.contains("Directory not empty"));

        let excessive = run_command(
            Path::new("/bin/sh"),
            &[
                OsStr::new("-c"),
                OsStr::new("dd if=/dev/zero bs=5000 count=1 1>&2 2>/dev/null; exit 1"),
            ],
            "testing bounded diagnostics",
        )
        .unwrap_err();
        assert!(excessive.message.contains("exceeded 4096 bytes"));
        assert!(excessive.message.len() < 512);
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
