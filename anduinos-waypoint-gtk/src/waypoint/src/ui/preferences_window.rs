//! Modern sidebar-based preferences window

use adw::prelude::*;
use gtk::prelude::*;
use gtk::{ListBox, Orientation, SelectionMode, Stack};
use libadwaita as adw;

use crate::i18n::{tr, trf};

/// Show the main preferences window with sidebar navigation
pub fn show_preferences_window(parent: &adw::ApplicationWindow) {
    let window = adw::Window::new();
    window.set_title(Some(&tr("Preferences")));
    window.set_modal(true);
    window.set_transient_for(Some(parent));
    window.set_default_size(900, 600);

    // Main vertical container (header + content)
    let main_container = gtk::Box::new(Orientation::Vertical, 0);

    // Add header bar
    let header = adw::HeaderBar::new();
    header.set_show_end_title_buttons(true);
    main_container.append(&header);

    // Create the main horizontal box (sidebar + content)
    let main_box = gtk::Box::new(Orientation::Horizontal, 0);

    // Create sidebar
    let sidebar = create_sidebar();
    sidebar.set_width_request(200);
    main_box.append(&sidebar);

    // Add separator
    let separator = gtk::Separator::new(Orientation::Vertical);
    main_box.append(&separator);

    // Create stack for content pages
    let stack = Stack::new();
    stack.set_hexpand(true);
    stack.set_vexpand(true);

    // Add all preference pages to stack
    let scope_page = create_scope_content();
    stack.add_named(&scope_page, Some("scope"));

    let storage_page = create_storage_content();
    stack.add_named(&storage_page, Some("storage"));

    let scheduling_page = create_scheduling_content(parent);
    stack.add_named(&scheduling_page, Some("scheduling"));

    main_box.append(&stack);

    // Wire up sidebar navigation with lazy loading for scheduling page
    let stack_clone = stack.clone();
    let scheduling_page_clone = scheduling_page.clone();
    let scheduling_loaded = std::rc::Rc::new(std::cell::RefCell::new(false));
    let scheduling_loaded_clone = scheduling_loaded.clone();

    sidebar.connect_row_selected(move |_, row| {
        if let Some(row) = row {
            let index = row.index();
            let page_name = match index {
                0 => "scheduling",
                1 => "scope",
                2 => "storage",
                _ => "scheduling",
            };

            // Lazy load scheduling data when first viewed
            if page_name == "scheduling" && !*scheduling_loaded_clone.borrow() {
                unsafe {
                    if let Some(scheduler_content) =
                        scheduling_page_clone.data::<gtk::Box>("scheduler_content")
                    {
                        super::scheduler_dialog::load_scheduler_status(scheduler_content.as_ref());
                        *scheduling_loaded_clone.borrow_mut() = true;
                    }
                }
            }

            stack_clone.set_visible_child_name(page_name);
        }
    });

    // Select first item by default
    if let Some(first_row) = sidebar.row_at_index(0) {
        sidebar.select_row(Some(&first_row));
    }

    // Add main_box to main_container
    main_container.append(&main_box);

    window.set_content(Some(&main_container));
    window.present();
}

/// Create the sidebar with navigation items
fn create_sidebar() -> ListBox {
    let sidebar = ListBox::new();
    sidebar.set_selection_mode(SelectionMode::Single);
    sidebar.add_css_class("navigation-sidebar");

    let items = [
        (tr("Scheduled Recovery"), "preferences-system-time-symbolic"),
        (tr("Recovery Scope"), "folder-symbolic"),
        (tr("Storage"), "drive-harddisk-symbolic"),
    ];

    for (title, icon_name) in items {
        let row = adw::ActionRow::new();
        row.set_title(&title);

        let icon = gtk::Image::from_icon_name(icon_name);
        icon.set_margin_start(6);
        icon.set_margin_end(6);
        row.add_prefix(&icon);

        sidebar.append(&row);
    }

    sidebar
}

/// Create the fixed recovery-scope content page.
fn create_scope_content() -> gtk::Box {
    let container = gtk::Box::new(Orientation::Vertical, 0);

    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_vexpand(true);
    scrolled.set_hexpand(true);

    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(800);
    clamp.set_tightening_threshold(600);

    let content_box = gtk::Box::new(Orientation::Vertical, 0);
    content_box.set_margin_top(24);
    content_box.set_margin_bottom(24);
    content_box.set_margin_start(12);
    content_box.set_margin_end(12);

    // Add page from preferences module
    let page_content = super::preferences::create_recovery_scope_page();

    // Extract groups from the page and add to content box
    let mut child = page_content.first_child();
    while let Some(widget) = child {
        let next = widget.next_sibling();
        widget.unparent();
        content_box.append(&widget);
        child = next;
    }

    clamp.set_child(Some(&content_box));
    scrolled.set_child(Some(&clamp));
    container.append(&scrolled);

    container
}

/// Create a truthful, read-only storage page. Limits and automatic deletion
/// stay hidden until the recovery engine can enforce them using exclusive and
/// reclaimable Btrfs accounting.
fn create_storage_content() -> gtk::Box {
    let container = gtk::Box::new(Orientation::Vertical, 0);

    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_vexpand(true);
    scrolled.set_hexpand(true);

    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(800);
    clamp.set_tightening_threshold(600);

    let content_box = gtk::Box::new(Orientation::Vertical, 0);
    content_box.set_margin_top(24);
    content_box.set_margin_bottom(24);
    content_box.set_margin_start(12);
    content_box.set_margin_end(12);

    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("Recovery Storage"));
    group.set_description(Some(&tr(
        "Btrfs referenced space is not the same as space reclaimed by deleting a recovery point.",
    )));
    match crate::dbus_client::WaypointHelperClient::new()
        .and_then(|client| client.get_quota_usage())
    {
        Ok(usage) => {
            let referenced = adw::ActionRow::new();
            referenced.set_title(&tr("Referenced by Btrfs subvolumes"));
            referenced.set_subtitle(&waypoint_common::format_bytes(usage.referenced));
            group.add(&referenced);

            let exclusive = adw::ActionRow::new();
            exclusive.set_title(&tr("Currently exclusive across Btrfs qgroups"));
            exclusive.set_subtitle(&trf(
                "{0} · an estimate, not a deletion guarantee",
                &[&waypoint_common::format_bytes(usage.exclusive)],
            ));
            group.add(&exclusive);
        }
        Err(_) => {
            let unavailable = adw::ActionRow::new();
            unavailable.set_title(&tr("Detailed accounting unavailable"));
            unavailable.set_subtitle(&tr(
                "AnduinOS does not enable Btrfs quotas automatically because they add filesystem overhead.",
            ));
            group.add(&unavailable);
        }
    }
    content_box.append(&group);

    clamp.set_child(Some(&content_box));
    scrolled.set_child(Some(&clamp));
    container.append(&scrolled);

    container
}

/// Create scheduling content page
fn create_scheduling_content(parent: &adw::ApplicationWindow) -> gtk::Box {
    let container = gtk::Box::new(Orientation::Vertical, 0);

    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_vexpand(true);
    scrolled.set_hexpand(true);

    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(800);
    clamp.set_tightening_threshold(600);

    let content_box = gtk::Box::new(Orientation::Vertical, 0);
    content_box.set_margin_top(24);
    content_box.set_margin_bottom(24);
    content_box.set_margin_start(12);
    content_box.set_margin_end(12);

    // Create the scheduler content with lazy loading (doesn't fetch data immediately)
    let scheduler_content = super::scheduler_dialog::create_scheduler_content_lazy(parent);
    content_box.append(&scheduler_content);

    clamp.set_child(Some(&content_box));
    scrolled.set_child(Some(&clamp));
    container.append(&scrolled);

    // Store the scheduler_content in the container for lazy loading
    unsafe {
        container.set_data("scheduler_content", scheduler_content);
    }

    container
}
