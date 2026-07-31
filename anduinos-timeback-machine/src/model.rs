use std::fmt;
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::DEPLOYMENT_SCHEMA_VERSION;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct DeploymentId(Uuid);

impl DeploymentId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for DeploymentId {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Display for DeploymentId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.hyphenated().fmt(formatter)
    }
}

impl FromStr for DeploymentId {
    type Err = uuid::Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Uuid::parse_str(value).map(Self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum DeploymentKind {
    Factory,
    Manual,
    AptPre,
    AptPost,
    PreRollback,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum DeploymentState {
    Creating,
    Ready,
    Current,
    PendingRollback,
    BootedUnconfirmed,
    FallbackProtected,
    Incomplete,
    FailedReverted,
    Broken,
    Deleting,
}

impl DeploymentState {
    pub fn can_transition_to(self, next: Self) -> bool {
        use DeploymentState::*;
        matches!(
            (self, next),
            (Creating, Ready | Incomplete | Broken)
                | (
                    Ready,
                    PendingRollback | FallbackProtected | Deleting | Broken
                )
                | (Current, FallbackProtected | Broken)
                | (
                    PendingRollback,
                    BootedUnconfirmed | Ready | FailedReverted | Broken
                )
                | (BootedUnconfirmed, Current | FailedReverted | Broken)
                | (FallbackProtected, Ready | PendingRollback | Broken)
                | (Incomplete, Deleting | Broken)
                | (FailedReverted, Ready | Deleting | Broken)
                | (Broken, Deleting)
        )
    }

    pub fn protects_from_deletion(self) -> bool {
        matches!(
            self,
            Self::Current
                | Self::PendingRollback
                | Self::BootedUnconfirmed
                | Self::FallbackProtected
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DeploymentRecord {
    pub schema_version: u32,
    pub id: DeploymentId,
    pub parent_id: Option<DeploymentId>,
    pub kind: DeploymentKind,
    pub state: DeploymentState,
    pub created_at: DateTime<Utc>,
    pub title: String,
    pub reason: String,
    pub snapshot_uuid: Option<String>,
    pub snapshot_parent_uuid: Option<String>,
    pub kernel_release: Option<String>,
    pub initramfs_sha256: Option<String>,
    pub boot_artifact_sha256: Option<String>,
    pub dpkg_status_sha256: Option<String>,
    pub mok_certificate_sha256: Option<String>,
    pub pinned: bool,
    pub failure: Option<String>,
}

impl DeploymentRecord {
    pub fn validate(&self) -> Result<(), ModelError> {
        if self.schema_version != DEPLOYMENT_SCHEMA_VERSION {
            return Err(ModelError::UnsupportedSchema(self.schema_version));
        }
        if self.title.trim().is_empty() || self.title.chars().count() > 120 {
            return Err(ModelError::InvalidField("title"));
        }
        if self.reason.trim().is_empty() || self.reason.chars().count() > 500 {
            return Err(ModelError::InvalidField("reason"));
        }
        for (name, digest) in [
            ("initramfs_sha256", self.initramfs_sha256.as_deref()),
            ("boot_artifact_sha256", self.boot_artifact_sha256.as_deref()),
            ("dpkg_status_sha256", self.dpkg_status_sha256.as_deref()),
        ] {
            if digest.is_some_and(|value| !is_sha256(value)) {
                return Err(ModelError::InvalidDigest(name));
            }
        }
        if let Some(digest) = &self.mok_certificate_sha256 {
            if !is_sha256(digest) {
                return Err(ModelError::InvalidDigest("mok_certificate_sha256"));
            }
        }
        if self.requires_complete_identity()
            && (missing(&self.snapshot_uuid)
                || missing(&self.kernel_release)
                || self.initramfs_sha256.is_none()
                || self.boot_artifact_sha256.is_none()
                || self.dpkg_status_sha256.is_none())
        {
            return Err(ModelError::IncompleteIdentity);
        }
        Ok(())
    }

    pub fn can_restore(&self) -> bool {
        self.validate().is_ok()
            && self.failure.is_none()
            && matches!(
                self.state,
                DeploymentState::Ready | DeploymentState::FallbackProtected
            )
    }

    pub fn can_delete(&self) -> bool {
        !self.pinned
            && matches!(
                self.state,
                DeploymentState::Ready
                    | DeploymentState::Incomplete
                    | DeploymentState::FailedReverted
                    | DeploymentState::Broken
            )
    }

    fn requires_complete_identity(&self) -> bool {
        matches!(
            self.state,
            DeploymentState::Ready
                | DeploymentState::Current
                | DeploymentState::PendingRollback
                | DeploymentState::BootedUnconfirmed
                | DeploymentState::FallbackProtected
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ModelError {
    UnsupportedSchema(u32),
    InvalidField(&'static str),
    InvalidDigest(&'static str),
    IncompleteIdentity,
}

impl fmt::Display for ModelError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedSchema(version) => {
                write!(formatter, "unsupported deployment schema version {version}")
            }
            Self::InvalidField(field) => write!(formatter, "invalid field: {field}"),
            Self::InvalidDigest(field) => write!(formatter, "invalid SHA-256 digest: {field}"),
            Self::IncompleteIdentity => write!(formatter, "deployment identity is incomplete"),
        }
    }
}

impl std::error::Error for ModelError {}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn missing(value: &Option<String>) -> bool {
    value.as_ref().is_none_or(|item| item.trim().is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_record() -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind: DeploymentKind::Manual,
            state: DeploymentState::Ready,
            created_at: Utc::now(),
            title: "Before graphics driver update".into(),
            reason: "Manual recovery point".into(),
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: None,
            kernel_release: Some("7.0.0-28-generic".into()),
            initramfs_sha256: Some("a".repeat(64)),
            boot_artifact_sha256: Some("b".repeat(64)),
            dpkg_status_sha256: Some("c".repeat(64)),
            mok_certificate_sha256: Some("d".repeat(64)),
            pinned: false,
            failure: None,
        }
    }

    #[test]
    fn valid_ready_deployment_is_restorable() {
        let record = valid_record();
        assert_eq!(record.validate(), Ok(()));
        assert!(record.can_restore());
        assert!(record.can_delete());
    }

    #[test]
    fn uppercase_or_short_digests_are_rejected() {
        let mut record = valid_record();
        record.initramfs_sha256 = Some("A".repeat(64));
        assert_eq!(
            record.validate(),
            Err(ModelError::InvalidDigest("initramfs_sha256"))
        );
        record.initramfs_sha256 = Some("a".repeat(63));
        assert_eq!(
            record.validate(),
            Err(ModelError::InvalidDigest("initramfs_sha256"))
        );
    }

    #[test]
    fn protected_and_pinned_deployments_cannot_be_deleted() {
        let mut record = valid_record();
        record.state = DeploymentState::Current;
        assert!(!record.can_delete());
        record.state = DeploymentState::Ready;
        record.pinned = true;
        assert!(!record.can_delete());
        record.pinned = false;
        record.state = DeploymentState::Creating;
        assert!(!record.can_delete());
        record.state = DeploymentState::Deleting;
        assert!(!record.can_delete());
    }

    #[test]
    fn incomplete_transaction_may_be_recorded_but_not_restored() {
        let mut record = valid_record();
        record.state = DeploymentState::Incomplete;
        record.snapshot_uuid = None;
        record.kernel_release = None;
        record.initramfs_sha256 = None;
        record.boot_artifact_sha256 = None;
        record.dpkg_status_sha256 = None;
        record.failure = Some("Power was lost while creating the snapshot".into());
        assert_eq!(record.validate(), Ok(()));
        assert!(!record.can_restore());
        assert!(record.can_delete());
    }

    #[test]
    fn ready_deployment_requires_complete_identity() {
        let mut record = valid_record();
        record.initramfs_sha256 = None;
        assert_eq!(record.validate(), Err(ModelError::IncompleteIdentity));
        assert!(!record.can_restore());
    }

    #[test]
    fn state_machine_rejects_dangerous_shortcuts() {
        assert!(DeploymentState::Creating.can_transition_to(DeploymentState::Ready));
        assert!(DeploymentState::Ready.can_transition_to(DeploymentState::PendingRollback));
        assert!(
            DeploymentState::PendingRollback.can_transition_to(DeploymentState::BootedUnconfirmed)
        );
        assert!(DeploymentState::BootedUnconfirmed.can_transition_to(DeploymentState::Current));
        assert!(!DeploymentState::Ready.can_transition_to(DeploymentState::Current));
        assert!(!DeploymentState::Current.can_transition_to(DeploymentState::Deleting));
        assert!(!DeploymentState::Broken.can_transition_to(DeploymentState::Current));
    }

    #[test]
    fn deployment_id_round_trips_in_canonical_form() {
        let id = DeploymentId::new();
        assert_eq!(id.to_string().parse::<DeploymentId>().unwrap(), id);
        assert_eq!(id.to_string(), id.to_string().to_lowercase());
    }
}
