mod application;
mod config;
mod core_scan;
mod firmware;
mod helper;
mod i18n;
mod secureboot;
mod window;

use adw::prelude::*;
use application::DriverCenterApplication;

fn main() -> gtk::glib::ExitCode {
    i18n::init();
    adw::init().ok();
    DriverCenterApplication::new().run()
}
