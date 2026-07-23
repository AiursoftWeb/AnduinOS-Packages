mod application;
mod config;
mod i18n;
mod progress_dialog;
mod swap;
mod utils;
mod views;
mod widgets;
mod window;

use adw::prelude::*;
use application::SwapcontrolApplication;
use gtk::glib;

#[tokio::main]
async fn main() -> glib::ExitCode {
    // Initialize gettext
    i18n::init();

    // Create the application instance
    let app = SwapcontrolApplication::new();

    // Run the application
    app.run()
}
