use std::path::{Component, Path, PathBuf};

use anyhow::{Context, Result, bail, ensure};
use gio::prelude::*;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HistoryTargetKind {
    File,
    Directory,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HistoryTarget {
    pub relative_path: String,
    pub kind: HistoryTargetKind,
}

/// Turn a request from the unprivileged Nautilus extension into the only path
/// representation accepted by the Personal Files UI: a normalized path below
/// the current user's real home directory.
pub fn resolve_history_request(mode: &str, uri: &str) -> Result<HistoryTarget> {
    let home = dirs::home_dir().context("The current user's home directory is unavailable")?;
    let canonical_home = home
        .canonicalize()
        .context("Could not resolve the current user's home directory")?;
    ensure!(
        canonical_home.parent() == Some(Path::new("/home")),
        "File History is available only for standard /home user directories"
    );
    resolve_history_request_in_home(mode, uri, &home)
}

fn resolve_history_request_in_home(mode: &str, uri: &str, home: &Path) -> Result<HistoryTarget> {
    ensure!(
        matches!(mode, "selection" | "folder"),
        "Unsupported file-history request"
    );

    let file = gio::File::for_uri(uri);
    ensure!(
        file.uri_scheme().as_deref() == Some("file"),
        "Only local files can be opened in Personal Files history"
    );
    let requested = file
        .path()
        .context("The file-history request has no local path")?;
    ensure!(requested.is_absolute(), "The selected path is not absolute");

    let canonical_home = home
        .canonicalize()
        .context("Could not resolve the current user's home directory")?;
    let canonical_target = requested
        .canonicalize()
        .context("The selected file or folder no longer exists")?;

    // Reject symlinks anywhere in the caller-provided path. Canonicalizing for
    // containment alone would otherwise silently change which @home path the
    // user asked to inspect.
    ensure!(
        canonical_target == requested,
        "Symbolic-link paths are not supported by File History"
    );
    ensure!(
        canonical_target.starts_with(&canonical_home),
        "Only files in the current user's home directory are supported"
    );

    let metadata = std::fs::metadata(&canonical_target)
        .context("Could not inspect the selected file or folder")?;
    let kind = if metadata.is_dir() {
        HistoryTargetKind::Directory
    } else if metadata.is_file() {
        HistoryTargetKind::File
    } else {
        bail!("Only regular files and folders are supported");
    };
    ensure!(
        mode != "folder" || kind == HistoryTargetKind::Directory,
        "The folder-history request does not refer to a folder"
    );

    let relative = canonical_target
        .strip_prefix(&canonical_home)
        .context("The selected path is outside the current user's home directory")?;
    let relative_path = utf8_normal_relative_path(relative)?;
    Ok(HistoryTarget {
        relative_path,
        kind,
    })
}

fn utf8_normal_relative_path(path: &Path) -> Result<String> {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => normalized.push(value),
            _ => bail!("The selected path is not a normalized Personal Files path"),
        }
    }
    normalized
        .to_str()
        .map(ToOwned::to_owned)
        .context("The selected path is not valid UTF-8")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "waypoint-nautilus-request-{}",
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        std::fs::create_dir_all(root.join("Documents/Reports")).unwrap();
        std::fs::write(root.join("Documents/report.txt"), b"history").unwrap();
        root
    }

    fn uri(path: &Path) -> String {
        gio::File::for_path(path).uri().to_string()
    }

    #[test]
    fn selection_becomes_a_home_relative_file_target() {
        let home = fixture();
        let target = resolve_history_request_in_home(
            "selection",
            &uri(&home.join("Documents/report.txt")),
            &home,
        )
        .unwrap();
        assert_eq!(target.relative_path, "Documents/report.txt");
        assert_eq!(target.kind, HistoryTargetKind::File);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn background_request_accepts_a_local_home_folder() {
        let home = fixture();
        let target =
            resolve_history_request_in_home("folder", &uri(&home.join("Documents/Reports")), &home)
                .unwrap();
        assert_eq!(target.relative_path, "Documents/Reports");
        assert_eq!(target.kind, HistoryTargetKind::Directory);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn folder_request_rejects_a_file() {
        let home = fixture();
        let result = resolve_history_request_in_home(
            "folder",
            &uri(&home.join("Documents/report.txt")),
            &home,
        );
        assert!(result.is_err());
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn request_rejects_paths_outside_home() {
        let home = fixture();
        let outside = std::env::temp_dir().join(format!(
            "waypoint-nautilus-outside-{}",
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        std::fs::write(&outside, b"outside").unwrap();
        assert!(resolve_history_request_in_home("selection", &uri(&outside), &home).is_err());
        std::fs::remove_file(outside).unwrap();
        std::fs::remove_dir_all(home).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn request_rejects_a_symlink_even_when_it_points_inside_home() {
        use std::os::unix::fs::symlink;

        let home = fixture();
        symlink(
            home.join("Documents/report.txt"),
            home.join("Documents/report-link.txt"),
        )
        .unwrap();
        assert!(
            resolve_history_request_in_home(
                "selection",
                &uri(&home.join("Documents/report-link.txt")),
                &home,
            )
            .is_err()
        );
        std::fs::remove_dir_all(home).unwrap();
    }
}
