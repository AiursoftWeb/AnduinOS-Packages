use adw::prelude::*;
use libadwaita as adw;

use crate::i18n::tr;

pub(super) fn confirmation(
    parent: &adw::PreferencesWindow,
    heading: &str,
    body: &str,
    action_label: &str,
    destructive: bool,
) -> adw::MessageDialog {
    let dialog = adw::MessageDialog::new(Some(parent), Some(heading), Some(body));
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("run", action_label);
    dialog.set_default_response(Some("cancel"));
    dialog.set_close_response("cancel");
    dialog.set_response_appearance(
        "run",
        if destructive {
            adw::ResponseAppearance::Destructive
        } else {
            adw::ResponseAppearance::Suggested
        },
    );
    dialog
}

pub(super) fn show_result(parent: &adw::PreferencesWindow, result: anyhow::Result<String>) {
    let (heading, body) = match result {
        Ok(message) => (tr("Btrfs request completed"), message),
        Err(error) => (tr("Btrfs operation failed"), error.to_string()),
    };
    let dialog = adw::MessageDialog::new(Some(parent), Some(&heading), Some(&body));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}
