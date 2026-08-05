use std::fs::{self, File};
use std::io;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;

use crate::model::DeploymentId;

#[derive(Debug)]
pub struct DeploymentBrowseLock(File);

impl Drop for DeploymentBrowseLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

/// Prevent deletion while a deployment is being inspected or copied out.
/// The lock is non-blocking so the caller can truthfully report `busy`.
pub fn acquire_exclusive_deployment_lock_at(
    root: &Path,
    deployment_id: &str,
) -> io::Result<DeploymentBrowseLock> {
    deployment_id.parse::<DeploymentId>().map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid deployment lock identifier",
        )
    })?;
    let directory = root.join("browse-locks");
    match fs::symlink_metadata(&directory) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "deployment browse-lock path is not a real directory",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir(&directory)?;
            fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?;
        }
        Err(error) => return Err(error),
    }
    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(directory.join(format!("system-{deployment_id}.lock")))?;
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        let error = io::Error::last_os_error();
        return Err(if error.raw_os_error() == Some(libc::EWOULDBLOCK) {
            io::Error::new(
                io::ErrorKind::WouldBlock,
                "deployment is currently being browsed",
            )
        } else {
            error
        });
    }
    Ok(DeploymentBrowseLock(file))
}
