use std::io;
use std::mem::MaybeUninit;
use std::os::unix::ffi::OsStrExt;
use std::path::Path;

use serde::{Deserialize, Serialize};

pub const MINIMUM_TRANSACTION_RESERVE_BYTES: u64 = 2 * 1024 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SnapshotSpace {
    pub referenced_bytes: Option<u64>,
    pub exclusive_bytes: Option<u64>,
}

impl SnapshotSpace {
    /// For a single snapshot qgroup, exclusive bytes are the best available
    /// deletion estimate. They are deliberately not presented as a guarantee.
    pub fn estimated_reclaimable_bytes(self) -> Option<u64> {
        self.exclusive_bytes
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FilesystemSpace {
    pub total_bytes: u64,
    pub available_bytes: u64,
}

impl FilesystemSpace {
    pub fn has_reserve(self, reserve_bytes: u64) -> bool {
        self.available_bytes >= reserve_bytes
    }
}

pub fn probe_filesystem_space(path: &Path) -> io::Result<FilesystemSpace> {
    let path = std::ffi::CString::new(path.as_os_str().as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains a NUL byte"))?;
    let mut buffer = MaybeUninit::<libc::statvfs>::uninit();
    let result = unsafe { libc::statvfs(path.as_ptr(), buffer.as_mut_ptr()) };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    let values = unsafe { buffer.assume_init() };
    let block_size = values.f_frsize;
    let total_bytes = values
        .f_blocks
        .checked_mul(block_size)
        .ok_or_else(|| io::Error::other("filesystem size overflow"))?;
    let available_bytes = values
        .f_bavail
        .checked_mul(block_size)
        .ok_or_else(|| io::Error::other("available-space overflow"))?;
    Ok(FilesystemSpace {
        total_bytes,
        available_bytes,
    })
}

pub fn parse_qgroup_numbers(output: &str) -> Option<SnapshotSpace> {
    for line in output.lines().rev() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len() >= 3
            && fields[0].contains('/')
            && let (Ok(referenced), Ok(exclusive)) = (fields[1].parse(), fields[2].parse())
        {
            return Some(SnapshotSpace {
                referenced_bytes: Some(referenced),
                exclusive_bytes: Some(exclusive),
            });
        }
    }
    None
}

pub fn parse_qgroup_for_subvolume(output: &str, subvolume_id: u64) -> Option<SnapshotSpace> {
    let expected = format!("0/{subvolume_id}");
    output.lines().find_map(|line| {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        if fields.len() >= 3 && fields[0] == expected {
            Some(SnapshotSpace {
                referenced_bytes: fields[1].parse().ok(),
                exclusive_bytes: fields[2].parse().ok(),
            })
            .filter(|space| space.referenced_bytes.is_some() && space.exclusive_bytes.is_some())
        } else {
            None
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_raw_qgroup_output() {
        let space = parse_qgroup_numbers("qgroupid rfer excl\n0/256 4096 1024\n").unwrap();
        assert_eq!(space.estimated_reclaimable_bytes(), Some(1024));
    }

    #[test]
    fn selects_the_exact_level_zero_qgroup() {
        let output = "qgroupid rfer excl\n0/256 100 10\n1/0 500 50\n0/300 200 20\n";
        assert_eq!(
            parse_qgroup_for_subvolume(output, 300),
            Some(SnapshotSpace {
                referenced_bytes: Some(200),
                exclusive_bytes: Some(20),
            })
        );
        assert_eq!(parse_qgroup_for_subvolume(output, 301), None);
    }

    #[test]
    fn reserve_boundary_is_inclusive() {
        let space = FilesystemSpace {
            total_bytes: 10_000,
            available_bytes: 2_000,
        };
        assert!(space.has_reserve(2_000));
        assert!(!space.has_reserve(2_001));
    }

    #[test]
    fn probes_the_current_filesystem_without_overflow() {
        let space = probe_filesystem_space(Path::new("/")).unwrap();
        assert!(space.total_bytes > 0);
        assert!(space.available_bytes <= space.total_bytes);
    }
}
