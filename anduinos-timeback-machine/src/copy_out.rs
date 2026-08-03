use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use uuid::Uuid;

use crate::browsing::OpenedFileMetadata;

pub fn copy_file_atomic(
    mut source: File,
    source_metadata: &OpenedFileMetadata,
    destination: &Path,
) -> io::Result<u64> {
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
        let copied = io::copy(&mut source, &mut output)?;
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

fn temporary_path(parent: &Path, name: &std::ffi::OsStr) -> PathBuf {
    let mut temporary_name = std::ffi::OsString::from(".");
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
}
