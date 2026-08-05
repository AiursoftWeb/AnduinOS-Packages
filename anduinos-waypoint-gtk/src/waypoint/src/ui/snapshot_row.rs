use crate::i18n::{tr, trf};
use crate::snapshot::{Snapshot, format_bytes};
use crate::user_preferences::SnapshotPreferences;
use adw::prelude::*;
use gtk::prelude::*;
use gtk::{Box, Button, Orientation};
use libadwaita as adw;

pub struct SnapshotRow {
    row: adw::ActionRow,
}

pub enum SnapshotAction {
    Verify,
    Restore,
    Delete,
    ToggleFavorite,
    EditNote,
}

impl SnapshotRow {
    pub fn new_with_context<F>(
        snapshot: &Snapshot,
        preferences: &SnapshotPreferences,
        on_action: F,
        _max_size: Option<u64>,
    ) -> adw::ActionRow
    where
        F: Fn(String, SnapshotAction) + 'static,
    {
        let row = adw::ActionRow::new();
        row.set_title(&snapshot.name);

        // Create prefix box for the Waypoint icon.
        let prefix_box = Box::new(Orientation::Horizontal, 4);

        // Add waypoint icon as prefix
        let icon = gtk::Image::from_icon_name("org.anduinos.Waypoint");
        icon.set_pixel_size(16);
        prefix_box.append(&icon);

        row.add_prefix(&prefix_box);

        // Build subtitle with metadata - cleaner format with relative time
        let mut subtitle_parts = vec![snapshot.format_relative_time()];
        subtitle_parts.push(format!(
            "{} · {}",
            localized_kind(&snapshot.kind),
            localized_state(&snapshot.state)
        ));
        if snapshot.pinned {
            subtitle_parts.push(tr("Protected"));
        }

        // Add size if available
        if let Some(size) = snapshot.size_bytes {
            subtitle_parts.push(trf("≈{0} reclaimable", &[&format_bytes(size)]));
        }

        if let Some(count) = snapshot.package_count {
            subtitle_parts.push(trf("{0} packages", &[&count.to_string()]));
        }

        if let Some(kernel) = &snapshot.kernel_version {
            // Only show first part of kernel version (e.g., "6.6.54" instead of full version string)
            if let Some(short_version) = kernel.split_whitespace().next() {
                subtitle_parts.push(trf("Kernel {0}", &[short_version]));
            }
        }

        // Build subtitle text with optional note
        let subtitle = if let Some(note) = &preferences.note {
            // Truncate note if too long (show first 60 chars + ellipsis)
            let note_preview = if note.len() > 60 {
                format!("{}…", &note.chars().take(60).collect::<String>().trim())
            } else {
                note.to_string()
            };
            trf(
                "{0}\nNote: {1}",
                &[&subtitle_parts.join("  •  "), &note_preview],
            )
        } else {
            subtitle_parts.join("  •  ")
        };

        row.set_subtitle(&subtitle);

        // Add action buttons - primary action + menu
        let button_box = Box::new(Orientation::Horizontal, 6);

        // Star/favorite button
        let star_btn = Button::builder()
            .icon_name(if snapshot.pinned {
                "starred-symbolic"
            } else {
                "non-starred-symbolic"
            })
            .tooltip_text(if snapshot.pinned {
                tr("Unpin Recovery Point")
            } else {
                tr("Pin Recovery Point")
            })
            .valign(gtk::Align::Center)
            .build();
        star_btn.add_css_class("flat");

        // Primary action: Restore button
        let restore_btn = Button::builder()
            .icon_name("view-refresh-symbolic")
            .tooltip_text(tr("Restore System to This Point"))
            .valign(gtk::Align::Center)
            .build();
        restore_btn.add_css_class("flat");
        let can_restore = snapshot.state == "ready";
        restore_btn.set_sensitive(can_restore);
        if !can_restore {
            restore_btn.set_tooltip_text(Some(&tr("This recovery point is not ready to restore")));
        }

        // Menu button for secondary actions
        let menu_btn = gtk::MenuButton::new();
        menu_btn.set_icon_name("view-more-symbolic");
        menu_btn.set_tooltip_text(Some(&tr("More Actions")));
        menu_btn.set_valign(gtk::Align::Center);
        menu_btn.add_css_class("flat");

        // Create popover menu
        let menu = gtk::gio::Menu::new();

        // Verify action
        let verify_action_name = format!("snapshot.verify-{}", snapshot.id.replace('/', "-"));
        menu.append(Some(&tr("Verify Integrity")), Some(&verify_action_name));

        // Edit Note action
        let edit_note_action_name = format!("snapshot.edit-note-{}", snapshot.id.replace('/', "-"));
        menu.append(Some(&tr("Edit Note")), Some(&edit_note_action_name));

        // Delete action in a separate section (creates visual separator)
        let delete_section = gtk::gio::Menu::new();
        let delete_action_name = format!("snapshot.delete-{}", snapshot.id.replace('/', "-"));
        delete_section.append(
            Some(&tr("Delete Recovery Point")),
            Some(&delete_action_name),
        );
        menu.append_section(None, &delete_section);

        let popover = gtk::PopoverMenu::from_model(Some(&menu));
        menu_btn.set_popover(Some(&popover));

        // Connect buttons
        let snapshot_id = snapshot.id.clone();
        let callback = std::rc::Rc::new(on_action);

        // Connect star button
        let id_clone = snapshot_id.clone();
        let cb_clone = callback.clone();
        star_btn.connect_clicked(move |_| {
            cb_clone(id_clone.clone(), SnapshotAction::ToggleFavorite);
        });

        // Connect restore button
        let id_clone = snapshot_id.clone();
        let cb_clone = callback.clone();
        restore_btn.connect_clicked(move |_| {
            cb_clone(id_clone.clone(), SnapshotAction::Restore);
        });

        // Create action group for this row's menu actions
        let action_group = gtk::gio::SimpleActionGroup::new();

        // Verify action
        let verify_action =
            gtk::gio::SimpleAction::new(&format!("verify-{}", snapshot.id.replace('/', "-")), None);
        let verify_id = snapshot.id.clone();
        let verify_cb = callback.clone();
        verify_action.connect_activate(move |_, _| {
            verify_cb(verify_id.clone(), SnapshotAction::Verify);
        });
        action_group.add_action(&verify_action);

        // Edit Note action
        let edit_note_action = gtk::gio::SimpleAction::new(
            &format!("edit-note-{}", snapshot.id.replace('/', "-")),
            None,
        );
        let edit_note_id = snapshot.id.clone();
        let edit_note_cb = callback.clone();
        edit_note_action.connect_activate(move |_, _| {
            edit_note_cb(edit_note_id.clone(), SnapshotAction::EditNote);
        });
        action_group.add_action(&edit_note_action);

        // Delete action
        let delete_action =
            gtk::gio::SimpleAction::new(&format!("delete-{}", snapshot.id.replace('/', "-")), None);
        delete_action.set_enabled(snapshot.state == "ready" && !snapshot.pinned);
        let delete_id = snapshot.id.clone();
        let delete_cb = callback.clone();
        delete_action.connect_activate(move |_, _| {
            delete_cb(delete_id.clone(), SnapshotAction::Delete);
        });
        action_group.add_action(&delete_action);

        // Insert the action group into the row
        row.insert_action_group("snapshot", Some(&action_group));

        button_box.append(&star_btn);
        button_box.append(&restore_btn);
        button_box.append(&menu_btn);

        row.add_suffix(&button_box);
        row.set_activatable(false);

        row
    }
}

fn localized_kind(kind: &str) -> String {
    match kind {
        "factory" => tr("Factory"),
        "manual" => tr("Manual"),
        "automatic" => tr("Automatic"),
        "apt-pre" => tr("Before APT transaction"),
        "apt-post" => tr("After APT transaction"),
        "pre-rollback" => tr("Before system restore"),
        "imported" => tr("Imported"),
        other => other.to_string(),
    }
}

fn localized_state(state: &str) -> String {
    match state {
        "creating" => tr("Creating"),
        "ready" => tr("Ready"),
        "current" => tr("Current"),
        "pending-rollback" => tr("Restore pending"),
        "booted-unconfirmed" => tr("Awaiting boot confirmation"),
        "fallback-protected" => tr("Protected fallback"),
        "incomplete" => tr("Incomplete"),
        "failed-reverted" => tr("Restore reverted"),
        "broken" => tr("Damaged"),
        "deleting" => tr("Deleting"),
        other => other.to_string(),
    }
}

impl std::ops::Deref for SnapshotRow {
    type Target = adw::ActionRow;

    fn deref(&self) -> &Self::Target {
        &self.row
    }
}
