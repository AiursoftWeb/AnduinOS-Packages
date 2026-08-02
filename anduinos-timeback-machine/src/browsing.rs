use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

/// Resolve a browser path without allowing absolute paths, `..`, or symlinks
/// to escape the read-only snapshot root.
pub fn resolve(snapshot_root: &Path, relative: &Path) -> io::Result<PathBuf> {
    if relative.is_absolute() || relative.components().any(|c| matches!(c, Component::ParentDir | Component::RootDir | Component::Prefix(_))) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "snapshot path escapes its root"));
    }
    let root = fs::canonicalize(snapshot_root)?;
    let candidate = fs::canonicalize(root.join(relative))?;
    if candidate == root || candidate.starts_with(&root) { Ok(candidate) } else { Err(io::Error::new(io::ErrorKind::PermissionDenied, "symbolic link escapes snapshot root")) }
}

#[cfg(test)] mod tests { use super::*; #[test] fn rejects_parent_components() { assert_eq!(resolve(Path::new("/"), Path::new("../etc")).unwrap_err().kind(), io::ErrorKind::InvalidInput); } }
