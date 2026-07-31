pub mod client;
pub mod layout;
pub mod model;
pub mod store;

pub const CONTRACT_VERSION: u32 = 1;
pub const DEPLOYMENT_SCHEMA_VERSION: u32 = 1;
pub const SNAPSHOT_ROOT: &str = "/.snapshots/anduinos";
pub const DBUS_NAME: &str = "com.anduinos.TimebackMachine1";
pub const DBUS_PATH: &str = "/com/anduinos/TimebackMachine1";
pub const DBUS_INTERFACE: &str = "com.anduinos.TimebackMachine1";
