//! Independent personal-file snapshots and descriptor-confined recovery.
//!
//! Personal snapshots deliberately have no deployment/boot state. Restoring a
//! system deployment must never select, replace, or delete `@home`; this module
//! owns a separate history whose only recovery operation is exporting content
//! through already-open read-only descriptors.

use std::ffi::{CStr, OsString};
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::layout::LayoutReport;
use crate::operations::{CommandRunner, SystemCommandRunner};
use crate::space::{MINIMUM_TRANSACTION_RESERVE_BYTES, probe_filesystem_space};
use crate::{PERSONAL_SNAPSHOT_SCHEMA_VERSION, RECOVERY_STORE_ROOT};

const BTRFS: &str = "/usr/bin/btrfs";
const MAX_METADATA_BYTES: u64 = 1024 * 1024;
pub const MAX_DIRECTORY_ENTRIES: usize = 10_000;
pub const MAX_RELATIVE_PATH_BYTES: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct PersonalSnapshotId(Uuid);

impl PersonalSnapshotId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for PersonalSnapshotId {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Display for PersonalSnapshotId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.hyphenated().fmt(formatter)
    }
}

impl FromStr for PersonalSnapshotId {
    type Err = uuid::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Uuid::parse_str(value).map(Self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum PersonalSnapshotKind {
    Manual,
    Automatic,
    Imported,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum PersonalSnapshotState {
    Creating,
    Ready,
    Broken,
    Deleting,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PersonalSnapshotRecord {
    pub schema_version: u32,
    pub id: PersonalSnapshotId,
    pub kind: PersonalSnapshotKind,
    pub state: PersonalSnapshotState,
    pub created_at: DateTime<Utc>,
    pub title: String,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub schedule_id: Option<String>,
    pub snapshot_uuid: Option<String>,
    pub snapshot_parent_uuid: Option<String>,
    pub pinned: bool,
    pub failure: Option<String>,
}

impl PersonalSnapshotRecord {
    pub fn validate(&self) -> Result<(), PersonalError> {
        if self.schema_version != PERSONAL_SNAPSHOT_SCHEMA_VERSION {
            return Err(PersonalError::invalid(
                "unsupported personal snapshot schema",
            ));
        }
        validate_text(&self.title, 120, "title")?;
        validate_text(&self.reason, 500, "reason")?;
        match (self.kind, self.schedule_id.as_deref()) {
            (PersonalSnapshotKind::Automatic, Some(value)) => validate_label(value)?,
            (PersonalSnapshotKind::Automatic, None) | (_, Some(_)) => {
                return Err(PersonalError::invalid("invalid personal schedule identity"));
            }
            _ => {}
        }
        for value in [
            self.snapshot_uuid.as_deref(),
            self.snapshot_parent_uuid.as_deref(),
        ] {
            if value.is_some_and(|value| Uuid::parse_str(value).is_err()) {
                return Err(PersonalError::invalid("invalid personal snapshot UUID"));
            }
        }
        if self.state == PersonalSnapshotState::Ready && self.snapshot_uuid.is_none() {
            return Err(PersonalError::invalid(
                "ready personal snapshot has no filesystem identity",
            ));
        }
        if self.failure.as_deref().is_some_and(|value| {
            value.chars().count() > 2000 || value.chars().any(char::is_control)
        }) {
            return Err(PersonalError::invalid(
                "invalid personal snapshot failure text",
            ));
        }
        Ok(())
    }

    pub fn can_delete(&self) -> bool {
        !self.pinned
            && matches!(
                self.state,
                PersonalSnapshotState::Creating
                    | PersonalSnapshotState::Ready
                    | PersonalSnapshotState::Broken
                    | PersonalSnapshotState::Deleting
            )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum PersonalEntryKind {
    File,
    Directory,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PersonalDirectoryEntry {
    pub name: String,
    pub kind: PersonalEntryKind,
    pub size: u64,
    pub modified_unix_seconds: i64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PersonalDiscovery {
    pub schema_version: u32,
    pub snapshots: Vec<PersonalSnapshotRecord>,
    pub issues: Vec<PersonalDiscoveryIssue>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PersonalDiscoveryIssue {
    pub entry: String,
    pub message: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PersonalErrorCode {
    UnsupportedLayout,
    InvalidInput,
    NotFound,
    Protected,
    Busy,
    UnsafePath,
    InsufficientSpace,
    CommandFailed,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PersonalError {
    pub code: PersonalErrorCode,
    pub message: String,
}

impl PersonalError {
    fn new(code: PersonalErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    fn invalid(message: impl Into<String>) -> Self {
        Self::new(PersonalErrorCode::InvalidInput, message)
    }
}

impl fmt::Display for PersonalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for PersonalError {}

#[derive(Clone, Debug)]
pub struct PersonalSnapshotEngine<R = SystemCommandRunner> {
    home_root: PathBuf,
    store_root: PathBuf,
    runner: R,
    minimum_free_bytes: u64,
}

impl Default for PersonalSnapshotEngine<SystemCommandRunner> {
    fn default() -> Self {
        Self::new("/home", RECOVERY_STORE_ROOT, SystemCommandRunner)
            .with_minimum_free_bytes(MINIMUM_TRANSACTION_RESERVE_BYTES)
    }
}

impl<R: CommandRunner> PersonalSnapshotEngine<R> {
    pub fn new(home_root: impl Into<PathBuf>, store_root: impl Into<PathBuf>, runner: R) -> Self {
        Self {
            home_root: home_root.into(),
            store_root: store_root.into(),
            runner,
            minimum_free_bytes: 0,
        }
    }

    pub fn with_minimum_free_bytes(mut self, bytes: u64) -> Self {
        self.minimum_free_bytes = bytes;
        self
    }

    pub fn create_manual(
        &self,
        layout: &LayoutReport,
        title: &str,
        reason: &str,
        pinned: bool,
    ) -> Result<PersonalSnapshotRecord, PersonalError> {
        self.create(
            layout,
            title,
            reason,
            None,
            pinned,
            PersonalSnapshotKind::Manual,
        )
    }

    pub fn create_scheduled(
        &self,
        layout: &LayoutReport,
        schedule_id: &str,
        title: &str,
        reason: &str,
    ) -> Result<PersonalSnapshotRecord, PersonalError> {
        self.create(
            layout,
            title,
            reason,
            Some(schedule_id),
            false,
            PersonalSnapshotKind::Automatic,
        )
    }

    fn create(
        &self,
        layout: &LayoutReport,
        title: &str,
        reason: &str,
        schedule_id: Option<&str>,
        pinned: bool,
        kind: PersonalSnapshotKind,
    ) -> Result<PersonalSnapshotRecord, PersonalError> {
        ensure_supported(layout)?;
        validate_text(title, 120, "title")?;
        validate_text(reason, 500, "reason")?;
        if let Some(value) = schedule_id {
            validate_label(value)?;
        }
        self.ensure_space()?;
        self.ensure_directories()?;
        let _operation_lock = StoreLock::acquire(&self.personal_root().join("operation.lock"))?;

        let id = PersonalSnapshotId::new();
        let mut record = PersonalSnapshotRecord {
            schema_version: PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            id,
            kind,
            state: PersonalSnapshotState::Creating,
            created_at: Utc::now(),
            title: title.to_string(),
            reason: reason.to_string(),
            schedule_id: schedule_id.map(str::to_string),
            snapshot_uuid: None,
            snapshot_parent_uuid: None,
            pinned,
            failure: None,
        };
        record.validate()?;
        self.write_record(&record)?;

        let container = self.snapshot_container(id);
        let snapshot = container.join("home");
        let result = (|| {
            ensure_new_directory(&container)?;
            self.run_btrfs(&[
                OsString::from("subvolume"),
                OsString::from("snapshot"),
                OsString::from("-r"),
                self.home_root.as_os_str().to_owned(),
                snapshot.as_os_str().to_owned(),
            ])?;
            let (snapshot_uuid, parent_uuid) = self.snapshot_identity(&snapshot)?;
            record.snapshot_uuid = Some(snapshot_uuid);
            record.snapshot_parent_uuid = parent_uuid;
            self.run_btrfs(&[
                OsString::from("filesystem"),
                OsString::from("sync"),
                self.home_root.as_os_str().to_owned(),
            ])?;
            record.state = PersonalSnapshotState::Ready;
            self.write_record(&record)
        })();

        if let Err(error) = result {
            record.state = PersonalSnapshotState::Broken;
            record.failure = Some(error.to_string().chars().take(2000).collect());
            let _ = self.write_record(&record);
            if snapshot.exists() {
                let _ = self.run_btrfs(&[
                    OsString::from("subvolume"),
                    OsString::from("delete"),
                    OsString::from("--commit-after"),
                    snapshot.as_os_str().to_owned(),
                ]);
            }
            let _ = fs::remove_dir(&container);
            return Err(error);
        }
        Ok(record)
    }

    pub fn discover(&self) -> PersonalDiscovery {
        let mut report = PersonalDiscovery {
            schema_version: PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            snapshots: Vec::new(),
            issues: Vec::new(),
        };
        let directory = self.metadata_root();
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return report,
            Err(error) => {
                report.issues.push(PersonalDiscoveryIssue {
                    entry: "metadata".into(),
                    message: format!("Could not read personal snapshot metadata: {error}"),
                });
                return report;
            }
        };
        for entry in entries.flatten() {
            if entry.path().extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            match self.read_record_path(&entry.path()) {
                Ok(record) => {
                    let snapshot = self.snapshot_path(record.id);
                    match fs::symlink_metadata(&snapshot) {
                        Ok(metadata) if metadata.file_type().is_dir() => {
                            report.snapshots.push(record)
                        }
                        Err(error)
                            if record.state == PersonalSnapshotState::Deleting
                                && error.kind() == io::ErrorKind::NotFound =>
                        {
                            report.snapshots.push(record)
                        }
                        Ok(_) => report.issues.push(PersonalDiscoveryIssue {
                            entry: entry.file_name().to_string_lossy().into_owned(),
                            message: "Personal snapshot is not a real directory".into(),
                        }),
                        Err(error) => report.issues.push(PersonalDiscoveryIssue {
                            entry: entry.file_name().to_string_lossy().into_owned(),
                            message: format!("Personal snapshot is unavailable: {error}"),
                        }),
                    }
                }
                Err(error) => report.issues.push(PersonalDiscoveryIssue {
                    entry: entry.file_name().to_string_lossy().into_owned(),
                    message: error.to_string(),
                }),
            }
        }
        report
            .snapshots
            .sort_by_key(|record| (std::cmp::Reverse(record.created_at), record.id.to_string()));
        report
    }

    pub fn load(&self, id: PersonalSnapshotId) -> Result<PersonalSnapshotRecord, PersonalError> {
        self.read_record_path(&self.metadata_path(id))
    }

    pub fn verify(
        &self,
        layout: &LayoutReport,
        id: PersonalSnapshotId,
    ) -> Result<PersonalSnapshotRecord, PersonalError> {
        ensure_supported(layout)?;
        let record = self.load(id)?;
        if record.state != PersonalSnapshotState::Ready {
            return Err(PersonalError::new(
                PersonalErrorCode::InvalidInput,
                "Personal snapshot is not ready",
            ));
        }
        let snapshot = self.snapshot_path(id);
        ensure_real_directory(&snapshot)?;
        let (uuid, parent_uuid) = self.snapshot_identity(&snapshot)?;
        if record.snapshot_uuid.as_deref() != Some(uuid.as_str())
            || record.snapshot_parent_uuid != parent_uuid
        {
            return Err(PersonalError::new(
                PersonalErrorCode::UnsafePath,
                "Personal snapshot filesystem identity does not match metadata",
            ));
        }
        let output = self.run_btrfs(&[
            OsString::from("property"),
            OsString::from("get"),
            OsString::from("-ts"),
            snapshot.as_os_str().to_owned(),
            OsString::from("ro"),
        ])?;
        if String::from_utf8_lossy(&output).trim() != "ro=true" {
            return Err(PersonalError::new(
                PersonalErrorCode::UnsafePath,
                "Personal snapshot is not read-only",
            ));
        }
        Ok(record)
    }

    pub fn set_pinned(
        &self,
        layout: &LayoutReport,
        id: PersonalSnapshotId,
        pinned: bool,
    ) -> Result<PersonalSnapshotRecord, PersonalError> {
        ensure_supported(layout)?;
        let _lock = StoreLock::acquire(&self.personal_root().join("operation.lock"))?;
        let mut record = self.load(id)?;
        if record.state != PersonalSnapshotState::Ready {
            return Err(PersonalError::new(
                PersonalErrorCode::InvalidInput,
                "Only ready personal snapshots can change protection",
            ));
        }
        record.pinned = pinned;
        self.write_record(&record)?;
        Ok(record)
    }

    /// Adopt one read-only `home` subvolume received into the engine-owned
    /// import staging directory. External media paths never cross this API.
    pub fn adopt_imported(
        &self,
        layout: &LayoutReport,
        source: &PersonalSnapshotRecord,
        received: &Path,
    ) -> Result<PersonalSnapshotRecord, PersonalError> {
        ensure_supported(layout)?;
        source.validate()?;
        if source.state != PersonalSnapshotState::Ready {
            return Err(PersonalError::invalid(
                "Imported personal source is not ready",
            ));
        }
        self.ensure_space()?;
        self.ensure_directories()?;
        let staging = self.import_staging_root();
        if received.file_name().and_then(|value| value.to_str()) != Some("home")
            || !received.starts_with(&staging)
            || received
                .components()
                .any(|component| matches!(component, Component::ParentDir))
        {
            return Err(PersonalError::new(
                PersonalErrorCode::UnsafePath,
                "Imported personal snapshot is outside trusted staging",
            ));
        }
        ensure_real_directory(received)?;
        let read_only = self.run_btrfs(&[
            OsString::from("property"),
            OsString::from("get"),
            OsString::from("-ts"),
            received.as_os_str().to_owned(),
            OsString::from("ro"),
        ])?;
        if String::from_utf8_lossy(&read_only).trim() != "ro=true" {
            return Err(PersonalError::new(
                PersonalErrorCode::UnsafePath,
                "Imported personal snapshot is not read-only",
            ));
        }
        let (snapshot_uuid, snapshot_parent_uuid) = self.snapshot_identity(received)?;
        let _operation_lock = StoreLock::acquire(&self.personal_root().join("operation.lock"))?;
        let id = PersonalSnapshotId::new();
        let mut record = PersonalSnapshotRecord {
            schema_version: PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            id,
            kind: PersonalSnapshotKind::Imported,
            state: PersonalSnapshotState::Creating,
            created_at: Utc::now(),
            title: source.title.clone(),
            reason: source.reason.clone(),
            schedule_id: None,
            snapshot_uuid: Some(snapshot_uuid),
            snapshot_parent_uuid,
            pinned: source.pinned,
            failure: None,
        };
        self.write_record(&record)?;
        let container = self.snapshot_container(id);
        let destination = container.join("home");
        let result = (|| {
            ensure_new_directory(&container)?;
            fs::rename(received, &destination).map_err(|error| {
                personal_io("Could not commit imported personal snapshot", error)
            })?;
            sync_directory(&container)?;
            sync_directory(&self.snapshots_root())?;
            record.state = PersonalSnapshotState::Ready;
            self.write_record(&record)
        })();
        if let Err(error) = result {
            if matches!(
                fs::symlink_metadata(&destination),
                Ok(metadata) if metadata.file_type().is_dir()
            ) {
                let _ = self.run_btrfs(&[
                    OsString::from("subvolume"),
                    OsString::from("delete"),
                    OsString::from("--commit-after"),
                    destination.as_os_str().to_owned(),
                ]);
            }
            let _ = fs::remove_dir(&container);
            record.state = PersonalSnapshotState::Broken;
            record.failure = Some(error.to_string().chars().take(2000).collect());
            let _ = self.write_record(&record);
            return Err(error);
        }
        Ok(record)
    }

    pub fn delete(
        &self,
        layout: &LayoutReport,
        id: PersonalSnapshotId,
    ) -> Result<(), PersonalError> {
        ensure_supported(layout)?;
        let _operation_lock = StoreLock::acquire(&self.personal_root().join("operation.lock"))?;
        let _browse_lock = StoreLock::acquire_nonblocking(&self.browse_lock_path(id))?;
        let mut record = self.load(id)?;
        if !record.can_delete() {
            return Err(PersonalError::new(
                PersonalErrorCode::Protected,
                "Personal snapshot is protected",
            ));
        }
        let snapshot = self.snapshot_path(id);
        let snapshot_exists = match fs::symlink_metadata(&snapshot) {
            Ok(metadata) if metadata.file_type().is_dir() => true,
            Ok(_) => {
                return Err(PersonalError::new(
                    PersonalErrorCode::UnsafePath,
                    "Personal snapshot deletion target is not a real directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => false,
            Err(error) => {
                return Err(personal_io(
                    "Could not inspect personal snapshot deletion target",
                    error,
                ));
            }
        };
        record.state = PersonalSnapshotState::Deleting;
        self.write_record(&record)?;
        if snapshot_exists {
            self.run_btrfs(&[
                OsString::from("subvolume"),
                OsString::from("delete"),
                OsString::from("--commit-after"),
                snapshot.as_os_str().to_owned(),
            ])?;
        }
        let _ = fs::remove_dir(self.snapshot_container(id));
        fs::remove_file(self.metadata_path(id))
            .map_err(|error| personal_io("Could not remove personal snapshot metadata", error))?;
        sync_directory(&self.metadata_root())?;
        Ok(())
    }

    pub fn browser(
        &self,
        layout: &LayoutReport,
        id: PersonalSnapshotId,
        user_directory: &str,
    ) -> Result<PersonalSnapshotBrowser, PersonalError> {
        self.verify(layout, id)?;
        validate_single_component(user_directory)?;
        let lease = StoreLock::acquire_shared(&self.browse_lock_path(id))?;
        // Open the fixed, root-owned store first. Resolving from `/` with
        // RESOLVE_NO_XDEV would reject the intentional `@snapshots` mount
        // boundary before reaching the protected store.
        let store_root = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(&self.store_root)
            .map_err(|error| personal_io("Could not open personal recovery store", error))?;
        let snapshot_relative = PathBuf::from("personal")
            .join("snapshots")
            .join(id.to_string())
            .join("home");
        let snapshot_root = open_beneath(
            store_root.as_raw_fd(),
            &snapshot_relative,
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
        )?;
        let user_root = open_beneath(
            snapshot_root.as_raw_fd(),
            Path::new(user_directory),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
        )?;
        Ok(PersonalSnapshotBrowser {
            _snapshot_root: snapshot_root,
            user_root,
            _lease: lease,
        })
    }

    fn ensure_space(&self) -> Result<(), PersonalError> {
        if self.minimum_free_bytes == 0 {
            return Ok(());
        }
        let probe = self
            .store_root
            .parent()
            .ok_or_else(|| PersonalError::new(PersonalErrorCode::UnsafePath, "invalid store"))?;
        let space = probe_filesystem_space(probe).map_err(|error| {
            PersonalError::new(
                PersonalErrorCode::Io,
                format!("Could not inspect personal snapshot storage: {error}"),
            )
        })?;
        if space.available_bytes < self.minimum_free_bytes {
            return Err(PersonalError::new(
                PersonalErrorCode::InsufficientSpace,
                format!(
                    "Personal snapshot requires {} free bytes; only {} are available",
                    self.minimum_free_bytes, space.available_bytes
                ),
            ));
        }
        Ok(())
    }

    fn ensure_directories(&self) -> Result<(), PersonalError> {
        ensure_directory(&self.store_root, 0o700)?;
        ensure_directory(&self.personal_root(), 0o700)?;
        ensure_directory(&self.metadata_root(), 0o700)?;
        ensure_directory(&self.snapshots_root(), 0o700)?;
        ensure_directory(&self.personal_root().join("browse-locks"), 0o700)
    }

    fn write_record(&self, record: &PersonalSnapshotRecord) -> Result<(), PersonalError> {
        record.validate()?;
        let serialized = serde_json::to_vec_pretty(record).map_err(|error| {
            PersonalError::invalid(format!("Could not serialize record: {error}"))
        })?;
        if serialized.len() as u64 > MAX_METADATA_BYTES {
            return Err(PersonalError::invalid(
                "Personal snapshot metadata is too large",
            ));
        }
        let target = self.metadata_path(record.id);
        reject_non_regular_target(&target)?;
        let temporary = self.metadata_root().join(format!(
            ".{}.{}.tmp",
            record.id,
            Uuid::new_v4().hyphenated()
        ));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)
            .map_err(|error| personal_io("Could not create personal metadata", error))?;
        let result = (|| -> io::Result<()> {
            file.write_all(&serialized)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            fs::rename(&temporary, &target)?;
            sync_directory_raw(&self.metadata_root())
        })();
        if let Err(error) = result {
            let _ = fs::remove_file(&temporary);
            return Err(personal_io("Could not commit personal metadata", error));
        }
        Ok(())
    }

    fn read_record_path(&self, path: &Path) -> Result<PersonalSnapshotRecord, PersonalError> {
        let metadata = fs::symlink_metadata(path)
            .map_err(|error| personal_io("Could not inspect personal metadata", error))?;
        if !metadata.file_type().is_file() || metadata.len() > MAX_METADATA_BYTES {
            return Err(PersonalError::new(
                PersonalErrorCode::UnsafePath,
                "Personal snapshot metadata is not a bounded regular file",
            ));
        }
        let stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .ok_or_else(|| PersonalError::invalid("Invalid personal metadata filename"))?;
        let id = stem
            .parse::<PersonalSnapshotId>()
            .map_err(|_| PersonalError::invalid("Invalid personal metadata filename"))?;
        if id.to_string() != stem {
            return Err(PersonalError::invalid("Non-canonical personal snapshot ID"));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)
            .map_err(|error| personal_io("Could not open personal metadata", error))?;
        let mut contents = Vec::new();
        Read::by_ref(&mut file)
            .take(MAX_METADATA_BYTES + 1)
            .read_to_end(&mut contents)
            .map_err(|error| personal_io("Could not read personal metadata", error))?;
        if contents.len() as u64 > MAX_METADATA_BYTES {
            return Err(PersonalError::invalid("Personal metadata is too large"));
        }
        let record: PersonalSnapshotRecord =
            serde_json::from_slice(&contents).map_err(|error| {
                PersonalError::invalid(format!("Invalid personal metadata: {error}"))
            })?;
        record.validate()?;
        if record.id != id {
            return Err(PersonalError::invalid(
                "Personal snapshot ID does not match its filename",
            ));
        }
        Ok(record)
    }

    fn snapshot_identity(&self, path: &Path) -> Result<(String, Option<String>), PersonalError> {
        let output = self.run_btrfs(&[
            OsString::from("subvolume"),
            OsString::from("show"),
            path.as_os_str().to_owned(),
        ])?;
        let output = String::from_utf8(output)
            .map_err(|_| PersonalError::invalid("Btrfs returned a non-UTF-8 identity"))?;
        let mut uuid = None;
        let mut parent = None;
        for line in output.lines() {
            if let Some(value) = line.trim().strip_prefix("UUID:") {
                uuid = Some(value.trim().to_string());
            } else if let Some(value) = line.trim().strip_prefix("Parent UUID:") {
                let value = value.trim();
                if value != "-" {
                    parent = Some(value.to_string());
                }
            }
        }
        let uuid = uuid.ok_or_else(|| PersonalError::invalid("Btrfs reported no UUID"))?;
        if Uuid::parse_str(&uuid).is_err()
            || parent
                .as_deref()
                .is_some_and(|value| Uuid::parse_str(value).is_err())
        {
            return Err(PersonalError::invalid("Btrfs reported an invalid UUID"));
        }
        Ok((uuid, parent))
    }

    fn run_btrfs(&self, arguments: &[OsString]) -> Result<Vec<u8>, PersonalError> {
        self.runner
            .run(Path::new(BTRFS), arguments)
            .map(|output| output.stdout)
            .map_err(|error| {
                PersonalError::new(PersonalErrorCode::CommandFailed, error.to_string())
            })
    }

    fn personal_root(&self) -> PathBuf {
        self.store_root.join("personal")
    }

    fn metadata_root(&self) -> PathBuf {
        self.personal_root().join("metadata")
    }

    fn snapshots_root(&self) -> PathBuf {
        self.personal_root().join("snapshots")
    }

    fn metadata_path(&self, id: PersonalSnapshotId) -> PathBuf {
        self.metadata_root().join(format!("{id}.json"))
    }

    fn snapshot_container(&self, id: PersonalSnapshotId) -> PathBuf {
        self.snapshots_root().join(id.to_string())
    }

    pub fn snapshot_path(&self, id: PersonalSnapshotId) -> PathBuf {
        self.snapshot_container(id).join("home")
    }

    pub fn import_staging_root(&self) -> PathBuf {
        self.personal_root().join("import-staging")
    }

    pub fn prepare_import_staging(&self, layout: &LayoutReport) -> Result<PathBuf, PersonalError> {
        ensure_supported(layout)?;
        self.ensure_space()?;
        self.ensure_directories()?;
        let path = self.import_staging_root();
        ensure_directory(&path, 0o700)?;
        Ok(path)
    }

    fn browse_lock_path(&self, id: PersonalSnapshotId) -> PathBuf {
        self.personal_root()
            .join("browse-locks")
            .join(format!("{id}.lock"))
    }
}

pub struct PersonalSnapshotBrowser {
    _snapshot_root: File,
    user_root: File,
    _lease: StoreLock,
}

impl PersonalSnapshotBrowser {
    pub fn list(&self, relative_path: &str) -> Result<Vec<PersonalDirectoryEntry>, PersonalError> {
        validate_relative_path(relative_path, true)?;
        let directory = if relative_path.is_empty() {
            duplicate_file(&self.user_root)?
        } else {
            open_beneath(
                self.user_root.as_raw_fd(),
                Path::new(relative_path),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
            )?
        };
        list_directory_fd(directory.as_raw_fd())
    }

    pub fn open_file(&self, relative_path: &str) -> Result<File, PersonalError> {
        validate_relative_path(relative_path, false)?;
        let file = open_beneath(
            self.user_root.as_raw_fd(),
            Path::new(relative_path),
            libc::O_RDONLY | libc::O_CLOEXEC,
        )?;
        let metadata = file
            .metadata()
            .map_err(|error| personal_io("Could not inspect exported personal file", error))?;
        if !metadata.file_type().is_file() {
            return Err(PersonalError::new(
                PersonalErrorCode::UnsafePath,
                "Only regular personal files can be exported",
            ));
        }
        Ok(file)
    }
}

struct StoreLock(File);

impl StoreLock {
    fn acquire(path: &Path) -> Result<Self, PersonalError> {
        Self::open(path, libc::LOCK_EX, false)
    }

    fn acquire_nonblocking(path: &Path) -> Result<Self, PersonalError> {
        Self::open(path, libc::LOCK_EX, true)
    }

    fn acquire_shared(path: &Path) -> Result<Self, PersonalError> {
        Self::open(path, libc::LOCK_SH, false)
    }

    fn open(path: &Path, mode: libc::c_int, nonblocking: bool) -> Result<Self, PersonalError> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)
            .map_err(|error| personal_io("Could not open personal snapshot lock", error))?;
        let flags = mode | if nonblocking { libc::LOCK_NB } else { 0 };
        if unsafe { libc::flock(file.as_raw_fd(), flags) } != 0 {
            let error = io::Error::last_os_error();
            return Err(
                if nonblocking && error.raw_os_error() == Some(libc::EWOULDBLOCK) {
                    PersonalError::new(
                        PersonalErrorCode::Busy,
                        "Personal snapshot is being browsed",
                    )
                } else {
                    personal_io("Could not lock personal snapshot", error)
                },
            );
        }
        Ok(Self(file))
    }
}

impl Drop for StoreLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

#[repr(C)]
struct OpenHow {
    flags: u64,
    mode: u64,
    resolve: u64,
}

const RESOLVE_NO_XDEV: u64 = 0x01;
const RESOLVE_NO_MAGICLINKS: u64 = 0x02;
const RESOLVE_NO_SYMLINKS: u64 = 0x04;
const RESOLVE_BENEATH: u64 = 0x08;

fn open_beneath(directory_fd: RawFd, path: &Path, flags: i32) -> Result<File, PersonalError> {
    let bytes = path.as_os_str().as_bytes();
    if bytes.contains(&0) {
        return Err(PersonalError::invalid("Personal path contains a NUL byte"));
    }
    let mut terminated = Vec::with_capacity(bytes.len() + 1);
    terminated.extend_from_slice(bytes);
    terminated.push(0);
    let how = OpenHow {
        flags: flags as u64,
        mode: 0,
        resolve: RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV,
    };
    let fd = unsafe {
        libc::syscall(
            libc::SYS_openat2,
            directory_fd,
            terminated.as_ptr().cast::<libc::c_char>(),
            &how,
            std::mem::size_of::<OpenHow>(),
        ) as libc::c_int
    };
    if fd < 0 {
        let error = io::Error::last_os_error();
        let code = if error.kind() == io::ErrorKind::NotFound {
            PersonalErrorCode::NotFound
        } else {
            PersonalErrorCode::UnsafePath
        };
        return Err(PersonalError::new(
            code,
            format!("Could not resolve personal snapshot path safely: {error}"),
        ));
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn list_directory_fd(fd: RawFd) -> Result<Vec<PersonalDirectoryEntry>, PersonalError> {
    let duplicate = unsafe { libc::fcntl(fd, libc::F_DUPFD_CLOEXEC, 3) };
    if duplicate < 0 {
        return Err(personal_io(
            "Could not duplicate personal directory descriptor",
            io::Error::last_os_error(),
        ));
    }
    let directory = unsafe { libc::fdopendir(duplicate) };
    if directory.is_null() {
        unsafe { libc::close(duplicate) };
        return Err(personal_io(
            "Could not inspect personal directory",
            io::Error::last_os_error(),
        ));
    }
    let mut entries = Vec::new();
    loop {
        unsafe { *libc::__errno_location() = 0 };
        let item = unsafe { libc::readdir(directory) };
        if item.is_null() {
            let error = io::Error::last_os_error();
            unsafe { libc::closedir(directory) };
            if error.raw_os_error() == Some(0) {
                break;
            }
            return Err(personal_io("Could not read personal directory", error));
        }
        let name = unsafe { CStr::from_ptr((*item).d_name.as_ptr()) };
        if name.to_bytes() == b"." || name.to_bytes() == b".." {
            continue;
        }
        let name = std::str::from_utf8(name.to_bytes())
            .map_err(|_| PersonalError::invalid("Personal filename is not valid UTF-8"))?;
        let name_c = std::ffi::CString::new(name)
            .map_err(|_| PersonalError::invalid("Personal filename contains NUL"))?;
        let mut stat = std::mem::MaybeUninit::<libc::stat>::uninit();
        if unsafe {
            libc::fstatat(
                fd,
                name_c.as_ptr(),
                stat.as_mut_ptr(),
                libc::AT_SYMLINK_NOFOLLOW,
            )
        } != 0
        {
            unsafe { libc::closedir(directory) };
            return Err(personal_io(
                "Could not inspect personal directory entry",
                io::Error::last_os_error(),
            ));
        }
        let stat = unsafe { stat.assume_init() };
        let file_type = stat.st_mode & libc::S_IFMT;
        let kind = match file_type {
            libc::S_IFREG => PersonalEntryKind::File,
            libc::S_IFDIR => PersonalEntryKind::Directory,
            _ => continue,
        };
        entries.push(PersonalDirectoryEntry {
            name: name.to_string(),
            kind,
            size: stat.st_size.max(0) as u64,
            modified_unix_seconds: stat.st_mtime,
        });
        if entries.len() > MAX_DIRECTORY_ENTRIES {
            unsafe { libc::closedir(directory) };
            return Err(PersonalError::new(
                PersonalErrorCode::InvalidInput,
                "Personal directory exceeds the entry limit",
            ));
        }
    }
    entries.sort_by(|left, right| {
        (
            left.kind != PersonalEntryKind::Directory,
            left.name.to_lowercase(),
        )
            .cmp(&(
                right.kind != PersonalEntryKind::Directory,
                right.name.to_lowercase(),
            ))
    });
    Ok(entries)
}

fn duplicate_file(file: &File) -> Result<File, PersonalError> {
    file.try_clone()
        .map_err(|error| personal_io("Could not duplicate personal snapshot descriptor", error))
}

pub fn validate_relative_path(value: &str, allow_empty: bool) -> Result<(), PersonalError> {
    if value.as_bytes().len() > MAX_RELATIVE_PATH_BYTES
        || value
            .chars()
            .any(|character| character == '\0' || character.is_control())
    {
        return Err(PersonalError::invalid("Invalid personal relative path"));
    }
    if value.is_empty() {
        return if allow_empty {
            Ok(())
        } else {
            Err(PersonalError::invalid("Personal file path is empty"))
        };
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path.components().any(|component| {
            !matches!(component, Component::Normal(_))
                || component.as_os_str().as_bytes().is_empty()
        })
    {
        return Err(PersonalError::invalid(
            "Personal path must contain only normal relative components",
        ));
    }
    Ok(())
}

fn validate_single_component(value: &str) -> Result<(), PersonalError> {
    validate_relative_path(value, false)?;
    if Path::new(value).components().count() != 1 {
        return Err(PersonalError::invalid(
            "User directory must be one component",
        ));
    }
    Ok(())
}

fn validate_label(value: &str) -> Result<(), PersonalError> {
    if value.is_empty()
        || value.len() > 50
        || value.starts_with(['-', '.'])
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(PersonalError::invalid("Invalid personal schedule ID"));
    }
    Ok(())
}

fn validate_text(value: &str, maximum: usize, field: &str) -> Result<(), PersonalError> {
    if value.trim().is_empty()
        || value.chars().count() > maximum
        || value.chars().any(char::is_control)
    {
        return Err(PersonalError::invalid(format!(
            "Invalid personal snapshot {field}"
        )));
    }
    Ok(())
}

fn ensure_supported(layout: &LayoutReport) -> Result<(), PersonalError> {
    if layout.is_supported() {
        Ok(())
    } else {
        Err(PersonalError::new(
            PersonalErrorCode::UnsupportedLayout,
            "The complete AnduinOS Btrfs layout is required",
        ))
    }
}

fn ensure_directory(path: &Path, mode: u32) -> Result<(), PersonalError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {
            if metadata.uid() != unsafe { libc::geteuid() } {
                return Err(PersonalError::new(
                    PersonalErrorCode::UnsafePath,
                    format!("{} is not owned by the recovery helper", path.display()),
                ));
            }
            fs::set_permissions(path, fs::Permissions::from_mode(mode))
                .map_err(|error| personal_io("Could not secure personal storage", error))
        }
        Ok(_) => Err(PersonalError::new(
            PersonalErrorCode::UnsafePath,
            format!("{} is not a real directory", path.display()),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir(path)
                .map_err(|error| personal_io("Could not create personal storage", error))?;
            fs::set_permissions(path, fs::Permissions::from_mode(mode))
                .map_err(|error| personal_io("Could not secure personal storage", error))
        }
        Err(error) => Err(personal_io("Could not inspect personal storage", error)),
    }
}

fn ensure_new_directory(path: &Path) -> Result<(), PersonalError> {
    fs::create_dir(path)
        .map_err(|error| personal_io("Could not create snapshot container", error))?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|error| personal_io("Could not secure snapshot container", error))
}

fn ensure_real_directory(path: &Path) -> Result<(), PersonalError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| personal_io("Could not inspect personal snapshot", error))?;
    if metadata.file_type().is_dir() {
        Ok(())
    } else {
        Err(PersonalError::new(
            PersonalErrorCode::UnsafePath,
            "Personal snapshot is not a real directory",
        ))
    }
}

fn reject_non_regular_target(path: &Path) -> Result<(), PersonalError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() => Ok(()),
        Ok(_) => Err(PersonalError::new(
            PersonalErrorCode::UnsafePath,
            "Personal metadata target is not a regular file",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(personal_io(
            "Could not inspect personal metadata target",
            error,
        )),
    }
}

fn sync_directory(path: &Path) -> Result<(), PersonalError> {
    sync_directory_raw(path).map_err(|error| personal_io("Could not sync personal metadata", error))
}

fn sync_directory_raw(path: &Path) -> io::Result<()> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)?
        .sync_all()
}

fn personal_io(context: &str, error: io::Error) -> PersonalError {
    let code = if error.kind() == io::ErrorKind::NotFound {
        PersonalErrorCode::NotFound
    } else {
        PersonalErrorCode::Io
    };
    PersonalError::new(code, format!("{context}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::inspect_mountinfo;
    use crate::operations::{CommandOutput, OperationError};
    use std::sync::{Arc, Mutex};

    #[derive(Clone, Default)]
    struct RecordingRunner(Arc<Mutex<Vec<Vec<OsString>>>>);

    impl CommandRunner for RecordingRunner {
        fn run(
            &self,
            _program: &Path,
            arguments: &[OsString],
        ) -> Result<CommandOutput, OperationError> {
            self.0.lock().unwrap().push(arguments.to_vec());
            let text = if arguments.first().and_then(|value| value.to_str()) == Some("subvolume")
                && arguments.get(1).and_then(|value| value.to_str()) == Some("show")
            {
                "UUID: aaaaaaaa-1111-4222-8333-aaaaaaaaaaaa\nParent UUID: bbbbbbbb-1111-4222-8333-aaaaaaaaaaaa\n"
            } else if arguments.first().and_then(|value| value.to_str()) == Some("property") {
                "ro=true\n"
            } else {
                ""
            };
            Ok(CommandOutput {
                stdout: text.as_bytes().to_vec(),
            })
        }
    }

    fn supported_layout() -> LayoutReport {
        inspect_mountinfo(
            "25 1 0:32 /@root / rw - btrfs /dev/vda4 rw\n\
             26 25 0:32 /@home /home rw - btrfs /dev/vda4 rw\n\
             27 25 0:32 /@log /var/log rw - btrfs /dev/vda4 rw\n\
             28 25 0:32 /@snapshots /.snapshots rw - btrfs /dev/vda4 rw\n\
             29 25 0:32 /@containers /var/lib/containers rw - btrfs /dev/vda4 rw\n\
             30 25 0:32 /@libvirt /var/lib/libvirt/images rw - btrfs /dev/vda4 rw\n",
        )
    }

    fn temporary_root(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "anduinos-personal-{name}-{}",
            Uuid::new_v4().hyphenated()
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn relative_paths_reject_escape_and_special_components() {
        for value in ["/etc/passwd", "../secret", "folder/../secret", "./file", ""] {
            assert!(
                validate_relative_path(value, false).is_err(),
                "accepted {value:?}"
            );
        }
        assert!(validate_relative_path("Documents/report.odt", false).is_ok());
        assert!(validate_relative_path("", true).is_ok());
    }

    #[test]
    fn model_keeps_personal_history_independent() {
        let record = PersonalSnapshotRecord {
            schema_version: PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            id: PersonalSnapshotId::new(),
            kind: PersonalSnapshotKind::Automatic,
            state: PersonalSnapshotState::Ready,
            created_at: Utc::now(),
            title: "Hourly personal snapshot".into(),
            reason: "Protect deleted files".into(),
            schedule_id: Some("personal-hourly".into()),
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: Some(Uuid::new_v4().to_string()),
            pinned: false,
            failure: None,
        };
        assert!(record.validate().is_ok());
        assert!(record.can_delete());
        let mut interrupted_delete = record;
        interrupted_delete.state = PersonalSnapshotState::Deleting;
        assert!(interrupted_delete.can_delete());
        interrupted_delete.pinned = true;
        assert!(!interrupted_delete.can_delete());
    }

    #[test]
    fn create_records_a_read_only_home_snapshot_without_running_btrfs() {
        let root = temporary_root("create");
        let home = root.join("home");
        let store = root.join("store");
        fs::create_dir(&home).unwrap();
        fs::create_dir(&store).unwrap();
        let runner = RecordingRunner::default();
        let engine = PersonalSnapshotEngine::new(&home, &store, runner.clone());
        let record = engine
            .create_manual(
                &supported_layout(),
                "Before cleanup",
                "Manual history",
                false,
            )
            .unwrap();
        assert_eq!(
            fs::metadata(&store).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(record.state, PersonalSnapshotState::Ready);
        let calls = runner.0.lock().unwrap();
        assert!(calls.iter().any(|call| {
            call.get(0).and_then(|value| value.to_str()) == Some("subvolume")
                && call.get(1).and_then(|value| value.to_str()) == Some("snapshot")
                && call.get(2).and_then(|value| value.to_str()) == Some("-r")
                && call.get(3) == Some(&home.as_os_str().to_owned())
        }));
        drop(calls);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn malformed_metadata_is_quarantined_from_discovery() {
        let root = temporary_root("metadata");
        let store = root.join("store");
        fs::create_dir(&store).unwrap();
        let engine =
            PersonalSnapshotEngine::new(root.join("home"), &store, RecordingRunner::default());
        engine.ensure_directories().unwrap();
        fs::write(
            engine
                .metadata_root()
                .join(format!("{}.json", Uuid::new_v4())),
            b"{}",
        )
        .unwrap();
        let report = engine.discover();
        assert!(report.snapshots.is_empty());
        assert_eq!(report.issues.len(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn browser_is_caller_scoped_descriptor_confined_and_holds_delete_lease() {
        use std::os::unix::fs::symlink;

        let root = temporary_root("browser");
        let store = root.join("store");
        fs::create_dir(&store).unwrap();
        let engine =
            PersonalSnapshotEngine::new(root.join("home"), &store, RecordingRunner::default());
        engine.ensure_directories().unwrap();
        let id = PersonalSnapshotId::new();
        let historical = engine.snapshot_path(id).join("alice/Documents");
        fs::create_dir_all(&historical).unwrap();
        fs::write(historical.join("report.txt"), b"historical contents").unwrap();
        symlink("/etc/passwd", historical.join("escape")).unwrap();
        let record = PersonalSnapshotRecord {
            schema_version: PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            id,
            kind: PersonalSnapshotKind::Manual,
            state: PersonalSnapshotState::Ready,
            created_at: Utc::now(),
            title: "Personal history".into(),
            reason: "Before deletion".into(),
            schedule_id: None,
            snapshot_uuid: Some("aaaaaaaa-1111-4222-8333-aaaaaaaaaaaa".into()),
            snapshot_parent_uuid: Some("bbbbbbbb-1111-4222-8333-aaaaaaaaaaaa".into()),
            pinned: false,
            failure: None,
        };
        engine.write_record(&record).unwrap();
        let browser = engine.browser(&supported_layout(), id, "alice").unwrap();
        let entries = browser.list("Documents").unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "report.txt");
        let mut file = browser.open_file("Documents/report.txt").unwrap();
        let mut contents = String::new();
        file.read_to_string(&mut contents).unwrap();
        assert_eq!(contents, "historical contents");
        assert!(browser.open_file("Documents/escape").is_err());
        let busy = engine.delete(&supported_layout(), id).unwrap_err();
        assert_eq!(busy.code, PersonalErrorCode::Busy);
        drop(browser);
        fs::remove_dir_all(root).unwrap();
    }
}
