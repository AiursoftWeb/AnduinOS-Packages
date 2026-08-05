//! Keyboard shortcuts window

use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

use crate::i18n::tr;

/// Show the keyboard shortcuts window
pub fn show_shortcuts_window(window: &adw::ApplicationWindow) {
    let dialog = adw::Window::builder()
        .title(tr("Keyboard Shortcuts"))
        .modal(true)
        .transient_for(window)
        .default_width(500)
        .default_height(450)
        .build();

    let main_box = gtk::Box::new(gtk::Orientation::Vertical, 0);

    let header = adw::HeaderBar::new();
    main_box.append(&header);

    let scrolled = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .vexpand(true)
        .build();

    let content_box = gtk::Box::new(gtk::Orientation::Vertical, 24);
    content_box.set_margin_top(24);
    content_box.set_margin_bottom(24);
    content_box.set_margin_start(24);
    content_box.set_margin_end(24);

    // General shortcuts group
    let general_group = adw::PreferencesGroup::builder()
        .title(tr("General"))
        .build();

    add_shortcut_row(&general_group, &tr("Open Search"), "Ctrl+F");
    add_shortcut_row(&general_group, &tr("Create a New Recovery Point"), "Ctrl+N");
    add_shortcut_row(
        &general_group,
        &tr("Refresh the Recovery Point List"),
        "Ctrl+R or F5",
    );
    add_shortcut_row(&general_group, &tr("Open Preferences"), "Ctrl+,");
    add_shortcut_row(&general_group, &tr("Show Keyboard Shortcuts"), "Ctrl+?");
    add_shortcut_row(&general_group, &tr("Close the Search Bar"), "Escape");

    content_box.append(&general_group);

    // Editing shortcuts group
    let editing_group = adw::PreferencesGroup::builder()
        .title(tr("Note Editing"))
        .build();

    add_shortcut_row(&editing_group, &tr("Save Note Changes"), "Ctrl+Enter");
    add_shortcut_row(&editing_group, &tr("Cancel Note Editing"), "Escape");

    content_box.append(&editing_group);

    scrolled.set_child(Some(&content_box));
    main_box.append(&scrolled);

    dialog.set_content(Some(&main_box));
    dialog.present();
}

fn add_shortcut_row(group: &adw::PreferencesGroup, action: &str, shortcut: &str) {
    let row = adw::ActionRow::builder().title(action).build();

    // Create a box to hold the keyboard shortcut keys
    let shortcut_box = gtk::Box::new(gtk::Orientation::Horizontal, 6);

    // Parse the shortcut string and create key buttons
    // Handle special cases like "Ctrl+R or F5"
    if shortcut.contains(" or ") {
        let parts: Vec<&str> = shortcut.split(" or ").collect();
        for (idx, part) in parts.iter().enumerate() {
            if idx > 0 {
                let separator = gtk::Label::new(Some("/"));
                separator.set_css_classes(&["dim-label"]);
                separator.set_margin_start(6);
                separator.set_margin_end(6);
                shortcut_box.append(&separator);
            }
            add_keys_to_box(&shortcut_box, part);
        }
    } else {
        add_keys_to_box(&shortcut_box, shortcut);
    }

    row.add_suffix(&shortcut_box);
    group.add(&row);
}

fn add_keys_to_box(container: &gtk::Box, shortcut: &str) {
    // Split by + to get individual keys
    let keys: Vec<&str> = shortcut.split('+').collect();

    for (idx, key) in keys.iter().enumerate() {
        if idx > 0 {
            let plus = gtk::Label::new(Some("+"));
            plus.set_css_classes(&["dim-label"]);
            plus.set_margin_start(3);
            plus.set_margin_end(3);
            container.append(&plus);
        }

        let key_label = gtk::Label::new(Some(key.trim()));
        key_label.set_css_classes(&["keycap"]);
        container.append(&key_label);
    }
}
