use std::fs::{File, OpenOptions};
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use crate::browse_lock::{DeploymentBrowseLock, acquire_shared_deployment_lock_at};
use crate::model::DeploymentId;
use crate::personal::{
    PersonalDirectoryEntry, duplicate_file, list_directory_fd, open_beneath,
    open_beneath_allow_final_mount, validate_relative_path,
};

pub struct SystemSnapshotBrowser {
    root: File,
    _lease: DeploymentBrowseLock,
}

impl SystemSnapshotBrowser {
    pub fn open(
        store_root: &Path,
        id: DeploymentId,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let lease = acquire_shared_deployment_lock_at(store_root, &id.to_string())?;
        let store = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(store_root)?;
        let relative = Path::new("deployments").join(id.to_string());
        let container = open_beneath(
            store.as_raw_fd(),
            &relative,
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
        )?;
        // `root` is the one intentional Btrfs subvolume boundary. Resolve its
        // fixed name from the trusted deployment container, then prohibit any
        // further filesystem crossing while browsing snapshot contents.
        let root = open_beneath_allow_final_mount(
            container.as_raw_fd(),
            Path::new("root"),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
        )?;
        Ok(Self {
            root,
            _lease: lease,
        })
    }

    pub fn list(
        &self,
        relative_path: &str,
    ) -> Result<Vec<PersonalDirectoryEntry>, Box<dyn std::error::Error + Send + Sync>> {
        validate_relative_path(relative_path, true)?;
        let directory = if relative_path.is_empty() {
            duplicate_file(&self.root)?
        } else {
            open_beneath(
                self.root.as_raw_fd(),
                Path::new(relative_path),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
            )?
        };
        Ok(list_directory_fd(directory.as_raw_fd())?)
    }

    pub fn open_file(
        &self,
        relative_path: &str,
    ) -> Result<File, Box<dyn std::error::Error + Send + Sync>> {
        validate_relative_path(relative_path, false)?;
        let file = open_beneath(
            self.root.as_raw_fd(),
            Path::new(relative_path),
            libc::O_RDONLY | libc::O_CLOEXEC,
        )?;
        if !file.metadata()?.file_type().is_file() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "Only regular files can be exported",
            )
            .into());
        }
        Ok(file)
    }
}
