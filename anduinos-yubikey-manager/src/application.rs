use crate::{config, i18n::i18n, window::YubiKeyManagerWindow};
use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gio, glib};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct YubiKeyManagerApplication;

    #[glib::object_subclass]
    impl ObjectSubclass for YubiKeyManagerApplication {
        const NAME: &'static str = "YubiKeyManagerApplication";
        type Type = super::YubiKeyManagerApplication;
        type ParentType = adw::Application;
    }

    impl ObjectImpl for YubiKeyManagerApplication {}

    impl ApplicationImpl for YubiKeyManagerApplication {
        fn activate(&self) {
            self.parent_activate();
            let app = self.obj();
            if let Some(window) = app.active_window() {
                window.present();
            } else {
                GtkWindowExt::present(&YubiKeyManagerWindow::new(&app));
            }
        }

        fn startup(&self) {
            self.parent_startup();
            let app = self.obj();
            let action = gio::SimpleAction::new("about", None);
            let weak = app.downgrade();
            action.connect_activate(move |_, _| {
                if let Some(app) = weak.upgrade() {
                    app.show_about();
                }
            });
            app.add_action(&action);
            app.set_accels_for_action("window.close", &["<primary>w"]);
        }
    }

    impl GtkApplicationImpl for YubiKeyManagerApplication {}
    impl AdwApplicationImpl for YubiKeyManagerApplication {}
}

glib::wrapper! {
    pub struct YubiKeyManagerApplication(ObjectSubclass<imp::YubiKeyManagerApplication>)
        @extends gio::Application, gtk::Application, adw::Application,
        @implements gio::ActionGroup, gio::ActionMap;
}

impl YubiKeyManagerApplication {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("application-id", config::APP_ID)
            .build()
    }

    fn show_about(&self) {
        let dialog = adw::AboutDialog::builder()
            .application_name(i18n("AnduinOS YubiKey Security Center"))
            .application_icon(config::APP_ID)
            .developer_name(i18n("AnduinOS Team"))
            .version(config::VERSION)
            .website("https://github.com/AiursoftWeb/AnduinOS-Packages")
            .issue_url("https://github.com/AiursoftWeb/AnduinOS-Packages/issues")
            .license_type(gtk::License::Gpl30)
            .build();
        dialog.present(self.active_window().as_ref());
    }
}
