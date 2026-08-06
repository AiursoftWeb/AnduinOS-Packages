//! Read-only, non-authoritative Btrfs space measurements for presentation.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SnapshotSpace {
    #[serde(default)]
    pub referenced_bytes: Option<u64>,
    #[serde(default)]
    pub exclusive_bytes: Option<u64>,
    #[serde(default)]
    pub shared_bytes: Option<u64>,
    #[serde(default)]
    pub measured_at_unix_seconds: Option<i64>,
}
