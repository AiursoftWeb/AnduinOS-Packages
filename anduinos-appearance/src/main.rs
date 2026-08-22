mod application;
mod config;
mod display;
mod i18n;
mod layout;
mod preview;
mod window;

use adw::prelude::*;
use application::AppearanceApplication;

fn main() -> gtk::glib::ExitCode {
    i18n::init();
    AppearanceApplication::new().run()
}
