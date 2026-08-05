use std::fmt;

use chrono::Utc;

use crate::RECOVERY_STORE_ROOT;
use crate::coordination::TransactionStartLock;
use crate::layout;
use crate::model::DeploymentId;
use crate::operations::OperationEngine;
use crate::package_transaction::{
    PackageTransaction, PackageTransactionPhase, PackageTransactionStore,
};
use crate::transaction::TransactionStore;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PackageHookError(pub String);

impl fmt::Display for PackageHookError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

impl std::error::Error for PackageHookError {}

pub trait PackageHookBackend {
    fn begin_transaction(&self) -> Result<PackageTransaction, PackageHookError>;
    fn pending_transaction(&self) -> Result<Option<PackageTransaction>, PackageHookError>;
    fn create_pre(&self, transaction_id: &str) -> Result<DeploymentId, PackageHookError>;
    fn create_post(&self, transaction_id: &str) -> Result<DeploymentId, PackageHookError>;
    fn update_transaction(&self, transaction: &PackageTransaction) -> Result<(), PackageHookError>;
    fn archive_transaction(&self, transaction: &PackageTransaction)
    -> Result<(), PackageHookError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemPackageHookBackend;

impl PackageHookBackend for SystemPackageHookBackend {
    fn begin_transaction(&self) -> Result<PackageTransaction, PackageHookError> {
        if !layout::inspect_current().is_supported() {
            return Err(PackageHookError(
                "The complete AnduinOS Btrfs layout is unavailable".into(),
            ));
        }
        let _lock = TransactionStartLock::acquire(RECOVERY_STORE_ROOT)
            .map_err(|error| PackageHookError(error.to_string()))?;
        if TransactionStore::default()
            .load_pending()
            .map_err(|error| PackageHookError(error.message))?
            .is_some()
        {
            return Err(PackageHookError(
                "A system restore transaction already owns the recovery boundary".into(),
            ));
        }

        let store = PackageTransactionStore::default();
        if let Some(mut stale) = store
            .load_pending()
            .map_err(|error| PackageHookError(error.message))?
        {
            if !matches!(
                stale.phase,
                PackageTransactionPhase::Complete | PackageTransactionPhase::Interrupted
            ) {
                stale
                    .interrupt("Superseded by a later APT transaction", Utc::now())
                    .map_err(|error| PackageHookError(error.message))?;
            }
            store
                .archive(&stale)
                .map_err(|error| PackageHookError(error.message))?;
        }

        let transaction = PackageTransaction::new();
        store
            .create(&transaction)
            .map_err(|error| PackageHookError(error.message))?;
        Ok(transaction)
    }

    fn pending_transaction(&self) -> Result<Option<PackageTransaction>, PackageHookError> {
        PackageTransactionStore::default()
            .load_pending()
            .map_err(|error| PackageHookError(error.message))
    }

    fn create_pre(&self, transaction_id: &str) -> Result<DeploymentId, PackageHookError> {
        OperationEngine::default()
            .create_apt_pre(
                &layout::inspect_current(),
                transaction_id,
                |_phase, _fraction, _message| {},
            )
            .map(|record| record.id)
            .map_err(|error| PackageHookError(error.message))
    }

    fn create_post(&self, transaction_id: &str) -> Result<DeploymentId, PackageHookError> {
        OperationEngine::default()
            .create_apt_post(
                &layout::inspect_current(),
                transaction_id,
                |_phase, _fraction, _message| {},
            )
            .map(|record| record.id)
            .map_err(|error| PackageHookError(error.message))
    }

    fn update_transaction(&self, transaction: &PackageTransaction) -> Result<(), PackageHookError> {
        PackageTransactionStore::default()
            .update(transaction)
            .map_err(|error| PackageHookError(error.message))
    }

    fn archive_transaction(
        &self,
        transaction: &PackageTransaction,
    ) -> Result<(), PackageHookError> {
        PackageTransactionStore::default()
            .archive(transaction)
            .map_err(|error| PackageHookError(error.message))
    }
}

#[derive(Clone, Debug)]
pub struct PackageHookCoordinator<B = SystemPackageHookBackend> {
    backend: B,
}

impl Default for PackageHookCoordinator<SystemPackageHookBackend> {
    fn default() -> Self {
        Self::new(SystemPackageHookBackend)
    }
}

impl<B: PackageHookBackend> PackageHookCoordinator<B> {
    pub fn new(backend: B) -> Self {
        Self { backend }
    }

    pub fn before_packages(&self) -> Result<PackageTransaction, PackageHookError> {
        let mut transaction = self.backend.begin_transaction()?;
        match self.backend.create_pre(&transaction.id.to_string()) {
            Ok(deployment) => {
                transaction
                    .record_pre(deployment, Utc::now())
                    .map_err(|error| PackageHookError(error.message))?;
                self.backend.update_transaction(&transaction)?;
                Ok(transaction)
            }
            Err(error) => {
                self.interrupt_and_archive(&mut transaction, &error.to_string())?;
                Err(error)
            }
        }
    }

    pub fn after_packages(&self) -> Result<PackageTransaction, PackageHookError> {
        let mut transaction = self
            .backend
            .pending_transaction()?
            .ok_or_else(|| PackageHookError("No APT recovery transaction is pending".into()))?;
        if transaction.phase != PackageTransactionPhase::AwaitingPost {
            let error = PackageHookError(
                "The APT recovery transaction did not complete its pre-change point".into(),
            );
            if !matches!(
                transaction.phase,
                PackageTransactionPhase::Complete | PackageTransactionPhase::Interrupted
            ) {
                self.interrupt_and_archive(&mut transaction, &error.to_string())?;
            }
            return Err(error);
        }
        match self.backend.create_post(&transaction.id.to_string()) {
            Ok(deployment) => {
                transaction
                    .record_post(deployment, Utc::now())
                    .map_err(|error| PackageHookError(error.message))?;
                self.backend.archive_transaction(&transaction)?;
                Ok(transaction)
            }
            Err(error) => {
                self.interrupt_and_archive(&mut transaction, &error.to_string())?;
                Err(error)
            }
        }
    }

    fn interrupt_and_archive(
        &self,
        transaction: &mut PackageTransaction,
        failure: &str,
    ) -> Result<(), PackageHookError> {
        transaction
            .interrupt(failure, Utc::now())
            .map_err(|error| PackageHookError(error.message))?;
        self.backend.archive_transaction(transaction)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use super::*;

    #[derive(Clone, Default)]
    struct FakeBackend {
        pending: Arc<Mutex<Option<PackageTransaction>>>,
        history: Arc<Mutex<Vec<PackageTransaction>>>,
        fail_pre: bool,
        fail_post: bool,
    }

    impl PackageHookBackend for FakeBackend {
        fn begin_transaction(&self) -> Result<PackageTransaction, PackageHookError> {
            let transaction = PackageTransaction::new();
            *self.pending.lock().unwrap() = Some(transaction.clone());
            Ok(transaction)
        }

        fn pending_transaction(&self) -> Result<Option<PackageTransaction>, PackageHookError> {
            Ok(self.pending.lock().unwrap().clone())
        }

        fn create_pre(&self, _transaction_id: &str) -> Result<DeploymentId, PackageHookError> {
            if self.fail_pre {
                Err(PackageHookError("pre failed".into()))
            } else {
                Ok(DeploymentId::new())
            }
        }

        fn create_post(&self, _transaction_id: &str) -> Result<DeploymentId, PackageHookError> {
            if self.fail_post {
                Err(PackageHookError("post failed".into()))
            } else {
                Ok(DeploymentId::new())
            }
        }

        fn update_transaction(
            &self,
            transaction: &PackageTransaction,
        ) -> Result<(), PackageHookError> {
            *self.pending.lock().unwrap() = Some(transaction.clone());
            Ok(())
        }

        fn archive_transaction(
            &self,
            transaction: &PackageTransaction,
        ) -> Result<(), PackageHookError> {
            *self.pending.lock().unwrap() = None;
            self.history.lock().unwrap().push(transaction.clone());
            Ok(())
        }
    }

    #[test]
    fn pre_and_post_form_one_complete_transaction() {
        let backend = FakeBackend::default();
        let coordinator = PackageHookCoordinator::new(backend.clone());
        let pre = coordinator.before_packages().unwrap();
        assert_eq!(pre.phase, PackageTransactionPhase::AwaitingPost);
        let post = coordinator.after_packages().unwrap();
        assert_eq!(post.id, pre.id);
        assert_eq!(post.phase, PackageTransactionPhase::Complete);
        assert!(backend.pending.lock().unwrap().is_none());
        assert_eq!(backend.history.lock().unwrap().len(), 1);
    }

    #[test]
    fn snapshot_failure_is_archived_and_never_blocks_apt() {
        let backend = FakeBackend {
            fail_pre: true,
            ..Default::default()
        };
        let coordinator = PackageHookCoordinator::new(backend.clone());
        assert!(coordinator.before_packages().is_err());
        assert!(backend.pending.lock().unwrap().is_none());
        assert_eq!(
            backend.history.lock().unwrap()[0].phase,
            PackageTransactionPhase::Interrupted
        );
    }
}
