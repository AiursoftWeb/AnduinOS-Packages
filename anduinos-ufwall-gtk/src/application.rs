use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use gtk::gio;

use crate::config;
use crate::window::UfwallWindow;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct UfwallApplication {}

    #[glib::object_subclass]
    impl ObjectSubclass for UfwallApplication {
        const NAME: &'static str = "UfwallApplication";
        type Type = super::UfwallApplication;
        type ParentType = adw::Application;
    }

    impl ObjectImpl for UfwallApplication {}

    impl ApplicationImpl for UfwallApplication {
        fn activate(&self) {
            self.parent_activate();
            let app = self.obj();
            
            // Check if there's already an active window
            if let Some(window) = app.active_window() {
                window.present();
                return;
            }

            let window = UfwallWindow::new(&app);
            window.present();
        }

        fn startup(&self) {
            self.parent_startup();
            let app = self.obj();

            // Setup actions like About and Preferences
            app.setup_actions();
            app.setup_accels();
        }
    }

    impl GtkApplicationImpl for UfwallApplication {}
    impl AdwApplicationImpl for UfwallApplication {}
}

glib::wrapper! {
    pub struct UfwallApplication(ObjectSubclass<imp::UfwallApplication>)
        @extends gio::Application, gtk::Application, adw::Application,
        @implements gio::ActionGroup, gio::ActionMap;
}

impl UfwallApplication {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("application-id", config::APP_ID)
            .property("resource-base-path", "/com/anduinos/ufwall/")
            .property("flags", gio::ApplicationFlags::empty())
            .build()
    }

    fn setup_actions(&self) {
        let action_about = gio::ActionEntry::builder("about")
            .activate(move |app: &Self, _, _| {
                app.show_about();
            })
            .build();

        let action_quit = gio::ActionEntry::builder("quit")
            .activate(move |app: &Self, _, _| {
                app.quit();
            })
            .build();

        self.add_action_entries([action_about, action_quit]);
    }

    fn setup_accels(&self) {
        self.set_accels_for_action("app.quit", &["<primary>q"]);
        self.set_accels_for_action("window.close", &["<primary>w"]);
    }

    fn show_about(&self) {
        let window = self.active_window();
        let about = adw::AboutDialog::builder()
            .application_name("Firewall")
            .application_icon(config::APP_ID)
            .developer_name("AnduinOS Team")
            .version(config::VERSION)
            .website("https://github.com/anduin/ufwall-gtk")
            .issue_url("https://github.com/anduin/ufwall-gtk/issues")
            .license_type(gtk::License::Gpl30)
            .build();

        if let Some(win) = window {
            about.present(Some(&win));
        } else {
            about.present(None::<&gtk::Window>);
        }
    }
}

impl Default for UfwallApplication {
    fn default() -> Self {
        Self::new()
    }
}
