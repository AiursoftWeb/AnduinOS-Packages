use std::cell::Cell;
use std::process::ExitCode;
use std::rc::Rc;

use anduinos_timeback::layout;
use anduinos_timeback::store::DeploymentStore;
use anduinos_timeback::{CONTRACT_VERSION, DBUS_INTERFACE, DBUS_NAME, DBUS_PATH};
use gio::glib;
use gio::prelude::ToVariant;

const INTROSPECTION_XML: &str = include_str!("../data/com.anduinos.timebackmachine.xml");
const READ_ONLY_ERROR: &str = "com.anduinos.TimebackMachine1.Error.ReadOnlyMilestone";

fn main() -> ExitCode {
    let loop_ = glib::MainLoop::new(None, false);
    let failed = Rc::new(Cell::new(false));
    let loop_for_bus = loop_.clone();
    let failed_for_bus = failed.clone();

    let owner = gio::bus_own_name(
        gio::BusType::System,
        DBUS_NAME,
        gio::BusNameOwnerFlags::NONE,
        move |connection, _name| {
            if let Err(error) = register_api(&connection) {
                eprintln!("Could not export {DBUS_INTERFACE}: {error}");
                failed_for_bus.set(true);
                loop_for_bus.quit();
            }
        },
        |_connection, _name| {
            eprintln!("Timeback Machine read-only service is ready");
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

fn register_api(connection: &gio::DBusConnection) -> Result<(), glib::Error> {
    let interface = gio::DBusNodeInfo::for_xml(INTROSPECTION_XML)?
        .lookup_interface(DBUS_INTERFACE)
        .expect("the embedded D-Bus interface must exist");
    connection
        .register_object(DBUS_PATH, &interface)
        .method_call(
            |_connection,
             _sender,
             _object_path,
             _interface_name,
             method,
             _parameters,
             invocation| {
                match method {
                    "InspectLayout" => return_json(invocation, &layout::inspect_current()),
                    "ListDeployments" => {
                        return_json(invocation, &DeploymentStore::default().discover())
                    }
                    "CreateRecoveryPoint"
                    | "SetPinned"
                    | "DeleteRecoveryPoint"
                    | "ScheduleRollback"
                    | "CancelPendingRollback"
                    | "SetRetentionPolicy" => invocation.return_dbus_error(
                        READ_ONLY_ERROR,
                        "This release provides read-only discovery; recovery point mutations arrive in TM-2 and TM-3",
                    ),
                    _ => invocation.return_dbus_error(
                        "org.freedesktop.DBus.Error.UnknownMethod",
                        "Unknown Timeback Machine method",
                    ),
                }
            },
        )
        .property(
            |_connection, _sender, _object_path, _interface_name, property| match property {
                "ContractVersion" => CONTRACT_VERSION.to_variant(),
                "Busy" => false.to_variant(),
                _ => ().to_variant(),
            },
        )
        .build()?;
    Ok(())
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
