pub mod backend;
pub mod monitor;
pub mod stats;
pub mod traffic_monitor;
pub mod types;

use adw::prelude::*;

/// Show an error dialog to the user.
pub fn show_error(parent: &impl gtk::prelude::IsA<gtk::Widget>, title: &str, msg: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(title)
        .body(msg)
        .build();
    dialog.add_response("ok", "OK");
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("ok");

    let p = parent.root().and_then(|r| r.downcast::<gtk::Window>().ok());
    dialog.choose(p.as_ref(), gtk::gio::Cancellable::NONE, |_| {});
}

/// Show an info dialog to the user.
pub fn show_info(parent: &impl gtk::prelude::IsA<gtk::Widget>, title: &str, msg: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(title)
        .body(msg)
        .build();
    dialog.add_response("ok", "OK");
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("ok");

    let p = parent.root().and_then(|r| r.downcast::<gtk::Window>().ok());
    dialog.choose(p.as_ref(), gtk::gio::Cancellable::NONE, |_| {});
}
