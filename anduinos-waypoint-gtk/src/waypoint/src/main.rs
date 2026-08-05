mod btrfs;
mod dbus_client;
mod i18n;
mod packages;
mod performance;
mod signal_listener;
mod snapshot;
mod ui;
mod user_preferences;

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

    app.connect_startup(|_| {
        load_css();
    });

    app.connect_activate(build_ui);
    app.run()
}

fn load_css() {
    let provider = gtk::CssProvider::new();
    provider.load_from_data(
        r#"
        .theme-circle {
            min-width: 16px;
            min-height: 16px;
            border-radius: 50%;
            padding: 0;
            margin: 0;
            font-size: 0;
        }

        .theme-circle > * {
            min-width: 16px;
            min-height: 16px;
            border-radius: 50%;
            padding: 0;
            margin: 0;
        }

        .theme-circle-system {
            background: linear-gradient(90deg, #000000 50%, #ffffff 50%);
            border: 2px solid #000000;
        }

        .theme-circle-light {
            background-color: #ffffff;
            border: 2px solid #000000;
        }

        .theme-circle-dark {
            background-color: #000000;
            border: 2px solid #000000;
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
    let snapshot_created_rx = signal_listener::start_signal_listener(app.clone());

    let window = ui::MainWindow::build(app, snapshot_created_rx);
    window.present();
}
