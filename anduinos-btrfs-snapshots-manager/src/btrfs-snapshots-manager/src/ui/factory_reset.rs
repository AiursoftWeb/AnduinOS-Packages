use adw::prelude::*;
use libadwaita as adw;

use crate::i18n::tr;

/// Construct the confirmation without authorizing or scheduling any recovery.
pub(crate) fn confirmation(
    parent: &adw::ApplicationWindow,
    home_available: bool,
) -> (adw::MessageDialog, gtk::CheckButton) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&tr("Reset AnduinOS to Its Initial State?")),
        Some(&tr(
            "Restore the system to New OS. Personal files are kept by default.",
        )),
    );
    // Prefer a short, wide dialog, while leaving room around a small parent.
    dialog.set_default_size((parent.width() - 48).clamp(300, 580), -1);
    dialog.set_default_response(Some("cancel"));
    dialog.set_close_response("cancel");

    let content = gtk::Box::new(gtk::Orientation::Vertical, 16);
    let list = gtk::ListBox::new();
    list.set_selection_mode(gtk::SelectionMode::None);
    list.add_css_class("boxed-list");
    let row = adw::ActionRow::new();
    row.set_title(&tr("Roll back user data"));
    row.set_subtitle(&if home_available {
        tr("Restore all users’ files and settings to their initial state. Snapshot history is kept.")
    } else {
        tr("Unavailable because the factory Home recovery point is missing or damaged.")
    });
    let erase_home = gtk::CheckButton::new();
    erase_home.set_valign(gtk::Align::Center);
    erase_home.set_sensitive(home_available);
    erase_home.set_active(false);
    row.set_activatable_widget(Some(&erase_home));
    row.add_suffix(&erase_home);
    list.append(&row);
    content.append(&list);

    let note = gtk::Label::new(Some(&tr(
        "Safety snapshots are created before rollback. Restart follows within 60 seconds.",
    )));
    note.set_wrap(true);
    note.set_max_width_chars(60);
    note.add_css_class("dim-label");
    content.append(&note);
    dialog.set_extra_child(Some(&content));
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("reset", &tr("Reset and Restart"));
    dialog.set_response_appearance("reset", adw::ResponseAppearance::Destructive);
    (dialog, erase_home)
}
