use std::fs;
use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};

const EXPECTED_MOUNTS: [(&str, &str); 6] = [
    ("/", "/@root"),
    ("/home", "/@home"),
    ("/var/log", "/@log"),
    ("/.snapshots", "/@snapshots"),
    ("/var/lib/containers", "/@containers"),
    ("/var/lib/libvirt/images", "/@libvirt"),
];

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum LayoutSupport {
    Supported,
    OtherFilesystem,
    IncompatibleBtrfs,
    Unavailable,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MountReport {
    pub mount_point: String,
    pub subvolume: String,
    pub filesystem: String,
    pub source: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LayoutReport {
    pub support: LayoutSupport,
    pub root_filesystem: Option<String>,
    pub root_source: Option<String>,
    pub issues: Vec<String>,
    pub mounts: Vec<MountReport>,
}

impl LayoutReport {
    pub fn is_supported(&self) -> bool {
        self.support == LayoutSupport::Supported
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct MountEntry {
    root: String,
    mount_point: String,
    filesystem: String,
    source: String,
}

pub fn inspect_current() -> LayoutReport {
    match fs::read_to_string("/proc/self/mountinfo") {
        Ok(contents) => inspect_mountinfo(&contents),
        Err(error) => unavailable(error),
    }
}

pub fn inspect_mountinfo(contents: &str) -> LayoutReport {
    let entries = contents
        .lines()
        .filter_map(parse_mountinfo_line)
        .collect::<Vec<_>>();
    let Some(root) = entries.iter().find(|entry| entry.mount_point == "/") else {
        return LayoutReport {
            support: LayoutSupport::Unavailable,
            root_filesystem: None,
            root_source: None,
            issues: vec!["The root mount is missing from /proc/self/mountinfo".into()],
            mounts: Vec::new(),
        };
    };

    let mounts = entries
        .iter()
        .filter(|entry| {
            EXPECTED_MOUNTS
                .iter()
                .any(|(mount_point, _)| entry.mount_point == *mount_point)
        })
        .map(|entry| MountReport {
            mount_point: entry.mount_point.clone(),
            subvolume: entry.root.clone(),
            filesystem: entry.filesystem.clone(),
            source: entry.source.clone(),
        })
        .collect::<Vec<_>>();

    if root.filesystem != "btrfs" {
        return LayoutReport {
            support: LayoutSupport::OtherFilesystem,
            root_filesystem: Some(root.filesystem.clone()),
            root_source: Some(root.source.clone()),
            issues: vec![format!("Root filesystem is {}, not Btrfs", root.filesystem)],
            mounts,
        };
    }

    let mut issues = Vec::new();
    for (mount_point, subvolume) in EXPECTED_MOUNTS {
        match entries
            .iter()
            .find(|entry| entry.mount_point == mount_point)
        {
            None => issues.push(format!("Required mount {mount_point} is missing")),
            Some(entry) if entry.filesystem != "btrfs" => issues.push(format!(
                "{mount_point} uses {}, expected Btrfs",
                entry.filesystem
            )),
            Some(entry) if entry.root != subvolume => issues.push(format!(
                "{mount_point} uses subvolume {}, expected {subvolume}",
                entry.root
            )),
            Some(entry) if entry.source != root.source => issues.push(format!(
                "{mount_point} is on {}, expected {}",
                entry.source, root.source
            )),
            Some(_) => {}
        }
    }

    LayoutReport {
        support: if issues.is_empty() {
            LayoutSupport::Supported
        } else {
            LayoutSupport::IncompatibleBtrfs
        },
        root_filesystem: Some(root.filesystem.clone()),
        root_source: Some(root.source.clone()),
        issues,
        mounts,
    }
}

fn parse_mountinfo_line(line: &str) -> Option<MountEntry> {
    let (left, right) = line.split_once(" - ")?;
    let left_fields = left.split_whitespace().collect::<Vec<_>>();
    let right_fields = right.split_whitespace().collect::<Vec<_>>();
    if left_fields.len() < 6 || right_fields.len() < 2 {
        return None;
    }
    Some(MountEntry {
        root: unescape_mount_field(left_fields[3]),
        mount_point: unescape_mount_field(left_fields[4]),
        filesystem: right_fields[0].to_string(),
        source: unescape_mount_field(right_fields[1]),
    })
}

fn unescape_mount_field(value: &str) -> String {
    value
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
}

fn unavailable(error: io::Error) -> LayoutReport {
    LayoutReport {
        support: LayoutSupport::Unavailable,
        root_filesystem: None,
        root_source: None,
        issues: vec![format!(
            "Could not read {}: {error}",
            Path::new("/proc/self/mountinfo").display()
        )],
        mounts: Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SUPPORTED: &str = "\
25 1 0:32 /@root / rw,relatime - btrfs /dev/vda4 rw\n\
26 25 0:32 /@home /home rw,relatime - btrfs /dev/vda4 rw\n\
27 25 0:32 /@log /var/log rw,relatime - btrfs /dev/vda4 rw\n\
28 25 0:32 /@snapshots /.snapshots rw,relatime - btrfs /dev/vda4 rw\n\
29 25 0:32 /@containers /var/lib/containers rw,relatime - btrfs /dev/vda4 rw\n\
30 25 0:32 /@libvirt /var/lib/libvirt/images rw,relatime - btrfs /dev/vda4 rw\n";

    #[test]
    fn recognizes_exact_anduinos_layout() {
        let report = inspect_mountinfo(SUPPORTED);
        assert_eq!(report.support, LayoutSupport::Supported);
        assert_eq!(report.root_source.as_deref(), Some("/dev/vda4"));
        assert_eq!(report.mounts.len(), 6);
    }

    #[test]
    fn rejects_btrfs_with_missing_persistent_boundary() {
        let report = inspect_mountinfo(&SUPPORTED.replace(
            "27 25 0:32 /@log /var/log rw,relatime - btrfs /dev/vda4 rw\n",
            "",
        ));
        assert_eq!(report.support, LayoutSupport::IncompatibleBtrfs);
        assert!(report.issues.iter().any(|issue| issue.contains("/var/log")));
    }

    #[test]
    fn rejects_subvolume_on_another_filesystem() {
        let report = inspect_mountinfo(&SUPPORTED.replace(
            "/@home /home rw,relatime - btrfs /dev/vda4",
            "/@home /home rw,relatime - btrfs /dev/vdb1",
        ));
        assert_eq!(report.support, LayoutSupport::IncompatibleBtrfs);
        assert!(report
            .issues
            .iter()
            .any(|issue| issue.contains("/dev/vdb1")));
    }

    #[test]
    fn reports_ext4_without_claiming_btrfs_support() {
        let report = inspect_mountinfo("25 1 8:1 / / rw,relatime - ext4 /dev/vda1 rw\n");
        assert_eq!(report.support, LayoutSupport::OtherFilesystem);
        assert_eq!(report.root_filesystem.as_deref(), Some("ext4"));
    }

    #[test]
    fn parses_escaped_mount_fields() {
        let entry = parse_mountinfo_line(
            "25 1 0:32 /@root /path\\040with\\040spaces rw - btrfs /dev/vda4 rw",
        )
        .unwrap();
        assert_eq!(entry.mount_point, "/path with spaces");
    }
}
