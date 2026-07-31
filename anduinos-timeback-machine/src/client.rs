use std::fmt;

use gio::glib::VariantTy;

use crate::layout::LayoutReport;
use crate::store::DiscoveryReport;
use crate::{DBUS_INTERFACE, DBUS_NAME, DBUS_PATH};

const READ_ONLY_CALL_TIMEOUT_MS: i32 = 3_000;

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

fn call_json_method(method: &str) -> Result<String, ClientError> {
    let connection = gio::bus_get_sync(gio::BusType::System, None::<&gio::Cancellable>)
        .map_err(|error| ClientError(format!("Could not connect to the system bus: {error}")))?;
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
