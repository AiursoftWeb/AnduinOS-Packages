mod about_preferences;
mod analytics_dialog;
mod comparison_dialog;
mod comparison_view;
mod create_snapshot_dialog;
mod dialogs;
mod error_helpers;
mod external_backups;
mod main_window_helpers;
pub mod notifications;
mod personal_history;
pub mod preferences;
mod preferences_window;
mod schedule_card;
mod schedule_edit_dialog;
mod scheduler_dialog;
mod shortcuts_window;
mod snapshot_list;
mod snapshot_row;
mod toolbar;

use crate::dbus_client::WaypointHelperClient;
use crate::i18n::{tr, trf};
use crate::snapshot::SnapshotManager;
use crate::user_preferences::UserPreferencesManager;
use adw::prelude::*;
use gtk::glib;
use gtk::prelude::*;
use gtk::{
    Application, Button, Label, ListBox, Orientation, ScrolledWindow, SearchEntry, ToggleButton,
};
use libadwaita as adw;
use snapshot_row::SnapshotAction;
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::mpsc;

use snapshot_list::DateFilter;

// Path validation moved to validation module

pub struct MainWindow {
    window: adw::ApplicationWindow,
    snapshot_manager: Rc<RefCell<SnapshotManager>>,
    user_prefs_manager: Rc<RefCell<UserPreferencesManager>>,
    snapshot_list: ListBox,
    create_btn: Button,
    compare_btn: Button,
    _search_entry: SearchEntry,
    _match_label: Label,
    _date_filter: Rc<RefCell<DateFilter>>,
}

impl MainWindow {
    pub fn build(
        app: &Application,
        snapshot_created_rx: std::sync::mpsc::Receiver<
            crate::signal_listener::SnapshotCreatedEvent,
        >,
    ) -> adw::ApplicationWindow {
        let snapshot_manager = Rc::new(RefCell::new(SnapshotManager::new()));

        // Initialize user preferences manager
        let user_prefs_manager = match UserPreferencesManager::new() {
            Ok(pm) => Rc::new(RefCell::new(pm)),
            Err(e) => {
                log::error!("Failed to initialize user preferences manager: {e}");
                log::warn!("User preferences (favorites, notes) will not be saved");
                Rc::new(RefCell::new(UserPreferencesManager::ephemeral()))
            }
        };

        // Create header bar
        let header = adw::HeaderBar::new();
        header.set_title_widget(Some(&adw::WindowTitle::new(&tr("AnduinOS Waypoint"), "")));

        // Add application icon to header bar
        let app_icon = if let Ok(icon_path) =
            std::fs::canonicalize("assets/icons/hicolor/scalable/waypoint.svg")
        {
            gtk::Image::from_file(&icon_path)
        } else {
            // Fallback to system icon if assets folder not found (installed version)
            gtk::Image::from_icon_name("org.anduinos.Waypoint")
        };
        app_icon.set_pixel_size(24);
        app_icon.set_margin_start(6);
        header.pack_start(&app_icon);

        // Create hamburger menu
        let menu_button = gtk::MenuButton::builder()
            .icon_name("open-menu-symbolic")
            .build();

        let popover = gtk::Popover::new();
        let popover_box = gtk::Box::builder()
            .orientation(Orientation::Vertical)
            .spacing(6)
            .margin_top(12)
            .margin_bottom(12)
            .margin_start(12)
            .margin_end(12)
            .width_request(220)
            .build();

        // Theme section (using ListBox for proper styling)
        let theme_list = ListBox::new();
        theme_list.set_selection_mode(gtk::SelectionMode::None);
        theme_list.add_css_class("boxed-list");

        let theme_row = adw::ActionRow::builder().title(tr("Switch theme")).build();

        // Theme buttons
        let theme_buttons_box = gtk::Box::new(Orientation::Horizontal, 12);
        theme_buttons_box.set_valign(gtk::Align::Center);

        let system_btn = gtk::Button::builder()
            .label("")
            .tooltip_text(tr("Match system theme"))
            .width_request(16)
            .height_request(16)
            .build();
        system_btn.add_css_class("flat");
        system_btn.add_css_class("theme-circle");
        system_btn.add_css_class("theme-circle-system");

        let light_btn = gtk::Button::builder()
            .label("")
            .tooltip_text(tr("Light theme"))
            .width_request(16)
            .height_request(16)
            .build();
        light_btn.add_css_class("flat");
        light_btn.add_css_class("theme-circle");
        light_btn.add_css_class("theme-circle-light");

        let dark_btn = gtk::Button::builder()
            .label("")
            .tooltip_text(tr("Dark theme"))
            .width_request(16)
            .height_request(16)
            .build();
        dark_btn.add_css_class("flat");
        dark_btn.add_css_class("theme-circle");
        dark_btn.add_css_class("theme-circle-dark");

        system_btn.set_hexpand(false);
        system_btn.set_vexpand(false);
        system_btn.set_valign(gtk::Align::Center);
        light_btn.set_hexpand(false);
        light_btn.set_vexpand(false);
        light_btn.set_valign(gtk::Align::Center);
        dark_btn.set_hexpand(false);
        dark_btn.set_vexpand(false);
        dark_btn.set_valign(gtk::Align::Center);

        theme_buttons_box.append(&system_btn);
        theme_buttons_box.append(&light_btn);
        theme_buttons_box.append(&dark_btn);

        theme_row.add_suffix(&theme_buttons_box);
        theme_list.append(&theme_row);
        popover_box.append(&theme_list);

        // Menu items section
        let menu_list = ListBox::new();
        menu_list.set_selection_mode(gtk::SelectionMode::None);
        menu_list.add_css_class("boxed-list");

        let analytics_row = adw::ActionRow::builder()
            .title(tr("Analytics"))
            .activatable(true)
            .build();
        menu_list.append(&analytics_row);

        let external_backups_row = adw::ActionRow::builder()
            .title(tr("External Recovery Backups"))
            .activatable(true)
            .build();
        menu_list.append(&external_backups_row);

        let personal_history_row = adw::ActionRow::builder()
            .title(tr("Personal Files History"))
            .activatable(true)
            .build();
        menu_list.append(&personal_history_row);

        let preferences_row = adw::ActionRow::builder()
            .title(tr("Preferences"))
            .activatable(true)
            .build();
        menu_list.append(&preferences_row);

        let shortcuts_row = adw::ActionRow::builder()
            .title(tr("Keyboard Shortcuts"))
            .activatable(true)
            .build();
        menu_list.append(&shortcuts_row);

        let about_row = adw::ActionRow::builder()
            .title(tr("About AnduinOS Waypoint"))
            .activatable(true)
            .build();
        menu_list.append(&about_row);

        popover_box.append(&menu_list);

        popover.set_child(Some(&popover_box));
        menu_button.set_popover(Some(&popover));
        header.pack_end(&menu_button);

        // Status banner - also returns whether the helper verified recovery availability.
        let (banner, recovery_available) = main_window_helpers::create_status_banner();
        let pending_restore_banner = main_window_helpers::create_pending_restore_banner();

        // Toolbar with buttons
        let (toolbar, create_btn, compare_btn, search_btn) = toolbar::create_toolbar();

        if !recovery_available {
            create_btn.set_sensitive(false);
            create_btn.set_tooltip_text(Some(&tr("AnduinOS Btrfs layout required")));
        }

        // Search and filter UI (wrapped in Revealer for smooth animations)
        let search_revealer = gtk::Revealer::new();
        search_revealer.set_transition_type(gtk::RevealerTransitionType::SlideDown);
        search_revealer.set_transition_duration(200); // 200ms animation
        search_revealer.set_reveal_child(false); // Hidden by default

        let search_box = gtk::Box::new(Orientation::Vertical, 12);
        search_box.set_margin_top(12);
        search_box.set_margin_bottom(6);
        search_box.set_margin_start(12);
        search_box.set_margin_end(12);

        // Search entry
        let search_entry = SearchEntry::new();
        search_entry.set_placeholder_text(Some(&tr("Search recovery points…")));
        search_entry.set_hexpand(true);
        search_box.append(&search_entry);

        // Date filter buttons
        let filter_box = gtk::Box::new(Orientation::Horizontal, 6);
        filter_box.add_css_class("linked");

        let all_btn = ToggleButton::with_label(&tr("All"));
        let week_btn = ToggleButton::with_label(&tr("Last 7 days"));
        let month_btn = ToggleButton::with_label(&tr("Last 30 days"));
        let quarter_btn = ToggleButton::with_label(&tr("Last 90 days"));

        all_btn.set_active(true); // Default to "All"

        filter_box.append(&all_btn);
        filter_box.append(&week_btn);
        filter_box.append(&month_btn);
        filter_box.append(&quarter_btn);

        search_box.append(&filter_box);

        // Match count label
        let match_label = Label::new(None);
        match_label.set_halign(gtk::Align::Start);
        match_label.add_css_class("dim-label");
        match_label.add_css_class("caption");
        search_box.append(&match_label);

        // Add search box to revealer
        search_revealer.set_child(Some(&search_box));

        // Snapshot list
        let snapshot_list = ListBox::new();
        snapshot_list.set_selection_mode(gtk::SelectionMode::None);
        snapshot_list.add_css_class("boxed-list");

        let scrolled = ScrolledWindow::new();
        scrolled.set_vexpand(true);
        scrolled.set_child(Some(&snapshot_list));

        // Add margins around snapshot list
        scrolled.set_margin_top(6);
        scrolled.set_margin_bottom(12);
        scrolled.set_margin_start(12);
        scrolled.set_margin_end(12);

        // Main content box
        let content_box = gtk::Box::new(Orientation::Vertical, 0);
        content_box.append(&banner);
        content_box.append(&pending_restore_banner);
        content_box.append(&toolbar);
        content_box.append(&search_revealer);
        content_box.append(&scrolled);

        // Use ToolbarView for proper GNOME layout
        let toolbar_view = adw::ToolbarView::new();
        toolbar_view.add_top_bar(&header);
        toolbar_view.set_content(Some(&content_box));

        // Wrap in ToastOverlay for toast notifications (GNOME HIG)
        let toast_overlay = adw::ToastOverlay::new();
        toast_overlay.set_child(Some(&toolbar_view));

        // Create window
        let window = adw::ApplicationWindow::builder()
            .application(app)
            .title(tr("AnduinOS Waypoint"))
            .default_width(800)
            .default_height(720)
            .content(&toast_overlay)
            .build();

        // Add Ctrl+F keyboard shortcut to open search
        let window_key_controller = gtk::EventControllerKey::new();
        let revealer_for_shortcut = search_revealer.clone();
        let search_entry_for_shortcut = search_entry.clone();
        let search_btn_for_shortcut = search_btn.clone();

        let create_btn_for_shortcut = create_btn.clone();
        let win_for_prefs_shortcut = window.clone();
        let list_for_refresh_shortcut = snapshot_list.clone();
        let sm_for_refresh_shortcut = snapshot_manager.clone();
        let up_for_refresh_shortcut = user_prefs_manager.clone();
        let compare_for_refresh_shortcut = compare_btn.clone();

        window_key_controller.connect_key_pressed(move |_, key, _code, modifier| {
            // Check for Ctrl+F (Cmd+F on macOS)
            let is_ctrl_f =
                key == gtk::gdk::Key::f && modifier.contains(gtk::gdk::ModifierType::CONTROL_MASK);

            if is_ctrl_f && !revealer_for_shortcut.reveals_child() {
                // Open search
                revealer_for_shortcut.set_reveal_child(true);
                search_btn_for_shortcut.add_css_class("suggested-action");
                search_entry_for_shortcut.grab_focus();
                return glib::Propagation::Stop;
            }

            // Ctrl+N: Create new snapshot
            let is_ctrl_n =
                key == gtk::gdk::Key::n && modifier.contains(gtk::gdk::ModifierType::CONTROL_MASK);

            if is_ctrl_n {
                create_btn_for_shortcut.emit_clicked();
                return glib::Propagation::Stop;
            }

            // Ctrl+, (comma): Open preferences
            let is_ctrl_comma = key == gtk::gdk::Key::comma
                && modifier.contains(gtk::gdk::ModifierType::CONTROL_MASK);

            if is_ctrl_comma {
                Self::show_preferences_dialog(&win_for_prefs_shortcut);
                return glib::Propagation::Stop;
            }

            // Ctrl+? (question mark): Show keyboard shortcuts
            let is_ctrl_question = key == gtk::gdk::Key::question
                && modifier.contains(gtk::gdk::ModifierType::CONTROL_MASK);

            if is_ctrl_question {
                shortcuts_window::show_shortcuts_window(&win_for_prefs_shortcut);
                return glib::Propagation::Stop;
            }

            // F5 or Ctrl+R: Refresh snapshot list
            let is_f5 = key == gtk::gdk::Key::F5;
            let is_ctrl_r =
                key == gtk::gdk::Key::r && modifier.contains(gtk::gdk::ModifierType::CONTROL_MASK);

            if is_f5 || is_ctrl_r {
                let sm_clone = sm_for_refresh_shortcut.clone();
                let up_clone = up_for_refresh_shortcut.clone();
                let list_clone = list_for_refresh_shortcut.clone();
                let compare_clone = compare_for_refresh_shortcut.clone();
                let win_clone_for_refresh = win_for_prefs_shortcut.clone();

                snapshot_list::refresh_snapshot_list_internal(
                    &win_clone_for_refresh,
                    &sm_clone,
                    &up_clone,
                    &list_clone,
                    &compare_clone,
                    None, // No search filter
                    None, // No date filter
                    None, // No match label
                    move |_id, _action| {
                        // Empty callback - action handlers are set up elsewhere
                    },
                    None, // No create button for refresh
                );

                return glib::Propagation::Stop;
            }

            glib::Propagation::Proceed
        });
        window.add_controller(window_key_controller);

        let date_filter = Rc::new(RefCell::new(DateFilter::All));

        let main_window = Self {
            window: window.clone(),
            snapshot_manager: snapshot_manager.clone(),
            user_prefs_manager: user_prefs_manager.clone(),
            snapshot_list: snapshot_list.clone(),
            create_btn: create_btn.clone(),
            compare_btn: compare_btn.clone(),
            _search_entry: search_entry.clone(),
            _match_label: match_label.clone(),
            _date_filter: date_filter.clone(),
        };

        // Load snapshots and update button states
        main_window.refresh_snapshot_list();

        // Connect search entry to filter snapshots
        let win_clone_search = window.clone();
        let sm_clone_search = snapshot_manager.clone();
        let up_clone_search = user_prefs_manager.clone();
        let list_clone_search = snapshot_list.clone();
        let compare_btn_clone_search = compare_btn.clone();
        let match_label_clone = match_label.clone();
        let date_filter_clone = date_filter.clone();

        search_entry.connect_search_changed(move |entry| {
            let search_text = entry.text().to_string();
            Self::refresh_with_filter(
                &win_clone_search,
                &sm_clone_search,
                &up_clone_search,
                &list_clone_search,
                &compare_btn_clone_search,
                &match_label_clone,
                &search_text,
                *date_filter_clone.borrow(),
            );
        });

        // Connect date filter buttons
        let win_clone_all = window.clone();
        let sm_clone_all = snapshot_manager.clone();
        let up_clone_all = user_prefs_manager.clone();
        let list_clone_all = snapshot_list.clone();
        let compare_btn_clone_all = compare_btn.clone();
        let match_label_clone_all = match_label.clone();
        let search_entry_clone_all = search_entry.clone();
        let date_filter_clone_all = date_filter.clone();
        let week_btn_clone = week_btn.clone();
        let month_btn_clone = month_btn.clone();
        let quarter_btn_clone = quarter_btn.clone();

        all_btn.connect_toggled(move |btn| {
            if btn.is_active() {
                *date_filter_clone_all.borrow_mut() = DateFilter::All;
                week_btn_clone.set_active(false);
                month_btn_clone.set_active(false);
                quarter_btn_clone.set_active(false);
                let search_text = search_entry_clone_all.text().to_string();
                Self::refresh_with_filter(
                    &win_clone_all,
                    &sm_clone_all,
                    &up_clone_all,
                    &list_clone_all,
                    &compare_btn_clone_all,
                    &match_label_clone_all,
                    &search_text,
                    DateFilter::All,
                );
            }
        });

        let win_clone_week = window.clone();
        let sm_clone_week = snapshot_manager.clone();
        let up_clone_week = user_prefs_manager.clone();
        let list_clone_week = snapshot_list.clone();
        let compare_btn_clone_week = compare_btn.clone();
        let match_label_clone_week = match_label.clone();
        let search_entry_clone_week = search_entry.clone();
        let date_filter_clone_week = date_filter.clone();
        let all_btn_clone = all_btn.clone();
        let month_btn_clone2 = month_btn.clone();
        let quarter_btn_clone2 = quarter_btn.clone();

        week_btn.connect_toggled(move |btn| {
            if btn.is_active() {
                *date_filter_clone_week.borrow_mut() = DateFilter::Last7Days;
                all_btn_clone.set_active(false);
                month_btn_clone2.set_active(false);
                quarter_btn_clone2.set_active(false);
                let search_text = search_entry_clone_week.text().to_string();
                Self::refresh_with_filter(
                    &win_clone_week,
                    &sm_clone_week,
                    &up_clone_week,
                    &list_clone_week,
                    &compare_btn_clone_week,
                    &match_label_clone_week,
                    &search_text,
                    DateFilter::Last7Days,
                );
            }
        });

        let win_clone_month = window.clone();
        let sm_clone_month = snapshot_manager.clone();
        let up_clone_month = user_prefs_manager.clone();
        let list_clone_month = snapshot_list.clone();
        let compare_btn_clone_month = compare_btn.clone();
        let match_label_clone_month = match_label.clone();
        let search_entry_clone_month = search_entry.clone();
        let date_filter_clone_month = date_filter.clone();
        let all_btn_clone2 = all_btn.clone();
        let week_btn_clone2 = week_btn.clone();
        let quarter_btn_clone3 = quarter_btn.clone();

        month_btn.connect_toggled(move |btn| {
            if btn.is_active() {
                *date_filter_clone_month.borrow_mut() = DateFilter::Last30Days;
                all_btn_clone2.set_active(false);
                week_btn_clone2.set_active(false);
                quarter_btn_clone3.set_active(false);
                let search_text = search_entry_clone_month.text().to_string();
                Self::refresh_with_filter(
                    &win_clone_month,
                    &sm_clone_month,
                    &up_clone_month,
                    &list_clone_month,
                    &compare_btn_clone_month,
                    &match_label_clone_month,
                    &search_text,
                    DateFilter::Last30Days,
                );
            }
        });

        let win_clone_quarter = window.clone();
        let sm_clone_quarter = snapshot_manager.clone();
        let up_clone_quarter = user_prefs_manager.clone();
        let list_clone_quarter = snapshot_list.clone();
        let compare_btn_clone_quarter = compare_btn.clone();
        let match_label_clone_quarter = match_label.clone();
        let search_entry_clone_quarter = search_entry.clone();
        let date_filter_clone_quarter = date_filter.clone();
        let all_btn_clone3 = all_btn.clone();
        let week_btn_clone3 = week_btn.clone();
        let month_btn_clone3 = month_btn.clone();

        quarter_btn.connect_toggled(move |btn| {
            if btn.is_active() {
                *date_filter_clone_quarter.borrow_mut() = DateFilter::Last90Days;
                all_btn_clone3.set_active(false);
                week_btn_clone3.set_active(false);
                month_btn_clone3.set_active(false);
                let search_text = search_entry_clone_quarter.text().to_string();
                Self::refresh_with_filter(
                    &win_clone_quarter,
                    &sm_clone_quarter,
                    &up_clone_quarter,
                    &list_clone_quarter,
                    &compare_btn_clone_quarter,
                    &match_label_clone_quarter,
                    &search_text,
                    DateFilter::Last90Days,
                );
            }
        });

        // Connect create button
        let sm_clone = snapshot_manager.clone();
        let up_clone = user_prefs_manager.clone();
        let list_clone = snapshot_list.clone();
        let win_clone = window.clone();
        let compare_btn_clone = compare_btn.clone();

        create_btn.connect_clicked(move |_| {
            Self::on_create_snapshot(
                &win_clone,
                sm_clone.clone(),
                up_clone.clone(),
                list_clone.clone(),
                compare_btn_clone.clone(),
            );
        });

        // Connect compare button
        let sm_clone2 = snapshot_manager.clone();
        let win_clone2 = window.clone();

        compare_btn.connect_clicked(move |_| {
            Self::show_compare_dialog(&win_clone2, &sm_clone2);
        });

        // Connect search button to toggle revealer
        let revealer_clone = search_revealer.clone();
        let search_entry_clone = search_entry.clone();
        let search_btn_clone = search_btn.clone();

        search_btn.connect_clicked(move |_| {
            let is_revealed = revealer_clone.reveals_child();
            revealer_clone.set_reveal_child(!is_revealed);

            // Update button state
            if !is_revealed {
                // Opening search - add "suggested-action" class to highlight button
                search_btn_clone.add_css_class("suggested-action");
                // Auto-focus search entry
                search_entry_clone.grab_focus();
            } else {
                // Closing search - remove highlight
                search_btn_clone.remove_css_class("suggested-action");
            }
        });

        // Add ESC key handler to close search
        let key_controller = gtk::EventControllerKey::new();
        let revealer_for_esc = search_revealer.clone();
        let search_btn_for_esc = search_btn.clone();

        key_controller.connect_key_pressed(move |_, key, _code, _modifier| {
            if key == gtk::gdk::Key::Escape && revealer_for_esc.reveals_child() {
                revealer_for_esc.set_reveal_child(false);
                search_btn_for_esc.remove_css_class("suggested-action");
                glib::Propagation::Stop
            } else {
                glib::Propagation::Proceed
            }
        });
        search_entry.add_controller(key_controller);

        // Connect theme buttons
        let style_manager = adw::StyleManager::default();
        system_btn.connect_clicked(move |_| {
            style_manager.set_color_scheme(adw::ColorScheme::Default);
        });

        let style_manager_light = adw::StyleManager::default();
        light_btn.connect_clicked(move |_| {
            style_manager_light.set_color_scheme(adw::ColorScheme::ForceLight);
        });

        let style_manager_dark = adw::StyleManager::default();
        dark_btn.connect_clicked(move |_| {
            style_manager_dark.set_color_scheme(adw::ColorScheme::ForceDark);
        });

        // Connect hamburger menu items
        let win_clone_menu_analytics = window.clone();
        let sm_clone_menu_analytics = snapshot_manager.clone();
        let popover_clone_analytics = popover.clone();
        analytics_row.connect_activated(move |_| {
            popover_clone_analytics.popdown();
            Self::show_analytics_dialog(&win_clone_menu_analytics, &sm_clone_menu_analytics);
        });

        let win_clone_external_backups = window.clone();
        let popover_clone_external_backups = popover.clone();
        external_backups_row.connect_activated(move |_| {
            popover_clone_external_backups.popdown();
            external_backups::show(&win_clone_external_backups);
        });

        let win_clone_personal_history = window.clone();
        let popover_clone_personal_history = popover.clone();
        personal_history_row.connect_activated(move |_| {
            popover_clone_personal_history.popdown();
            personal_history::show(&win_clone_personal_history);
        });

        let win_clone_menu_prefs = window.clone();
        let popover_clone_prefs = popover.clone();
        preferences_row.connect_activated(move |_| {
            popover_clone_prefs.popdown();
            Self::show_preferences_dialog(&win_clone_menu_prefs);
        });

        let win_clone_menu_shortcuts = window.clone();
        let popover_clone_shortcuts = popover.clone();
        shortcuts_row.connect_activated(move |_| {
            popover_clone_shortcuts.popdown();
            shortcuts_window::show_shortcuts_window(&win_clone_menu_shortcuts);
        });

        let win_clone_menu_about = window.clone();
        let popover_clone_about = popover.clone();
        about_row.connect_activated(move |_| {
            popover_clone_about.popdown();
            Self::show_about_dialog(&win_clone_menu_about);
        });

        // Refresh immediately when the helper creates a manual or scheduled
        // recovery point. The periodic refresh below remains a resilience
        // fallback for points created by APT hooks while the UI is running.
        let window_signal = window.clone();
        let manager_signal = snapshot_manager.clone();
        let user_prefs_signal = user_prefs_manager.clone();
        let list_signal = snapshot_list.clone();
        let compare_signal = compare_btn.clone();
        glib::spawn_future_local(async move {
            loop {
                match snapshot_created_rx.try_recv() {
                    Ok(event) => {
                        log::debug!(
                            "Refreshing after recovery point {} was created by {}",
                            event.snapshot_name,
                            event.created_by
                        );
                        Self::refresh_list_static(
                            &window_signal,
                            &manager_signal,
                            &user_prefs_signal,
                            &list_signal,
                            &compare_signal,
                        );
                    }
                    Err(mpsc::TryRecvError::Empty) => {}
                    Err(mpsc::TryRecvError::Disconnected) => break,
                }
                glib::timeout_future(std::time::Duration::from_millis(100)).await;
            }
        });

        // Set up periodic snapshot list refresh (every 30 seconds)
        // This ensures external snapshots (from scheduler) appear in the UI
        let window_refresh = window.clone();
        let manager_refresh = snapshot_manager.clone();
        let user_prefs_refresh = user_prefs_manager.clone();
        let list_refresh = snapshot_list.clone();
        let compare_refresh = compare_btn.clone();
        glib::timeout_add_seconds_local(30, move || {
            Self::refresh_list_static(
                &window_refresh,
                &manager_refresh,
                &user_prefs_refresh,
                &list_refresh,
                &compare_refresh,
            );
            glib::ControlFlow::Continue
        });

        window
    }

    fn refresh_snapshot_list(&self) {
        let window = self.window.clone();
        let manager = self.snapshot_manager.clone();
        let user_prefs = self.user_prefs_manager.clone();
        let list = self.snapshot_list.clone();
        let compare_btn = self.compare_btn.clone();

        snapshot_list::refresh_snapshot_list_internal(
            &self.window,
            &self.snapshot_manager,
            &self.user_prefs_manager,
            &self.snapshot_list,
            &self.compare_btn,
            None, // No search filter
            None, // No date filter
            None, // No match label
            move |id, action| {
                Self::handle_snapshot_action(
                    &window,
                    &manager,
                    &user_prefs,
                    &list,
                    &compare_btn,
                    id,
                    action,
                );
            },
            Some(&self.create_btn),
        );
    }

    #[allow(clippy::too_many_arguments)] // GTK callback state; replaced by a view model during the UI adaptation milestone.
    fn refresh_with_filter(
        window: &adw::ApplicationWindow,
        manager: &Rc<RefCell<SnapshotManager>>,
        user_prefs_manager: &Rc<RefCell<UserPreferencesManager>>,
        list: &ListBox,
        compare_btn: &Button,
        match_label: &Label,
        search_text: &str,
        date_filter: DateFilter,
    ) {
        let window_clone = window.clone();
        let manager_clone = manager.clone();
        let user_prefs_clone = user_prefs_manager.clone();
        let list_clone = list.clone();
        let compare_btn_clone = compare_btn.clone();

        snapshot_list::refresh_snapshot_list_internal(
            window,
            manager,
            user_prefs_manager,
            list,
            compare_btn,
            Some(search_text),
            Some(date_filter),
            Some(match_label),
            move |id, action| {
                Self::handle_snapshot_action(
                    &window_clone,
                    &manager_clone,
                    &user_prefs_clone,
                    &list_clone,
                    &compare_btn_clone,
                    id,
                    action,
                );
            },
            None, // No create button for filtered view
        );
    }

    fn on_create_snapshot(
        window: &adw::ApplicationWindow,
        manager: Rc<RefCell<SnapshotManager>>,
        user_prefs_manager: Rc<RefCell<UserPreferencesManager>>,
        list: ListBox,
        compare_btn: Button,
    ) {
        let window_for_dialog = window.clone();
        let window_for_callback = window.clone();
        let list_clone = list.clone();
        let manager_clone = manager.clone();
        let user_prefs_clone = user_prefs_manager.clone();
        let compare_btn_clone = compare_btn.clone();

        create_snapshot_dialog::show_create_snapshot_dialog_async(
            &window_for_dialog,
            move |result| {
                if let Some((snapshot_name, description)) = result {
                    Self::create_snapshot_with_description(
                        &window_for_callback,
                        manager_clone.clone(),
                        user_prefs_clone.clone(),
                        list_clone.clone(),
                        compare_btn_clone.clone(),
                        snapshot_name,
                        description,
                    );
                }
            },
        );
    }

    #[allow(clippy::too_many_arguments)] // GTK callback state; replaced by a view model during the UI adaptation milestone.
    fn create_snapshot_with_description(
        window: &adw::ApplicationWindow,
        manager: Rc<RefCell<SnapshotManager>>,
        user_prefs_manager: Rc<RefCell<UserPreferencesManager>>,
        list: ListBox,
        compare_btn: Button,
        snapshot_name: String,
        description: String,
    ) {
        let window_clone = window.clone();
        let list_clone = list.clone();
        let manager_clone = manager.clone();
        let user_prefs_clone = user_prefs_manager.clone();
        let compare_btn_clone = compare_btn.clone();
        let snapshot_name_clone = snapshot_name.clone();
        let description_clone = description.clone();

        // Show loading state
        dialogs::show_toast(&window_clone, &tr("Creating recovery point…"));

        // Create channel for thread communication
        let (sender, receiver) = mpsc::channel();

        // Spawn blocking operation in thread
        std::thread::spawn(move || {
            // Connect to D-Bus helper
            let client = match WaypointHelperClient::new() {
                Ok(c) => c,
                Err(e) => {
                    let error = trf(
                        "Failed to connect to the recovery service: {0}\n\nCheck: systemctl status anduinos-waypoint-helper.service",
                        &[&e.to_string()],
                    );
                    let _ = sender.send((None, Some((tr("Connection Error"), error))));
                    return;
                }
            };

            let result = client.create_deployment(snapshot_name_clone, description_clone, false);

            // Send result back to main thread
            let _ = sender.send((Some((result, client)), None));
        });

        // Receive results on main thread
        glib::source::idle_add_local_once(move || {
            if let Ok(msg) = receiver.recv() {
                let (result_opt, error_opt) = msg;

                // Handle connection error
                if let Some((title, error)) = error_opt {
                    Self::show_error_dialog(&window_clone, &title, &error);
                    return;
                }

                // Handle snapshot result
                if let Some((result, _client)) = result_opt {
                    match result {
                        Ok((true, message)) => {
                            let _deployment = match serde_json::from_str::<
                                crate::dbus_client::RecoveryDeployment,
                            >(&message)
                            {
                                Ok(deployment) => deployment,
                                Err(error) => {
                                    Self::show_error_dialog(
                                        &window_clone,
                                        &tr("Recovery Point Creation Failed"),
                                        &trf(
                                            "The recovery service returned invalid deployment state: {0}",
                                            &[&error.to_string()],
                                        ),
                                    );
                                    return;
                                }
                            };
                            dialogs::show_toast(&window_clone, &tr("Recovery point created"));

                            // Send desktop notification
                            if let Some(app) = window_clone.application() {
                                notifications::notify_snapshot_created(&app, &snapshot_name);
                            }

                            // Refresh snapshot list
                            Self::refresh_list_static(
                                &window_clone,
                                &manager_clone,
                                &user_prefs_clone,
                                &list_clone,
                                &compare_btn_clone,
                            );
                        }
                        Ok((false, message)) => {
                            error_helpers::show_error_with_context(
                                &window_clone,
                                error_helpers::ErrorContext::Create,
                                &message,
                            );
                        }
                        Err(e) => {
                            error_helpers::show_error_with_context(
                                &window_clone,
                                error_helpers::ErrorContext::Create,
                                &e.to_string(),
                            );
                        }
                    }
                }
            }
        });
    }

    fn refresh_list_static(
        window: &adw::ApplicationWindow,
        manager: &Rc<RefCell<SnapshotManager>>,
        user_prefs_manager: &Rc<RefCell<UserPreferencesManager>>,
        list: &ListBox,
        compare_btn: &Button,
    ) {
        let window_clone = window.clone();
        let manager_clone = manager.clone();
        let user_prefs_clone = user_prefs_manager.clone();
        let list_clone = list.clone();
        let compare_btn_clone = compare_btn.clone();

        snapshot_list::refresh_snapshot_list_internal(
            window,
            manager,
            user_prefs_manager,
            list,
            compare_btn,
            None, // No search filter
            None, // No date filter
            None, // No match label
            move |id, action| {
                Self::handle_snapshot_action(
                    &window_clone,
                    &manager_clone,
                    &user_prefs_clone,
                    &list_clone,
                    &compare_btn_clone,
                    id,
                    action,
                );
            },
            None, // No create button needed here
        );
    }

    fn show_error_dialog(window: &adw::ApplicationWindow, title: &str, message: &str) {
        dialogs::show_error(window, title, message);
    }

    #[allow(clippy::too_many_arguments)] // GTK callback state; replaced by a view model during the UI adaptation milestone.
    fn handle_snapshot_action(
        window: &adw::ApplicationWindow,
        manager: &Rc<RefCell<SnapshotManager>>,
        user_prefs_manager: &Rc<RefCell<UserPreferencesManager>>,
        list: &ListBox,
        compare_btn: &Button,
        snapshot_id: &str,
        action: SnapshotAction,
    ) {
        match action {
            SnapshotAction::Verify => {
                Self::verify_snapshot(window, manager, snapshot_id);
            }
            SnapshotAction::Restore => {
                Self::restore_snapshot(window, manager, list, snapshot_id);
            }
            SnapshotAction::Delete => {
                Self::delete_snapshot(
                    window,
                    manager,
                    user_prefs_manager,
                    list,
                    compare_btn,
                    snapshot_id,
                );
            }
            SnapshotAction::ToggleFavorite => {
                Self::toggle_favorite(
                    window,
                    user_prefs_manager,
                    manager,
                    list,
                    compare_btn,
                    snapshot_id,
                );
            }
            SnapshotAction::EditNote => {
                Self::edit_note(
                    window,
                    user_prefs_manager,
                    manager,
                    list,
                    compare_btn,
                    snapshot_id,
                );
            }
        }
    }

    fn verify_snapshot(
        window: &adw::ApplicationWindow,
        manager: &Rc<RefCell<SnapshotManager>>,
        snapshot_id: &str,
    ) {
        // Get the snapshot to retrieve its actual name (directory name on disk)
        let snapshot = match manager.borrow().get_snapshot(snapshot_id) {
            Ok(Some(s)) => s,
            Ok(None) => {
                Self::show_error_dialog(window, &tr("Not Found"), &tr("Recovery point not found"));
                return;
            }
            Err(e) => {
                Self::show_error_dialog(
                    window,
                    &tr("Error"),
                    &trf("Failed to load recovery point: {0}", &[&e.to_string()]),
                );
                return;
            }
        };

        let window_clone = window.clone();
        let deployment_id = snapshot.id.clone();

        // Run verification in background thread
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let result = (|| -> anyhow::Result<crate::dbus_client::VerificationResult> {
                let client = WaypointHelperClient::new()?;
                client.verify_snapshot(deployment_id)
            })();
            let _ = tx.send(result);
        });

        // Poll for result
        glib::spawn_future_local(async move {
            let result = loop {
                match rx.try_recv() {
                    Ok(result) => break result,
                    Err(mpsc::TryRecvError::Empty) => {
                        glib::timeout_future(std::time::Duration::from_millis(50)).await;
                        continue;
                    }
                    Err(mpsc::TryRecvError::Disconnected) => {
                        Self::show_error_dialog(
                            &window_clone,
                            &tr("Verification Failed"),
                            &tr("The verification worker stopped unexpectedly"),
                        );
                        return;
                    }
                }
            };

            match result {
                Ok(verification) => {
                    if verification.is_valid {
                        let mut message = tr("✓ Recovery point is valid and intact");
                        if !verification.warnings.is_empty() {
                            message.push_str(&tr("\n\nWarnings:\n"));
                            for warning in &verification.warnings {
                                message.push_str(&format!("• {warning}\n"));
                            }
                        }

                        let dialog = adw::MessageDialog::new(
                            Some(&window_clone),
                            Some(&tr("Verification Successful")),
                            Some(&message),
                        );
                        dialog.add_response("ok", &tr("OK"));
                        dialog.set_default_response(Some("ok"));
                        dialog.present();
                    } else {
                        let mut message =
                            tr("✗ Recovery point verification failed\n\nErrors found:\n");
                        for error in &verification.errors {
                            message.push_str(&format!("• {error}\n"));
                        }

                        if !verification.warnings.is_empty() {
                            message.push_str(&tr("\nWarnings:\n"));
                            for warning in &verification.warnings {
                                message.push_str(&format!("• {warning}\n"));
                            }
                        }

                        Self::show_error_dialog(
                            &window_clone,
                            &tr("Verification Failed"),
                            &message,
                        );
                    }
                }
                Err(e) => {
                    Self::show_error_dialog(
                        &window_clone,
                        &tr("Verification Error"),
                        &trf("Failed to verify recovery point: {0}", &[&e.to_string()]),
                    );
                }
            }
        });
    }

    fn toggle_favorite(
        window: &adw::ApplicationWindow,
        user_prefs_manager: &Rc<RefCell<UserPreferencesManager>>,
        manager: &Rc<RefCell<SnapshotManager>>,
        list: &ListBox,
        compare_btn: &Button,
        snapshot_id: &str,
    ) {
        let snapshot = match manager.borrow().get_snapshot(snapshot_id) {
            Ok(Some(snapshot)) => snapshot,
            Ok(None) => {
                dialogs::show_error(window, &tr("Not Found"), &tr("Recovery point not found"));
                return;
            }
            Err(error) => {
                dialogs::show_error(window, &tr("Error"), &error.to_string());
                return;
            }
        };
        let desired = !snapshot.pinned;
        let protection_progress = if desired {
            tr("Protecting recovery point…")
        } else {
            tr("Removing recovery-point protection…")
        };
        dialogs::show_toast(window, &protection_progress);

        let (sender, receiver) = mpsc::channel();
        let id_for_worker = snapshot_id.to_string();
        std::thread::spawn(move || {
            let result = WaypointHelperClient::new()
                .and_then(|client| client.set_deployment_pinned(id_for_worker, desired));
            let _ = sender.send(result);
        });

        let window = window.clone();
        let manager = manager.clone();
        let user_prefs_manager = user_prefs_manager.clone();
        let list = list.clone();
        let compare_btn = compare_btn.clone();
        let snapshot_id = snapshot_id.to_string();
        glib::timeout_add_local(std::time::Duration::from_millis(50), move || match receiver
            .try_recv()
        {
            Ok(Ok((true, _))) => {
                let current_preferences = { user_prefs_manager.borrow().get(&snapshot_id) };
                if let Ok(mut preferences) = current_preferences {
                    preferences.is_favorite = desired;
                    let _ = user_prefs_manager
                        .borrow()
                        .update(&snapshot_id, preferences);
                }
                let result_message = if desired {
                    tr("Recovery point protected")
                } else {
                    tr("Recovery-point protection removed")
                };
                dialogs::show_toast(&window, &result_message);
                Self::refresh_list_static(
                    &window,
                    &manager,
                    &user_prefs_manager,
                    &list,
                    &compare_btn,
                );
                glib::ControlFlow::Break
            }
            Ok(Ok((false, message))) => {
                dialogs::show_error(&window, &tr("Protection Failed"), &message);
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                dialogs::show_error(&window, &tr("Protection Failed"), &error.to_string());
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => {
                dialogs::show_error(
                    &window,
                    &tr("Protection Failed"),
                    &tr("The recovery service operation ended unexpectedly"),
                );
                glib::ControlFlow::Break
            }
        });
    }

    fn edit_note(
        window: &adw::ApplicationWindow,
        user_prefs_manager: &Rc<RefCell<UserPreferencesManager>>,
        manager: &Rc<RefCell<SnapshotManager>>,
        list: &ListBox,
        compare_btn: &Button,
        snapshot_id: &str,
    ) {
        // Get snapshot info for context
        let snapshot = match manager.borrow().get_snapshot(snapshot_id) {
            Ok(Some(s)) => s,
            Ok(None) => {
                dialogs::show_error(window, &tr("Not Found"), &tr("Recovery point not found"));
                return;
            }
            Err(e) => {
                dialogs::show_error(
                    window,
                    &tr("Error"),
                    &trf("Failed to load recovery point: {0}", &[&e.to_string()]),
                );
                return;
            }
        };

        // Load current user preferences for this snapshot
        let current_prefs = user_prefs_manager
            .borrow()
            .get(snapshot_id)
            .unwrap_or_default();

        // Create note edit dialog using AdwWindow
        let dialog = adw::Window::new();
        dialog.set_transient_for(Some(window));
        dialog.set_modal(true);
        dialog.set_default_width(550);
        dialog.set_default_height(450);
        dialog.set_title(Some(&tr("Edit Note")));

        // Create toolbar view for better layout
        let toolbar_view = adw::ToolbarView::new();

        // Header bar
        let header = adw::HeaderBar::new();
        header.set_show_title(true);
        toolbar_view.add_top_bar(&header);

        // Content area with proper margins
        let content_box = gtk::Box::new(gtk::Orientation::Vertical, 18);
        content_box.set_margin_top(24);
        content_box.set_margin_bottom(24);
        content_box.set_margin_start(24);
        content_box.set_margin_end(24);

        // Snapshot name context with icon
        let context_box = gtk::Box::new(gtk::Orientation::Horizontal, 12);
        let snapshot_icon = gtk::Image::from_icon_name("org.anduinos.Waypoint");
        snapshot_icon.set_pixel_size(24);
        context_box.append(&snapshot_icon);

        let snapshot_info_box = gtk::Box::new(gtk::Orientation::Vertical, 4);
        let snapshot_label = gtk::Label::new(Some(&snapshot.name));
        snapshot_label.set_halign(gtk::Align::Start);
        snapshot_label.add_css_class("title-4");
        snapshot_info_box.append(&snapshot_label);

        let timestamp_label = gtk::Label::new(Some(&snapshot.format_timestamp()));
        timestamp_label.set_halign(gtk::Align::Start);
        timestamp_label.add_css_class("dim-label");
        timestamp_label.add_css_class("caption");
        snapshot_info_box.append(&timestamp_label);

        context_box.append(&snapshot_info_box);
        content_box.append(&context_box);

        // Section title
        let section_label = gtk::Label::new(Some(&tr("Note")));
        section_label.set_halign(gtk::Align::Start);
        section_label.add_css_class("heading");
        content_box.append(&section_label);

        // Text view with border
        let scrolled_window = gtk::ScrolledWindow::new();
        scrolled_window.set_vexpand(true);
        scrolled_window.set_min_content_height(200);
        scrolled_window.add_css_class("card");

        let text_view = gtk::TextView::new();
        text_view.set_wrap_mode(gtk::WrapMode::WordChar);
        text_view.set_accepts_tab(false);
        text_view.set_top_margin(16);
        text_view.set_bottom_margin(16);
        text_view.set_left_margin(16);
        text_view.set_right_margin(16);

        // Add placeholder text
        let buffer = text_view.buffer();
        if let Some(note) = &current_prefs.note {
            buffer.set_text(note);
        }

        // Placeholder hint when empty
        let placeholder_label = gtk::Label::new(Some(&tr(
            "Add a personal note about this recovery point…\n\nFor example: “Before upgrading system packages” or “Clean install after testing”",
        )));
        placeholder_label.set_halign(gtk::Align::Start);
        placeholder_label.set_valign(gtk::Align::Start);
        placeholder_label.add_css_class("dim-label");
        placeholder_label.set_margin_top(16);
        placeholder_label.set_margin_start(16);
        placeholder_label.set_margin_end(16);
        placeholder_label.set_wrap(true);
        placeholder_label.set_wrap_mode(gtk::pango::WrapMode::WordChar);

        // Overlay for placeholder
        let overlay = gtk::Overlay::new();
        overlay.set_child(Some(&text_view));
        overlay.add_overlay(&placeholder_label);

        // Show/hide placeholder based on text
        let placeholder_clone = placeholder_label.clone();
        buffer.connect_changed(move |buf| {
            let has_text = !buf
                .text(&buf.start_iter(), &buf.end_iter(), false)
                .trim()
                .is_empty();
            placeholder_clone.set_visible(!has_text);
        });
        placeholder_label.set_visible(current_prefs.note.is_none());

        scrolled_window.set_child(Some(&overlay));
        content_box.append(&scrolled_window);

        // Character counter and helper text
        let footer_box = gtk::Box::new(gtk::Orientation::Horizontal, 12);
        footer_box.set_halign(gtk::Align::Fill);

        let helper_label = gtk::Label::new(Some(&tr("Only you can read recovery-point notes")));
        helper_label.set_halign(gtk::Align::Start);
        helper_label.set_hexpand(true);
        helper_label.add_css_class("dim-label");
        helper_label.add_css_class("caption");
        footer_box.append(&helper_label);

        let char_count_label = gtk::Label::new(Some(&trf("{0} characters", &["0"])));
        char_count_label.set_halign(gtk::Align::End);
        char_count_label.add_css_class("dim-label");
        char_count_label.add_css_class("caption");
        footer_box.append(&char_count_label);

        // Update character count
        let char_count_clone = char_count_label.clone();
        buffer.connect_changed(move |buf| {
            let text = buf.text(&buf.start_iter(), &buf.end_iter(), false);
            let count = text.chars().count();
            char_count_clone.set_text(&trf("{0} characters", &[&count.to_string()]));
        });

        // Set initial count
        if let Some(note) = &current_prefs.note {
            let count = note.chars().count();
            char_count_label.set_text(&trf("{0} characters", &[&count.to_string()]));
        }

        content_box.append(&footer_box);

        // Bottom button area
        let button_box = gtk::Box::new(gtk::Orientation::Horizontal, 12);
        button_box.set_halign(gtk::Align::End);
        button_box.set_margin_top(12);

        let cancel_btn = gtk::Button::with_label(&tr("Cancel"));
        let save_btn = gtk::Button::with_label(&tr("Save"));
        save_btn.add_css_class("suggested-action");

        button_box.append(&cancel_btn);
        button_box.append(&save_btn);
        content_box.append(&button_box);

        toolbar_view.set_content(Some(&content_box));
        dialog.set_content(Some(&toolbar_view));

        // Save function
        let save_note = {
            let dialog = dialog.clone();
            let user_prefs_clone = user_prefs_manager.clone();
            let manager_clone = manager.clone();
            let list_clone = list.clone();
            let compare_btn_clone = compare_btn.clone();
            let snapshot_id = snapshot_id.to_string();
            let text_view_clone = text_view.clone();

            move || {
                // Get note text from buffer
                let buffer = text_view_clone.buffer();
                let note_text = buffer
                    .text(&buffer.start_iter(), &buffer.end_iter(), false)
                    .to_string();

                // Update note (trim whitespace, use None if empty)
                let note = if note_text.trim().is_empty() {
                    None
                } else {
                    Some(note_text.trim().to_string())
                };

                // Save note to user preferences
                if let Err(e) = user_prefs_clone.borrow().update_note(&snapshot_id, note) {
                    log::error!("Failed to save snapshot note: {e}");
                    return;
                }

                // Refresh list to show updated note in subtitle
                let window_weak = list_clone.root().and_downcast::<adw::ApplicationWindow>();
                if let Some(window) = window_weak {
                    let window_inner = window.clone();
                    let manager_inner = manager_clone.clone();
                    let user_prefs_inner = user_prefs_clone.clone();
                    let list_inner = list_clone.clone();
                    let compare_btn_inner = compare_btn_clone.clone();

                    snapshot_list::refresh_snapshot_list_internal(
                        &window,
                        &manager_clone,
                        &user_prefs_clone,
                        &list_clone,
                        &compare_btn_clone,
                        None,
                        None,
                        None,
                        move |id, action| {
                            Self::handle_snapshot_action(
                                &window_inner,
                                &manager_inner,
                                &user_prefs_inner,
                                &list_inner,
                                &compare_btn_inner,
                                id,
                                action,
                            );
                        },
                        None,
                    );
                }

                dialog.close();
            }
        };

        // Handle cancel button
        let dialog_clone = dialog.clone();
        cancel_btn.connect_clicked(move |_| {
            dialog_clone.close();
        });

        // Handle save button
        let save_note_clone = save_note.clone();
        save_btn.connect_clicked(move |_| {
            save_note_clone();
        });

        // Keyboard shortcuts
        let key_controller = gtk::EventControllerKey::new();
        let save_note_clone2 = save_note.clone();
        let dialog_clone2 = dialog.clone();
        key_controller.connect_key_pressed(move |_, key, _, modifiers| {
            // Ctrl+Enter to save
            if modifiers.contains(gtk::gdk::ModifierType::CONTROL_MASK)
                && (key == gtk::gdk::Key::Return || key == gtk::gdk::Key::KP_Enter)
            {
                save_note_clone2();
                return gtk::glib::Propagation::Stop;
            }
            // Escape to cancel
            if key == gtk::gdk::Key::Escape {
                dialog_clone2.close();
                return gtk::glib::Propagation::Stop;
            }
            gtk::glib::Propagation::Proceed
        });
        dialog.add_controller(key_controller);

        // Auto-focus text view
        text_view.grab_focus();

        // Show dialog
        dialog.present();
    }

    fn delete_snapshot(
        window: &adw::ApplicationWindow,
        manager: &Rc<RefCell<SnapshotManager>>,
        user_prefs_manager: &Rc<RefCell<UserPreferencesManager>>,
        list: &ListBox,
        compare_btn: &Button,
        snapshot_id: &str,
    ) {
        let snapshot = match manager.borrow().get_snapshot(snapshot_id) {
            Ok(Some(s)) => s,
            Ok(None) => {
                dialogs::show_error(window, &tr("Not Found"), &tr("Recovery point not found"));
                return;
            }
            Err(e) => {
                dialogs::show_error(
                    window,
                    &tr("Error"),
                    &trf("Failed to load recovery point: {0}", &[&e.to_string()]),
                );
                return;
            }
        };

        let snapshot_name = snapshot.name.clone();
        let deployment_id = snapshot.id.clone();

        let window_clone = window.clone();
        let manager_clone = manager.clone();
        let user_prefs_clone = user_prefs_manager.clone();
        let list_clone = list.clone();
        let compare_btn_clone = compare_btn.clone();

        let message = trf(
            "Are you sure you want to delete “{0}”?\n\nThis action cannot be undone.",
            &[&snapshot_name],
        );

        dialogs::show_confirmation(
            window,
            &tr("Delete Recovery Point?"),
            &message,
            &tr("Delete"),
            true,
            move || {
                let window = window_clone.clone();
                let manager = manager_clone.clone();
                let user_prefs = user_prefs_clone.clone();
                let list = list_clone.clone();
                let compare_btn = compare_btn_clone.clone();
                let name = deployment_id.clone();
                let name_for_notification = snapshot_name.clone();

                // Show loading state
                dialogs::show_toast(&window, &tr("Deleting recovery point…"));

                // Create channel for thread communication
                let (sender, receiver) = mpsc::channel();

                // Spawn blocking operation in thread
                std::thread::spawn(move || {
                    // Connect to D-Bus helper
                    let client = match WaypointHelperClient::new() {
                        Ok(c) => c,
                        Err(e) => {
                            let error = trf(
                                "Failed to connect to recovery service: {0}",
                                &[&e.to_string()],
                            );
                            let _ = sender.send((None, Some((tr("Connection Error"), error))));
                            return;
                        }
                    };

                    // Delete snapshot via D-Bus
                    let result = client.delete_deployment(name);

                    // Send result back to main thread
                    let _ = sender.send((Some(result), None));
                });

                // Receive results on main thread
                glib::source::idle_add_local_once(move || {
                    if let Ok(msg) = receiver.recv() {
                        let (result_opt, error_opt) = msg;

                        // Handle connection error
                        if let Some((title, error)) = error_opt {
                            dialogs::show_error(&window, &title, &error);
                            return;
                        }

                        // Handle delete result
                        if let Some(result) = result_opt {
                            match result {
                                Ok((true, message)) => {
                                    dialogs::show_toast(&window, &message);

                                    // Send desktop notification
                                    if let Some(app) = window.application() {
                                        notifications::notify_snapshot_deleted(
                                            &app,
                                            &name_for_notification,
                                        );
                                    }

                                    // Refresh the list
                                    Self::refresh_list_static(
                                        &window,
                                        &manager,
                                        &user_prefs,
                                        &list,
                                        &compare_btn,
                                    );
                                }
                                Ok((false, message)) => {
                                    error_helpers::show_error_with_context(
                                        &window,
                                        error_helpers::ErrorContext::Delete,
                                        &message,
                                    );
                                }
                                Err(e) => {
                                    error_helpers::show_error_with_context(
                                        &window,
                                        error_helpers::ErrorContext::Delete,
                                        &e.to_string(),
                                    );
                                }
                            }
                        }
                    }
                });
            },
        );
    }

    fn restore_snapshot(
        window: &adw::ApplicationWindow,
        manager: &Rc<RefCell<SnapshotManager>>,
        _list: &ListBox,
        snapshot_id: &str,
    ) {
        let snapshot = match manager.borrow().get_snapshot(snapshot_id) {
            Ok(Some(s)) => s,
            Ok(None) => {
                dialogs::show_error(window, &tr("Not Found"), &tr("Recovery point not found"));
                return;
            }
            Err(e) => {
                dialogs::show_error(
                    window,
                    &tr("Error"),
                    &trf("Failed to load recovery point: {0}", &[&e.to_string()]),
                );
                return;
            }
        };

        // Only the deployment-level recovery transaction is shipped. Restoring
        // caller-selected paths as root remains release-gated until it can use
        // a descriptor-confined, non-privileged export channel.
        Self::perform_full_restore(window, &snapshot.id);
    }

    fn perform_full_restore(window: &adw::ApplicationWindow, snapshot_basename: &str) {
        let window_clone = window.clone();
        let snapshot_id_owned = snapshot_basename.to_string();
        let snapshot_id_for_idle = snapshot_basename.to_string();

        // Show loading toast while fetching preview
        dialogs::show_toast(window, &tr("Loading system restore preview…"));

        // Create channel for background thread communication
        let (tx, rx) = mpsc::channel();

        // Fetch preview in background thread
        std::thread::spawn(move || {
            let client = match WaypointHelperClient::new() {
                Ok(c) => c,
                Err(e) => {
                    let _ = tx.send(Err(anyhow::anyhow!(
                        "Failed to connect to recovery service: {e}"
                    )));
                    return;
                }
            };

            let result = client.preview_restore(snapshot_id_owned);
            let _ = tx.send(result);
        });

        // Poll for preview result
        glib::source::idle_add_local(move || {
            match rx.try_recv() {
                Ok(Ok(preview)) => {
                    // Show preview dialog with package changes
                    Self::show_restore_preview_dialog(
                        &window_clone,
                        &snapshot_id_for_idle,
                        preview,
                    );
                    glib::ControlFlow::Break
                }
                Ok(Err(e)) => {
                    dialogs::show_error(
                        &window_clone,
                        &tr("Preview Failed"),
                        &trf(
                            "Failed to generate system restore preview: {0}",
                            &[&e.to_string()],
                        ),
                    );
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    dialogs::show_error(
                        &window_clone,
                        &tr("Error"),
                        &tr("The preview worker stopped unexpectedly"),
                    );
                    glib::ControlFlow::Break
                }
            }
        });
    }

    fn show_restore_preview_dialog(
        window: &adw::ApplicationWindow,
        snapshot_basename: &str,
        preview: crate::dbus_client::RestorePreview,
    ) {
        // Create custom window for better preview
        let dialog = adw::Window::new();
        dialog.set_title(Some(&tr("System Restore Preview")));
        dialog.set_modal(true);
        dialog.set_transient_for(Some(window));
        dialog.set_default_size(700, 600);

        let content_box = gtk::Box::new(gtk::Orientation::Vertical, 0);

        // Header bar
        let header = adw::HeaderBar::new();
        let cancel_button = gtk::Button::with_label(&tr("Cancel"));
        header.pack_start(&cancel_button);

        let restore_button = gtk::Button::with_label(&tr("Prepare Restore"));
        restore_button.add_css_class("destructive-action");
        header.pack_end(&restore_button);

        content_box.append(&header);

        // Scrolled content area
        let scrolled = gtk::ScrolledWindow::new();
        scrolled.set_vexpand(true);
        scrolled.set_hexpand(true);

        let clamp = adw::Clamp::new();
        clamp.set_maximum_size(600);

        let inner_box = gtk::Box::new(gtk::Orientation::Vertical, 18);
        inner_box.set_margin_top(24);
        inner_box.set_margin_bottom(24);
        inner_box.set_margin_start(18);
        inner_box.set_margin_end(18);

        let selected_group = adw::PreferencesGroup::new();
        selected_group.set_title(&tr("Selected Recovery Point"));
        let selected_row = adw::ActionRow::new();
        selected_row.set_title(&preview.snapshot_name);
        let created = trf("Created: {0}", &[&preview.snapshot_timestamp]);
        let subtitle = preview
            .snapshot_description
            .as_deref()
            .map(crate::i18n::localized_generated_description)
            .filter(|description| !description.trim().is_empty())
            .map(|description| format!("{description}\n{created}"))
            .unwrap_or(created);
        selected_row.set_subtitle(&subtitle);
        selected_row.add_prefix(&gtk::Image::from_icon_name("org.anduinos.Waypoint"));
        selected_group.add(&selected_row);
        inner_box.append(&selected_group);

        // Kernel changes group
        let kernel_group = adw::PreferencesGroup::new();
        kernel_group.set_title(&tr("System Changes"));

        let kernel_current = preview
            .current_kernel
            .clone()
            .unwrap_or_else(|| tr("Unknown"));
        let kernel_snapshot = preview
            .snapshot_kernel
            .clone()
            .unwrap_or_else(|| tr("Unknown"));

        let kernel_row = adw::ActionRow::new();
        kernel_row.set_title(&tr("Kernel Version"));

        if kernel_current != kernel_snapshot {
            kernel_row.set_subtitle(&trf("{0} → {1}", &[&kernel_current, &kernel_snapshot]));
            let kernel_icon = gtk::Image::from_icon_name("dialog-warning-symbolic");
            kernel_icon.add_css_class("warning");
            kernel_row.add_prefix(&kernel_icon);
        } else {
            kernel_row.set_subtitle(&trf("{0} (no change)", &[&kernel_current]));
            let kernel_icon = gtk::Image::from_icon_name("emblem-ok-symbolic");
            kernel_icon.add_css_class("success");
            kernel_row.add_prefix(&kernel_icon);
        }

        kernel_group.add(&kernel_row);
        inner_box.append(&kernel_group);

        let scope_group = adw::PreferencesGroup::new();
        scope_group.set_title(&tr("Recovery Scope and Safety"));

        let system_scope_row = adw::ActionRow::new();
        system_scope_row.set_title(&tr("System"));
        system_scope_row.set_subtitle(&tr("System files, configuration, kernel, and installed packages will return to this recovery point"));
        system_scope_row.add_prefix(&gtk::Image::from_icon_name("computer-symbolic"));
        scope_group.add(&system_scope_row);

        let personal_scope_row = adw::ActionRow::new();
        personal_scope_row.set_title(&tr("Personal Files"));
        personal_scope_row.set_subtitle(&tr("Personal Files in /home will not be changed"));
        let personal_icon = gtk::Image::from_icon_name("emblem-ok-symbolic");
        personal_icon.add_css_class("success");
        personal_scope_row.add_prefix(&personal_icon);
        scope_group.add(&personal_scope_row);

        let fallback_row = adw::ActionRow::new();
        fallback_row.set_title(&tr("Known-good Fallback"));
        fallback_row.set_subtitle(&tr(
            "The current system will be preserved before the one-time recovery boot",
        ));
        let fallback_icon = gtk::Image::from_icon_name("emblem-ok-symbolic");
        fallback_icon.add_css_class("success");
        fallback_row.add_prefix(&fallback_icon);
        scope_group.add(&fallback_row);
        inner_box.append(&scope_group);

        // Package changes summary with visual indicators
        let pkg_group = adw::PreferencesGroup::new();
        pkg_group.set_title(&tr("Package Changes"));
        pkg_group.set_description(Some(&trf(
            "{0} total changes",
            &[&preview.total_package_changes.to_string()],
        )));

        // Packages to add
        if !preview.packages_to_add.is_empty() {
            let add_row = adw::ExpanderRow::new();
            add_row.set_title(&trf(
                "{0} packages to install",
                &[&preview.packages_to_add.len().to_string()],
            ));
            let add_icon = gtk::Image::from_icon_name("list-add-symbolic");
            add_icon.add_css_class("success");
            add_row.add_prefix(&add_icon);

            // Show first few examples
            for pkg in preview.packages_to_add.iter().take(5) {
                let pkg_row = adw::ActionRow::new();
                pkg_row.set_title(&pkg.name);
                if let Some(ref version) = pkg.snapshot_version {
                    pkg_row.set_subtitle(version);
                }
                add_row.add_row(&pkg_row);
            }

            if preview.packages_to_add.len() > 5 {
                let more_row = adw::ActionRow::new();
                more_row.set_title(&trf(
                    "… and {0} more",
                    &[&(preview.packages_to_add.len() - 5).to_string()],
                ));
                more_row.add_css_class("dim-label");
                add_row.add_row(&more_row);
            }

            pkg_group.add(&add_row);
        }

        // Packages to remove
        if !preview.packages_to_remove.is_empty() {
            let remove_row = adw::ExpanderRow::new();
            remove_row.set_title(&trf(
                "{0} packages to remove",
                &[&preview.packages_to_remove.len().to_string()],
            ));
            let remove_icon = gtk::Image::from_icon_name("list-remove-symbolic");
            remove_icon.add_css_class("error");
            remove_row.add_prefix(&remove_icon);

            for pkg in preview.packages_to_remove.iter().take(5) {
                let pkg_row = adw::ActionRow::new();
                pkg_row.set_title(&pkg.name);
                if let Some(ref version) = pkg.current_version {
                    pkg_row.set_subtitle(version);
                }
                remove_row.add_row(&pkg_row);
            }

            if preview.packages_to_remove.len() > 5 {
                let more_row = adw::ActionRow::new();
                more_row.set_title(&trf(
                    "… and {0} more",
                    &[&(preview.packages_to_remove.len() - 5).to_string()],
                ));
                more_row.add_css_class("dim-label");
                remove_row.add_row(&more_row);
            }

            pkg_group.add(&remove_row);
        }

        // Packages to upgrade
        if !preview.packages_to_upgrade.is_empty() {
            let upgrade_row = adw::ExpanderRow::new();
            upgrade_row.set_title(&trf(
                "{0} packages to upgrade",
                &[&preview.packages_to_upgrade.len().to_string()],
            ));
            let upgrade_icon = gtk::Image::from_icon_name("go-up-symbolic");
            upgrade_icon.add_css_class("accent");
            upgrade_row.add_prefix(&upgrade_icon);

            for pkg in preview.packages_to_upgrade.iter().take(5) {
                let pkg_row = adw::ActionRow::new();
                pkg_row.set_title(&pkg.name);
                let curr = pkg.current_version.as_deref().unwrap_or("?");
                let snap = pkg.snapshot_version.as_deref().unwrap_or("?");
                pkg_row.set_subtitle(&format!("{curr} → {snap}"));
                upgrade_row.add_row(&pkg_row);
            }

            if preview.packages_to_upgrade.len() > 5 {
                let more_row = adw::ActionRow::new();
                more_row.set_title(&trf(
                    "… and {0} more",
                    &[&(preview.packages_to_upgrade.len() - 5).to_string()],
                ));
                more_row.add_css_class("dim-label");
                upgrade_row.add_row(&more_row);
            }

            pkg_group.add(&upgrade_row);
        }

        // Packages to downgrade
        if !preview.packages_to_downgrade.is_empty() {
            let downgrade_row = adw::ExpanderRow::new();
            downgrade_row.set_title(&trf(
                "{0} packages to downgrade",
                &[&preview.packages_to_downgrade.len().to_string()],
            ));
            let downgrade_icon = gtk::Image::from_icon_name("go-down-symbolic");
            downgrade_icon.add_css_class("warning");
            downgrade_row.add_prefix(&downgrade_icon);

            for pkg in preview.packages_to_downgrade.iter().take(5) {
                let pkg_row = adw::ActionRow::new();
                pkg_row.set_title(&pkg.name);
                let curr = pkg.current_version.as_deref().unwrap_or("?");
                let snap = pkg.snapshot_version.as_deref().unwrap_or("?");
                pkg_row.set_subtitle(&format!("{curr} → {snap}"));
                downgrade_row.add_row(&pkg_row);
            }

            if preview.packages_to_downgrade.len() > 5 {
                let more_row = adw::ActionRow::new();
                more_row.set_title(&trf(
                    "… and {0} more",
                    &[&(preview.packages_to_downgrade.len() - 5).to_string()],
                ));
                more_row.add_css_class("dim-label");
                downgrade_row.add_row(&more_row);
            }

            pkg_group.add(&downgrade_row);
        }

        inner_box.append(&pkg_group);

        // Warning section
        let warning_group = adw::PreferencesGroup::new();
        warning_group.set_title(&tr("Important"));

        let warning_row1 = adw::ActionRow::new();
        warning_row1.set_title(&tr(
            "System changes made after this recovery point will be lost",
        ));
        warning_row1.set_subtitle(&tr(
            "Personal Files are independent and will remain unchanged",
        ));
        let warning_icon1 = gtk::Image::from_icon_name("dialog-warning-symbolic");
        warning_icon1.add_css_class("warning");
        warning_row1.add_prefix(&warning_icon1);
        warning_group.add(&warning_row1);

        let warning_row2 = adw::ActionRow::new();
        warning_row2.set_title(&tr("A restart is required to apply this system restore"));
        let warning_icon2 = gtk::Image::from_icon_name("system-reboot-symbolic");
        warning_icon2.add_css_class("error");
        warning_row2.add_prefix(&warning_icon2);
        warning_group.add(&warning_row2);

        let warning_row3 = adw::ActionRow::new();
        warning_row3.set_title(&tr(
            "A known-good fallback recovery point will be created first",
        ));
        let warning_icon3 = gtk::Image::from_icon_name("emblem-ok-symbolic");
        warning_icon3.add_css_class("success");
        warning_row3.add_prefix(&warning_icon3);
        warning_group.add(&warning_row3);

        inner_box.append(&warning_group);

        clamp.set_child(Some(&inner_box));
        scrolled.set_child(Some(&clamp));
        content_box.append(&scrolled);
        dialog.set_content(Some(&content_box));

        // Wire up cancel button
        let dialog_clone = dialog.clone();
        cancel_button.connect_clicked(move |_| {
            dialog_clone.close();
        });

        // Wire up restore button
        let window_clone = window.clone();
        let snapshot_name = snapshot_basename.to_string();
        let dialog_clone = dialog.clone();

        restore_button.connect_clicked(move |_| {
            dialog_clone.close();

            let window = window_clone.clone();
            let name = snapshot_name.clone();
            let name_for_notification = snapshot_name.clone();

            // Show loading state
            dialogs::show_toast(&window, &tr("Preparing system restore…"));

            // Create channel for thread communication
            let (sender, receiver) = mpsc::channel();

            // Spawn blocking operation in thread
            std::thread::spawn(move || {
                // Connect to D-Bus helper
                let client = match WaypointHelperClient::new() {
                    Ok(c) => c,
                    Err(e) => {
                        let error = trf(
                            "Failed to connect to recovery service: {0}",
                            &[&e.to_string()],
                        );
                        let _ = sender.send((None, Some((tr("Connection Error"), error))));
                        return;
                    }
                };

                // Restore snapshot via D-Bus (password prompt happens here)
                let result = client.schedule_deployment_restore(name);

                // Send result back to main thread
                let _ = sender.send((Some(result), None));
            });

            // Receive results on main thread
            glib::source::idle_add_local_once(move || {
                if let Ok(msg) = receiver.recv() {
                        let (result_opt, error_opt) = msg;

                        // Handle connection error
                        if let Some((title, error)) = error_opt {
                            dialogs::show_error(&window, &title, &error);
                            return;
                        }

                        // Handle restore result
                        if let Some(result) = result_opt {
                            match result {
                                Ok((true, message)) => {
                                    log::info!("System restore prepared: {message}");
                                    // Send desktop notification
                                    if let Some(app) = window.application() {
                                        notifications::notify_snapshot_restored(&app, &name_for_notification);
                                    }

                                    // Show success message with reboot instructions
                                    let success_dialog = adw::MessageDialog::new(
                                        Some(&window),
                                        Some(&tr("System Restore Ready")),
                                        Some(&tr(
                                            "A one-time system restore has been prepared. You must restart to apply it.\n\nThe current system is preserved as a known-good fallback until the restored boot is confirmed.\n\nRestart now?",
                                        )),
                                    );

                                    success_dialog.add_response("later", &tr("Restart Later"));
                                    success_dialog.add_response("now", &tr("Restart Now"));
                                    success_dialog.set_response_appearance("now", adw::ResponseAppearance::Suggested);
                                    success_dialog.set_default_response(Some("now"));
                                    success_dialog.set_close_response("later");

                                    success_dialog.connect_response(None, |_, response| {
                                        if response == "now" {
                                            // Attempt to reboot
                                            let _ = std::process::Command::new("reboot")
                                                .spawn();
                                        }
                                    });

                                    success_dialog.present();
                                }
                                Ok((false, message)) => {
                                    error_helpers::show_error_with_context(
                                        &window,
                                        error_helpers::ErrorContext::Restore,
                                        &message
                                    );
                                }
                                Err(e) => {
                                    error_helpers::show_error_with_context(
                                        &window,
                                        error_helpers::ErrorContext::Restore,
                                        &e.to_string()
                                    );
                                }
                            }
                        }
                    }
                });
        });

        dialog.present();
    }

    /// Show dialog to compare two snapshots
    fn show_compare_dialog(
        window: &adw::ApplicationWindow,
        manager: &Rc<RefCell<SnapshotManager>>,
    ) {
        comparison_dialog::show_compare_dialog(window, manager);
    }

    /// Show preferences dialog
    fn show_preferences_dialog(window: &adw::ApplicationWindow) {
        preferences_window::show_preferences_window(window);
    }

    /// Show analytics dialog
    fn show_analytics_dialog(
        window: &adw::ApplicationWindow,
        snapshot_manager: &std::rc::Rc<std::cell::RefCell<SnapshotManager>>,
    ) {
        // Load snapshots
        let snapshots = match snapshot_manager.borrow().load_snapshots() {
            Ok(s) => s,
            Err(e) => {
                log::error!("Failed to load snapshots for analytics: {e}");
                Vec::new()
            }
        };
        analytics_dialog::show_analytics_dialog(window, &snapshots, snapshot_manager);
    }

    fn show_about_dialog(window: &adw::ApplicationWindow) {
        about_preferences::show_about_dialog(window);
    }
}
