use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gdk, gio, glib};

use crate::{config, i18n::i18n, window};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct TimebackApplication;

    #[glib::object_subclass]
    impl ObjectSubclass for TimebackApplication {
        const NAME: &'static str = "TimebackApplication";
        type Type = super::TimebackApplication;
        type ParentType = adw::Application;
    }

    impl ObjectImpl for TimebackApplication {}

    impl ApplicationImpl for TimebackApplication {
        fn activate(&self) {
            self.parent_activate();
            let app = self.obj();
            if let Some(active) = app.active_window() {
                active.present();
            } else {
                window::build(&app).present();
            }
        }

        fn startup(&self) {
            self.parent_startup();
            let app = self.obj();
            install_styles();

            let about = gio::SimpleAction::new("about", None);
            let weak = app.downgrade();
            about.connect_activate(move |_, _| {
                if let Some(app) = weak.upgrade() {
                    app.show_about();
                }
            });
            app.add_action(&about);
            app.set_accels_for_action("window.close", &["<primary>w"]);
        }
    }

    impl GtkApplicationImpl for TimebackApplication {}
    impl AdwApplicationImpl for TimebackApplication {}
}

glib::wrapper! {
    pub struct TimebackApplication(ObjectSubclass<imp::TimebackApplication>)
        @extends gio::Application, gtk::Application, adw::Application,
        @implements gio::ActionGroup, gio::ActionMap;
}

impl TimebackApplication {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("application-id", config::APP_ID)
            .build()
    }

    fn show_about(&self) {
        let dialog = adw::AboutDialog::builder()
            .application_name(i18n("AnduinOS Timeback Machine"))
            .application_icon(config::APP_ID)
            .developer_name(i18n("AnduinOS Team"))
            .version(config::VERSION)
            .comments(i18n(
                "Create recovery points and safely return AnduinOS to an earlier state.",
            ))
            .website("https://www.anduinos.com")
            .issue_url("https://github.com/AiursoftWeb/AnduinOS-Packages/issues")
            .license_type(gtk::License::Gpl30)
            .build();
        dialog.present(self.active_window().as_ref());
    }
}

fn install_styles() {
    let Some(display) = gdk::Display::default() else {
        return;
    };
    let provider = gtk::CssProvider::new();
    provider.load_from_data(include_str!("../data/style.css"));
    gtk::style_context_add_provider_for_display(
        &display,
        &provider,
        gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
    );
}
