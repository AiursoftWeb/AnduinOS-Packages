use serde::{Deserialize, Serialize};

use crate::layout::{LayoutReport, MountReport};

pub const SYSTEM_TARGET_ID: &str = "btrfs:@root";
pub const HOME_TARGET_ID: &str = "btrfs:@home";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum TargetKind { System, Home, Custom }

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SnapshotTarget {
    pub id: String,
    pub kind: TargetKind,
    pub display_name: String,
    pub filesystem_source: String,
    pub subvolume: String,
    pub mount_point: String,
    pub available: bool,
    pub issue: Option<String>,
}

impl SnapshotTarget {
    pub fn supports_system_restore(&self) -> bool { self.kind == TargetKind::System }
}

/// Discover only real, independently mounted Btrfs subvolumes. In particular,
/// `/home` is never offered merely because it is a directory below `/`.
pub fn discover_targets(report: &LayoutReport) -> Vec<SnapshotTarget> {
    let root = target(report, "/", "/@root", TargetKind::System, "System");
    let home = target(report, "/home", "/@home", TargetKind::Home, "Home");
    let mut targets = vec![root, home];
    for mount in &report.mounts {
        if ["/", "/home", "/.snapshots"].contains(&mount.mount_point.as_str())
            || mount.filesystem != "btrfs"
            || report.root_source.as_deref() != Some(mount.source.as_str())
        {
            continue;
        }
        targets.push(SnapshotTarget {
            id: format!("btrfs:{}", mount.subvolume.trim_start_matches('/')),
            kind: TargetKind::Custom,
            display_name: mount.mount_point.clone(),
            filesystem_source: mount.source.clone(),
            subvolume: mount.subvolume.clone(),
            mount_point: mount.mount_point.clone(),
            available: true,
            issue: None,
        });
    }
    targets.sort_by(|left, right| left.kind.cmp(&right.kind).then_with(|| left.id.cmp(&right.id)));
    targets.dedup_by(|left, right| left.id == right.id);
    targets
}

fn target(report: &LayoutReport, mount: &str, expected_subvolume: &str, kind: TargetKind, name: &str) -> SnapshotTarget {
    match report.mounts.iter().find(|item| item.mount_point == mount) {
        Some(item) if compatible(report, item, expected_subvolume) => SnapshotTarget {
            id: format!("btrfs:{}", item.subvolume.trim_start_matches('/')),
            kind,
            display_name: name.into(),
            filesystem_source: item.source.clone(),
            subvolume: item.subvolume.clone(),
            mount_point: item.mount_point.clone(),
            available: true,
            issue: None,
        },
        Some(item) => unavailable(kind, name, mount, format!(
            "{mount} is not an independent {expected_subvolume} Btrfs subvolume on the system filesystem (found {} on {})",
            item.subvolume, item.filesystem
        )),
        None => unavailable(kind, name, mount, format!("{mount} is not independently mounted")),
    }
}

fn compatible(report: &LayoutReport, item: &MountReport, expected: &str) -> bool {
    item.filesystem == "btrfs" && item.subvolume == expected
        && report.root_source.as_deref() == Some(item.source.as_str())
}

fn unavailable(kind: TargetKind, name: &str, mount: &str, issue: String) -> SnapshotTarget {
    SnapshotTarget { id: match kind { TargetKind::System => SYSTEM_TARGET_ID, TargetKind::Home => HOME_TARGET_ID, TargetKind::Custom => "btrfs:unavailable" }.into(), kind, display_name: name.into(), filesystem_source: String::new(), subvolume: String::new(), mount_point: mount.into(), available: false, issue: Some(issue) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::inspect_mountinfo;
    #[test]
    fn home_requires_an_independent_subvolume() {
        let report = inspect_mountinfo("25 1 0:32 /@root / rw - btrfs /dev/vda4 rw\n");
        let targets = discover_targets(&report);
        assert!(targets[0].available);
        assert!(!targets[1].available);
        assert!(targets[1].issue.as_deref().unwrap().contains("independently mounted"));
    }
}
