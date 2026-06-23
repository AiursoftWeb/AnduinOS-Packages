mod application;
mod config;
mod i18n;
mod ufw;
mod views;
mod widgets;
mod window;

use adw::prelude::*;
use application::UfwallApplication;
use gtk::glib;

#[tokio::main]
async fn main() -> glib::ExitCode {
    // Initialize gettext
    i18n::init();

    // Create the application instance
    let app = UfwallApplication::new();

    // Run the application
    app.run()
}
