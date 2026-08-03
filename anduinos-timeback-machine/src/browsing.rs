use std::ffi::{CString, OsString};
use std::fs::{self, File};
use std::io;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::automatic_home::HomeSnapshotStore;
use crate::model::{DeploymentId, DeploymentState};
use crate::store::DeploymentStore;
use crate::SNAPSHOT_ROOT;

const MAX_DIRECTORY_ENTRIES: usize = 1_000;
const RESOLVE_NO_MAGICLINKS: u64 = 0x02;
const RESOLVE_NO_SYMLINKS: u64 = 0x04;
const RESOLVE_BENEATH: u64 = 0x08;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SnapshotKind {
    System,
    Home,
}

impl SnapshotKind {
    pub fn parse(value: &str) -> Result<Self, BrowseError> {
        match value {
            "system" => Ok(Self::System),
            "home" => Ok(Self::Home),
            _ => Err(BrowseError::invalid("Unknown snapshot kind")),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DirectoryListing {
    pub path: Vec<String>,
    pub entries: Vec<BrowserEntry>,
    pub truncated: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BrowserEntry {
    /// Hex-encoded raw filename. Linux filenames do not have to be UTF-8.
    pub token: String,
    pub display_name: String,
    pub kind: EntryKind,
    pub size: u64,
    pub modified_unix: i64,
    pub hidden: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum EntryKind {
    Directory,
    File,
    Symlink,
    Special,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenedFileMetadata {
    pub size: u64,
    pub modified_unix: i64,
    pub mode: u32,
}

#[derive(Debug)]
pub struct BrowseError {
    pub code: &'static str,
    pub message: String,
}

impl BrowseError {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            code: "invalid-path",
            message: message.into(),
        }
    }

    fn unavailable(message: impl Into<String>) -> Self {
        Self {
            code: "snapshot-unavailable",
            message: message.into(),
        }
    }

    fn io(context: &str, error: io::Error) -> Self {
        Self {
            code: "browse-io",
            message: format!("{context}: {error}"),
        }
    }
}

pub fn list_directory(
    kind: SnapshotKind,
    snapshot_id: &str,
    path: &[String],
) -> Result<DirectoryListing, BrowseError> {
    let root = snapshot_root(kind, snapshot_id)?;
    let components = decode_path(path)?;
    let directory = open_beneath(
        &root,
        &components,
        libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
    )?;
    let proc_path = PathBuf::from(format!("/proc/self/fd/{}", directory.as_raw_fd()));
    let mut entries = Vec::new();
    let read_dir = fs::read_dir(proc_path)
        .map_err(|error| BrowseError::io("Could not read the snapshot directory", error))?;
    let mut truncated = false;
    for entry in read_dir {
        if entries.len() == MAX_DIRECTORY_ENTRIES {
            truncated = true;
            break;
        }
        let entry =
            entry.map_err(|error| BrowseError::io("Could not read a directory entry", error))?;
        let name = entry.file_name();
        let bytes = name.as_bytes();
        let metadata = fs::symlink_metadata(entry.path())
            .map_err(|error| BrowseError::io("Could not inspect a directory entry", error))?;
        let file_type = metadata.file_type();
        entries.push(BrowserEntry {
            token: encode_component(bytes),
            display_name: name.to_string_lossy().into_owned(),
            kind: if file_type.is_dir() {
                EntryKind::Directory
            } else if file_type.is_file() {
                EntryKind::File
            } else if file_type.is_symlink() {
                EntryKind::Symlink
            } else {
                EntryKind::Special
            },
            size: metadata.len(),
            modified_unix: metadata.mtime(),
            hidden: bytes.first() == Some(&b'.'),
        });
    }
    entries.sort_by(|left, right| {
        let left_dir = left.kind == EntryKind::Directory;
        let right_dir = right.kind == EntryKind::Directory;
        right_dir
            .cmp(&left_dir)
            .then_with(|| {
                left.display_name
                    .to_lowercase()
                    .cmp(&right.display_name.to_lowercase())
            })
            .then_with(|| left.token.cmp(&right.token))
    });
    Ok(DirectoryListing {
        path: path.to_vec(),
        entries,
        truncated,
    })
}

pub fn open_regular_file(
    kind: SnapshotKind,
    snapshot_id: &str,
    path: &[String],
) -> Result<(File, OpenedFileMetadata), BrowseError> {
    if path.is_empty() {
        return Err(BrowseError::invalid("A file path is required"));
    }
    let root = snapshot_root(kind, snapshot_id)?;
    let components = decode_path(path)?;
    let descriptor = open_beneath(&root, &components, libc::O_RDONLY | libc::O_CLOEXEC)?;
    let file = File::from(descriptor);
    let metadata = file
        .metadata()
        .map_err(|error| BrowseError::io("Could not inspect the snapshot file", error))?;
    if !metadata.is_file() {
        return Err(BrowseError::invalid(
            "Only regular files can be copied out of a snapshot",
        ));
    }
    let details = OpenedFileMetadata {
        size: metadata.len(),
        modified_unix: metadata.mtime(),
        mode: metadata.mode(),
    };
    Ok((file, details))
}

fn snapshot_root(kind: SnapshotKind, snapshot_id: &str) -> Result<PathBuf, BrowseError> {
    match kind {
        SnapshotKind::System => {
            let id = snapshot_id
                .parse::<DeploymentId>()
                .map_err(|_| BrowseError::invalid("Invalid system snapshot ID"))?;
            let record = DeploymentStore::default()
                .load_record(id)
                .map_err(|problem| BrowseError::unavailable(problem.message))?;
            if record.state == DeploymentState::Creating
                || record.state == DeploymentState::Deleting
                || record.snapshot_uuid.is_none()
            {
                return Err(BrowseError::unavailable(
                    "This system snapshot is not ready for browsing",
                ));
            }
            Ok(Path::new(SNAPSHOT_ROOT)
                .join("deployments")
                .join(id.to_string())
                .join("root"))
        }
        SnapshotKind::Home => {
            let id = Uuid::parse_str(snapshot_id)
                .map_err(|_| BrowseError::invalid("Invalid Home snapshot ID"))?;
            let found = HomeSnapshotStore::default()
                .discover()
                .map_err(BrowseError::unavailable)?
                .into_iter()
                .any(|record| record.id == id && !record.deleting);
            if !found {
                return Err(BrowseError::unavailable(
                    "This Home snapshot is not available for browsing",
                ));
            }
            Ok(Path::new(SNAPSHOT_ROOT)
                .join("home")
                .join("snapshots")
                .join(id.to_string()))
        }
    }
}

fn decode_path(tokens: &[String]) -> Result<Vec<OsString>, BrowseError> {
    tokens
        .iter()
        .map(|token| {
            let bytes = decode_component(token)?;
            if bytes.is_empty()
                || bytes == b"."
                || bytes == b".."
                || bytes.contains(&b'/')
                || bytes.contains(&0)
            {
                return Err(BrowseError::invalid("Unsafe snapshot path component"));
            }
            Ok(OsString::from_vec(bytes))
        })
        .collect()
}

fn encode_component(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(HEX[(byte >> 4) as usize] as char);
        encoded.push(HEX[(byte & 0x0f) as usize] as char);
    }
    encoded
}

pub fn decode_name_token(value: &str) -> Result<OsString, BrowseError> {
    decode_component(value).map(OsString::from_vec)
}

fn decode_component(value: &str) -> Result<Vec<u8>, BrowseError> {
    if value.len() % 2 != 0 {
        return Err(BrowseError::invalid("Invalid filename token"));
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let high = hex_digit(pair[0])?;
            let low = hex_digit(pair[1])?;
            Ok((high << 4) | low)
        })
        .collect()
}

fn hex_digit(value: u8) -> Result<u8, BrowseError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(BrowseError::invalid("Invalid filename token")),
    }
}

fn open_beneath(root: &Path, components: &[OsString], flags: i32) -> Result<OwnedFd, BrowseError> {
    let root_c = CString::new(root.as_os_str().as_bytes())
        .map_err(|_| BrowseError::invalid("Snapshot root contains a null byte"))?;
    let root_fd = unsafe {
        libc::open(
            root_c.as_ptr(),
            libc::O_PATH | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    };
    if root_fd < 0 {
        return Err(BrowseError::io(
            "Could not open the snapshot root",
            io::Error::last_os_error(),
        ));
    }
    let root_fd = unsafe { OwnedFd::from_raw_fd(root_fd) };
    let relative = if components.is_empty() {
        OsString::from(".")
    } else {
        let mut bytes = Vec::new();
        for (index, component) in components.iter().enumerate() {
            if index > 0 {
                bytes.push(b'/');
            }
            bytes.extend_from_slice(component.as_bytes());
        }
        OsString::from_vec(bytes)
    };
    let relative_c = CString::new(relative.as_bytes())
        .map_err(|_| BrowseError::invalid("Snapshot path contains a null byte"))?;
    let how = OpenHow {
        flags: flags as u64,
        mode: 0,
        resolve: RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_SYMLINKS,
    };
    let descriptor = unsafe {
        libc::syscall(
            libc::SYS_openat2,
            root_fd.as_raw_fd(),
            relative_c.as_ptr(),
            &how,
            std::mem::size_of::<OpenHow>(),
        ) as i32
    };
    if descriptor < 0 {
        return Err(BrowseError::io(
            "Could not safely open the snapshot path",
            io::Error::last_os_error(),
        ));
    }
    Ok(unsafe { OwnedFd::from_raw_fd(descriptor) })
}

#[repr(C)]
struct OpenHow {
    flags: u64,
    mode: u64,
    resolve: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filename_tokens_round_trip_non_utf8_names() {
        let original = b"hello-\xff.txt";
        assert_eq!(
            decode_component(&encode_component(original)).unwrap(),
            original
        );
    }

    #[test]
    fn unsafe_components_are_rejected() {
        for value in [b"".as_slice(), b".", b"..", b"a/b", b"a\0b"] {
            let error = decode_path(&[encode_component(value)]).unwrap_err();
            assert_eq!(error.code, "invalid-path");
        }
    }

    #[test]
    fn open_beneath_refuses_symlink_escape() {
        let root = std::env::temp_dir().join(format!("timeback-browser-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        std::os::unix::fs::symlink("/etc", root.join("escape")).unwrap();
        let result = open_beneath(
            &root,
            &[OsString::from("escape"), OsString::from("passwd")],
            libc::O_RDONLY | libc::O_CLOEXEC,
        );
        fs::remove_dir_all(&root).unwrap();
        assert!(result.is_err());
    }
}
