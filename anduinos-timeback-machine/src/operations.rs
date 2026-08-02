use std::ffi::OsString;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;

use chrono::Utc;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::coordination::TransactionStartLock;
use crate::layout::{LayoutReport, LayoutSupport};
use crate::model::{DeploymentId, DeploymentKind, DeploymentRecord, DeploymentState};
use crate::package_transaction::PackageTransactionStore;
use crate::store::DeploymentStore;
use crate::transaction::TransactionStore;
use crate::{DEPLOYMENT_SCHEMA_VERSION, SNAPSHOT_ROOT};

const BTRFS: &str = "/usr/bin/btrfs";
const KERNEL_RELEASE: &str = "/proc/sys/kernel/osrelease";
const MOK_CERTIFICATE: &str = "var/lib/shim-signed/mok/MOK.der";
const MAX_COMMAND_DIAGNOSTIC: usize = 2000;
const MAX_FAILURE_MESSAGE: usize = 2000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OperationPhase {
    Validate,
    RecordTransaction,
    Snapshot,
    CaptureIdentity,
    Commit,
    Cleanup,
}

impl OperationPhase {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Validate => "validate",
            Self::RecordTransaction => "record-transaction",
            Self::Snapshot => "snapshot",
            Self::CaptureIdentity => "capture-identity",
            Self::Commit => "commit",
            Self::Cleanup => "cleanup",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OperationErrorCode {
    Busy,
    UnsupportedLayout,
    InvalidInput,
    UnsafePath,
    NotFound,
    Protected,
    Io,
    CommandFailed,
    InvalidIdentity,
}

impl OperationErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Busy => "busy",
            Self::UnsupportedLayout => "unsupported-layout",
            Self::InvalidInput => "invalid-input",
            Self::UnsafePath => "unsafe-path",
            Self::NotFound => "not-found",
            Self::Protected => "protected",
            Self::Io => "io-error",
            Self::CommandFailed => "command-failed",
            Self::InvalidIdentity => "invalid-identity",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OperationError {
    pub code: OperationErrorCode,
    pub message: String,
}

impl OperationError {
    fn new(code: OperationErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for OperationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for OperationError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommandOutput {
    pub stdout: Vec<u8>,
}

pub trait CommandRunner: Clone + Send + Sync + 'static {
    fn run(&self, program: &Path, arguments: &[OsString]) -> Result<CommandOutput, OperationError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemCommandRunner;

impl CommandRunner for SystemCommandRunner {
    fn run(&self, program: &Path, arguments: &[OsString]) -> Result<CommandOutput, OperationError> {
        let output = Command::new(program)
            .args(arguments)
            .env_clear()
            .env("LC_ALL", "C")
            .output()
            .map_err(|error| {
                OperationError::new(
                    OperationErrorCode::CommandFailed,
                    format!("Could not execute {}: {error}", program.display()),
                )
            })?;
        if !output.status.success() {
            let diagnostic = safe_diagnostic(&output.stderr);
            return Err(OperationError::new(
                OperationErrorCode::CommandFailed,
                format!(
                    "{} exited with {}{}",
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
        Ok(CommandOutput {
            stdout: output.stdout,
        })
    }
}

#[derive(Clone, Debug)]
pub struct OperationEngine<R = SystemCommandRunner> {
    system_root: PathBuf,
    snapshot_root: PathBuf,
    runner: R,
}

impl Default for OperationEngine<SystemCommandRunner> {
    fn default() -> Self {
        Self::new("/", SNAPSHOT_ROOT, SystemCommandRunner)
    }
}

impl<R: CommandRunner> OperationEngine<R> {
    pub fn new(
        system_root: impl Into<PathBuf>,
        snapshot_root: impl Into<PathBuf>,
        runner: R,
    ) -> Self {
        Self {
            system_root: system_root.into(),
            snapshot_root: snapshot_root.into(),
            runner,
        }
    }

    pub fn create_manual<F>(
        &self,
        layout: &LayoutReport,
        title: &str,
        reason: &str,
        pinned: bool,
        progress: F,
    ) -> Result<DeploymentRecord, OperationError>
    where
        F: FnMut(OperationPhase, f64, &str),
    {
        self.create_snapshot(
            layout,
            title,
            reason,
            pinned,
            DeploymentKind::Manual,
            DeploymentState::Ready,
            progress,
        )
    }

    pub fn create_automatic<F>(&self, layout: &LayoutReport, progress: F) -> Result<DeploymentRecord, OperationError>
    where F: FnMut(OperationPhase, f64, &str),
    {
        self.create_snapshot(layout, "Scheduled system snapshot", "Created by the automatic snapshot schedule", false, DeploymentKind::Automatic, DeploymentState::Ready, progress)
    }

    pub fn create_pre_rollback<F>(
        &self,
        layout: &LayoutReport,
        progress: F,
    ) -> Result<DeploymentRecord, OperationError>
    where
        F: FnMut(OperationPhase, f64, &str),
    {
        self.create_snapshot(
            layout,
            "Before system restore",
            "Automatically protected before restoring an earlier recovery point",
            false,
            DeploymentKind::PreRollback,
            DeploymentState::FallbackProtected,
            progress,
        )
    }

    pub fn create_apt_pre<F>(
        &self,
        layout: &LayoutReport,
        transaction_id: &str,
        progress: F,
    ) -> Result<DeploymentRecord, OperationError>
    where
        F: FnMut(OperationPhase, f64, &str),
    {
        validate_transaction_label(transaction_id)?;
        self.create_snapshot(
            layout,
            "Before package changes",
            &format!("Automatic recovery point for package transaction {transaction_id}"),
            false,
            DeploymentKind::AptPre,
            DeploymentState::Ready,
            progress,
        )
    }

    pub fn create_apt_post<F>(
        &self,
        layout: &LayoutReport,
        transaction_id: &str,
        progress: F,
    ) -> Result<DeploymentRecord, OperationError>
    where
        F: FnMut(OperationPhase, f64, &str),
    {
        validate_transaction_label(transaction_id)?;
        self.create_snapshot(
            layout,
            "After package changes",
            &format!("Automatic recovery point for package transaction {transaction_id}"),
            false,
            DeploymentKind::AptPost,
            DeploymentState::Ready,
            progress,
        )
    }

    fn create_snapshot<F>(
        &self,
        layout: &LayoutReport,
        title: &str,
        reason: &str,
        pinned: bool,
        kind: DeploymentKind,
        completed_state: DeploymentState,
        mut progress: F,
    ) -> Result<DeploymentRecord, OperationError>
    where
        F: FnMut(OperationPhase, f64, &str),
    {
        progress(
            OperationPhase::Validate,
            0.02,
            "Validating recovery storage",
        );
        ensure_supported_layout(layout)?;
        self.ensure_store_directories()?;
        let _lock = self.acquire_lock()?;

        let id = DeploymentId::new();
        let mut record = DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id,
            parent_id: self.current_parent(),
            kind,
            state: DeploymentState::Creating,
            created_at: Utc::now(),
            title: title.to_string(),
            reason: reason.to_string(),
            snapshot_uuid: None,
            snapshot_parent_uuid: None,
            kernel_release: None,
            initramfs_sha256: None,
            boot_artifact_sha256: None,
            dpkg_status_sha256: None,
            mok_certificate_sha256: None,
            pinned,
            failure: None,
        };
        record.validate().map_err(|error| {
            OperationError::new(OperationErrorCode::InvalidInput, error.to_string())
        })?;

        progress(
            OperationPhase::RecordTransaction,
            0.08,
            "Recording the recovery transaction",
        );
        self.write_record_atomic(&record)?;
        let deployment_dir = self.deployment_dir(id);
        let snapshot = deployment_dir.join("root");

        let result: Result<(), OperationError> = (|| {
            ensure_new_directory(&deployment_dir, 0o700)?;
            progress(
                OperationPhase::Snapshot,
                0.18,
                "Creating a read-only Btrfs snapshot",
            );
            self.run_btrfs(&[
                OsString::from("subvolume"),
                OsString::from("snapshot"),
                OsString::from("-r"),
                self.system_root.as_os_str().to_owned(),
                snapshot.as_os_str().to_owned(),
            ])?;

            let (snapshot_uuid, parent_uuid) = self.snapshot_identity(&snapshot)?;
            record.snapshot_uuid = Some(snapshot_uuid);
            record.snapshot_parent_uuid = parent_uuid;
            self.sync_btrfs()?;

            progress(
                OperationPhase::CaptureIdentity,
                0.55,
                "Hashing package and boot identities",
            );
            let kernel = read_kernel_release(&self.system_root)?;
            record.kernel_release = Some(kernel.clone());
            record.initramfs_sha256 = Some(hash_regular_file(
                &snapshot.join("boot").join(format!("initrd.img-{kernel}")),
            )?);
            record.boot_artifact_sha256 = Some(hash_regular_file(
                &snapshot.join("boot").join(format!("vmlinuz-{kernel}")),
            )?);
            record.dpkg_status_sha256 =
                Some(hash_regular_file(&snapshot.join("var/lib/dpkg/status"))?);
            record.mok_certificate_sha256 =
                hash_optional_regular_file(&snapshot.join(MOK_CERTIFICATE))?;

            progress(
                OperationPhase::Commit,
                0.90,
                "Committing verified recovery metadata",
            );
            if !record.state.can_transition_to(completed_state) {
                return Err(OperationError::new(
                    OperationErrorCode::InvalidIdentity,
                    "Recovery point cannot enter its completed state",
                ));
            }
            record.state = completed_state;
            record.validate().map_err(|error| {
                OperationError::new(OperationErrorCode::InvalidIdentity, error.to_string())
            })?;
            self.write_record_atomic(&record)?;
            self.sync_btrfs()?;
            Ok(())
        })();

        if let Err(error) = result {
            progress(
                OperationPhase::Cleanup,
                0.95,
                "Reverting the incomplete recovery point",
            );
            let cleanup_error = self.cleanup_snapshot(&snapshot, &deployment_dir).err();
            record.state = DeploymentState::Incomplete;
            record.snapshot_uuid = None;
            record.snapshot_parent_uuid = None;
            record.failure = Some(truncate_failure(match cleanup_error {
                Some(cleanup) => format!("{error}; cleanup also failed: {cleanup}"),
                None => error.to_string(),
            }));
            let metadata_error = self.write_record_atomic(&record).err();
            return Err(match metadata_error {
                Some(metadata) => OperationError::new(
                    error.code,
                    format!("{error}; could not persist failure state: {metadata}"),
                ),
                None => error,
            });
        }

        progress(OperationPhase::Commit, 1.0, "Recovery point created");
        Ok(record)
    }

    pub fn transition_deployment(
        &self,
        layout: &LayoutReport,
        id: DeploymentId,
        next: DeploymentState,
    ) -> Result<DeploymentRecord, OperationError> {
        ensure_supported_layout(layout)?;
        self.ensure_store_directories()?;
        let _lock = self.acquire_lock()?;
        let mut record = self.load_record(id)?;
        if record.state == next {
            return Ok(record);
        }
        if !record.state.can_transition_to(next) {
            return Err(OperationError::new(
                OperationErrorCode::Protected,
                format!(
                    "Recovery point cannot transition from {:?} to {next:?}",
                    record.state
                ),
            ));
        }
        record.state = next;
        record.validate().map_err(|error| {
            OperationError::new(OperationErrorCode::InvalidIdentity, error.to_string())
        })?;
        self.write_record_atomic(&record)?;
        Ok(record)
    }

    pub fn set_pinned(
        &self,
        layout: &LayoutReport,
        id: DeploymentId,
        pinned: bool,
    ) -> Result<DeploymentRecord, OperationError> {
        ensure_supported_layout(layout)?;
        self.ensure_store_directories()?;
        let _lock = self.acquire_lock()?;
        let mut record = self.load_record(id)?;
        if record.state == DeploymentState::Deleting {
            return Err(OperationError::new(
                OperationErrorCode::Protected,
                "A deleting recovery point cannot be pinned",
            ));
        }
        record.pinned = pinned;
        record.validate().map_err(|error| {
            OperationError::new(OperationErrorCode::InvalidIdentity, error.to_string())
        })?;
        self.write_record_atomic(&record)?;
        Ok(record)
    }

    pub fn verify<F>(
        &self,
        layout: &LayoutReport,
        id: DeploymentId,
        mut progress: F,
    ) -> Result<DeploymentRecord, OperationError>
    where
        F: FnMut(OperationPhase, f64, &str),
    {
        progress(
            OperationPhase::Validate,
            0.05,
            "Validating recovery metadata",
        );
        ensure_supported_layout(layout)?;
        self.ensure_store_directories()?;
        let _lock = self.acquire_lock()?;
        let record = self.load_record(id)?;
        if !record.can_restore() {
            return Err(OperationError::new(
                OperationErrorCode::InvalidIdentity,
                "This deployment is not in a restorable state",
            ));
        }
        let snapshot = self.deployment_dir(id).join("root");
        let expected_uuid = record.snapshot_uuid.as_deref().ok_or_else(|| {
            OperationError::new(
                OperationErrorCode::InvalidIdentity,
                "Recovery metadata has no snapshot UUID",
            )
        })?;
        let (actual_uuid, actual_parent_uuid) = self.snapshot_identity(&snapshot)?;
        if actual_uuid != expected_uuid {
            return Err(identity_mismatch("Btrfs snapshot UUID"));
        }
        if actual_parent_uuid != record.snapshot_parent_uuid {
            return Err(identity_mismatch("Btrfs parent UUID"));
        }
        self.verify_read_only(&snapshot)?;

        progress(
            OperationPhase::CaptureIdentity,
            0.35,
            "Verifying kernel and package identities",
        );
        let kernel = record.kernel_release.as_deref().ok_or_else(|| {
            OperationError::new(
                OperationErrorCode::InvalidIdentity,
                "Recovery metadata has no kernel release",
            )
        })?;
        verify_digest(
            "initramfs",
            record.initramfs_sha256.as_deref(),
            &hash_regular_file(&snapshot.join("boot").join(format!("initrd.img-{kernel}")))?,
        )?;
        progress(
            OperationPhase::CaptureIdentity,
            0.58,
            "Verifying boot artifacts",
        );
        verify_digest(
            "kernel boot artifact",
            record.boot_artifact_sha256.as_deref(),
            &hash_regular_file(&snapshot.join("boot").join(format!("vmlinuz-{kernel}")))?,
        )?;
        verify_digest(
            "dpkg database",
            record.dpkg_status_sha256.as_deref(),
            &hash_regular_file(&snapshot.join("var/lib/dpkg/status"))?,
        )?;
        let actual_mok = hash_optional_regular_file(&snapshot.join(MOK_CERTIFICATE))?;
        if actual_mok.as_deref() != record.mok_certificate_sha256.as_deref() {
            return Err(identity_mismatch("MOK certificate"));
        }
        progress(
            OperationPhase::Commit,
            1.0,
            "Recovery point integrity verified",
        );
        Ok(record)
    }

    pub fn delete(&self, layout: &LayoutReport, id: DeploymentId) -> Result<(), OperationError> {
        self.delete_with_restorable_floor(layout, id, None)
    }

    pub fn delete_automatic(
        &self,
        layout: &LayoutReport,
        id: DeploymentId,
        minimum_restorable_deployments: usize,
    ) -> Result<(), OperationError> {
        self.delete_with_restorable_floor(layout, id, Some(minimum_restorable_deployments))
    }

    fn delete_with_restorable_floor(
        &self,
        layout: &LayoutReport,
        id: DeploymentId,
        minimum_restorable_deployments: Option<usize>,
    ) -> Result<(), OperationError> {
        ensure_supported_layout(layout)?;
        self.ensure_store_directories()?;
        let _lock = self.acquire_lock()?;
        let _transaction_start =
            TransactionStartLock::acquire(&self.snapshot_root).map_err(|error| {
                OperationError::new(
                    OperationErrorCode::Busy,
                    format!("Could not coordinate recovery-point deletion: {error}"),
                )
            })?;
        let mut record = self.load_record(id)?;
        self.ensure_not_transaction_referenced(id)?;
        if record.state != DeploymentState::Deleting {
            if let Some(minimum) = minimum_restorable_deployments {
                if !matches!(
                    record.kind,
                    DeploymentKind::Automatic | DeploymentKind::AptPre | DeploymentKind::AptPost
                ) {
                    return Err(OperationError::new(
                        OperationErrorCode::Protected,
                        "Automatic cleanup may delete only scheduled or package recovery points",
                    ));
                }
                let discovery = DeploymentStore::new(&self.snapshot_root).discover();
                if !discovery.issues.is_empty() {
                    return Err(OperationError::new(
                        OperationErrorCode::InvalidIdentity,
                        "Automatic cleanup stopped because recovery metadata has unresolved issues",
                    ));
                }
                let restorable = discovery
                    .deployments
                    .iter()
                    .filter(|deployment| deployment.can_restore())
                    .count();
                if record.can_restore() && restorable <= minimum {
                    return Err(OperationError::new(
                        OperationErrorCode::Protected,
                        "Automatic cleanup would remove the last known-good recovery point",
                    ));
                }
            }
            if !record.can_delete() {
                return Err(OperationError::new(
                    OperationErrorCode::Protected,
                    "This recovery point is pinned or protects a boot transaction",
                ));
            }
            if !record.state.can_transition_to(DeploymentState::Deleting) {
                return Err(OperationError::new(
                    OperationErrorCode::Protected,
                    "This recovery point cannot enter the deleting state",
                ));
            }
            record.state = DeploymentState::Deleting;
            self.write_record_atomic(&record)?;
        }

        let deployment_dir = self.deployment_dir(id);
        let snapshot = deployment_dir.join("root");
        match fs::symlink_metadata(&snapshot) {
            Ok(metadata) if metadata.file_type().is_dir() => {
                self.run_btrfs(&[
                    OsString::from("subvolume"),
                    OsString::from("delete"),
                    OsString::from("--commit-after"),
                    snapshot.as_os_str().to_owned(),
                ])?;
            }
            Ok(_) => {
                return Err(OperationError::new(
                    OperationErrorCode::UnsafePath,
                    "The deployment root is not a real directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_error("Could not inspect the deployment root", error)),
        }
        match fs::remove_dir(&deployment_dir) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_error("Could not remove the deployment directory", error)),
        }
        self.remove_record(id)?;
        self.sync_btrfs()?;
        Ok(())
    }

    fn ensure_not_transaction_referenced(&self, id: DeploymentId) -> Result<(), OperationError> {
        let rollback = TransactionStore::new(&self.snapshot_root)
            .load_pending()
            .map_err(|error| {
                OperationError::new(
                    OperationErrorCode::InvalidIdentity,
                    format!("Could not validate pending rollback references: {error}"),
                )
            })?;
        if rollback.as_ref().is_some_and(|transaction| {
            transaction.target_deployment_id == id || transaction.fallback_deployment_id == id
        }) {
            return Err(OperationError::new(
                OperationErrorCode::Protected,
                "This recovery point is referenced by a pending rollback",
            ));
        }
        let package = PackageTransactionStore::new(&self.snapshot_root)
            .load_pending()
            .map_err(|error| {
                OperationError::new(
                    OperationErrorCode::InvalidIdentity,
                    format!("Could not validate pending package references: {error}"),
                )
            })?;
        if package.as_ref().is_some_and(|transaction| {
            transaction.pre_deployment_id == Some(id) || transaction.post_deployment_id == Some(id)
        }) {
            return Err(OperationError::new(
                OperationErrorCode::Protected,
                "This recovery point is referenced by a pending package transaction",
            ));
        }
        Ok(())
    }

    fn current_parent(&self) -> Option<DeploymentId> {
        DeploymentStore::new(&self.snapshot_root)
            .discover()
            .deployments
            .into_iter()
            .find(|record| record.state == DeploymentState::Current)
            .map(|record| record.id)
    }

    fn load_record(&self, id: DeploymentId) -> Result<DeploymentRecord, OperationError> {
        DeploymentStore::new(&self.snapshot_root)
            .load_record(id)
            .map_err(|error| {
                OperationError::new(
                    if error.code == crate::store::DiscoveryIssueCode::ReadFailed {
                        OperationErrorCode::NotFound
                    } else {
                        OperationErrorCode::InvalidIdentity
                    },
                    error.message,
                )
            })
    }

    fn ensure_store_directories(&self) -> Result<(), OperationError> {
        let parent = self.snapshot_root.parent().ok_or_else(|| {
            OperationError::new(
                OperationErrorCode::UnsafePath,
                "Snapshot root has no parent directory",
            )
        })?;
        ensure_existing_directory(parent)?;
        ensure_directory(&self.snapshot_root, 0o700)?;
        ensure_directory(&self.snapshot_root.join("deployments"), 0o700)?;
        ensure_directory(&self.snapshot_root.join("metadata"), 0o700)?;
        ensure_directory(&self.snapshot_root.join("transactions"), 0o700)?;
        Ok(())
    }

    fn acquire_lock(&self) -> Result<OperationLock, OperationError> {
        let path = self.snapshot_root.join("operation.lock");
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&path)
            .map_err(|error| io_error("Could not open the operation lock", error))?;
        let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
        if result != 0 {
            let error = io::Error::last_os_error();
            return Err(OperationError::new(
                if error.kind() == io::ErrorKind::WouldBlock {
                    OperationErrorCode::Busy
                } else {
                    OperationErrorCode::Io
                },
                format!("Could not lock the recovery store: {error}"),
            ));
        }
        Ok(OperationLock(file))
    }

    fn deployment_dir(&self, id: DeploymentId) -> PathBuf {
        self.snapshot_root.join("deployments").join(id.to_string())
    }

    fn write_record_atomic(&self, record: &DeploymentRecord) -> Result<(), OperationError> {
        record.validate().map_err(|error| {
            OperationError::new(OperationErrorCode::InvalidIdentity, error.to_string())
        })?;
        let metadata_dir = self.snapshot_root.join("metadata");
        ensure_existing_directory(&metadata_dir)?;
        let target = metadata_dir.join(format!("{}.json", record.id));
        if let Ok(metadata) = fs::symlink_metadata(&target) {
            if !metadata.file_type().is_file() {
                return Err(OperationError::new(
                    OperationErrorCode::UnsafePath,
                    "Deployment metadata target is not a regular file",
                ));
            }
        }
        let temporary = metadata_dir.join(format!(
            ".{}.{}.tmp",
            record.id,
            Uuid::new_v4().hyphenated()
        ));
        let serialized = serde_json::to_vec_pretty(record).map_err(|error| {
            OperationError::new(
                OperationErrorCode::InvalidIdentity,
                format!("Could not serialize deployment metadata: {error}"),
            )
        })?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)
            .map_err(|error| io_error("Could not create temporary deployment metadata", error))?;
        let write_result = (|| {
            file.write_all(&serialized)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            fs::rename(&temporary, &target)?;
            sync_directory(&metadata_dir)?;
            Ok::<(), io::Error>(())
        })();
        if let Err(error) = write_result {
            let _ = fs::remove_file(&temporary);
            return Err(io_error(
                "Could not atomically commit deployment metadata",
                error,
            ));
        }
        Ok(())
    }

    fn remove_record(&self, id: DeploymentId) -> Result<(), OperationError> {
        let metadata_dir = self.snapshot_root.join("metadata");
        let path = metadata_dir.join(format!("{id}.json"));
        match fs::remove_file(&path) {
            Ok(()) => sync_directory(&metadata_dir)
                .map_err(|error| io_error("Could not sync the metadata directory", error)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(io_error("Could not remove deployment metadata", error)),
        }
    }

    fn snapshot_identity(
        &self,
        snapshot: &Path,
    ) -> Result<(String, Option<String>), OperationError> {
        let output = self.run_btrfs(&[
            OsString::from("subvolume"),
            OsString::from("show"),
            OsString::from("--raw"),
            snapshot.as_os_str().to_owned(),
        ])?;
        let text = String::from_utf8(output.stdout).map_err(|_| {
            OperationError::new(
                OperationErrorCode::InvalidIdentity,
                "Btrfs returned a non-UTF-8 subvolume identity",
            )
        })?;
        let uuid = parse_btrfs_uuid(&text, "UUID:")?.ok_or_else(|| {
            OperationError::new(
                OperationErrorCode::InvalidIdentity,
                "Btrfs did not report a snapshot UUID",
            )
        })?;
        let parent = parse_btrfs_uuid(&text, "Parent UUID:")?;
        Ok((uuid, parent))
    }

    fn verify_read_only(&self, snapshot: &Path) -> Result<(), OperationError> {
        let output = self.run_btrfs(&[
            OsString::from("property"),
            OsString::from("get"),
            OsString::from("-ts"),
            snapshot.as_os_str().to_owned(),
            OsString::from("ro"),
        ])?;
        let value = String::from_utf8(output.stdout).map_err(|_| {
            OperationError::new(
                OperationErrorCode::InvalidIdentity,
                "Btrfs returned a non-UTF-8 read-only property",
            )
        })?;
        if value.trim() != "ro=true" {
            return Err(identity_mismatch("read-only snapshot property"));
        }
        Ok(())
    }

    fn sync_btrfs(&self) -> Result<(), OperationError> {
        self.run_btrfs(&[
            OsString::from("filesystem"),
            OsString::from("sync"),
            self.snapshot_root.as_os_str().to_owned(),
        ])?;
        Ok(())
    }

    fn cleanup_snapshot(
        &self,
        snapshot: &Path,
        deployment_dir: &Path,
    ) -> Result<(), OperationError> {
        match fs::symlink_metadata(snapshot) {
            Ok(metadata) if metadata.file_type().is_dir() => {
                self.run_btrfs(&[
                    OsString::from("subvolume"),
                    OsString::from("delete"),
                    OsString::from("--commit-after"),
                    snapshot.as_os_str().to_owned(),
                ])?;
            }
            Ok(_) => {
                return Err(OperationError::new(
                    OperationErrorCode::UnsafePath,
                    "Incomplete snapshot path is not a real directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_error("Could not inspect incomplete snapshot", error)),
        }
        match fs::remove_dir(deployment_dir) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_error("Could not remove incomplete deployment", error)),
        }
        self.sync_btrfs()?;
        Ok(())
    }

    fn run_btrfs(&self, arguments: &[OsString]) -> Result<CommandOutput, OperationError> {
        self.runner.run(Path::new(BTRFS), arguments)
    }
}

use std::os::fd::AsRawFd;

struct OperationLock(File);

impl Drop for OperationLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

fn ensure_supported_layout(layout: &LayoutReport) -> Result<(), OperationError> {
    if layout.support == LayoutSupport::Supported {
        Ok(())
    } else {
        Err(OperationError::new(
            OperationErrorCode::UnsupportedLayout,
            "The complete AnduinOS Btrfs layout is required",
        ))
    }
}

fn validate_transaction_label(value: &str) -> Result<(), OperationError> {
    let parsed = Uuid::parse_str(value).map_err(|_| {
        OperationError::new(
            OperationErrorCode::InvalidInput,
            "Package transaction identifier is not a UUID",
        )
    })?;
    if parsed.hyphenated().to_string() != value {
        return Err(OperationError::new(
            OperationErrorCode::InvalidInput,
            "Package transaction identifier is not canonical",
        ));
    }
    Ok(())
}

fn ensure_existing_directory(path: &Path) -> Result<(), OperationError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| io_error(&format!("Could not inspect {}", path.display()), error))?;
    if metadata.file_type().is_dir() {
        Ok(())
    } else {
        Err(OperationError::new(
            OperationErrorCode::UnsafePath,
            format!("{} is not a real directory", path.display()),
        ))
    }
}

fn ensure_directory(path: &Path, mode: u32) -> Result<(), OperationError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {
            fs::set_permissions(path, fs::Permissions::from_mode(mode)).map_err(|error| {
                io_error(&format!("Could not secure {}", path.display()), error)
            })?;
            Ok(())
        }
        Ok(_) => Err(OperationError::new(
            OperationErrorCode::UnsafePath,
            format!("{} is not a real directory", path.display()),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir(path).map_err(|error| {
                io_error(&format!("Could not create {}", path.display()), error)
            })?;
            fs::set_permissions(path, fs::Permissions::from_mode(mode)).map_err(|error| {
                io_error(&format!("Could not secure {}", path.display()), error)
            })?;
            ensure_existing_directory(path)
        }
        Err(error) => Err(io_error(
            &format!("Could not inspect {}", path.display()),
            error,
        )),
    }
}

fn ensure_new_directory(path: &Path, mode: u32) -> Result<(), OperationError> {
    fs::create_dir(path)
        .map_err(|error| io_error(&format!("Could not create {}", path.display()), error))?;
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
        .map_err(|error| io_error(&format!("Could not secure {}", path.display()), error))?;
    ensure_existing_directory(path)
}

fn read_kernel_release(system_root: &Path) -> Result<String, OperationError> {
    let path = if system_root == Path::new("/") {
        PathBuf::from(KERNEL_RELEASE)
    } else {
        system_root.join("proc/sys/kernel/osrelease")
    };
    let mut file = open_regular_file(&path)?;
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(130)
        .read_to_end(&mut bytes)
        .map_err(|error| io_error("Could not read the kernel release", error))?;
    let release = String::from_utf8(bytes).map_err(|_| {
        OperationError::new(
            OperationErrorCode::InvalidIdentity,
            "Kernel release is not UTF-8",
        )
    })?;
    let release = release.trim().to_string();
    if release.is_empty()
        || release.len() > 128
        || !release
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
    {
        return Err(OperationError::new(
            OperationErrorCode::InvalidIdentity,
            "Kernel release contains unsafe characters",
        ));
    }
    Ok(release)
}

fn hash_optional_regular_file(path: &Path) -> Result<Option<String>, OperationError> {
    match fs::symlink_metadata(path) {
        Ok(_) => hash_regular_file(path).map(Some),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(io_error(
            &format!("Could not inspect {}", path.display()),
            error,
        )),
    }
}

fn hash_regular_file(path: &Path) -> Result<String, OperationError> {
    let mut file = open_regular_file(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| io_error(&format!("Could not read {}", path.display()), error))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn open_regular_file(path: &Path) -> Result<File, OperationError> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| io_error(&format!("Could not open {}", path.display()), error))?;
    let metadata = file
        .metadata()
        .map_err(|error| io_error(&format!("Could not inspect {}", path.display()), error))?;
    if !metadata.file_type().is_file() {
        return Err(OperationError::new(
            OperationErrorCode::UnsafePath,
            format!("{} is not a regular file", path.display()),
        ));
    }
    Ok(file)
}

fn parse_btrfs_uuid(text: &str, field: &str) -> Result<Option<String>, OperationError> {
    let value = text.lines().find_map(|line| {
        let line = line.trim_start();
        line.strip_prefix(field).map(str::trim)
    });
    let Some(value) = value else {
        return Ok(None);
    };
    if value == "-" {
        return Ok(None);
    }
    let uuid = Uuid::parse_str(value).map_err(|_| {
        OperationError::new(
            OperationErrorCode::InvalidIdentity,
            format!("Btrfs reported an invalid {field} value"),
        )
    })?;
    Ok(Some(uuid.hyphenated().to_string()))
}

fn sync_directory(path: &Path) -> io::Result<()> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)?
        .sync_all()
}

fn safe_diagnostic(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let sanitized = text
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .collect::<String>();
    sanitized
        .trim()
        .chars()
        .take(MAX_COMMAND_DIAGNOSTIC)
        .collect()
}

fn truncate_failure(message: String) -> String {
    message.chars().take(MAX_FAILURE_MESSAGE).collect()
}

fn io_error(context: &str, error: io::Error) -> OperationError {
    OperationError::new(OperationErrorCode::Io, format!("{context}: {error}"))
}

fn verify_digest(
    identity: &str,
    expected: Option<&str>,
    actual: &str,
) -> Result<(), OperationError> {
    if expected == Some(actual) {
        Ok(())
    } else {
        Err(identity_mismatch(identity))
    }
}

fn identity_mismatch(identity: &str) -> OperationError {
    OperationError::new(
        OperationErrorCode::InvalidIdentity,
        format!("Recovery point {identity} does not match its recorded identity"),
    )
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};

    use crate::layout::MountReport;

    use super::*;

    const SNAPSHOT_UUID: &str = "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb";
    const PARENT_UUID: &str = "cccccccc-4444-4555-8666-dddddddddddd";

    #[derive(Clone)]
    struct FakeRunner {
        calls: Arc<Mutex<Vec<Vec<String>>>>,
        fail_snapshot: bool,
        fail_identity: bool,
        fail_delete: Arc<AtomicBool>,
    }

    impl FakeRunner {
        fn new(fail_snapshot: bool) -> Self {
            Self {
                calls: Arc::new(Mutex::new(Vec::new())),
                fail_snapshot,
                fail_identity: false,
                fail_delete: Arc::new(AtomicBool::new(false)),
            }
        }

        fn with_identity_failure(mut self) -> Self {
            self.fail_identity = true;
            self
        }
    }

    impl CommandRunner for FakeRunner {
        fn run(
            &self,
            _program: &Path,
            arguments: &[OsString],
        ) -> Result<CommandOutput, OperationError> {
            let arguments = arguments
                .iter()
                .map(|argument| argument.to_string_lossy().to_string())
                .collect::<Vec<_>>();
            self.calls.lock().unwrap().push(arguments.clone());
            if arguments.starts_with(&["subvolume".into(), "snapshot".into()]) {
                if self.fail_snapshot {
                    return Err(OperationError::new(
                        OperationErrorCode::CommandFailed,
                        "injected snapshot failure",
                    ));
                }
                let source = Path::new(&arguments[3]);
                let target = Path::new(&arguments[4]);
                fs::create_dir(target).unwrap();
                for relative in [
                    "boot/initrd.img-test-kernel",
                    "boot/vmlinuz-test-kernel",
                    "var/lib/dpkg/status",
                    MOK_CERTIFICATE,
                ] {
                    let destination = target.join(relative);
                    fs::create_dir_all(destination.parent().unwrap()).unwrap();
                    fs::copy(source.join(relative), destination).unwrap();
                }
            } else if arguments.starts_with(&["subvolume".into(), "show".into()]) {
                if self.fail_identity {
                    return Err(OperationError::new(
                        OperationErrorCode::CommandFailed,
                        "injected identity failure",
                    ));
                }
                return Ok(CommandOutput {
                    stdout: format!("UUID: {SNAPSHOT_UUID}\nParent UUID: {PARENT_UUID}\n")
                        .into_bytes(),
                });
            } else if arguments.starts_with(&["subvolume".into(), "delete".into()]) {
                if self.fail_delete.load(Ordering::Acquire) {
                    return Err(OperationError::new(
                        OperationErrorCode::CommandFailed,
                        "injected delete failure",
                    ));
                }
                fs::remove_dir_all(Path::new(arguments.last().unwrap())).unwrap();
            } else if arguments.starts_with(&["property".into(), "get".into()]) {
                return Ok(CommandOutput {
                    stdout: b"ro=true\n".to_vec(),
                });
            }
            Ok(CommandOutput { stdout: Vec::new() })
        }
    }

    struct TestEnvironment {
        base: PathBuf,
        system_root: PathBuf,
        snapshot_root: PathBuf,
    }

    impl TestEnvironment {
        fn new() -> Self {
            let base = std::env::temp_dir().join(format!("timeback-operation-{}", Uuid::new_v4()));
            let system_root = base.join("system");
            let snapshot_parent = base.join("snapshots");
            let snapshot_root = snapshot_parent.join("anduinos");
            for relative in [
                "boot/initrd.img-test-kernel",
                "boot/vmlinuz-test-kernel",
                "var/lib/dpkg/status",
                MOK_CERTIFICATE,
                "proc/sys/kernel/osrelease",
            ] {
                let path = system_root.join(relative);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(&path, format!("contents:{relative}")).unwrap();
            }
            fs::write(
                system_root.join("proc/sys/kernel/osrelease"),
                "test-kernel\n",
            )
            .unwrap();
            fs::create_dir_all(snapshot_parent).unwrap();
            Self {
                base,
                system_root,
                snapshot_root,
            }
        }

        fn layout(&self) -> LayoutReport {
            LayoutReport {
                support: LayoutSupport::Supported,
                root_filesystem: Some("btrfs".into()),
                root_source: Some("/dev/test".into()),
                issues: Vec::new(),
                mounts: Vec::<MountReport>::new(),
            }
        }
    }

    impl Drop for TestEnvironment {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.base).unwrap();
        }
    }

    #[test]
    fn creates_complete_manual_recovery_point() {
        let environment = TestEnvironment::new();
        let runner = FakeRunner::new(false);
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            runner.clone(),
        );
        let record = engine
            .create_manual(
                &environment.layout(),
                "Before experimenting",
                "Manual recovery point",
                true,
                |_, _, _| {},
            )
            .unwrap();

        assert_eq!(record.state, DeploymentState::Ready);
        assert_eq!(record.snapshot_uuid.as_deref(), Some(SNAPSHOT_UUID));
        assert_eq!(record.snapshot_parent_uuid.as_deref(), Some(PARENT_UUID));
        assert_eq!(record.kernel_release.as_deref(), Some("test-kernel"));
        assert!(record.can_restore());
        assert!(record.pinned);
        assert_eq!(record.initramfs_sha256.as_ref().unwrap().len(), 64);
        let loaded = DeploymentStore::new(&environment.snapshot_root)
            .load_record(record.id)
            .unwrap();
        assert_eq!(loaded, record);
        assert!(runner
            .calls
            .lock()
            .unwrap()
            .iter()
            .any(|call| call.starts_with(&["filesystem".into(), "sync".into()])));
        engine
            .verify(&environment.layout(), record.id, |_, _, _| {})
            .unwrap();
    }

    #[test]
    fn creates_typed_package_recovery_points() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(false),
        );
        let transaction = "11111111-2222-4333-8444-555555555555";
        let pre = engine
            .create_apt_pre(&environment.layout(), transaction, |_, _, _| {})
            .unwrap();
        let post = engine
            .create_apt_post(&environment.layout(), transaction, |_, _, _| {})
            .unwrap();
        assert_eq!(pre.kind, DeploymentKind::AptPre);
        assert_eq!(post.kind, DeploymentKind::AptPost);
        assert!(pre.reason.contains(transaction));
        assert!(post.reason.contains(transaction));
        assert!(pre.can_restore());
        assert!(post.can_restore());
        assert_eq!(
            engine
                .create_apt_pre(&environment.layout(), "not-a-uuid", |_, _, _| {})
                .unwrap_err()
                .code,
            OperationErrorCode::InvalidInput
        );
    }

    #[test]
    fn integrity_verification_detects_snapshot_content_changes() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(false),
        );
        let record = engine
            .create_manual(
                &environment.layout(),
                "Integrity test",
                "Tamper detection",
                false,
                |_, _, _| {},
            )
            .unwrap();
        fs::write(
            environment
                .snapshot_root
                .join("deployments")
                .join(record.id.to_string())
                .join("root/var/lib/dpkg/status"),
            "tampered",
        )
        .unwrap();
        let error = engine
            .verify(&environment.layout(), record.id, |_, _, _| {})
            .unwrap_err();
        assert_eq!(error.code, OperationErrorCode::InvalidIdentity);
        assert!(error.message.contains("dpkg database"));
    }

    #[test]
    fn snapshot_failure_is_recorded_and_cleaned() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(true),
        );
        let error = engine
            .create_manual(
                &environment.layout(),
                "Expected failure",
                "Failure injection",
                false,
                |_, _, _| {},
            )
            .unwrap_err();
        assert_eq!(error.code, OperationErrorCode::CommandFailed);

        let report = DeploymentStore::new(&environment.snapshot_root).discover();
        assert_eq!(report.deployments.len(), 1);
        assert_eq!(report.deployments[0].state, DeploymentState::Incomplete);
        assert!(report.deployments[0].failure.is_some());
        assert!(report.issues.is_empty());
    }

    #[test]
    fn post_snapshot_identity_failure_deletes_the_snapshot() {
        let environment = TestEnvironment::new();
        let runner = FakeRunner::new(false).with_identity_failure();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            runner.clone(),
        );
        engine
            .create_manual(
                &environment.layout(),
                "Expected identity failure",
                "Failure after snapshot creation",
                false,
                |_, _, _| {},
            )
            .unwrap_err();

        let report = DeploymentStore::new(&environment.snapshot_root).discover();
        assert_eq!(report.deployments.len(), 1);
        assert_eq!(report.deployments[0].state, DeploymentState::Incomplete);
        assert!(!environment
            .snapshot_root
            .join("deployments")
            .join(report.deployments[0].id.to_string())
            .exists());
        assert!(runner
            .calls
            .lock()
            .unwrap()
            .iter()
            .any(|call| call.starts_with(&["subvolume".into(), "delete".into()])));
    }

    #[test]
    fn pin_and_delete_use_atomic_metadata_states() {
        let environment = TestEnvironment::new();
        let runner = FakeRunner::new(false);
        let engine =
            OperationEngine::new(&environment.system_root, &environment.snapshot_root, runner);
        let record = engine
            .create_manual(
                &environment.layout(),
                "Disposable",
                "Delete transaction test",
                false,
                |_, _, _| {},
            )
            .unwrap();
        let pinned = engine
            .set_pinned(&environment.layout(), record.id, true)
            .unwrap();
        assert!(pinned.pinned);
        assert_eq!(
            engine
                .delete(&environment.layout(), record.id)
                .unwrap_err()
                .code,
            OperationErrorCode::Protected
        );
        engine
            .set_pinned(&environment.layout(), record.id, false)
            .unwrap();
        engine.delete(&environment.layout(), record.id).unwrap();
        assert!(DeploymentStore::new(&environment.snapshot_root)
            .discover()
            .deployments
            .is_empty());
    }

    #[test]
    fn automatic_delete_preserves_the_restorable_floor_and_manual_points() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(false),
        );
        let automatic = engine
            .create_apt_pre(
                &environment.layout(),
                "11111111-2222-4333-8444-555555555555",
                |_, _, _| {},
            )
            .unwrap();
        assert_eq!(
            engine
                .delete_automatic(&environment.layout(), automatic.id, 1)
                .unwrap_err()
                .code,
            OperationErrorCode::Protected
        );
        let manual = engine
            .create_manual(
                &environment.layout(),
                "Manual known-good point",
                "Automatic deletion boundary",
                false,
                |_, _, _| {},
            )
            .unwrap();
        assert_eq!(
            engine
                .delete_automatic(&environment.layout(), manual.id, 1)
                .unwrap_err()
                .code,
            OperationErrorCode::Protected
        );
        engine
            .delete_automatic(&environment.layout(), automatic.id, 1)
            .unwrap();
    }

    #[test]
    fn pending_package_reference_blocks_even_manual_deletion() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(false),
        );
        let record = engine
            .create_apt_pre(
                &environment.layout(),
                "11111111-2222-4333-8444-555555555555",
                |_, _, _| {},
            )
            .unwrap();
        let store = PackageTransactionStore::new(&environment.snapshot_root);
        let mut transaction = crate::package_transaction::PackageTransaction::new();
        store.create(&transaction).unwrap();
        transaction.record_pre(record.id, Utc::now()).unwrap();
        store.update(&transaction).unwrap();
        assert_eq!(
            engine
                .delete(&environment.layout(), record.id)
                .unwrap_err()
                .code,
            OperationErrorCode::Protected
        );
    }

    #[test]
    fn interrupted_delete_resumes_from_deleting_state() {
        let environment = TestEnvironment::new();
        let runner = FakeRunner::new(false);
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            runner.clone(),
        );
        let record = engine
            .create_manual(
                &environment.layout(),
                "Retry deletion",
                "Power-safe deletion test",
                false,
                |_, _, _| {},
            )
            .unwrap();
        runner.fail_delete.store(true, Ordering::Release);
        assert_eq!(
            engine
                .delete(&environment.layout(), record.id)
                .unwrap_err()
                .code,
            OperationErrorCode::CommandFailed
        );
        assert_eq!(
            DeploymentStore::new(&environment.snapshot_root)
                .load_record(record.id)
                .unwrap()
                .state,
            DeploymentState::Deleting
        );

        runner.fail_delete.store(false, Ordering::Release);
        engine.delete(&environment.layout(), record.id).unwrap();
        assert!(DeploymentStore::new(&environment.snapshot_root)
            .discover()
            .deployments
            .is_empty());
    }

    #[test]
    fn delete_resumes_after_snapshot_was_already_removed() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(false),
        );
        let mut record = engine
            .create_manual(
                &environment.layout(),
                "Power-loss delete",
                "Snapshot deletion committed before metadata cleanup",
                false,
                |_, _, _| {},
            )
            .unwrap();
        record.state = DeploymentState::Deleting;
        engine.write_record_atomic(&record).unwrap();
        fs::remove_dir_all(
            environment
                .snapshot_root
                .join("deployments")
                .join(record.id.to_string())
                .join("root"),
        )
        .unwrap();

        let interrupted = DeploymentStore::new(&environment.snapshot_root).discover();
        assert_eq!(interrupted.deployments, vec![record.clone()]);
        assert!(interrupted.issues.is_empty());

        engine.delete(&environment.layout(), record.id).unwrap();
        let completed = DeploymentStore::new(&environment.snapshot_root).discover();
        assert!(completed.deployments.is_empty());
        assert!(completed.issues.is_empty());
    }

    #[test]
    fn operation_lock_rejects_concurrent_mutation() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(false),
        );
        engine.ensure_store_directories().unwrap();
        let _lock = engine.acquire_lock().unwrap();
        let error = engine
            .create_manual(
                &environment.layout(),
                "Concurrent",
                "Must be rejected",
                false,
                |_, _, _| {},
            )
            .unwrap_err();
        assert_eq!(error.code, OperationErrorCode::Busy);
    }

    #[test]
    fn rejects_mutation_on_nonstandard_layout() {
        let environment = TestEnvironment::new();
        let engine = OperationEngine::new(
            &environment.system_root,
            &environment.snapshot_root,
            FakeRunner::new(false),
        );
        let mut layout = environment.layout();
        layout.support = LayoutSupport::OtherFilesystem;
        let error = engine
            .create_manual(&layout, "No", "Wrong filesystem", false, |_, _, _| {})
            .unwrap_err();
        assert_eq!(error.code, OperationErrorCode::UnsupportedLayout);
        assert!(!environment.snapshot_root.exists());
    }
}
