use std::cell::{Cell, RefCell};
use std::process::Command;

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use crate::file_history_request;
use crate::i18n::tr;
use crate::signal_listener::SnapshotSignalMonitor;
use crate::ui::{self, MainWindow};

pub const APP_ID: &str = "org.anduinos.BtrfsSnapshotsManager";
const NOTIFIER_UNIT: &str = "anduinos-btrfs-snapshots-manager-notifier.service";
const NOTIFIER_START_ARGS: [&str; 4] = ["--user", "start", "--no-block", NOTIFIER_UNIT];

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SnapshotsManagerApplication {
        pub main_window: glib::WeakRef<MainWindow>,
        pub signal_monitor: RefCell<Option<SnapshotSignalMonitor>>,
        pub smoke_exit_scheduled: Cell<bool>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SnapshotsManagerApplication {
        const NAME: &'static str = "SnapshotsManagerApplication";
        type Type = super::SnapshotsManagerApplication;
        type ParentType = adw::Application;
    }

    impl ObjectImpl for SnapshotsManagerApplication {}

    impl ApplicationImpl for SnapshotsManagerApplication {
        fn startup(&self) {
            self.parent_startup();
            let app = self.obj();
            app.load_css();
            app.install_actions();
            app.ensure_notifier_running();
            *self.signal_monitor.borrow_mut() = Some(SnapshotSignalMonitor::start());

            app.set_accels_for_action("win.close", &["<primary>w"]);
            app.set_accels_for_action("win.search", &["<primary>f"]);
            app.set_accels_for_action("win.create", &["<primary>n"]);
            app.set_accels_for_action("win.refresh", &["F5"]);
            app.set_accels_for_action("app.preferences", &["<primary>comma"]);
        }

        fn activate(&self) {
            self.parent_activate();
            let app = self.obj();
            app.ensure_main_window().present();
            app.schedule_smoke_exit();
        }
    }

    impl GtkApplicationImpl for SnapshotsManagerApplication {}
    impl AdwApplicationImpl for SnapshotsManagerApplication {}
}

glib::wrapper! {
    pub struct SnapshotsManagerApplication(ObjectSubclass<imp::SnapshotsManagerApplication>)
        @extends gio::Application, gtk::Application, adw::Application,
        @implements gio::ActionGroup, gio::ActionMap;
}

impl SnapshotsManagerApplication {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("application-id", APP_ID)
            .build()
    }

    pub fn ensure_main_window(&self) -> MainWindow {
        if let Some(window) = self.imp().main_window.upgrade() {
            return window;
        }
        let monitor = self
            .imp()
            .signal_monitor
            .borrow()
            .as_ref()
            .cloned()
            .unwrap_or_else(SnapshotSignalMonitor::start);
        let window = MainWindow::new(self, monitor);
        self.imp().main_window.set(Some(&window));
        window
    }

    fn install_actions(&self) {
        let preferences = gio::SimpleAction::new("preferences", None);
        let weak = self.downgrade();
        preferences.connect_activate(move |_, _| {
            if let Some(app) = weak.upgrade() {
                let window = app.ensure_main_window();
                window.present();
                window.show_advanced_settings();
            }
        });
        self.add_action(&preferences);

        let about = gio::SimpleAction::new("about", None);
        let weak = self.downgrade();
        about.connect_activate(move |_, _| {
            if let Some(app) = weak.upgrade() {
                app.show_about();
            }
        });
        self.add_action(&about);

        let parameter_type = glib::VariantTy::new("(ss)").expect("valid file-history action type");
        let history = gio::SimpleAction::new("file-history", Some(parameter_type));
        let weak = self.downgrade();
        history.connect_activate(move |_, parameter| {
            let Some(app) = weak.upgrade() else {
                return;
            };
            let Some((mode, uri)) = parameter.and_then(|value| value.get::<(String, String)>())
            else {
                log::warn!("Rejected malformed File History activation");
                return;
            };
            match file_history_request::resolve_history_request(&mode, &uri) {
                Ok(target) => ui::show_personal_history_target(&app, target),
                Err(error) => log::warn!("Rejected File History activation: {error}"),
            }
        });
        self.add_action(&history);
    }

    fn show_about(&self) {
        let about = adw::AboutWindow::builder()
            .application_name(tr("Disk Snapshots Manager"))
            .application_icon(APP_ID)
            .developer_name(tr("AnduinOS Team"))
            .version(env!("CARGO_PKG_VERSION"))
            .website("https://www.anduinos.com")
            .issue_url("https://github.com/AiursoftWeb/AnduinOS-Packages/issues")
            .license_type(gtk::License::Gpl30)
            .transient_for(&self.ensure_main_window())
            .modal(true)
            .build();
        about.present();
    }

    fn load_css(&self) {
        let provider = gtk::CssProvider::new();
        provider.load_from_data(
            r#"
            .file-history-target {
                background-color: alpha(@accent_color, 0.12);
            }
            "#,
        );
        if let Some(display) = gtk::gdk::Display::default() {
            gtk::style_context_add_provider_for_display(
                &display,
                &provider,
                gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
            );
        } else {
            log::warn!("Disk Snapshots Manager started without a graphical display");
        }
    }

    fn ensure_notifier_running(&self) {
        glib::spawn_future_local(async {
            let result = gio::spawn_blocking(|| {
                Command::new("/usr/bin/systemctl")
                    .args(NOTIFIER_START_ARGS)
                    .status()
            })
            .await;
            match result {
                Ok(Ok(status)) if status.success() => {}
                Ok(Ok(status)) => log::warn!(
                    "Could not start the desktop notification listener: systemctl exited with {status}"
                ),
                Ok(Err(error)) => {
                    log::warn!("Could not start the desktop notification listener: {error}")
                }
                Err(_) => log::warn!("Starting the desktop notification listener was interrupted"),
            }
        });
    }

    fn schedule_smoke_exit(&self) {
        if std::env::var_os("ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_UI_SMOKE_TEST").is_none()
            || self.imp().smoke_exit_scheduled.replace(true)
        {
            return;
        }
        let weak = self.downgrade();
        glib::idle_add_local_once(move || {
            if let Some(app) = weak.upgrade() {
                app.ensure_main_window().show_advanced_settings();
                for window in app.windows() {
                    window.close();
                }
                app.quit();
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn application_identity_is_stable() {
        assert_eq!(APP_ID, "org.anduinos.BtrfsSnapshotsManager");
    }

    #[test]
    fn notifier_is_started_as_a_supervised_user_service() {
        assert_eq!(
            NOTIFIER_START_ARGS,
            [
                "--user",
                "start",
                "--no-block",
                "anduinos-btrfs-snapshots-manager-notifier.service"
            ]
        );
    }
}
