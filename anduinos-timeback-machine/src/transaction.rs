use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::model::DeploymentId;

pub const ROLLBACK_SCHEMA_VERSION: u32 = 1;
pub const MAX_APPLY_ATTEMPTS: u32 = 3;
const MAX_TRANSACTION_BYTES: u64 = 1024 * 1024;
const MAX_FAILURE_LENGTH: usize = 2000;
const PENDING_TRANSACTION: &str = "pending-rollback.json";

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct RollbackId(Uuid);

impl RollbackId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for RollbackId {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Display for RollbackId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.hyphenated().fmt(formatter)
    }
}

impl FromStr for RollbackId {
    type Err = uuid::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Uuid::parse_str(value).map(Self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RollbackPhase {
    Preparing,
    Armed,
    Applying,
    BootedUnconfirmed,
    Reverting,
    Reverted,
    Confirmed,
    Failed,
}

impl RollbackPhase {
    pub fn can_transition_to(self, next: Self) -> bool {
        use RollbackPhase::*;
        matches!(
            (self, next),
            (Preparing, Armed | Failed)
                | (Armed, Applying | Failed)
                | (Applying, BootedUnconfirmed | Reverting | Failed)
                | (BootedUnconfirmed, Confirmed | Reverting)
                | (Reverting, Reverted | Failed)
        )
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Reverted | Self::Confirmed | Self::Failed)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RollbackTransaction {
    pub schema_version: u32,
    pub id: RollbackId,
    pub target_deployment_id: DeploymentId,
    pub fallback_deployment_id: DeploymentId,
    pub phase: RollbackPhase,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub apply_attempts: u32,
    pub applying_boot_id: Option<String>,
    pub root_filesystem_uuid: String,
    pub kernel_release: String,
    pub grub_entry_id: String,
    pub failure: Option<String>,
}

impl RollbackTransaction {
    pub fn new(
        target_deployment_id: DeploymentId,
        fallback_deployment_id: DeploymentId,
        root_filesystem_uuid: impl Into<String>,
        kernel_release: impl Into<String>,
    ) -> Self {
        let id = RollbackId::new();
        let now = Utc::now();
        Self {
            schema_version: ROLLBACK_SCHEMA_VERSION,
            id,
            target_deployment_id,
            fallback_deployment_id,
            phase: RollbackPhase::Preparing,
            created_at: now,
            updated_at: now,
            apply_attempts: 0,
            applying_boot_id: None,
            root_filesystem_uuid: root_filesystem_uuid.into(),
            kernel_release: kernel_release.into(),
            grub_entry_id: format!("anduinos-timeback-{id}"),
            failure: None,
        }
    }

    pub fn transition(
        &mut self,
        next: RollbackPhase,
        now: DateTime<Utc>,
    ) -> Result<(), TransactionError> {
        if !self.phase.can_transition_to(next) {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidTransition,
                format!(
                    "Rollback transaction cannot transition from {:?} to {next:?}",
                    self.phase
                ),
            ));
        }
        self.phase = next;
        self.updated_at = now;
        self.validate()
    }

    pub fn begin_apply(
        &mut self,
        boot_id: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<(), TransactionError> {
        if self.phase != RollbackPhase::Armed && self.phase != RollbackPhase::Applying {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidTransition,
                "Only an armed or interrupted applying transaction can begin applying",
            ));
        }
        if self.apply_attempts >= MAX_APPLY_ATTEMPTS {
            return Err(TransactionError::new(
                TransactionErrorCode::AttemptLimit,
                "Rollback apply attempt limit has been reached",
            ));
        }
        self.phase = RollbackPhase::Applying;
        self.apply_attempts += 1;
        self.applying_boot_id = Some(boot_id.into());
        self.updated_at = now;
        self.validate()
    }

    pub fn record_failure(
        &mut self,
        failure: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<(), TransactionError> {
        if self.phase.is_terminal() {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidTransition,
                "A terminal rollback transaction cannot record another failure",
            ));
        }
        self.phase = RollbackPhase::Failed;
        self.failure = Some(
            failure
                .into()
                .chars()
                .map(|character| {
                    if character.is_control() {
                        ' '
                    } else {
                        character
                    }
                })
                .take(MAX_FAILURE_LENGTH)
                .collect(),
        );
        self.updated_at = now;
        self.validate()
    }

    pub fn old_root_name(&self) -> String {
        format!("@root.timeback-old-{}", self.id)
    }

    pub fn new_root_name(&self) -> String {
        format!("@root.timeback-new-{}", self.id)
    }

    pub fn validate(&self) -> Result<(), TransactionError> {
        if self.schema_version != ROLLBACK_SCHEMA_VERSION {
            return Err(TransactionError::new(
                TransactionErrorCode::UnsupportedSchema,
                format!(
                    "Unsupported rollback transaction schema {}",
                    self.schema_version
                ),
            ));
        }
        if self.target_deployment_id == self.fallback_deployment_id {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "Rollback target and fallback must be different deployments",
            ));
        }
        validate_uuid(&self.root_filesystem_uuid, "root filesystem UUID")?;
        validate_kernel_release(&self.kernel_release)?;
        if self.grub_entry_id != format!("anduinos-timeback-{}", self.id) {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "GRUB entry ID does not match the rollback transaction",
            ));
        }
        if self.updated_at < self.created_at {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "Rollback transaction timestamps are out of order",
            ));
        }
        if self.apply_attempts > MAX_APPLY_ATTEMPTS {
            return Err(TransactionError::new(
                TransactionErrorCode::AttemptLimit,
                "Rollback transaction exceeds the apply attempt limit",
            ));
        }
        if matches!(self.phase, RollbackPhase::Preparing | RollbackPhase::Armed)
            && self.apply_attempts != 0
        {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "A rollback transaction cannot have apply attempts before applying",
            ));
        }
        if matches!(
            self.phase,
            RollbackPhase::Applying
                | RollbackPhase::BootedUnconfirmed
                | RollbackPhase::Reverting
                | RollbackPhase::Reverted
                | RollbackPhase::Confirmed
        ) && self.apply_attempts == 0
        {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "An applied rollback transaction must record at least one attempt",
            ));
        }
        if self.apply_attempts > 0 && self.applying_boot_id.is_none() {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "An attempted rollback must record its initramfs boot ID",
            ));
        }
        if let Some(boot_id) = &self.applying_boot_id {
            validate_uuid(boot_id, "initramfs boot ID")?;
        }
        if self.phase == RollbackPhase::Failed && self.failure.is_none() {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "A failed rollback transaction must contain a diagnostic",
            ));
        }
        if self.phase != RollbackPhase::Failed && self.failure.is_some() {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "Only a failed rollback transaction may contain a diagnostic",
            ));
        }
        if self.failure.as_deref().is_some_and(|failure| {
            failure.is_empty()
                || failure.chars().count() > MAX_FAILURE_LENGTH
                || failure.chars().any(char::is_control)
        }) {
            return Err(TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                "Rollback failure diagnostic is invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransactionErrorCode {
    AlreadyPending,
    NotFound,
    UnsafePath,
    TooLarge,
    InvalidJson,
    UnsupportedSchema,
    InvalidRecord,
    InvalidTransition,
    AttemptLimit,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TransactionError {
    pub code: TransactionErrorCode,
    pub message: String,
}

impl TransactionError {
    fn new(code: TransactionErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for TransactionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for TransactionError {}

#[derive(Clone, Debug)]
pub struct TransactionStore {
    transactions_dir: PathBuf,
}

impl TransactionStore {
    pub fn new(snapshot_root: impl AsRef<Path>) -> Self {
        Self {
            transactions_dir: snapshot_root.as_ref().join("transactions"),
        }
    }

    pub fn load_pending(&self) -> Result<Option<RollbackTransaction>, TransactionError> {
        let path = self.pending_path();
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(io_error("Could not inspect rollback transaction", error)),
        };
        if !metadata.file_type().is_file() {
            return Err(TransactionError::new(
                TransactionErrorCode::UnsafePath,
                "Pending rollback transaction is not a regular file",
            ));
        }
        if metadata.len() > MAX_TRANSACTION_BYTES {
            return Err(TransactionError::new(
                TransactionErrorCode::TooLarge,
                "Pending rollback transaction exceeds the safety limit",
            ));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&path)
            .map_err(|error| io_error("Could not open rollback transaction", error))?;
        let mut bytes = Vec::with_capacity(metadata.len() as usize);
        Read::by_ref(&mut file)
            .take(MAX_TRANSACTION_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| io_error("Could not read rollback transaction", error))?;
        if bytes.len() as u64 > MAX_TRANSACTION_BYTES {
            return Err(TransactionError::new(
                TransactionErrorCode::TooLarge,
                "Pending rollback transaction exceeds the safety limit",
            ));
        }
        let transaction =
            serde_json::from_slice::<RollbackTransaction>(&bytes).map_err(|error| {
                TransactionError::new(
                    TransactionErrorCode::InvalidJson,
                    format!("Pending rollback transaction is invalid JSON: {error}"),
                )
            })?;
        transaction.validate()?;
        Ok(Some(transaction))
    }

    pub fn create(&self, transaction: &RollbackTransaction) -> Result<(), TransactionError> {
        transaction.validate()?;
        ensure_real_directory(&self.transactions_dir)?;
        match fs::symlink_metadata(self.pending_path()) {
            Ok(_) => {
                return Err(TransactionError::new(
                    TransactionErrorCode::AlreadyPending,
                    "Another rollback transaction is already pending",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_error("Could not inspect rollback transaction", error)),
        }
        self.write_atomic(transaction, false)
    }

    pub fn update(&self, transaction: &RollbackTransaction) -> Result<(), TransactionError> {
        transaction.validate()?;
        ensure_real_directory(&self.transactions_dir)?;
        let metadata = fs::symlink_metadata(self.pending_path()).map_err(|error| {
            if error.kind() == io::ErrorKind::NotFound {
                TransactionError::new(
                    TransactionErrorCode::NotFound,
                    "No rollback transaction is pending",
                )
            } else {
                io_error("Could not inspect rollback transaction", error)
            }
        })?;
        if !metadata.file_type().is_file() {
            return Err(TransactionError::new(
                TransactionErrorCode::UnsafePath,
                "Pending rollback transaction is not a regular file",
            ));
        }
        self.write_atomic(transaction, true)
    }

    pub fn remove(&self) -> Result<(), TransactionError> {
        let path = self.pending_path();
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(io_error("Could not inspect rollback transaction", error)),
        };
        if !metadata.file_type().is_file() {
            return Err(TransactionError::new(
                TransactionErrorCode::UnsafePath,
                "Pending rollback transaction is not a regular file",
            ));
        }
        match fs::remove_file(path) {
            Ok(()) => sync_directory(&self.transactions_dir)
                .map_err(|error| io_error("Could not sync rollback transactions", error)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(io_error("Could not remove rollback transaction", error)),
        }
    }

    fn pending_path(&self) -> PathBuf {
        self.transactions_dir.join(PENDING_TRANSACTION)
    }

    fn write_atomic(
        &self,
        transaction: &RollbackTransaction,
        replace: bool,
    ) -> Result<(), TransactionError> {
        let target = self.pending_path();
        let temporary = self.transactions_dir.join(format!(
            ".pending-rollback.{}.tmp",
            Uuid::new_v4().hyphenated()
        ));
        let serialized = serde_json::to_vec_pretty(transaction).map_err(|error| {
            TransactionError::new(
                TransactionErrorCode::InvalidRecord,
                format!("Could not serialize rollback transaction: {error}"),
            )
        })?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)
            .map_err(|error| io_error("Could not create rollback transaction", error))?;
        let result = (|| {
            file.write_all(&serialized)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            if replace {
                fs::rename(&temporary, &target)?;
            } else {
                fs::hard_link(&temporary, &target)?;
                let _ = fs::remove_file(&temporary);
            }
            sync_directory(&self.transactions_dir)
        })();
        if let Err(error) = result {
            let _ = fs::remove_file(&temporary);
            return Err(TransactionError::new(
                if error.kind() == io::ErrorKind::AlreadyExists {
                    TransactionErrorCode::AlreadyPending
                } else {
                    TransactionErrorCode::Io
                },
                format!("Could not atomically commit rollback transaction: {error}"),
            ));
        }
        Ok(())
    }
}

fn validate_uuid(value: &str, name: &str) -> Result<(), TransactionError> {
    let parsed = Uuid::parse_str(value).map_err(|_| {
        TransactionError::new(
            TransactionErrorCode::InvalidRecord,
            format!("Rollback {name} is not a UUID"),
        )
    })?;
    if parsed.hyphenated().to_string() != value {
        return Err(TransactionError::new(
            TransactionErrorCode::InvalidRecord,
            format!("Rollback {name} is not in canonical form"),
        ));
    }
    Ok(())
}

fn validate_kernel_release(value: &str) -> Result<(), TransactionError> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
    {
        return Err(TransactionError::new(
            TransactionErrorCode::InvalidRecord,
            "Rollback kernel release contains unsafe characters",
        ));
    }
    Ok(())
}

fn ensure_real_directory(path: &Path) -> Result<(), TransactionError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| io_error("Could not inspect rollback transaction directory", error))?;
    if metadata.file_type().is_dir() {
        Ok(())
    } else {
        Err(TransactionError::new(
            TransactionErrorCode::UnsafePath,
            "Rollback transaction directory is not a real directory",
        ))
    }
}

fn sync_directory(path: &Path) -> io::Result<()> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)?
        .sync_all()
}

fn io_error(context: &str, error: io::Error) -> TransactionError {
    TransactionError::new(TransactionErrorCode::Io, format!("{context}: {error}"))
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::symlink;

    use super::*;

    struct TestStore {
        root: PathBuf,
    }

    impl TestStore {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "timeback-transaction-{}",
                Uuid::new_v4().hyphenated()
            ));
            fs::create_dir_all(root.join("transactions")).unwrap();
            Self { root }
        }

        fn store(&self) -> TransactionStore {
            TransactionStore::new(&self.root)
        }
    }

    impl Drop for TestStore {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }

    fn transaction() -> RollbackTransaction {
        RollbackTransaction::new(
            DeploymentId::new(),
            DeploymentId::new(),
            "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb",
            "7.0.0-28-generic",
        )
    }

    #[test]
    fn transaction_names_are_deterministic_and_path_safe() {
        let transaction = transaction();
        transaction.validate().unwrap();
        assert_eq!(
            transaction.grub_entry_id,
            format!("anduinos-timeback-{}", transaction.id)
        );
        assert_eq!(
            transaction.old_root_name(),
            format!("@root.timeback-old-{}", transaction.id)
        );
        assert_eq!(
            transaction.new_root_name(),
            format!("@root.timeback-new-{}", transaction.id)
        );
    }

    #[test]
    fn state_machine_rejects_skipped_and_terminal_transitions() {
        let mut transaction = transaction();
        assert!(transaction
            .transition(RollbackPhase::Applying, Utc::now())
            .is_err());
        transaction
            .transition(RollbackPhase::Armed, Utc::now())
            .unwrap();
        transaction
            .begin_apply("bbbbbbbb-1111-4222-8333-cccccccccccc", Utc::now())
            .unwrap();
        transaction
            .transition(RollbackPhase::BootedUnconfirmed, Utc::now())
            .unwrap();
        transaction
            .transition(RollbackPhase::Confirmed, Utc::now())
            .unwrap();
        assert!(transaction
            .transition(RollbackPhase::Reverting, Utc::now())
            .is_err());
    }

    #[test]
    fn apply_attempts_are_strictly_bounded() {
        let mut transaction = transaction();
        transaction
            .transition(RollbackPhase::Armed, Utc::now())
            .unwrap();
        for attempt in 0..MAX_APPLY_ATTEMPTS {
            transaction
                .begin_apply(format!("00000000-0000-4000-8000-{attempt:012}"), Utc::now())
                .unwrap();
        }
        assert_eq!(
            transaction
                .begin_apply("dddddddd-1111-4222-8333-eeeeeeeeeeee", Utc::now())
                .unwrap_err()
                .code,
            TransactionErrorCode::AttemptLimit
        );
    }

    #[test]
    fn pending_transaction_is_created_and_updated_atomically() {
        let environment = TestStore::new();
        let store = environment.store();
        let mut transaction = transaction();
        store.create(&transaction).unwrap();
        assert_eq!(store.load_pending().unwrap(), Some(transaction.clone()));
        assert_eq!(
            store.create(&transaction).unwrap_err().code,
            TransactionErrorCode::AlreadyPending
        );
        transaction
            .transition(RollbackPhase::Armed, Utc::now())
            .unwrap();
        store.update(&transaction).unwrap();
        assert_eq!(store.load_pending().unwrap(), Some(transaction));
        store.remove().unwrap();
        assert_eq!(store.load_pending().unwrap(), None);
        store.remove().unwrap();
    }

    #[test]
    fn transaction_symlink_is_never_followed() {
        let environment = TestStore::new();
        let outside = environment.root.join("outside");
        fs::write(&outside, "do not touch").unwrap();
        symlink(
            &outside,
            environment.root.join("transactions/pending-rollback.json"),
        )
        .unwrap();
        let error = environment.store().load_pending().unwrap_err();
        assert_eq!(error.code, TransactionErrorCode::UnsafePath);
        let error = environment.store().remove().unwrap_err();
        assert_eq!(error.code, TransactionErrorCode::UnsafePath);
        assert_eq!(fs::read_to_string(outside).unwrap(), "do not touch");
    }

    #[test]
    fn invalid_cross_references_and_diagnostics_are_rejected() {
        let mut invalid_reference = transaction();
        invalid_reference.fallback_deployment_id = invalid_reference.target_deployment_id;
        assert_eq!(
            invalid_reference.validate().unwrap_err().code,
            TransactionErrorCode::InvalidRecord
        );

        let mut invalid_failure = transaction();
        invalid_failure.phase = RollbackPhase::Failed;
        assert_eq!(
            invalid_failure.validate().unwrap_err().code,
            TransactionErrorCode::InvalidRecord
        );
        invalid_failure.failure = Some("x".repeat(MAX_FAILURE_LENGTH + 1));
        assert_eq!(
            invalid_failure.validate().unwrap_err().code,
            TransactionErrorCode::InvalidRecord
        );
    }
}
