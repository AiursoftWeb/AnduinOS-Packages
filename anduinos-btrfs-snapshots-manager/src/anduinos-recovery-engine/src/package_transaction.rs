use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::RECOVERY_STORE_ROOT;
use crate::model::DeploymentId;

pub const PACKAGE_TRANSACTION_SCHEMA_VERSION: u32 = 1;
const MAX_TRANSACTION_BYTES: u64 = 1024 * 1024;
const MAX_FAILURE_LENGTH: usize = 2000;
const PENDING_PACKAGE_TRANSACTION: &str = "pending-package.json";

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct PackageTransactionId(Uuid);

impl PackageTransactionId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for PackageTransactionId {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Display for PackageTransactionId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.hyphenated().fmt(formatter)
    }
}

impl FromStr for PackageTransactionId {
    type Err = uuid::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Uuid::parse_str(value).map(Self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum PackageTransactionPhase {
    PreparingPre,
    AwaitingPost,
    Complete,
    Interrupted,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PackageTransaction {
    pub schema_version: u32,
    pub id: PackageTransactionId,
    pub phase: PackageTransactionPhase,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub pre_deployment_id: Option<DeploymentId>,
    pub post_deployment_id: Option<DeploymentId>,
    pub failure: Option<String>,
}

impl PackageTransaction {
    pub fn new() -> Self {
        let now = Utc::now();
        Self {
            schema_version: PACKAGE_TRANSACTION_SCHEMA_VERSION,
            id: PackageTransactionId::new(),
            phase: PackageTransactionPhase::PreparingPre,
            created_at: now,
            updated_at: now,
            pre_deployment_id: None,
            post_deployment_id: None,
            failure: None,
        }
    }

    pub fn record_pre(
        &mut self,
        deployment_id: DeploymentId,
        now: DateTime<Utc>,
    ) -> Result<(), PackageTransactionError> {
        if self.phase != PackageTransactionPhase::PreparingPre {
            return Err(invalid(
                "Only a preparing transaction can record its pre snapshot",
            ));
        }
        self.pre_deployment_id = Some(deployment_id);
        self.phase = PackageTransactionPhase::AwaitingPost;
        self.updated_at = now;
        self.validate()
    }

    pub fn skip_pre(&mut self, now: DateTime<Utc>) -> Result<(), PackageTransactionError> {
        if self.phase != PackageTransactionPhase::PreparingPre {
            return Err(invalid(
                "Only a preparing transaction can skip its pre snapshot",
            ));
        }
        self.phase = PackageTransactionPhase::AwaitingPost;
        self.updated_at = now;
        self.validate()
    }

    pub fn complete_without_post(
        &mut self,
        now: DateTime<Utc>,
    ) -> Result<(), PackageTransactionError> {
        if self.phase != PackageTransactionPhase::AwaitingPost || self.pre_deployment_id.is_none() {
            return Err(invalid(
                "Only a pre-snapshot transaction can finish without post",
            ));
        }
        self.phase = PackageTransactionPhase::Complete;
        self.updated_at = now;
        self.validate()
    }

    pub fn record_post(
        &mut self,
        deployment_id: DeploymentId,
        now: DateTime<Utc>,
    ) -> Result<(), PackageTransactionError> {
        if self.phase != PackageTransactionPhase::AwaitingPost {
            return Err(invalid(
                "Only an awaiting transaction can record its post snapshot",
            ));
        }
        self.post_deployment_id = Some(deployment_id);
        self.phase = PackageTransactionPhase::Complete;
        self.updated_at = now;
        self.validate()
    }

    pub fn interrupt(
        &mut self,
        failure: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<(), PackageTransactionError> {
        if matches!(
            self.phase,
            PackageTransactionPhase::Complete | PackageTransactionPhase::Interrupted
        ) {
            return Err(invalid(
                "A terminal package transaction cannot be interrupted again",
            ));
        }
        self.phase = PackageTransactionPhase::Interrupted;
        self.post_deployment_id = None;
        self.failure = Some(sanitize_failure(failure.into()));
        self.updated_at = now;
        self.validate()
    }

    pub fn validate(&self) -> Result<(), PackageTransactionError> {
        if self.schema_version != PACKAGE_TRANSACTION_SCHEMA_VERSION {
            return Err(PackageTransactionError::new(
                PackageTransactionErrorCode::UnsupportedSchema,
                format!(
                    "Unsupported package transaction schema {}",
                    self.schema_version
                ),
            ));
        }
        if self.updated_at < self.created_at {
            return Err(invalid("Package transaction timestamps are out of order"));
        }
        match self.phase {
            PackageTransactionPhase::PreparingPre => {
                if self.pre_deployment_id.is_some()
                    || self.post_deployment_id.is_some()
                    || self.failure.is_some()
                {
                    return Err(invalid(
                        "A preparing package transaction contains later state",
                    ));
                }
            }
            PackageTransactionPhase::AwaitingPost => {
                if self.post_deployment_id.is_some() || self.failure.is_some() {
                    return Err(invalid(
                        "An awaiting package transaction has invalid snapshot references",
                    ));
                }
            }
            PackageTransactionPhase::Complete => {
                if (self.pre_deployment_id.is_none() && self.post_deployment_id.is_none())
                    || self.failure.is_some()
                {
                    return Err(invalid("A complete package transaction is incomplete"));
                }
            }
            PackageTransactionPhase::Interrupted => {
                if self.post_deployment_id.is_some() || self.failure.is_none() {
                    return Err(invalid(
                        "An interrupted package transaction has invalid terminal state",
                    ));
                }
            }
        }
        if self.failure.as_deref().is_some_and(|failure| {
            failure.is_empty()
                || failure.chars().count() > MAX_FAILURE_LENGTH
                || failure.chars().any(char::is_control)
        }) {
            return Err(invalid("Package transaction failure diagnostic is invalid"));
        }
        Ok(())
    }
}

impl Default for PackageTransaction {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PackageTransactionErrorCode {
    AlreadyPending,
    NotFound,
    UnsafePath,
    TooLarge,
    InvalidJson,
    UnsupportedSchema,
    InvalidRecord,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PackageTransactionError {
    pub code: PackageTransactionErrorCode,
    pub message: String,
}

impl PackageTransactionError {
    fn new(code: PackageTransactionErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for PackageTransactionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for PackageTransactionError {}

#[derive(Clone, Debug)]
pub struct PackageTransactionStore {
    transactions_dir: PathBuf,
    history_dir: PathBuf,
}

impl PackageTransactionStore {
    pub fn new(snapshot_root: impl AsRef<Path>) -> Self {
        let transactions_dir = snapshot_root.as_ref().join("transactions");
        let history_dir = transactions_dir.join("package-history");
        Self {
            transactions_dir,
            history_dir,
        }
    }

    pub fn load_pending(&self) -> Result<Option<PackageTransaction>, PackageTransactionError> {
        read_transaction(&self.pending_path())
            .map(Some)
            .or_else(|error| {
                if error.code == PackageTransactionErrorCode::NotFound {
                    Ok(None)
                } else {
                    Err(error)
                }
            })
    }

    pub fn load_history(&self) -> Result<Vec<PackageTransaction>, PackageTransactionError> {
        let metadata = match fs::symlink_metadata(&self.history_dir) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => {
                return Err(io_error(
                    "Could not inspect package transaction history",
                    error,
                ));
            }
        };
        if !metadata.file_type().is_dir() {
            return Err(PackageTransactionError::new(
                PackageTransactionErrorCode::UnsafePath,
                "Package transaction history is not a real directory",
            ));
        }
        let entries = fs::read_dir(&self.history_dir)
            .map_err(|error| io_error("Could not read package transaction history", error))?;
        let mut transactions = Vec::new();
        for entry in entries {
            let entry = entry.map_err(|error| {
                io_error("Could not read a package transaction history entry", error)
            })?;
            let path = entry.path();
            if path.extension().and_then(|extension| extension.to_str()) != Some("json") {
                continue;
            }
            let stem = path
                .file_stem()
                .and_then(|stem| stem.to_str())
                .ok_or_else(|| {
                    PackageTransactionError::new(
                        PackageTransactionErrorCode::UnsafePath,
                        "Package transaction history filename is not valid UTF-8",
                    )
                })?;
            let filename_id = stem.parse::<PackageTransactionId>().map_err(|_| {
                PackageTransactionError::new(
                    PackageTransactionErrorCode::UnsafePath,
                    "Package transaction history filename is not a UUID",
                )
            })?;
            if filename_id.to_string() != stem {
                return Err(PackageTransactionError::new(
                    PackageTransactionErrorCode::UnsafePath,
                    "Package transaction history filename is not canonical",
                ));
            }
            let transaction = read_transaction(&path)?;
            if transaction.id != filename_id {
                return Err(PackageTransactionError::new(
                    PackageTransactionErrorCode::InvalidRecord,
                    "Package transaction ID does not match its history filename",
                ));
            }
            if !matches!(
                transaction.phase,
                PackageTransactionPhase::Complete | PackageTransactionPhase::Interrupted
            ) {
                return Err(PackageTransactionError::new(
                    PackageTransactionErrorCode::InvalidRecord,
                    "Package transaction history contains a nonterminal record",
                ));
            }
            transactions.push(transaction);
        }
        transactions.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| left.id.to_string().cmp(&right.id.to_string()))
        });
        Ok(transactions)
    }

    pub fn create(&self, transaction: &PackageTransaction) -> Result<(), PackageTransactionError> {
        transaction.validate()?;
        ensure_real_directory(&self.transactions_dir)?;
        match fs::symlink_metadata(self.pending_path()) {
            Ok(_) => {
                return Err(PackageTransactionError::new(
                    PackageTransactionErrorCode::AlreadyPending,
                    "Another package transaction is already pending",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(io_error(
                    "Could not inspect pending package transaction",
                    error,
                ));
            }
        }
        write_atomic(
            &self.transactions_dir,
            &self.pending_path(),
            transaction,
            false,
        )
    }

    pub fn update(&self, transaction: &PackageTransaction) -> Result<(), PackageTransactionError> {
        transaction.validate()?;
        ensure_regular_file(&self.pending_path())?;
        write_atomic(
            &self.transactions_dir,
            &self.pending_path(),
            transaction,
            true,
        )
    }

    pub fn archive(&self, transaction: &PackageTransaction) -> Result<(), PackageTransactionError> {
        if !matches!(
            transaction.phase,
            PackageTransactionPhase::Complete | PackageTransactionPhase::Interrupted
        ) {
            return Err(invalid(
                "Only terminal package transactions can be archived",
            ));
        }
        self.update(transaction)?;
        ensure_directory(&self.history_dir)?;
        let destination = self.history_dir.join(format!("{}.json", transaction.id));
        if fs::symlink_metadata(&destination).is_ok() {
            return Err(PackageTransactionError::new(
                PackageTransactionErrorCode::UnsafePath,
                "Package transaction history entry already exists",
            ));
        }
        fs::rename(self.pending_path(), destination)
            .map_err(|error| io_error("Could not archive package transaction", error))?;
        sync_directory(&self.history_dir)
            .map_err(|error| io_error("Could not sync package transaction history", error))?;
        sync_directory(&self.transactions_dir)
            .map_err(|error| io_error("Could not sync package transactions", error))
    }

    fn pending_path(&self) -> PathBuf {
        self.transactions_dir.join(PENDING_PACKAGE_TRANSACTION)
    }
}

impl Default for PackageTransactionStore {
    fn default() -> Self {
        Self::new(RECOVERY_STORE_ROOT)
    }
}

fn read_transaction(path: &Path) -> Result<PackageTransaction, PackageTransactionError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        if error.kind() == io::ErrorKind::NotFound {
            PackageTransactionError::new(
                PackageTransactionErrorCode::NotFound,
                "No package transaction is pending",
            )
        } else {
            io_error("Could not inspect package transaction", error)
        }
    })?;
    if !metadata.file_type().is_file() {
        return Err(PackageTransactionError::new(
            PackageTransactionErrorCode::UnsafePath,
            "Package transaction is not a regular file",
        ));
    }
    if metadata.len() > MAX_TRANSACTION_BYTES {
        return Err(PackageTransactionError::new(
            PackageTransactionErrorCode::TooLarge,
            "Package transaction exceeds the safety limit",
        ));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| io_error("Could not open package transaction", error))?;
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    Read::by_ref(&mut file)
        .take(MAX_TRANSACTION_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| io_error("Could not read package transaction", error))?;
    if bytes.len() as u64 > MAX_TRANSACTION_BYTES {
        return Err(PackageTransactionError::new(
            PackageTransactionErrorCode::TooLarge,
            "Package transaction exceeds the safety limit",
        ));
    }
    let transaction = serde_json::from_slice::<PackageTransaction>(&bytes).map_err(|error| {
        PackageTransactionError::new(
            PackageTransactionErrorCode::InvalidJson,
            format!("Package transaction is invalid JSON: {error}"),
        )
    })?;
    transaction.validate()?;
    Ok(transaction)
}

fn write_atomic(
    directory: &Path,
    target: &Path,
    transaction: &PackageTransaction,
    replace: bool,
) -> Result<(), PackageTransactionError> {
    let temporary = directory.join(format!(".pending-package.{}.tmp", Uuid::new_v4()));
    let serialized = serde_json::to_vec_pretty(transaction).map_err(|error| {
        PackageTransactionError::new(
            PackageTransactionErrorCode::InvalidRecord,
            format!("Could not serialize package transaction: {error}"),
        )
    })?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(&temporary)
        .map_err(|error| io_error("Could not create package transaction", error))?;
    let result = (|| {
        file.write_all(&serialized)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        if replace {
            fs::rename(&temporary, target)?;
        } else {
            fs::hard_link(&temporary, target)?;
            fs::remove_file(&temporary)?;
        }
        sync_directory(directory)
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(io_error(
            "Could not atomically commit package transaction",
            error,
        ));
    }
    Ok(())
}

fn ensure_regular_file(path: &Path) -> Result<(), PackageTransactionError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        if error.kind() == io::ErrorKind::NotFound {
            PackageTransactionError::new(
                PackageTransactionErrorCode::NotFound,
                "No package transaction is pending",
            )
        } else {
            io_error("Could not inspect package transaction", error)
        }
    })?;
    if metadata.file_type().is_file() {
        Ok(())
    } else {
        Err(PackageTransactionError::new(
            PackageTransactionErrorCode::UnsafePath,
            "Package transaction is not a regular file",
        ))
    }
}

fn ensure_real_directory(path: &Path) -> Result<(), PackageTransactionError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| io_error("Could not inspect package transaction directory", error))?;
    if metadata.file_type().is_dir() {
        Ok(())
    } else {
        Err(PackageTransactionError::new(
            PackageTransactionErrorCode::UnsafePath,
            "Package transaction directory is not a real directory",
        ))
    }
}

fn ensure_directory(path: &Path) -> Result<(), PackageTransactionError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {
            fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                .map_err(|error| io_error("Could not secure package transaction history", error))
        }
        Ok(_) => Err(PackageTransactionError::new(
            PackageTransactionErrorCode::UnsafePath,
            "Package transaction history is not a real directory",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir(path)
                .map_err(|error| io_error("Could not create package transaction history", error))?;
            fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                .map_err(|error| io_error("Could not secure package transaction history", error))?;
            ensure_real_directory(path)
        }
        Err(error) => Err(io_error(
            "Could not inspect package transaction history",
            error,
        )),
    }
}

fn sync_directory(path: &Path) -> io::Result<()> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)?
        .sync_all()
}

fn sanitize_failure(value: String) -> String {
    let sanitized = value
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .take(MAX_FAILURE_LENGTH)
        .collect::<String>()
        .trim()
        .to_string();
    if sanitized.is_empty() {
        "Unspecified package hook failure".into()
    } else {
        sanitized
    }
}

fn invalid(message: impl Into<String>) -> PackageTransactionError {
    PackageTransactionError::new(PackageTransactionErrorCode::InvalidRecord, message)
}

fn io_error(context: &str, error: io::Error) -> PackageTransactionError {
    PackageTransactionError::new(
        PackageTransactionErrorCode::Io,
        format!("{context}: {error}"),
    )
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::symlink;

    use super::*;

    struct TestStore(PathBuf);

    impl TestStore {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "btrfs-snapshots-manager-package-{}",
                Uuid::new_v4()
            ));
            fs::create_dir_all(root.join("transactions")).unwrap();
            Self(root)
        }

        fn store(&self) -> PackageTransactionStore {
            PackageTransactionStore::new(&self.0)
        }
    }

    impl Drop for TestStore {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn paired_transaction_requires_pre_before_post() {
        let mut transaction = PackageTransaction::new();
        assert!(
            transaction
                .record_post(DeploymentId::new(), Utc::now())
                .is_err()
        );
        transaction
            .record_pre(DeploymentId::new(), Utc::now())
            .unwrap();
        transaction
            .record_post(DeploymentId::new(), Utc::now())
            .unwrap();
        assert_eq!(transaction.phase, PackageTransactionPhase::Complete);
        transaction.validate().unwrap();
    }

    #[test]
    fn transactions_support_pre_only_and_post_only() {
        let mut pre_only = PackageTransaction::new();
        pre_only
            .record_pre(DeploymentId::new(), Utc::now())
            .unwrap();
        pre_only.complete_without_post(Utc::now()).unwrap();
        assert!(pre_only.pre_deployment_id.is_some());
        assert!(pre_only.post_deployment_id.is_none());

        let mut post_only = PackageTransaction::new();
        post_only.skip_pre(Utc::now()).unwrap();
        post_only
            .record_post(DeploymentId::new(), Utc::now())
            .unwrap();
        assert!(post_only.pre_deployment_id.is_none());
        assert!(post_only.post_deployment_id.is_some());
    }

    #[test]
    fn pending_transaction_is_atomic_and_archived_by_identifier() {
        let environment = TestStore::new();
        let store = environment.store();
        let mut transaction = PackageTransaction::new();
        store.create(&transaction).unwrap();
        transaction
            .record_pre(DeploymentId::new(), Utc::now())
            .unwrap();
        store.update(&transaction).unwrap();
        transaction
            .record_post(DeploymentId::new(), Utc::now())
            .unwrap();
        store.archive(&transaction).unwrap();
        assert!(store.load_pending().unwrap().is_none());
        let archived = read_transaction(
            &environment
                .0
                .join("transactions/package-history")
                .join(format!("{}.json", transaction.id)),
        )
        .unwrap();
        assert_eq!(archived, transaction);
        assert_eq!(store.load_history().unwrap(), vec![transaction]);
    }

    #[test]
    fn history_is_newest_first() {
        let environment = TestStore::new();
        let store = environment.store();
        let mut transactions = Vec::new();
        for age in [2, 1] {
            let mut transaction = PackageTransaction::new();
            transaction.created_at = Utc::now() - chrono::Duration::days(age);
            transaction.updated_at = transaction.created_at;
            store.create(&transaction).unwrap();
            transaction
                .record_pre(DeploymentId::new(), transaction.created_at)
                .unwrap();
            store.update(&transaction).unwrap();
            transaction
                .record_post(DeploymentId::new(), transaction.created_at)
                .unwrap();
            store.archive(&transaction).unwrap();
            transactions.push(transaction);
        }
        let history = store.load_history().unwrap();
        assert_eq!(history[0], transactions[1]);
        assert_eq!(history[1], transactions[0]);
    }

    #[test]
    fn unsafe_history_entry_stops_automatic_reading() {
        let environment = TestStore::new();
        let history = environment.0.join("transactions/package-history");
        fs::create_dir(&history).unwrap();
        let outside = environment.0.join("outside.json");
        fs::write(&outside, "{}").unwrap();
        symlink(
            &outside,
            history.join("00000000-0000-0000-0000-000000000000.json"),
        )
        .unwrap();
        assert_eq!(
            environment.store().load_history().unwrap_err().code,
            PackageTransactionErrorCode::UnsafePath
        );
    }

    #[test]
    fn pending_symlink_is_never_followed() {
        let environment = TestStore::new();
        let outside = environment.0.join("outside.json");
        fs::write(&outside, "{}").unwrap();
        symlink(
            &outside,
            environment.0.join("transactions/pending-package.json"),
        )
        .unwrap();
        assert_eq!(
            environment.store().load_pending().unwrap_err().code,
            PackageTransactionErrorCode::UnsafePath
        );
    }

    #[test]
    fn interrupted_failure_is_sanitized_and_bounded() {
        let mut transaction = PackageTransaction::new();
        transaction
            .interrupt(format!("bad\n{}", "x".repeat(3000)), Utc::now())
            .unwrap();
        let failure = transaction.failure.unwrap();
        assert!(!failure.contains('\n'));
        assert!(failure.chars().count() <= MAX_FAILURE_LENGTH);
    }
}
