mod application;
mod dbus_client;
mod file_history_request;
mod i18n;
mod signal_listener;
mod ui;

use application::SnapshotsManagerApplication;
use gio::prelude::*;
use gtk::glib;

fn main() -> glib::ExitCode {
    // Initialize logging
    // To enable performance profiling, set RUST_LOG=debug:
    //   RUST_LOG=debug cargo run
    // Performance statistics will be logged after each snapshot list refresh
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    i18n::init();
    log::info!(
        "Starting Disk Snapshots Manager v{}",
        env!("CARGO_PKG_VERSION")
    );

    let app = SnapshotsManagerApplication::new();
    app.run()
}
