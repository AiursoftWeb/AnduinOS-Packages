use std::cell::{Cell, RefCell};
use std::rc::Rc;

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::gio;
use gtk::glib;

use crate::config;
use crate::i18n::i18n;
use crate::window::{self, Ui};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct AppearanceApplication {
        pub ui: RefCell<Option<Rc<Ui>>>,
        pub resident: Cell<bool>,
        pub hold: RefCell<Option<gio::ApplicationHoldGuard>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for AppearanceApplication {
        const NAME: &'static str = "AppearanceApplication";
        type Type = super::AppearanceApplication;
        type ParentType = adw::Application;
    }

    impl ObjectImpl for AppearanceApplication {}

    impl ApplicationImpl for AppearanceApplication {
        fn startup(&self) {
            self.parent_startup();
            let app = self.obj();
            app.setup_actions();
            let resident = app.flags().contains(gio::ApplicationFlags::IS_SERVICE);
            self.resident.set(resident);
            if resident {
                self.hold.replace(Some(app.hold()));
                let app = app.clone();
                glib::idle_add_local_once(move || {
                    app.ensure_window();
                });
            }
        }

        fn activate(&self) {
            self.parent_activate();
            let app = self.obj();
            let ui = app.ensure_window();
            ui.refresh();
            ui.window.present();
        }
    }

    impl GtkApplicationImpl for AppearanceApplication {}
    impl AdwApplicationImpl for AppearanceApplication {}
}

glib::wrapper! {
    pub struct AppearanceApplication(ObjectSubclass<imp::AppearanceApplication>)
        @extends gio::Application, gtk::Application, adw::Application,
        @implements gio::ActionGroup, gio::ActionMap;
}

impl AppearanceApplication {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("application-id", config::APP_ID)
            .build()
    }

    fn setup_actions(&self) {
        let action_about = gio::SimpleAction::new("about", None);
        let app = self.clone();
        action_about.connect_activate(move |_, _| app.show_about());
        self.add_action(&action_about);
        self.set_accels_for_action("window.close", &["<Primary>w"]);
        self.set_accels_for_action("app.quit", &["<Primary>q"]);
        let action_quit = gio::SimpleAction::new("quit", None);
        let app = self.clone();
        action_quit.connect_activate(move |_, _| app.quit());
        self.add_action(&action_quit);
    }

    fn show_about(&self) {
        let dialog = adw::AboutDialog::builder()
            .application_name(i18n("AnduinOS Appearance"))
            .application_icon(config::ICON_NAME)
            .developer_name(i18n("AnduinOS Team"))
            .website("https://www.anduinos.com")
            .issue_url("https://github.com/AiursoftWeb/AnduinOS-Packages/issues")
            .license_type(gtk::License::Gpl30)
            .version(config::VERSION)
            .build();
        dialog.present(self.active_window().as_ref());
    }

    fn ensure_window(&self) -> Rc<Ui> {
        if let Some(ui) = self.imp().ui.borrow().clone() {
            return ui;
        }
        let ui = window::build(self, self.imp().resident.get());
        self.imp().ui.replace(Some(ui.clone()));
        ui
    }
}

impl Default for AppearanceApplication {
    fn default() -> Self {
        Self::new()
    }
}
