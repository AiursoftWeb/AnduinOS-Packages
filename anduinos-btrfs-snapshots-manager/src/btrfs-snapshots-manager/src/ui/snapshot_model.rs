use chrono::{DateTime, Utc};

use crate::dbus_client::{PersonalSnapshot, RecoveryDeployment};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum SnapshotScope {
    #[default]
    System,
    Home,
}

#[derive(Debug, Clone)]
pub struct SnapshotItem {
    pub id: String,
    pub title: String,
    pub created_at: DateTime<Utc>,
    pub reason: String,
    pub kind: String,
    pub state: String,
    pub keep_forever: bool,
    pub kernel: Option<String>,
    pub summary: Option<String>,
    pub space: Option<snapshots_manager_common::SnapshotSpace>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SnapshotCapabilities {
    pub can_browse: bool,
    pub can_verify: bool,
    pub can_restore: bool,
    pub can_delete: bool,
    pub can_pin: bool,
    pub can_rename: bool,
}

impl SnapshotCapabilities {
    pub fn for_item(scope: SnapshotScope, item: &SnapshotItem) -> Self {
        let stable = !matches!(item.state.as_str(), "creating" | "deleting");
        match scope {
            SnapshotScope::System => {
                let browsable = item.state == "ready";
                let deletable_state =
                    matches!(item.state.as_str(), "ready" | "incomplete" | "broken");
                Self {
                    can_browse: browsable,
                    can_verify: stable,
                    can_restore: item.state == "ready",
                    can_delete: deletable_state && !item.keep_forever,
                    can_pin: item.state == "ready",
                    can_rename: stable,
                }
            }
            SnapshotScope::Home => Self {
                can_browse: item.state == "ready",
                can_verify: stable,
                can_restore: false,
                can_delete: matches!(item.state.as_str(), "ready" | "broken") && !item.keep_forever,
                can_pin: item.state == "ready",
                can_rename: stable,
            },
        }
    }

    pub fn can_select(self) -> bool {
        self.can_delete
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PagePresentation {
    Unsupported,
    Empty,
    NoMatches,
    Content,
}

impl PagePresentation {
    pub fn after_load(available: bool, total: usize, visible: usize, query: &str) -> Self {
        if !available {
            Self::Unsupported
        } else if total == 0 {
            Self::Empty
        } else if visible == 0 && !query.trim().is_empty() {
            Self::NoMatches
        } else {
            Self::Content
        }
    }
}

impl From<RecoveryDeployment> for SnapshotItem {
    fn from(value: RecoveryDeployment) -> Self {
        Self {
            id: value.id,
            title: value.title,
            created_at: value.created_at,
            reason: value.reason,
            kind: value.kind,
            state: value.state,
            keep_forever: value.pinned,
            kernel: value.kernel_release,
            summary: None,
            space: None,
        }
    }
}

impl From<PersonalSnapshot> for SnapshotItem {
    fn from(value: PersonalSnapshot) -> Self {
        Self {
            id: value.id,
            title: value.title,
            created_at: value.created_at,
            reason: value.reason,
            kind: value.kind,
            state: value.state,
            keep_forever: value.pinned,
            kernel: None,
            summary: None,
            space: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(state: &str, pinned: bool) -> SnapshotItem {
        SnapshotItem {
            id: "id".into(),
            title: "Snapshot".into(),
            created_at: Utc::now(),
            reason: String::new(),
            kind: "manual".into(),
            state: state.into(),
            keep_forever: pinned,
            kernel: None,
            summary: None,
            space: None,
        }
    }

    #[test]
    fn ready_system_snapshot_exposes_all_supported_actions() {
        let capabilities =
            SnapshotCapabilities::for_item(SnapshotScope::System, &item("ready", false));
        assert!(capabilities.can_browse);
        assert!(capabilities.can_verify);
        assert!(capabilities.can_restore);
        assert!(capabilities.can_delete);
        assert!(capabilities.can_pin);
        assert!(capabilities.can_rename);
    }

    #[test]
    fn ready_system_snapshot_is_always_reusable() {
        let capabilities =
            SnapshotCapabilities::for_item(SnapshotScope::System, &item("ready", false));
        assert!(capabilities.can_restore);
    }

    #[test]
    fn completed_rollback_fallback_is_an_ordinary_deletable_snapshot() {
        let mut fallback = item("ready", false);
        fallback.kind = "pre-rollback".into();
        let capabilities = SnapshotCapabilities::for_item(SnapshotScope::System, &fallback);
        assert!(capabilities.can_delete);
        assert!(capabilities.can_pin);
    }

    #[test]
    fn damaged_snapshots_can_be_checked_and_safely_removed() {
        for state in ["broken", "incomplete"] {
            let capabilities =
                SnapshotCapabilities::for_item(SnapshotScope::System, &item(state, false));
            assert!(capabilities.can_verify, "{state}");
            assert!(capabilities.can_delete, "{state}");
            assert!(!capabilities.can_browse, "{state}");
            assert!(!capabilities.can_restore, "{state}");
        }
    }

    #[test]
    fn home_never_exposes_whole_snapshot_restore() {
        let capabilities =
            SnapshotCapabilities::for_item(SnapshotScope::Home, &item("ready", false));
        assert!(capabilities.can_browse);
        assert!(!capabilities.can_restore);
    }

    #[test]
    fn pinned_snapshots_are_not_batch_selectable() {
        let capabilities =
            SnapshotCapabilities::for_item(SnapshotScope::Home, &item("ready", true));
        assert!(!capabilities.can_delete);
        assert!(!capabilities.can_select());
        assert!(capabilities.can_pin);
    }

    #[test]
    fn page_presentation_distinguishes_empty_search_and_unsupported() {
        assert_eq!(
            PagePresentation::after_load(false, 3, 3, ""),
            PagePresentation::Unsupported
        );
        assert_eq!(
            PagePresentation::after_load(true, 0, 0, ""),
            PagePresentation::Empty
        );
        assert_eq!(
            PagePresentation::after_load(true, 3, 0, "missing"),
            PagePresentation::NoMatches
        );
        assert_eq!(
            PagePresentation::after_load(true, 3, 2, ""),
            PagePresentation::Content
        );
    }
}
