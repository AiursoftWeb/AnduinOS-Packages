//! Trusted external-backup identities and on-disk metadata.
//!
//! The public boundary intentionally accepts filesystem and backup IDs, never
//! caller-selected paths. A privileged adapter must resolve the filesystem ID
//! through `/dev/disk/by-uuid`, then match the block-device identity against
//! its own mount namespace before using the returned mount point.

use std::ffi::OsString;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::RECOVERY_STORE_ROOT;
use crate::layout::LayoutReport;
use crate::model::DeploymentRecord;
use crate::operations::{OperationEngine, OperationError};
use crate::space::{
    MINIMUM_TRANSACTION_RESERVE_BYTES, parse_qgroup_for_subvolume, probe_filesystem_space,
};

pub const BACKUP_SCHEMA_VERSION: u32 = 1;
pub const BACKUP_DIRECTORY_NAME: &str = ".anduinos-waypoint";
pub const BACKUP_STREAM_NAME: &str = "root.btrfs";
pub const BACKUP_MANIFEST_NAME: &str = "manifest.json";
pub const MAX_MANIFEST_BYTES: u64 = 64 * 1024;

const UUID_DIRECTORY: &str = "/dev/disk/by-uuid";
const MOUNTINFO: &str = "/proc/self/mountinfo";
const BTRFS: &str = "/usr/bin/btrfs";
const MAX_COMMAND_OUTPUT_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct BackupId(Uuid);

impl BackupId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for BackupId {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Display for BackupId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.hyphenated().fmt(formatter)
    }
}

impl FromStr for BackupId {
    type Err = uuid::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Uuid::parse_str(value).map(Self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BackupFormat {
    FullBtrfsSendV1,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BackupManifest {
    pub schema_version: u32,
    pub backup_id: BackupId,
    pub created_at: DateTime<Utc>,
    pub format: BackupFormat,
    pub source: DeploymentRecord,
    pub stream_sha256: String,
    pub stream_size_bytes: u64,
    pub referenced_bytes: u64,
}

impl BackupManifest {
    pub fn validate(&self) -> Result<(), ExternalBackupError> {
        if self.schema_version != BACKUP_SCHEMA_VERSION {
            return Err(ExternalBackupError::InvalidManifest(format!(
                "unsupported backup schema {}",
                self.schema_version
            )));
        }
        self.source.validate().map_err(|error| {
            ExternalBackupError::InvalidManifest(format!(
                "invalid source recovery metadata: {error}"
            ))
        })?;
        if !self.source.can_restore() {
            return Err(ExternalBackupError::InvalidManifest(
                "source recovery point is not restorable".into(),
            ));
        }
        if self.stream_size_bytes == 0 || self.referenced_bytes == 0 {
            return Err(ExternalBackupError::InvalidManifest(
                "backup byte counts must be non-zero".into(),
            ));
        }
        if !is_sha256(&self.stream_sha256) {
            return Err(ExternalBackupError::InvalidManifest(
                "stream digest is not a lowercase SHA-256 value".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MountInfoEntry {
    pub device_major: u64,
    pub device_minor: u64,
    pub mount_point: PathBuf,
    pub writable: bool,
    pub filesystem_type: String,
    pub source: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BackupDestination {
    pub filesystem_uuid: String,
    pub device_major: u64,
    pub device_minor: u64,
    pub mount_point: PathBuf,
    pub filesystem_type: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct BackupDiscovery {
    pub backups: Vec<BackupManifest>,
    pub issues: Vec<BackupDiscoveryIssue>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BackupDiscoveryIssue {
    pub entry: String,
    pub message: String,
}

impl BackupDestination {
    pub fn backup_root(&self) -> PathBuf {
        self.mount_point.join(BACKUP_DIRECTORY_NAME).join("backups")
    }

    pub fn backup_directory(&self, backup_id: BackupId) -> PathBuf {
        self.backup_root().join(backup_id.to_string())
    }
}

#[derive(Debug)]
pub enum ExternalBackupError {
    InvalidFilesystemUuid,
    FilesystemNotFound,
    NotBlockDevice,
    NotMounted,
    AmbiguousMount,
    ReadOnly,
    UnsafeMountPoint,
    SystemFilesystem,
    UnsupportedFilesystem(String),
    InvalidMountInfo(String),
    InvalidManifest(String),
    UnsafeStorage(String),
    InsufficientSpace { available: u64, required: u64 },
    CommandFailed(String),
    Recovery(String),
    Io(std::io::Error),
}

impl fmt::Display for ExternalBackupError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidFilesystemUuid => formatter.write_str("invalid filesystem UUID"),
            Self::FilesystemNotFound => formatter.write_str("filesystem UUID was not found"),
            Self::NotBlockDevice => formatter.write_str("filesystem UUID is not a block device"),
            Self::NotMounted => formatter.write_str("external filesystem is not mounted"),
            Self::AmbiguousMount => {
                formatter.write_str("external filesystem has multiple eligible mount points")
            }
            Self::ReadOnly => formatter.write_str("external filesystem is mounted read-only"),
            Self::UnsafeMountPoint => formatter.write_str("external mount point is not trusted"),
            Self::SystemFilesystem => {
                formatter.write_str("AnduinOS system storage cannot be a backup destination")
            }
            Self::UnsupportedFilesystem(kind) => {
                write!(formatter, "unsupported external filesystem: {kind}")
            }
            Self::InvalidMountInfo(message) => write!(formatter, "invalid mountinfo: {message}"),
            Self::InvalidManifest(message) => {
                write!(formatter, "invalid backup manifest: {message}")
            }
            Self::UnsafeStorage(message) => write!(formatter, "unsafe backup storage: {message}"),
            Self::InsufficientSpace {
                available,
                required,
            } => write!(
                formatter,
                "backup operation requires {required} bytes but only {available} bytes are available"
            ),
            Self::CommandFailed(message) => write!(formatter, "backup command failed: {message}"),
            Self::Recovery(message) => write!(formatter, "recovery operation failed: {message}"),
            Self::Io(error) => error.fmt(formatter),
        }
    }
}

impl From<OperationError> for ExternalBackupError {
    fn from(value: OperationError) -> Self {
        Self::Recovery(value.to_string())
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ExternalBackupManager;

impl ExternalBackupManager {
    pub fn list_destinations(&self) -> Result<Vec<BackupDestination>, ExternalBackupError> {
        let mut destinations = Vec::new();
        for entry in fs::read_dir(UUID_DIRECTORY)? {
            let entry = entry?;
            let Some(uuid) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            if let Ok(destination) = resolve_backup_destination(&uuid) {
                destinations.push(destination);
            }
        }
        destinations.sort_by(|left, right| left.filesystem_uuid.cmp(&right.filesystem_uuid));
        destinations.dedup_by(|left, right| left.filesystem_uuid == right.filesystem_uuid);
        Ok(destinations)
    }

    pub fn export(
        &self,
        layout: &LayoutReport,
        deployment_id: crate::model::DeploymentId,
        filesystem_uuid: &str,
    ) -> Result<BackupManifest, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        let engine = OperationEngine::default();
        let source = engine.verify(layout, deployment_id, |_phase, _fraction, _message| {})?;
        let snapshot = Path::new(RECOVERY_STORE_ROOT)
            .join("deployments")
            .join(deployment_id.to_string())
            .join("root");
        let referenced_bytes = referenced_bytes(&snapshot)?;
        require_space(
            &destination.mount_point,
            referenced_bytes.saturating_add(MINIMUM_TRANSACTION_RESERVE_BYTES),
        )?;
        ensure_backup_storage(&destination)?;

        let backup_id = BackupId::new();
        let backup_root = destination.backup_root();
        let final_directory = destination.backup_directory(backup_id);
        let temporary_directory = backup_root.join(format!(
            ".{}.{}.partial",
            backup_id,
            Uuid::new_v4().hyphenated()
        ));
        create_storage_directory(&temporary_directory, &destination.filesystem_type)?;
        let stream_path = temporary_directory.join(BACKUP_STREAM_NAME);

        let result = (|| {
            let mut stream = OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .mode(0o600)
                .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
                .open(&stream_path)?;
            send_full_snapshot(&snapshot, &stream)?;
            stream.sync_all()?;
            let stream_size_bytes = stream.metadata()?.len();
            if stream_size_bytes == 0 {
                return Err(ExternalBackupError::InvalidManifest(
                    "Btrfs produced an empty send stream".into(),
                ));
            }
            let stream_sha256 = hash_open_file(&mut stream)?;
            let manifest = BackupManifest {
                schema_version: BACKUP_SCHEMA_VERSION,
                backup_id,
                created_at: Utc::now(),
                format: BackupFormat::FullBtrfsSendV1,
                source,
                stream_sha256,
                stream_size_bytes,
                referenced_bytes,
            };
            manifest.validate()?;
            write_manifest(&temporary_directory, &manifest)?;
            sync_directory(&temporary_directory)?;
            validate_destination(&destination)?;
            fs::rename(&temporary_directory, &final_directory)?;
            sync_directory(&backup_root)?;
            Ok(manifest)
        })();

        if result.is_err() {
            let _ = remove_known_backup_directory(&temporary_directory);
        }
        result
    }

    pub fn discover(&self, filesystem_uuid: &str) -> Result<BackupDiscovery, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        let root = destination.backup_root();
        match fs::symlink_metadata(&root) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(BackupDiscovery::default());
            }
            Err(error) => return Err(error.into()),
            Ok(metadata) if !metadata.file_type().is_dir() => {
                return Err(ExternalBackupError::UnsafeStorage(
                    "backup root is not a real directory".into(),
                ));
            }
            Ok(_) => {}
        }
        let mut report = BackupDiscovery::default();
        for entry in fs::read_dir(&root)? {
            let entry = entry?;
            let value = entry.file_name();
            let Some(value) = value.to_str() else {
                report.issues.push(BackupDiscoveryIssue {
                    entry: "non-UTF-8 entry".into(),
                    message: "Backup directory name is not UTF-8".into(),
                });
                continue;
            };
            if value.starts_with('.') {
                continue;
            }
            let Ok(backup_id) = value.parse::<BackupId>() else {
                report.issues.push(BackupDiscoveryIssue {
                    entry: safe_entry(value),
                    message: "Backup directory name is not a UUID".into(),
                });
                continue;
            };
            match read_backup_at(&destination, backup_id, false) {
                Ok(manifest) => report.backups.push(manifest),
                Err(error) => report.issues.push(BackupDiscoveryIssue {
                    entry: backup_id.to_string(),
                    message: error.to_string(),
                }),
            }
        }
        report
            .backups
            .sort_by_key(|item| std::cmp::Reverse(item.created_at));
        Ok(report)
    }

    pub fn verify(
        &self,
        filesystem_uuid: &str,
        backup_id: BackupId,
    ) -> Result<BackupManifest, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        read_backup_at(&destination, backup_id, true)
    }

    pub fn import(
        &self,
        layout: &LayoutReport,
        filesystem_uuid: &str,
        backup_id: BackupId,
    ) -> Result<DeploymentRecord, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        let manifest = read_backup_at(&destination, backup_id, true)?;
        let recovery_parent = Path::new(RECOVERY_STORE_ROOT).parent().ok_or_else(|| {
            ExternalBackupError::UnsafeStorage("recovery store has no parent".into())
        })?;
        require_space(
            recovery_parent,
            manifest
                .referenced_bytes
                .saturating_add(MINIMUM_TRANSACTION_RESERVE_BYTES),
        )?;
        let stream_path = destination
            .backup_directory(backup_id)
            .join(BACKUP_STREAM_NAME);
        let mut stream = open_regular_file(&stream_path)?;
        let record = OperationEngine::default().import_full_stream(
            layout,
            &mut stream,
            &manifest.source,
            |_phase, _fraction, _message| {},
        )?;
        Ok(record)
    }

    pub fn delete(
        &self,
        filesystem_uuid: &str,
        backup_id: BackupId,
    ) -> Result<(), ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        read_backup_at(&destination, backup_id, false)?;
        let directory = destination.backup_directory(backup_id);
        remove_known_backup_directory(&directory)?;
        sync_directory(&destination.backup_root())?;
        Ok(())
    }
}

pub(crate) fn validate_destination(
    destination: &BackupDestination,
) -> Result<(), ExternalBackupError> {
    let refreshed = resolve_backup_destination(&destination.filesystem_uuid)?;
    if &refreshed != destination {
        return Err(ExternalBackupError::UnsafeStorage(
            "external mount identity changed during the operation".into(),
        ));
    }
    let canonical = fs::canonicalize(&destination.mount_point)?;
    if canonical != destination.mount_point {
        return Err(ExternalBackupError::UnsafeMountPoint);
    }
    let metadata = fs::symlink_metadata(&destination.mount_point)?;
    if !metadata.file_type().is_dir() {
        return Err(ExternalBackupError::UnsafeMountPoint);
    }
    Ok(())
}

fn ensure_backup_storage(destination: &BackupDestination) -> Result<(), ExternalBackupError> {
    let application_root = destination.mount_point.join(BACKUP_DIRECTORY_NAME);
    ensure_storage_directory(&application_root, &destination.filesystem_type)?;
    ensure_storage_directory(
        &application_root.join("backups"),
        &destination.filesystem_type,
    )
}

pub(crate) fn ensure_storage_directory(
    path: &Path,
    filesystem: &str,
) -> Result<(), ExternalBackupError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => validate_storage_directory(path, &metadata, filesystem),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            create_storage_directory(path, filesystem)
        }
        Err(error) => Err(error.into()),
    }
}

pub(crate) fn create_storage_directory(
    path: &Path,
    filesystem: &str,
) -> Result<(), ExternalBackupError> {
    fs::create_dir(path)?;
    if is_posix_filesystem(filesystem) {
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    let metadata = fs::symlink_metadata(path)?;
    validate_storage_directory(path, &metadata, filesystem)
}

pub(crate) fn validate_storage_directory(
    path: &Path,
    metadata: &fs::Metadata,
    filesystem: &str,
) -> Result<(), ExternalBackupError> {
    if !metadata.file_type().is_dir() {
        return Err(ExternalBackupError::UnsafeStorage(format!(
            "{} is not a real directory",
            path.display()
        )));
    }
    if is_posix_filesystem(filesystem) && (metadata.uid() != 0 || metadata.mode() & 0o022 != 0) {
        return Err(ExternalBackupError::UnsafeStorage(format!(
            "{} is not owned and protected by root",
            path.display()
        )));
    }
    Ok(())
}

fn is_posix_filesystem(filesystem: &str) -> bool {
    matches!(filesystem, "btrfs" | "ext4" | "xfs")
}

pub(crate) fn require_space(path: &Path, required: u64) -> Result<(), ExternalBackupError> {
    let space = probe_filesystem_space(path)?;
    if space.available_bytes < required {
        return Err(ExternalBackupError::InsufficientSpace {
            available: space.available_bytes,
            required,
        });
    }
    Ok(())
}

pub(crate) fn referenced_bytes(snapshot: &Path) -> Result<u64, ExternalBackupError> {
    let root_id = run_btrfs_text(&[
        OsString::from("inspect-internal"),
        OsString::from("rootid"),
        snapshot.as_os_str().to_owned(),
    ])?
    .trim()
    .parse::<u64>()
    .map_err(|_| {
        ExternalBackupError::CommandFailed("btrfs returned an invalid subvolume ID".into())
    })?;
    let qgroups = run_btrfs_text(&[
        OsString::from("qgroup"),
        OsString::from("show"),
        OsString::from("--raw"),
        snapshot.as_os_str().to_owned(),
    ])?;
    parse_qgroup_for_subvolume(&qgroups, root_id)
        .and_then(|space| space.referenced_bytes)
        .filter(|bytes| *bytes != 0)
        .ok_or_else(|| {
            ExternalBackupError::CommandFailed(
                "Btrfs qgroup accounting is required to size an external backup".into(),
            )
        })
}

fn run_btrfs_text(arguments: &[OsString]) -> Result<String, ExternalBackupError> {
    let output = Command::new(BTRFS)
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()?;
    if !output.status.success() {
        return Err(ExternalBackupError::CommandFailed(format!(
            "btrfs exited with {}{}",
            output.status,
            diagnostic_suffix(&output.stderr)
        )));
    }
    if output.stdout.len() > MAX_COMMAND_OUTPUT_BYTES {
        return Err(ExternalBackupError::CommandFailed(
            "btrfs returned excessive output".into(),
        ));
    }
    String::from_utf8(output.stdout)
        .map_err(|_| ExternalBackupError::CommandFailed("btrfs returned non-UTF-8 output".into()))
}

pub(crate) fn send_full_snapshot(
    snapshot: &Path,
    stream: &File,
) -> Result<(), ExternalBackupError> {
    let output = Command::new(BTRFS)
        .args(["send", "--proto", "1"])
        .arg(snapshot)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdout(Stdio::from(stream.try_clone()?))
        .output()?;
    if !output.status.success() {
        return Err(ExternalBackupError::CommandFailed(format!(
            "btrfs send exited with {}{}",
            output.status,
            diagnostic_suffix(&output.stderr)
        )));
    }
    Ok(())
}

fn write_manifest(directory: &Path, manifest: &BackupManifest) -> Result<(), ExternalBackupError> {
    let serialized = serde_json::to_vec_pretty(manifest).map_err(|error| {
        ExternalBackupError::InvalidManifest(format!("could not serialize manifest: {error}"))
    })?;
    if serialized.len() as u64 > MAX_MANIFEST_BYTES {
        return Err(ExternalBackupError::InvalidManifest(
            "serialized manifest exceeds its size limit".into(),
        ));
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(directory.join(BACKUP_MANIFEST_NAME))?;
    file.write_all(&serialized)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    Ok(())
}

fn read_backup_at(
    destination: &BackupDestination,
    backup_id: BackupId,
    verify_checksum: bool,
) -> Result<BackupManifest, ExternalBackupError> {
    let directory = destination.backup_directory(backup_id);
    let directory_metadata = fs::symlink_metadata(&directory)?;
    validate_storage_directory(
        &directory,
        &directory_metadata,
        &destination.filesystem_type,
    )?;
    let mut entries = fs::read_dir(&directory)?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|entry| entry.file_name())
        .collect::<Vec<_>>();
    entries.sort();
    let mut expected: Vec<OsString> = vec![BACKUP_MANIFEST_NAME.into(), BACKUP_STREAM_NAME.into()];
    expected.sort();
    if entries != expected {
        return Err(ExternalBackupError::UnsafeStorage(
            "backup directory contains unexpected entries".into(),
        ));
    }

    let manifest_path = directory.join(BACKUP_MANIFEST_NAME);
    let mut manifest_file = open_regular_file(&manifest_path)?;
    let manifest_size = manifest_file.metadata()?.len();
    if manifest_size == 0 || manifest_size > MAX_MANIFEST_BYTES {
        return Err(ExternalBackupError::InvalidManifest(
            "manifest size is outside its safety limit".into(),
        ));
    }
    let mut bytes = Vec::with_capacity(manifest_size as usize);
    manifest_file.read_to_end(&mut bytes)?;
    let manifest: BackupManifest = serde_json::from_slice(&bytes).map_err(|error| {
        ExternalBackupError::InvalidManifest(format!("could not parse manifest: {error}"))
    })?;
    manifest.validate()?;
    if manifest.backup_id != backup_id {
        return Err(ExternalBackupError::InvalidManifest(
            "manifest ID does not match its directory".into(),
        ));
    }

    let stream_path = directory.join(BACKUP_STREAM_NAME);
    let mut stream = open_regular_file(&stream_path)?;
    if stream.metadata()?.len() != manifest.stream_size_bytes {
        return Err(ExternalBackupError::InvalidManifest(
            "stream size does not match the manifest".into(),
        ));
    }
    if verify_checksum && hash_open_file(&mut stream)? != manifest.stream_sha256 {
        return Err(ExternalBackupError::InvalidManifest(
            "stream checksum does not match the manifest".into(),
        ));
    }
    Ok(manifest)
}

pub(crate) fn open_regular_file(path: &Path) -> Result<File, ExternalBackupError> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)?;
    if !file.metadata()?.file_type().is_file() {
        return Err(ExternalBackupError::UnsafeStorage(format!(
            "{} is not a regular file",
            path.display()
        )));
    }
    Ok(file)
}

pub(crate) fn hash_open_file(file: &mut File) -> Result<String, ExternalBackupError> {
    file.seek(SeekFrom::Start(0))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn remove_known_backup_directory(directory: &Path) -> Result<(), ExternalBackupError> {
    match fs::symlink_metadata(directory) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.into()),
        Ok(metadata) if !metadata.file_type().is_dir() => {
            return Err(ExternalBackupError::UnsafeStorage(
                "backup cleanup target is not a real directory".into(),
            ));
        }
        Ok(_) => {}
    }
    let entries = fs::read_dir(directory)?.collect::<Result<Vec<_>, _>>()?;
    for entry in &entries {
        let name = entry.file_name();
        if name != BACKUP_STREAM_NAME && name != BACKUP_MANIFEST_NAME {
            return Err(ExternalBackupError::UnsafeStorage(
                "backup cleanup refused an unexpected directory entry".into(),
            ));
        }
        let metadata = fs::symlink_metadata(entry.path())?;
        if !metadata.file_type().is_file() {
            return Err(ExternalBackupError::UnsafeStorage(
                "backup cleanup refused a non-regular entry".into(),
            ));
        }
    }
    for name in [BACKUP_STREAM_NAME, BACKUP_MANIFEST_NAME] {
        match fs::remove_file(directory.join(name)) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    fs::remove_dir(directory)?;
    Ok(())
}

pub(crate) fn sync_directory(path: &Path) -> Result<(), ExternalBackupError> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)?
        .sync_all()?;
    Ok(())
}

fn safe_entry(value: &str) -> String {
    value
        .chars()
        .take(120)
        .map(|character| {
            if character.is_control() {
                '�'
            } else {
                character
            }
        })
        .collect()
}

fn diagnostic_suffix(stderr: &[u8]) -> String {
    let text = String::from_utf8_lossy(&stderr[..stderr.len().min(2000)]);
    let safe = text
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .collect::<String>();
    let safe = safe.trim();
    if safe.is_empty() {
        String::new()
    } else {
        format!(": {safe}")
    }
}

impl std::error::Error for ExternalBackupError {}

impl From<std::io::Error> for ExternalBackupError {
    fn from(value: std::io::Error) -> Self {
        Self::Io(value)
    }
}

/// Resolve a caller-supplied filesystem identity without accepting a path.
pub fn resolve_backup_destination(
    filesystem_uuid: &str,
) -> Result<BackupDestination, ExternalBackupError> {
    resolve_backup_destination_at(
        filesystem_uuid,
        Path::new(UUID_DIRECTORY),
        Path::new(MOUNTINFO),
    )
}

fn resolve_backup_destination_at(
    filesystem_uuid: &str,
    uuid_directory: &Path,
    mountinfo_path: &Path,
) -> Result<BackupDestination, ExternalBackupError> {
    validate_filesystem_uuid(filesystem_uuid)?;
    let link = uuid_directory.join(filesystem_uuid);
    let device = fs::canonicalize(&link).map_err(|error| {
        if error.kind() == std::io::ErrorKind::NotFound {
            ExternalBackupError::FilesystemNotFound
        } else {
            ExternalBackupError::Io(error)
        }
    })?;
    if !device.starts_with("/dev") && uuid_directory == Path::new(UUID_DIRECTORY) {
        return Err(ExternalBackupError::NotBlockDevice);
    }
    let device_metadata = fs::metadata(&device)?;
    if uuid_directory == Path::new(UUID_DIRECTORY)
        && device_metadata.mode() & libc::S_IFMT != libc::S_IFBLK
    {
        return Err(ExternalBackupError::NotBlockDevice);
    }
    let device_number = if uuid_directory == Path::new(UUID_DIRECTORY) {
        device_metadata.rdev()
    } else {
        // Unit tests use a regular file whose st_dev identifies its fixture
        // filesystem. Production always takes the block-device branch above.
        device_metadata.dev()
    };
    let device_major = libc::major(device_number) as u64;
    let device_minor = libc::minor(device_number) as u64;

    let mountinfo = fs::read_to_string(mountinfo_path)?;
    let mut matching = parse_mountinfo(&mountinfo)?
        .into_iter()
        .filter(|entry| mount_entry_matches_device(entry, &device, device_major, device_minor))
        .collect::<Vec<_>>();
    if matching.is_empty() {
        return Err(ExternalBackupError::NotMounted);
    }
    if matching
        .iter()
        .any(|entry| is_anduinos_system_mount(&entry.mount_point))
    {
        return Err(ExternalBackupError::SystemFilesystem);
    }
    matching.retain(|entry| is_trusted_external_mount(&entry.mount_point));
    if matching.is_empty() {
        return Err(ExternalBackupError::UnsafeMountPoint);
    }
    if matching.len() != 1 {
        return Err(ExternalBackupError::AmbiguousMount);
    }
    let entry = matching.pop().expect("one matching mount");
    if !entry.writable {
        return Err(ExternalBackupError::ReadOnly);
    }
    if !matches!(
        entry.filesystem_type.as_str(),
        "btrfs" | "ext4" | "xfs" | "exfat" | "ntfs3"
    ) {
        return Err(ExternalBackupError::UnsupportedFilesystem(
            entry.filesystem_type,
        ));
    }
    Ok(BackupDestination {
        filesystem_uuid: filesystem_uuid.to_string(),
        device_major,
        device_minor,
        mount_point: entry.mount_point,
        filesystem_type: entry.filesystem_type,
    })
}

pub fn parse_mountinfo(input: &str) -> Result<Vec<MountInfoEntry>, ExternalBackupError> {
    input
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(parse_mountinfo_line)
        .collect()
}

fn parse_mountinfo_line(line: &str) -> Result<MountInfoEntry, ExternalBackupError> {
    let (prefix, suffix) = line.split_once(" - ").ok_or_else(|| {
        ExternalBackupError::InvalidMountInfo("missing filesystem separator".into())
    })?;
    let prefix_fields = prefix.split_whitespace().collect::<Vec<_>>();
    let suffix_fields = suffix.split_whitespace().collect::<Vec<_>>();
    if prefix_fields.len() < 6 || suffix_fields.len() < 3 {
        return Err(ExternalBackupError::InvalidMountInfo(
            "too few fields".into(),
        ));
    }
    let (major, minor) = prefix_fields[2]
        .split_once(':')
        .ok_or_else(|| ExternalBackupError::InvalidMountInfo("invalid device number".into()))?;
    let device_major = major
        .parse()
        .map_err(|_| ExternalBackupError::InvalidMountInfo("invalid device major".into()))?;
    let device_minor = minor
        .parse()
        .map_err(|_| ExternalBackupError::InvalidMountInfo("invalid device minor".into()))?;
    let mount_point = PathBuf::from(unescape_mountinfo(prefix_fields[4])?);
    validate_absolute_normal_path(&mount_point)?;
    let writable = prefix_fields[5].split(',').any(|option| option == "rw");
    let filesystem_type = suffix_fields[0].to_string();
    let source = unescape_mountinfo(suffix_fields[1])?;
    Ok(MountInfoEntry {
        device_major,
        device_minor,
        mount_point,
        writable,
        filesystem_type,
        source,
    })
}

fn mount_entry_matches_device(
    entry: &MountInfoEntry,
    expected_device: &Path,
    expected_major: u64,
    expected_minor: u64,
) -> bool {
    if entry.device_major == expected_major && entry.device_minor == expected_minor {
        return true;
    }
    let source = Path::new(&entry.source);
    source.is_absolute()
        && fs::canonicalize(source).is_ok_and(|canonical| canonical == expected_device)
}

fn unescape_mountinfo(value: &str) -> Result<String, ExternalBackupError> {
    let bytes = value.as_bytes();
    let mut result = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] != b'\\' {
            result.push(bytes[index]);
            index += 1;
            continue;
        }
        if index + 3 >= bytes.len()
            || !bytes[index + 1..=index + 3]
                .iter()
                .all(|byte| matches!(byte, b'0'..=b'7'))
        {
            return Err(ExternalBackupError::InvalidMountInfo(
                "invalid escape sequence".into(),
            ));
        }
        let decoded = (bytes[index + 1] - b'0') * 64
            + (bytes[index + 2] - b'0') * 8
            + (bytes[index + 3] - b'0');
        if decoded == 0 {
            return Err(ExternalBackupError::InvalidMountInfo(
                "NUL escape is not allowed".into(),
            ));
        }
        result.push(decoded);
        index += 4;
    }
    String::from_utf8(result)
        .map_err(|_| ExternalBackupError::InvalidMountInfo("mount point is not UTF-8".into()))
}

fn validate_filesystem_uuid(value: &str) -> Result<(), ExternalBackupError> {
    if value.is_empty()
        || value.len() > 128
        || value.starts_with('.')
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
    {
        return Err(ExternalBackupError::InvalidFilesystemUuid);
    }
    Ok(())
}

fn validate_absolute_normal_path(path: &Path) -> Result<(), ExternalBackupError> {
    if !path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::RootDir | Component::Normal(_)))
    {
        return Err(ExternalBackupError::UnsafeMountPoint);
    }
    Ok(())
}

fn is_trusted_external_mount(path: &Path) -> bool {
    [
        Path::new("/run/media"),
        Path::new("/media"),
        Path::new("/mnt"),
    ]
    .into_iter()
    .any(|root| path != root && path.starts_with(root))
}

fn is_anduinos_system_mount(path: &Path) -> bool {
    [
        Path::new("/"),
        Path::new("/home"),
        Path::new("/.snapshots"),
        Path::new("/var/log"),
        Path::new("/var/lib/containers"),
        Path::new("/var/lib/libvirt/images"),
    ]
    .contains(&path)
}

pub(crate) fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{DeploymentId, DeploymentKind, DeploymentState};

    fn record() -> DeploymentRecord {
        DeploymentRecord {
            schema_version: crate::DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind: DeploymentKind::Manual,
            state: DeploymentState::Ready,
            created_at: Utc::now(),
            title: "Before an upgrade".into(),
            reason: "Manual recovery point".into(),
            schedule_id: None,
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: None,
            kernel_release: Some("7.0.0-28-generic".into()),
            initramfs_sha256: Some("1".repeat(64)),
            boot_artifact_sha256: Some("2".repeat(64)),
            dpkg_status_sha256: Some("3".repeat(64)),
            mok_certificate_sha256: None,
            pinned: false,
            failure: None,
        }
    }

    #[test]
    fn manifest_accepts_only_complete_restorable_content() {
        let manifest = BackupManifest {
            schema_version: BACKUP_SCHEMA_VERSION,
            backup_id: BackupId::new(),
            created_at: Utc::now(),
            format: BackupFormat::FullBtrfsSendV1,
            source: record(),
            stream_sha256: "a".repeat(64),
            stream_size_bytes: 4096,
            referenced_bytes: 8192,
        };
        assert!(manifest.validate().is_ok());

        let mut invalid = manifest.clone();
        invalid.stream_sha256 = "A".repeat(64);
        assert!(matches!(
            invalid.validate(),
            Err(ExternalBackupError::InvalidManifest(_))
        ));
        invalid = manifest;
        invalid.source.state = DeploymentState::Incomplete;
        assert!(matches!(
            invalid.validate(),
            Err(ExternalBackupError::InvalidManifest(_))
        ));
    }

    #[test]
    fn manifest_json_rejects_unknown_fields() {
        let manifest = BackupManifest {
            schema_version: BACKUP_SCHEMA_VERSION,
            backup_id: BackupId::new(),
            created_at: Utc::now(),
            format: BackupFormat::FullBtrfsSendV1,
            source: record(),
            stream_sha256: "a".repeat(64),
            stream_size_bytes: 4096,
            referenced_bytes: 8192,
        };
        let mut value = serde_json::to_value(manifest).unwrap();
        value
            .as_object_mut()
            .unwrap()
            .insert("destination_path".into(), serde_json::json!("/tmp/escape"));
        assert!(serde_json::from_value::<BackupManifest>(value).is_err());
    }

    #[test]
    fn mountinfo_parser_decodes_kernel_escapes_and_options() {
        let entries = parse_mountinfo(
            "91 22 8:17 / /run/media/alice/My\\040Drive rw,nosuid,nodev - ext4 /dev/sdb1 rw\n",
        )
        .unwrap();
        assert_eq!(
            entries,
            vec![MountInfoEntry {
                device_major: 8,
                device_minor: 17,
                mount_point: PathBuf::from("/run/media/alice/My Drive"),
                writable: true,
                filesystem_type: "ext4".into(),
                source: "/dev/sdb1".into(),
            }]
        );
    }

    #[test]
    fn mountinfo_parser_rejects_malformed_or_nul_escaped_paths() {
        assert!(parse_mountinfo("not mountinfo").is_err());
        assert!(parse_mountinfo("91 22 8:17 / /run/media/a\\000b rw - ext4 /dev/sdb1 rw").is_err());
    }

    #[test]
    fn btrfs_mount_source_matches_when_superblock_number_is_anonymous() {
        let entry = MountInfoEntry {
            device_major: 0,
            device_minor: 75,
            mount_point: PathBuf::from("/mnt/Backup"),
            writable: true,
            filesystem_type: "btrfs".into(),
            source: "/dev/null".into(),
        };
        assert!(mount_entry_matches_device(
            &entry,
            Path::new("/dev/null"),
            253,
            4
        ));
    }

    #[test]
    fn mount_policy_rejects_system_and_arbitrary_locations() {
        assert!(is_trusted_external_mount(Path::new(
            "/run/media/alice/Backup"
        )));
        assert!(is_trusted_external_mount(Path::new("/media/Backup")));
        assert!(is_trusted_external_mount(Path::new("/mnt/Backup")));
        assert!(!is_trusted_external_mount(Path::new("/run/media")));
        assert!(!is_trusted_external_mount(Path::new("/tmp/Backup")));
        assert!(!is_trusted_external_mount(Path::new("/.snapshots")));
        assert!(!is_trusted_external_mount(Path::new("/home")));
        assert!(is_anduinos_system_mount(Path::new("/")));
        assert!(is_anduinos_system_mount(Path::new("/.snapshots")));
    }

    #[test]
    fn filesystem_uuid_is_a_single_safe_component() {
        for valid in ["B7F7-1969", "97fc8f18-29f7-4a86-a8d3-7eac31e51ee0"] {
            assert!(validate_filesystem_uuid(valid).is_ok());
        }
        for invalid in ["", ".", "../sda", "a/b", "uuid with spaces"] {
            assert!(validate_filesystem_uuid(invalid).is_err());
        }
    }

    #[test]
    fn backup_reader_distinguishes_listing_from_full_checksum_verification() {
        let test_root = std::env::temp_dir().join(format!(
            "anduinos-waypoint-external-test-{}",
            Uuid::new_v4().hyphenated()
        ));
        fs::create_dir(&test_root).unwrap();
        let destination = BackupDestination {
            filesystem_uuid: "TEST-0001".into(),
            device_major: 0,
            device_minor: 0,
            mount_point: test_root.clone(),
            filesystem_type: "exfat".into(),
        };
        ensure_backup_storage(&destination).unwrap();
        let backup_id = BackupId::new();
        let directory = destination.backup_directory(backup_id);
        create_storage_directory(&directory, "exfat").unwrap();
        let stream_bytes = b"a small deterministic Btrfs stream fixture";
        let stream_path = directory.join(BACKUP_STREAM_NAME);
        fs::write(&stream_path, stream_bytes).unwrap();
        let manifest = BackupManifest {
            schema_version: BACKUP_SCHEMA_VERSION,
            backup_id,
            created_at: Utc::now(),
            format: BackupFormat::FullBtrfsSendV1,
            source: record(),
            stream_sha256: format!("{:x}", Sha256::digest(stream_bytes)),
            stream_size_bytes: stream_bytes.len() as u64,
            referenced_bytes: 8192,
        };
        write_manifest(&directory, &manifest).unwrap();
        assert_eq!(
            read_backup_at(&destination, backup_id, true).unwrap(),
            manifest
        );

        fs::write(&stream_path, vec![b'x'; stream_bytes.len()]).unwrap();
        assert!(read_backup_at(&destination, backup_id, false).is_ok());
        assert!(matches!(
            read_backup_at(&destination, backup_id, true),
            Err(ExternalBackupError::InvalidManifest(_))
        ));

        remove_known_backup_directory(&directory).unwrap();
        fs::remove_dir(destination.backup_root()).unwrap();
        fs::remove_dir(test_root.join(BACKUP_DIRECTORY_NAME)).unwrap();
        fs::remove_dir(test_root).unwrap();
    }
}
