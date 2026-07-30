mod application;
mod backend;
mod config;
mod device_monitor;
mod git_signing;
mod home;
mod i18n;
mod model;
mod progress_dialog;
mod ssh;
mod window;

use adw::prelude::*;
use application::YubiKeyManagerApplication;
use gtk::glib;
use std::io::{self, BufRead};
use zeroize::Zeroizing;

fn main() -> glib::ExitCode {
    // OpenSSH invokes this same binary as a private askpass helper. The PIN
    // arrives on a pipe inherited by ssh-add and never appears in argv, the
    // environment, a terminal transcript, or a temporary file.
    if std::env::var_os("ANDUINOS_YUBIKEY_ASKPASS").is_some() {
        let mut pin = Zeroizing::new(String::new());
        if io::stdin().lock().read_line(&mut pin).is_err() {
            return glib::ExitCode::FAILURE;
        }
        print!("{}", pin.as_str());
        return glib::ExitCode::SUCCESS;
    }
    i18n::init();
    let app = YubiKeyManagerApplication::new();
    app.run()
}
