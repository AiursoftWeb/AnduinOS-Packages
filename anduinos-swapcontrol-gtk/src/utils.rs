use std::cell::RefCell;

use gtk::prelude::DialogExt;
use gtk::prelude::GtkWindowExt;

/// Show an error dialog over a parent window.
pub fn show_error(parent: &gtk::Window, title: &str, message: &str) {
    let dialog = gtk::MessageDialog::builder()
        .transient_for(parent)
        .modal(true)
        .message_type(gtk::MessageType::Error)
        .buttons(gtk::ButtonsType::Ok)
        .text(title)
        .secondary_text(message)
        .build();

    dialog.connect_response(|dialog, _| {
        dialog.close();
    });

    dialog.present();
}

/// Show an info dialog over a parent window.
pub fn show_info(parent: &gtk::Window, title: &str, message: &str) {
    let dialog = gtk::MessageDialog::builder()
        .transient_for(parent)
        .modal(true)
        .message_type(gtk::MessageType::Info)
        .buttons(gtk::ButtonsType::Ok)
        .text(title)
        .secondary_text(message)
        .build();

    dialog.connect_response(|dialog, _| {
        dialog.close();
    });

    dialog.present();
}

/// Show a confirmation dialog. Calls `on_ok` only if the user clicks OK.
pub fn show_confirm<F>(parent: &gtk::Window, title: &str, message: &str, on_ok: F)
where
    F: FnOnce() + 'static,
{
    let dialog = gtk::MessageDialog::builder()
        .transient_for(parent)
        .modal(true)
        .message_type(gtk::MessageType::Warning)
        .buttons(gtk::ButtonsType::OkCancel)
        .text(title)
        .secondary_text(message)
        .build();

    let on_ok = RefCell::new(Some(on_ok));
    dialog.connect_response(move |dialog, response| {
        if response == gtk::ResponseType::Ok {
            if let Some(cb) = on_ok.borrow_mut().take() {
                cb();
            }
        }
        dialog.close();
    });

    dialog.present();
}
