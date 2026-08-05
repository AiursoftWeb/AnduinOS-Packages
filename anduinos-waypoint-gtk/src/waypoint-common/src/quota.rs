//! Read-only Btrfs space-accounting values.
//!
//! Waypoint deliberately does not expose quota mutation or a synthetic space
//! limit until the recovery engine can enforce reclaimable-space reserves.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct SnapshotSpace {
    pub referenced_bytes: Option<u64>,
    pub exclusive_bytes: Option<u64>,
}

impl SnapshotSpace {
    pub fn estimated_reclaimable_bytes(self) -> Option<u64> {
        self.exclusive_bytes
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QuotaUsage {
    /// Sum of referenced bytes reported by level-zero qgroups.
    pub referenced: u64,

    /// Sum of exclusive bytes reported by level-zero qgroups.
    /// This is an estimate and not a deletion guarantee.
    pub exclusive: u64,
}
