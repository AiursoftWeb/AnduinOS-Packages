use std::fs::{self, File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

pub struct TransactionStartLock(File);

impl TransactionStartLock {
    pub fn acquire(snapshot_root: impl AsRef<Path>) -> io::Result<Self> {
        let transactions = snapshot_root.as_ref().join("transactions");
        let metadata = fs::symlink_metadata(&transactions)?;
        if !metadata.file_type().is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Waypoint transactions path is not a real directory",
            ));
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(transactions.join("start.lock"))?;
        let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Self(file))
    }
}

impl Drop for TransactionStartLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transaction_directory_must_be_real() {
        let root = std::env::temp_dir().join(format!("waypoint-lock-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("transactions"), "not a directory").unwrap();
        assert!(TransactionStartLock::acquire(&root).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
