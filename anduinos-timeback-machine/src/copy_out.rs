use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use uuid::Uuid;

use crate::browsing::{decode_name_token, DirectoryListing, EntryKind, OpenedFileMetadata};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConflictPolicy {
    KeepBoth,
    Replace,
    Skip,
}

#[derive(Clone, Debug)]
pub struct ExportSelection {
    pub snapshot_path: Vec<String>,
    pub name_token: String,
    pub kind: EntryKind,
    pub size: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ExportProgress {
    pub copied_bytes: u64,
    pub total_bytes: u64,
    pub copied_files: u64,
    pub total_files: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ExportReport {
    pub copied_bytes: u64,
    pub copied_files: u64,
    pub copied_directories: u64,
    pub skipped_items: u64,
}

#[derive(Debug)]
pub enum ExportError {
    Cancelled,
    Failed(String),
}

impl std::fmt::Display for ExportError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Cancelled => write!(formatter, "Copy cancelled"),
            Self::Failed(message) => message.fmt(formatter),
        }
    }
}

struct ManifestItem {
    root_index: usize,
    snapshot_path: Vec<String>,
    relative_path: PathBuf,
    kind: EntryKind,
    size: u64,
}

pub fn export_items<L, O, P>(
    selections: &[ExportSelection],
    destination: &Path,
    conflict_policy: ConflictPolicy,
    cancelled: &AtomicBool,
    mut list_directory: L,
    mut open_file: O,
    mut on_progress: P,
) -> Result<ExportReport, ExportError>
where
    L: FnMut(&[String]) -> Result<DirectoryListing, String>,
    O: FnMut(&[String]) -> Result<(File, OpenedFileMetadata), String>,
    P: FnMut(ExportProgress),
{
    check_cancelled(cancelled)?;
    let mut manifest = Vec::new();
    let mut skipped_items = 0;
    for (root_index, selection) in selections.iter().enumerate() {
        let name = decode_name_token(&selection.name_token)
            .map_err(|error| ExportError::Failed(error.message))?;
        let relative = PathBuf::from(name);
        collect_manifest(
            root_index,
            selection.snapshot_path.clone(),
            relative,
            selection.kind,
            selection.size,
            cancelled,
            &mut list_directory,
            &mut manifest,
            &mut skipped_items,
        )?;
    }
    let total_bytes = manifest
        .iter()
        .filter(|item| item.kind == EntryKind::File)
        .map(|item| item.size)
        .sum();
    let total_files = manifest
        .iter()
        .filter(|item| item.kind == EntryKind::File)
        .count() as u64;
    let mut progress = ExportProgress {
        total_bytes,
        total_files,
        ..ExportProgress::default()
    };
    on_progress(progress);

    let mut report = ExportReport {
        skipped_items,
        ..ExportReport::default()
    };
    let roots = resolve_roots(selections, destination, conflict_policy)?;
    for item in &manifest {
        check_cancelled(cancelled)?;
        let root = roots
            .get(item.root_index)
            .ok_or_else(|| ExportError::Failed("Export root is unavailable".into()))?;
        let Some(root_destination) = &root.destination else {
            report.skipped_items += 1;
            continue;
        };
        let relative_without_root = item.relative_path.components().skip(1).collect::<PathBuf>();
        let target = root_destination.join(relative_without_root);
        match item.kind {
            EntryKind::Directory => {
                prepare_directory(&target, conflict_policy)?;
                report.copied_directories += 1;
            }
            EntryKind::File => {
                if conflict_policy == ConflictPolicy::Skip && target.exists() {
                    report.skipped_items += 1;
                    continue;
                }
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent).map_err(|error| {
                        ExportError::Failed(format!("Could not create destination folder: {error}"))
                    })?;
                }
                let (source, metadata) =
                    open_file(&item.snapshot_path).map_err(ExportError::Failed)?;
                copy_file_atomic_with_progress(source, &metadata, &target, cancelled, |bytes| {
                    progress.copied_bytes += bytes;
                    on_progress(progress);
                })
                .map_err(|error| match error.kind() {
                    io::ErrorKind::Interrupted => ExportError::Cancelled,
                    _ => {
                        ExportError::Failed(format!("Could not copy {}: {error}", target.display()))
                    }
                })?;
                progress.copied_files += 1;
                report.copied_files += 1;
                report.copied_bytes = progress.copied_bytes;
                on_progress(progress);
            }
            EntryKind::Symlink | EntryKind::Special => report.skipped_items += 1,
        }
    }
    Ok(report)
}

fn collect_manifest<L>(
    root_index: usize,
    snapshot_path: Vec<String>,
    relative_path: PathBuf,
    kind: EntryKind,
    size: u64,
    cancelled: &AtomicBool,
    list_directory: &mut L,
    manifest: &mut Vec<ManifestItem>,
    skipped: &mut u64,
) -> Result<(), ExportError>
where
    L: FnMut(&[String]) -> Result<DirectoryListing, String>,
{
    check_cancelled(cancelled)?;
    match kind {
        EntryKind::File => manifest.push(ManifestItem {
            root_index,
            snapshot_path,
            relative_path,
            kind,
            size,
        }),
        EntryKind::Directory => {
            manifest.push(ManifestItem {
                root_index,
                snapshot_path: snapshot_path.clone(),
                relative_path: relative_path.clone(),
                kind,
                size: 0,
            });
            let listing = list_directory(&snapshot_path).map_err(ExportError::Failed)?;
            if listing.truncated {
                return Err(ExportError::Failed(
                    "A selected folder contains more than 1,000 items. Nothing was copied because the folder listing is incomplete."
                        .into(),
                ));
            }
            for entry in listing.entries {
                let mut child_path = snapshot_path.clone();
                child_path.push(entry.token.clone());
                let child_name = decode_name_token(&entry.token)
                    .map_err(|error| ExportError::Failed(error.message))?;
                let child_relative = relative_path.join(child_name);
                match entry.kind {
                    EntryKind::Directory => collect_manifest(
                        root_index,
                        child_path,
                        child_relative,
                        entry.kind,
                        entry.size,
                        cancelled,
                        list_directory,
                        manifest,
                        skipped,
                    )?,
                    EntryKind::File => manifest.push(ManifestItem {
                        root_index,
                        snapshot_path: child_path,
                        relative_path: child_relative,
                        kind: entry.kind,
                        size: entry.size,
                    }),
                    EntryKind::Symlink | EntryKind::Special => *skipped += 1,
                }
            }
        }
        EntryKind::Symlink | EntryKind::Special => *skipped += 1,
    }
    Ok(())
}

struct ResolvedRoot {
    destination: Option<PathBuf>,
}

fn resolve_roots(
    selections: &[ExportSelection],
    destination: &Path,
    policy: ConflictPolicy,
) -> Result<Vec<ResolvedRoot>, ExportError> {
    let mut roots = Vec::new();
    for selection in selections {
        let name = decode_name_token(&selection.name_token)
            .map_err(|error| ExportError::Failed(error.message))?;
        let target = destination.join(name);
        let resolved = if !target.exists() || policy == ConflictPolicy::Replace {
            Some(target)
        } else if policy == ConflictPolicy::Skip {
            None
        } else {
            Some(unique_destination(&target))
        };
        roots.push(ResolvedRoot {
            destination: resolved,
        });
    }
    Ok(roots)
}

fn unique_destination(target: &Path) -> PathBuf {
    let parent = target.parent().unwrap_or_else(|| Path::new("."));
    let stem = target
        .file_stem()
        .map(OsString::from)
        .unwrap_or_else(|| OsString::from("restored"));
    let extension = target.extension().map(OsString::from);
    for number in 2..=10_000 {
        let mut name = stem.clone();
        name.push(format!(" ({number})"));
        if let Some(extension) = &extension {
            name.push(".");
            name.push(extension);
        }
        let candidate = parent.join(name);
        if !candidate.exists() {
            return candidate;
        }
    }
    parent.join(format!("restored-{}", Uuid::new_v4().hyphenated()))
}

fn prepare_directory(target: &Path, policy: ConflictPolicy) -> Result<(), ExportError> {
    match fs::symlink_metadata(target) {
        Ok(metadata) if metadata.is_dir() => Ok(()),
        Ok(_) if policy == ConflictPolicy::Replace => {
            fs::remove_file(target).map_err(|error| {
                ExportError::Failed(format!("Could not replace {}: {error}", target.display()))
            })?;
            fs::create_dir(target).map_err(|error| {
                ExportError::Failed(format!("Could not create {}: {error}", target.display()))
            })
        }
        Ok(_) => Err(ExportError::Failed(format!(
            "{} already exists and is not a folder",
            target.display()
        ))),
        Err(error) if error.kind() == io::ErrorKind::NotFound => fs::create_dir_all(target)
            .map_err(|error| {
                ExportError::Failed(format!("Could not create {}: {error}", target.display()))
            }),
        Err(error) => Err(ExportError::Failed(format!(
            "Could not inspect {}: {error}",
            target.display()
        ))),
    }
}

pub fn copy_file_atomic(
    source: File,
    source_metadata: &OpenedFileMetadata,
    destination: &Path,
) -> io::Result<u64> {
    copy_file_atomic_with_progress(
        source,
        source_metadata,
        destination,
        &AtomicBool::new(false),
        |_| {},
    )
}

fn copy_file_atomic_with_progress<P>(
    mut source: File,
    source_metadata: &OpenedFileMetadata,
    destination: &Path,
    cancelled: &AtomicBool,
    mut on_progress: P,
) -> io::Result<u64>
where
    P: FnMut(u64),
{
    let parent = destination.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "Destination has no parent directory",
        )
    })?;
    let name = destination.file_name().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "Destination has no filename")
    })?;
    let temporary = temporary_path(parent, name);
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(&temporary)?;
    let result = (|| {
        let mut copied = 0;
        let mut buffer = vec![0u8; 1024 * 1024];
        loop {
            if cancelled.load(Ordering::Acquire) {
                return Err(io::Error::new(io::ErrorKind::Interrupted, "Copy cancelled"));
            }
            let count = source.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            output.write_all(&buffer[..count])?;
            copied += count as u64;
            on_progress(count as u64);
        }
        output.flush()?;
        output.sync_all()?;
        fs::set_permissions(
            &temporary,
            fs::Permissions::from_mode(source_metadata.mode & 0o0777),
        )?;
        fs::rename(&temporary, destination)?;
        File::open(parent)?.sync_all()?;
        Ok(copied)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), ExportError> {
    if cancelled.load(Ordering::Acquire) {
        Err(ExportError::Cancelled)
    } else {
        Ok(())
    }
}

fn temporary_path(parent: &Path, name: &std::ffi::OsStr) -> PathBuf {
    let mut temporary_name = OsString::from(".");
    temporary_name.push(name);
    temporary_name.push(format!(".timeback-{}.tmp", Uuid::new_v4().hyphenated()));
    parent.join(temporary_name)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn copies_atomically_and_strips_privileged_mode_bits() {
        let root = std::env::temp_dir().join(format!("timeback-copy-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let source_path = root.join("source");
        fs::write(&source_path, b"snapshot contents").unwrap();
        let destination = root.join("restored");
        let metadata = OpenedFileMetadata {
            size: 17,
            modified_unix: 0,
            mode: 0o106755,
        };
        copy_file_atomic(File::open(source_path).unwrap(), &metadata, &destination).unwrap();
        let mut contents = String::new();
        File::open(&destination)
            .unwrap()
            .read_to_string(&mut contents)
            .unwrap();
        assert_eq!(contents, "snapshot contents");
        assert_eq!(
            fs::metadata(&destination).unwrap().permissions().mode() & 0o7777,
            0o755
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cancellation_removes_the_temporary_file() {
        let root = std::env::temp_dir().join(format!("timeback-cancel-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let source_path = root.join("source");
        fs::write(&source_path, vec![7u8; 2 * 1024 * 1024]).unwrap();
        let destination = root.join("restored");
        let cancelled = AtomicBool::new(false);
        let result = copy_file_atomic_with_progress(
            File::open(source_path).unwrap(),
            &OpenedFileMetadata {
                size: 2 * 1024 * 1024,
                modified_unix: 0,
                mode: 0o100644,
            },
            &destination,
            &cancelled,
            |_| cancelled.store(true, Ordering::Release),
        );
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::Interrupted);
        assert!(!destination.exists());
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        fs::remove_dir_all(root).unwrap();
    }
}
