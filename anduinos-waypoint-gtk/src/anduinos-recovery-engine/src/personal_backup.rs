//! Independently verifiable full-stream backups for Personal Files history.

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::RECOVERY_STORE_ROOT;
use crate::external_backup::{
    BackupDestination, BackupDiscoveryIssue, BackupFormat, BackupId, ExternalBackupError,
    create_storage_directory, ensure_storage_directory, hash_open_file, is_sha256,
    open_regular_file, referenced_bytes, require_space, resolve_backup_destination,
    send_full_snapshot, sync_directory, validate_destination, validate_storage_directory,
};
use crate::layout::LayoutReport;
use crate::personal::{PersonalSnapshotEngine, PersonalSnapshotId, PersonalSnapshotRecord};
use crate::space::MINIMUM_TRANSACTION_RESERVE_BYTES;

pub const PERSONAL_BACKUP_SCHEMA_VERSION: u32 = 1;
pub const PERSONAL_BACKUP_STREAM_NAME: &str = "home.btrfs";
pub const PERSONAL_BACKUP_MANIFEST_NAME: &str = "manifest.json";
const PERSONAL_BACKUP_DIRECTORY: &str = "personal-backups";
const MAX_MANIFEST_BYTES: u64 = 64 * 1024;
const BTRFS: &str = "/usr/bin/btrfs";

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PersonalBackupManifest {
    pub schema_version: u32,
    pub backup_id: BackupId,
    pub created_at: DateTime<Utc>,
    pub format: BackupFormat,
    pub source: PersonalSnapshotRecord,
    pub stream_sha256: String,
    pub stream_size_bytes: u64,
    pub referenced_bytes: u64,
}

impl PersonalBackupManifest {
    pub fn validate(&self) -> Result<(), ExternalBackupError> {
        if self.schema_version != PERSONAL_BACKUP_SCHEMA_VERSION {
            return Err(ExternalBackupError::InvalidManifest(
                "unsupported Personal Files backup schema".into(),
            ));
        }
        self.source.validate().map_err(|error| {
            ExternalBackupError::InvalidManifest(format!(
                "invalid Personal Files source metadata: {error}"
            ))
        })?;
        if self.source.state != crate::personal::PersonalSnapshotState::Ready
            || self.source.snapshot_uuid.is_none()
        {
            return Err(ExternalBackupError::InvalidManifest(
                "Personal Files source is not a complete ready snapshot".into(),
            ));
        }
        if self.stream_size_bytes == 0 || self.referenced_bytes == 0 {
            return Err(ExternalBackupError::InvalidManifest(
                "Personal Files backup byte counts must be non-zero".into(),
            ));
        }
        if !is_sha256(&self.stream_sha256) {
            return Err(ExternalBackupError::InvalidManifest(
                "Personal Files stream digest is invalid".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct PersonalBackupDiscovery {
    pub backups: Vec<PersonalBackupManifest>,
    pub issues: Vec<BackupDiscoveryIssue>,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct PersonalBackupManager;

impl PersonalBackupManager {
    pub fn export(
        &self,
        layout: &LayoutReport,
        snapshot_id: PersonalSnapshotId,
        filesystem_uuid: &str,
    ) -> Result<PersonalBackupManifest, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        let engine = PersonalSnapshotEngine::default();
        let source = engine
            .verify(layout, snapshot_id)
            .map_err(|error| ExternalBackupError::Recovery(error.to_string()))?;
        let snapshot = engine.snapshot_path(snapshot_id);
        let referenced_bytes = referenced_bytes(&snapshot)?;
        require_space(
            &destination.mount_point,
            referenced_bytes.saturating_add(MINIMUM_TRANSACTION_RESERVE_BYTES),
        )?;
        let root = personal_backup_root(&destination);
        ensure_personal_storage(&destination, &root)?;
        let backup_id = BackupId::new();
        let final_directory = root.join(backup_id.to_string());
        let temporary = root.join(format!(
            ".{}.{}.partial",
            backup_id,
            Uuid::new_v4().hyphenated()
        ));
        create_storage_directory(&temporary, &destination.filesystem_type)?;
        let stream_path = temporary.join(PERSONAL_BACKUP_STREAM_NAME);
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
                    "Btrfs produced an empty Personal Files stream".into(),
                ));
            }
            let stream_sha256 = hash_open_file(&mut stream)?;
            let manifest = PersonalBackupManifest {
                schema_version: PERSONAL_BACKUP_SCHEMA_VERSION,
                backup_id,
                created_at: Utc::now(),
                format: BackupFormat::FullBtrfsSendV1,
                source,
                stream_sha256,
                stream_size_bytes,
                referenced_bytes,
            };
            manifest.validate()?;
            write_manifest(&temporary, &manifest)?;
            sync_directory(&temporary)?;
            validate_destination(&destination)?;
            fs::rename(&temporary, &final_directory)?;
            sync_directory(&root)?;
            Ok(manifest)
        })();
        if result.is_err() {
            let _ = remove_backup_directory(&temporary);
        }
        result
    }

    pub fn discover(
        &self,
        filesystem_uuid: &str,
    ) -> Result<PersonalBackupDiscovery, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        let root = personal_backup_root(&destination);
        match fs::symlink_metadata(&root) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(PersonalBackupDiscovery::default());
            }
            Err(error) => return Err(error.into()),
            Ok(metadata) => {
                validate_storage_directory(&root, &metadata, &destination.filesystem_type)?
            }
        }
        let mut report = PersonalBackupDiscovery::default();
        for entry in fs::read_dir(&root)? {
            let entry = entry?;
            let Some(value) = entry.file_name().to_str().map(str::to_string) else {
                report.issues.push(BackupDiscoveryIssue {
                    entry: "non-UTF-8 entry".into(),
                    message: "Personal backup directory name is not UTF-8".into(),
                });
                continue;
            };
            if value.starts_with('.') {
                continue;
            }
            let Ok(id) = value.parse::<BackupId>() else {
                report.issues.push(BackupDiscoveryIssue {
                    entry: value.chars().take(120).collect(),
                    message: "Personal backup directory name is not a UUID".into(),
                });
                continue;
            };
            match read_backup(&destination, id, false) {
                Ok(manifest) => report.backups.push(manifest),
                Err(error) => report.issues.push(BackupDiscoveryIssue {
                    entry: id.to_string(),
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
    ) -> Result<PersonalBackupManifest, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        read_backup(&destination, backup_id, true)
    }

    pub fn import(
        &self,
        layout: &LayoutReport,
        filesystem_uuid: &str,
        backup_id: BackupId,
    ) -> Result<PersonalSnapshotRecord, ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        let manifest = read_backup(&destination, backup_id, true)?;
        let recovery_parent = Path::new(RECOVERY_STORE_ROOT).parent().ok_or_else(|| {
            ExternalBackupError::UnsafeStorage("recovery store has no parent".into())
        })?;
        require_space(
            recovery_parent,
            manifest
                .referenced_bytes
                .saturating_add(MINIMUM_TRANSACTION_RESERVE_BYTES),
        )?;
        let engine = PersonalSnapshotEngine::default();
        let staging_root = engine
            .prepare_import_staging(layout)
            .map_err(|error| ExternalBackupError::Recovery(error.to_string()))?;
        let staging = staging_root.join(Uuid::new_v4().hyphenated().to_string());
        fs::create_dir(&staging)?;
        fs::set_permissions(&staging, fs::Permissions::from_mode(0o700))?;
        let mut stream = open_regular_file(
            &personal_backup_directory(&destination, backup_id).join(PERSONAL_BACKUP_STREAM_NAME),
        )?;
        let result = (|| {
            inspect_receive_stream(&mut stream)?;
            receive_stream(&mut stream, &staging)?;
            let received = ensure_received_home(&staging)?;
            engine
                .adopt_imported(layout, &manifest.source, &received)
                .map_err(|error| ExternalBackupError::Recovery(error.to_string()))
        })();
        let _ = cleanup_import_staging(&staging);
        result
    }

    pub fn delete(
        &self,
        filesystem_uuid: &str,
        backup_id: BackupId,
    ) -> Result<(), ExternalBackupError> {
        let destination = resolve_backup_destination(filesystem_uuid)?;
        validate_destination(&destination)?;
        read_backup(&destination, backup_id, false)?;
        let directory = personal_backup_directory(&destination, backup_id);
        remove_backup_directory(&directory)?;
        sync_directory(&personal_backup_root(&destination))
    }
}

fn personal_backup_root(destination: &BackupDestination) -> PathBuf {
    destination
        .mount_point
        .join(crate::external_backup::BACKUP_DIRECTORY_NAME)
        .join(PERSONAL_BACKUP_DIRECTORY)
}

fn personal_backup_directory(destination: &BackupDestination, id: BackupId) -> PathBuf {
    personal_backup_root(destination).join(id.to_string())
}

fn ensure_personal_storage(
    destination: &BackupDestination,
    root: &Path,
) -> Result<(), ExternalBackupError> {
    ensure_storage_directory(
        &destination
            .mount_point
            .join(crate::external_backup::BACKUP_DIRECTORY_NAME),
        &destination.filesystem_type,
    )?;
    ensure_storage_directory(root, &destination.filesystem_type)
}

fn write_manifest(
    directory: &Path,
    manifest: &PersonalBackupManifest,
) -> Result<(), ExternalBackupError> {
    let serialized = serde_json::to_vec_pretty(manifest).map_err(|error| {
        ExternalBackupError::InvalidManifest(format!("could not serialize manifest: {error}"))
    })?;
    if serialized.len() as u64 > MAX_MANIFEST_BYTES {
        return Err(ExternalBackupError::InvalidManifest(
            "Personal Files manifest exceeds its size limit".into(),
        ));
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(directory.join(PERSONAL_BACKUP_MANIFEST_NAME))?;
    file.write_all(&serialized)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    Ok(())
}

fn read_backup(
    destination: &BackupDestination,
    id: BackupId,
    checksum: bool,
) -> Result<PersonalBackupManifest, ExternalBackupError> {
    let directory = personal_backup_directory(destination, id);
    let metadata = fs::symlink_metadata(&directory)?;
    validate_storage_directory(&directory, &metadata, &destination.filesystem_type)?;
    let mut entries = fs::read_dir(&directory)?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|entry| entry.file_name())
        .collect::<Vec<_>>();
    entries.sort();
    let mut expected: Vec<OsString> = vec![
        PERSONAL_BACKUP_MANIFEST_NAME.into(),
        PERSONAL_BACKUP_STREAM_NAME.into(),
    ];
    expected.sort();
    if entries != expected {
        return Err(ExternalBackupError::UnsafeStorage(
            "Personal backup contains unexpected entries".into(),
        ));
    }
    let mut file = open_regular_file(&directory.join(PERSONAL_BACKUP_MANIFEST_NAME))?;
    if file.metadata()?.len() == 0 || file.metadata()?.len() > MAX_MANIFEST_BYTES {
        return Err(ExternalBackupError::InvalidManifest(
            "Personal Files manifest size is outside its limit".into(),
        ));
    }
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(MAX_MANIFEST_BYTES + 1)
        .read_to_end(&mut bytes)?;
    let manifest: PersonalBackupManifest = serde_json::from_slice(&bytes).map_err(|error| {
        ExternalBackupError::InvalidManifest(format!("could not parse manifest: {error}"))
    })?;
    manifest.validate()?;
    if manifest.backup_id != id {
        return Err(ExternalBackupError::InvalidManifest(
            "Personal Files manifest ID does not match its directory".into(),
        ));
    }
    let mut stream = open_regular_file(&directory.join(PERSONAL_BACKUP_STREAM_NAME))?;
    if stream.metadata()?.len() != manifest.stream_size_bytes {
        return Err(ExternalBackupError::InvalidManifest(
            "Personal Files stream size does not match its manifest".into(),
        ));
    }
    if checksum && hash_open_file(&mut stream)? != manifest.stream_sha256 {
        return Err(ExternalBackupError::InvalidManifest(
            "Personal Files stream checksum does not match its manifest".into(),
        ));
    }
    Ok(manifest)
}

fn inspect_receive_stream(stream: &mut File) -> Result<(), ExternalBackupError> {
    stream.seek(SeekFrom::Start(0))?;
    let output = Command::new(BTRFS)
        .args(["receive", "--dump"])
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdin(Stdio::from(stream.try_clone()?))
        .stdout(Stdio::null())
        .output()?;
    if !output.status.success() {
        return Err(ExternalBackupError::CommandFailed(format!(
            "Personal Files send stream inspection failed: {}",
            String::from_utf8_lossy(&output.stderr[..output.stderr.len().min(2000)]).trim()
        )));
    }
    Ok(())
}

fn receive_stream(stream: &mut File, staging: &Path) -> Result<(), ExternalBackupError> {
    stream.seek(SeekFrom::Start(0))?;
    let output = Command::new(BTRFS)
        .args(["receive", "--chroot", "--max-errors", "1"])
        .arg(staging)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdin(Stdio::from(stream.try_clone()?))
        .stdout(Stdio::null())
        .output()?;
    if !output.status.success() {
        return Err(ExternalBackupError::CommandFailed(format!(
            "Could not receive Personal Files stream: {}",
            String::from_utf8_lossy(&output.stderr[..output.stderr.len().min(2000)]).trim()
        )));
    }
    Ok(())
}

fn ensure_received_home(staging: &Path) -> Result<PathBuf, ExternalBackupError> {
    let entries = fs::read_dir(staging)?.collect::<Result<Vec<_>, _>>()?;
    if entries.len() != 1 || entries[0].file_name() != "home" {
        return Err(ExternalBackupError::UnsafeStorage(
            "Personal Files stream must contain exactly one home subvolume".into(),
        ));
    }
    let path = entries[0].path();
    if !fs::symlink_metadata(&path)?.file_type().is_dir() {
        return Err(ExternalBackupError::UnsafeStorage(
            "Received Personal Files root is not a real directory".into(),
        ));
    }
    Ok(path)
}

fn delete_received_subvolume(path: &Path) -> Result<(), ExternalBackupError> {
    let output = Command::new(BTRFS)
        .args(["subvolume", "delete", "--recursive", "--commit-after"])
        .arg(path)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()?;
    if output.status.success() {
        Ok(())
    } else {
        Err(ExternalBackupError::CommandFailed(
            "Could not clean up received Personal Files subvolume".into(),
        ))
    }
}

fn cleanup_import_staging(staging: &Path) -> Result<(), ExternalBackupError> {
    let entries = match fs::read_dir(staging) {
        Ok(entries) => entries.collect::<Result<Vec<_>, _>>()?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.into()),
    };
    for entry in entries {
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path)?;
        if metadata.file_type().is_dir() {
            if delete_received_subvolume(&path).is_err() {
                fs::remove_dir_all(&path)?;
            }
        } else {
            fs::remove_file(&path)?;
        }
    }
    match fs::remove_dir(staging) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn remove_backup_directory(directory: &Path) -> Result<(), ExternalBackupError> {
    match fs::symlink_metadata(directory) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.into()),
        Ok(metadata) if !metadata.file_type().is_dir() => {
            return Err(ExternalBackupError::UnsafeStorage(
                "Personal backup cleanup target is not a directory".into(),
            ));
        }
        Ok(_) => {}
    }
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        if entry.file_name() != PERSONAL_BACKUP_STREAM_NAME
            && entry.file_name() != PERSONAL_BACKUP_MANIFEST_NAME
        {
            return Err(ExternalBackupError::UnsafeStorage(
                "Personal backup cleanup refused an unexpected entry".into(),
            ));
        }
        if !fs::symlink_metadata(entry.path())?.file_type().is_file() {
            return Err(ExternalBackupError::UnsafeStorage(
                "Personal backup cleanup refused a non-regular entry".into(),
            ));
        }
    }
    for name in [PERSONAL_BACKUP_STREAM_NAME, PERSONAL_BACKUP_MANIFEST_NAME] {
        match fs::remove_file(directory.join(name)) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    fs::remove_dir(directory)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::personal::{PersonalSnapshotKind, PersonalSnapshotState};

    #[test]
    fn personal_manifest_requires_a_ready_source_and_digest() {
        let source = PersonalSnapshotRecord {
            schema_version: crate::PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            id: PersonalSnapshotId::new(),
            kind: PersonalSnapshotKind::Manual,
            state: PersonalSnapshotState::Ready,
            created_at: Utc::now(),
            title: "Personal history".into(),
            reason: "Before cleanup".into(),
            schedule_id: None,
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: None,
            pinned: false,
            failure: None,
        };
        let manifest = PersonalBackupManifest {
            schema_version: PERSONAL_BACKUP_SCHEMA_VERSION,
            backup_id: BackupId::new(),
            created_at: Utc::now(),
            format: BackupFormat::FullBtrfsSendV1,
            source,
            stream_sha256: "a".repeat(64),
            stream_size_bytes: 1,
            referenced_bytes: 1,
        };
        assert!(manifest.validate().is_ok());
        let mut invalid = manifest;
        invalid.stream_sha256 = "bad".into();
        assert!(invalid.validate().is_err());
    }

    #[test]
    fn personal_backup_reader_detects_stream_tampering() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-personal-backup-{}",
            Uuid::new_v4().hyphenated()
        ));
        fs::create_dir(&root).unwrap();
        let destination = BackupDestination {
            filesystem_uuid: "test-drive".into(),
            device_major: 0,
            device_minor: 0,
            mount_point: root.clone(),
            filesystem_type: "exfat".into(),
        };
        let backup_id = BackupId::new();
        let directory = personal_backup_directory(&destination, backup_id);
        fs::create_dir_all(&directory).unwrap();
        let stream_path = directory.join(PERSONAL_BACKUP_STREAM_NAME);
        fs::write(&stream_path, b"trusted stream").unwrap();
        let mut stream = open_regular_file(&stream_path).unwrap();
        let source = PersonalSnapshotRecord {
            schema_version: crate::PERSONAL_SNAPSHOT_SCHEMA_VERSION,
            id: PersonalSnapshotId::new(),
            kind: PersonalSnapshotKind::Manual,
            state: PersonalSnapshotState::Ready,
            created_at: Utc::now(),
            title: "Personal history".into(),
            reason: "Before cleanup".into(),
            schedule_id: None,
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: None,
            pinned: false,
            failure: None,
        };
        let manifest = PersonalBackupManifest {
            schema_version: PERSONAL_BACKUP_SCHEMA_VERSION,
            backup_id,
            created_at: Utc::now(),
            format: BackupFormat::FullBtrfsSendV1,
            source,
            stream_sha256: hash_open_file(&mut stream).unwrap(),
            stream_size_bytes: stream.metadata().unwrap().len(),
            referenced_bytes: 14,
        };
        write_manifest(&directory, &manifest).unwrap();
        assert_eq!(
            read_backup(&destination, backup_id, true).unwrap(),
            manifest
        );
        fs::write(&stream_path, b"altered stream").unwrap();
        assert!(read_backup(&destination, backup_id, false).is_ok());
        assert!(read_backup(&destination, backup_id, true).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
