use std::ffi::OsStr;
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use std::process::Command;

use chrono::Utc;

use crate::RECOVERY_STORE_ROOT;
use crate::boot::BootIntegration;
use crate::coordination::TransactionStartLock;
use crate::layout::{self, LayoutReport};
use crate::model::{DeploymentId, DeploymentRecord, DeploymentState};
use crate::operations::OperationEngine;
use crate::package_transaction::PackageTransactionStore;
use crate::secure_boot::SecureBootValidator;
use crate::transaction::{RollbackPhase, RollbackTransaction, TransactionStore};

const BLKID: &str = "/usr/sbin/blkid";
const UPDATE_GRUB: &str = "/usr/sbin/update-grub";
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
    fn layout(&self) -> LayoutReport;
    fn pending(&self) -> Result<Option<RollbackTransaction>, RollbackError>;
    fn package_transaction_pending(&self) -> Result<bool, RollbackError>;
    fn verify_target(&self, id: DeploymentId) -> Result<DeploymentRecord, RollbackError>;
    fn create_fallback(&self) -> Result<DeploymentRecord, RollbackError>;
    fn transition(
        &self,
        id: DeploymentId,
        state: DeploymentState,
    ) -> Result<DeploymentRecord, RollbackError>;
    fn root_filesystem_uuid(&self, report: &LayoutReport) -> Result<String, RollbackError>;
    fn verify_one_shot_support(&self) -> Result<(), RollbackError>;
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

    fn create_fallback(&self) -> Result<DeploymentRecord, RollbackError> {
        OperationEngine::default()
            .create_pre_rollback(&self.layout(), |_phase, _fraction, _message| {})
            .map_err(|error| RollbackError::new(RollbackErrorCode::StateCommit, error.message))
    }

    fn transition(
        &self,
        id: DeploymentId,
        state: DeploymentState,
    ) -> Result<DeploymentRecord, RollbackError> {
        OperationEngine::default()
            .transition_deployment(&self.layout(), id, state)
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
        verify_grub_config(Path::new(GRUB_CONFIG), transaction)
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

    pub fn schedule<F>(
        &self,
        target_id: DeploymentId,
        mut progress: F,
    ) -> Result<RollbackTransaction, RollbackError>
    where
        F: FnMut(RollbackProgressPhase, f64, &str),
    {
        progress(
            RollbackProgressPhase::Validate,
            0.02,
            "Checking the recovery target",
        );
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
        let target = self.backend.verify_target(target_id)?;
        if target.state != DeploymentState::Ready {
            return Err(RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "Only a ready, unprotected system snapshot can be scheduled",
            ));
        }
        let target_original_state = target.state;
        let kernel = target.kernel_release.clone().ok_or_else(|| {
            RollbackError::new(
                RollbackErrorCode::InvalidTarget,
                "Recovery target has no kernel identity",
            )
        })?;
        let root_uuid = self.backend.root_filesystem_uuid(&report)?;
        self.backend.verify_one_shot_support()?;

        progress(
            RollbackProgressPhase::ProtectCurrent,
            0.18,
            "Protecting the current system",
        );
        let fallback = self.backend.create_fallback()?;
        let mut transaction = RollbackTransaction::new(target.id, fallback.id, root_uuid, kernel);
        let result = (|| {
            progress(
                RollbackProgressPhase::RecordTransaction,
                0.58,
                "Recording the restore transaction",
            );
            self.backend.create_transaction(&transaction)?;
            self.backend
                .transition(target.id, DeploymentState::PendingRollback)?;

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
            progress(
                RollbackProgressPhase::Cleanup,
                0.95,
                "Cancelling the incomplete restore",
            );
            let cleanup =
                self.cleanup_failed_schedule(target.id, target_original_state, fallback.id);
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
        self.backend
            .transition(transaction.target_deployment_id, DeploymentState::Ready)?;
        self.backend
            .transition(transaction.fallback_deployment_id, DeploymentState::Ready)?;
        self.backend.regenerate_grub()?;
        Ok(())
    }

    fn cleanup_failed_schedule(
        &self,
        target: DeploymentId,
        target_state: DeploymentState,
        fallback: DeploymentId,
    ) -> Result<(), RollbackError> {
        let mut problems = Vec::new();
        for result in [
            self.backend.clear_once(),
            self.backend.remove_transaction(),
            self.backend.transition(target, target_state).map(|_| ()),
            self.backend
                .transition(fallback, DeploymentState::Ready)
                .map(|_| ()),
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
    use crate::DEPLOYMENT_SCHEMA_VERSION;
    use crate::layout::{LayoutSupport, MountReport};
    use crate::model::{DeploymentKind, DeploymentRecord};

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

        fn create_fallback(&self) -> Result<DeploymentRecord, RollbackError> {
            self.hit("create-fallback")?;
            let fallback = record(
                DeploymentKind::PreRollback,
                DeploymentState::FallbackProtected,
            );
            self.inner
                .lock()
                .unwrap()
                .records
                .insert(fallback.id, fallback.clone());
            Ok(fallback)
        }

        fn transition(
            &self,
            id: DeploymentId,
            state: DeploymentState,
        ) -> Result<DeploymentRecord, RollbackError> {
            self.hit(&format!("transition-{state:?}"))?;
            let mut inner = self.inner.lock().unwrap();
            let record = inner
                .records
                .get_mut(&id)
                .ok_or_else(|| RollbackError::new(RollbackErrorCode::InvalidTarget, "missing"))?;
            if record.state != state && !record.state.can_transition_to(state) {
                return Err(RollbackError::new(
                    RollbackErrorCode::StateCommit,
                    "invalid test transition",
                ));
            }
            record.state = state;
            Ok(record.clone())
        }

        fn root_filesystem_uuid(&self, _report: &LayoutReport) -> Result<String, RollbackError> {
            self.hit("root-uuid")?;
            Ok("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb".into())
        }

        fn verify_one_shot_support(&self) -> Result<(), RollbackError> {
            self.hit("verify-one-shot")
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

    #[test]
    fn schedule_arms_only_after_fallback_transaction_and_grub_verification() {
        let (backend, target) = FakeBackend::new();
        let transaction = RollbackCoordinator::new(backend.clone())
            .schedule(target, |_phase, _fraction, _message| {})
            .unwrap();
        assert_eq!(transaction.phase, RollbackPhase::Armed);
        let inner = backend.inner.lock().unwrap();
        assert_eq!(
            inner.records[&target].state,
            DeploymentState::PendingRollback
        );
        assert_eq!(
            inner.records[&transaction.fallback_deployment_id].state,
            DeploymentState::FallbackProtected
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
    fn every_post_fallback_failure_clears_boot_and_restores_safe_metadata() {
        for failure in [
            "create-transaction",
            "transition-PendingRollback",
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
            assert!(inner.calls.iter().any(|call| call == "clear-once"));
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
