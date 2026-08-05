use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::RECOVERY_STORE_ROOT;
use crate::model::{DeploymentId, DeploymentKind, DeploymentRecord, DeploymentState};
use crate::transaction::RollbackTransaction;

pub const LINEAGE_SCHEMA_VERSION: u32 = 1;
const MAX_LINEAGE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_LINEAGE_NODES: usize = 100_000;
const MAX_ACTIVATION_EVENTS: usize = 10_000;
const LINEAGE_FILE: &str = "system-lineage.json";
const LINEAGE_LOCK: &str = "system-lineage.lock";

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum LineageRelation {
    Exact,
    LegacyUnknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ActivationOutcome {
    Confirmed,
    Reverted,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LineageNode {
    pub recovery_point_id: DeploymentId,
    pub parent_id: Option<DeploymentId>,
    pub relation: LineageRelation,
    pub created_at: DateTime<Utc>,
    pub kind: DeploymentKind,
    pub title: String,
    pub snapshot_available: bool,
    pub removed_at: Option<DateTime<Utc>>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ActivationEvent {
    pub transaction_id: String,
    pub previous_head_id: Option<DeploymentId>,
    pub target_recovery_point_id: DeploymentId,
    pub safety_recovery_point_id: DeploymentId,
    pub scheduled_at: DateTime<Utc>,
    pub completed_at: DateTime<Utc>,
    pub outcome: ActivationOutcome,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SystemLineage {
    pub schema_version: u32,
    pub revision: u64,
    pub current_head_id: Option<DeploymentId>,
    pub nodes: Vec<LineageNode>,
    pub activations: Vec<ActivationEvent>,
}

impl SystemLineage {
    fn migrate(deployments: &[DeploymentRecord]) -> Self {
        let current_head_id = deployments
            .iter()
            .find(|record| record.state == DeploymentState::Current)
            .map(|record| record.id);
        let nodes = deployments
            .iter()
            .filter(|record| record.snapshot_uuid.is_some())
            .map(|record| LineageNode {
                recovery_point_id: record.id,
                parent_id: None,
                relation: LineageRelation::LegacyUnknown,
                created_at: record.created_at,
                kind: record.kind,
                title: record.title.clone(),
                snapshot_available: record.state != DeploymentState::Deleting,
                removed_at: None,
            })
            .collect();
        Self {
            schema_version: LINEAGE_SCHEMA_VERSION,
            revision: 1,
            current_head_id,
            nodes,
            activations: Vec::new(),
        }
    }

    pub fn validate(&self) -> Result<(), LineageError> {
        if self.schema_version != LINEAGE_SCHEMA_VERSION {
            return Err(LineageError::invalid("unsupported system lineage schema"));
        }
        if self.nodes.len() > MAX_LINEAGE_NODES || self.activations.len() > MAX_ACTIVATION_EVENTS {
            return Err(LineageError::invalid(
                "system lineage exceeds its safety limit",
            ));
        }
        let mut node_ids = HashSet::with_capacity(self.nodes.len());
        for node in &self.nodes {
            if !node_ids.insert(node.recovery_point_id) {
                return Err(LineageError::invalid(
                    "system lineage contains duplicate nodes",
                ));
            }
            if node.parent_id == Some(node.recovery_point_id) {
                return Err(LineageError::invalid("a lineage node cannot parent itself"));
            }
            if node.title.trim().is_empty()
                || node.title.chars().count() > 120
                || node.title.chars().any(char::is_control)
            {
                return Err(LineageError::invalid("a lineage node has an invalid title"));
            }
            if node.snapshot_available == node.removed_at.is_some() {
                return Err(LineageError::invalid(
                    "lineage snapshot availability is inconsistent",
                ));
            }
            if node.relation == LineageRelation::LegacyUnknown && node.parent_id.is_some() {
                return Err(LineageError::invalid(
                    "legacy lineage nodes cannot claim an exact parent",
                ));
            }
        }
        if self
            .current_head_id
            .is_some_and(|current| !node_ids.contains(&current))
        {
            return Err(LineageError::invalid("the current lineage head is missing"));
        }
        for node in &self.nodes {
            if node
                .parent_id
                .is_some_and(|parent| !node_ids.contains(&parent))
            {
                return Err(LineageError::invalid("a lineage parent is missing"));
            }
        }
        validate_acyclic(&self.nodes)?;

        let mut transaction_ids = HashSet::with_capacity(self.activations.len());
        for event in &self.activations {
            let parsed = Uuid::parse_str(&event.transaction_id)
                .map_err(|_| LineageError::invalid("an activation transaction ID is invalid"))?;
            if parsed.hyphenated().to_string() != event.transaction_id
                || !transaction_ids.insert(event.transaction_id.as_str())
            {
                return Err(LineageError::invalid(
                    "activation transaction IDs must be unique canonical UUIDs",
                ));
            }
            if event.completed_at < event.scheduled_at
                || !node_ids.contains(&event.target_recovery_point_id)
                || !node_ids.contains(&event.safety_recovery_point_id)
                || event
                    .previous_head_id
                    .is_some_and(|head| !node_ids.contains(&head))
            {
                return Err(LineageError::invalid("an activation event is inconsistent"));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LineageErrorCode {
    UnsafePath,
    InvalidRecord,
    Io,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LineageError {
    pub code: LineageErrorCode,
    pub message: String,
}

impl LineageError {
    fn new(code: LineageErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    fn invalid(message: impl Into<String>) -> Self {
        Self::new(LineageErrorCode::InvalidRecord, message)
    }
}

impl fmt::Display for LineageError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for LineageError {}

#[derive(Clone, Debug)]
pub struct LineageStore {
    history_directory: PathBuf,
}

impl Default for LineageStore {
    fn default() -> Self {
        Self::new(RECOVERY_STORE_ROOT)
    }
}

impl LineageStore {
    pub fn new(snapshot_root: impl AsRef<Path>) -> Self {
        Self {
            history_directory: snapshot_root.as_ref().join("history"),
        }
    }

    pub fn load(&self) -> Result<Option<SystemLineage>, LineageError> {
        load_from(&self.history_directory.join(LINEAGE_FILE))
    }

    pub fn ensure_initialized(
        &self,
        deployments: &[DeploymentRecord],
    ) -> Result<SystemLineage, LineageError> {
        self.mutate(|lineage| {
            if lineage.is_none() {
                *lineage = Some(SystemLineage::migrate(deployments));
            }
            Ok(false)
        })
    }

    pub fn record_recovery_point(
        &self,
        record: &DeploymentRecord,
    ) -> Result<SystemLineage, LineageError> {
        if record.snapshot_uuid.is_none() {
            return Err(LineageError::invalid(
                "a lineage node requires a completed recovery point",
            ));
        }
        self.mutate(|lineage| {
            let lineage = lineage
                .as_mut()
                .ok_or_else(|| LineageError::invalid("system lineage is not initialized"))?;
            if lineage
                .nodes
                .iter()
                .any(|node| node.recovery_point_id == record.id)
            {
                return Ok(false);
            }
            lineage.nodes.push(LineageNode {
                recovery_point_id: record.id,
                parent_id: lineage.current_head_id,
                relation: LineageRelation::Exact,
                created_at: record.created_at,
                kind: record.kind,
                title: record.title.clone(),
                snapshot_available: true,
                removed_at: None,
            });
            lineage.current_head_id = Some(record.id);
            Ok(true)
        })
    }

    /// Register a valid recovery point whose historical relationship to the
    /// currently running system cannot be proven. External imports use this
    /// path: merely copying a recovery point back to local storage must not
    /// claim that the machine is currently running from it.
    pub fn record_detached_recovery_point(
        &self,
        record: &DeploymentRecord,
    ) -> Result<SystemLineage, LineageError> {
        if record.snapshot_uuid.is_none() {
            return Err(LineageError::invalid(
                "a detached lineage node requires a completed recovery point",
            ));
        }
        self.mutate(|lineage| {
            let lineage = lineage
                .as_mut()
                .ok_or_else(|| LineageError::invalid("system lineage is not initialized"))?;
            if lineage
                .nodes
                .iter()
                .any(|node| node.recovery_point_id == record.id)
            {
                return Ok(false);
            }
            lineage.nodes.push(LineageNode {
                recovery_point_id: record.id,
                parent_id: None,
                relation: LineageRelation::LegacyUnknown,
                created_at: record.created_at,
                kind: record.kind,
                title: record.title.clone(),
                snapshot_available: true,
                removed_at: None,
            });
            Ok(true)
        })
    }

    pub fn record_activation(
        &self,
        transaction: &RollbackTransaction,
        outcome: ActivationOutcome,
        completed_at: DateTime<Utc>,
    ) -> Result<SystemLineage, LineageError> {
        self.mutate(|lineage| {
            let lineage = lineage
                .as_mut()
                .ok_or_else(|| LineageError::invalid("system lineage is not initialized"))?;
            let transaction_id = transaction.id.to_string();
            if let Some(existing) = lineage
                .activations
                .iter()
                .find(|event| event.transaction_id == transaction_id)
            {
                if existing.outcome != outcome {
                    return Err(LineageError::invalid(
                        "an activation transaction changed its terminal outcome",
                    ));
                }
                return Ok(false);
            }
            let known = |id| {
                lineage
                    .nodes
                    .iter()
                    .any(|node| node.recovery_point_id == id)
            };
            if !known(transaction.target_deployment_id)
                || !known(transaction.fallback_deployment_id)
            {
                return Err(LineageError::invalid(
                    "activation recovery points are missing from the lineage",
                ));
            }
            let previous_head_id = lineage.current_head_id;
            lineage.activations.push(ActivationEvent {
                transaction_id,
                previous_head_id,
                target_recovery_point_id: transaction.target_deployment_id,
                safety_recovery_point_id: transaction.fallback_deployment_id,
                scheduled_at: transaction.created_at,
                completed_at,
                outcome,
            });
            lineage.current_head_id = Some(match outcome {
                ActivationOutcome::Confirmed => transaction.target_deployment_id,
                ActivationOutcome::Reverted => transaction.fallback_deployment_id,
            });
            Ok(true)
        })
    }

    pub fn mark_snapshot_removed(
        &self,
        recovery_point_id: DeploymentId,
        removed_at: DateTime<Utc>,
    ) -> Result<SystemLineage, LineageError> {
        self.mutate(|lineage| {
            let lineage = lineage
                .as_mut()
                .ok_or_else(|| LineageError::invalid("system lineage is not initialized"))?;
            let node = lineage
                .nodes
                .iter_mut()
                .find(|node| node.recovery_point_id == recovery_point_id)
                .ok_or_else(|| {
                    LineageError::invalid("deleted recovery point is missing from the lineage")
                })?;
            if !node.snapshot_available {
                return Ok(false);
            }
            node.snapshot_available = false;
            node.removed_at = Some(removed_at);
            Ok(true)
        })
    }

    fn mutate<F>(&self, mutation: F) -> Result<SystemLineage, LineageError>
    where
        F: FnOnce(&mut Option<SystemLineage>) -> Result<bool, LineageError>,
    {
        ensure_history_directory(&self.history_directory)?;
        let lock = open_lock(&self.history_directory.join(LINEAGE_LOCK))?;
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) } != 0 {
            return Err(io_error(
                "Could not lock the system lineage",
                io::Error::last_os_error(),
            ));
        }
        let path = self.history_directory.join(LINEAGE_FILE);
        let mut lineage = load_from(&path)?;
        let changed = mutation(&mut lineage)?;
        let mut lineage = lineage
            .ok_or_else(|| LineageError::invalid("system lineage mutation produced no record"))?;
        if changed {
            lineage.revision = lineage
                .revision
                .checked_add(1)
                .ok_or_else(|| LineageError::invalid("system lineage revision cannot advance"))?;
        }
        lineage.validate()?;
        if changed || !path.exists() {
            save_to(&path, &lineage)?;
        }
        Ok(lineage)
    }
}

fn validate_acyclic(nodes: &[LineageNode]) -> Result<(), LineageError> {
    let parents = nodes
        .iter()
        .map(|node| (node.recovery_point_id, node.parent_id))
        .collect::<HashMap<_, _>>();
    let mut completed = HashSet::new();
    for node in nodes {
        let mut path = HashSet::new();
        let mut cursor = Some(node.recovery_point_id);
        while let Some(id) = cursor {
            if completed.contains(&id) {
                break;
            }
            if !path.insert(id) {
                return Err(LineageError::invalid("system lineage contains a cycle"));
            }
            cursor = parents.get(&id).copied().flatten();
        }
        completed.extend(path);
    }
    Ok(())
}

fn ensure_history_directory(path: &Path) -> Result<(), LineageError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(()),
        Ok(_) => Err(LineageError::new(
            LineageErrorCode::UnsafePath,
            "system lineage path is not a real directory",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let parent = path.parent().ok_or_else(|| {
                LineageError::new(LineageErrorCode::UnsafePath, "lineage parent is missing")
            })?;
            let parent_metadata = fs::symlink_metadata(parent)
                .map_err(|error| io_error("Could not inspect the lineage parent", error))?;
            if !parent_metadata.file_type().is_dir() {
                return Err(LineageError::new(
                    LineageErrorCode::UnsafePath,
                    "lineage parent is not a real directory",
                ));
            }
            fs::create_dir(path)
                .map_err(|error| io_error("Could not create system history", error))?;
            fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                .map_err(|error| io_error("Could not protect system history", error))
        }
        Err(error) => Err(io_error("Could not inspect system history", error)),
    }
}

fn open_lock(path: &Path) -> Result<File, LineageError> {
    OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| io_error("Could not open the system lineage lock", error))
}

fn load_from(path: &Path) -> Result<Option<SystemLineage>, LineageError> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(io_error("Could not inspect system lineage", error)),
    };
    if !metadata.file_type().is_file() {
        return Err(LineageError::new(
            LineageErrorCode::UnsafePath,
            "system lineage is not a regular file",
        ));
    }
    if metadata.len() > MAX_LINEAGE_BYTES {
        return Err(LineageError::invalid(
            "system lineage exceeds its size limit",
        ));
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| io_error("Could not open system lineage", error))?;
    let mut contents = Vec::with_capacity(metadata.len() as usize);
    file.take(MAX_LINEAGE_BYTES + 1)
        .read_to_end(&mut contents)
        .map_err(|error| io_error("Could not read system lineage", error))?;
    if contents.len() as u64 > MAX_LINEAGE_BYTES {
        return Err(LineageError::invalid(
            "system lineage exceeds its size limit",
        ));
    }
    let lineage = serde_json::from_slice::<SystemLineage>(&contents).map_err(|error| {
        LineageError::invalid(format!("System lineage is invalid JSON: {error}"))
    })?;
    lineage.validate()?;
    Ok(Some(lineage))
}

fn save_to(path: &Path, lineage: &SystemLineage) -> Result<(), LineageError> {
    lineage.validate()?;
    let serialized = serde_json::to_vec_pretty(lineage)
        .map_err(|error| LineageError::invalid(format!("Could not serialize lineage: {error}")))?;
    if serialized.len() as u64 > MAX_LINEAGE_BYTES {
        return Err(LineageError::invalid(
            "system lineage exceeds its size limit",
        ));
    }
    let directory = path.parent().ok_or_else(|| {
        LineageError::new(LineageErrorCode::UnsafePath, "lineage directory is missing")
    })?;
    let temporary = directory.join(format!(
        ".system-lineage-{}.tmp",
        Uuid::new_v4().hyphenated()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)?;
        file.write_all(&serialized)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        File::open(directory)?.sync_all()
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(io_error("Could not atomically save system lineage", error));
    }
    Ok(())
}

fn io_error(context: &str, error: io::Error) -> LineageError {
    LineageError::new(LineageErrorCode::Io, format!("{context}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::DEPLOYMENT_SCHEMA_VERSION;
    use crate::transaction::{RollbackPhase, RollbackTransaction};

    fn temporary_root() -> PathBuf {
        std::env::temp_dir().join(format!("waypoint-lineage-{}", Uuid::new_v4()))
    }

    fn record(title: &str, state: DeploymentState, created_at: DateTime<Utc>) -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind: DeploymentKind::Manual,
            state,
            created_at,
            title: title.into(),
            reason: "Test recovery point".into(),
            snapshot_uuid: Some(Uuid::new_v4().to_string()),
            snapshot_parent_uuid: None,
            kernel_release: Some("6.0-test".into()),
            initramfs_sha256: Some("a".repeat(64)),
            boot_artifact_sha256: Some("b".repeat(64)),
            dpkg_status_sha256: Some("c".repeat(64)),
            mok_certificate_sha256: None,
            pinned: false,
            failure: None,
        }
    }

    fn setup() -> (PathBuf, LineageStore) {
        let root = temporary_root();
        fs::create_dir(&root).unwrap();
        let store = LineageStore::new(&root);
        (root, store)
    }

    #[test]
    fn legacy_points_are_migrated_without_inventing_relationships() {
        let (root, store) = setup();
        let current = record("Current origin", DeploymentState::Current, Utc::now());
        let older = record("Older", DeploymentState::Ready, Utc::now());
        let lineage = store
            .ensure_initialized(&[older.clone(), current.clone()])
            .unwrap();
        assert_eq!(lineage.current_head_id, Some(current.id));
        assert!(lineage.nodes.iter().all(|node| {
            node.parent_id.is_none() && node.relation == LineageRelation::LegacyUnknown
        }));
        assert_eq!(store.load().unwrap(), Some(lineage));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn new_points_form_an_exact_chain_and_activation_creates_a_branch() {
        let (root, store) = setup();
        let origin = record("Origin", DeploymentState::Current, Utc::now());
        store
            .ensure_initialized(std::slice::from_ref(&origin))
            .unwrap();
        let first = record("First", DeploymentState::Ready, Utc::now());
        let second = record("Second", DeploymentState::Ready, Utc::now());
        store.record_recovery_point(&first).unwrap();
        let lineage = store.record_recovery_point(&second).unwrap();
        assert_eq!(lineage.current_head_id, Some(second.id));
        assert_eq!(
            lineage
                .nodes
                .iter()
                .find(|node| node.recovery_point_id == first.id)
                .unwrap()
                .parent_id,
            Some(origin.id)
        );
        assert_eq!(
            lineage
                .nodes
                .iter()
                .find(|node| node.recovery_point_id == second.id)
                .unwrap()
                .parent_id,
            Some(first.id)
        );

        let mut transaction =
            RollbackTransaction::new(first.id, second.id, Uuid::new_v4().to_string(), "6.0-test");
        transaction
            .transition(RollbackPhase::Armed, Utc::now())
            .unwrap();
        let lineage = store
            .record_activation(&transaction, ActivationOutcome::Confirmed, Utc::now())
            .unwrap();
        assert_eq!(lineage.current_head_id, Some(first.id));
        assert_eq!(lineage.activations[0].previous_head_id, Some(second.id));
        let repeated = store
            .record_activation(&transaction, ActivationOutcome::Confirmed, Utc::now())
            .unwrap();
        assert_eq!(repeated.activations.len(), 1);
        let removed = store.mark_snapshot_removed(second.id, Utc::now()).unwrap();
        let tombstone = removed
            .nodes
            .iter()
            .find(|node| node.recovery_point_id == second.id)
            .unwrap();
        assert!(!tombstone.snapshot_available);
        assert!(tombstone.removed_at.is_some());
        assert_eq!(removed.current_head_id, Some(first.id));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn detached_import_does_not_claim_to_be_the_running_head() {
        let (root, store) = setup();
        let origin = record("Current origin", DeploymentState::Current, Utc::now());
        store
            .ensure_initialized(std::slice::from_ref(&origin))
            .unwrap();
        let mut imported = record("Imported history", DeploymentState::Ready, Utc::now());
        imported.kind = DeploymentKind::Imported;
        let lineage = store.record_detached_recovery_point(&imported).unwrap();
        assert_eq!(lineage.current_head_id, Some(origin.id));
        let node = lineage
            .nodes
            .iter()
            .find(|node| node.recovery_point_id == imported.id)
            .unwrap();
        assert_eq!(node.parent_id, None);
        assert_eq!(node.relation, LineageRelation::LegacyUnknown);

        let next = record("Next local point", DeploymentState::Ready, Utc::now());
        let lineage = store.record_recovery_point(&next).unwrap();
        let next_node = lineage
            .nodes
            .iter()
            .find(|node| node.recovery_point_id == next.id)
            .unwrap();
        assert_eq!(next_node.parent_id, Some(origin.id));
        assert_eq!(lineage.current_head_id, Some(next.id));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn symlinks_and_cycles_are_rejected() {
        let (root, store) = setup();
        fs::create_dir(root.join("history")).unwrap();
        std::os::unix::fs::symlink("/etc/passwd", root.join("history/system-lineage.json"))
            .unwrap();
        assert_eq!(store.load().unwrap_err().code, LineageErrorCode::UnsafePath);
        fs::remove_dir_all(&root).unwrap();

        let first = record("First", DeploymentState::Ready, Utc::now());
        let second = record("Second", DeploymentState::Ready, Utc::now());
        let lineage = SystemLineage {
            schema_version: LINEAGE_SCHEMA_VERSION,
            revision: 1,
            current_head_id: Some(first.id),
            nodes: vec![
                LineageNode {
                    recovery_point_id: first.id,
                    parent_id: Some(second.id),
                    relation: LineageRelation::Exact,
                    created_at: first.created_at,
                    kind: first.kind,
                    title: first.title,
                    snapshot_available: true,
                    removed_at: None,
                },
                LineageNode {
                    recovery_point_id: second.id,
                    parent_id: Some(first.id),
                    relation: LineageRelation::Exact,
                    created_at: second.created_at,
                    kind: second.kind,
                    title: second.title,
                    snapshot_available: true,
                    removed_at: None,
                },
            ],
            activations: vec![],
        };
        assert_eq!(
            lineage.validate().unwrap_err().code,
            LineageErrorCode::InvalidRecord
        );
    }
}
