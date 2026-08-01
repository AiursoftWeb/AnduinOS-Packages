use std::cell::Cell;
use std::collections::HashMap;
use std::panic::{self, AssertUnwindSafe};
use std::process::Command;
use std::process::ExitCode;
use std::rc::Rc;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use anduinos_timeback::layout;
use anduinos_timeback::model::DeploymentId;
use anduinos_timeback::operations::{OperationEngine, OperationError, OperationPhase};
use anduinos_timeback::retention::{RetentionCoordinator, RetentionExecutionError};
use anduinos_timeback::rollback::{RollbackCoordinator, RollbackError, RollbackProgressPhase};
use anduinos_timeback::store::DeploymentStore;
use anduinos_timeback::{CONTRACT_VERSION, DBUS_INTERFACE, DBUS_NAME, DBUS_PATH};
use gio::glib;
use gio::prelude::ToVariant;

const INTROSPECTION_XML: &str = include_str!("../data/com.anduinos.timebackmachine.xml");
const READ_ONLY_ERROR: &str = "com.anduinos.TimebackMachine1.Error.ReadOnlyMilestone";
const BUSY_ERROR: &str = "com.anduinos.TimebackMachine1.Error.Busy";
const AUTHORIZATION_ERROR: &str = "com.anduinos.TimebackMachine1.Error.NotAuthorized";
const INVALID_ARGUMENT_ERROR: &str = "org.freedesktop.DBus.Error.InvalidArgs";
const CREATE_ACTION: &str = "com.anduinos.timebackmachine.create";
const MANAGE_ACTION: &str = "com.anduinos.timebackmachine.manage";
const RESTORE_ACTION: &str = "com.anduinos.timebackmachine.restore";

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
    let interface = gio::DBusNodeInfo::for_xml(INTROSPECTION_XML)?
        .lookup_interface(DBUS_INTERFACE)
        .expect("the embedded D-Bus interface must exist");
    connection
        .register_object(DBUS_PATH, &interface)
        .method_call({
            let busy = busy.clone();
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
