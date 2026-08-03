use std::cell::RefCell;
use std::collections::HashMap;
use std::fmt;
use std::fs::File;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use gio::glib::VariantTy;
use gio::prelude::{ToVariant, UnixFDListExtManual};

use crate::automatic_home::HomeSnapshotRecord;
use crate::automation::{AutomaticConfiguration, AutomaticStatus};
use crate::browsing::{DirectoryListing, OpenedFileMetadata};
use crate::layout::LayoutReport;
use crate::lineage::SystemLineage;
use crate::model::SnapshotTarget;
use crate::retention::RetentionPlan;
use crate::store::DiscoveryReport;
use crate::{DBUS_INTERFACE, DBUS_NAME, DBUS_PATH};

const READ_ONLY_CALL_TIMEOUT_MS: i32 = 3_000;
const MUTATING_CALL_TIMEOUT_MS: i32 = 300_000;
const BROWSE_CALL_TIMEOUT_MS: i32 = 30_000;
const OPERATION_TIMEOUT_SECONDS: u32 = 600;

#[derive(Clone, Debug, PartialEq)]
pub struct OperationProgress {
    pub operation_id: String,
    pub phase: String,
    pub fraction: f64,
    pub message: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OperationResult {
    pub operation_id: String,
    pub success: bool,
    pub error_code: String,
    pub message: String,
}

#[derive(Debug)]
pub struct ClientError(String);

impl fmt::Display for ClientError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

impl std::error::Error for ClientError {}

pub fn inspect_layout() -> Result<LayoutReport, ClientError> {
    let json = call_json_method("InspectLayout")?;
    serde_json::from_str(&json).map_err(|error| {
        ClientError(format!(
            "The daemon returned an invalid layout report: {error}"
        ))
    })
}

pub fn list_deployments() -> Result<DiscoveryReport, ClientError> {
    let json = call_json_method("ListDeployments")?;
    serde_json::from_str(&json).map_err(|error| {
        ClientError(format!(
            "The daemon returned an invalid deployment report: {error}"
        ))
    })
}

pub fn inspect_system_history() -> Result<SystemLineage, ClientError> {
    serde_json::from_str(&call_json_method("InspectSystemHistory")?).map_err(|error| {
        ClientError(format!(
            "The daemon returned invalid system history: {error}"
        ))
    })
}

pub fn inspect_retention() -> Result<RetentionPlan, ClientError> {
    let json = call_json_method("InspectRetention")?;
    serde_json::from_str(&json).map_err(|error| {
        ClientError(format!(
            "The daemon returned an invalid retention report: {error}"
        ))
    })
}

pub fn inspect_automatic() -> Result<AutomaticStatus, ClientError> {
    serde_json::from_str(&call_json_method("InspectAutomatic")?).map_err(|error| {
        ClientError(format!(
            "The daemon returned invalid automatic-snapshot status: {error}"
        ))
    })
}

pub fn list_home_snapshots() -> Result<Vec<HomeSnapshotRecord>, ClientError> {
    serde_json::from_str(&call_json_method("ListHomeSnapshots")?).map_err(|error| {
        ClientError(format!(
            "The daemon returned an invalid Home snapshot list: {error}"
        ))
    })
}

pub fn begin_snapshot_browse(
    snapshot_kind: &str,
    snapshot_id: &str,
) -> Result<String, ClientError> {
    let connection = system_bus()?;
    let reply_type = VariantTy::new("(s)").expect("static D-Bus reply type is valid");
    let reply = connection
        .call_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            "BeginSnapshotBrowse",
            Some(&(snapshot_kind, snapshot_id).to_variant()),
            Some(reply_type),
            gio::DBusCallFlags::NONE,
            MUTATING_CALL_TIMEOUT_MS,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("Could not start snapshot browsing: {error}")))?;
    Ok(reply.child_get::<String>(0))
}

pub fn close_snapshot_browse(session_id: &str) -> Result<(), ClientError> {
    system_bus()?
        .call_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            "CloseSnapshotBrowse",
            Some(&(session_id,).to_variant()),
            None,
            gio::DBusCallFlags::NONE,
            BROWSE_CALL_TIMEOUT_MS,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("Could not close snapshot browsing: {error}")))?;
    Ok(())
}

pub fn keep_snapshot_browse_alive(session_id: &str) -> Result<(), ClientError> {
    system_bus()?
        .call_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            "KeepSnapshotBrowseAlive",
            Some(&(session_id,).to_variant()),
            None,
            gio::DBusCallFlags::NONE,
            BROWSE_CALL_TIMEOUT_MS,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| {
            ClientError(format!("Could not keep snapshot browsing active: {error}"))
        })?;
    Ok(())
}

pub fn list_snapshot_directory_session(
    session_id: &str,
    path: &[String],
    offset: usize,
    limit: usize,
    sort_mode: &str,
    descending: bool,
) -> Result<DirectoryListing, ClientError> {
    let path_json = serde_json::to_string(path)
        .map_err(|error| ClientError(format!("Could not encode the browser path: {error}")))?;
    let offset = u32::try_from(offset)
        .map_err(|_| ClientError("Directory page offset is too large".into()))?;
    let limit = u32::try_from(limit)
        .map_err(|_| ClientError("Directory page limit is too large".into()))?;
    let connection = system_bus()?;
    let reply_type = VariantTy::new("(s)").expect("static D-Bus reply type is valid");
    let reply = connection
        .call_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            "ListSnapshotDirectorySession",
            Some(&(session_id, path_json, offset, limit, sort_mode, descending).to_variant()),
            Some(reply_type),
            gio::DBusCallFlags::NONE,
            BROWSE_CALL_TIMEOUT_MS,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("Could not list snapshot files: {error}")))?;
    serde_json::from_str(&reply.child_get::<String>(0))
        .map_err(|error| ClientError(format!("The daemon returned an invalid listing: {error}")))
}

pub fn list_snapshot_directory_session_all(
    session_id: &str,
    path: &[String],
) -> Result<DirectoryListing, ClientError> {
    let mut offset = 0usize;
    let mut entries = Vec::new();
    let mut truncated = false;
    let total_entries = loop {
        let page = list_snapshot_directory_session(session_id, path, offset, 1_000, "name", false)?;
        let total_entries = page.total_entries;
        truncated |= page.truncated;
        entries.extend(page.entries);
        match page.next_offset {
            Some(next) if next > offset => offset = next,
            Some(_) => return Err(ClientError("The daemon returned an invalid cursor".into())),
            None => break total_entries,
        }
    };
    Ok(DirectoryListing {
        path: path.to_vec(),
        entries,
        total_entries,
        next_offset: None,
        truncated,
    })
}

pub fn open_snapshot_file_session(
    session_id: &str,
    path: &[String],
) -> Result<(File, OpenedFileMetadata), ClientError> {
    let path_json = serde_json::to_string(path)
        .map_err(|error| ClientError(format!("Could not encode the browser path: {error}")))?;
    let connection = system_bus()?;
    let reply_type = VariantTy::new("(hs)").expect("static D-Bus reply type is valid");
    let (reply, descriptors) = connection
        .call_with_unix_fd_list_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            "OpenSnapshotFileSession",
            Some(&(session_id, path_json).to_variant()),
            Some(reply_type),
            gio::DBusCallFlags::NONE,
            BROWSE_CALL_TIMEOUT_MS,
            gio::UnixFDList::NONE,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("Could not open the snapshot file: {error}")))?;
    let handle = reply.child_get::<gio::glib::variant::Handle>(0).0;
    let descriptors = descriptors
        .ok_or_else(|| ClientError("The daemon did not return a file descriptor".into()))?;
    let descriptor = descriptors
        .get(handle)
        .map_err(|error| ClientError(format!("Could not receive the snapshot file: {error}")))?;
    let metadata = serde_json::from_str(&reply.child_get::<String>(1)).map_err(|error| {
        ClientError(format!(
            "The daemon returned invalid file metadata: {error}"
        ))
    })?;
    Ok((File::from(descriptor), metadata))
}

pub fn list_snapshot_directory(
    snapshot_kind: &str,
    snapshot_id: &str,
    path: &[String],
) -> Result<DirectoryListing, ClientError> {
    list_snapshot_directory_page(snapshot_kind, snapshot_id, path, 0, 1_000)
}

pub fn list_snapshot_directory_page(
    snapshot_kind: &str,
    snapshot_id: &str,
    path: &[String],
    offset: usize,
    limit: usize,
) -> Result<DirectoryListing, ClientError> {
    list_snapshot_directory_page_sorted(
        snapshot_kind,
        snapshot_id,
        path,
        offset,
        limit,
        "name",
        false,
    )
}

pub fn list_snapshot_directory_page_sorted(
    snapshot_kind: &str,
    snapshot_id: &str,
    path: &[String],
    offset: usize,
    limit: usize,
    sort_mode: &str,
    descending: bool,
) -> Result<DirectoryListing, ClientError> {
    let offset = u32::try_from(offset)
        .map_err(|_| ClientError("Directory page offset is too large".into()))?;
    let limit = u32::try_from(limit)
        .map_err(|_| ClientError("Directory page limit is too large".into()))?;
    let path_json = serde_json::to_string(path)
        .map_err(|error| ClientError(format!("Could not encode the browser path: {error}")))?;
    let connection = system_bus()?;
    let reply_type = VariantTy::new("(s)").expect("static D-Bus reply type is valid");
    let reply = connection
        .call_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            "ListSnapshotDirectoryPageSorted",
            Some(
                &(
                    snapshot_kind,
                    snapshot_id,
                    path_json,
                    offset,
                    limit,
                    sort_mode,
                    descending,
                )
                    .to_variant(),
            ),
            Some(reply_type),
            gio::DBusCallFlags::NONE,
            MUTATING_CALL_TIMEOUT_MS,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("Could not list snapshot files: {error}")))?;
    serde_json::from_str(&reply.child_get::<String>(0))
        .map_err(|error| ClientError(format!("The daemon returned an invalid listing: {error}")))
}

pub fn list_snapshot_directory_all(
    snapshot_kind: &str,
    snapshot_id: &str,
    path: &[String],
) -> Result<DirectoryListing, ClientError> {
    let mut offset = 0usize;
    let mut entries = Vec::new();
    let mut truncated = false;
    let total_entries = loop {
        let page = list_snapshot_directory_page(snapshot_kind, snapshot_id, path, offset, 1_000)?;
        let total_entries = page.total_entries;
        truncated |= page.truncated;
        entries.extend(page.entries);
        match page.next_offset {
            Some(next) if next > offset => offset = next,
            Some(_) => {
                return Err(ClientError(
                    "The daemon returned an invalid directory cursor".into(),
                ))
            }
            None => break total_entries,
        }
        if offset > 100_000 {
            return Err(ClientError(
                "The directory exceeds the browser safety limit".into(),
            ));
        }
    };
    Ok(DirectoryListing {
        path: path.to_vec(),
        entries,
        total_entries,
        next_offset: None,
        truncated,
    })
}

pub fn open_snapshot_file(
    snapshot_kind: &str,
    snapshot_id: &str,
    path: &[String],
) -> Result<(File, OpenedFileMetadata), ClientError> {
    let path_json = serde_json::to_string(path)
        .map_err(|error| ClientError(format!("Could not encode the browser path: {error}")))?;
    let connection = system_bus()?;
    let reply_type = VariantTy::new("(hs)").expect("static D-Bus reply type is valid");
    let (reply, descriptors) = connection
        .call_with_unix_fd_list_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            "OpenSnapshotFile",
            Some(&(snapshot_kind, snapshot_id, path_json).to_variant()),
            Some(reply_type),
            gio::DBusCallFlags::NONE,
            MUTATING_CALL_TIMEOUT_MS,
            gio::UnixFDList::NONE,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("Could not open the snapshot file: {error}")))?;
    let handle = reply.child_get::<gio::glib::variant::Handle>(0).0;
    let descriptors = descriptors
        .ok_or_else(|| ClientError("The daemon did not return a file descriptor".into()))?;
    let descriptor = descriptors
        .get(handle)
        .map_err(|error| ClientError(format!("Could not receive the snapshot file: {error}")))?;
    let metadata = serde_json::from_str(&reply.child_get::<String>(1)).map_err(|error| {
        ClientError(format!(
            "The daemon returned invalid file metadata: {error}"
        ))
    })?;
    Ok((File::from(descriptor), metadata))
}

pub fn set_automatic_configuration<F>(
    configuration: &AutomaticConfiguration,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    let json = serde_json::to_string(configuration).map_err(|error| {
        ClientError(format!("Could not encode automatic configuration: {error}"))
    })?;
    run_operation("SetAutomaticPolicy", &(json,).to_variant(), on_progress)
}

pub fn create_recovery_point<F>(
    title: &str,
    reason: &str,
    pinned: bool,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation(
        "CreateRecoveryPoint",
        &(title, reason, pinned).to_variant(),
        on_progress,
    )
}

pub fn create_snapshot<F>(
    target: SnapshotTarget,
    title: &str,
    reason: &str,
    pinned: bool,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation(
        "CreateSnapshot",
        &(target.as_str(), title, reason, pinned).to_variant(),
        on_progress,
    )
}

pub fn set_pinned<F>(
    deployment_id: &str,
    pinned: bool,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation(
        "SetPinned",
        &(deployment_id, pinned).to_variant(),
        on_progress,
    )
}

pub fn delete_recovery_point<F>(
    deployment_id: &str,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation(
        "DeleteRecoveryPoint",
        &(deployment_id,).to_variant(),
        on_progress,
    )
}

pub fn delete_home_snapshot<F>(
    snapshot_id: &str,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation(
        "DeleteHomeSnapshot",
        &(snapshot_id,).to_variant(),
        on_progress,
    )
}

pub fn verify_recovery_point<F>(
    deployment_id: &str,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation(
        "VerifyRecoveryPoint",
        &(deployment_id,).to_variant(),
        on_progress,
    )
}

pub fn schedule_rollback<F>(
    deployment_id: &str,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation(
        "ScheduleRollback",
        &(deployment_id,).to_variant(),
        on_progress,
    )
}

pub fn cancel_pending_rollback<F>(on_progress: F) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation("CancelPendingRollback", &().to_variant(), on_progress)
}

pub fn run_retention<F>(on_progress: F) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    run_operation("RunRetention", &().to_variant(), on_progress)
}

fn call_json_method(method: &str) -> Result<String, ClientError> {
    let connection = system_bus()?;
    let reply_type = VariantTy::new("(s)").expect("static D-Bus reply type is valid");
    let reply = connection
        .call_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            method,
            None,
            Some(reply_type),
            gio::DBusCallFlags::NONE,
            READ_ONLY_CALL_TIMEOUT_MS,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("D-Bus method {method} failed: {error}")))?;
    Ok(reply.child_get::<String>(0))
}

fn run_operation<F>(
    method: &str,
    parameters: &gio::glib::Variant,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    let context = gio::glib::MainContext::new();
    context
        .with_thread_default(|| run_operation_in_context(&context, method, parameters, on_progress))
        .map_err(|error| ClientError(format!("Could not start a D-Bus event context: {error}")))?
}

fn run_operation_in_context<F>(
    context: &gio::glib::MainContext,
    method: &str,
    parameters: &gio::glib::Variant,
    on_progress: F,
) -> Result<OperationResult, ClientError>
where
    F: Fn(OperationProgress) + 'static,
{
    let connection = system_bus()?;
    let operation_id = Rc::new(RefCell::new(None::<String>));
    let completed = Rc::new(RefCell::new(HashMap::<String, OperationResult>::new()));
    let loop_ = gio::glib::MainLoop::new(Some(context), false);
    let progress_callback = Rc::new(on_progress);

    let progress_subscription = connection.subscribe_to_signal(
        Some(DBUS_NAME),
        Some(DBUS_INTERFACE),
        Some("OperationProgress"),
        Some(DBUS_PATH),
        None,
        gio::DBusSignalFlags::NONE,
        {
            let operation_id = operation_id.clone();
            let progress_callback = progress_callback.clone();
            move |signal| {
                let parameters = signal.parameters;
                let update = OperationProgress {
                    operation_id: parameters.child_get(0),
                    phase: parameters.child_get(1),
                    fraction: parameters.child_get(2),
                    message: parameters.child_get(3),
                };
                if operation_id.borrow().as_deref() == Some(&update.operation_id) {
                    progress_callback(update);
                }
            }
        },
    );
    let finished_subscription = connection.subscribe_to_signal(
        Some(DBUS_NAME),
        Some(DBUS_INTERFACE),
        Some("OperationFinished"),
        Some(DBUS_PATH),
        None,
        gio::DBusSignalFlags::NONE,
        {
            let operation_id = operation_id.clone();
            let completed = completed.clone();
            let loop_ = loop_.clone();
            move |signal| {
                let parameters = signal.parameters;
                let result = OperationResult {
                    operation_id: parameters.child_get(0),
                    success: parameters.child_get(1),
                    error_code: parameters.child_get(2),
                    message: parameters.child_get(3),
                };
                let is_requested = operation_id.borrow().as_deref() == Some(&result.operation_id);
                completed
                    .borrow_mut()
                    .insert(result.operation_id.clone(), result);
                if is_requested {
                    loop_.quit();
                }
            }
        },
    );

    let reply_type = VariantTy::new("(s)").expect("static D-Bus reply type is valid");
    let reply = connection
        .call_sync(
            Some(DBUS_NAME),
            DBUS_PATH,
            DBUS_INTERFACE,
            method,
            Some(parameters),
            Some(reply_type),
            gio::DBusCallFlags::NONE,
            MUTATING_CALL_TIMEOUT_MS,
            None::<&gio::Cancellable>,
        )
        .map_err(|error| ClientError(format!("D-Bus method {method} failed: {error}")))?;
    let requested = reply.child_get::<String>(0);
    *operation_id.borrow_mut() = Some(requested.clone());

    if !completed.borrow().contains_key(&requested) {
        if wait_for_operation(context, &loop_, OPERATION_TIMEOUT_SECONDS) {
            return Err(ClientError(format!(
                "Recovery operation {requested} did not finish within {OPERATION_TIMEOUT_SECONDS} seconds"
            )));
        }
    }
    drop(progress_subscription);
    drop(finished_subscription);
    let result = completed.borrow_mut().remove(&requested).ok_or_else(|| {
        ClientError(format!(
            "Recovery operation {requested} ended without a result signal"
        ))
    });
    result
}

fn wait_for_operation(
    context: &gio::glib::MainContext,
    loop_: &gio::glib::MainLoop,
    timeout_seconds: u32,
) -> bool {
    let timed_out = Arc::new(AtomicBool::new(false));
    let timeout_source = gio::glib::timeout_source_new_seconds(
        timeout_seconds,
        Some("timeback-operation-timeout"),
        gio::glib::Priority::DEFAULT,
        {
            let loop_ = loop_.clone();
            let timed_out = timed_out.clone();
            move || {
                timed_out.store(true, Ordering::Release);
                loop_.quit();
                gio::glib::ControlFlow::Break
            }
        },
    );
    let _timeout_id = timeout_source.attach(Some(context));
    loop_.run();
    // SourceId::remove() looks up the numeric ID in GLib's default main
    // context. This timeout belongs to the private context above, so removing
    // it by ID can fail and panic after a fast operation. Destroy the source
    // object directly instead.
    timeout_source.destroy();
    timed_out.load(Ordering::Acquire)
}

fn system_bus() -> Result<gio::DBusConnection, ClientError> {
    gio::bus_get_sync(gio::BusType::System, None::<&gio::Cancellable>)
        .map_err(|error| ClientError(format!("Could not connect to the system bus: {error}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fast_operation_cleans_up_timeout_on_private_context() {
        let context = gio::glib::MainContext::new();
        context
            .with_thread_default(|| {
                let loop_ = gio::glib::MainLoop::new(Some(&context), false);
                let completion = gio::glib::idle_source_new(
                    Some("timeback-test-completion"),
                    gio::glib::Priority::DEFAULT,
                    {
                        let loop_ = loop_.clone();
                        move || {
                            loop_.quit();
                            gio::glib::ControlFlow::Break
                        }
                    },
                );
                let _completion_id = completion.attach(Some(&context));

                assert!(!wait_for_operation(&context, &loop_, 60));
            })
            .expect("the private test context must become thread-default");
    }
}
