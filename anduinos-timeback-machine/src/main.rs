mod application;
mod config;
mod i18n;
mod window;

use adw::prelude::*;
use application::TimebackApplication;
use gtk::glib;

fn main() -> glib::ExitCode {
    i18n::init();
    TimebackApplication::new().run()
}
