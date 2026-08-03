mod application;
mod config;
mod history_graph;
mod i18n;
mod snapshot_browser;
mod window;

use adw::prelude::*;
use application::TimebackApplication;
use gtk::glib;

fn main() -> glib::ExitCode {
    i18n::init();
    TimebackApplication::new().run()
}
