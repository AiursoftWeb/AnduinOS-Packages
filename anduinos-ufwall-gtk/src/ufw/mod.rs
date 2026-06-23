pub mod backend;
pub mod monitor;
pub mod stats;
pub mod types;

use adw::prelude::*;
use gtk::prelude::*;

/// Show an error dialog to the user.
pub fn show_error(parent: &impl gtk::prelude::IsA<gtk::Widget>, title: &str, msg: &str) {
    let dialog = adw::MessageDialog::builder()
        .heading(title)
        .body(msg)
        .modal(true)
        .build();
    dialog.add_response("ok", "OK");
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("ok");

    if let Some(root) = parent.root() {
        if let Some(window) = root.downcast_ref::<gtk::Window>() {
            dialog.set_transient_for(Some(window));
        }
    }
    dialog.present();
}
