mod dbus_client;
mod file_history_request;
mod i18n;
mod signal_listener;
mod ui;

use gio::prelude::*;
use gtk::prelude::*;
use gtk::{Application, glib};

const APP_ID: &str = "org.anduinos.Waypoint";

fn main() -> glib::ExitCode {
    // Initialize logging
    // To enable performance profiling, set RUST_LOG=debug:
    //   RUST_LOG=debug cargo run
    // Performance statistics will be logged after each snapshot list refresh
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    i18n::init();
    log::info!("Starting AnduinOS Waypoint v{}", env!("CARGO_PKG_VERSION"));

    // Initialize GTK
    let app = Application::builder().application_id(APP_ID).build();

    app.connect_startup(|app| {
        load_css();
        install_file_history_action(app);
    });

    app.connect_activate(|app| {
        if let Some(window) = app.active_window() {
            window.present();
        } else {
            build_ui(app);
        }
    });
    app.run()
}

fn install_file_history_action(app: &Application) {
    let parameter_type = glib::VariantTy::new("(ss)").expect("valid file-history action type");
    let action = gio::SimpleAction::new("file-history", Some(parameter_type));
    let app_weak = app.downgrade();
    action.connect_activate(move |_, parameter| {
        let Some(app) = app_weak.upgrade() else {
            return;
        };
        let Some((mode, uri)) = parameter.and_then(|value| value.get::<(String, String)>()) else {
            log::warn!("Rejected malformed File History activation");
            return;
        };
        match file_history_request::resolve_history_request(&mode, &uri) {
            Ok(target) => ui::show_personal_history_target(&app, target),
            Err(error) => {
                // Session peers are untrusted input even though they run as the
                // same user. Never let an invalid activation reach the helper.
                log::warn!("Rejected File History activation: {error}");
            }
        }
    });
    app.add_action(&action);
}

fn load_css() {
    let provider = gtk::CssProvider::new();
    provider.load_from_data(
        r#"
        .file-history-target {
            background-color: alpha(@accent_color, 0.12);
        }
        "#,
    );

    gtk::style_context_add_provider_for_display(
        &gtk::gdk::Display::default().expect("Could not connect to a display."),
        &provider,
        gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
    );
}

fn build_ui(app: &Application) {
    // Keep the list synchronized with recovery points created by the scheduler.
    let snapshot_created_rx = signal_listener::start_signal_listener();

    let window = ui::MainWindow::build(app, snapshot_created_rx);
    window.present();
}
