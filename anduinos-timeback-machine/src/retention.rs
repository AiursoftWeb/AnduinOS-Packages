use std::collections::{HashMap, HashSet};
use std::ffi::CString;
use std::fmt;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

use crate::model::{DeploymentId, DeploymentKind, DeploymentRecord, DeploymentState};
use crate::operations::OperationEngine;
use crate::package_transaction::{
    PackageTransaction, PackageTransactionId, PackageTransactionPhase, PackageTransactionStore,
};
use crate::store::DeploymentStore;
use crate::transaction::TransactionStore;
use crate::{layout, SNAPSHOT_ROOT};

const GIB: u64 = 1024 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RetentionPolicy {
    pub maximum_complete_transactions: usize,
    pub minimum_complete_transactions: usize,
    pub maximum_age_days: i64,
    pub minimum_restorable_deployments: usize,
    pub minimum_free_percent: u8,
    pub minimum_free_bytes: u64,
    pub maximum_free_target_bytes: u64,
}

impl Default for RetentionPolicy {
    fn default() -> Self {
        Self {
            maximum_complete_transactions: 10,
            minimum_complete_transactions: 2,
            maximum_age_days: 30,
            minimum_restorable_deployments: 1,
            minimum_free_percent: 10,
            minimum_free_bytes: 4 * GIB,
            maximum_free_target_bytes: 32 * GIB,
        }
    }
}

impl RetentionPolicy {
    pub fn validate(self) -> Result<(), RetentionError> {
        if self.minimum_complete_transactions > self.maximum_complete_transactions {
            return Err(RetentionError::InvalidPolicy(
                "The minimum complete transaction count exceeds the maximum".into(),
            ));
        }
        if self.maximum_complete_transactions > 10_000
            || self.minimum_complete_transactions > 10_000
        {
            return Err(RetentionError::InvalidPolicy(
                "The transaction count limit is excessive".into(),
            ));
        }
        if !(1..=3_650).contains(&self.maximum_age_days) {
            return Err(RetentionError::InvalidPolicy(
                "The maximum age must be between 1 and 3650 days".into(),
            ));
        }
        if self.minimum_restorable_deployments == 0 || self.minimum_restorable_deployments > 100 {
            return Err(RetentionError::InvalidPolicy(
                "The restorable deployment floor is invalid".into(),
            ));
        }
        if self.minimum_free_percent > 50 {
            return Err(RetentionError::InvalidPolicy(
                "The free-space percentage cannot exceed 50".into(),
            ));
        }
        if self.minimum_free_bytes > self.maximum_free_target_bytes {
            return Err(RetentionError::InvalidPolicy(
                "The free-space floor exceeds its cap".into(),
            ));
        }
        Ok(())
    }

    pub fn free_space_target(self, total_bytes: u64) -> u64 {
        let percentage = total_bytes.saturating_mul(u64::from(self.minimum_free_percent)) / 100;
        percentage
            .max(self.minimum_free_bytes)
            .min(self.maximum_free_target_bytes)
            .min(total_bytes / 4)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SpaceStatus {
    pub total_bytes: u64,
    pub available_bytes: u64,
}

impl SpaceStatus {
    pub fn target(self, policy: RetentionPolicy) -> u64 {
        policy.free_space_target(self.total_bytes)
    }

    pub fn is_under_pressure(self, policy: RetentionPolicy) -> bool {
        self.available_bytes < self.target(policy)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RetentionReason {
    TransactionLimit,
    AgeLimit,
    SpacePressurePost,
    SpacePressurePre,
    InterruptedTransaction,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RetentionAction {
    pub transaction_id: PackageTransactionId,
    pub deployment_id: DeploymentId,
    pub kind: DeploymentKind,
    pub reason: RetentionReason,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RetentionPlan {
    pub policy: RetentionPolicy,
    pub space: SpaceStatus,
    pub free_space_target_bytes: u64,
    pub under_space_pressure: bool,
    pub restorable_deployments: usize,
    pub actions: Vec<RetentionAction>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RetentionProtection {
    deployment_ids: HashSet<DeploymentId>,
}

impl RetentionProtection {
    pub fn new(deployment_ids: impl IntoIterator<Item = DeploymentId>) -> Self {
        Self {
            deployment_ids: deployment_ids.into_iter().collect(),
        }
    }

    pub fn contains(&self, id: DeploymentId) -> bool {
        self.deployment_ids.contains(&id)
    }
}

impl Default for RetentionProtection {
    fn default() -> Self {
        Self::new([])
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RetentionError {
    InvalidPolicy(String),
}

impl fmt::Display for RetentionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidPolicy(message) => message.fmt(formatter),
        }
    }
}

impl std::error::Error for RetentionError {}

pub fn plan_retention(
    policy: RetentionPolicy,
    now: DateTime<Utc>,
    space: SpaceStatus,
    deployments: &[DeploymentRecord],
    history: &[PackageTransaction],
    protection: &RetentionProtection,
) -> Result<RetentionPlan, RetentionError> {
    policy.validate()?;
    let under_pressure = space.is_under_pressure(policy);
    let records = deployments
        .iter()
        .map(|record| (record.id, record))
        .collect::<HashMap<_, _>>();
    let mut restorable_remaining = deployments
        .iter()
        .filter(|record| record.can_restore())
        .count();
    let initial_restorable = restorable_remaining;
    let mut complete = history
        .iter()
        .filter(|transaction| transaction.phase == PackageTransactionPhase::Complete)
        .collect::<Vec<_>>();
    complete.sort_by(|left, right| {
        right
            .created_at
            .cmp(&left.created_at)
            .then_with(|| left.id.to_string().cmp(&right.id.to_string()))
    });

    let mut normal_groups = Vec::new();
    let mut pressure_groups = Vec::new();
    let maximum_age = Duration::days(policy.maximum_age_days);
    for (index, transaction) in complete.iter().enumerate() {
        if index < policy.minimum_complete_transactions {
            continue;
        }
        let age_expired = now.signed_duration_since(transaction.created_at) > maximum_age;
        let over_limit = index >= policy.maximum_complete_transactions;
        if age_expired || over_limit {
            normal_groups.push((
                *transaction,
                if over_limit {
                    RetentionReason::TransactionLimit
                } else {
                    RetentionReason::AgeLimit
                },
            ));
        } else if under_pressure {
            pressure_groups.push(*transaction);
        }
    }
    normal_groups.sort_by_key(|(transaction, _)| transaction.created_at);
    pressure_groups.sort_by_key(|transaction| transaction.created_at);

    let mut actions = Vec::new();
    let mut selected = HashSet::new();
    for (transaction, reason) in normal_groups {
        select_transaction_pair(
            transaction,
            reason,
            &records,
            protection,
            policy,
            &mut restorable_remaining,
            &mut selected,
            &mut actions,
        );
    }

    if under_pressure {
        // A post-update point is less valuable than its matching pre-update
        // point while space is scarce. Select all eligible posts first so an
        // executor which re-measures after every deletion can stop early.
        for transaction in &pressure_groups {
            select_reference(
                transaction,
                transaction.post_deployment_id,
                DeploymentKind::AptPost,
                RetentionReason::SpacePressurePost,
                &records,
                protection,
                policy,
                &mut restorable_remaining,
                &mut selected,
                &mut actions,
            );
        }
        for transaction in &pressure_groups {
            select_reference(
                transaction,
                transaction.pre_deployment_id,
                DeploymentKind::AptPre,
                RetentionReason::SpacePressurePre,
                &records,
                protection,
                policy,
                &mut restorable_remaining,
                &mut selected,
                &mut actions,
            );
        }
    }

    let mut interrupted = history
        .iter()
        .filter(|transaction| transaction.phase == PackageTransactionPhase::Interrupted)
        .collect::<Vec<_>>();
    interrupted.sort_by_key(|transaction| transaction.created_at);
    for transaction in interrupted {
        let age_expired = now.signed_duration_since(transaction.created_at) > maximum_age;
        if age_expired || under_pressure {
            select_reference(
                transaction,
                transaction.pre_deployment_id,
                DeploymentKind::AptPre,
                RetentionReason::InterruptedTransaction,
                &records,
                protection,
                policy,
                &mut restorable_remaining,
                &mut selected,
                &mut actions,
            );
        }
    }

    Ok(RetentionPlan {
        policy,
        space,
        free_space_target_bytes: space.target(policy),
        under_space_pressure: under_pressure,
        restorable_deployments: initial_restorable,
        actions,
    })
}

#[allow(clippy::too_many_arguments)]
fn select_transaction_pair(
    transaction: &PackageTransaction,
    reason: RetentionReason,
    records: &HashMap<DeploymentId, &DeploymentRecord>,
    protection: &RetentionProtection,
    policy: RetentionPolicy,
    restorable_remaining: &mut usize,
    selected: &mut HashSet<DeploymentId>,
    actions: &mut Vec<RetentionAction>,
) {
    let references = [
        (transaction.post_deployment_id, DeploymentKind::AptPost),
        (transaction.pre_deployment_id, DeploymentKind::AptPre),
    ];
    let candidates = references
        .into_iter()
        .filter_map(|(id, kind)| id.map(|id| (id, kind)))
        .filter_map(|(id, kind)| records.get(&id).map(|record| (id, kind, *record)))
        .collect::<Vec<_>>();
    if candidates.is_empty()
        || candidates
            .iter()
            .any(|(id, kind, record)| !is_eligible(*id, *kind, record, protection, selected))
    {
        return;
    }
    let restorable_candidates = candidates
        .iter()
        .filter(|(_, _, record)| record.can_restore())
        .count();
    if restorable_remaining.saturating_sub(restorable_candidates)
        < policy.minimum_restorable_deployments
    {
        return;
    }
    *restorable_remaining -= restorable_candidates;
    for (id, _kind, record) in candidates {
        selected.insert(id);
        actions.push(RetentionAction {
            transaction_id: transaction.id,
            deployment_id: id,
            kind: record.kind,
            reason,
        });
    }
}

#[allow(clippy::too_many_arguments)]
fn select_reference(
    transaction: &PackageTransaction,
    deployment_id: Option<DeploymentId>,
    expected_kind: DeploymentKind,
    reason: RetentionReason,
    records: &HashMap<DeploymentId, &DeploymentRecord>,
    protection: &RetentionProtection,
    policy: RetentionPolicy,
    restorable_remaining: &mut usize,
    selected: &mut HashSet<DeploymentId>,
    actions: &mut Vec<RetentionAction>,
) {
    let Some(id) = deployment_id else {
        return;
    };
    let Some(record) = records.get(&id) else {
        return;
    };
    if !is_eligible(id, expected_kind, record, protection, selected) {
        return;
    }
    if record.can_restore() {
        if *restorable_remaining <= policy.minimum_restorable_deployments {
            return;
        }
        *restorable_remaining -= 1;
    }
    selected.insert(id);
    actions.push(RetentionAction {
        transaction_id: transaction.id,
        deployment_id: id,
        kind: record.kind,
        reason,
    });
}

fn is_eligible(
    id: DeploymentId,
    expected_kind: DeploymentKind,
    record: &DeploymentRecord,
    protection: &RetentionProtection,
    selected: &HashSet<DeploymentId>,
) -> bool {
    record.kind == expected_kind
        && record.state == DeploymentState::Ready
        && !record.pinned
        && !protection.contains(id)
        && !selected.contains(&id)
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RetentionExecutionReport {
    pub initial_space: SpaceStatus,
    pub final_space: SpaceStatus,
    pub free_space_target_bytes: u64,
    pub deleted: Vec<RetentionAction>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetentionExecutionErrorCode {
    UnsupportedLayout,
    UnsafeMetadata,
    SpaceAccounting,
    DeleteFailed,
}

impl RetentionExecutionErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::UnsupportedLayout => "unsupported-layout",
            Self::UnsafeMetadata => "unsafe-metadata",
            Self::SpaceAccounting => "space-accounting",
            Self::DeleteFailed => "delete-failed",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RetentionExecutionError {
    pub code: RetentionExecutionErrorCode,
    pub message: String,
}

impl RetentionExecutionError {
    fn new(code: RetentionExecutionErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for RetentionExecutionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for RetentionExecutionError {}

pub trait RetentionBackend: Clone + Send + Sync + 'static {
    fn is_supported(&self) -> bool;
    fn space_status(&self) -> Result<SpaceStatus, RetentionExecutionError>;
    fn deployments(&self) -> Result<Vec<DeploymentRecord>, RetentionExecutionError>;
    fn package_history(&self) -> Result<Vec<PackageTransaction>, RetentionExecutionError>;
    fn protection(&self) -> Result<RetentionProtection, RetentionExecutionError>;
    fn delete_automatic(
        &self,
        id: DeploymentId,
        minimum_restorable_deployments: usize,
    ) -> Result<(), RetentionExecutionError>;
}

#[derive(Clone, Debug)]
pub struct SystemRetentionBackend {
    snapshot_root: PathBuf,
}

impl Default for SystemRetentionBackend {
    fn default() -> Self {
        Self {
            snapshot_root: PathBuf::from(SNAPSHOT_ROOT),
        }
    }
}

impl SystemRetentionBackend {
    pub fn new(snapshot_root: impl Into<PathBuf>) -> Self {
        Self {
            snapshot_root: snapshot_root.into(),
        }
    }
}

impl RetentionBackend for SystemRetentionBackend {
    fn is_supported(&self) -> bool {
        layout::inspect_current().is_supported()
    }

    fn space_status(&self) -> Result<SpaceStatus, RetentionExecutionError> {
        filesystem_space(&self.snapshot_root)
    }

    fn deployments(&self) -> Result<Vec<DeploymentRecord>, RetentionExecutionError> {
        let report = DeploymentStore::new(&self.snapshot_root).discover();
        if !report.issues.is_empty() {
            return Err(RetentionExecutionError::new(
                RetentionExecutionErrorCode::UnsafeMetadata,
                format!(
                    "Automatic cleanup stopped because {} deployment metadata issue(s) require attention",
                    report.issues.len()
                ),
            ));
        }
        Ok(report.deployments)
    }

    fn package_history(&self) -> Result<Vec<PackageTransaction>, RetentionExecutionError> {
        PackageTransactionStore::new(&self.snapshot_root)
            .load_history()
            .map_err(|error| {
                RetentionExecutionError::new(
                    RetentionExecutionErrorCode::UnsafeMetadata,
                    format!("Automatic cleanup could not trust package history: {error}"),
                )
            })
    }

    fn protection(&self) -> Result<RetentionProtection, RetentionExecutionError> {
        let mut protected = Vec::new();
        if let Some(transaction) = TransactionStore::new(&self.snapshot_root)
            .load_pending()
            .map_err(|error| {
                RetentionExecutionError::new(
                    RetentionExecutionErrorCode::UnsafeMetadata,
                    format!("Could not inspect the pending rollback: {error}"),
                )
            })?
        {
            protected.extend([
                transaction.target_deployment_id,
                transaction.fallback_deployment_id,
            ]);
        }
        if let Some(transaction) = PackageTransactionStore::new(&self.snapshot_root)
            .load_pending()
            .map_err(|error| {
                RetentionExecutionError::new(
                    RetentionExecutionErrorCode::UnsafeMetadata,
                    format!("Could not inspect the pending package transaction: {error}"),
                )
            })?
        {
            protected.extend(transaction.pre_deployment_id);
            protected.extend(transaction.post_deployment_id);
        }
        Ok(RetentionProtection::new(protected))
    }

    fn delete_automatic(
        &self,
        id: DeploymentId,
        minimum_restorable_deployments: usize,
    ) -> Result<(), RetentionExecutionError> {
        OperationEngine::default()
            .delete_automatic(
                &layout::inspect_current(),
                id,
                minimum_restorable_deployments,
            )
            .map_err(|error| {
                RetentionExecutionError::new(
                    RetentionExecutionErrorCode::DeleteFailed,
                    format!("Could not delete automatic recovery point {id}: {error}"),
                )
            })
    }
}

#[derive(Clone, Debug)]
pub struct RetentionCoordinator<B = SystemRetentionBackend> {
    backend: B,
    policy: RetentionPolicy,
}

impl Default for RetentionCoordinator<SystemRetentionBackend> {
    fn default() -> Self {
        Self::new(
            SystemRetentionBackend::default(),
            RetentionPolicy::default(),
        )
    }
}

impl<B: RetentionBackend> RetentionCoordinator<B> {
    pub fn new(backend: B, policy: RetentionPolicy) -> Self {
        Self { backend, policy }
    }

    pub fn inspect(&self) -> Result<RetentionPlan, RetentionExecutionError> {
        if !self.backend.is_supported() {
            return Err(RetentionExecutionError::new(
                RetentionExecutionErrorCode::UnsupportedLayout,
                "The complete AnduinOS Btrfs layout is required for retention",
            ));
        }
        self.policy.validate().map_err(|error| {
            RetentionExecutionError::new(
                RetentionExecutionErrorCode::UnsafeMetadata,
                format!("Retention policy is invalid: {error}"),
            )
        })?;
        let space = self.backend.space_status()?;
        let deployments = self.backend.deployments()?;
        let history = self.backend.package_history()?;
        let protection = self.backend.protection()?;
        plan_retention(
            self.policy,
            Utc::now(),
            space,
            &deployments,
            &history,
            &protection,
        )
        .map_err(|error| {
            RetentionExecutionError::new(
                RetentionExecutionErrorCode::UnsafeMetadata,
                format!("Could not construct a safe retention plan: {error}"),
            )
        })
    }

    pub fn apply(&self) -> Result<RetentionExecutionReport, RetentionExecutionError> {
        let initial = self.inspect()?;
        let mut deleted = Vec::new();
        for _ in 0..10_000 {
            let plan = self.inspect()?;
            let Some(action) = plan.actions.into_iter().next() else {
                let final_space = self.backend.space_status()?;
                return Ok(RetentionExecutionReport {
                    initial_space: initial.space,
                    final_space,
                    free_space_target_bytes: initial.free_space_target_bytes,
                    deleted,
                });
            };
            self.backend.delete_automatic(
                action.deployment_id,
                self.policy.minimum_restorable_deployments,
            )?;
            deleted.push(action);
        }
        Err(RetentionExecutionError::new(
            RetentionExecutionErrorCode::UnsafeMetadata,
            "Retention exceeded its bounded deletion count",
        ))
    }
}

fn filesystem_space(path: &Path) -> Result<SpaceStatus, RetentionExecutionError> {
    let bytes = path.as_os_str().as_bytes();
    let path = CString::new(bytes).map_err(|_| {
        RetentionExecutionError::new(
            RetentionExecutionErrorCode::SpaceAccounting,
            "The recovery store path contains an embedded NUL byte",
        )
    })?;
    let mut status = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    let result = unsafe { libc::statvfs(path.as_ptr(), status.as_mut_ptr()) };
    if result != 0 {
        return Err(RetentionExecutionError::new(
            RetentionExecutionErrorCode::SpaceAccounting,
            format!(
                "Could not read Btrfs free space: {}",
                std::io::Error::last_os_error()
            ),
        ));
    }
    let status = unsafe { status.assume_init() };
    let fragment_size = status.f_frsize;
    Ok(SpaceStatus {
        total_bytes: status.f_blocks.saturating_mul(fragment_size),
        available_bytes: status.f_bavail.saturating_mul(fragment_size),
    })
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use chrono::TimeZone;
    use uuid::Uuid;

    use super::*;
    use crate::DEPLOYMENT_SCHEMA_VERSION;

    fn now() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 8, 1, 0, 0, 0).unwrap()
    }

    fn deployment(kind: DeploymentKind, days_old: i64) -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind,
            state: DeploymentState::Ready,
            created_at: now() - Duration::days(days_old),
            title: "Automatic package recovery point".into(),
            reason: "Package transaction".into(),
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: None,
            kernel_release: Some("7.0.0-28-generic".into()),
            initramfs_sha256: Some("a".repeat(64)),
            boot_artifact_sha256: Some("b".repeat(64)),
            dpkg_status_sha256: Some("c".repeat(64)),
            mok_certificate_sha256: None,
            pinned: false,
            failure: None,
        }
    }

    fn complete(pre: DeploymentId, post: DeploymentId, days_old: i64) -> PackageTransaction {
        let created_at = now() - Duration::days(days_old);
        PackageTransaction {
            schema_version: 1,
            id: PackageTransactionId::new(),
            phase: PackageTransactionPhase::Complete,
            created_at,
            updated_at: created_at,
            pre_deployment_id: Some(pre),
            post_deployment_id: Some(post),
            failure: None,
        }
    }

    fn interrupted(pre: DeploymentId, days_old: i64) -> PackageTransaction {
        let created_at = now() - Duration::days(days_old);
        PackageTransaction {
            schema_version: 1,
            id: PackageTransactionId::new(),
            phase: PackageTransactionPhase::Interrupted,
            created_at,
            updated_at: created_at,
            pre_deployment_id: Some(pre),
            post_deployment_id: None,
            failure: Some("APT was interrupted".into()),
        }
    }

    fn roomy() -> SpaceStatus {
        SpaceStatus {
            total_bytes: 100 * GIB,
            available_bytes: 50 * GIB,
        }
    }

    #[test]
    fn policy_uses_a_bounded_free_space_target() {
        let policy = RetentionPolicy::default();
        assert_eq!(policy.free_space_target(8 * GIB), 2 * GIB);
        assert_eq!(policy.free_space_target(100 * GIB), 10 * GIB);
        assert_eq!(policy.free_space_target(1024 * GIB), 32 * GIB);
    }

    #[test]
    fn normal_cleanup_keeps_the_two_newest_and_deletes_old_pairs_together() {
        let mut deployments = Vec::new();
        let mut history = Vec::new();
        for days in [1, 2, 31] {
            let pre = deployment(DeploymentKind::AptPre, days);
            let post = deployment(DeploymentKind::AptPost, days);
            history.push(complete(pre.id, post.id, days));
            deployments.extend([pre, post]);
        }
        let plan = plan_retention(
            RetentionPolicy::default(),
            now(),
            roomy(),
            &deployments,
            &history,
            &RetentionProtection::default(),
        )
        .unwrap();
        assert!(!plan.under_space_pressure);
        assert_eq!(plan.actions.len(), 2);
        assert_eq!(plan.actions[0].kind, DeploymentKind::AptPost);
        assert_eq!(plan.actions[1].kind, DeploymentKind::AptPre);
        assert!(plan
            .actions
            .iter()
            .all(|action| action.reason == RetentionReason::AgeLimit));
    }

    #[test]
    fn pressure_prefers_post_points_but_preserves_two_complete_pairs() {
        let mut deployments = Vec::new();
        let mut history = Vec::new();
        for days in [1, 2, 3, 4] {
            let pre = deployment(DeploymentKind::AptPre, days);
            let post = deployment(DeploymentKind::AptPost, days);
            history.push(complete(pre.id, post.id, days));
            deployments.extend([pre, post]);
        }
        let pressure = SpaceStatus {
            total_bytes: 100 * GIB,
            available_bytes: GIB,
        };
        let plan = plan_retention(
            RetentionPolicy::default(),
            now(),
            pressure,
            &deployments,
            &history,
            &RetentionProtection::default(),
        )
        .unwrap();
        assert!(plan.under_space_pressure);
        assert_eq!(plan.actions.len(), 4);
        assert!(plan.actions[..2]
            .iter()
            .all(|action| action.kind == DeploymentKind::AptPost));
        assert!(plan.actions[2..]
            .iter()
            .all(|action| action.kind == DeploymentKind::AptPre));
    }

    #[test]
    fn pinned_protected_and_non_ready_points_are_never_selected() {
        let mut pre = deployment(DeploymentKind::AptPre, 40);
        let mut post = deployment(DeploymentKind::AptPost, 40);
        pre.pinned = true;
        post.state = DeploymentState::PendingRollback;
        let history = vec![complete(pre.id, post.id, 40)];
        let plan = plan_retention(
            RetentionPolicy {
                minimum_complete_transactions: 0,
                ..RetentionPolicy::default()
            },
            now(),
            roomy(),
            &[pre, post],
            &history,
            &RetentionProtection::default(),
        )
        .unwrap();
        assert!(plan.actions.is_empty());
    }

    #[test]
    fn one_protected_member_preserves_the_whole_normal_pair() {
        let pre = deployment(DeploymentKind::AptPre, 40);
        let post = deployment(DeploymentKind::AptPost, 40);
        let history = vec![complete(pre.id, post.id, 40)];
        let plan = plan_retention(
            RetentionPolicy {
                minimum_complete_transactions: 0,
                ..RetentionPolicy::default()
            },
            now(),
            roomy(),
            &[pre.clone(), post],
            &history,
            &RetentionProtection::new([pre.id]),
        )
        .unwrap();
        assert!(plan.actions.is_empty());
    }

    #[test]
    fn sole_restorable_deployment_is_never_selected() {
        let pre = deployment(DeploymentKind::AptPre, 40);
        let mut post = deployment(DeploymentKind::AptPost, 40);
        post.state = DeploymentState::Broken;
        let history = vec![complete(pre.id, post.id, 40)];
        let plan = plan_retention(
            RetentionPolicy {
                minimum_complete_transactions: 0,
                ..RetentionPolicy::default()
            },
            now(),
            roomy(),
            &[pre, post],
            &history,
            &RetentionProtection::default(),
        )
        .unwrap();
        assert!(plan.actions.is_empty());
    }

    #[test]
    fn policy_rejects_disabling_the_known_good_floor() {
        let interrupted_pre = deployment(DeploymentKind::AptPre, 40);
        let transaction = interrupted(interrupted_pre.id, 40);
        let plan = plan_retention(
            RetentionPolicy {
                minimum_restorable_deployments: 0,
                ..RetentionPolicy::default()
            },
            now(),
            roomy(),
            &[interrupted_pre],
            &[transaction],
            &RetentionProtection::default(),
        );
        assert!(matches!(plan, Err(RetentionError::InvalidPolicy(_))));
    }

    #[test]
    fn interrupted_pre_point_is_eligible_when_another_known_good_point_exists() {
        let interrupted_pre = deployment(DeploymentKind::AptPre, 40);
        let manual = deployment(DeploymentKind::Manual, 1);
        let transaction = interrupted(interrupted_pre.id, 40);
        let plan = plan_retention(
            RetentionPolicy::default(),
            now(),
            roomy(),
            &[interrupted_pre, manual],
            &[transaction],
            &RetentionProtection::default(),
        )
        .unwrap();
        assert_eq!(plan.actions.len(), 1);
        assert_eq!(
            plan.actions[0].reason,
            RetentionReason::InterruptedTransaction
        );
    }

    #[derive(Clone)]
    struct FakeRetentionBackend {
        state: Arc<Mutex<FakeRetentionState>>,
    }

    struct FakeRetentionState {
        supported: bool,
        space: SpaceStatus,
        deployments: Vec<DeploymentRecord>,
        history: Vec<PackageTransaction>,
        deleted: Vec<DeploymentId>,
    }

    impl RetentionBackend for FakeRetentionBackend {
        fn is_supported(&self) -> bool {
            self.state.lock().unwrap().supported
        }

        fn space_status(&self) -> Result<SpaceStatus, RetentionExecutionError> {
            Ok(self.state.lock().unwrap().space)
        }

        fn deployments(&self) -> Result<Vec<DeploymentRecord>, RetentionExecutionError> {
            Ok(self.state.lock().unwrap().deployments.clone())
        }

        fn package_history(&self) -> Result<Vec<PackageTransaction>, RetentionExecutionError> {
            Ok(self.state.lock().unwrap().history.clone())
        }

        fn protection(&self) -> Result<RetentionProtection, RetentionExecutionError> {
            Ok(RetentionProtection::default())
        }

        fn delete_automatic(
            &self,
            id: DeploymentId,
            _minimum_restorable_deployments: usize,
        ) -> Result<(), RetentionExecutionError> {
            let mut state = self.state.lock().unwrap();
            let index = state
                .deployments
                .iter()
                .position(|record| record.id == id)
                .ok_or_else(|| {
                    RetentionExecutionError::new(
                        RetentionExecutionErrorCode::DeleteFailed,
                        "unknown fake deployment",
                    )
                })?;
            state.deployments.remove(index);
            state.deleted.push(id);
            state.space.available_bytes = 12 * GIB;
            Ok(())
        }
    }

    #[test]
    fn coordinator_remeasures_and_stops_after_pressure_is_relieved() {
        let mut deployments = Vec::new();
        let mut history = Vec::new();
        for days in [0, 0, 0] {
            let pre = deployment(DeploymentKind::AptPre, days);
            let post = deployment(DeploymentKind::AptPost, days);
            history.push(complete(pre.id, post.id, days));
            deployments.extend([pre, post]);
        }
        let state = Arc::new(Mutex::new(FakeRetentionState {
            supported: true,
            space: SpaceStatus {
                total_bytes: 100 * GIB,
                available_bytes: GIB,
            },
            deployments,
            history,
            deleted: Vec::new(),
        }));
        let report = RetentionCoordinator::new(
            FakeRetentionBackend {
                state: state.clone(),
            },
            RetentionPolicy::default(),
        )
        .apply()
        .unwrap();
        assert_eq!(report.deleted.len(), 1);
        assert_eq!(report.deleted[0].kind, DeploymentKind::AptPost);
        assert_eq!(state.lock().unwrap().deleted.len(), 1);
        assert!(!report
            .final_space
            .is_under_pressure(RetentionPolicy::default()));
    }

    #[test]
    fn coordinator_refuses_an_unsupported_layout_without_reading_metadata() {
        let backend = FakeRetentionBackend {
            state: Arc::new(Mutex::new(FakeRetentionState {
                supported: false,
                space: roomy(),
                deployments: Vec::new(),
                history: Vec::new(),
                deleted: Vec::new(),
            })),
        };
        assert_eq!(
            RetentionCoordinator::new(backend, RetentionPolicy::default())
                .inspect()
                .unwrap_err()
                .code,
            RetentionExecutionErrorCode::UnsupportedLayout
        );
    }
}
