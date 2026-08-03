use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::panic::{self, AssertUnwindSafe};
use std::process::Command;
use std::process::ExitCode;
use std::rc::Rc;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use anduinos_timeback::automatic_home::HomeSnapshotStore;
use anduinos_timeback::automation::{AutomaticConfiguration, AutomaticPolicy, AutomaticStore};
use anduinos_timeback::browsing::{self, SnapshotBrowseLock, SnapshotKind, SortMode};
use anduinos_timeback::layout;
use anduinos_timeback::model::DeploymentId;
use anduinos_timeback::operations::{OperationEngine, OperationError, OperationPhase};
use anduinos_timeback::retention::{RetentionCoordinator, RetentionExecutionError};
use anduinos_timeback::rollback::{RollbackCoordinator, RollbackError, RollbackProgressPhase};
use anduinos_timeback::store::DeploymentStore;
use anduinos_timeback::{CONTRACT_VERSION, DBUS_INTERFACE, DBUS_NAME, DBUS_PATH};
use gio::glib;
use gio::prelude::{ToVariant, UnixFDListExtManual};

const INTROSPECTION_XML: &str = include_str!("../data/com.anduinos.timebackmachine.xml");
const READ_ONLY_ERROR: &str = "com.anduinos.TimebackMachine1.Error.ReadOnlyMilestone";
const BUSY_ERROR: &str = "com.anduinos.TimebackMachine1.Error.Busy";
const AUTHORIZATION_ERROR: &str = "com.anduinos.TimebackMachine1.Error.NotAuthorized";
const INVALID_ARGUMENT_ERROR: &str = "org.freedesktop.DBus.Error.InvalidArgs";
const CREATE_ACTION: &str = "com.anduinos.timebackmachine.create";
const MANAGE_ACTION: &str = "com.anduinos.timebackmachine.manage";
const RESTORE_ACTION: &str = "com.anduinos.timebackmachine.restore";
const BROWSE_ACTION: &str = "com.anduinos.timebackmachine.browse";
const BROWSE_SESSION_TTL: Duration = Duration::from_secs(15 * 60);
const MAX_BROWSE_SESSIONS: usize = 128;

struct BrowseSession {
    sender: String,
    kind: SnapshotKind,
    snapshot_id: String,
    last_used: Instant,
    _lock: SnapshotBrowseLock,
}

struct DaemonOperationError {
    code: String,
    message: String,
}

impl From<OperationError> for DaemonOperationError {
    fn from(error: OperationError) -> Self {
        Self {
            code: error.code.as_str().into(),
            message: error.message,
        }
    }
}

impl From<RollbackError> for DaemonOperationError {
    fn from(error: RollbackError) -> Self {
        Self {
            code: error.code.as_str().into(),
            message: error.message,
        }
    }
}

impl From<RetentionExecutionError> for DaemonOperationError {
    fn from(error: RetentionExecutionError) -> Self {
        Self {
            code: error.code.as_str().into(),
            message: error.message,
        }
    }
}

fn main() -> ExitCode {
    let loop_ = glib::MainLoop::new(None, false);
    let failed = Rc::new(Cell::new(false));
    let loop_for_bus = loop_.clone();
    let failed_for_bus = failed.clone();
    let busy = Arc::new(AtomicBool::new(false));

    let owner = gio::bus_own_name(
        gio::BusType::System,
        DBUS_NAME,
        gio::BusNameOwnerFlags::NONE,
        {
            let busy = busy.clone();
            move |connection, _name| {
                if let Err(error) = register_api(&connection, busy.clone()) {
                    eprintln!("Could not export {DBUS_INTERFACE}: {error}");
                    failed_for_bus.set(true);
                    loop_for_bus.quit();
                }
            }
        },
        |_connection, _name| {
            eprintln!("Timeback Machine service is ready");
        },
        {
            let loop_ = loop_.clone();
            let failed = failed.clone();
            move |_connection, name| {
                eprintln!("Could not own D-Bus name {name}");
                failed.set(true);
                loop_.quit();
            }
        },
    );

    loop_.run();
    gio::bus_unown_name(owner);
    if failed.get() {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

fn register_api(
    connection: &gio::DBusConnection,
    busy: Arc<AtomicBool>,
) -> Result<(), glib::Error> {
    let browse_sessions = Rc::new(RefCell::new(HashMap::<String, BrowseSession>::new()));
    let sessions_for_names = browse_sessions.clone();
    #[allow(deprecated)]
    connection.signal_subscribe(
        Some("org.freedesktop.DBus"),
        Some("org.freedesktop.DBus"),
        Some("NameOwnerChanged"),
        Some("/org/freedesktop/DBus"),
        None,
        gio::DBusSignalFlags::NONE,
        move |_connection, _sender, _path, _interface, _signal, parameters| {
            let name = parameters.child_get::<String>(0);
            let new_owner = parameters.child_get::<String>(2);
            if new_owner.is_empty() && name.starts_with(':') {
                sessions_for_names
                    .borrow_mut()
                    .retain(|_, session| session.sender != name);
            }
        },
    );
    let sessions_for_expiry = browse_sessions.clone();
    glib::timeout_add_local(Duration::from_secs(60), move || {
        expire_browse_sessions(&mut sessions_for_expiry.borrow_mut());
        glib::ControlFlow::Continue
    });
    let interface = gio::DBusNodeInfo::for_xml(INTROSPECTION_XML)?
        .lookup_interface(DBUS_INTERFACE)
        .expect("the embedded D-Bus interface must exist");
    connection
        .register_object(DBUS_PATH, &interface)
        .method_call({
            let busy = busy.clone();
            let browse_sessions = browse_sessions.clone();
            move |connection,
                  sender,
                  _object_path,
                  _interface_name,
                  method,
                  parameters,
                  invocation| {
                match method {
                    "InspectLayout" => return_json(invocation, &layout::inspect_current()),
                    "ListDeployments" => {
                        return_json(invocation, &DeploymentStore::default().discover())
                    }
                    "InspectRetention" => match RetentionCoordinator::default().inspect() {
                        Ok(plan) => return_json(invocation, &plan),
                        Err(error) => invocation.return_dbus_error(
                            "com.anduinos.TimebackMachine1.Error.RetentionUnavailable",
                            &error.to_string(),
                        ),
                    },
                    "InspectAutomatic" => {
                        match AutomaticStore::default().status(chrono::Utc::now()) {
                            Ok(status) => return_json(invocation, &status),
                            Err(error) => invocation.return_dbus_error(
                                "com.anduinos.TimebackMachine1.Error.AutomaticUnavailable",
                                &error.to_string(),
                            ),
                        }
                    }
                    "ListHomeSnapshots" => match HomeSnapshotStore::default().discover() {
                        Ok(records) => return_json(invocation, &records),
                        Err(error) => invocation.return_dbus_error(
                            "com.anduinos.TimebackMachine1.Error.HomeSnapshotsUnavailable",
                            &error,
                        ),
                    },
                    "BeginSnapshotBrowse" => {
                        let Some(sender) = sender else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The D-Bus caller is unknown",
                            );
                            return;
                        };
                        if let Err(message) = authorize(sender, BROWSE_ACTION) {
                            invocation.return_dbus_error(AUTHORIZATION_ERROR, &message);
                            return;
                        }
                        let kind = match SnapshotKind::parse(&parameters.child_get::<String>(0)) {
                            Ok(kind) => kind,
                            Err(error) => {
                                return_browse_error(invocation, error);
                                return;
                            }
                        };
                        let snapshot_id = parameters.child_get::<String>(1);
                        let browse_lock = match browsing::acquire_shared_snapshot_lock(
                            kind,
                            &snapshot_id,
                        ) {
                            Ok(lock) => lock,
                            Err(error) => {
                                return_browse_error(invocation, error);
                                return;
                            }
                        };
                        if let Err(error) = browsing::list_directory_page(
                            kind,
                            &snapshot_id,
                            &[],
                            0,
                            1,
                        ) {
                            return_browse_error(invocation, error);
                            return;
                        }
                        expire_browse_sessions(&mut browse_sessions.borrow_mut());
                        if browse_sessions.borrow().len() >= MAX_BROWSE_SESSIONS {
                            invocation.return_dbus_error(
                                BUSY_ERROR,
                                "Too many snapshot browsing sessions are active",
                            );
                            return;
                        }
                        let session_id = uuid::Uuid::new_v4().hyphenated().to_string();
                        browse_sessions.borrow_mut().insert(
                            session_id.clone(),
                            BrowseSession {
                                sender: sender.to_string(),
                                kind,
                                snapshot_id,
                                last_used: Instant::now(),
                                _lock: browse_lock,
                            },
                        );
                        invocation.return_value(Some(&(session_id,).to_variant()));
                    }
                    "ListSnapshotDirectorySession" => {
                        let Some(sender) = sender else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The D-Bus caller is unknown",
                            );
                            return;
                        };
                        let session_id = parameters.child_get::<String>(0);
                        let (kind, snapshot_id) = match browse_session_target(
                            &browse_sessions,
                            &session_id,
                            sender,
                        ) {
                            Ok(target) => target,
                            Err(message) => {
                                invocation.return_dbus_error(AUTHORIZATION_ERROR, &message);
                                return;
                            }
                        };
                        let path = match serde_json::from_str::<Vec<String>>(
                            &parameters.child_get::<String>(1),
                        ) {
                            Ok(path) => path,
                            Err(error) => {
                                invocation.return_dbus_error(
                                    INVALID_ARGUMENT_ERROR,
                                    &format!("Invalid snapshot browser path: {error}"),
                                );
                                return;
                            }
                        };
                        let sort_mode =
                            match SortMode::parse(&parameters.child_get::<String>(4)) {
                                Ok(mode) => mode,
                                Err(error) => {
                                    return_browse_error(invocation, error);
                                    return;
                                }
                            };
                        match browsing::list_directory_page_sorted(
                            kind,
                            &snapshot_id,
                            &path,
                            parameters.child_get::<u32>(2) as usize,
                            parameters.child_get::<u32>(3) as usize,
                            sort_mode,
                            parameters.child_get::<bool>(5),
                        ) {
                            Ok(listing) => return_json(invocation, &listing),
                            Err(error) => return_browse_error(invocation, error),
                        }
                    }
                    "OpenSnapshotFileSession" => {
                        let Some(sender) = sender else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The D-Bus caller is unknown",
                            );
                            return;
                        };
                        let session_id = parameters.child_get::<String>(0);
                        let (kind, snapshot_id) = match browse_session_target(
                            &browse_sessions,
                            &session_id,
                            sender,
                        ) {
                            Ok(target) => target,
                            Err(message) => {
                                invocation.return_dbus_error(AUTHORIZATION_ERROR, &message);
                                return;
                            }
                        };
                        let path = match serde_json::from_str::<Vec<String>>(
                            &parameters.child_get::<String>(1),
                        ) {
                            Ok(path) => path,
                            Err(error) => {
                                invocation.return_dbus_error(
                                    INVALID_ARGUMENT_ERROR,
                                    &format!("Invalid snapshot browser path: {error}"),
                                );
                                return;
                            }
                        };
                        return_open_snapshot_file(
                            invocation,
                            browsing::open_regular_file(kind, &snapshot_id, &path),
                        );
                    }
                    "CloseSnapshotBrowse" => {
                        let Some(sender) = sender else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The D-Bus caller is unknown",
                            );
                            return;
                        };
                        let session_id = parameters.child_get::<String>(0);
                        let mut sessions = browse_sessions.borrow_mut();
                        expire_browse_sessions(&mut sessions);
                        if sessions
                            .get(&session_id)
                            .is_some_and(|session| session.sender == sender)
                        {
                            sessions.remove(&session_id);
                            invocation.return_value(Some(&().to_variant()));
                        } else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The browsing session is unavailable for this caller",
                            );
                        }
                    }
                    "KeepSnapshotBrowseAlive" => {
                        let Some(sender) = sender else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The D-Bus caller is unknown",
                            );
                            return;
                        };
                        let session_id = parameters.child_get::<String>(0);
                        match browse_session_target(&browse_sessions, &session_id, sender) {
                            Ok(_) => invocation.return_value(Some(&().to_variant())),
                            Err(message) => {
                                invocation.return_dbus_error(AUTHORIZATION_ERROR, &message)
                            }
                        }
                    }
                    "ListSnapshotDirectory"
                    | "ListSnapshotDirectoryPage"
                    | "ListSnapshotDirectoryPageSorted" => {
                        let Some(sender) = sender else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The D-Bus caller is unknown",
                            );
                            return;
                        };
                        if let Err(message) = authorize(sender, BROWSE_ACTION) {
                            invocation.return_dbus_error(AUTHORIZATION_ERROR, &message);
                            return;
                        }
                        let kind = match SnapshotKind::parse(&parameters.child_get::<String>(0)) {
                            Ok(kind) => kind,
                            Err(error) => {
                                return_browse_error(invocation, error);
                                return;
                            }
                        };
                        let snapshot_id = parameters.child_get::<String>(1);
                        let path_json = parameters.child_get::<String>(2);
                        let path = match serde_json::from_str::<Vec<String>>(&path_json) {
                            Ok(path) => path,
                            Err(error) => {
                                invocation.return_dbus_error(
                                    INVALID_ARGUMENT_ERROR,
                                    &format!("Invalid snapshot browser path: {error}"),
                                );
                                return;
                            }
                        };
                        let paginated = method != "ListSnapshotDirectory";
                        let (offset, limit) = if paginated {
                            (
                                parameters.child_get::<u32>(3) as usize,
                                parameters.child_get::<u32>(4) as usize,
                            )
                        } else {
                            (0, 1_000)
                        };
                        let (sort_mode, descending) =
                            if method == "ListSnapshotDirectoryPageSorted" {
                                let sort_mode =
                                    match SortMode::parse(&parameters.child_get::<String>(5)) {
                                        Ok(mode) => mode,
                                        Err(error) => {
                                            return_browse_error(invocation, error);
                                            return;
                                        }
                                    };
                                (sort_mode, parameters.child_get::<bool>(6))
                            } else {
                                (SortMode::Name, false)
                            };
                        match browsing::list_directory_page_sorted(
                            kind,
                            &snapshot_id,
                            &path,
                            offset,
                            limit,
                            sort_mode,
                            descending,
                        ) {
                            Ok(listing) => return_json(invocation, &listing),
                            Err(error) => return_browse_error(invocation, error),
                        }
                    }
                    "OpenSnapshotFile" => {
                        let Some(sender) = sender else {
                            invocation.return_dbus_error(
                                AUTHORIZATION_ERROR,
                                "The D-Bus caller is unknown",
                            );
                            return;
                        };
                        if let Err(message) = authorize(sender, BROWSE_ACTION) {
                            invocation.return_dbus_error(AUTHORIZATION_ERROR, &message);
                            return;
                        }
                        let kind = match SnapshotKind::parse(&parameters.child_get::<String>(0)) {
                            Ok(kind) => kind,
                            Err(error) => {
                                return_browse_error(invocation, error);
                                return;
                            }
                        };
                        let snapshot_id = parameters.child_get::<String>(1);
                        let path_json = parameters.child_get::<String>(2);
                        let path = match serde_json::from_str::<Vec<String>>(&path_json) {
                            Ok(path) => path,
                            Err(error) => {
                                invocation.return_dbus_error(
                                    INVALID_ARGUMENT_ERROR,
                                    &format!("Invalid snapshot browser path: {error}"),
                                );
                                return;
                            }
                        };
                        match browsing::open_regular_file(kind, &snapshot_id, &path) {
                            Ok((file, metadata)) => {
                                let descriptors = gio::UnixFDList::new();
                                let index = match descriptors.append(&file) {
                                    Ok(index) => index,
                                    Err(error) => {
                                        invocation.return_dbus_error(
                                            "com.anduinos.TimebackMachine1.Error.BrowseIo",
                                            &format!("Could not transfer the snapshot file: {error}"),
                                        );
                                        return;
                                    }
                                };
                                let metadata_json = match serde_json::to_string(&metadata) {
                                    Ok(json) => json,
                                    Err(error) => {
                                        invocation.return_dbus_error(
                                            "com.anduinos.TimebackMachine1.Error.Internal",
                                            &format!("Could not encode file metadata: {error}"),
                                        );
                                        return;
                                    }
                                };
                                invocation.return_value_with_unix_fd_list(
                                    Some(&(glib::variant::Handle(index), metadata_json).to_variant()),
                                    Some(&descriptors),
                                );
                            }
                            Err(error) => return_browse_error(invocation, error),
                        }
                    }
                    "SetAutomaticPolicy" => {
                        let json = parameters.child_get::<String>(0);
                        let configuration =
                            match serde_json::from_str::<AutomaticConfiguration>(&json) {
                                Ok(configuration) => configuration,
                                Err(configuration_error) => {
                                    match serde_json::from_str::<AutomaticPolicy>(&json) {
                                        Ok(policy) => {
                                            let mut configuration = match AutomaticStore::default()
                                                .configuration()
                                            {
                                                Ok(configuration) => configuration,
                                                Err(error) => {
                                                    invocation.return_dbus_error(
                                                        INVALID_ARGUMENT_ERROR,
                                                        &format!(
                                                            "Could not migrate the automatic policy: {error}"
                                                        ),
                                                    );
                                                    return;
                                                }
                                            };
                                            configuration.policies_linked = false;
                                            configuration.system = policy;
                                            configuration
                                        }
                                        Err(_) => {
                                            invocation.return_dbus_error(
                                                INVALID_ARGUMENT_ERROR,
                                                &format!(
                                                    "Invalid automatic policy: {configuration_error}"
                                                ),
                                            );
                                            return;
                                        }
                                    }
                                }
                            };
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            Some(MANAGE_ACTION),
                            invocation,
                            move |connection, operation_id| {
                                emit_progress(
                                    connection,
                                    operation_id,
                                    OperationPhase::Validate,
                                    0.2,
                                    "Validating automatic snapshot policy",
                                );
                                AutomaticStore::default()
                                    .set_configuration(&configuration)
                                    .map_err(|error| DaemonOperationError {
                                        code: "automatic-policy".into(),
                                        message: error.to_string(),
                                    })?;
                                emit_progress(
                                    connection,
                                    operation_id,
                                    OperationPhase::Commit,
                                    1.0,
                                    "Automatic snapshot policy saved",
                                );
                                Ok("Automatic snapshot policies saved".into())
                            },
                        );
                    }
                    "CreateRecoveryPoint" => {
                        let title = parameters.child_get::<String>(0);
                        let reason = parameters.child_get::<String>(1);
                        let pinned = parameters.child_get::<bool>(2);
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            Some(CREATE_ACTION),
                            invocation,
                            move |connection, operation_id| {
                                let engine = OperationEngine::default();
                                let report = layout::inspect_current();
                                let progress_connection = connection.clone();
                                let progress_operation = operation_id.to_string();
                                engine
                                    .create_manual(
                                        &report,
                                        &title,
                                        &reason,
                                        pinned,
                                        move |phase, fraction, message| {
                                            emit_progress(
                                                &progress_connection,
                                                &progress_operation,
                                                phase,
                                                fraction,
                                                message,
                                            );
                                        },
                                    )
                                    .map(|record| format!("Recovery point {} created", record.id))
                                    .map_err(Into::into)
                            },
                        );
                    }
                    "SetPinned" => {
                        let id = match parse_deployment_id(&parameters.child_get::<String>(0)) {
                            Ok(id) => id,
                            Err(message) => {
                                invocation.return_dbus_error(INVALID_ARGUMENT_ERROR, &message);
                                return;
                            }
                        };
                        let pinned = parameters.child_get::<bool>(1);
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            Some(MANAGE_ACTION),
                            invocation,
                            move |connection, operation_id| {
                                emit_progress(
                                    connection,
                                    operation_id,
                                    OperationPhase::Validate,
                                    0.1,
                                    "Validating recovery point",
                                );
                                OperationEngine::default()
                                    .set_pinned(&layout::inspect_current(), id, pinned)
                                    .map(|_| {
                                        emit_progress(
                                            connection,
                                            operation_id,
                                            OperationPhase::Commit,
                                            1.0,
                                            "Recovery point updated",
                                        );
                                        "Recovery point updated".into()
                                    })
                                    .map_err(Into::into)
                            },
                        );
                    }
                    "DeleteRecoveryPoint" => {
                        let id = match parse_deployment_id(&parameters.child_get::<String>(0)) {
                            Ok(id) => id,
                            Err(message) => {
                                invocation.return_dbus_error(INVALID_ARGUMENT_ERROR, &message);
                                return;
                            }
                        };
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            Some(MANAGE_ACTION),
                            invocation,
                            move |connection, operation_id| {
                                emit_progress(
                                    connection,
                                    operation_id,
                                    OperationPhase::Validate,
                                    0.1,
                                    "Validating deletion safeguards",
                                );
                                OperationEngine::default()
                                    .delete(&layout::inspect_current(), id)
                                    .map(|_| {
                                        emit_progress(
                                            connection,
                                            operation_id,
                                            OperationPhase::Cleanup,
                                            1.0,
                                            "Recovery point deleted",
                                        );
                                        "Recovery point deleted".into()
                                    })
                                    .map_err(Into::into)
                            },
                        );
                    }
                    "VerifyRecoveryPoint" => {
                        let id = match parse_deployment_id(&parameters.child_get::<String>(0)) {
                            Ok(id) => id,
                            Err(message) => {
                                invocation.return_dbus_error(INVALID_ARGUMENT_ERROR, &message);
                                return;
                            }
                        };
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            None,
                            invocation,
                            move |connection, operation_id| {
                                let progress_connection = connection.clone();
                                let progress_operation = operation_id.to_string();
                                OperationEngine::default()
                                    .verify(
                                        &layout::inspect_current(),
                                        id,
                                        move |phase, fraction, message| {
                                            emit_progress(
                                                &progress_connection,
                                                &progress_operation,
                                                phase,
                                                fraction,
                                                message,
                                            );
                                        },
                                    )
                                    .map(|_| "Recovery point integrity verified".into())
                                    .map_err(Into::into)
                            },
                        );
                    }
                    "ScheduleRollback" => {
                        let id = match parse_deployment_id(&parameters.child_get::<String>(0)) {
                            Ok(id) => id,
                            Err(message) => {
                                invocation.return_dbus_error(INVALID_ARGUMENT_ERROR, &message);
                                return;
                            }
                        };
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            Some(RESTORE_ACTION),
                            invocation,
                            move |connection, operation_id| {
                                let progress_connection = connection.clone();
                                let progress_operation = operation_id.to_string();
                                RollbackCoordinator::default()
                                    .schedule(id, move |phase, fraction, message| {
                                        emit_rollback_progress(
                                            &progress_connection,
                                            &progress_operation,
                                            phase,
                                            fraction,
                                            message,
                                        );
                                    })
                                    .map(|_| "System restore is ready. Restart to continue.".into())
                                    .map_err(Into::into)
                            },
                        );
                    }
                    "CancelPendingRollback" => {
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            Some(RESTORE_ACTION),
                            invocation,
                            move |connection, operation_id| {
                                emit_rollback_progress(
                                    connection,
                                    operation_id,
                                    RollbackProgressPhase::Cleanup,
                                    0.2,
                                    "Clearing the one-time recovery boot",
                                );
                                RollbackCoordinator::default()
                                    .cancel()
                                    .map(|_| {
                                        emit_rollback_progress(
                                            connection,
                                            operation_id,
                                            RollbackProgressPhase::Commit,
                                            1.0,
                                            "Pending system restore cancelled",
                                        );
                                        "Pending system restore cancelled".into()
                                    })
                                    .map_err(Into::into)
                            },
                        );
                    }
                    "RunRetention" => {
                        start_operation(
                            connection,
                            sender,
                            busy.clone(),
                            Some(MANAGE_ACTION),
                            invocation,
                            move |connection, operation_id| {
                                emit_progress(
                                    connection,
                                    operation_id,
                                    OperationPhase::Validate,
                                    0.1,
                                    "Constructing a safe retention plan",
                                );
                                RetentionCoordinator::default()
                                    .apply()
                                    .map(|report| {
                                        emit_progress(
                                            connection,
                                            operation_id,
                                            OperationPhase::Cleanup,
                                            1.0,
                                            "Automatic recovery-point cleanup complete",
                                        );
                                        format!(
                                            "Retention deleted {} automatic recovery point(s)",
                                            report.deleted.len()
                                        )
                                    })
                                    .map_err(Into::into)
                            },
                        );
                    }
                    "SetRetentionPolicy" => invocation.return_dbus_error(
                        READ_ONLY_ERROR,
                        "The first retention release uses the fixed Balanced policy",
                    ),
                    _ => invocation.return_dbus_error(
                        "org.freedesktop.DBus.Error.UnknownMethod",
                        "Unknown Timeback Machine method",
                    ),
                }
            }
        })
        .property({
            let busy = busy.clone();
            move |_connection, _sender, _object_path, _interface_name, property| match property {
                "ContractVersion" => CONTRACT_VERSION.to_variant(),
                "Busy" => busy.load(Ordering::Acquire).to_variant(),
                _ => ().to_variant(),
            }
        })
        .build()?;
    Ok(())
}

fn start_operation<F>(
    connection: gio::DBusConnection,
    sender: Option<&str>,
    busy: Arc<AtomicBool>,
    authorization_action: Option<&'static str>,
    invocation: gio::DBusMethodInvocation,
    work: F,
) where
    F: FnOnce(&gio::DBusConnection, &str) -> Result<String, DaemonOperationError> + Send + 'static,
{
    if busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        invocation.return_dbus_error(BUSY_ERROR, "Another recovery operation is in progress");
        return;
    }
    emit_busy_changed(&connection, true);
    if let Some(action) = authorization_action {
        let Some(sender) = sender else {
            busy.store(false, Ordering::Release);
            emit_busy_changed(&connection, false);
            invocation.return_dbus_error(AUTHORIZATION_ERROR, "The D-Bus caller is unknown");
            return;
        };
        if let Err(message) = authorize(sender, action) {
            busy.store(false, Ordering::Release);
            emit_busy_changed(&connection, false);
            invocation.return_dbus_error(AUTHORIZATION_ERROR, &message);
            return;
        }
    }

    let operation_id = uuid::Uuid::new_v4().hyphenated().to_string();
    let thread_connection = connection.clone();
    let thread_operation = operation_id.clone();
    let thread_busy = busy.clone();
    let spawn = std::thread::Builder::new()
        .name(format!("timeback-{operation_id}"))
        .spawn(move || {
            let result = panic::catch_unwind(AssertUnwindSafe(|| {
                work(&thread_connection, &thread_operation)
            }));
            match result {
                Ok(Ok(message)) => {
                    emit_finished(&thread_connection, &thread_operation, true, "", &message)
                }
                Ok(Err(error)) => emit_finished(
                    &thread_connection,
                    &thread_operation,
                    false,
                    &error.code,
                    &error.message,
                ),
                Err(_) => emit_finished(
                    &thread_connection,
                    &thread_operation,
                    false,
                    "internal-panic",
                    "The recovery worker stopped unexpectedly",
                ),
            }
            if let Err(error) = thread_connection.emit_signal(
                None,
                DBUS_PATH,
                DBUS_INTERFACE,
                "DeploymentsChanged",
                None,
            ) {
                eprintln!("Could not emit DeploymentsChanged: {error}");
            }
            thread_busy.store(false, Ordering::Release);
            emit_busy_changed(&thread_connection, false);
        });
    if let Err(error) = spawn {
        busy.store(false, Ordering::Release);
        emit_busy_changed(&connection, false);
        invocation.return_dbus_error(
            "com.anduinos.TimebackMachine1.Error.Internal",
            &format!("Could not start the recovery worker: {error}"),
        );
        return;
    }
    invocation.return_value(Some(&(operation_id,).to_variant()));
}

fn emit_rollback_progress(
    connection: &gio::DBusConnection,
    operation_id: &str,
    phase: RollbackProgressPhase,
    fraction: f64,
    message: &str,
) {
    let _ = connection.emit_signal(
        None,
        DBUS_PATH,
        DBUS_INTERFACE,
        "OperationProgress",
        Some(
            &(
                operation_id,
                phase.as_str(),
                fraction.clamp(0.0, 1.0),
                message,
            )
                .to_variant(),
        ),
    );
}

fn emit_busy_changed(connection: &gio::DBusConnection, busy: bool) {
    let changed = HashMap::from([("Busy", busy.to_variant())]);
    if let Err(error) = connection.emit_signal(
        None,
        DBUS_PATH,
        "org.freedesktop.DBus.Properties",
        "PropertiesChanged",
        Some(&(DBUS_INTERFACE, changed, Vec::<String>::new()).to_variant()),
    ) {
        eprintln!("Could not emit Busy property change: {error}");
    }
}

fn authorize(sender: &str, action: &str) -> Result<(), String> {
    let output = Command::new("/usr/bin/pkcheck")
        .args([
            "--action-id",
            action,
            "--system-bus-name",
            sender,
            "--allow-user-interaction",
        ])
        .env_clear()
        .env("LC_ALL", "C")
        .output()
        .map_err(|error| format!("Could not start Polkit authorization: {error}"))?;
    if output.status.success() {
        return Ok(());
    }
    match output.status.code() {
        Some(1) => Err("Polkit denied this recovery operation".into()),
        Some(2) => Err("No authentication agent is available".into()),
        Some(3) => Err("Authentication was cancelled".into()),
        _ => Err("Polkit could not authorize this recovery operation".into()),
    }
}

fn parse_deployment_id(value: &str) -> Result<DeploymentId, String> {
    let id = DeploymentId::from_str(value)
        .map_err(|_| "Deployment ID must be a lowercase hyphenated UUID".to_string())?;
    if id.to_string() != value {
        return Err("Deployment ID must use canonical lowercase UUID form".into());
    }
    Ok(id)
}

fn emit_progress(
    connection: &gio::DBusConnection,
    operation_id: &str,
    phase: OperationPhase,
    fraction: f64,
    message: &str,
) {
    if let Err(error) = connection.emit_signal(
        None,
        DBUS_PATH,
        DBUS_INTERFACE,
        "OperationProgress",
        Some(
            &(
                operation_id,
                phase.as_str(),
                fraction.clamp(0.0, 1.0),
                message,
            )
                .to_variant(),
        ),
    ) {
        eprintln!("Could not emit operation progress: {error}");
    }
}

fn emit_finished(
    connection: &gio::DBusConnection,
    operation_id: &str,
    success: bool,
    error_code: &str,
    message: &str,
) {
    if let Err(error) = connection.emit_signal(
        None,
        DBUS_PATH,
        DBUS_INTERFACE,
        "OperationFinished",
        Some(&(operation_id, success, error_code, message).to_variant()),
    ) {
        eprintln!("Could not emit operation result: {error}");
    }
}

fn return_json<T: serde::Serialize>(invocation: gio::DBusMethodInvocation, value: &T) {
    match serde_json::to_string(value) {
        Ok(json) => invocation.return_value(Some(&(json,).to_variant())),
        Err(error) => invocation.return_dbus_error(
            "com.anduinos.TimebackMachine1.Error.Serialization",
            &format!("Could not serialize read-only report: {error}"),
        ),
    }
}

fn return_browse_error(
    invocation: gio::DBusMethodInvocation,
    error: anduinos_timeback::browsing::BrowseError,
) {
    let name = format!(
        "com.anduinos.TimebackMachine1.Error.{}",
        error
            .code
            .split('-')
            .map(|part| {
                let mut characters = part.chars();
                match characters.next() {
                    Some(first) => first.to_uppercase().collect::<String>() + characters.as_str(),
                    None => String::new(),
                }
            })
            .collect::<String>()
    );
    invocation.return_dbus_error(&name, &error.message);
}

fn expire_browse_sessions(sessions: &mut HashMap<String, BrowseSession>) {
    let now = Instant::now();
    sessions.retain(|_, session| now.duration_since(session.last_used) < BROWSE_SESSION_TTL);
}

fn browse_session_target(
    sessions: &Rc<RefCell<HashMap<String, BrowseSession>>>,
    session_id: &str,
    sender: &str,
) -> Result<(SnapshotKind, String), String> {
    let mut sessions = sessions.borrow_mut();
    expire_browse_sessions(&mut sessions);
    let session = sessions
        .get_mut(session_id)
        .ok_or_else(|| "The browsing session expired or does not exist".to_string())?;
    if session.sender != sender {
        return Err("The browsing session belongs to another D-Bus caller".into());
    }
    session.last_used = Instant::now();
    Ok((session.kind, session.snapshot_id.clone()))
}

fn return_open_snapshot_file(
    invocation: gio::DBusMethodInvocation,
    result: Result<
        (
            std::fs::File,
            anduinos_timeback::browsing::OpenedFileMetadata,
        ),
        anduinos_timeback::browsing::BrowseError,
    >,
) {
    match result {
        Ok((file, metadata)) => {
            let descriptors = gio::UnixFDList::new();
            let index = match descriptors.append(&file) {
                Ok(index) => index,
                Err(error) => {
                    invocation.return_dbus_error(
                        "com.anduinos.TimebackMachine1.Error.BrowseIo",
                        &format!("Could not transfer the snapshot file: {error}"),
                    );
                    return;
                }
            };
            let metadata_json = match serde_json::to_string(&metadata) {
                Ok(json) => json,
                Err(error) => {
                    invocation.return_dbus_error(
                        "com.anduinos.TimebackMachine1.Error.Internal",
                        &format!("Could not encode file metadata: {error}"),
                    );
                    return;
                }
            };
            invocation.return_value_with_unix_fd_list(
                Some(&(glib::variant::Handle(index), metadata_json).to_variant()),
                Some(&descriptors),
            );
        }
        Err(error) => return_browse_error(invocation, error),
    }
}
