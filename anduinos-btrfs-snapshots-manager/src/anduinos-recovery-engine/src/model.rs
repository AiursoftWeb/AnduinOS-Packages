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
    Automatic,
    AptPre,
    AptPost,
    PreRollback,
    Imported,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum DeploymentState {
    Creating,
    #[serde(
        alias = "current",
        alias = "pending-rollback",
        alias = "booted-unconfirmed",
        alias = "fallback-protected",
        alias = "failed-reverted"
    )]
    Ready,
    Incomplete,
    Broken,
    Deleting,
}

impl DeploymentState {
    pub fn can_transition_to(self, next: Self) -> bool {
        use DeploymentState::*;
        matches!(
            (self, next),
            (Creating, Ready | Incomplete | Broken | Deleting)
                | (Ready, Deleting | Broken)
                | (Incomplete, Deleting | Broken)
                | (Broken, Deleting)
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub schedule_id: Option<String>,
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
        if invalid_text(&self.title, 120) {
            return Err(ModelError::InvalidField("title"));
        }
        if invalid_text(&self.reason, 500) {
            return Err(ModelError::InvalidField("reason"));
        }
        match (self.kind, &self.schedule_id) {
            (DeploymentKind::Automatic, Some(schedule_id)) => {
                if schedule_id.is_empty()
                    || schedule_id.len() > 50
                    || schedule_id.starts_with(['-', '.'])
                    || !schedule_id
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
                {
                    return Err(ModelError::InvalidField("schedule_id"));
                }
            }
            (DeploymentKind::Automatic, None) | (_, Some(_)) => {
                return Err(ModelError::InvalidField("schedule_id"));
            }
            _ => {}
        }
        for (name, value) in [
            ("snapshot_uuid", self.snapshot_uuid.as_deref()),
            ("snapshot_parent_uuid", self.snapshot_parent_uuid.as_deref()),
        ] {
            if value.is_some_and(|uuid| Uuid::parse_str(uuid).is_err()) {
                return Err(ModelError::InvalidField(name));
            }
        }
        if self.kernel_release.as_deref().is_some_and(|release| {
            release.is_empty()
                || release.len() > 128
                || !release
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
        }) {
            return Err(ModelError::InvalidField("kernel_release"));
        }
        if self
            .failure
            .as_deref()
            .is_some_and(|failure| invalid_optional_text(failure, 2000))
        {
            return Err(ModelError::InvalidField("failure"));
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
        if let Some(digest) = &self.mok_certificate_sha256
            && !is_sha256(digest)
        {
            return Err(ModelError::InvalidDigest("mok_certificate_sha256"));
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
        self.validate().is_ok() && self.failure.is_none() && self.state == DeploymentState::Ready
    }

    pub fn can_delete(&self) -> bool {
        !self.pinned
            && matches!(
                self.state,
                DeploymentState::Creating
                    | DeploymentState::Ready
                    | DeploymentState::Incomplete
                    | DeploymentState::Broken
            )
    }

    fn requires_complete_identity(&self) -> bool {
        self.state == DeploymentState::Ready
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

fn invalid_text(value: &str, maximum_characters: usize) -> bool {
    value.trim().is_empty()
        || value.chars().count() > maximum_characters
        || value.chars().any(char::is_control)
}

fn invalid_optional_text(value: &str, maximum_characters: usize) -> bool {
    value.chars().count() > maximum_characters || value.chars().any(char::is_control)
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
            reason: "Manual system snapshot".into(),
            schedule_id: None,
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
    fn pinned_or_unfinished_deployments_cannot_be_deleted() {
        let mut record = valid_record();
        record.pinned = true;
        assert!(!record.can_delete());
        record.pinned = false;
        record.state = DeploymentState::Creating;
        assert!(record.can_delete());
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
    fn automatic_deployments_require_a_bounded_schedule_identity() {
        let mut record = valid_record();
        record.kind = DeploymentKind::Automatic;
        assert_eq!(
            record.validate(),
            Err(ModelError::InvalidField("schedule_id"))
        );

        record.schedule_id = Some("daily".into());
        assert_eq!(record.validate(), Ok(()));

        record.schedule_id = Some("../daily".into());
        assert_eq!(
            record.validate(),
            Err(ModelError::InvalidField("schedule_id"))
        );

        record.kind = DeploymentKind::Manual;
        assert_eq!(
            record.validate(),
            Err(ModelError::InvalidField("schedule_id"))
        );
    }

    #[test]
    fn state_machine_rejects_dangerous_shortcuts() {
        assert!(DeploymentState::Creating.can_transition_to(DeploymentState::Ready));
        assert!(DeploymentState::Creating.can_transition_to(DeploymentState::Deleting));
        assert!(DeploymentState::Ready.can_transition_to(DeploymentState::Deleting));
        assert!(DeploymentState::Ready.can_transition_to(DeploymentState::Broken));
        assert!(!DeploymentState::Broken.can_transition_to(DeploymentState::Ready));
    }

    #[test]
    fn legacy_transaction_and_identity_states_normalize_to_ready() {
        for legacy in [
            "current",
            "pending-rollback",
            "booted-unconfirmed",
            "fallback-protected",
            "failed-reverted",
        ] {
            let state: DeploymentState = serde_json::from_str(&format!("\"{legacy}\"")).unwrap();
            assert_eq!(state, DeploymentState::Ready, "{legacy}");
        }
        assert_eq!(
            serde_json::to_string(&DeploymentState::Ready).unwrap(),
            "\"ready\""
        );
    }

    #[test]
    fn deployment_id_round_trips_in_canonical_form() {
        let id = DeploymentId::new();
        assert_eq!(id.to_string().parse::<DeploymentId>().unwrap(), id);
        assert_eq!(id.to_string(), id.to_string().to_lowercase());
    }

    #[test]
    fn display_fields_reject_control_characters_and_oversized_values() {
        let mut record = valid_record();
        record.title = "Trusted\nterminal escape".into();
        assert_eq!(record.validate(), Err(ModelError::InvalidField("title")));

        let mut record = valid_record();
        record.failure = Some("x".repeat(2001));
        assert_eq!(record.validate(), Err(ModelError::InvalidField("failure")));
    }

    #[test]
    fn snapshot_and_kernel_identities_are_typed_and_bounded() {
        let mut record = valid_record();
        record.snapshot_uuid = Some("not-a-uuid".into());
        assert_eq!(
            record.validate(),
            Err(ModelError::InvalidField("snapshot_uuid"))
        );

        let mut record = valid_record();
        record.kernel_release = Some("../../boot/vmlinuz".into());
        assert_eq!(
            record.validate(),
            Err(ModelError::InvalidField("kernel_release"))
        );
    }
}
