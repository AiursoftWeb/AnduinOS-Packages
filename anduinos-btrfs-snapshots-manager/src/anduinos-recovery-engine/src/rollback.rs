use std::ffi::OsStr;
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use std::process::Command;

use chrono::Utc;

use crate::RECOVERY_STORE_ROOT;
use crate::boot::{BootIntegration, RecoveryBootArtifacts};
use crate::coordination::TransactionStartLock;
use crate::layout::{self, LayoutReport};
#[cfg(test)]
use crate::model::DeploymentState;
use crate::model::{DeploymentId, DeploymentKind, DeploymentRecord};
use crate::operations::OperationEngine;
use crate::package_transaction::PackageTransactionStore;
use crate::personal::{
    PersonalSnapshotEngine, PersonalSnapshotId, PersonalSnapshotKind, PersonalSnapshotRecord,
};
use crate::secure_boot::SecureBootValidator;
use crate::transaction::{RollbackPhase, RollbackTransaction, TransactionStore};

const BLKID: &str = "/usr/sbin/blkid";
const UPDATE_GRUB: &str = "/usr/sbin/update-grub";
const GRUB_SCRIPT_CHECK: &str = "/usr/bin/grub-script-check";
const COMMAND_PATH: &str =
    "/usr/libexec/anduinos-btrfs-snapshots-manager/no-os-prober:/usr/sbin:/usr/bin:/sbin:/bin";
const GRUB_CONFIG: &str = "/boot/grub/grub.cfg";
const MAX_GRUB_CONFIG_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RollbackProgressPhase {
    Validate,
    ProtectCurrent,
    RecordTransaction,
    ConfigureBoot,
    Commit,
    Cleanup,
}

impl RollbackProgressPhase {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Validate => "validate",
            Self::ProtectCurrent => "protect-current",
            Self::RecordTransaction => "record-transaction",
            Self::ConfigureBoot => "configure-boot",
            Self::Commit => "commit",
            Self::Cleanup => "cleanup",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RollbackErrorCode {
    AlreadyPending,
    InvalidTarget,
    UnsupportedLayout,
    BootIntegration,
    CommandFailed,
    StateCommit,
}

impl RollbackErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AlreadyPending => "already-pending",
            Self::InvalidTarget => "invalid-target",
            Self::UnsupportedLayout => "unsupported-layout",
            Self::BootIntegration => "boot-integration",
            Self::CommandFailed => "command-failed",
            Self::StateCommit => "state-commit",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RollbackError {
    pub code: RollbackErrorCode,
    pub message: String,
}

impl RollbackError {
    fn new(code: RollbackErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for RollbackError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for RollbackError {}

pub trait RollbackBackend {
    fn lock_preparation(&self) -> Result<Option<TransactionStartLock>, RollbackError> {
        Ok(None)
    }
    fn layout(&self) -> LayoutReport;
    fn pending(&self) -> Result<Option<RollbackTransaction>, RollbackError>;
    fn package_transaction_pending(&self) -> Result<bool, RollbackError>;
    fn verify_target(&self, id: DeploymentId) -> Result<DeploymentRecord, RollbackError>;
    fn verify_factory_home(
        &self,
        _report: &LayoutReport,
    ) -> Result<PersonalSnapshotRecord, RollbackError> {
        Err(RollbackError::new(
            RollbackErrorCode::InvalidTarget,
            "The factory Home baseline is unavailable",
        ))
    }
    fn verify_home(
        &self,
        _report: &LayoutReport,
        _id: PersonalSnapshotId,
    ) -> Result<PersonalSnapshotRecord, RollbackError> {
        Err(RollbackError::new(
            RollbackErrorCode::InvalidTarget,
            "Home rollback is unavailable",
        ))
    }
    fn check_home_capacity(&self, _report: &LayoutReport) -> Result<(), RollbackError> {
        Err(RollbackError::new(
            RollbackErrorCode::StateCommit,
            "Home safety snapshot capacity is unavailable",
        ))
    }
    fn create_home_fallback(
        &self,
        _report: &LayoutReport,
    ) -> Result<PersonalSnapshotRecord, RollbackError> {
        Err(RollbackError::new(
            RollbackErrorCode::StateCommit,
            "Cannot protect the current Home",
        ))
    }
    fn check_fallback_capacity(&self, report: &LayoutReport) -> Result<(), RollbackError>;
    fn create_fallback(&self) -> Result<DeploymentRecord, RollbackError>;
    fn root_filesystem_uuid(&self, report: &LayoutReport) -> Result<String, RollbackError>;
    fn verify_one_shot_support(&self) -> Result<(), RollbackError>;
    fn verify_recovery_boot_source(&self) -> Result<(), RollbackError>;
    fn provision_recovery_boot_artifacts(&self) -> Result<RecoveryBootArtifacts, RollbackError>;
    fn create_transaction(&self, transaction: &RollbackTransaction) -> Result<(), RollbackError>;
    fn update_transaction(&self, transaction: &RollbackTransaction) -> Result<(), RollbackError>;
    fn remove_transaction(&self) -> Result<(), RollbackError>;
    fn regenerate_grub(&self) -> Result<(), RollbackError>;
    fn verify_grub_entry(&self, transaction: &RollbackTransaction) -> Result<(), RollbackError>;
    fn arm_once(&self) -> Result<String, RollbackError>;
    fn clear_once(&self) -> Result<(), RollbackError>;
}

#[derive(Clone, Debug, Default)]
pub struct SystemRollbackBackend;

impl RollbackBackend for SystemRollbackBackend {
    fn lock_preparation(&self) -> Result<Option<TransactionStartLock>, RollbackError> {
        TransactionStartLock::acquire_preparation(RECOVERY_STORE_ROOT)
            .map(Some)
            .map_err(|error| {
                RollbackError::new(
                    RollbackErrorCode::StateCommit,
                    format!("Could not coordinate recovery preparation: {error}"),
                )
            })
    }
    fn layout(&self) -> LayoutReport {
        layout::inspect_current()
    }

    fn pending(&self) -> Result<Option<RollbackTransaction>, RollbackError> {
        TransactionStore::default()
            .load_pending()
            .map_err(transaction_error)
    }

    fn package_transaction_pending(&self) -> Result<bool, RollbackError> {
        PackageTransactionStore::default()
            .load_pending()
            .map(|transaction| transaction.is_some())
            .map_err(|error| RollbackError::new(RollbackErrorCode::StateCommit, error.message))
    }

    fn verify_target(&self, id: DeploymentId) -> Result<DeploymentRecord, RollbackError> {
        let record = OperationEngine::default()
            .verify(&self.layout(), id, |_phase, _fraction, _message| {})
            .map_err(|error| RollbackError::new(RollbackErrorCode::InvalidTarget, error.message))?;
        let snapshot_root = Path::new(RECOVERY_STORE_ROOT)
            .join("deployments")
            .join(id.to_string())
            .join("root");
        SecureBootValidator::default()
            .verify_target(&snapshot_root, &record)
            .map_err(|error| {
                RollbackError::new(
                    RollbackErrorCode::InvalidTarget,
                    format!("Secure Boot validation failed: {error}"),
                )
            })?;
        Ok(record)
    }

    fn verify_factory_home(
        &self,
        report: &LayoutReport,
    ) -> Result<PersonalSnapshotRecord, RollbackError> {
        let engine = PersonalSnapshotEngine::default();
        let discovery = engine.discover();
        if !discovery.issues.is_empty() {
            return Err(RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "Factory Home recovery metadata has unresolved issues",
            ));
        }
        let factories = discovery
            .snapshots
            .iter()
            .filter(|record| record.kind == PersonalSnapshotKind::Factory)
            .collect::<Vec<_>>();
        let [factory] = factories.as_slice() else {
            return Err(RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "Exactly one factory Home baseline is required",
            ));
        };
        engine.verify(report, factory.id).map_err(|error| {
            RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                format!("Factory Home baseline verification failed: {error}"),
            )
        })
    }

    fn verify_home(
        &self,
        report: &LayoutReport,
        id: PersonalSnapshotId,
    ) -> Result<PersonalSnapshotRecord, RollbackError> {
        let engine = PersonalSnapshotEngine::default();
        let record = engine.verify(report, id).map_err(personal_error)?;
        crate::home_accounts::verify_home_accounts(
            Path::new("/etc/passwd"),
            &engine.snapshot_path(id),
        )
        .map_err(|error| RollbackError::new(RollbackErrorCode::InvalidTarget, error))?;
        Ok(record)
    }

    fn check_home_capacity(&self, report: &LayoutReport) -> Result<(), RollbackError> {
        PersonalSnapshotEngine::default()
            .check_restore_capacity(report)
            .map_err(personal_error)
    }

    fn create_home_fallback(
        &self,
        report: &LayoutReport,
    ) -> Result<PersonalSnapshotRecord, RollbackError> {
        PersonalSnapshotEngine::default()
            .create_manual(
                report,
                "Before rollback",
                "Safety snapshot before Home rollback",
                false,
            )
            .map_err(personal_error)
    }

    fn check_fallback_capacity(&self, report: &LayoutReport) -> Result<(), RollbackError> {
        OperationEngine::default()
            .check_pre_rollback_capacity(report)
            .map_err(|error| RollbackError::new(RollbackErrorCode::StateCommit, error.message))
    }

    fn create_fallback(&self) -> Result<DeploymentRecord, RollbackError> {
        OperationEngine::default()
            .create_pre_rollback(&self.layout(), |_phase, _fraction, _message| {})
            .map_err(|error| RollbackError::new(RollbackErrorCode::StateCommit, error.message))
    }

    fn root_filesystem_uuid(&self, report: &LayoutReport) -> Result<String, RollbackError> {
        let source = report.root_source.as_deref().ok_or_else(|| {
            RollbackError::new(
                RollbackErrorCode::UnsupportedLayout,
                "The root filesystem source is unavailable",
            )
        })?;
        let output = run_command(
            Path::new(BLKID),
            &[
                OsStr::new("-s"),
                OsStr::new("UUID"),
                OsStr::new("-o"),
                OsStr::new("value"),
                OsStr::new(source),
            ],
        )?;
        canonical_uuid(output.trim(), "root filesystem")
    }

    fn verify_one_shot_support(&self) -> Result<(), RollbackError> {
        BootIntegration::default()
            .ensure_external_environment_block()
            .map(|_| ())
            .map_err(|error| RollbackError::new(RollbackErrorCode::BootIntegration, error.message))
    }

    fn verify_recovery_boot_source(&self) -> Result<(), RollbackError> {
        BootIntegration::default()
            .verify_recovery_boot_source()
            .map_err(|error| RollbackError::new(RollbackErrorCode::BootIntegration, error.message))
    }

    fn provision_recovery_boot_artifacts(&self) -> Result<RecoveryBootArtifacts, RollbackError> {
        BootIntegration::default()
            .provision_recovery_boot_artifacts()
            .map_err(|error| RollbackError::new(RollbackErrorCode::BootIntegration, error.message))
    }

    fn create_transaction(&self, transaction: &RollbackTransaction) -> Result<(), RollbackError> {
        let _start_lock = TransactionStartLock::acquire(RECOVERY_STORE_ROOT).map_err(|error| {
            RollbackError::new(
                RollbackErrorCode::StateCommit,
                format!("Could not coordinate rollback transaction: {error}"),
            )
        })?;
        if PackageTransactionStore::default()
            .load_pending()
            .map_err(|error| RollbackError::new(RollbackErrorCode::StateCommit, error.message))?
            .is_some()
        {
            return Err(RollbackError::new(
                RollbackErrorCode::AlreadyPending,
                "A package transaction claimed the recovery boundary",
            ));
        }
        // Deletion takes the same start lock. Bind only still-present targets
        // at the final commit boundary, not just at the earlier UI check.
        self.verify_target(transaction.target_deployment_id)?;
        self.verify_target(transaction.fallback_deployment_id)?;
        if let Some(id) = transaction.factory_home_snapshot_id {
            PersonalSnapshotEngine::default()
                .verify(&self.layout(), id)
                .map_err(personal_error)?;
        }
        if let Some(id) = transaction.fallback_home_snapshot_id {
            PersonalSnapshotEngine::default()
                .verify(&self.layout(), id)
                .map_err(personal_error)?;
        }
        TransactionStore::default()
            .create(transaction)
            .map_err(transaction_error)
    }

    fn update_transaction(&self, transaction: &RollbackTransaction) -> Result<(), RollbackError> {
        TransactionStore::default()
            .update(transaction)
            .map_err(transaction_error)
    }

    fn remove_transaction(&self) -> Result<(), RollbackError> {
        TransactionStore::default()
            .remove()
            .map_err(transaction_error)
    }

    fn regenerate_grub(&self) -> Result<(), RollbackError> {
        run_command(Path::new(UPDATE_GRUB), &[]).map(|_| ())
    }

    fn verify_grub_entry(&self, transaction: &RollbackTransaction) -> Result<(), RollbackError> {
        verify_grub_config(Path::new(GRUB_CONFIG), transaction)?;
        run_command(
            Path::new(GRUB_SCRIPT_CHECK),
            &[Path::new(GRUB_CONFIG).as_os_str()],
        )
        .map(|_| ())
    }

    fn arm_once(&self) -> Result<String, RollbackError> {
        BootIntegration::default()
            .arm_pending_once()
            .map_err(|error| RollbackError::new(RollbackErrorCode::BootIntegration, error.message))
    }

    fn clear_once(&self) -> Result<(), RollbackError> {
        BootIntegration::default()
            .clear_pending_once()
            .map_err(|error| RollbackError::new(RollbackErrorCode::BootIntegration, error.message))
    }
}

#[derive(Clone, Debug)]
pub struct RollbackCoordinator<B = SystemRollbackBackend> {
    backend: B,
}

impl Default for RollbackCoordinator<SystemRollbackBackend> {
    fn default() -> Self {
        Self::new(SystemRollbackBackend)
    }
}

impl<B: RollbackBackend> RollbackCoordinator<B> {
    pub fn new(backend: B) -> Self {
        Self { backend }
    }

    /// Perform every non-destructive rollback prerequisite check available
    /// before asking the user for final confirmation. `schedule` deliberately
    /// repeats the same boundary because system state can change meanwhile.
    pub fn check_ready(&self, target_id: DeploymentId) -> Result<DeploymentRecord, RollbackError> {
        let (target, _, _, _) = self.validate_ready(target_id, false, false)?;
        Ok(target)
    }

    pub fn check_factory_reset_ready(
        &self,
        target_id: DeploymentId,
        reset_home: bool,
    ) -> Result<DeploymentRecord, RollbackError> {
        let (target, _, _, _) = self.validate_ready(target_id, true, reset_home)?;
        Ok(target)
    }

    fn validate_ready(
        &self,
        target_id: DeploymentId,
        factory_reset: bool,
        reset_home: bool,
    ) -> Result<
        (
            DeploymentRecord,
            LayoutReport,
            String,
            Option<PersonalSnapshotRecord>,
        ),
        RollbackError,
    > {
        let report = self.validate_environment()?;
        let target = self.backend.verify_target(target_id)?;
        if !target.can_restore() {
            return Err(RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "Only a complete, healthy system snapshot can be scheduled",
            ));
        }
        if factory_reset && target.kind != DeploymentKind::Factory {
            return Err(RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "Factory reset requires the installer-owned factory recovery point",
            ));
        }
        let factory_home = if reset_home {
            let home = self.backend.verify_factory_home(&report)?;
            self.backend.check_home_capacity(&report)?;
            Some(home)
        } else {
            None
        };
        let root_uuid = self.backend.root_filesystem_uuid(&report)?;
        self.backend.check_fallback_capacity(&report)?;
        self.backend.verify_recovery_boot_source()?;
        self.backend.verify_one_shot_support()?;
        Ok((target, report, root_uuid, factory_home))
    }

    fn validate_environment(&self) -> Result<LayoutReport, RollbackError> {
        if self.backend.pending()?.is_some() {
            return Err(RollbackError::new(
                RollbackErrorCode::AlreadyPending,
                "Another system restore is already pending",
            ));
        }
        if self.backend.package_transaction_pending()? {
            return Err(RollbackError::new(
                RollbackErrorCode::AlreadyPending,
                "A package transaction is already creating system snapshots",
            ));
        }
        let report = self.backend.layout();
        if !report.is_supported() {
            return Err(RollbackError::new(
                RollbackErrorCode::UnsupportedLayout,
                "The complete AnduinOS Btrfs layout is required",
            ));
        }
        Ok(report)
    }

    pub fn check_home_ready(
        &self,
        id: PersonalSnapshotId,
    ) -> Result<PersonalSnapshotRecord, RollbackError> {
        let report = self.validate_environment()?;
        let home = self.backend.verify_home(&report, id)?;
        self.backend.check_home_capacity(&report)?;
        self.backend.check_fallback_capacity(&report)?;
        self.backend.verify_recovery_boot_source()?;
        self.backend.verify_one_shot_support()?;
        self.backend.root_filesystem_uuid(&report)?;
        Ok(home)
    }

    pub fn schedule_home<F>(
        &self,
        id: PersonalSnapshotId,
        mut progress: F,
    ) -> Result<RollbackTransaction, RollbackError>
    where
        F: FnMut(RollbackProgressPhase, f64, &str),
    {
        let _preparation_lock = self.backend.lock_preparation()?;
        progress(
            RollbackProgressPhase::Validate,
            0.02,
            "Checking the Home recovery target",
        );
        let home = self.check_home_ready(id)?;
        let report = self.backend.layout();
        let root_uuid = self.backend.root_filesystem_uuid(&report)?;
        let boot = self.backend.provision_recovery_boot_artifacts()?;
        progress(
            RollbackProgressPhase::ProtectCurrent,
            0.18,
            "Protecting the current Home",
        );
        let home_fallback = self.backend.create_home_fallback(&report)?;
        // This snapshot anchors root identity and boot metadata only. Early
        // boot does not replace @root for a Home-only transaction.
        let anchor = self.backend.create_fallback()?;
        if anchor.snapshot_parent_uuid.is_none() {
            return Err(RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "Root anchor has no parent UUID",
            ));
        }
        let mut transaction = RollbackTransaction::new(
            anchor.id,
            anchor.id,
            root_uuid,
            boot.kernel_release,
            boot.kernel_sha256,
            boot.initramfs_sha256,
            boot.confirm_sha256,
        );
        transaction.home_only = true;
        transaction
            .enable_home_reset(&home)
            .map_err(transaction_error)?;
        transaction.fallback_home_snapshot_id = Some(home_fallback.id);
        self.arm_transaction(transaction, progress)
    }

    pub fn schedule<F>(
        &self,
        target_id: DeploymentId,
        progress: F,
    ) -> Result<RollbackTransaction, RollbackError>
    where
        F: FnMut(RollbackProgressPhase, f64, &str),
    {
        self.schedule_internal(target_id, false, false, progress)
    }

    pub fn schedule_factory_reset<F>(
        &self,
        target_id: DeploymentId,
        reset_home: bool,
        progress: F,
    ) -> Result<RollbackTransaction, RollbackError>
    where
        F: FnMut(RollbackProgressPhase, f64, &str),
    {
        self.schedule_internal(target_id, true, reset_home, progress)
    }

    fn schedule_internal<F>(
        &self,
        target_id: DeploymentId,
        factory_reset: bool,
        reset_home: bool,
        mut progress: F,
    ) -> Result<RollbackTransaction, RollbackError>
    where
        F: FnMut(RollbackProgressPhase, f64, &str),
    {
        let _preparation_lock = self.backend.lock_preparation()?;
        progress(
            RollbackProgressPhase::Validate,
            0.02,
            "Checking the recovery target",
        );
        let (target, report, root_uuid, factory_home) =
            self.validate_ready(target_id, factory_reset, reset_home)?;
        let recovery_boot = self.backend.provision_recovery_boot_artifacts()?;

        progress(
            RollbackProgressPhase::ProtectCurrent,
            0.18,
            "Protecting the current system",
        );
        let fallback = self.backend.create_fallback()?;
        let home_fallback = if reset_home {
            Some(self.backend.create_home_fallback(&report)?)
        } else {
            None
        };
        let mut transaction = RollbackTransaction::new(
            target.id,
            fallback.id,
            root_uuid,
            recovery_boot.kernel_release,
            recovery_boot.kernel_sha256,
            recovery_boot.initramfs_sha256,
            recovery_boot.confirm_sha256,
        );
        if let Some(factory_home) = factory_home.as_ref() {
            transaction
                .enable_home_reset(factory_home)
                .map_err(transaction_error)?;
        }
        transaction.fallback_home_snapshot_id = home_fallback.map(|record| record.id);
        self.arm_transaction(transaction, progress)
    }

    fn arm_transaction<F>(
        &self,
        mut transaction: RollbackTransaction,
        mut progress: F,
    ) -> Result<RollbackTransaction, RollbackError>
    where
        F: FnMut(RollbackProgressPhase, f64, &str),
    {
        let mut recorded = false;
        let result = (|| {
            progress(
                RollbackProgressPhase::RecordTransaction,
                0.58,
                "Recording the restore transaction",
            );
            self.backend.create_transaction(&transaction)?;
            recorded = true;

            progress(
                RollbackProgressPhase::ConfigureBoot,
                0.72,
                "Creating the one-time recovery boot entry",
            );
            self.backend.regenerate_grub()?;
            self.backend.verify_grub_entry(&transaction)?;
            transaction
                .transition(RollbackPhase::Armed, Utc::now())
                .map_err(transaction_error)?;
            self.backend.update_transaction(&transaction)?;
            let armed_entry = self.backend.arm_once()?;
            if armed_entry != transaction.grub_entry_id {
                return Err(RollbackError::new(
                    RollbackErrorCode::BootIntegration,
                    "GRUB armed an unexpected recovery entry",
                ));
            }
            progress(
                RollbackProgressPhase::Commit,
                1.0,
                "System restore is ready for restart",
            );
            Ok(transaction.clone())
        })();

        if let Err(error) = result {
            if !recorded {
                return Err(error);
            }
            progress(
                RollbackProgressPhase::Cleanup,
                0.95,
                "Cancelling the incomplete restore",
            );
            let cleanup = self.cleanup_failed_schedule();
            return match cleanup {
                Ok(()) => Err(error),
                Err(cleanup) => Err(RollbackError::new(
                    error.code,
                    format!("{error}; cleanup also failed: {cleanup}"),
                )),
            };
        }
        result
    }

    pub fn cancel(&self) -> Result<(), RollbackError> {
        let _preparation_lock = self.backend.lock_preparation()?;
        let transaction = self.backend.pending()?.ok_or_else(|| {
            RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "No system restore is pending",
            )
        })?;
        if !matches!(
            transaction.phase,
            RollbackPhase::Preparing | RollbackPhase::Armed
        ) {
            return Err(RollbackError::new(
                RollbackErrorCode::StateCommit,
                "A restore can no longer be cancelled after early boot has begun",
            ));
        }
        self.backend.clear_once()?;
        self.backend.remove_transaction()?;
        self.backend.regenerate_grub()?;
        Ok(())
    }

    fn cleanup_failed_schedule(&self) -> Result<(), RollbackError> {
        let mut problems = Vec::new();
        for result in [
            self.backend.clear_once(),
            self.backend.remove_transaction(),
            self.backend.regenerate_grub(),
        ] {
            if let Err(error) = result {
                problems.push(error.message);
            }
        }
        if problems.is_empty() {
            Ok(())
        } else {
            Err(RollbackError::new(
                RollbackErrorCode::StateCommit,
                problems.join("; "),
            ))
        }
    }
}

fn personal_error(error: crate::personal::PersonalError) -> RollbackError {
    RollbackError::new(RollbackErrorCode::InvalidTarget, error.message)
}

fn run_command(program: &Path, arguments: &[&OsStr]) -> Result<String, RollbackError> {
    let output = Command::new(program)
        .args(arguments)
        .env_clear()
        .env("PATH", COMMAND_PATH)
        .env("LC_ALL", "C")
        .output()
        .map_err(|error| {
            RollbackError::new(
                RollbackErrorCode::CommandFailed,
                format!("Could not execute {}: {error}", program.display()),
            )
        })?;
    if !output.status.success() {
        let diagnostic = command_diagnostic(&output.stderr);
        return Err(RollbackError::new(
            RollbackErrorCode::CommandFailed,
            if diagnostic.is_empty() {
                format!("{} exited with {}", program.display(), output.status)
            } else {
                format!(
                    "{} exited with {}: {diagnostic}",
                    program.display(),
                    output.status
                )
            },
        ));
    }
    if output.stdout.len() > 4096 {
        return Err(RollbackError::new(
            RollbackErrorCode::CommandFailed,
            format!("{} returned excessive output", program.display()),
        ));
    }
    String::from_utf8(output.stdout).map_err(|_| {
        RollbackError::new(
            RollbackErrorCode::CommandFailed,
            format!("{} returned non-UTF-8 output", program.display()),
        )
    })
}

fn command_diagnostic(stderr: &[u8]) -> String {
    if stderr.len() > 4096 {
        return "diagnostic output exceeded 4096 bytes".into();
    }
    let Ok(value) = std::str::from_utf8(stderr) else {
        return "diagnostic output was not UTF-8".into();
    };
    value
        .trim()
        .chars()
        .map(|character| {
            if character == '\n' || character == '\r' || character == '\t' {
                ' '
            } else if character.is_control() {
                '\u{fffd}'
            } else {
                character
            }
        })
        .collect()
}

fn verify_grub_config(path: &Path, transaction: &RollbackTransaction) -> Result<(), RollbackError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        RollbackError::new(
            RollbackErrorCode::BootIntegration,
            format!("Could not inspect GRUB configuration: {error}"),
        )
    })?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_GRUB_CONFIG_BYTES {
        return Err(RollbackError::new(
            RollbackErrorCode::BootIntegration,
            "GRUB configuration is not a safe regular file",
        ));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| {
            RollbackError::new(
                RollbackErrorCode::BootIntegration,
                format!("Could not open GRUB configuration: {error}"),
            )
        })?;
    file.seek(SeekFrom::Start(0)).map_err(|error| {
        RollbackError::new(
            RollbackErrorCode::BootIntegration,
            format!("Could not read GRUB configuration: {error}"),
        )
    })?;
    let mut contents = String::new();
    Read::by_ref(&mut file)
        .take(MAX_GRUB_CONFIG_BYTES + 1)
        .read_to_string(&mut contents)
        .map_err(|error| {
            RollbackError::new(
                RollbackErrorCode::BootIntegration,
                format!("Could not read GRUB configuration: {error}"),
            )
        })?;
    let entry_marker = format!("--id '{}'", transaction.grub_entry_id);
    let request_marker = format!("anduinos.btrfs_snapshots_manager={}", transaction.id);
    if contents.matches(&entry_marker).count() != 1
        || contents.matches(&request_marker).count() != 1
    {
        return Err(RollbackError::new(
            RollbackErrorCode::BootIntegration,
            "GRUB did not contain exactly one transaction-bound recovery entry",
        ));
    }
    Ok(())
}

fn canonical_uuid(value: &str, name: &str) -> Result<String, RollbackError> {
    let parsed = uuid::Uuid::parse_str(value).map_err(|_| {
        RollbackError::new(
            RollbackErrorCode::InvalidTarget,
            format!("{name} UUID is invalid"),
        )
    })?;
    let canonical = parsed.hyphenated().to_string();
    if canonical != value {
        return Err(RollbackError::new(
            RollbackErrorCode::InvalidTarget,
            format!("{name} UUID is not canonical"),
        ));
    }
    Ok(canonical)
}

fn transaction_error(error: crate::transaction::TransactionError) -> RollbackError {
    let code = if error.code == crate::transaction::TransactionErrorCode::AlreadyPending {
        RollbackErrorCode::AlreadyPending
    } else {
        RollbackErrorCode::StateCommit
    };
    RollbackError::new(code, error.message)
}

impl Default for TransactionStore {
    fn default() -> Self {
        Self::new(RECOVERY_STORE_ROOT)
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};

    use chrono::Utc;

    use super::*;
    use crate::layout::{LayoutSupport, MountReport};
    use crate::model::{DeploymentKind, DeploymentRecord};
    use crate::{DEPLOYMENT_SCHEMA_VERSION, PERSONAL_SNAPSHOT_SCHEMA_VERSION};

    #[derive(Clone)]
    struct FakeBackend {
        inner: Arc<Mutex<FakeState>>,
    }

    struct FakeState {
        records: HashMap<DeploymentId, DeploymentRecord>,
        pending: Option<RollbackTransaction>,
        package_pending: bool,
        calls: Vec<String>,
        fail_once: Option<String>,
        factory_home: Option<PersonalSnapshotRecord>,
    }

    impl FakeBackend {
        fn new() -> (Self, DeploymentId) {
            let target = record(DeploymentKind::Manual, DeploymentState::Ready);
            let target_id = target.id;
            let mut records = HashMap::new();
            records.insert(target.id, target);
            (
                Self {
                    inner: Arc::new(Mutex::new(FakeState {
                        records,
                        pending: None,
                        package_pending: false,
                        calls: Vec::new(),
                        fail_once: None,
                        factory_home: None,
                    })),
                },
                target_id,
            )
        }

        fn fail_once(&self, operation: &str) {
            self.inner.lock().unwrap().fail_once = Some(operation.into());
        }

        fn hit(&self, operation: &str) -> Result<(), RollbackError> {
            let mut inner = self.inner.lock().unwrap();
            inner.calls.push(operation.into());
            if inner.fail_once.as_deref() == Some(operation) {
                inner.fail_once = None;
                return Err(RollbackError::new(
                    RollbackErrorCode::CommandFailed,
                    format!("injected {operation} failure"),
                ));
            }
            Ok(())
        }
    }

    impl RollbackBackend for FakeBackend {
        fn layout(&self) -> LayoutReport {
            supported_layout()
        }

        fn pending(&self) -> Result<Option<RollbackTransaction>, RollbackError> {
            self.hit("pending")?;
            Ok(self.inner.lock().unwrap().pending.clone())
        }

        fn package_transaction_pending(&self) -> Result<bool, RollbackError> {
            self.hit("package-pending")?;
            Ok(self.inner.lock().unwrap().package_pending)
        }

        fn verify_target(&self, id: DeploymentId) -> Result<DeploymentRecord, RollbackError> {
            self.hit("verify-target")?;
            self.inner
                .lock()
                .unwrap()
                .records
                .get(&id)
                .cloned()
                .ok_or_else(|| RollbackError::new(RollbackErrorCode::InvalidTarget, "missing"))
        }

        fn verify_factory_home(
            &self,
            _report: &LayoutReport,
        ) -> Result<PersonalSnapshotRecord, RollbackError> {
            self.hit("verify-factory-home")?;
            self.inner
                .lock()
                .unwrap()
                .factory_home
                .clone()
                .ok_or_else(|| {
                    RollbackError::new(
                        RollbackErrorCode::InvalidTarget,
                        "missing factory Home baseline",
                    )
                })
        }

        fn check_fallback_capacity(&self, _report: &LayoutReport) -> Result<(), RollbackError> {
            self.hit("check-fallback-capacity")
        }

        fn verify_home(
            &self,
            report: &LayoutReport,
            id: PersonalSnapshotId,
        ) -> Result<PersonalSnapshotRecord, RollbackError> {
            self.hit("verify-home")?;
            let record = self.verify_factory_home(report)?;
            if record.id != id {
                return Err(RollbackError::new(
                    RollbackErrorCode::InvalidTarget,
                    "Missing Home snapshot",
                ));
            }
            Ok(record)
        }

        fn check_home_capacity(&self, _report: &LayoutReport) -> Result<(), RollbackError> {
            self.hit("check-home-capacity")
        }

        fn create_home_fallback(
            &self,
            _report: &LayoutReport,
        ) -> Result<PersonalSnapshotRecord, RollbackError> {
            self.hit("create-home-fallback")?;
            Ok(factory_home())
        }

        fn create_fallback(&self) -> Result<DeploymentRecord, RollbackError> {
            self.hit("create-fallback")?;
            let mut fallback = record(DeploymentKind::PreRollback, DeploymentState::Ready);
            fallback.snapshot_parent_uuid = Some("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb".into());
            self.inner
                .lock()
                .unwrap()
                .records
                .insert(fallback.id, fallback.clone());
            Ok(fallback)
        }

        fn root_filesystem_uuid(&self, _report: &LayoutReport) -> Result<String, RollbackError> {
            self.hit("root-uuid")?;
            Ok("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb".into())
        }

        fn verify_one_shot_support(&self) -> Result<(), RollbackError> {
            self.hit("verify-one-shot")
        }

        fn verify_recovery_boot_source(&self) -> Result<(), RollbackError> {
            self.hit("verify-recovery-boot-source")
        }

        fn provision_recovery_boot_artifacts(
            &self,
        ) -> Result<RecoveryBootArtifacts, RollbackError> {
            self.hit("provision-recovery-boot")?;
            Ok(RecoveryBootArtifacts {
                kernel_release: "7.0.0-test".into(),
                kernel_sha256: "d".repeat(64),
                initramfs_sha256: "e".repeat(64),
                confirm_sha256: "f".repeat(64),
            })
        }

        fn create_transaction(
            &self,
            transaction: &RollbackTransaction,
        ) -> Result<(), RollbackError> {
            self.hit("create-transaction")?;
            self.inner.lock().unwrap().pending = Some(transaction.clone());
            Ok(())
        }

        fn update_transaction(
            &self,
            transaction: &RollbackTransaction,
        ) -> Result<(), RollbackError> {
            self.hit("update-transaction")?;
            self.inner.lock().unwrap().pending = Some(transaction.clone());
            Ok(())
        }

        fn remove_transaction(&self) -> Result<(), RollbackError> {
            self.hit("remove-transaction")?;
            self.inner.lock().unwrap().pending = None;
            Ok(())
        }

        fn regenerate_grub(&self) -> Result<(), RollbackError> {
            self.hit("regenerate-grub")
        }

        fn verify_grub_entry(
            &self,
            _transaction: &RollbackTransaction,
        ) -> Result<(), RollbackError> {
            self.hit("verify-grub")
        }

        fn arm_once(&self) -> Result<String, RollbackError> {
            self.hit("arm-once")?;
            Ok(self
                .inner
                .lock()
                .unwrap()
                .pending
                .as_ref()
                .unwrap()
                .grub_entry_id
                .clone())
        }

        fn clear_once(&self) -> Result<(), RollbackError> {
            self.hit("clear-once")
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
            title: "Test system snapshot".into(),
            reason: "Rollback coordinator test".into(),
            schedule_id: None,
            snapshot_uuid: Some("cccccccc-1111-4222-8333-dddddddddddd".into()),
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

    fn supported_layout() -> LayoutReport {
        LayoutReport {
            support: LayoutSupport::Supported,
            root_filesystem: Some("btrfs".into()),
            root_source: Some("/dev/test".into()),
            issues: Vec::new(),
            mounts: vec![MountReport {
                mount_point: "/".into(),
                subvolume: "/@root".into(),
                filesystem: "btrfs".into(),
                source: "/dev/test".into(),
            }],
        }
    }

    fn factory_home() -> PersonalSnapshotRecord {
        PersonalSnapshotRecord {
            schema_version: PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            id: crate::personal::PersonalSnapshotId::new(),
            kind: PersonalSnapshotKind::Factory,
            state: crate::personal::PersonalSnapshotState::Ready,
            created_at: Utc::now(),
            title: crate::personal::FACTORY_HOME_TITLE.into(),
            reason: "Initial AnduinOS Home".into(),
            schedule_id: None,
            snapshot_uuid: Some("99999999-1111-4222-8333-aaaaaaaaaaaa".into()),
            snapshot_parent_uuid: None,
            pinned: true,
            failure: None,
        }
    }

    #[test]
    fn schedule_arms_only_after_fallback_transaction_and_grub_verification() {
        let (backend, target) = FakeBackend::new();
        let transaction = RollbackCoordinator::new(backend.clone())
            .schedule(target, |_phase, _fraction, _message| {})
            .unwrap();
        assert_eq!(transaction.phase, RollbackPhase::Armed);
        let inner = backend.inner.lock().unwrap();
        assert_eq!(inner.records[&target].state, DeploymentState::Ready);
        assert_eq!(
            inner.records[&transaction.fallback_deployment_id].state,
            DeploymentState::Ready
        );
        let arm = inner
            .calls
            .iter()
            .position(|call| call == "arm-once")
            .unwrap();
        let verify = inner
            .calls
            .iter()
            .position(|call| call == "verify-grub")
            .unwrap();
        assert!(verify < arm);
        let capability = inner
            .calls
            .iter()
            .position(|call| call == "verify-one-shot")
            .unwrap();
        let fallback = inner
            .calls
            .iter()
            .position(|call| call == "create-fallback")
            .unwrap();
        assert!(capability < fallback);
    }

    #[test]
    fn home_restore_protects_home_and_binds_unchanged_root_before_arming() {
        let (backend, _) = FakeBackend::new();
        let mut home = factory_home();
        home.kind = PersonalSnapshotKind::Manual;
        backend.inner.lock().unwrap().factory_home = Some(home.clone());
        let transaction = RollbackCoordinator::new(backend.clone())
            .schedule_home(home.id, |_, _, _| {})
            .unwrap();
        assert!(transaction.home_only && transaction.reset_home);
        assert_eq!(transaction.factory_home_snapshot_id, Some(home.id));
        assert_eq!(
            transaction.target_deployment_id,
            transaction.fallback_deployment_id
        );
        assert_eq!(transaction.phase, RollbackPhase::Armed);
        let inner = backend.inner.lock().unwrap();
        let protect = inner
            .calls
            .iter()
            .position(|call| call == "create-home-fallback")
            .unwrap();
        let arm = inner
            .calls
            .iter()
            .position(|call| call == "arm-once")
            .unwrap();
        assert!(protect < arm);
    }

    #[test]
    fn home_capacity_or_safety_snapshot_failure_never_arms_recovery() {
        for failure in ["verify-home", "check-home-capacity", "create-home-fallback"] {
            let (backend, _) = FakeBackend::new();
            let home = factory_home();
            backend.inner.lock().unwrap().factory_home = Some(home.clone());
            backend.fail_once(failure);
            assert!(
                RollbackCoordinator::new(backend.clone())
                    .schedule_home(home.id, |_, _, _| {})
                    .is_err()
            );
            let inner = backend.inner.lock().unwrap();
            assert!(inner.pending.is_none());
            assert!(!inner.calls.iter().any(|call| call == "arm-once"));
        }
    }

    #[test]
    fn factory_reset_binds_home_only_when_erasure_is_explicitly_requested() {
        let (backend, target) = FakeBackend::new();
        {
            let mut inner = backend.inner.lock().unwrap();
            let target = inner.records.get_mut(&target).unwrap();
            target.kind = DeploymentKind::Factory;
            target.title = crate::model::FACTORY_DEPLOYMENT_TITLE.into();
            target.pinned = true;
            inner.factory_home = Some(factory_home());
        }
        let transaction = RollbackCoordinator::new(backend.clone())
            .schedule_factory_reset(target, true, |_phase, _fraction, _message| {})
            .unwrap();
        let expected = backend.inner.lock().unwrap().factory_home.clone().unwrap();
        assert!(transaction.reset_home);
        assert_eq!(transaction.factory_home_snapshot_id, Some(expected.id));
        assert_eq!(
            transaction.factory_home_snapshot_uuid,
            expected.snapshot_uuid
        );
    }

    #[test]
    fn factory_reset_without_home_erasure_does_not_require_a_home_baseline() {
        let (backend, target) = FakeBackend::new();
        {
            let mut inner = backend.inner.lock().unwrap();
            let target = inner.records.get_mut(&target).unwrap();
            target.kind = DeploymentKind::Factory;
            target.title = crate::model::FACTORY_DEPLOYMENT_TITLE.into();
            target.pinned = true;
        }
        let transaction = RollbackCoordinator::new(backend.clone())
            .schedule_factory_reset(target, false, |_phase, _fraction, _message| {})
            .unwrap();
        assert!(!transaction.reset_home);
        assert!(transaction.factory_home_snapshot_id.is_none());
        assert!(
            !backend
                .inner
                .lock()
                .unwrap()
                .calls
                .iter()
                .any(|call| call == "verify-factory-home")
        );
    }

    #[test]
    fn readiness_checks_capacity_without_creating_or_arming_any_state() {
        let (backend, target) = FakeBackend::new();
        let checked = RollbackCoordinator::new(backend.clone())
            .check_ready(target)
            .unwrap();
        assert_eq!(checked.id, target);
        let inner = backend.inner.lock().unwrap();
        assert!(
            inner
                .calls
                .iter()
                .any(|call| call == "check-fallback-capacity")
        );
        assert!(
            inner
                .calls
                .iter()
                .any(|call| call == "verify-recovery-boot-source")
        );
        assert!(!inner.calls.iter().any(|call| call == "create-fallback"));
        assert!(
            !inner
                .calls
                .iter()
                .any(|call| call == "provision-recovery-boot")
        );
        assert!(!inner.calls.iter().any(|call| call == "arm-once"));
        assert!(inner.pending.is_none());
    }

    #[test]
    fn readiness_capacity_failure_never_provisions_or_creates_recovery_state() {
        let (backend, target) = FakeBackend::new();
        backend.fail_once("check-fallback-capacity");
        assert!(
            RollbackCoordinator::new(backend.clone())
                .check_ready(target)
                .is_err()
        );
        let inner = backend.inner.lock().unwrap();
        assert!(!inner.calls.iter().any(|call| call == "create-fallback"));
        assert!(
            !inner
                .calls
                .iter()
                .any(|call| call == "provision-recovery-boot")
        );
        assert!(!inner.calls.iter().any(|call| call == "arm-once"));
        assert!(inner.pending.is_none());
    }

    #[test]
    fn every_post_fallback_failure_clears_boot_and_restores_safe_metadata() {
        for failure in [
            "create-transaction",
            "regenerate-grub",
            "verify-grub",
            "update-transaction",
            "arm-once",
        ] {
            let (backend, target) = FakeBackend::new();
            backend.fail_once(failure);
            assert!(
                RollbackCoordinator::new(backend.clone())
                    .schedule(target, |_phase, _fraction, _message| {})
                    .is_err()
            );
            let inner = backend.inner.lock().unwrap();
            assert!(
                inner.pending.is_none(),
                "pending transaction after {failure}"
            );
            assert_eq!(inner.records[&target].state, DeploymentState::Ready);
            assert!(
                inner
                    .records
                    .values()
                    .filter(|record| record.kind == DeploymentKind::PreRollback)
                    .all(|record| record.state == DeploymentState::Ready)
            );
            assert_eq!(
                inner.calls.iter().any(|call| call == "clear-once"),
                failure != "create-transaction"
            );
        }
    }

    #[test]
    fn armed_restore_can_be_cancelled_before_reboot() {
        let (backend, target) = FakeBackend::new();
        let coordinator = RollbackCoordinator::new(backend.clone());
        let transaction = coordinator
            .schedule(target, |_phase, _fraction, _message| {})
            .unwrap();
        coordinator.cancel().unwrap();
        let inner = backend.inner.lock().unwrap();
        assert!(inner.pending.is_none());
        assert_eq!(inner.records[&target].state, DeploymentState::Ready);
        assert_eq!(
            inner.records[&transaction.fallback_deployment_id].state,
            DeploymentState::Ready
        );
    }

    #[test]
    fn the_same_healthy_snapshot_can_be_scheduled_repeatedly() {
        let (backend, target) = FakeBackend::new();
        let coordinator = RollbackCoordinator::new(backend.clone());
        coordinator
            .schedule(target, |_phase, _fraction, _message| {})
            .unwrap();
        coordinator.cancel().unwrap();
        coordinator
            .schedule(target, |_phase, _fraction, _message| {})
            .unwrap();

        let inner = backend.inner.lock().unwrap();
        assert_eq!(inner.records[&target].state, DeploymentState::Ready);
        assert!(inner.pending.is_some());
    }

    #[test]
    fn package_transaction_blocks_restore_before_fallback_creation() {
        let (backend, target) = FakeBackend::new();
        backend.inner.lock().unwrap().package_pending = true;
        assert_eq!(
            RollbackCoordinator::new(backend.clone())
                .schedule(target, |_phase, _fraction, _message| {})
                .unwrap_err()
                .code,
            RollbackErrorCode::AlreadyPending
        );
        let inner = backend.inner.lock().unwrap();
        assert!(!inner.calls.iter().any(|call| call == "create-fallback"));
        assert_eq!(inner.records.len(), 1);
    }

    #[test]
    fn grub_config_requires_exactly_one_transaction_entry() {
        let mut transaction = RollbackTransaction::new(
            DeploymentId::new(),
            DeploymentId::new(),
            "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb",
            "7.0.0-test",
            "a".repeat(64),
            "b".repeat(64),
            "c".repeat(64),
        );
        transaction
            .transition(RollbackPhase::Armed, Utc::now())
            .unwrap();
        let root =
            std::env::temp_dir().join(format!("snapshots-manager-grub-{}", uuid::Uuid::new_v4()));
        let line = format!(
            "menuentry x --id '{}' {{ linux anduinos.btrfs_snapshots_manager={} }}\n",
            transaction.grub_entry_id, transaction.id
        );
        fs::write(&root, &line).unwrap();
        verify_grub_config(&root, &transaction).unwrap();
        fs::write(&root, format!("{line}{line}")).unwrap();
        assert!(verify_grub_config(&root, &transaction).is_err());
        fs::remove_file(root).unwrap();
    }
}
