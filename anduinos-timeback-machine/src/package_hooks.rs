use std::fmt;

use chrono::Utc;

use crate::coordination::TransactionStartLock;
use crate::layout::{self, LayoutReport};
use crate::model::DeploymentRecord;
use crate::operations::OperationEngine;
use crate::package_transaction::{
    PackageTransaction, PackageTransactionError, PackageTransactionPhase, PackageTransactionStore,
};
use crate::transaction::TransactionStore;
use crate::SNAPSHOT_ROOT;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PackageHookOutcome {
    SkippedUnsupportedLayout,
    SkippedRollbackPending,
    NoPendingTransaction,
    PreCaptured,
    PostCaptured,
    InterruptedArchived,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PackageHookErrorCode {
    SnapshotFailed,
    TransactionFailed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PackageHookError {
    pub code: PackageHookErrorCode,
    pub message: String,
}

impl PackageHookError {
    fn new(code: PackageHookErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for PackageHookError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for PackageHookError {}

pub trait PackageHookBackend {
    fn layout(&self) -> LayoutReport;
    fn rollback_pending(&self) -> Result<bool, PackageHookError>;
    fn pending(&self) -> Result<Option<PackageTransaction>, PackageHookError>;
    fn create_transaction(&self, transaction: &PackageTransaction) -> Result<(), PackageHookError>;
    fn update_transaction(&self, transaction: &PackageTransaction) -> Result<(), PackageHookError>;
    fn archive_transaction(&self, transaction: &PackageTransaction)
        -> Result<(), PackageHookError>;
    fn create_pre(
        &self,
        transaction: &PackageTransaction,
    ) -> Result<DeploymentRecord, PackageHookError>;
    fn create_post(
        &self,
        transaction: &PackageTransaction,
    ) -> Result<DeploymentRecord, PackageHookError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemPackageHookBackend;

impl PackageHookBackend for SystemPackageHookBackend {
    fn layout(&self) -> LayoutReport {
        layout::inspect_current()
    }

    fn rollback_pending(&self) -> Result<bool, PackageHookError> {
        TransactionStore::default()
            .load_pending()
            .map(|transaction| transaction.is_some())
            .map_err(|error| transaction_error(error.message))
    }

    fn pending(&self) -> Result<Option<PackageTransaction>, PackageHookError> {
        PackageTransactionStore::default()
            .load_pending()
            .map_err(package_transaction_error)
    }

    fn create_transaction(&self, transaction: &PackageTransaction) -> Result<(), PackageHookError> {
        let _start_lock = TransactionStartLock::acquire(SNAPSHOT_ROOT).map_err(|error| {
            transaction_error(format!("Could not coordinate package transaction: {error}"))
        })?;
        if TransactionStore::default()
            .load_pending()
            .map_err(|error| transaction_error(error.message))?
            .is_some()
        {
            return Err(transaction_error(
                "A system restore claimed the transaction boundary",
            ));
        }
        PackageTransactionStore::default()
            .create(transaction)
            .map_err(package_transaction_error)
    }

    fn update_transaction(&self, transaction: &PackageTransaction) -> Result<(), PackageHookError> {
        PackageTransactionStore::default()
            .update(transaction)
            .map_err(package_transaction_error)
    }

    fn archive_transaction(
        &self,
        transaction: &PackageTransaction,
    ) -> Result<(), PackageHookError> {
        PackageTransactionStore::default()
            .archive(transaction)
            .map_err(package_transaction_error)
    }

    fn create_pre(
        &self,
        transaction: &PackageTransaction,
    ) -> Result<DeploymentRecord, PackageHookError> {
        OperationEngine::default()
            .create_apt_pre(
                &self.layout(),
                &transaction.id.to_string(),
                |_phase, _fraction, _message| {},
            )
            .map_err(|error| {
                PackageHookError::new(PackageHookErrorCode::SnapshotFailed, error.message)
            })
    }

    fn create_post(
        &self,
        transaction: &PackageTransaction,
    ) -> Result<DeploymentRecord, PackageHookError> {
        OperationEngine::default()
            .create_apt_post(
                &self.layout(),
                &transaction.id.to_string(),
                |_phase, _fraction, _message| {},
            )
            .map_err(|error| {
                PackageHookError::new(PackageHookErrorCode::SnapshotFailed, error.message)
            })
    }
}

#[derive(Clone, Debug)]
pub struct PackageHookEngine<B = SystemPackageHookBackend> {
    backend: B,
}

impl Default for PackageHookEngine<SystemPackageHookBackend> {
    fn default() -> Self {
        Self::new(SystemPackageHookBackend)
    }
}

impl<B: PackageHookBackend> PackageHookEngine<B> {
    pub fn new(backend: B) -> Self {
        Self { backend }
    }

    pub fn pre(&self) -> Result<PackageHookOutcome, PackageHookError> {
        if !self.backend.layout().is_supported() {
            return Ok(PackageHookOutcome::SkippedUnsupportedLayout);
        }
        if self.backend.rollback_pending()? {
            return Ok(PackageHookOutcome::SkippedRollbackPending);
        }
        if let Some(mut previous) = self.backend.pending()? {
            match previous.phase {
                PackageTransactionPhase::PreparingPre | PackageTransactionPhase::AwaitingPost => {
                    previous
                        .interrupt(
                            "A newer package operation began before the previous post snapshot",
                            Utc::now(),
                        )
                        .map_err(package_transaction_error)?;
                    self.backend.archive_transaction(&previous)?;
                }
                PackageTransactionPhase::Complete | PackageTransactionPhase::Interrupted => {
                    self.backend.archive_transaction(&previous)?;
                }
            }
        }

        let mut transaction = PackageTransaction::new();
        self.backend.create_transaction(&transaction)?;
        match self.backend.create_pre(&transaction) {
            Ok(deployment) => {
                transaction
                    .record_pre(deployment.id, Utc::now())
                    .map_err(package_transaction_error)?;
                self.backend.update_transaction(&transaction)?;
                Ok(PackageHookOutcome::PreCaptured)
            }
            Err(error) => {
                self.archive_failure(&mut transaction, &error.to_string())?;
                Err(error)
            }
        }
    }

    pub fn post(&self) -> Result<PackageHookOutcome, PackageHookError> {
        if !self.backend.layout().is_supported() {
            return Ok(PackageHookOutcome::SkippedUnsupportedLayout);
        }
        let Some(mut transaction) = self.backend.pending()? else {
            return Ok(PackageHookOutcome::NoPendingTransaction);
        };
        match transaction.phase {
            PackageTransactionPhase::PreparingPre => {
                transaction
                    .interrupt("The package pre snapshot did not complete", Utc::now())
                    .map_err(package_transaction_error)?;
                self.backend.archive_transaction(&transaction)?;
                Ok(PackageHookOutcome::InterruptedArchived)
            }
            PackageTransactionPhase::AwaitingPost => match self.backend.create_post(&transaction) {
                Ok(deployment) => {
                    transaction
                        .record_post(deployment.id, Utc::now())
                        .map_err(package_transaction_error)?;
                    self.backend.archive_transaction(&transaction)?;
                    Ok(PackageHookOutcome::PostCaptured)
                }
                Err(error) => {
                    self.archive_failure(&mut transaction, &error.to_string())?;
                    Err(error)
                }
            },
            PackageTransactionPhase::Complete | PackageTransactionPhase::Interrupted => {
                self.backend.archive_transaction(&transaction)?;
                Ok(PackageHookOutcome::InterruptedArchived)
            }
        }
    }

    fn archive_failure(
        &self,
        transaction: &mut PackageTransaction,
        message: &str,
    ) -> Result<(), PackageHookError> {
        transaction
            .interrupt(message, Utc::now())
            .map_err(package_transaction_error)?;
        self.backend.archive_transaction(transaction)
    }
}

fn package_transaction_error(error: PackageTransactionError) -> PackageHookError {
    transaction_error(error.message)
}

fn transaction_error(message: impl Into<String>) -> PackageHookError {
    PackageHookError::new(PackageHookErrorCode::TransactionFailed, message)
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::layout::LayoutSupport;
    use crate::model::{DeploymentId, DeploymentKind, DeploymentState};
    use crate::DEPLOYMENT_SCHEMA_VERSION;

    #[derive(Clone)]
    struct FakeBackend(Arc<Mutex<FakeState>>);

    struct FakeState {
        supported: bool,
        rollback: bool,
        pending: Option<PackageTransaction>,
        history: Vec<PackageTransaction>,
        kinds: Vec<DeploymentKind>,
        fail_pre: bool,
    }

    impl FakeBackend {
        fn new() -> Self {
            Self(Arc::new(Mutex::new(FakeState {
                supported: true,
                rollback: false,
                pending: None,
                history: Vec::new(),
                kinds: Vec::new(),
                fail_pre: false,
            })))
        }
    }

    impl PackageHookBackend for FakeBackend {
        fn layout(&self) -> LayoutReport {
            let supported = self.0.lock().unwrap().supported;
            LayoutReport {
                support: if supported {
                    LayoutSupport::Supported
                } else {
                    LayoutSupport::OtherFilesystem
                },
                root_filesystem: Some(if supported { "btrfs" } else { "ext4" }.into()),
                root_source: Some("/dev/test".into()),
                issues: Vec::new(),
                mounts: Vec::new(),
            }
        }

        fn rollback_pending(&self) -> Result<bool, PackageHookError> {
            Ok(self.0.lock().unwrap().rollback)
        }

        fn pending(&self) -> Result<Option<PackageTransaction>, PackageHookError> {
            Ok(self.0.lock().unwrap().pending.clone())
        }

        fn create_transaction(
            &self,
            transaction: &PackageTransaction,
        ) -> Result<(), PackageHookError> {
            self.0.lock().unwrap().pending = Some(transaction.clone());
            Ok(())
        }

        fn update_transaction(
            &self,
            transaction: &PackageTransaction,
        ) -> Result<(), PackageHookError> {
            self.0.lock().unwrap().pending = Some(transaction.clone());
            Ok(())
        }

        fn archive_transaction(
            &self,
            transaction: &PackageTransaction,
        ) -> Result<(), PackageHookError> {
            let mut state = self.0.lock().unwrap();
            state.history.push(transaction.clone());
            state.pending = None;
            Ok(())
        }

        fn create_pre(
            &self,
            _transaction: &PackageTransaction,
        ) -> Result<DeploymentRecord, PackageHookError> {
            let mut state = self.0.lock().unwrap();
            if state.fail_pre {
                return Err(PackageHookError::new(
                    PackageHookErrorCode::SnapshotFailed,
                    "injected pre failure",
                ));
            }
            state.kinds.push(DeploymentKind::AptPre);
            Ok(record(DeploymentKind::AptPre))
        }

        fn create_post(
            &self,
            _transaction: &PackageTransaction,
        ) -> Result<DeploymentRecord, PackageHookError> {
            self.0.lock().unwrap().kinds.push(DeploymentKind::AptPost);
            Ok(record(DeploymentKind::AptPost))
        }
    }

    fn record(kind: DeploymentKind) -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind,
            state: DeploymentState::Ready,
            created_at: Utc::now(),
            title: "Package recovery point".into(),
            reason: "Package hook test".into(),
            snapshot_uuid: Some("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb".into()),
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
    fn pre_and_post_create_one_archived_pair() {
        let backend = FakeBackend::new();
        let engine = PackageHookEngine::new(backend.clone());
        assert_eq!(engine.pre().unwrap(), PackageHookOutcome::PreCaptured);
        assert_eq!(engine.post().unwrap(), PackageHookOutcome::PostCaptured);
        let state = backend.0.lock().unwrap();
        assert!(state.pending.is_none());
        assert_eq!(
            state.kinds,
            [DeploymentKind::AptPre, DeploymentKind::AptPost]
        );
        assert_eq!(state.history[0].phase, PackageTransactionPhase::Complete);
        assert!(state.history[0].pre_deployment_id.is_some());
        assert!(state.history[0].post_deployment_id.is_some());
    }

    #[test]
    fn newer_pre_archives_interrupted_previous_operation() {
        let backend = FakeBackend::new();
        let engine = PackageHookEngine::new(backend.clone());
        engine.pre().unwrap();
        engine.pre().unwrap();
        let state = backend.0.lock().unwrap();
        assert_eq!(state.history[0].phase, PackageTransactionPhase::Interrupted);
        assert_eq!(
            state.pending.as_ref().unwrap().phase,
            PackageTransactionPhase::AwaitingPost
        );
    }

    #[test]
    fn unsupported_layout_and_pending_rollback_are_noops() {
        let backend = FakeBackend::new();
        backend.0.lock().unwrap().supported = false;
        assert_eq!(
            PackageHookEngine::new(backend.clone()).pre().unwrap(),
            PackageHookOutcome::SkippedUnsupportedLayout
        );
        backend.0.lock().unwrap().supported = true;
        backend.0.lock().unwrap().rollback = true;
        assert_eq!(
            PackageHookEngine::new(backend.clone()).pre().unwrap(),
            PackageHookOutcome::SkippedRollbackPending
        );
        assert!(backend.0.lock().unwrap().pending.is_none());
    }

    #[test]
    fn snapshot_failure_is_archived_for_diagnostics() {
        let backend = FakeBackend::new();
        backend.0.lock().unwrap().fail_pre = true;
        assert_eq!(
            PackageHookEngine::new(backend.clone())
                .pre()
                .unwrap_err()
                .code,
            PackageHookErrorCode::SnapshotFailed
        );
        let state = backend.0.lock().unwrap();
        assert!(state.pending.is_none());
        assert_eq!(state.history[0].phase, PackageTransactionPhase::Interrupted);
    }

    #[test]
    fn post_without_pre_is_a_noop() {
        assert_eq!(
            PackageHookEngine::new(FakeBackend::new()).post().unwrap(),
            PackageHookOutcome::NoPendingTransaction
        );
    }
}
