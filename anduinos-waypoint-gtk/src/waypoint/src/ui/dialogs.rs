use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

use crate::i18n::tr;

/// Show a confirmation dialog
pub fn show_confirmation<F>(
    window: &adw::ApplicationWindow,
    title: &str,
    message: &str,
    confirm_label: &str,
    destructive: bool,
    on_confirm: F,
) where
    F: Fn() + 'static,
{
    let dialog = adw::MessageDialog::new(Some(window), Some(title), Some(message));

    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("confirm", confirm_label);

    if destructive {
        dialog.set_response_appearance("confirm", adw::ResponseAppearance::Destructive);
    } else {
        dialog.set_response_appearance("confirm", adw::ResponseAppearance::Suggested);
    }

    dialog.set_default_response(Some("cancel"));
    dialog.set_close_response("cancel");

    dialog.connect_response(None, move |_, response| {
        if response == "confirm" {
            on_confirm();
        }
    });

    dialog.present();
}

/// Show an error dialog
pub fn show_error(window: &adw::ApplicationWindow, title: &str, message: &str) {
    let dialog = adw::MessageDialog::new(Some(window), Some(title), Some(message));
    dialog.add_response("ok", &tr("OK"));
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("ok");
    dialog.present();
}

/// Show a toast notification
pub fn show_toast(window: &adw::ApplicationWindow, message: &str) {
    show_toast_with_timeout(window, message, 3);
}

/// Show a toast notification with custom timeout
pub fn show_toast_with_timeout(
    window: &adw::ApplicationWindow,
    message: &str,
    timeout_seconds: u32,
) {
    // Get the ToastOverlay from the window content
    if let Some(content) = window.content()
        && let Ok(toast_overlay) = content.downcast::<adw::ToastOverlay>()
    {
        let toast = adw::Toast::new(message);
        toast.set_timeout(timeout_seconds);
        toast_overlay.add_toast(toast);
        return;
    }

    // Fallback: print to stdout
    println!("✓ {message}");
}
