use crate::snapshot::SnapshotManager;
use adw::prelude::*;
use libadwaita as adw;
use std::cell::RefCell;
use std::rc::Rc;

use super::comparison_view::ComparisonView;
use super::dialogs;
use crate::i18n::{tr, trf};

/// Show dialog to compare two snapshots
pub fn show_compare_dialog(
    window: &adw::ApplicationWindow,
    manager: &Rc<RefCell<SnapshotManager>>,
) {
    let snapshots = match manager.borrow().load_snapshots() {
        Ok(s) => s,
        Err(e) => {
            dialogs::show_error(
                window,
                &tr("Error"),
                &trf("Failed to load recovery points: {0}", &[&e.to_string()]),
            );
            return;
        }
    };

    if snapshots.len() < 2 {
        dialogs::show_error(
            window,
            &tr("Not Enough Recovery Points"),
            &tr(
                "You need at least 2 recovery points to compare.\n\nCreate more recovery points first.",
            ),
        );
        return;
    }

    // Create comparison dialog with navigation view
    let dialog = adw::Window::new();
    dialog.set_title(Some(&tr("Compare Recovery Points")));
    dialog.set_default_size(850, 700);
    dialog.set_modal(true);
    dialog.set_transient_for(Some(window));

    // Create the comparison view with snapshots
    let comparison_view = ComparisonView::new(snapshots);

    // Set the comparison view as dialog content
    dialog.set_content(Some(comparison_view.widget()));

    // Present the dialog
    dialog.present();
}
